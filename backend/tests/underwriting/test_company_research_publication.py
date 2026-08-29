from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from hashlib import sha256
from threading import Barrier, BrokenBarrierError
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import bindparam, create_engine, event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.hashing import canonical_hash
from app.underwriting.domain.company_research import (
    CompanyResearchCompany,
    CompanyResearchSecurity,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_models import (
    UnderwritingMarketCaptureEnvelope,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAssessmentVersion,
    UnderwritingResearchProjectSecurity,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.models import UnderwritingResearchVersion
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchJudgmentConfirmation,
    CompanyResearchPublicationService,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService
from tests.underwriting.test_company_research_persistence import (
    _rebind_historical_basis_without_updating_draft_lock,
    _substitute_basis,
    _tamper_row,
)
from tests.underwriting.test_company_research_workbench import (
    NOW,
    _durably_rewrite_payload,
    _model_workspace,
    _prepared,
    _rewrite_as_legacy_model_gap_successor,
)


CONFIRMED_AT = NOW + timedelta(minutes=5)
FROZEN_AT = CONFIRMED_AT + timedelta(minutes=5)
REVIEWER = "human:local-user"
REJECTED_FACT_KEY = "fy2025_other_bets_revenue"


def test_company_research_publication_service_is_the_public_confirmation_seam() -> None:
    assert CompanyResearchPublicationService.__name__ == (
        "CompanyResearchPublicationService"
    )


def _awaiting_judgment_confirmation(session):
    initialized = _prepared(session)
    workbench = CompanyResearchWorkbench(session, now=lambda: NOW)
    workspace = workbench.workspace(project_id=initialized.project.id)
    evidence = next(
        item.artifact for item in workspace.modules if item.key == "evidence_and_gaps"
    )
    assert evidence is not None

    current = evidence
    for fact in evidence.payload["facts"]:
        decision = "rejected" if fact["fact_key"] == REJECTED_FACT_KEY else "confirmed"
        current = workbench.review_evidence(
            project_id=initialized.project.id,
            evidence_artifact_id=current.id,
            fact_key=fact["fact_key"],
            decision=decision,
            expected_head_id=current.id,
        ).evidence_artifact

    worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert worker.run_claim(claim) == "awaiting_judgment_review"

    repository = workbench._company
    preparation = repository.preparation_for_project(initialized.project.id, fresh=True)
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    draft = session.scalar(
        select(UnderwritingWorkspaceDraft).where(
            UnderwritingWorkspaceDraft.project_id == initialized.project.id
        )
    )
    memo = repository.current_artifact(initialized.project.id, "memo")
    assert job is not None and draft is not None and memo is not None
    return initialized, repository, preparation, job, draft, memo


def _job_projection(job: Job) -> tuple[object, ...]:
    return (
        job.id,
        job.kind,
        job.status,
        job.progress,
        job.attempt,
        job.step,
        job.error,
        job.cancel_requested,
        job.claim_token,
        job.target_type,
        job.target_id,
        job.research_case_id,
        job.started_at,
        job.finished_at,
    )


def _row_projection(row) -> tuple[tuple[str, object], ...]:
    return tuple(
        (column.name, deepcopy(getattr(row, column.name)))
        for column in row.__table__.columns
    )


def _durable_publication_snapshot(
    session, *, project_id, preparation_id, job_id
) -> dict[str, object]:
    session.expire_all()
    repository = CompanyResearchRepository(session)
    preparation = repository.preparation_for_project(project_id, fresh=True)
    draft = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    job = session.scalar(
        select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
    )
    artifacts = tuple(
        session.scalars(
            select(CompanyResearchArtifactVersion)
            .where(CompanyResearchArtifactVersion.project_id == project_id)
            .order_by(
                CompanyResearchArtifactVersion.kind,
                CompanyResearchArtifactVersion.version,
                CompanyResearchArtifactVersion.id,
            )
            .execution_options(populate_existing=True)
        )
    )
    events = tuple(
        session.scalars(
            select(CompanyResearchEvent)
            .where(CompanyResearchEvent.preparation_id == preparation_id)
            .order_by(CompanyResearchEvent.sequence)
            .execution_options(populate_existing=True)
        )
    )
    assert preparation is not None and draft is not None and job is not None
    return {
        "preparation": _row_projection(preparation),
        "draft": _row_projection(draft),
        "job": _row_projection(job),
        "artifacts": tuple(_row_projection(row) for row in artifacts),
        "events": tuple(_row_projection(row) for row in events),
        "job_count": session.scalar(select(func.count()).select_from(Job)),
    }


def _confirmation_request(draft, memo, **changes) -> dict[str, object]:
    values: dict[str, object] = {
        "expected_lock_version": draft.lock_version,
        "expected_memo_id": memo.id,
        "expected_memo_content_hash": memo.content_hash,
        "markdown": "Reviewed conclusion.",
    }
    values.update(changes)
    return values


def _ready_to_freeze_workspace(session):
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    confirmation = CompanyResearchPublicationService(
        session, now=lambda: CONFIRMED_AT
    ).confirm_judgment(
        project_id=initialized.project.id,
        **_confirmation_request(draft, machine_memo),
    )
    session.flush()
    return (
        initialized,
        repository,
        preparation,
        job,
        draft,
        machine_memo,
        confirmation,
    )


def test_publication_preview_is_zero_write_and_keeps_not_answerable_closed(
    session, monkeypatch
) -> None:
    initialized, repository, preparation, job, _draft, _machine_memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )
    confirmed_memo = repository.current_artifact(initialized.project.id, "memo")
    assert confirmed_memo is not None
    pending = CompanyResearchEvent(
        preparation_id=uuid4(),
        sequence=1,
        hash_version=2,
        previous_event_hash=None,
        event_type="pending_unrelated",
        payload={},
        content_hash="0" * 64,
        created_at=FROZEN_AT,
    )
    session.add(pending)
    original_flush = session.flush

    def reject_flush(*args, **kwargs):
        raise AssertionError("publication preview must not flush")

    monkeypatch.setattr(session, "flush", reject_flush)
    writes: list[str] = []
    locked_reads: list[object] = []

    def capture_writes(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {
            "INSERT",
            "UPDATE",
            "DELETE",
        }:
            writes.append(statement)

    def capture_locked_reads(
        _connection, clauseelement, _multiparams, _params, _execution_options
    ):
        if getattr(clauseelement, "_for_update_arg", None) is not None:
            locked_reads.append(clauseelement)

    event.listen(session.bind, "before_cursor_execute", capture_writes)
    event.listen(session.bind, "before_execute", capture_locked_reads)

    try:
        preview = CompanyResearchPublicationService(
            session, now=lambda: FROZEN_AT
        ).preview(
            project_id=initialized.project.id,
            expected_lock_version=confirmation.draft_lock_version,
        )
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_writes)
        event.remove(session.bind, "before_execute", capture_locked_reads)

    assert preview.assessment.answerability == "not_answerable"
    assert preview.assessment.direction is None
    assert preview.assessment.confidence is None
    assert preview.value_range is None
    assert preview.return_range is None
    assert preview.blockers == tuple(confirmed_memo.payload["gap_keys"])
    assert pending in session.new
    assert writes == []
    assert locked_reads == [], [str(value) for value in locked_reads]
    monkeypatch.setattr(session, "flush", original_flush)
    with session.no_autoflush:
        assert (
            _durable_publication_snapshot(
                session,
                project_id=initialized.project.id,
                preparation_id=preparation.id,
                job_id=job.id,
            )
            == before
        )


def test_workbench_default_workspace_read_retains_locked_semantics(session) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, _confirmation = (
        _ready_to_freeze_workspace(session)
    )
    locked_reads: list[object] = []

    def capture_locked_reads(
        _connection, clauseelement, _multiparams, _params, _execution_options
    ):
        if getattr(clauseelement, "_for_update_arg", None) is not None:
            locked_reads.append(clauseelement)

    event.listen(session.bind, "before_execute", capture_locked_reads)
    try:
        CompanyResearchWorkbench(session, now=lambda: FROZEN_AT).workspace(
            project_id=initialized.project.id
        )
    finally:
        event.remove(session.bind, "before_execute", capture_locked_reads)

    assert locked_reads


def test_publish_freezes_one_company_research_revision_and_exactly_replays(
    session,
) -> None:
    initialized, repository, preparation, _job, _draft, _machine_memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )

    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="alphabet-first-freeze",
    )

    assert revision.sequence == 1
    assert revision.assessment.answerability == "not_answerable"
    assert revision.assessment.direction is None
    assert revision.assessment.confidence is None
    assert revision.value_range is None
    assert revision.return_range is None
    assert revision.preparation_status == "completed"
    assert revision.progress == 100
    assert revision.current_step is None
    assert revision.artifacts == preview.artifacts
    assert service.revision(initialized.project.id, revision.id) == revision
    assert (
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version,
            expected_manifest_hash=preview.manifest_hash,
            idempotency_key="alphabet-first-freeze",
        )
        == revision
    )

    session.expire_all()
    current_preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    current_draft = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == initialized.project.id)
        .execution_options(populate_existing=True)
    )
    assert current_preparation is not None
    assert (
        current_preparation.status,
        current_preparation.current_step,
        current_preparation.progress,
    ) == ("completed", None, 100)
    assert current_draft is not None
    assert current_draft.lock_version == preview.expected_lock_version + 1
    assert current_draft.base_revision_id == revision.id
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingResearchVersion))
        == 1
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingResearchAssessmentVersion)
        )
        == 1
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingRevisionBoundary))
        == 1
    )
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingRevisionManifest))
        == 1
    )
    events = repository.events(preparation.id)
    assert events[-1].event_type == "company_research_published"
    assert events[-1].payload == {
        "revision_id": str(revision.id),
        "manifest_hash": revision.manifest_hash,
        "idempotency_key": "alphabet-first-freeze",
    }


