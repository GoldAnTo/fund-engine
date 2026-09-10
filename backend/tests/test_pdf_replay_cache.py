from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from types import SimpleNamespace

import pytest
from pypdf.errors import PdfReadError
from reportlab.pdfgen import canvas

from app.datasources.docling import PypdfAdapter
from app.documents.locators import compute_text_sha256
from app.services.automatic_admission import AutomaticAdmissionGate


def _pdf(*pages: str) -> bytes:
    output = io.BytesIO()
    writer = canvas.Canvas(output, invariant=1)
    for text in pages:
        writer.drawString(72, 720, text)
        writer.showPage()
    writer.save()
    return output.getvalue()


def _lineages(raw: bytes):
    digest = hashlib.sha256(raw).hexdigest()
    parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    return [
        SimpleNamespace(
            locator=span.locator,
            artifact=SimpleNamespace(
                raw_bytes=raw, content_sha256=digest, mime_type="application/pdf"
            ),
            document=SimpleNamespace(
                content_sha256=digest, parser_version=span.locator.parser_version
            ),
            span=SimpleNamespace(
                verbatim_text=span.verbatim_text,
                text_sha256=span.text_sha256,
                context_hash=span.context_hash,
                locator_v1=span.locator.to_storage_dict(),
            ),
            candidate=SimpleNamespace(
                quote=span.verbatim_text,
                quote_start=0,
                quote_end=len(span.verbatim_text),
                quote_sha256=hashlib.sha256(span.verbatim_text.encode()).hexdigest(),
            ),
        )
        for span in parsed
    ]


def _count_real_parses(monkeypatch):
    original = PypdfAdapter.extract_spans
    calls = []

    def counted(self, raw, *, document_sha256):
        calls.append(hashlib.sha256(raw).hexdigest())
        return original(self, raw, document_sha256=document_sha256)

    monkeypatch.setattr(PypdfAdapter, "extract_spans", counted)
    return calls


def test_default_parser_reuses_full_pdf_across_gate_instances(monkeypatch):
    # Distinct page neighbours matter: replay must preserve full-document context.
    lineages = _lineages(_pdf("First neighbour.", "Repeated fact.", "Last neighbour."))
    calls = _count_real_parses(monkeypatch)

    for lineage in lineages:
        result = AutomaticAdmissionGate(None)._locator_gate(lineage, None)
        assert result.passed, result.facts

    assert len(calls) == 1


def test_warm_replay_returns_fresh_mutable_locator_objects(monkeypatch):
    lineage = _lineages(_pdf("Immutable cached replay."))[0]
    calls = _count_real_parses(monkeypatch)
    gate = AutomaticAdmissionGate(None)
    locator = lineage.locator
    first = gate._replay_pdf_span(lineage, locator)
    first.locator.extra["poison"] = ["mutable"]
    second = gate._replay_pdf_span(lineage, locator)

    assert "poison" not in second.locator.extra
    assert first is not second
    assert first.locator is not second.locator
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", [
    "raw", "quote", "quote_hash", "span", "span_hash", "context", "locator", "parser",
])
def test_warm_cache_keeps_every_locator_tamper_check(mutation):
    lineage = _lineages(_pdf("Original complete fact.", "Different adjacent page."))[0]
    gate = AutomaticAdmissionGate(None)
    assert gate._locator_gate(lineage, None).passed
    altered = deepcopy(lineage)
    if mutation == "raw":
        # Keep the database's declared SHA unchanged while actual bytes differ.
        altered.artifact.raw_bytes = _pdf("Modified complete fact.", "Different adjacent page.")
    elif mutation == "quote":
        altered.candidate.quote = "Forged"
        altered.candidate.quote_sha256 = hashlib.sha256(b"Forged").hexdigest()
    elif mutation == "quote_hash":
        altered.candidate.quote_sha256 = "0" * 64
    elif mutation == "span":
        altered.span.verbatim_text = "Forged complete fact."
        altered.span.text_sha256 = compute_text_sha256(altered.span.verbatim_text)
    elif mutation == "span_hash":
        altered.span.text_sha256 = "0" * 64
    elif mutation == "context":
        altered.span.context_hash = "0" * 64
    elif mutation == "locator":
        altered.span.locator_v1["page"] = 2
    else:
        altered.document.parser_version = "pypdf-different-config"

    result = gate._locator_gate(altered, None)
    assert not result.passed
    if mutation == "raw":
        assert "locator_artifact_hash_mismatch" in result.facts["failures"]


