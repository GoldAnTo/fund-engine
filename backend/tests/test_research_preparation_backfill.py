from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Base, CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.models.operational import EventResearchLifecycle, Job, ResearchRun
from app.models.research_preparation import ResearchPreparation


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