def test_company_research_boundary_schema_fits_the_persisted_column(session) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="boundary-schema-shape",
    )
    boundary = session.get(UnderwritingRevisionBoundary, revision.boundary_id)
    column_length = UnderwritingRevisionBoundary.__table__.c.schema_version.type.length

    assert boundary is not None
    assert column_length is not None
    assert len(boundary.schema_version) <= column_length


def test_revision_replay_does_not_depend_on_current_project_security_membership(
    session,
) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="frozen-membership",
    )
    table = UnderwritingResearchProjectSecurity.__table__
    connection = session.connection()
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"ALTER TABLE {table.name} DISABLE TRIGGER USER"))
    try:
        connection.execute(
            text(f"DELETE FROM {table.name} WHERE project_id = :project_id").bindparams(
                bindparam("project_id", type_=table.c.project_id.type)
            ),
            {"project_id": initialized.project.id},
        )
    finally:
        if connection.dialect.name == "postgresql":
            connection.execute(text(f"ALTER TABLE {table.name} ENABLE TRIGGER USER"))
    session.flush()
    session.expire_all()

    assert service.revision(initialized.project.id, revision.id) == revision
    assert service.export(initialized.project.id, revision.id) == service.export(
        initialized.project.id, revision.id
    )


def test_revision_replay_rejects_assessment_parent_substitution(session) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="assessment-parent",
    )
    manifest = session.get(UnderwritingRevisionManifest, revision.manifest_id)
    assert manifest is not None
    assessment_id = UUID(manifest.manifest["assessment_id"])
    successor = UnderwritingResearchAssessmentVersion(
        project_id=initialized.project.id,
        version=2,
        supersedes_id=assessment_id,
        answerability="not_answerable",
        direction=None,
        confidence=None,
        publication_status="user_frozen",
        blockers=[],
        resolution_requirements=[],
        next_review_at=None,
        content_hash="a" * 64,
        created_at=FROZEN_AT + timedelta(minutes=1),
    )
    session.add(successor)
    session.flush()
    _tamper_row(
        session,
        UnderwritingResearchAssessmentVersion,
        assessment_id,
        supersedes_id=successor.id,
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="assessment is invalid"):
        service.revision(initialized.project.id, revision.id)


def test_revision_replay_authenticates_idempotency_key_and_published_at(
    session,
) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="frozen-publication-identity",
    )

    _tamper_row(
        session,
        UnderwritingRevisionManifest,
        revision.manifest_id,
        idempotency_key="substituted-publication-key",
    )
    session.expire_all()
    with pytest.raises(CompanyResearchIntegrityError, match="publication identity"):
        service.revision(initialized.project.id, revision.id)

    _tamper_row(
        session,
        UnderwritingRevisionManifest,
        revision.manifest_id,
        idempotency_key="frozen-publication-identity",
    )
    _tamper_row(
        session,
        UnderwritingResearchVersion,
        revision.id,
        created_at=FROZEN_AT + timedelta(minutes=1),
    )
    session.expire_all()
    with pytest.raises(CompanyResearchIntegrityError, match="publication identity"):
        service.revision(initialized.project.id, revision.id)


def _frozen_row_snapshot(session, *, project_id, preparation_id):
    session.expire_all()
    repository = CompanyResearchRepository(session)
    preparation = repository.preparation_for_project(project_id, fresh=True)
    draft = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    assert preparation is not None and draft is not None
    return {
        "preparation": _row_projection(preparation),
        "draft": _row_projection(draft),
        "assessment_count": session.scalar(
            select(func.count()).select_from(UnderwritingResearchAssessmentVersion)
        ),
        "boundary_count": session.scalar(
            select(func.count()).select_from(UnderwritingRevisionBoundary)
        ),
        "manifest_count": session.scalar(
            select(func.count()).select_from(UnderwritingRevisionManifest)
        ),
        "revision_count": session.scalar(
            select(func.count()).select_from(UnderwritingResearchVersion)
        ),
        "events": tuple(
            _row_projection(row) for row in repository.events(preparation_id)
        ),
    }


def test_publish_rejects_stale_or_changed_manifest_and_reused_key(session) -> None:
    initialized, _repository, preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    session.commit()
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    before = _frozen_row_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
    )

    with pytest.raises(ValidationError, match="manifest hash changed"):
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version,
            expected_manifest_hash="f" * 64,
            idempotency_key="wrong-manifest",
        )
    session.commit()
    assert (
        _frozen_row_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
        )
        == before
    )

    with pytest.raises(ConflictError, match="stale"):
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version + 1,
            expected_manifest_hash=preview.manifest_hash,
            idempotency_key="stale-draft",
        )
    session.commit()
    assert (
        _frozen_row_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
        )
        == before
    )

    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="one-key",
    )
    session.commit()
    with pytest.raises(ConflictError, match="reused"):
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version,
            expected_manifest_hash="e" * 64,
            idempotency_key="one-key",
        )
    with pytest.raises(ConflictError, match="stale"):
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version,
            expected_manifest_hash=preview.manifest_hash,
            idempotency_key="different-key",
        )
    assert service.revision(initialized.project.id, revision.id) == revision


