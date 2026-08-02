"""Tests for the PDF parse path and the storage-chain gold seed.

Covers ``app.services.pdf_text`` span extraction against the committed binary
PDF fixture (paragraph splitting, CJK soft-wrap joining, table-block line
preservation, fail-closed on text-less PDFs) and the storage-chain seed's
end-to-end invariants (traceability, review coverage, PDF parser stamping).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.ledger import (
    AIAssessment,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
)
from app.services.pdf_text import PARSER_VERSION, PdfParseError, extract_spans

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "storage_chain"
    / "06_sungrow_annual_summary.pdf"
)


# ---------------------------------------------------------------------------
# pdf_text span extraction
# ---------------------------------------------------------------------------


def test_pdf_extracts_spans_with_reproducible_locators():
    spans = extract_spans(FIXTURE.read_bytes())
    assert spans, "committed PDF fixture must yield spans"
    for locator, text in spans:
        assert locator["parser"] == PARSER_VERSION
        assert isinstance(locator["page"], int) and locator["page"] >= 1
        assert isinstance(locator["paragraph"], int)
        assert text.strip()


def test_pdf_joins_soft_wrapped_cjk_lines():
    """pypdf breaks CJK mid-word at draw boundaries; narrative paragraphs must
    be rejoined without spaces."""
    spans = extract_spans(FIXTURE.read_bytes())
    page1 = " ".join(text for loc, text in spans if loc["page"] == 1)
    assert "归母净利润110.4亿元" in page1  # was split as 归母净\\n利润
    assert "储能系统收入298.5亿元" in page1


def test_pdf_preserves_table_block_line_structure():
    """Table regions must keep row layout for the rule-based extractor."""
    from app.services.table_extraction import FinancialTableExtractor

    spans = extract_spans(FIXTURE.read_bytes())
    table_texts = [text for loc, text in spans if loc["page"] == 2]
    assert any("\n" in text for text in table_texts)
    facts = []
    for text in table_texts:
        facts.extend(FinancialTableExtractor().extract(text))
    keys = {(f.metric_name, f.observed_period.isoformat()) for f in facts}
    assert ("revenue", "2025-12-31") in keys
    assert ("revenue_segment:储能系统", "2025-12-31") in keys
    assert len(facts) == 8


def test_pdf_fail_closed_on_textless_pdf():
    """A PDF with no text layer must raise, never silently yield zero spans."""
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()  # blank page: no text operators at all
    c.save()
    with pytest.raises(PdfParseError):
        extract_spans(buf.getvalue())


# ---------------------------------------------------------------------------
# Storage-chain seed invariants
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_session(session):
    from app.scripts.seed_storage_chain_case import seed

    seed(session)
    return session


def test_storage_seed_case_and_assessments(storage_session):
    case = storage_session.scalars(
        select(ResearchCase).where(ResearchCase.industry_topic == "storage_chain")
    ).one()
    assert case.title == "锂电储能链"

    assessments = storage_session.scalars(select(AIAssessment)).all()
    assert len(assessments) == 3
    conclusions = {a.conclusion for a in assessments}
    assert conclusions == {"supported", "insufficient_evidence", "contradicted"}

    reviewed = storage_session.scalars(select(ReviewDecision)).all()
    assert {r.ai_assessment_id for r in reviewed} == {a.id for a in assessments}


def test_storage_seed_pdf_document_stamped_with_pypdf_parser(storage_session):
    row = storage_session.execute(
        select(DocumentVersion.parser_version, func.count())
        .group_by(DocumentVersion.parser_version)
    ).all()
    parsers = dict(row)
    assert parsers.get(PARSER_VERSION) == 1
    assert parsers.get("docling-v1") == 5


def test_storage_seed_assessments_traceable_to_spans(storage_session):
    """Every assessment must trace snapshot → link → statement → span."""
    for assessment in storage_session.scalars(select(AIAssessment)).all():
        snapshot = storage_session.get(EvidenceSnapshot, assessment.snapshot_id)
        assert snapshot is not None and snapshot.evidence_link_ids
        for link_id in snapshot.evidence_link_ids:
            import uuid as _uuid

            link = storage_session.get(EvidenceLink, _uuid.UUID(link_id))
            assert link is not None
            statement = storage_session.get(
                SourceStatement, link.source_statement_id
            )
            assert statement is not None
            assert (
                storage_session.get(SourceSpan, statement.source_span_id)
                is not None
            )


def test_storage_seed_pdf_statement_originates_from_pdf_span(storage_session):
    """The PDF-sourced statement must sit on a pypdf-parsed span."""
    stmt = storage_session.scalars(
        select(SourceStatement).where(
            SourceStatement.normalized_text.contains("储能系统收入298.5亿元")
        )
    ).one()
    span = storage_session.get(SourceSpan, stmt.source_span_id)
    assert span.locator["parser"] == PARSER_VERSION
