from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Base, CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase, SourceSpan
from app.models.operational import EventResearchLifecycle, Job, ResearchRun
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact
from app.models.source_governance import SourceContract
from app.services.atomic_claims import AtomicClaimService


def test_backfill_module_is_available() -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    assert callable(ResearchPreparationBackfill.enqueue_eligible)


def _eligible_case(session, *, created_at, admit: bool = True, link_document: bool = True, lifecycle: bool = True, scope: bool = True, allow_ai_processing: bool = True, contract_effective_from=None, contract_effective_until=None, document_available_at=None):
    case = ResearchCase(title="old", industry_topic="test", created_by="test", created_at=created_at)
    document = DocumentVersion(content_sha256=uuid.uuid4().hex * 2, source_url="https://example.test/backfill", available_at=document_available_at or created_at, acquired_at=created_at, parser_version="test")
    session.add_all((case, document))
    session.flush()
    rows = []
    if link_document:
        rows.append(CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=created_at))
    if admit:
        rows.append(CaseTenantAdmission(research_case_id=case.id, tenant_id="test", initial_document_version_id=document.id, admitted_by="test", admitted_at=created_at))
    if scope:
        rows.append(EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="test", change_summary="scope", created_at=created_at))
    if lifecycle:
        rows.append(EventResearchLifecycle(research_case_id=case.id, status="awaiting_scope", active_run_id=None, current_round=0, status_summary="waiting", current_gap=None, next_human_action=None, updated_at=created_at))
    session.add_all(rows)
    session.add(SourceContract(
        document_version_id=document.id,
        source_type="company_disclosure",
        provider_or_tenant="issuer",
        allow_ai_processing=allow_ai_processing,
        allow_display=True,
        allow_export=False,
        allow_api=False,
        region="CN",
        effective_from=contract_effective_from,
        effective_until=contract_effective_until,
        retention_policy="case_retained",
        deletion_policy="manual",
        downstream_restrictions=[],
        contract_version="test",
        intake_metadata={},
        declared_by="test",
        created_at=created_at,
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


def test_backfill_reused_reviewed_candidates_queue_protocol_but_partial_review_stays_gated(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-reviewed.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        fully_reviewed = _eligible_case(session, created_at=now - timedelta(minutes=1))
        partial = _eligible_case(session, created_at=now)
        candidates = []
        for case, suffix in ((fully_reviewed, "full"), (partial, "partial-a"), (partial, "partial-b")):
            document_id = session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == case.id))
            text = f"Revenue {suffix} grew."
            span = SourceSpan(document_version_id=document_id, locator={"page": 1}, verbatim_text=text)
            session.add(span)
            session.flush()
            candidate = AtomicClaimService(session).admit(
                AtomicClaimDraft(source_span_id=span.id, quote=text, quote_start=0, quote_end=len(text), normalized_text=text, claim_type="reported_claim", assertion_actor=None, subject=None, predicate=None, object_text=None, numeric_value=None, unit=None, observed_period=None, scope={}),
                authority_level="primary_disclosure", run_ref=suffix,
            )
            candidates.append(candidate)
        for candidate in (candidates[0], candidates[1]):
            AtomicClaimService(session).review(candidate.id, outcome="confirmed", reviewer="reviewer", reason="reviewed", idempotency_key=f"review-{candidate.id}")
        preparations = ResearchPreparationBackfill(session).enqueue_eligible()
        session.commit()
        by_case = {preparation.research_case_id: preparation.id for preparation in preparations}
        fully_reviewed_id, partial_id = fully_reviewed.id, partial.id
    with sessions() as check:
        full = check.get(ResearchPreparation, by_case[fully_reviewed_id])
        partial_prep = check.get(ResearchPreparation, by_case[partial_id])
        assert full is not None and full.claim_review_state == "confirmed" and full.status == "preparing"
        assert partial_prep is not None and partial_prep.claim_review_state == "awaiting_review"
        full_jobs = list(check.scalars(select(Job).where(Job.target_id == full.id)))
        partial_jobs = list(check.scalars(select(Job).where(Job.target_id == partial_prep.id)))
        assert [job.correlation_id.rsplit(":", 1)[-1] for job in full_jobs] == ["draft_protocol"]
        assert partial_jobs == []
        assert check.scalars(select(ResearchRun)).all() == []


def test_backfill_excludes_an_ai_disallowed_initial_source(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-contract.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as session:
        _eligible_case(session, created_at=datetime.now(UTC), allow_ai_processing=False)
        assert ResearchPreparationBackfill(session).enqueue_eligible() == []
        assert session.scalars(select(ResearchPreparation)).all() == []
        assert session.scalars(select(Job)).all() == []


@pytest.mark.parametrize(
    "contract_kwargs",
    [
        {"contract_effective_from": datetime.now(UTC) + timedelta(days=1)},
        {"contract_effective_until": datetime.now(UTC) + timedelta(days=1), "document_available_at": datetime.now(UTC) + timedelta(days=2)},
    ],
)
def test_backfill_requires_contract_to_cover_document_availability(tmp_path, contract_kwargs) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-contract-window.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as session:
        _eligible_case(session, created_at=datetime.now(UTC), **contract_kwargs)
        assert ResearchPreparationBackfill(session).enqueue_eligible() == []
        assert session.scalars(select(ResearchPreparation)).all() == []


def test_backfill_skips_an_old_unavailable_case_to_fill_limit(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-starvation-one.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        _eligible_case(session, created_at=now - timedelta(minutes=2), allow_ai_processing=False)
        eligible = _eligible_case(session, created_at=now - timedelta(minutes=1))
        eligible_id = eligible.id
        preparations = ResearchPreparationBackfill(session).enqueue_eligible(limit=1)
        assert [preparation.research_case_id for preparation in preparations] == [eligible_id]
        session.commit()
    with sessions() as check:
        assert len(check.scalars(select(ResearchPreparation)).all()) == 1
        assert check.scalar(select(Job)).correlation_id.endswith(":parse_claims")


def test_backfill_pages_past_many_unavailable_cases_and_remains_idempotent(tmp_path) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill-starvation-many.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        for index in range(150):
            _eligible_case(
                session,
                created_at=now - timedelta(minutes=200 - index),
                allow_ai_processing=False,
            )
        first = _eligible_case(session, created_at=now - timedelta(minutes=2))
        second = _eligible_case(session, created_at=now - timedelta(minutes=1))
        first_id, second_id = first.id, second.id
        preparations = ResearchPreparationBackfill(session).enqueue_eligible(limit=2)
        assert [preparation.research_case_id for preparation in preparations] == [first_id, second_id]
        session.commit()
    with sessions() as check:
        assert ResearchPreparationBackfill(check).enqueue_eligible(limit=2) == []
        assert len(check.scalars(select(ResearchPreparation)).all()) == 2
        assert len(check.scalars(select(Job)).all()) == 2


@pytest.mark.parametrize(
    "excluded_by",
    ["admission", "document", "lifecycle", "published", "run", "preparation", "scope"],
)
def test_backfill_excludes_each_ineligible_case(tmp_path, excluded_by: str) -> None:
    from app.services.research_preparation_backfill import ResearchPreparationBackfill
    from app.services.research_preparation import ResearchPreparationService

    engine = create_engine(f"sqlite:///{tmp_path / f'backfill-{excluded_by}.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime.now(UTC)
    with sessions() as session:
        case = _eligible_case(
            session,
            created_at=now,
            admit=excluded_by != "admission",
            link_document=excluded_by != "document",
            lifecycle=excluded_by != "lifecycle",
            scope=excluded_by != "scope",
        )
        if excluded_by == "published":
            session.get(EventResearchLifecycle, case.id).status = "published"
        elif excluded_by == "run":
            session.add(ResearchRun(research_case_id=case.id, status="succeeded", stage="completed", round=1, max_rounds=1, budget=1, budget_used=1, created_at=now, updated_at=now))
        elif excluded_by == "preparation":
            ResearchPreparationService(session).create_for_case(case.id, input_fingerprint="a" * 64, actor="existing")
        session.flush()
        preparations_before = list(session.scalars(select(ResearchPreparation)))
        jobs_before = list(session.scalars(select(Job)))
        assert ResearchPreparationBackfill(session).enqueue_eligible() == []
        assert list(session.scalars(select(ResearchPreparation))) == preparations_before
        assert list(session.scalars(select(Job))) == jobs_before