@pytest.mark.parametrize(
    ("owner", "method_name"),
    (
        (ProductRepository, "append_assessment"),
        (ProductRepository, "append_boundary"),
        (ProductRepository, "append_manifest"),
        (ProductRepository, "append_company_research_revision"),
        (ProductRepository, "reset_draft_after_publish"),
        (CompanyResearchRepository, "append_event"),
        (CompanyResearchPublicationService, "_complete_publication"),
    ),
    ids=(
        "assessment",
        "boundary",
        "manifest",
        "revision",
        "draft_cas",
        "publication_event",
        "preparation_completion",
    ),
)
def test_failure_after_each_publication_write_rolls_back_before_caller_commit(
    session, monkeypatch, owner, method_name
) -> None:
    initialized, _repository, preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    session.commit()
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    before = _frozen_row_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
    )
    original = getattr(owner, method_name)

    def fail_after_write(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError(f"injected after {method_name}")

    monkeypatch.setattr(owner, method_name, fail_after_write)

    with pytest.raises(RuntimeError, match=f"injected after {method_name}"):
        service.publish(
            project_id=initialized.project.id,
            expected_lock_version=preview.expected_lock_version,
            expected_manifest_hash=preview.manifest_hash,
            idempotency_key=f"failure-{method_name}",
        )
    session.commit()

    assert (
        _frozen_row_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
        )
        == before
    )


def test_export_is_deterministic_hashed_and_preserves_frozen_domain_order(
    session,
) -> None:
    initialized, repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="alphabet-export",
    )
    frozen_evidence = next(
        item for item in revision.artifacts if item.kind == "evidence_index"
    )
    evidence = repository.artifact(frozen_evidence.id)
    assert evidence is not None

    first = service.export(initialized.project.id, revision.id)
    second = service.export(initialized.project.id, revision.id)

    assert first == second
    assert first.filename == f"alphabet-company-research-{revision.id}.md"
    assert first.media_type == "text/markdown"
    assert sha256(first.content.encode("utf-8")).hexdigest() == first.content_hash
    assert "not_answerable" in first.content
    assert "No authenticated market price is bundled." in first.content
    assert "Item 7, Results of Operations" in first.content
    headings = (
        "## Revision",
        "## Company and Securities",
        "## Historical Basis",
        "## Strategy and Model",
        "## Assessment",
        "## Memo",
        "## Evidence",
        "## Research Gaps",
        "## Assumptions",
        "## Strongest Counterevidence",
        "## Next Verification Events",
    )
    assert tuple(first.content.index(heading) for heading in headings) == tuple(
        sorted(first.content.index(heading) for heading in headings)
    )
    fact_keys = tuple(item["fact_key"] for item in evidence.payload["facts"])
    assert tuple(first.content.index(key) for key in fact_keys) == tuple(
        sorted(first.content.index(key) for key in fact_keys)
    )


def test_export_escapes_all_non_memo_markdown_and_uses_a_safe_json_fence(
    session, monkeypatch
) -> None:
    initialized, repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="hostile-export",
    )
    payloads = {
        reference.id: deepcopy(repository.artifact(reference.id).payload)
        for reference in revision.artifacts
    }
    by_kind = {reference.kind: reference.id for reference in revision.artifacts}
    payloads[by_kind["evidence_index"]]["facts"][0]["source_locator"] = (
        "Item 7\n## Evidence Injection <script>alert(1)</script>"
    )
    payloads[by_kind["scenario_set"]]["hostile_fence"] = "``````"
    hostile_company = CompanyResearchCompany(
        object_id=revision.company.object_id,
        external_key=revision.company.external_key,
        canonical_name="Alphabet\n## Identity Injection <img src=x>",
    )
    original_security = revision.securities[0]
    hostile_security = CompanyResearchSecurity(
        object_id=original_security.object_id,
        company_id=original_security.company_id,
        external_key=original_security.external_key,
        canonical_name="Class A\n# Security Injection",
        symbol="GOOGL\n- injected list",
        exchange="NASDAQ <script>",
        share_class=original_security.share_class,
        trading_currency=original_security.trading_currency,
    )
    hostile_revision = replace(
        revision,
        company=hostile_company,
        securities=(hostile_security, *revision.securities[1:]),
        strategy_version="strategy\n## Strategy Injection",
        model_version="model <script>",
        memo_markdown="## Memo-owned Markdown\n\nThis stays *verbatim*.",
        strongest_counterevidence=(
            {
                "fact_key": "hostile_counterevidence",
                "source_locator": "locator\n## Counterevidence Injection",
                "source_url": "https://example.test/(unsafe)",
                "raw_hash": "b" * 64,
            },
        ),
        next_verification_events=("next\n## Event Injection <script>",),
    )

    monkeypatch.setattr(service, "revision", lambda *_args: hostile_revision)
    monkeypatch.setattr(
        service._repository,
        "artifact",
        lambda artifact_id, *, fresh: SimpleNamespace(payload=payloads[artifact_id]),
    )

    first = service.export(initialized.project.id, revision.id)
    second = service.export(initialized.project.id, revision.id)

    assert first == second
    assert "## Memo-owned Markdown\n\nThis stays *verbatim*." in first.content
    assert "<script>" not in first.content
    assert "<img src=x>" not in first.content
    for injected in (
        "## Identity Injection",
        "# Security Injection",
        "## Strategy Injection",
        "## Evidence Injection",
        "## Counterevidence Injection",
        "## Event Injection",
    ):
        assert f"\n{injected}" not in first.content
    assumptions = first.content.split("## Assumptions\n\n", 1)[1]
    opening_fence = assumptions.splitlines()[0]
    assert opening_fence.endswith("json")
    assert len(opening_fence.removesuffix("json")) > 6


def test_revision_replay_uses_frozen_artifact_ids_not_later_current_heads(
    session,
) -> None:
    initialized, repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="frozen-head-replay",
    )
    frozen_memo_ref = next(item for item in revision.artifacts if item.kind == "memo")
    frozen_memo = repository.artifact(frozen_memo_ref.id)
    assert frozen_memo is not None
    successor = repository.append_artifact(
        project_id=initialized.project.id,
        kind="memo",
        input_hash=frozen_memo.input_hash,
        payload=frozen_memo.payload,
        source_refs=frozen_memo.source_refs,
        expected_parent_id=frozen_memo.id,
        created_at=FROZEN_AT + timedelta(minutes=1),
    )

    assert (
        repository.current_artifact(initialized.project.id, "memo").id == successor.id
    )
    assert service.revision(initialized.project.id, revision.id) == revision


def test_revision_replay_fails_closed_for_a_tampered_frozen_artifact(session) -> None:
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="tampered-frozen-replay",
    )
    frozen = next(item for item in revision.artifacts if item.kind == "business_map")
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        frozen.id,
        content_hash="0" * 64,
    )
    session.expire_all()

    with pytest.raises(CompanyResearchIntegrityError, match="content hash mismatch"):
        service.revision(initialized.project.id, revision.id)


