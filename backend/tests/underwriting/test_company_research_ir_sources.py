from __future__ import annotations

import hashlib
import importlib
import io
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.models.ledger import ValidationError
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_sources import (
    CompanyResearchSourceCompiler,
)

NOW = datetime(2026, 9, 9, 10, tzinfo=UTC)
REVENUES = "The following table presents revenues by type (in millions):\nYear Ended December 31,\n2024 2025\nGoogle Search & other $ 198,084 $ 224,532\nYouTube ads 36,147 40,367\nGoogle subscriptions, platforms, and devices 40,340 48,030\nGoogle Cloud 43,229 58,705\nOther Bets 1,648 1,537\nTotal revenues $ 350,018 $ 402,836"
CASH_FLOW = "CONSOLIDATED STATEMENTS OF CASH FLOWS\n(in millions)\nYear Ended December 31,\n2023 2024 2025\nPurchases of property and equipment (32,251) (52,535) (91,447)"
QUARTER = "Q4 2025 Supplemental Information (in millions, except for number of employees; unaudited)\nRevenues, Traffic Acquisition Costs (TAC), and Number of Employees\nQuarter Ended December 31,\n2024 2025\nTotal revenues $ 96,469 $ 113,828"


def _module():
    name = "app.underwriting.services.company_research_ir_sources"
    assert importlib.util.find_spec(name) is not None, "IR source capture is not implemented"
    return importlib.import_module(name)


def test_ir_capture_contract_is_available():
    module = _module()
    assert callable(module.capture_governed_alphabet_ir_sources)
    assert callable(module.validate_frozen_company_ir_sources)


def _pdf(pages):
    writer = PdfWriter()
    for number in range(1, max(pages) + 1):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        lines = []
        for line in pages.get(number, "Alphabet historical public disclosure.").splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({escaped}) Tj 0 -14 Td")
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 10 Tf 30 750 Td " + " ".join(lines) + " ET").encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


@pytest.fixture
def case(monkeypatch):
    module = _module()
    evidence = CompanyResearchSourceCompiler._evidence_payload(load_alphabet_golden_case_fixture())
    refs = tuple({key: fact[key] for key in ("source_role", "source_url", "source_locator", "raw_hash")} for fact in evidence["facts"])
    pages = {4: "ITEM 1. BUSINESS\nAlphabet products and Google Search support advertisers.", 6: "Google Cloud\nCloud offers infrastructure and subscription products.", 10: "ITEM 1A. RISK FACTORS\nCompetition and execution risks affect our business.", 34: REVENUES, 39: "Capital Expenditures and Leases\nOur technical infrastructure requires sustained investment.", 53: CASH_FLOW}
    bodies = [_pdf(pages), _pdf({1: "Alphabet Announces Fourth Quarter and Fiscal Year 2025 Results", 2: QUARTER})]
    specs = tuple(replace(spec, raw_hash=hashlib.sha256(body).hexdigest(), raw_size=len(body)) for spec, body in zip(module._SOURCE_SPECS, bodies, strict=True))
    monkeypatch.setattr(module, "_SOURCE_SPECS", specs)
    calls = []

    def fetcher(url):
        calls.append(url)
        index = [spec.source_url for spec in specs].index(url)
        return module.CompanySourceHTTPResponse(bodies[index], url, 200, "application/pdf")

    return module, evidence, refs, fetcher, calls, pages


def _capture(case, root):
    module, evidence, refs, fetcher, *_ = case
    return module.capture_governed_alphabet_ir_sources(evidence, refs, now=lambda: NOW, storage_root=root, fetcher=fetcher).payload


def _rehash(payload):
    payload["excerpts_hash"] = canonical_hash(payload["excerpts"])
    payload["bundle_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "bundle_hash"})


def test_ir_capture_freezes_distinct_originals_and_proves_unchanged_facts(case, tmp_path):
    module, evidence, _, _, calls, _ = case
    original = deepcopy(evidence)
    payload = _capture(case, tmp_path)
    assert evidence == original
    assert payload["schema_version"] == "company-research.live-source-bundle.v2"
    assert payload["evidence_payload_hash"] == canonical_hash(evidence)
    assert payload["source_mode"] == "live_http_verified"
    assert len(calls) == len(payload["documents"]) == 2
    assert all("sec.gov" not in url for url in calls)
    assert sum(len(item["text"]) for item in payload["excerpts"]) <= 60_000
    assert all(len(item["text"]) <= 8500 for item in payload["excerpts"])
    for document in payload["documents"]:
        assert document["raw_hash"] != document["governed_source"]["raw_hash"]  # Synthetic transport bytes.
        assert document["available_at"] <= payload["cutoff_at"]
        assert sorted(proof["fact_key"] for proof in document["fact_verifications"]) == document["fact_keys"]
        assert hashlib.sha256((tmp_path / document["raw_path"]).read_bytes()).hexdigest() == document["raw_hash"]
        for proof in document["fact_verifications"]:
            assert "2025" in proof["quote"] and "millions" in proof["quote"]
            assert any(proof["quote"] in excerpt["text"] for excerpt in payload["excerpts"] if excerpt["source_id"] == document["source_id"])
    assert {proof["fact_key"] for document in payload["documents"] for proof in document["fact_verifications"]} == {fact["fact_key"] for fact in evidence["facts"]}
    module.validate_frozen_company_ir_sources(payload, storage_root=tmp_path)


@pytest.mark.parametrize("change", ["wrong_column", "label", "period", "unit", "missing", "capex_sign"])
def test_fact_verification_rejects_wrong_context_despite_pinned_transport(case, tmp_path, monkeypatch, change):
    module, evidence, refs, fetcher, _, pages = case
    if change == "wrong_column":
        pages[34] = pages[34].replace("198,084 $ 224,532", "224,532 $ 198,084")
    elif change == "label":
        pages[34] = pages[34].replace("Google Search & other", "Different business")
    elif change == "period":
        pages[34] = pages[34].replace("2024 2025", "2023 2024")
    elif change == "unit":
        pages[34] = pages[34].replace("in millions", "in billions")
    elif change == "missing":
        pages[34] = pages[34].replace("Google Cloud 43,229 58,705", "Google Cloud unavailable")
    else:
        pages[53] = pages[53].replace("(91,447)", "91,447")
    body = _pdf(pages)
    first = replace(module._SOURCE_SPECS[0], raw_hash=hashlib.sha256(body).hexdigest(), raw_size=len(body))
    monkeypatch.setattr(module, "_SOURCE_SPECS", (first, module._SOURCE_SPECS[1]))
    with pytest.raises(ValidationError):
        module.capture_governed_alphabet_ir_sources(evidence, refs, now=lambda: NOW, storage_root=tmp_path, fetcher=lambda url: replace(fetcher(url), body=body) if url == first.source_url else fetcher(url))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["raw", "text", "proof", "missing_proof", "governed_hash", "url", "excerpt", "path", "future"])
