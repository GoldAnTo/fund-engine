"""Acceptance tests for the frozen AI-compute evidence slice (plan Task 8, Step 1).

These tests exercise ``app.scripts.seed_ai_compute_case.seed`` against an
in-memory SQLite ledger and assert the required auditable minimum, plus full
traceability from every AIAssessment back to a SourceSpan and from every
HoldingDisclosure to a themed Stock.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.ledger import (
    AIAssessment,
    Company,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    ThemeRole,
    Thesis,
)
from app.scripts.seed_ai_compute_case import seed


def _count(session, model) -> int:
    return len(list(session.scalars(select(model))))


def test_ai_compute_seed_has_required_auditable_minimum(session):
    seed(session)

    assert _count(session, ResearchCase) == 1
    assert _count(session, Thesis) == 3
    assert _count(session, DocumentVersion) >= 6
    assert _count(session, SourceSpan) >= 30
    assert _count(session, Company) == 3
    assert _count(session, Fund) == 2

    disclosures = list(session.scalars(select(HoldingDisclosure)))
    assert disclosures, "expected at least one holding disclosure"
    for disclosure in disclosures:
        assert disclosure.report_period is not None
        assert disclosure.published_at is not None

    # Every assessment conclusion must appear at least once: supported,
    # contradicted, insufficient_evidence.
    conclusions = {a.conclusion for a in session.scalars(select(AIAssessment))}
    assert conclusions >= {"supported", "contradicted", "insufficient_evidence"}


def test_ai_compute_assessments_trace_back_to_source_spans(seeded_session):
    session = seeded_session
    assessments = list(session.scalars(select(AIAssessment)))
    assert assessments, "expected seeded AI assessments"

    for assessment in assessments:
        snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
        assert snapshot is not None
        assert snapshot.evidence_link_ids, (
            f"snapshot {snapshot.id} has no evidence links"
        )
        for link_id in snapshot.evidence_link_ids:
            link = session.get(EvidenceLink, uuid.UUID(link_id))
            assert link is not None
            statement = session.get(SourceStatement, link.source_statement_id)
            assert statement is not None
            span = session.get(SourceSpan, statement.source_span_id)
            assert span is not None
            assert span.verbatim_text, "span verbatim text must not be empty"


def test_ai_compute_held_stocks_carry_theme_role(seeded_session):
    session = seeded_session
    disclosures = list(session.scalars(select(HoldingDisclosure)))
    assert disclosures, "expected seeded holding disclosures"

    for disclosure in disclosures:
        stock = session.get(Stock, disclosure.stock_id)
        assert stock is not None
        roles = list(
            session.scalars(
                select(ThemeRole).where(ThemeRole.company_id == stock.company_id)
            )
        )
        assert roles, f"stock {stock.code} has no theme role on its company"