def test_sqlite_concurrent_different_publication_keys_create_at_most_one_revision(
    tmp_path,
) -> None:
    database_path = tmp_path / "company-research-publication.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    setup = sessions()
    try:
        initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
            _ready_to_freeze_workspace(setup)
        )
        project_id = initialized.project.id
        preview = CompanyResearchPublicationService(
            setup, now=lambda: FROZEN_AT
        ).preview(
            project_id=project_id,
            expected_lock_version=confirmation.draft_lock_version,
        )
        setup.commit()
    finally:
        setup.close()
    barrier = Barrier(2)

    def publish_once(key: str):
        concurrent = sessions()
        try:
            try:
                with concurrent.begin_nested():
                    assert (
                        concurrent.scalar(
                            select(UnderwritingWorkspaceDraft.id).where(
                                UnderwritingWorkspaceDraft.project_id == project_id
                            )
                        )
                        is not None
                    )
                    barrier.wait()
                    result = CompanyResearchPublicationService(
                        concurrent, now=lambda: FROZEN_AT
                    ).publish(
                        project_id=project_id,
                        expected_lock_version=preview.expected_lock_version,
                        expected_manifest_hash=preview.manifest_hash,
                        idempotency_key=key,
                    )
                concurrent.commit()
                return result, None
            except Exception as exc:  # noqa: BLE001 - assert public taxonomy below
                concurrent.commit()
                return None, exc
        finally:
            concurrent.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(publish_once, ("different-key-a", "different-key-b"))
            )
        results = tuple(result for result, error in outcomes if error is None)
        errors = tuple(error for result, error in outcomes if result is None)
        assert len(results) == 1
        assert len(errors) == 1
        assert type(errors[0]) is ConflictError
        verify = sessions()
        try:
            assert (
                verify.scalar(
                    select(func.count()).select_from(UnderwritingResearchVersion)
                )
                == 1
            )
            assert (
                verify.scalar(
                    select(func.count()).select_from(UnderwritingRevisionManifest)
                )
                == 1
            )
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_postgres_company_research_publication_if_configured(session, engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("TEST_DATABASE_URL is not configured")
    initialized, _repository, _preparation, _job, _draft, _memo, confirmation = (
        _ready_to_freeze_workspace(session)
    )
    project_id = initialized.project.id
    preview = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT).preview(
        project_id=project_id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    session.commit()
    sessions = sessionmaker(bind=engine, future=True)
    barrier = Barrier(2)

    def publish_once(key: str):
        concurrent = sessions()
        try:
            barrier.wait()
            result = CompanyResearchPublicationService(
                concurrent, now=lambda: FROZEN_AT
            ).publish(
                project_id=project_id,
                expected_lock_version=preview.expected_lock_version,
                expected_manifest_hash=preview.manifest_hash,
                idempotency_key=key,
            )
            concurrent.commit()
            return result, None
        except Exception as exc:  # noqa: BLE001 - assert public taxonomy below
            concurrent.rollback()
            return None, exc
        finally:
            concurrent.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                publish_once,
                ("company-research-pg-a", "company-research-pg-b"),
            )
        )
    results = tuple(result for result, error in outcomes if error is None)
    errors = tuple(error for result, error in outcomes if result is None)
    assert len(results) == 1
    assert len(errors) == 1
    assert type(errors[0]) is ConflictError
    verifier = sessions()
    try:
        assert (
            verifier.scalar(
                select(func.count()).select_from(UnderwritingResearchVersion)
            )
            == 1
        )
    finally:
        verifier.close()


def _rewrite_one_event_type(
    session,
    preparation_id,
    *,
    target_event_type: str,
    replacement_event_type: str,
) -> None:
    rows = tuple(
        session.scalars(
            select(CompanyResearchEvent)
            .where(CompanyResearchEvent.preparation_id == preparation_id)
            .order_by(CompanyResearchEvent.sequence)
        )
    )
    target = next(row for row in rows if row.event_type == target_event_type)
    previous_hash = None
    table = CompanyResearchEvent.__table__
    statement = text(
        "UPDATE uw_company_research_events SET previous_event_hash = :previous_hash, "
        "event_type = :event_type, content_hash = :content_hash WHERE id = :event_id"
    ).bindparams(
        bindparam("previous_hash", type_=table.c.previous_event_hash.type),
        bindparam("event_type", type_=table.c.event_type.type),
        bindparam("content_hash", type_=table.c.content_hash.type),
        bindparam("event_id", type_=table.c.id.type),
    )
    connection = session.connection()
    disable_trigger = text(
        "ALTER TABLE uw_company_research_events DISABLE TRIGGER USER"
    )
    enable_trigger = text("ALTER TABLE uw_company_research_events ENABLE TRIGGER USER")
    if connection.dialect.name == "postgresql":
        connection.execute(disable_trigger)
    try:
        for row in rows:
            event_type = (
                replacement_event_type if row.id == target.id else row.event_type
            )
            content_hash = CompanyResearchRepository.event_content_hash_v2(
                preparation_id=row.preparation_id,
                sequence=row.sequence,
                previous_event_hash=previous_hash,
                event_type=event_type,
                payload=row.payload,
                created_at=row.created_at,
            )
            assert (
                connection.execute(
                    statement,
                    {
                        "previous_hash": previous_hash,
                        "event_type": event_type,
                        "content_hash": content_hash,
                        "event_id": row.id,
                    },
                ).rowcount
                == 1
            )
            previous_hash = content_hash
    finally:
        if connection.dialect.name == "postgresql":
            connection.execute(enable_trigger)
    session.expire_all()


def _rewrite_event_chain_without_one_review(session, preparation_id) -> None:
    _rewrite_one_event_type(
        session,
        preparation_id,
        target_event_type="evidence_reviewed",
        replacement_event_type="review_event_removed",
    )


def _make_event_time_nonmonotonic(session, preparation_id) -> None:
    rows = tuple(
        session.scalars(
            select(CompanyResearchEvent)
            .where(CompanyResearchEvent.preparation_id == preparation_id)
            .order_by(CompanyResearchEvent.sequence)
        )
    )
    target = next(row for row in rows if row.event_type == "model_stage_claimed")
    target_index = rows.index(target)
    assert target_index > 0
    changed_time = rows[target_index - 1].created_at - timedelta(seconds=1)
    previous_hash = None
    table = CompanyResearchEvent.__table__
    statement = text(
        "UPDATE uw_company_research_events SET previous_event_hash = :previous_hash, "
        "created_at = :created_at, content_hash = :content_hash WHERE id = :event_id"
    ).bindparams(
        bindparam("previous_hash", type_=table.c.previous_event_hash.type),
        bindparam("created_at", type_=table.c.created_at.type),
        bindparam("content_hash", type_=table.c.content_hash.type),
        bindparam("event_id", type_=table.c.id.type),
    )
    connection = session.connection()
    disable_trigger = text(
        "ALTER TABLE uw_company_research_events DISABLE TRIGGER USER"
    )
    enable_trigger = text("ALTER TABLE uw_company_research_events ENABLE TRIGGER USER")
    if connection.dialect.name == "postgresql":
        connection.execute(disable_trigger)
    try:
        for row in rows:
            created_at = changed_time if row.id == target.id else row.created_at
            content_hash = CompanyResearchRepository.event_content_hash_v2(
                preparation_id=row.preparation_id,
                sequence=row.sequence,
                previous_event_hash=previous_hash,
                event_type=row.event_type,
                payload=row.payload,
                created_at=created_at,
            )
            assert (
                connection.execute(
                    statement,
                    {
                        "previous_hash": previous_hash,
                        "created_at": created_at,
                        "content_hash": content_hash,
                        "event_id": row.id,
                    },
                ).rowcount
                == 1
            )
            previous_hash = content_hash
    finally:
        if connection.dialect.name == "postgresql":
            connection.execute(enable_trigger)
    session.expire_all()


