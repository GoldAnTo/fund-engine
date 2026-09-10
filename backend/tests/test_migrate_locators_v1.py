"""Tests for the v1 locator backfill script
(``app.scripts.migrate_locators_v1``).

Covers:

- Spans with a legacy ``{page, paragraph, parser}`` locator get a
  ``locator_v1`` filled in.
- Spans that already have ``locator_v1`` are skipped (idempotent).
- Spans with no recoverable localization channel are reported as
  unrecoverable and left untouched.
- The dry-run mode does not write back.
- The S4 model fields (``text_sha256``, ``context_hash``, ``title``,
  ``byte_size``, ``language``, ``parse_state``) round-trip through
  ``DocumentService.add_span`` / ``freeze``.
"""
from __future__ import annotations

import io
from datetime import date

import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as rl_canvas

from app.datasources.docling import PypdfAdapter
from app.documents.locators import compute_text_sha256
from app.repositories.documents import DocumentRepository
from app.scripts.migrate_locators_v1 import migrate
from app.services.ingest import DocumentService

# CJK-safe PDF font (test fixtures only).  Registering twice raises, so
# guard with try/except — pytest may import this module alongside
# test_pdf_parser_adapters which already registered the same name.
_CJK_FONT = "STSong-Light"
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
except KeyError:
    pass


@pytest.fixture(autouse=True)
def _isolate_db(engine):
    """Reset the schema between tests so this module's writes do not
    leak into other modules' session-scoped data.  The shared engine
    fixture from conftest keeps the connection alive (StaticPool); we
    drop and re-create the tables so each test sees a clean slate.
    """
    from app.models.ledger import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    # No teardown needed; the next test's setup will re-create.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Migration script
# ---------------------------------------------------------------------------


def test_migrate_upgrades_legacy_pypdf_locator(session, doc_service):
    raw = _make_text_pdf(["营业收入 50 亿元"])
    version = doc_service.freeze(
        raw=raw,
        source_url="https://example.test/migrate-1",
        parser_version="pypdf-v1",
    )
    doc_service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
        verbatim_text="营业收入 50 亿元",
    )
    session.flush()

    stats = migrate(session)
    session.commit()

    assert stats.scanned == 1
    assert stats.upgraded == 1
    assert stats.skipped_already_v1 == 0
    assert stats.skipped_unrecoverable == 0

    # Re-fetch the span and confirm the v1 column was written.
    from app.models.ledger import SourceSpan

    span = session.query(SourceSpan).one()
    assert span.locator_v1 is not None
    assert span.locator_v1["schema"] == "source-locator/v1"
    assert span.locator_v1["page"] == 1
    assert span.locator_v1["parser_version"] == "pypdf-v1"
    assert span.locator_v1["parser_item_ref"] == "#/legacy-paragraph/1"
    # Legacy locator still in place (immutable ledger).
    assert span.locator["paragraph"] == 1


def test_migrate_is_idempotent(session, doc_service):
    raw = _make_text_pdf(["hello"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/migrate-2"
    )
    doc_service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
        verbatim_text="hello",
    )
    session.flush()

    first = migrate(session)
    session.commit()
    second = migrate(session)
    # Second pass: 0 upgraded, 1 skipped (already has v1).
    assert first.upgraded == 1
    assert second.upgraded == 0
    assert second.skipped_already_v1 == 1


def test_migrate_marks_unrecoverable_locator(session, doc_service):
    """A span with no page / no paragraph / nothing usable cannot be
    upgraded — the script reports it and leaves the row alone."""
    raw = _make_text_pdf(["orphan"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/migrate-3"
    )
    doc_service.add_span(
        document_version_id=version.id,
        locator={"parser": "pypdf-v1"},  # no page, no paragraph
        verbatim_text="orphan",
    )
    session.flush()

    stats = migrate(session)
    session.commit()

    assert stats.scanned == 1
    assert stats.upgraded == 0
    assert stats.skipped_unrecoverable == 1
    # Example included in the report for operator triage.
    assert len(stats.unrecoverable_examples) == 1
    assert stats.unrecoverable_examples[0]["locator"] == {"parser": "pypdf-v1"}


# ---------------------------------------------------------------------------
# DocumentService + repository round-trip the S4 fields
# ---------------------------------------------------------------------------


def test_document_service_freeze_persists_s4_meta(session, doc_service):
    raw = _make_text_pdf(["alpha"])
    version = doc_service.freeze(
        raw=raw,
        source_url="https://example.test/s4-1",
        title="Q3 2025 报告",
        language="zh",
        parse_state="success",
    )
    session.flush()
    assert version.title == "Q3 2025 报告"
    assert version.language == "zh"
    assert version.parse_state == "success"
    # byte_size defaults to len(raw) when caller doesn't pass it.
    assert version.byte_size == len(raw)


def test_document_service_freeze_backfills_byte_size(session, doc_service):
    raw = _make_text_pdf(["size test"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/s4-2"
    )
    assert version.byte_size == len(raw)


def test_add_span_persists_text_sha256_and_locator_v1(session, doc_service):
    raw = _make_text_pdf(["hash me"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/s4-3"
    )
    locator_v1 = {
        "schema": "source-locator/v1",
        "document_sha256": version.content_sha256,
        "page": 1,
        "parser_version": "pypdf-v1",
        "text_position": {"start": 0, "end": 7},
    }
    doc_service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 1, "parser": "pypdf-v1"},
        verbatim_text="hash me",
        text_sha256=compute_text_sha256("hash me"),
        context_hash="deadbeef" * 4,
        locator_v1=locator_v1,
    )
    session.flush()

    from app.models.ledger import SourceSpan

    span = session.query(SourceSpan).one()
    assert span.text_sha256 == compute_text_sha256("hash me")
    assert span.context_hash == "deadbeef" * 4
    assert span.locator_v1 == locator_v1
    # Legacy column untouched.
    assert span.locator == {
        "page": 1,
        "paragraph": 1,
        "parser": "pypdf-v1",
    }


def test_pypdf_adapter_pipeline_writes_v1(session, doc_service):
    """End-to-end: a PypdfAdapter result + DocumentService round-trips v1
    fields into the ledger without manual coercion."""
    raw = _make_text_pdf(["收入端", "成本端"])
    version = doc_service.freeze(
        raw=raw, source_url="https://example.test/s4-4"
    )
    digest = version.content_sha256
    spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)
    assert len(spans) == 2
    for parsed in spans:
        doc_service.add_span(
            document_version_id=version.id,
            locator=parsed.legacy_locator_dict(),
            verbatim_text=parsed.verbatim_text,
            text_sha256=parsed.text_sha256,
            context_hash=parsed.context_hash,
            locator_v1=parsed.locator.to_storage_dict(),
        )
    session.flush()

    from app.models.ledger import SourceSpan

    persisted = list(session.scalars(select(SourceSpan)))
    assert len(persisted) == 2
    # Pair by verbatim_text — UUID ordering is not stable across the
    # SQLAlchemy session flush boundary.
    persisted_by_text = {s.verbatim_text: s for s in persisted}
    for parsed in spans:
        span = persisted_by_text[parsed.verbatim_text]
        assert span.text_sha256 == parsed.text_sha256
        assert span.context_hash == parsed.context_hash
        assert span.locator_v1 == parsed.locator.to_storage_dict()


# Late import to avoid a circular path during fixture resolution.
from sqlalchemy import select  # noqa: E402