def test_injected_pypdf_is_not_shared_with_default_cache(monkeypatch):
    lineage = _lineages(_pdf("Default cache isolation."))[0]
    calls = _count_real_parses(monkeypatch)
    assert AutomaticAdmissionGate(None)._locator_gate(lineage, None).passed
    injected = AutomaticAdmissionGate(None, pdf_parser=PypdfAdapter())
    assert injected._locator_gate(lineage, None).passed
    assert injected._locator_gate(lineage, None).passed
    assert AutomaticAdmissionGate(None)._locator_gate(lineage, None).passed

    assert len(calls) == 3


def test_cache_keys_actual_bytes_not_the_declared_digest(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw_a, raw_b = _pdf("Actual original."), _pdf("Actual changed.")
    declared = hashlib.sha256(raw_a).hexdigest()
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache()
    first = cache.parse(PypdfAdapter(), raw_a, document_sha256=declared)
    second = cache.parse(PypdfAdapter(), raw_b, document_sha256=declared)
    assert first[0].verbatim_text != second[0].verbatim_text
    assert len(calls) == 2


@pytest.mark.parametrize("identity", ["stamp", "package", "config", "implementation"])
def test_parser_identity_changes_cannot_reuse_cached_results(monkeypatch, identity):
    from app.datasources import docling
    from app.services.pdf_replay_cache import PdfReplayCache

    raw = _pdf("Exact parser identity.")
    digest = hashlib.sha256(raw).hexdigest()
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache()
    cache.parse(PypdfAdapter(), raw, document_sha256=digest)
    if identity == "stamp":
        monkeypatch.setattr(PypdfAdapter, "parser_version", "different-exact-stamp")
    elif identity == "package":
        monkeypatch.setattr(docling, "PYPDF_PACKAGE_VERSION", "different-package")
    elif identity == "config":
        monkeypatch.setattr(docling, "PYPDF_CONFIG_VERSION", "different-config")
    else:
        original = PypdfAdapter.extract_spans

        def changed(self, raw, *, document_sha256):
            return original(self, raw, document_sha256=document_sha256)

        monkeypatch.setattr(PypdfAdapter, "extract_spans", changed)
    cache.parse(PypdfAdapter(), raw, document_sha256=digest)
    assert len(calls) == 2


def test_unknown_instance_configuration_bypasses_cache(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw = _pdf("Unknown configuration.")
    digest = hashlib.sha256(raw).hexdigest()
    calls = _count_real_parses(monkeypatch)
    parser = PypdfAdapter()
    parser.unknown_configuration = True
    cache = PdfReplayCache()
    cache.parse(parser, raw, document_sha256=digest)
    cache.parse(parser, raw, document_sha256=digest)
    assert len(calls) == 2


def test_parser_failures_are_never_cached(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw = b"%PDF-1.7 incomplete"
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache()
    for _ in range(2):
        with pytest.raises(PdfReadError):
            cache.parse(PypdfAdapter(), raw, document_sha256=hashlib.sha256(raw).hexdigest())
    assert len(calls) == 2


def test_cache_evicts_least_recent_entry(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raws = [_pdf(f"LRU entry {index}.") for index in range(3)]
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache(max_entries=2)
    for index in (0, 1, 0, 2, 0, 1):
        raw = raws[index]
        cache.parse(PypdfAdapter(), raw, document_sha256=hashlib.sha256(raw).hexdigest())
    assert len(calls) == 4


def test_oversized_results_are_not_retained(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw = _pdf("Oversized for the configured budget.")
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache(max_bytes=1)
    for _ in range(2):
        cache.parse(PypdfAdapter(), raw, document_sha256=hashlib.sha256(raw).hexdigest())
    assert len(calls) == 2


def test_total_payload_budget_evicts_before_entry_limit(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw_a, raw_b = _pdf("Payload budget A."), _pdf("Payload budget B.")
    probe = PdfReplayCache()
    for raw in (raw_a, raw_b):
        probe.parse(PypdfAdapter(), raw, document_sha256=hashlib.sha256(raw).hexdigest())
    budget = sum(len(payload) for payload in probe._entries.values()) - 1
    calls = _count_real_parses(monkeypatch)
    cache = PdfReplayCache(max_entries=16, max_bytes=budget)
    for raw in (raw_a, raw_b, raw_a):
        cache.parse(PypdfAdapter(), raw, document_sha256=hashlib.sha256(raw).hexdigest())
    assert len(calls) == 3
    assert sum(len(payload) for payload in cache._entries.values()) <= budget


def test_concurrent_cold_misses_keep_accounting_and_return_values_isolated(monkeypatch):
    from app.services.pdf_replay_cache import PdfReplayCache

    raw = _pdf("Thread-safe replay.")
    digest = hashlib.sha256(raw).hexdigest()
    rendezvous = Barrier(4)
    original = PypdfAdapter.extract_spans

    def concurrent(self, raw, *, document_sha256):
        rendezvous.wait(timeout=5)
        return original(self, raw, document_sha256=document_sha256)

    monkeypatch.setattr(PypdfAdapter, "extract_spans", concurrent)
    cache = PdfReplayCache(max_entries=2, max_bytes=65536)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda _: cache.parse(PypdfAdapter(), raw, document_sha256=digest), range(4)
        ))
    assert len(cache._entries) == 1
    assert cache._payload_bytes == sum(len(payload) for payload in cache._entries.values())
    results[0][0].locator.extra["poison"] = True
    assert all("poison" not in spans[0].locator.extra for spans in results[1:])
    assert "poison" not in cache.parse(PypdfAdapter(), raw, document_sha256=digest)[0].locator.extra


@pytest.mark.parametrize("permission", ["allow_display", "allow_ai_processing"])
def test_warm_parse_never_caches_source_contract_authorization(session, permission):
    from tests.test_automatic_admission import NOW, _seed

    if session.get_bind().dialect.name != "sqlite":
        pytest.skip("contract withdrawal is a SQLite-only tamper fixture")
    raw = _pdf("Example Corp 2026-12-31 Revenue was 100 USD.")
    source = _lineages(raw)[0]
    seeded = _seed(
        session, raw_bytes=raw, text=source.span.verbatim_text,
        mime_type="application/pdf", parser_version=source.document.parser_version,
        locator_v1=source.span.locator_v1,
        span_text_sha256=source.span.text_sha256,
        span_context_hash=source.span.context_hash,
        entity_names=("Example Corp",), metric_terms=("Revenue",),
        subject="Example Corp", predicate="Revenue", unit="USD",
        normalized_text=source.span.verbatim_text,
    )
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    lineage = gate._load_lineage(seeded.candidate, seeded.context, seeded.job)
    assert gate._source_gate(lineage, seeded.context, evaluation_at=NOW).passed
    assert gate._locator_gate(lineage, seeded.context).passed

    setattr(lineage.contract, permission, False)

    assert gate._locator_gate(lineage, seeded.context).passed
    result = gate._source_gate(lineage, seeded.context, evaluation_at=NOW)
    assert not result.passed
    assert "source_contract_permissions_denied" in result.facts["failures"]