def test_confirm_judgment_atomically_advances_the_publication_boundary(session) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    project_id = initialized.project.id
    prior_draft_lock_version = draft.lock_version
    prior_draft_content = deepcopy(draft.content)
    prior_base_revision_id = draft.base_revision_id
    prior_job = _job_projection(job)
    prior_attempt_count = session.scalar(
        select(func.count()).select_from(Job).where(Job.id == job.id)
    )
    prior_heads = {
        kind: (row.id, row.content_hash, row.version)
        for kind in (
            "evidence_index",
            "research_gaps",
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
        )
        if (row := repository.current_artifact(project_id, kind)) is not None
    }
    prior_events = repository.events(preparation.id)
    prior_memo_payload = deepcopy(machine_memo.payload)
    prior_memo_source_refs = deepcopy(machine_memo.source_refs)

    result = CompanyResearchPublicationService(
        session, now=lambda: CONFIRMED_AT
    ).confirm_judgment(
        project_id=project_id,
        expected_lock_version=draft.lock_version,
        expected_memo_id=machine_memo.id,
        expected_memo_content_hash=machine_memo.content_hash,
        markdown="  Investment conclusion.\r\n\rSecond paragraph.\r  ",
    )

    assert type(result) is CompanyResearchJudgmentConfirmation
    assert result.project_id == project_id
    assert result.preparation_id == preparation.id
    assert result.draft_id == draft.id
    assert result.draft_lock_version == prior_draft_lock_version + 1
    assert result.machine_memo_id == machine_memo.id
    assert result.machine_memo_content_hash == machine_memo.content_hash
    assert result.confirmed_memo_id != machine_memo.id
    assert result.assessment_status == prior_memo_payload["assessment_status"]
    assert result.reviewer == REVIEWER
    assert result.markdown == "Investment conclusion.\n\nSecond paragraph."
    assert result.confirmed_at == CONFIRMED_AT

    session.expire_all()
    confirmed = repository.current_artifact(project_id, "memo")
    assert confirmed is not None
    assert confirmed.id == result.confirmed_memo_id
    assert confirmed.content_hash == result.confirmed_memo_content_hash
    assert confirmed.version == machine_memo.version + 1
    assert confirmed.supersedes_id == machine_memo.id
    assert confirmed.parent_content_hash == machine_memo.content_hash
    assert confirmed.source_refs == prior_memo_source_refs
    assert confirmed.payload["_lineage"] == prior_memo_payload["_lineage"]
    decoded = CompanyResearchArtifactCodec.decode(
        "memo",
        {key: value for key, value in confirmed.payload.items() if key != "_lineage"},
    )
    assert decoded.candidate_status == "human_confirmed"
    assert decoded.reviewer == REVIEWER
    assert decoded.markdown == "Investment conclusion.\n\nSecond paragraph."
    expected_payload = {
        **prior_memo_payload,
        "candidate_status": "human_confirmed",
        "reviewer": REVIEWER,
        "markdown": "Investment conclusion.\n\nSecond paragraph.",
    }
    assert confirmed.payload == expected_payload
    assert (
        CompanyResearchRepository._persisted_utc(confirmed.created_at) == CONFIRMED_AT
    )

    current_preparation = repository.preparation_for_project(project_id, fresh=True)
    current_draft = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    current_job = session.scalar(
        select(Job).where(Job.id == job.id).execution_options(populate_existing=True)
    )
    assert current_preparation is not None
    assert (
        current_preparation.status,
        current_preparation.current_step,
        current_preparation.progress,
        current_preparation.last_error_code,
    ) == ("ready_to_freeze", "memo", 95, None)
    assert current_draft is not None
    assert current_draft.lock_version == prior_draft_lock_version + 1
    assert current_draft.content == prior_draft_content
    assert current_draft.base_revision_id == prior_base_revision_id
    assert current_job is not None and _job_projection(current_job) == prior_job
    assert (
        session.scalar(select(func.count()).select_from(Job).where(Job.id == job.id))
        == prior_attempt_count
    )

    assert {
        kind: (row.id, row.content_hash, row.version)
        for kind in prior_heads
        if (row := repository.current_artifact(project_id, kind)) is not None
    } == prior_heads
    evidence = repository.current_artifact(project_id, "evidence_index")
    assert evidence is not None
    assert [fact["review_decision"] for fact in evidence.payload["facts"]] == [
        "confirmed",
        "confirmed",
        "confirmed",
        "confirmed",
        "rejected",
        "confirmed",
        "confirmed",
    ]
    current_events = repository.events(preparation.id)
    assert current_events[:-1] == prior_events
    event = current_events[-1]
    assert event.hash_version == 2
    assert event.event_type == "judgment_confirmed"
    assert event.payload == {
        "machine_memo_id": str(machine_memo.id),
        "machine_memo_content_hash": machine_memo.content_hash,
        "confirmed_memo_id": str(confirmed.id),
        "confirmed_memo_content_hash": confirmed.content_hash,
        "assessment_status": decoded.assessment_status,
        "reviewer": REVIEWER,
    }
    assert CompanyResearchRepository._persisted_utc(event.created_at) == CONFIRMED_AT
    assert (
        CompanyResearchRepository._persisted_utc(confirmed.created_at) == CONFIRMED_AT
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(CompanyResearchArtifactVersion)
            .where(
                CompanyResearchArtifactVersion.project_id == project_id,
                CompanyResearchArtifactVersion.kind == "memo",
            )
        )
        == 2
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(CompanyResearchEvent)
            .where(
                CompanyResearchEvent.preparation_id == preparation.id,
                CompanyResearchEvent.event_type == "judgment_confirmed",
            )
        )
        == 1
    )


@pytest.mark.parametrize("markdown", ("", " \r\n\t ", "x" * 100001))
def test_confirmation_rejects_invalid_normalized_markdown_before_reading_state(
    session, markdown
) -> None:
    before = session.scalar(select(func.count()).select_from(CompanyResearchEvent))

    with pytest.raises(ValidationError, match="markdown"):
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=uuid4(),
            expected_lock_version=1,
            expected_memo_id=uuid4(),
            expected_memo_content_hash="a" * 64,
            markdown=markdown,
        )

    session.commit()
    assert (
        session.scalar(select(func.count()).select_from(CompanyResearchEvent)) == before
    )


def test_confirmation_does_not_reclassify_a_non_lock_database_error(
    session, monkeypatch
) -> None:
    injected = OperationalError(
        "UPDATE uw_workspace_drafts",
        {},
        RuntimeError("disk I/O error"),
    )

    def fail_reservation(*_args, **_kwargs):
        raise injected

    monkeypatch.setattr(
        CompanyResearchRepository,
        "_reserve_sqlite_writer_before_ownership_read",
        fail_reservation,
    )

    with pytest.raises(OperationalError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=uuid4(),
            expected_lock_version=1,
            expected_memo_id=uuid4(),
            expected_memo_content_hash="a" * 64,
            markdown="Reviewed conclusion.",
        )
    assert captured.value is injected


