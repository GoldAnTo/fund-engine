"""Frozen official IR PDFs with proofs against the unchanged governed evidence."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from urllib.request import Request, build_opener

from app.underwriting.fixtures.alphabet_golden_case import (
    LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services import company_research_live_sources as legacy
from app.underwriting.services.company_research_live_sources import (
    CompanySourceHTTPResponse,
    FrozenCompanyResearchSourceBundle,
)

_SCHEMA = "company-research.live-source-bundle.v2"
_EXTRACTOR = "alphabet-ir-disclosure-text-and-fact-proof.v1"
_NoRedirect = legacy._NoRedirect
_MAX_RAW_BYTES = 10_000_000
_DOCUMENT_FIELDS = legacy._DOCUMENT_FIELDS | {"governed_source", "fact_verifications"}

# Discovered from https://abc.xyz/investor/default.aspx and its public financial
# widget: https://abc.xyz/feed/FinancialReport.svc/GetFinancialReportList?year=2025
# The Fourth Quarter / tenk entry links this PDF alongside the governed SEC HTML.
# This is a distinct original: its hash must never stand in for the SEC raw hash.
_SOURCE_SPECS = (
    legacy._SourceSpec(
        "alphabet-2025-10k",
        "https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/GOOG-10-K-2025.pdf",
        "9953e8d2e70b18c5bf0af3b2b2a1e96de0555f51cbb39a625497b434e19fb43a",
        940_068,
        "application/pdf",
    ),
    legacy._SOURCE_SPECS[1],
)
_GOVERNED_URLS = {spec.source_id: spec.source_url for spec in legacy._SOURCE_SPECS}
_REVENUE_LABELS = {
    "fy2025_search_other_revenue": "Google Search & other",
    "fy2025_youtube_ads_revenue": "YouTube ads",
    "fy2025_google_cloud_revenue": "Google Cloud",
    "fy2025_subscriptions_platforms_devices_revenue": "Google subscriptions, platforms, and devices",
    "fy2025_other_bets_revenue": "Other Bets",
}


def _fetch_http(url: str) -> CompanySourceHTTPResponse:
    spec = next((item for item in _SOURCE_SPECS if item.source_url == url), None)
    if spec is None:
        raise legacy._fail("IR source URL is not governed")
    user_agent = os.environ.get("COMPANY_RESEARCH_HTTP_USER_AGENT") or "fund-engine-company-research/1.0 (public-disclosure-research)"
    request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=25) as response:
            return CompanySourceHTTPResponse(
                body=response.read(min(spec.raw_size, _MAX_RAW_BYTES) + 1),
                final_url=response.geturl(),
                status_code=response.status,
                media_type=response.headers.get_content_type(),
            )
    except Exception:  # noqa: BLE001 - sanitize all errors at the external HTTP boundary.
        raise legacy._fail("official IR disclosure download failed") from None


def _facts(spec, fixture):
    return [fact for fact in fixture.facts if fact.source_url == _GOVERNED_URLS[spec.source_id]]


def _governed_source(facts):
    return {"source_url": facts[0].source_url, "raw_hash": facts[0].raw_hash}


def _page(text: str, number: int) -> str:
    match = re.search(rf"(?ms)^\[PDF page {number}\]\n(.*?)(?=^\[PDF page \d+\]|\Z)", text)
    if match is None:
        raise legacy._fail("IR fact page is missing")
    return match.group(1)


def _table_quote(text: str, page: int, heading: str, period: str, last_label: str) -> str:
    body = _page(text, page)
    start = body.find(heading)
    if start < 0:
        raise legacy._fail("IR fact table heading or units do not match")
    table = body[start:]
    last = re.search(rf"(?m)^{re.escape(last_label)} [^\n]+$", table)
    if last is None:
        raise legacy._fail("IR fact table row is missing")
    quote = table[:last.end()]
    if period not in quote or "in millions" not in quote or len(quote) > 8500:
        raise legacy._fail("IR fact period or unit does not match")
    return quote


def _verify_row(quote: str, label: str, value: str, *, columns: int = 2, outflow: bool = False) -> None:
    amount = f"{int(value):,}"
    numeric = r"\(?[0-9][0-9,]*\)?"
    expected = rf"\({re.escape(amount)}\)" if outflow else re.escape(amount)
    pattern = rf"(?m)^{re.escape(label)} (?:\$ ?{numeric} |{numeric} ){{{columns - 1}}}(?:\$ )?{expected}$"
    if re.search(pattern, quote) is None:
        raise legacy._fail("IR fact label or reported value does not match")


def _fact_verifications(spec, text: str, facts) -> list[dict[str, str]]:
    by_key = {fact.fact_key: fact for fact in facts}
    if any(fact.currency != "USD" or fact.unit != "million" or fact.value_kind != "reported" for fact in facts):
        raise legacy._fail("IR fact currency or unit is not governed")
    proofs = []
    if spec.source_id == "alphabet-2025-10k":
        if set(by_key) != set(_REVENUE_LABELS) | {"fy2025_capital_expenditures"}:
            raise legacy._fail("IR annual fact coverage is incomplete")
        quote = _table_quote(text, 34, "The following table presents revenues by type (in millions):", "Year Ended December 31,\n2024 2025\n", "Total revenues")
        for key, label in _REVENUE_LABELS.items():
            _verify_row(quote, label, by_key[key].value)
            proofs.append({"fact_key": key, "quote": quote, "locator": "PDF page 34 (report page 33), revenues by type, USD millions, FY2025 column"})
        key = "fy2025_capital_expenditures"
        quote = _table_quote(text, 53, "CONSOLIDATED STATEMENTS OF CASH FLOWS\n(in millions)", "Year Ended December 31,\n2023 2024 2025\n", "Purchases of property and equipment")
        _verify_row(quote, "Purchases of property and equipment", by_key[key].value, columns=3, outflow=True)
        proofs.append({"fact_key": key, "quote": quote, "locator": "PDF page 53 (report page 52), cash flows, USD millions, FY2025 purchases outflow; positive capital expenditure magnitude"})
    else:
        key = "q4_2025_consolidated_revenue"
        if set(by_key) != {key}:
            raise legacy._fail("IR quarterly fact coverage is incomplete")
        quote = _table_quote(text, 2, "Q4 2025 Supplemental Information (in millions, except for number of employees; unaudited)", "Quarter Ended December 31,\n2024 2025\n", "Total revenues")
        _verify_row(quote, "Total revenues", by_key[key].value)
        proofs.append({"fact_key": key, "quote": quote, "locator": "PDF page 2, supplemental information, USD millions, Q4 2025 total revenues"})
    return sorted(proofs, key=lambda proof: proof["fact_key"])


def _excerpts(documents: list[dict[str, object]], texts: dict[str, str]) -> list[dict[str, str]]:
    result = []
    remaining = 60_000
    for document in documents:
        text = texts[document["source_id"]]
        selections = []
        for proof in document["fact_verifications"]:
            start = text.find(proof["quote"])
            if start < 0:
                raise legacy._fail("IR fact proof is missing from extracted text")
            selections.append((proof["locator"], start, start + len(proof["quote"])))
        anchors = (("Business and products", r"(?m)^ITEM 1\. BUSINESS$", 8500), ("Cloud business", r"(?m)^Google Cloud$", 8500), ("Risk factors", r"(?m)^ITEM 1A\. RISK FACTORS$", 8500), ("Capital allocation", r"(?m)^Capital Expenditures and Leases$", 6500)) if document["source_id"] == "alphabet-2025-10k" else (("Quarterly results and outlook", r"\A", 6500),)
        for label, anchor, length in anchors:
            match = re.search(anchor, text)
            if match is None:
                raise legacy._fail("IR disclosure section is missing")
            selections.append((label, match.start(), min(len(text), match.start() + length)))
        covered = []
        for label, start, end in selections:
            if any(start >= left and end <= right for left, right in covered):
                continue
            end = min(end, start + remaining)
            if end <= start:
                raise legacy._fail("IR excerpts exceed the model input boundary")
            result.append({"excerpt_id": f"{document['source_id']}:{start}-{end}", "source_id": document["source_id"], "raw_hash": document["raw_hash"], "source_url": document["source_url"], "locator": f"{label}; extracted text characters {start}:{end}", "text": text[start:end]})
            covered.append((start, end))
            remaining -= end - start
    for document in documents:
        for proof in document["fact_verifications"]:
            if not any(proof["quote"] in excerpt["text"] for excerpt in result if excerpt["source_id"] == document["source_id"]):
                raise legacy._fail("IR fact proof is absent from model input")
    return result


def capture_governed_alphabet_ir_sources(
    evidence_payload: object,
    source_refs: object,
    *,
    now: Callable[[], datetime],
    storage_root: Path,
    fetcher: Callable[[str], CompanySourceHTTPResponse] | None = None,
) -> FrozenCompanyResearchSourceBundle:
    """Fetch official originals and corroborate the existing historical facts."""
    fixture = legacy._governed_evidence(evidence_payload, source_refs)
    assert isinstance(evidence_payload, Mapping)
    captured_at = legacy._timestamp(now())
    if captured_at < fixture.cutoff:
        raise legacy._fail("IR capture clock precedes the historical boundary")
    documents, texts, blobs = [], {}, []
    for spec in _SOURCE_SPECS:
        try:
            response = (fetcher or _fetch_http)(spec.source_url)
        except Exception:  # noqa: BLE001 - sanitize injected and default transport errors.
            raise legacy._fail("official IR disclosure download failed") from None
        if (type(response) is not CompanySourceHTTPResponse or type(response.body) is not bytes
            or response.status_code != 200 or response.final_url != spec.source_url
            or not isinstance(response.media_type, str)
            or response.media_type.split(";", 1)[0].strip().lower() != spec.media_type
            or not 0 < len(response.body) <= _MAX_RAW_BYTES
            or len(response.body) != spec.raw_size or legacy._sha(response.body) != spec.raw_hash):
            raise legacy._fail("download does not match the governed IR original")
        text = legacy._extract_text(response.body, spec)
        text_bytes = text.encode("utf-8")
        text_hash = legacy._sha(text_bytes)
        facts = _facts(spec, fixture)
        raw_path, text_path = f"raw/{spec.raw_hash}.bin", f"text/{text_hash}.txt"
        documents.append({
            "source_id": spec.source_id, "source_url": spec.source_url, "final_url": response.final_url,
            "http_status": response.status_code, "media_type": spec.media_type,
            # These are the governed disclosure's publication/availability dates;
            # the IR feed's ReportDate is a fiscal period, not a publication date.
            "published_at": max(fact.published_at for fact in facts).isoformat(),
            "available_at": max(fact.available_at for fact in facts).isoformat(),
            "retrieved_at": legacy._timestamp(now()).isoformat(),
            "raw_hash": spec.raw_hash, "raw_size": len(response.body), "raw_path": raw_path,
            "text_hash": text_hash, "text_size": len(text_bytes), "text_path": text_path,
            "extractor_version": _EXTRACTOR, "fact_keys": sorted(fact.fact_key for fact in facts),
            "governed_source": _governed_source(facts), "fact_verifications": _fact_verifications(spec, text, facts),
        })
        texts[spec.source_id] = text
        blobs.extend(((raw_path, response.body), (text_path, text_bytes)))
    excerpts = _excerpts(documents, texts)
    payload = {
        "schema_version": _SCHEMA, "source_mode": "live_http_verified",
        "company_external_key": fixture.company_external_key, "cutoff_at": fixture.cutoff.isoformat(),
        "governed_manifest_hash": evidence_payload["fixture_content_hash"],
        "evidence_payload_hash": canonical_hash(evidence_payload), "captured_at": captured_at.isoformat(),
        "documents": documents, "excerpts": excerpts, "excerpts_hash": canonical_hash(excerpts),
    }
    payload["bundle_hash"] = canonical_hash(payload)
    for relative, contents in blobs:
        legacy._write_blob(storage_root, relative, contents)
    validate_frozen_company_ir_sources(payload, storage_root=storage_root)
    return FrozenCompanyResearchSourceBundle(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def validate_frozen_company_ir_sources(payload: object, *, storage_root: Path, verify_raw: bool = True) -> None:
    """Revalidate distinct IR bytes, governed identities, table proofs, and excerpts."""
    if not isinstance(payload, dict) or set(payload) != legacy._PAYLOAD_FIELDS:
        raise legacy._fail("IR receipt fields are invalid")
    if payload["bundle_hash"] != canonical_hash({key: value for key, value in payload.items() if key != "bundle_hash"}):
        raise legacy._fail("IR receipt hash mismatch")
    fixture = load_alphabet_golden_case_fixture()
    cutoff, captured_at = legacy._timestamp(payload["cutoff_at"]), legacy._timestamp(payload["captured_at"])
    if (payload["schema_version"] != _SCHEMA or payload["source_mode"] != "live_http_verified"
        or payload["company_external_key"] != fixture.company_external_key or cutoff != fixture.cutoff
        or captured_at < cutoff or payload["governed_manifest_hash"] not in {fixture.content_hash, LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256}):
        raise legacy._fail("IR receipt boundary is not governed")
    legacy._digest(payload["evidence_payload_hash"])
    documents = payload["documents"]
    if not isinstance(documents, list) or len(documents) != len(_SOURCE_SPECS):
        raise legacy._fail("IR document coverage is invalid")
    texts = {}
    for document, spec in zip(documents, _SOURCE_SPECS, strict=True):
        if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
            raise legacy._fail("IR document fields are invalid")
        facts = _facts(spec, fixture)
        if (document["source_id"] != spec.source_id or document["source_url"] != spec.source_url
            or document["final_url"] != spec.source_url or document["http_status"] != 200
            or document["media_type"] != spec.media_type or document["raw_hash"] != spec.raw_hash
            or type(document["raw_size"]) is not int or document["raw_size"] != spec.raw_size
            or document["extractor_version"] != _EXTRACTOR or document["governed_source"] != _governed_source(facts)
            or document["fact_keys"] != sorted(fact.fact_key for fact in facts)
            or legacy._timestamp(document["published_at"]) != max(fact.published_at for fact in facts)
            or legacy._timestamp(document["available_at"]) != max(fact.available_at for fact in facts)
            or legacy._timestamp(document["retrieved_at"]) < captured_at):
            raise legacy._fail("IR document is not governed")
        if not legacy._timestamp(document["published_at"]) <= legacy._timestamp(document["available_at"]) <= cutoff:
            raise legacy._fail("IR document is unavailable at cutoff")
        text_hash = legacy._digest(document["text_hash"])
        if type(document["text_size"]) is not int or not 0 < document["text_size"] <= legacy._MAX_TEXT_BYTES:
            raise legacy._fail("IR text size is invalid")
        raw_address, text_address = f"raw/{spec.raw_hash}.bin", f"text/{text_hash}.txt"
        legacy._blob_path(storage_root, document["raw_path"], raw_address)
        text_bytes = legacy._read_blob(storage_root, document["text_path"], text_address, document["text_size"])
        if legacy._sha(text_bytes) != text_hash:
            raise legacy._fail("IR frozen text hash mismatch")
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeError:
            raise legacy._fail("IR frozen text encoding is invalid") from None
        if verify_raw:
            raw = legacy._read_blob(storage_root, document["raw_path"], raw_address, spec.raw_size)
            if legacy._sha(raw) != spec.raw_hash or legacy._extract_text(raw, spec) != text:
                raise legacy._fail("IR frozen raw or extracted text hash mismatch")
        if document["fact_verifications"] != _fact_verifications(spec, text, facts):
            raise legacy._fail("IR fact verifications do not match frozen text")
        texts[spec.source_id] = text
    if payload["excerpts"] != _excerpts(documents, texts) or payload["excerpts_hash"] != canonical_hash(payload["excerpts"]):
        raise legacy._fail("IR model input excerpts do not match frozen text")
