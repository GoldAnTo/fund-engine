"""S5 end-to-end smoke: v1 fields flow from ingest through the v1 read
API and out the DTO.

Covers the S5 acceptance criterion that every span written by a v1-aware
path round-trips through ``GET /api/v1/documents/{id}`` carrying the
upgraded ``locator_v1`` and ``text_sha256`` fields, while legacy spans
keep working unchanged.

Spec: ``docs/research/2026-08-02-docling-and-source-locator-v1-spec.md``
sections 3.5 (compat) and 5 (acceptance).
"""
from __future__ import annotations

import io

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as rl_canvas

from app.datasources.docling import PypdfAdapter
from app.documents.locators import compute_text_sha256
from app.repositories.documents import DocumentRepository
from app.services.ingest import DocumentService

_CJK_FONT = "STSong-Light"
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
except KeyError:
    pass


def _make_text_pdf(paragraphs: list[str]) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.setFont(_CJK_FONT, 11)
    y = 750
    for para_idx, para in enumerate(paragraphs):
        if para_idx > 0:
            c.drawString(50, y, " ")
            y -= 14
        for line in para.splitlines() or [para]:
            c.drawString(50, y, line)
            y -= 16
    c.save()
    return buf.getvalue()


@pytest.fixture
def doc_service(session) -> DocumentService:
    return DocumentService(DocumentRepository(session))


@pytest.fixture
def fresh_db(engine):
    from app.models.ledger import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def test_v1_span_round_trips_through_v1_read_api(fresh_db, session, doc_service, api_client):
    raw = _make_text_pdf(["收入端", "成本端"])
    version = doc_service.freeze(
        raw=raw,
        source_url="https://example.test/s5-1",
        title="Q3 2025 报告",
    )
    digest = version.content_sha256
    parsed_spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    assert len(parsed_spans) == 2

    for parsed in parsed_spans:
        doc_service.add_span(
            document_version_id=version.id,
            locator=parsed.legacy_locator_dict(),
            verbatim_text=parsed.verbatim_text,
            text_sha256=parsed.text_sha256,
            context_hash=parsed.context_hash,
            locator_v1=parsed.locator.to_storage_dict(),
        )
    session.flush()

    response = api_client.get(f"/api/v1/documents/{version.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document"]["title"] == "Q3 2025 报告"
    spans = body["spans"]
    assert len(spans) == 2
    # Pair by verbatim_text — UUID ordering is not stable across the
    # session flush boundary.
    dto_by_text = {s["verbatim_text"]: s for s in spans}
    for parsed in parsed_spans:
        dto = dto_by_text[parsed.verbatim_text]
        assert dto["locator_v1"] is not None
        assert dto["locator_v1"]["schema"] == "source-locator/v1"
        assert dto["locator_v1"]["page"] == parsed.locator.page
        assert dto["locator_v1"]["parser_version"] == "pypdf-v1"
        assert dto["text_sha256"] == parsed.text_sha256
        assert dto["text_sha256"] == compute_text_sha256(dto["verbatim_text"])


def test_legacy_span_does_not_carry_v1_fields(fresh_db, session, doc_service, api_client):
    """A span written through the legacy ``add_span(locator=...)`` path
    must keep returning ``locator_v1=None`` and ``text_sha256=None``
    so the workbench can fall back to the free-form ``locator`` dict.
    """
    raw = b"workbench source bytes"
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/s5-2"
    )
    doc_service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
        verbatim_text="workbench source",
    )
    session.flush()

    response = api_client.get(f"/api/v1/documents/{version.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["spans"]) == 1
    span_dto = body["spans"][0]
    assert span_dto["locator_v1"] is None
    assert span_dto["text_sha256"] is None
    # Legacy locator still in place.
    assert span_dto["locator"]["page"] == 1
    assert span_dto["locator"]["parser"] == "pypdf-v1"


def test_legacy_then_v1_mix_in_one_document(fresh_db, session, doc_service, api_client):
    """A document that mixes legacy and v1 spans (the realistic S5 state
    before the S4 backfill completes) must return v1 fields for the
    upgraded spans and leave the legacy span's DTO fields at None.
    """
    raw = _make_text_pdf(["hybrid"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/s5-3"
    )
    digest = version.content_sha256
    parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)[0]
    # v1 span
    doc_service.add_span(
        document_version_id=version.id,
        locator=parsed.legacy_locator_dict(),
        verbatim_text=parsed.verbatim_text,
        text_sha256=parsed.text_sha256,
        locator_v1=parsed.locator.to_storage_dict(),
    )
    # legacy span
    doc_service.add_span(
        document_version_id=version.id,
        locator={"page": 9, "paragraph": 9, "parser": "pypdf-v1"},
        verbatim_text="legacy span",
    )
    session.flush()

    response = api_client.get(f"/api/v1/documents/{version.id}")
    body = response.json()
    by_text = {s["verbatim_text"]: s for s in body["spans"]}
    v1_dto = by_text[parsed.verbatim_text]
    leg_dto = by_text["legacy span"]
    assert v1_dto["locator_v1"] is not None
    assert v1_dto["text_sha256"] is not None
    assert leg_dto["locator_v1"] is None
    assert leg_dto["text_sha256"] is None
    # The legacy span's free-form locator still carries the display keys.
    assert leg_dto["locator"]["page"] == 9