def test_confirmation_exact_replay_returns_the_existing_result_once(session) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)

    first = service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()
    after_first = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )
    second = service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()

    assert second == first
    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == after_first
    )
    assert len(repository.artifact_chain(first.confirmed_memo_id)) == 2


def test_confirmation_freshly_authenticates_cached_market_rows(session) -> None:
    initialized, repository, preparation, _job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    judgment = repository.current_artifact(initialized.project.id, "judgment_context")
    assert judgment is not None
    binding = next(
        value
        for value in judgment.payload["_lineage"]["market_snapshot_bindings"]
        if value["snapshot_kind"] == "price"
    )
    price = session.get(UnderwritingPriceSnapshot, UUID(binding["snapshot_id"]))
    capture = session.get(
        UnderwritingMarketCaptureEnvelope,
        UUID(binding["capture_envelope_id"]),
    )
    assert price is not None and capture is not None
    original_price = price.price
    original_lock_version = draft.lock_version
    _tamper_row(
        session,
        UnderwritingPriceSnapshot,
        price.id,
        expire=False,
        price=original_price + Decimal("1"),
    )
    assert price.price == original_price

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=initialized.project.id,
            **_confirmation_request(draft, machine_memo),
        )
    assert type(captured.value) is CompanyResearchIntegrityError
    session.commit()

    session.expire_all()
    assert repository.current_artifact(initialized.project.id, "memo") == machine_memo
    current_preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    current_draft = session.scalar(
        select(UnderwritingWorkspaceDraft)
        .where(UnderwritingWorkspaceDraft.project_id == initialized.project.id)
        .execution_options(populate_existing=True)
    )
    assert current_preparation is not None
    assert current_preparation.status == "awaiting_judgment_review"
    assert current_draft is not None
    assert current_draft.lock_version == original_lock_version
    assert not any(
        event.event_type == "judgment_confirmed"
        for event in repository.events(preparation.id)
    )


def test_confirmation_preserves_a_true_legacy_model_gap_successor(session) -> None:
    initialized, _workbench, repository = _model_workspace(session)
    preparation = repository.preparation_for_project(initialized.project.id)
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None and job.started_at is not None
    repository.append_event(
        preparation_id=preparation.id,
        event_type="model_stage_claimed",
        payload={"stage": "model_bundle", "attempt": job.attempt},
        created_at=CompanyResearchRepository._persisted_utc(job.started_at),
    )
    source_gaps, legacy_gaps = _rewrite_as_legacy_model_gap_successor(
        session, initialized, repository
    )
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(initialized.project.id)
    machine_memo = repository.current_artifact(initialized.project.id, "memo")
    assert draft is not None and machine_memo is not None
    assert "research_gaps" not in machine_memo.payload

    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    request = _confirmation_request(draft, machine_memo)
    result = service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()
    replay = service.confirm_judgment(project_id=initialized.project.id, **request)

    confirmed = repository.current_artifact(initialized.project.id, "memo")
    current_gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert confirmed is not None and confirmed.id == result.confirmed_memo_id
    assert "research_gaps" not in confirmed.payload
    assert current_gaps is not None
    assert (current_gaps.id, current_gaps.supersedes_id) == (
        legacy_gaps.id,
        source_gaps.id,
    )
    decoded = CompanyResearchArtifactCodec.decode(
        "memo",
        {key: value for key, value in confirmed.payload.items() if key != "_lineage"},
    )
    assert decoded.candidate_status == "human_confirmed"
    assert decoded.reviewer == REVIEWER
    assert replay == result


def test_legacy_model_gap_successor_can_publish_replay_and_export(session) -> None:
    initialized, _workbench, repository = _model_workspace(session)
    preparation = repository.preparation_for_project(initialized.project.id)
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None and job.started_at is not None
    repository.append_event(
        preparation_id=preparation.id,
        event_type="model_stage_claimed",
        payload={"stage": "model_bundle", "attempt": job.attempt},
        created_at=CompanyResearchRepository._persisted_utc(job.started_at),
    )
    _source_gaps, legacy_gaps = _rewrite_as_legacy_model_gap_successor(
        session, initialized, repository
    )
    draft = WorkspaceDraftService(session, now=lambda: NOW).read(initialized.project.id)
    machine_memo = repository.current_artifact(initialized.project.id, "memo")
    assert draft is not None and machine_memo is not None
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    confirmation = service.confirm_judgment(
        project_id=initialized.project.id,
        **_confirmation_request(draft, machine_memo),
    )
    frozen_service = CompanyResearchPublicationService(session, now=lambda: FROZEN_AT)
    preview = frozen_service.preview(
        project_id=initialized.project.id,
        expected_lock_version=confirmation.draft_lock_version,
    )
    revision = frozen_service.publish(
        project_id=initialized.project.id,
        expected_lock_version=preview.expected_lock_version,
        expected_manifest_hash=preview.manifest_hash,
        idempotency_key="legacy-model-gap-publication",
    )

    assert any(
        reference.kind == "research_gaps" and reference.id == legacy_gaps.id
        for reference in revision.artifacts
    )
    assert frozen_service.revision(initialized.project.id, revision.id) == revision
    exported = frozen_service.export(initialized.project.id, revision.id)
    assert exported.media_type == "text/markdown"
    assert exported.content_hash == sha256(exported.content.encode("utf-8")).hexdigest()