def test_ir_replay_rejects_rehashed_tampering(case, tmp_path, change):
    module = case[0]
    payload = _capture(case, tmp_path)
    document = payload["documents"][0]
    if change in {"raw", "text"}:
        (tmp_path / document[f"{change}_path"]).write_bytes(b"tampered")
    elif change == "proof":
        document["fact_verifications"][0]["quote"] = "invented"
    elif change == "missing_proof":
        document["fact_verifications"].pop()
    elif change == "governed_hash":
        document["governed_source"]["raw_hash"] = document["raw_hash"]
    elif change == "url":
        document["source_url"] = document["governed_source"]["source_url"]
    elif change == "excerpt":
        payload["excerpts"][0]["text"] = "invented"
    elif change == "path":
        document["raw_path"] = "../outside.bin"
    else:
        document["available_at"] = NOW.isoformat()
    _rehash(payload)
    with pytest.raises(ValidationError):
        module.validate_frozen_company_ir_sources(payload, storage_root=tmp_path)


@pytest.mark.parametrize("change", ["fact", "manifest", "refs", "clock"])
def test_ir_rejects_ungoverned_input_before_download(case, tmp_path, change):
    module, evidence, refs, fetcher, calls, _ = case
    instant = NOW
    if change == "fact":
        evidence["facts"][0]["value"] = "999999"
    elif change == "manifest":
        evidence["fixture_content_hash"] = "a" * 64
    elif change == "refs":
        refs = refs[:-1]
    else:
        instant = datetime.fromisoformat(evidence["cutoff"]) - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        module.capture_governed_alphabet_ir_sources(evidence, refs, now=lambda: instant, storage_root=tmp_path, fetcher=fetcher)
    assert calls == []


@pytest.mark.parametrize("change", ["bytes", "redirect", "status", "media", "exception"])
def test_ir_http_failure_has_no_fallback(case, tmp_path, change):
    module, evidence, refs, fetcher, *_ = case
    def broken(url):
        if change == "exception":
            raise RuntimeError("private provider error body")
        changes = {"bytes": {"body": b"different"}, "redirect": {"final_url": "https://other.invalid"}, "status": {"status_code": 403}, "media": {"media_type": None}}
        return replace(fetcher(url), **changes[change])
    with pytest.raises(ValidationError) as error:
        module.capture_governed_alphabet_ir_sources(evidence, refs, now=lambda: NOW, storage_root=tmp_path, fetcher=broken)
    assert "private provider" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_ir_transport_is_bounded_declared_and_rejects_redirects(case, monkeypatch):
    module, _, _, fetcher, *_ = case
    spec = module._SOURCE_SPECS[0]
    observed = {}
    class Response:
        status = 200
        headers = SimpleNamespace(get_content_type=lambda: "application/pdf")
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return spec.source_url
        def read(self, size):
            observed["size"] = size
            return fetcher(spec.source_url).body
    def open_request(request, *, timeout):
        observed.update(headers=request.headers, timeout=timeout)
        return Response()
    monkeypatch.setenv("COMPANY_RESEARCH_HTTP_USER_AGENT", "fund-engine IR offline test")
    monkeypatch.setattr(module, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    module._fetch_http(spec.source_url)
    assert observed["size"] == spec.raw_size + 1
    assert observed["timeout"] <= 30
    assert observed["headers"]["User-agent"] == "fund-engine IR offline test"
    with pytest.raises(ValidationError):
        module._fetch_http("https://www.sec.gov/unknown")
    with pytest.raises(ValidationError):
        module._NoRedirect().redirect_request(None, None, 302, "redirect", {}, spec.source_url)


def test_ir_replay_skip_raw_still_authenticates_text_and_proofs(case, tmp_path):
    module = case[0]
    payload = _capture(case, tmp_path)
    document = payload["documents"][0]
    (tmp_path / document["raw_path"]).unlink()
    module.validate_frozen_company_ir_sources(payload, storage_root=tmp_path, verify_raw=False)
    with pytest.raises(ValidationError):
        module.validate_frozen_company_ir_sources(payload, storage_root=tmp_path)
    document["fact_verifications"][0]["quote"] = "altered"
    _rehash(payload)
    with pytest.raises(ValidationError):
        module.validate_frozen_company_ir_sources(payload, storage_root=tmp_path, verify_raw=False)
