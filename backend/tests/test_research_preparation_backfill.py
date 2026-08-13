from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Base, CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase, SourceSpan
from app.models.operational import EventResearchLifecycle, Job, ResearchRun
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact
from app.services.atomic_claims import AtomicClaimService


def test_backfill_module_is_available() -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    assert callable(ResearchPreparationBackfill.enqueue_eligible)


def _eligible_case(session, *, created_at):
    case = ResearchCase(title="old", industry_topic="test", created_by="test", created_at=created_at)
    document = DocumentVersion(content_sha256=uuid.uuid4().hex * 2, source_url="https://example.test/backfill", available_at=created_at, acquired_at=created_at, parser_version="test")
    session.add_all((case, document))
    session.flush()
    session.add_all((
        CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=created_at),
        CaseTenantAdmission(research_case_id=case.id, tenant_id="test", initial_document_version_id=document.id, admitted_by="test", admitted_at=created_at),
        EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="test", change_summary="scope", created_at=created_at),
        EventResearchLifecycle(research_case_id=case.id, status="awaiting_scope", active_run_id=None, current_round=0, status_summary="waiting", current_gap=None, next_human_action=None, updated_at=created_at),
    ))
    session.flush()
    return case


def test_backfill_is_oldest_first_idempotent_and_queues_parse_only(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        oldest = _eligible_case(session, created_at=now - timedelta(minutes=2))
        newest = _eligible_case(session, created_at=now - timedelta(minutes=1))
        oldest_id, newest_id = oldest.id, newest.id
        first = ResearchPreparationBackfill(session).enqueue_eligible(limit=1)
        assert [preparation.research_case_id for preparation in first] == [oldest_id]
        session.commit()
    with sessions() as session:
        second = ResearchPreparationBackfill(session).enqueue_eligible(limit=10)
        assert [preparation.research_case_id for preparation in second] == [newest_id]
        session.commit()
    with sessions() as session:
        assert ResearchPreparationBackfill(session).enqueue_eligible(limit=10) == []
        preparations = list(session.scalars(select(ResearchPreparation).order_by(ResearchPreparation.created_at)))
        assert len(preparations) == 2
        assert all(preparation.parse_claims_state == "queued" for preparation in preparations)
        assert all(preparation.draft_protocol_state == "queued" for preparation in preparations)
        assert session.scalar(select(Job).where(Job.target_id == preparations[0].id)).correlation_id.endswith(":parse_claims")
        assert session.scalars(select(ResearchRun)).all() == []


def test_backfill_reuses_existing_candidates_without_a_parse_job(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-reuse.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        case = _eligible_case(session, created_at=now)
        document_id = session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == case.id))
        span = SourceSpan(document_version_id=document_id, locator={"page": 1}, verbatim_text="Revenue grew ten percent.")
        session.add(span)
        session.flush()
        candidate = AtomicClaimService(session).admit(
            AtomicClaimDraft(source_span_id=span.id, quote="Revenue grew", quote_start=0, quote_end=12, normalized_text="Revenue grew ten percent", claim_type="reported_claim", assertion_actor=None, subject=None, predicate=None, object_text=None, numeric_value=None, unit=None, observed_period=None, scope={}),
            authority_level="primary_disclosure",
            run_ref="historical",
        )
        preparation = ResearchPreparationBackfill(session).enqueue_eligible()[0]
        session.commit()
        preparation_id = preparation.id
        candidate_id = candidate.id
    with sessions() as check:
        preparation = check.get(ResearchPreparation, preparation_id)
        artifact = check.scalar(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation_id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        ))
        assert preparation is not None and preparation.parse_claims_state == "succeeded"
        assert preparation.claim_review_state == "awaiting_review"
        assert artifact is not None and artifact.payload == {"candidates": [{"candidate_id": str(candidate_id)}]}
        assert check.scalars(select(Job).where(Job.target_id == preparation_id)).all() == []
        assert check.scalars(select(ResearchRun)).all() == []