def test_confirmation_rejects_a_machine_memo_outside_the_exact_model_bundle_time(
    session,
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        machine_memo.id,
        created_at=machine_memo.created_at + timedelta(seconds=1),
    )
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=initialized.project.id,
            **_confirmation_request(draft, machine_memo),
        )
    assert type(captured.value) is CompanyResearchIntegrityError
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def test_confirmation_replay_reauthenticates_the_model_claim_audit_prefix(
    session,
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()
    _rewrite_one_event_type(
        session,
        preparation.id,
        target_event_type="model_stage_claimed",
        replacement_event_type="model_claim_removed",
    )
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        service.confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is CompanyResearchIntegrityError
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def test_confirmation_replay_reports_a_missing_terminal_event_as_integrity_error(
    session,
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()
    _rewrite_one_event_type(
        session,
        preparation.id,
        target_event_type="judgment_confirmed",
        replacement_event_type="confirmation_event_removed",
    )
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        service.confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is CompanyResearchIntegrityError
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def test_confirmation_replay_rejects_an_extra_memo_successor_as_integrity_error(
    session,
) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    result = service.confirm_judgment(project_id=initialized.project.id, **request)
    confirmed = repository.current_artifact(initialized.project.id, "memo")
    assert confirmed is not None and confirmed.id == result.confirmed_memo_id
    repository.append_artifact(
        project_id=initialized.project.id,
        kind="memo",
        input_hash=confirmed.input_hash,
        payload=confirmed.payload,
        source_refs=confirmed.source_refs,
        expected_parent_id=confirmed.id,
        created_at=CONFIRMED_AT + timedelta(seconds=1),
    )
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        service.confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is CompanyResearchIntegrityError
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


@pytest.mark.parametrize(
    "request_change",
    (
        {"markdown": "A different conclusion."},
        {"expected_memo_id": UUID(int=999)},
        {"expected_memo_content_hash": "f" * 64},
        {"expected_lock_version": 999},
    ),
)
def test_confirmation_replay_with_different_content_or_expectation_conflicts_without_writes(
    session, request_change
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    service = CompanyResearchPublicationService(session, now=lambda: CONFIRMED_AT)
    request = _confirmation_request(draft, machine_memo)
    service.confirm_judgment(project_id=initialized.project.id, **request)
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(ConflictError):
        service.confirm_judgment(
            project_id=initialized.project.id,
            **{**request, **request_change},
        )
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


class _InjectedConfirmationFailure(RuntimeError):
    pass


@pytest.mark.parametrize(
    "stage_method",
    (
        "append_judgment_confirmation_memo",
        "compare_and_swap_publication_draft",
        "advance_judgment_confirmation",
        "append_judgment_confirmation_event",
    ),
)
def test_confirmation_failure_at_each_mutation_stage_rolls_back_the_outer_savepoint(
    session, monkeypatch, stage_method
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    def fail(*_args, **_kwargs):
        raise _InjectedConfirmationFailure(stage_method)

    monkeypatch.setattr(CompanyResearchRepository, stage_method, fail)
    with pytest.raises(_InjectedConfirmationFailure, match=stage_method):
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=initialized.project.id,
            **_confirmation_request(draft, machine_memo),
        )
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def test_confirmation_failure_after_event_insert_rolls_back_every_mutation(
    session, monkeypatch
) -> None:
    initialized, _repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )
    original = CompanyResearchRepository.append_judgment_confirmation_event

    def append_then_fail(repository, *args, **kwargs):
        original(repository, *args, **kwargs)
        raise _InjectedConfirmationFailure("after_event_insert")

    monkeypatch.setattr(
        CompanyResearchRepository,
        "append_judgment_confirmation_event",
        append_then_fail,
    )
    with pytest.raises(_InjectedConfirmationFailure, match="after_event_insert"):
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(
            project_id=initialized.project.id,
            **_confirmation_request(draft, machine_memo),
        )
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def _tamper_stale_draft(
    session, initialized, _repository, _preparation, _job, draft, _memo, request
) -> None:
    WorkspaceDraftService(session, now=lambda: NOW + timedelta(minutes=1)).save(
        initialized.project.id,
        expected_lock_version=draft.lock_version,
        patch={},
    )


def _tamper_wrong_memo_id(
    _session, _initialized, _repository, _preparation, _job, _draft, _memo, request
) -> None:
    request["expected_memo_id"] = uuid4()


def _tamper_wrong_memo_hash(
    _session, _initialized, _repository, _preparation, _job, _draft, _memo, request
) -> None:
    request["expected_memo_content_hash"] = "f" * 64


def _tamper_preparation_chronology(
    session, _initialized, _repository, preparation, _job, _draft, _memo, _request
) -> None:
    _tamper_row(
        session,
        CompanyResearchPreparation,
        preparation.id,
        updated_at=preparation.created_at - timedelta(seconds=1),
    )


def _tamper_draft_chronology(
    session, _initialized, _repository, _preparation, _job, draft, _memo, _request
) -> None:
    _tamper_row(
        session,
        UnderwritingWorkspaceDraft,
        draft.id,
        updated_at=draft.created_at - timedelta(seconds=1),
    )


def _tamper_substitute_reviewed_evidence(
    _session, initialized, repository, _preparation, _job, _draft, _memo, _request
) -> None:
    evidence = repository.current_artifact(initialized.project.id, "evidence_index")
    assert evidence is not None
    repository.append_artifact(
        project_id=initialized.project.id,
        kind="evidence_index",
        input_hash=canonical_hash({"substitute": evidence.content_hash}),
        payload=evidence.payload,
        source_refs=evidence.source_refs,
        expected_parent_id=evidence.id,
        created_at=NOW,
    )


def _tamper_missing_evidence_review_event(
    session,
    _initialized,
    _repository,
    preparation,
    _job,
    _draft,
    _memo,
    _request,
) -> None:
    _rewrite_event_chain_without_one_review(session, preparation.id)


def _tamper_governed_gap_reason(
    session, initialized, repository, _preparation, _job, _draft, _memo, _request
) -> None:
    gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert gaps is not None
    payload = deepcopy(gaps.payload)
    payload["gaps"][0]["reason"] = "Recomputed but not governed."
    _durably_rewrite_payload(session, gaps, payload)


def _tamper_historical_basis(
    session, initialized, _repository, _preparation, _job, _draft, _memo, _request
) -> None:
    replacement = _substitute_basis(session, initialized.project)
    _rebind_historical_basis_without_updating_draft_lock(
        session, initialized.project, replacement.id
    )


def _tamper_foreign_model_parent(
    session, initialized, repository, _preparation, _job, _draft, _memo, _request
) -> None:
    driver = repository.current_artifact(initialized.project.id, "driver_map")
    assert driver is not None
    payload = deepcopy(driver.payload)
    payload["_lineage"]["artifact_refs"][0]["artifact_id"] = str(uuid4())
    _durably_rewrite_payload(session, driver, payload)


def _tamper_malformed_memo(
    session, _initialized, _repository, _preparation, _job, _draft, memo, _request
) -> None:
    payload = deepcopy(memo.payload)
    payload["candidate_status"] = "unknown"
    _durably_rewrite_payload(session, memo, payload)


def _tamper_multiple_memo_heads(
    session, initialized, _repository, _preparation, _job, _draft, memo, _request
) -> None:
    payload = deepcopy(memo.payload)
    source_refs = deepcopy(memo.source_refs)
    second_root = CompanyResearchArtifactVersion(
        project_id=initialized.project.id,
        kind="memo",
        version=2,
        supersedes_id=None,
        parent_content_hash=None,
        input_hash=memo.input_hash,
        payload=payload,
        source_refs=source_refs,
        content_hash=CompanyResearchRepository.artifact_content_hash(
            project_id=initialized.project.id,
            kind="memo",
            version=2,
            supersedes_id=None,
            parent_content_hash=None,
            input_hash=memo.input_hash,
            payload=payload,
            source_refs=source_refs,
        ),
        created_at=memo.created_at,
    )
    session.add(second_root)
    session.flush([second_root])


def _tamper_nonmonotonic_artifact_time(
    session, initialized, repository, _preparation, _job, _draft, memo, _request
) -> None:
    judgment = repository.current_artifact(initialized.project.id, "judgment_context")
    assert judgment is not None
    _tamper_row(
        session,
        CompanyResearchArtifactVersion,
        memo.id,
        created_at=judgment.created_at - timedelta(seconds=1),
    )


def _tamper_nonmonotonic_event_time(
    session,
    _initialized,
    _repository,
    preparation,
    _job,
    _draft,
    _memo,
    _request,
) -> None:
    _make_event_time_nonmonotonic(session, preparation.id)


def test_confirmation_reports_foreign_model_lineage_as_exact_integrity_error(
    session,
) -> None:
    initialized, repository, _preparation, _job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    _tamper_foreign_model_parent(
        session,
        initialized,
        repository,
        _preparation,
        _job,
        draft,
        machine_memo,
        request,
    )
    session.commit()

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is CompanyResearchIntegrityError


def test_confirmation_reports_a_stale_user_lock_as_exact_conflict(session) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    _tamper_stale_draft(
        session,
        initialized,
        repository,
        preparation,
        job,
        draft,
        machine_memo,
        request,
    )
    session.commit()

    with pytest.raises(ConflictError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is ConflictError


def test_confirmation_reports_a_malformed_durable_memo_as_exact_integrity_error(
    session,
) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    _tamper_malformed_memo(
        session,
        initialized,
        repository,
        preparation,
        job,
        draft,
        machine_memo,
        request,
    )
    session.commit()

    with pytest.raises(CompanyResearchIntegrityError) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is CompanyResearchIntegrityError


@pytest.mark.parametrize(
    ("tamper", "expected_error"),
    (
        (_tamper_stale_draft, ConflictError),
        (_tamper_wrong_memo_id, ConflictError),
        (_tamper_wrong_memo_hash, ConflictError),
        (_tamper_preparation_chronology, CompanyResearchIntegrityError),
        (_tamper_draft_chronology, CompanyResearchIntegrityError),
        (_tamper_substitute_reviewed_evidence, CompanyResearchIntegrityError),
        (_tamper_missing_evidence_review_event, CompanyResearchIntegrityError),
        (_tamper_governed_gap_reason, CompanyResearchIntegrityError),
        (_tamper_historical_basis, CompanyResearchIntegrityError),
        (_tamper_foreign_model_parent, CompanyResearchIntegrityError),
        (_tamper_malformed_memo, CompanyResearchIntegrityError),
        (_tamper_multiple_memo_heads, CompanyResearchIntegrityError),
        (_tamper_nonmonotonic_artifact_time, CompanyResearchIntegrityError),
        (_tamper_nonmonotonic_event_time, CompanyResearchIntegrityError),
    ),
    ids=(
        "stale_draft",
        "wrong_memo_id",
        "wrong_memo_hash",
        "preparation_chronology",
        "draft_chronology",
        "substitute_reviewed_evidence",
        "missing_evidence_review_event",
        "governed_gap_reason",
        "historical_basis",
        "foreign_model_parent",
        "malformed_memo",
        "multiple_memo_heads",
        "nonmonotonic_artifact_time",
        "nonmonotonic_event_time",
    ),
)
def test_confirmation_tamper_and_stale_matrix_fails_closed_with_zero_writes(
    session, tamper, expected_error
) -> None:
    initialized, repository, preparation, job, draft, machine_memo = (
        _awaiting_judgment_confirmation(session)
    )
    request = _confirmation_request(draft, machine_memo)
    tamper(
        session,
        initialized,
        repository,
        preparation,
        job,
        draft,
        machine_memo,
        request,
    )
    session.commit()
    before = _durable_publication_snapshot(
        session,
        project_id=initialized.project.id,
        preparation_id=preparation.id,
        job_id=job.id,
    )

    with pytest.raises(expected_error) as captured:
        CompanyResearchPublicationService(
            session, now=lambda: CONFIRMED_AT
        ).confirm_judgment(project_id=initialized.project.id, **request)
    assert type(captured.value) is expected_error
    session.commit()

    assert (
        _durable_publication_snapshot(
            session,
            project_id=initialized.project.id,
            preparation_id=preparation.id,
            job_id=job.id,
        )
        == before
    )


def test_sqlite_two_session_confirmation_has_one_winner_and_exact_replay(
    tmp_path,
) -> None:
    database_path = tmp_path / "company-research-confirmation.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    setup = sessions()
    try:
        initialized, _repository, preparation, job, draft, machine_memo = (
            _awaiting_judgment_confirmation(setup)
        )
        project_id = initialized.project.id
        preparation_id = preparation.id
        job_id = job.id
        request = _confirmation_request(draft, machine_memo)
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def confirm_once() -> CompanyResearchJudgmentConfirmation:
        concurrent = sessions()
        try:
            barrier.wait()
            result = CompanyResearchPublicationService(
                concurrent, now=lambda: CONFIRMED_AT
            ).confirm_judgment(project_id=project_id, **request)
            concurrent.commit()
            return result
        finally:
            concurrent.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: confirm_once(), range(2)))
        assert results[0] == results[1]
        verify = sessions()
        try:
            snapshot = _durable_publication_snapshot(
                verify,
                project_id=project_id,
                preparation_id=preparation_id,
                job_id=job_id,
            )
            artifacts = snapshot["artifacts"]
            events = snapshot["events"]
            assert (
                sum(
                    dict(row)["kind"] == "memo"
                    for row in artifacts  # type: ignore[arg-type]
                )
                == 2
            )
            assert (
                sum(
                    dict(row)["event_type"] == "judgment_confirmed"  # type: ignore[arg-type]
                    for row in events
                )
                == 1
            )
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_sqlite_top_level_savepoints_reserve_the_writer_before_authentication(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "company-research-savepoint-confirmation.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    setup = sessions()
    try:
        initialized, _repository, preparation, job, draft, machine_memo = (
            _awaiting_judgment_confirmation(setup)
        )
        project_id = initialized.project.id
        preparation_id = preparation.id
        job_id = job.id
        expected_lock_version = draft.lock_version
        request = _confirmation_request(draft, machine_memo)
        setup.commit()
    finally:
        setup.close()

    callers_ready = Barrier(2)
    append_barrier = Barrier(2)
    original_append = CompanyResearchRepository.append_judgment_confirmation_memo

    def synchronized_append(self, *args, **kwargs):
        try:
            append_barrier.wait(timeout=2)
        except BrokenBarrierError:
            pass
        return original_append(self, *args, **kwargs)

    monkeypatch.setattr(
        CompanyResearchRepository,
        "append_judgment_confirmation_memo",
        synchronized_append,
    )

    def confirm_once(markdown: str):
        concurrent = sessions()
        try:
            caught = None
            result = None
            with concurrent.begin_nested():
                assert (
                    concurrent.scalar(
                        select(UnderwritingWorkspaceDraft.id).where(
                            UnderwritingWorkspaceDraft.project_id == project_id
                        )
                    )
                    is not None
                )
                callers_ready.wait()
                try:
                    result = CompanyResearchPublicationService(
                        concurrent, now=lambda: CONFIRMED_AT
                    ).confirm_judgment(
                        project_id=project_id,
                        **{**request, "markdown": markdown},
                    )
                except Exception as exc:  # noqa: BLE001 - assert the public taxonomy
                    caught = exc
            concurrent.commit()
            return result, caught
        finally:
            concurrent.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(confirm_once, ("First review.", "Second review."))
            )
        results = tuple(result for result, _caught in outcomes if result is not None)
        errors = tuple(caught for _result, caught in outcomes if caught is not None)
        assert len(results) == 1
        assert len(errors) == 1
        assert type(errors[0]) is ConflictError

        verify = sessions()
        try:
            snapshot = _durable_publication_snapshot(
                verify,
                project_id=project_id,
                preparation_id=preparation_id,
                job_id=job_id,
            )
            assert dict(snapshot["draft"])["lock_version"] == (
                expected_lock_version + 1
            )
            assert (
                sum(dict(row)["kind"] == "memo" for row in snapshot["artifacts"]) == 2
            )
            assert (
                sum(
                    dict(row)["event_type"] == "judgment_confirmed"
                    for row in snapshot["events"]
                )
                == 1
            )
        finally:
            verify.close()
    finally:
        engine.dispose()
