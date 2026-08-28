"""Lease-fenced execution of review-gated company-research preparation."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.models.operational import Job
from app.repositories.operational import JobRepository
from app.underwriting.persistence.company_research_models import (
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchGovernedBasisMismatch,
    CompanyResearchPersistedBundle,
    CompanyResearchRepository,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.company_research_sources import (
    CompanyResearchEvidenceCompilation,
    CompanyResearchProviderInput,
    CompanyResearchSourceCompiler,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchGovernedInputs,
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_boundary import (
    resolve_alphabet_company_research_boundary,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchBuildInput,
    CompanyResearchBuildResult,
    CompanyResearchModelBuilder,
)
from app.underwriting.services.workspace_draft import WorkspaceDraftService
from app.underwriting.domain.company_research import (
    CompanyResearchAssessment,
    CompanyResearchIdentitySet,
    CompanyResearchMemoArtifact,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import UnderwritingResearchProject
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetGoldenCaseFixtureError,
)
from app.underwriting.domain.company_research import CompanyResearchValidationError


COMPANY_RESEARCH_STAGES = (
    "prepare_sources",
    "model_bundle",
)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 600)
_WORKER_CANDIDATE_PAGE_SIZE = 100
_SAFE_PROVIDER_ERROR = "provider_unavailable"
_SAFE_STALE_ERROR = "stale_output_discarded"
_VALIDATION_MESSAGE_LIMIT = 512
RETRYABLE_PROVIDER_ERRORS = (TimeoutError, ConnectionError)


def _safe_validation_message(message: str | None) -> str | None:
    if message is None:
        return None
    normalized = " ".join(message.split())
    if not normalized:
        return None
    return normalized[:_VALIDATION_MESSAGE_LIMIT]


@dataclass(frozen=True, slots=True)
class CompanyResearchClaim:
    job_id: UUID
    preparation_id: UUID
    claim_token: str
    request_hash: str
    strategy_version: str
    step: str


@dataclass(frozen=True, slots=True)
class _ModelBuildBoundary:
    """Immutable model inputs plus exact publication parents."""

    build_input: CompanyResearchBuildInput
    evidence_artifact_id: UUID
    evidence_content_hash: str
    research_gaps_artifact_id: UUID
    research_gaps_content_hash: str
    workspace_draft_id: UUID
    workspace_draft_lock_version: int
    historical_basis_id: UUID
    historical_basis_content_hash: str
    source_refs: tuple[dict[str, str], ...]


class CompanyResearchPreparationWorker:
    """Claim, compile outside a transaction, then commit one fenced output."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime],
        provider: Callable[
            [CompanyResearchProviderInput], CompanyResearchEvidenceCompilation
        ]
        | None = None,
        model_provider: Callable[
            [CompanyResearchBuildInput], CompanyResearchBuildResult
        ]
        | None = None,
    ) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._jobs = JobRepository(session)
        self._provider = provider
        self._model_provider = model_provider

    def _utcnow(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    def _candidate_pages(self, statement):
        cursor_created_at: datetime | None = None
        cursor_job_id: UUID | None = None
        while True:
            page_statement = statement
            if cursor_created_at is not None and cursor_job_id is not None:
                page_statement = page_statement.where(
                    or_(
                        Job.created_at > cursor_created_at,
                        and_(
                            Job.created_at == cursor_created_at,
                            Job.id > cursor_job_id,
                        ),
                    )
                )
            rows = tuple(
                self._session.execute(
                    page_statement.order_by(Job.created_at, Job.id).limit(
                        _WORKER_CANDIDATE_PAGE_SIZE
                    )
                )
            )
            if not rows:
                return
            cursor_job_id, _, cursor_created_at = rows[-1]
            yield tuple((job_id, preparation_id) for job_id, preparation_id, _ in rows)
            if len(rows) < _WORKER_CANDIDATE_PAGE_SIZE:
                return

    def claim_next(self) -> CompanyResearchClaim | None:
        """Atomically claim only an explicitly executable company stage."""
        self._repository._reserve_sqlite_writer_before_ownership_read()
        now = self._utcnow()
        candidate_statement = (
            select(Job.id, CompanyResearchPreparation.id, Job.created_at)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                Job.kind == "prepare_company_research",
                Job.target_type == "company_research_preparation",
                Job.target_id == CompanyResearchPreparation.id,
                Job.research_case_id.is_(None),
                Job.status == "queued",
                Job.cancel_requested.is_(False),
                or_(
                    and_(
                        Job.step == "evidence_index",
                        CompanyResearchPreparation.status.in_(
                            ("queued", "recoverable_failure")
                        ),
                        CompanyResearchPreparation.current_step == "evidence_index",
                    ),
                    and_(
                        Job.step == "model_bundle",
                        CompanyResearchPreparation.status.in_(
                            ("building_model", "recoverable_failure")
                        ),
                        CompanyResearchPreparation.current_step == "model_bundle",
                    ),
                ),
                (CompanyResearchPreparation.next_attempt_at.is_(None))
                | (CompanyResearchPreparation.next_attempt_at <= now),
            )
        )
        for candidates in self._candidate_pages(candidate_statement):
            for job_id, preparation_id in candidates:
                locked = self._repository.lock_worker_claim_state(
                    preparation_id=preparation_id,
                    job_id=job_id,
                    skip_locked=True,
                )
                if locked is None:
                    continue
                preparation, job = locked
                if (
                    job.target_id != preparation.id
                    or job.status != "queued"
                    or job.cancel_requested
                    or job.step not in {"evidence_index", "model_bundle"}
                    or preparation.current_step != job.step
                    or preparation.status
                    not in (
                        {"queued", "recoverable_failure"}
                        if job.step == "evidence_index"
                        else {"building_model", "recoverable_failure"}
                    )
                    or (
                        preparation.next_attempt_at is not None
                        and self._repository._persisted_utc(preparation.next_attempt_at)
                        > now
                    )
                ):
                    continue
                token = secrets.token_hex(16)
                job.status = "running"
                job.started_at = now
                job.claim_token = token
                stage = job.step
                preparation.status = (
                    "preparing_sources"
                    if stage == "evidence_index"
                    else "building_model"
                )
                preparation.progress = 5 if stage == "evidence_index" else 30
                preparation.updated_at = now
                self._jobs.append_event(
                    job_id=job.id,
                    seq=self._jobs.next_event_seq(job.id),
                    status="running",
                    step=stage,
                    message=f"company research {stage} job claimed",
                )
                self._repository.append_event(
                    preparation_id=preparation.id,
                    event_type="source_stage_claimed"
                    if stage == "evidence_index"
                    else "model_stage_claimed",
                    payload={"stage": stage, "attempt": job.attempt},
                    created_at=now,
                )
                self._session.flush()
                return CompanyResearchClaim(
                    job_id=job.id,
                    preparation_id=preparation.id,
                    claim_token=token,
                    request_hash=preparation.request_hash,
                    strategy_version=preparation.strategy_version,
                    step=stage,
                )
        return None

    def recover_stale_claims(self, *, before: datetime) -> int:
        """Make an abandoned source claim eligible again without cloning it."""
        before = before.astimezone(UTC)
        candidate_statement = (
            select(Job.id, CompanyResearchPreparation.id, Job.created_at)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                Job.kind == "prepare_company_research",
                Job.target_type == "company_research_preparation",
                Job.target_id == CompanyResearchPreparation.id,
                Job.research_case_id.is_(None),
                Job.status == "running",
                Job.step.in_(("evidence_index", "model_bundle")),
                Job.started_at.is_not(None),
                Job.started_at < before,
                or_(
                    and_(
                        CompanyResearchPreparation.status == "preparing_sources",
                        CompanyResearchPreparation.current_step == "evidence_index",
                    ),
                    and_(
                        CompanyResearchPreparation.status == "building_model",
                        CompanyResearchPreparation.current_step == "model_bundle",
                    ),
                ),
            )
        )
        recovered = 0
        for candidates in self._candidate_pages(candidate_statement):
            for job_id, preparation_id in candidates:
                locked = self._repository.lock_worker_claim_state(
                    preparation_id=preparation_id,
                    job_id=job_id,
                    skip_locked=True,
                )
                if locked is None:
                    continue
                preparation, job = locked
                if (
                    not self._repository.is_exact_prepare_job_owner(job, preparation.id)
                    or job.status != "running"
                    or job.step not in {"evidence_index", "model_bundle"}
                    or job.started_at is None
                    or self._repository._persisted_utc(job.started_at) >= before
                    or preparation.current_step != job.step
                    or preparation.status
                    != (
                        "preparing_sources"
                        if job.step == "evidence_index"
                        else "building_model"
                    )
                ):
                    continue
                now = self._utcnow()
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.error = _SAFE_STALE_ERROR
                    preparation.status = "blocked"
                    preparation.last_error_code = _SAFE_STALE_ERROR
                    event_type = "stale_output_discarded"
                else:
                    job.status = "queued"
                    job.error = None
                    job.started_at = None
                    preparation.status = (
                        "queued" if job.step == "evidence_index" else "building_model"
                    )
                    preparation.progress = 0 if job.step == "evidence_index" else 25
                    preparation.last_error_code = None
                    event_type = (
                        "source_claim_recovered"
                        if job.step == "evidence_index"
                        else "model_claim_recovered"
                    )
                job.claim_token = None
                job.finished_at = now if job.status == "cancelled" else None
                preparation.updated_at = now
                self._jobs.append_event(
                    job_id=job.id,
                    seq=self._jobs.next_event_seq(job.id),
                    status=job.status,
                    step=job.step,
                    message=event_type,
                )
                self._repository.append_event(
                    preparation_id=preparation.id,
                    event_type=event_type,
                    payload={"stage": job.step},
                    created_at=now,
                )
                recovered += 1
        self._session.flush()
        return recovered

    def cancel_queued_claims(self) -> int:
        """Honor cancellation before a queued job can reach provider work."""
        candidate_statement = (
            select(Job.id, CompanyResearchPreparation.id, Job.created_at)
            .join(
                CompanyResearchPreparation,
                CompanyResearchPreparation.job_id == Job.id,
            )
            .where(
                Job.kind == "prepare_company_research",
                Job.target_type == "company_research_preparation",
                Job.target_id == CompanyResearchPreparation.id,
                Job.research_case_id.is_(None),
                Job.status == "queued",
                Job.cancel_requested.is_(True),
                Job.step.in_(("evidence_index", "model_bundle")),
                or_(
                    and_(
                        CompanyResearchPreparation.status.in_(
                            ("queued", "recoverable_failure")
                        ),
                        CompanyResearchPreparation.current_step == "evidence_index",
                    ),
                    and_(
                        CompanyResearchPreparation.status.in_(
                            ("building_model", "recoverable_failure")
                        ),
                        CompanyResearchPreparation.current_step == "model_bundle",
                    ),
                ),
            )
        )
        cancelled = 0
        for candidates in self._candidate_pages(candidate_statement):
            for job_id, preparation_id in candidates:
                locked = self._repository.lock_worker_claim_state(
                    preparation_id=preparation_id,
                    job_id=job_id,
                    skip_locked=True,
                )
                if locked is None:
                    continue
                preparation, job = locked
                if (
                    not self._repository.is_exact_prepare_job_owner(job, preparation.id)
                    or job.status != "queued"
                    or not job.cancel_requested
                    or job.step not in {"evidence_index", "model_bundle"}
                    or preparation.current_step != job.step
                    or preparation.status
                    not in (
                        {"queued", "recoverable_failure"}
                        if job.step == "evidence_index"
                        else {"building_model", "recoverable_failure"}
                    )
                ):
                    continue
                now = self._utcnow()
                job.status = "cancelled"
                job.error = _SAFE_STALE_ERROR
                job.finished_at = now
                preparation.status = "blocked"
                preparation.last_error_code = _SAFE_STALE_ERROR
                preparation.updated_at = now
                self._jobs.append_event(
                    job_id=job.id,
                    seq=self._jobs.next_event_seq(job.id),
                    status="cancelled",
                    step=job.step,
                    message=_SAFE_STALE_ERROR,
                )
                self._repository.append_event(
                    preparation_id=preparation.id,
                    event_type="stale_output_discarded",
                    payload={"stage": job.step},
                    created_at=now,
                )
                cancelled += 1
        self._session.flush()
        return cancelled

    def _current_claim(
        self, claim: CompanyResearchClaim
    ) -> tuple[Job, CompanyResearchPreparation] | None:
        locked = self._repository.lock_worker_claim_state(
            preparation_id=claim.preparation_id,
            job_id=claim.job_id,
        )
        if locked is None:
            return None
        preparation, job = locked
        if (
            job is None
            or preparation is None
            or preparation.job_id != job.id
            or not self._repository.is_exact_prepare_job_owner(job, preparation.id)
            or job.status != "running"
            or job.claim_token != claim.claim_token
            or job.cancel_requested
            or job.step != claim.step
            or preparation.current_step != claim.step
            or preparation.status
            != (
                "preparing_sources"
                if claim.step == "evidence_index"
                else "building_model"
            )
            or preparation.request_hash != claim.request_hash
            or preparation.strategy_version != claim.strategy_version
        ):
            return None
        return job, preparation

    def _discard(self, claim: CompanyResearchClaim) -> None:
        locked = self._repository.lock_worker_claim_state(
            preparation_id=claim.preparation_id,
            job_id=claim.job_id,
        )
        if locked is None:
            return
        preparation, job = locked
        if (
            job is None
            or preparation is None
            or preparation.job_id != job.id
            or not self._repository.is_exact_prepare_job_owner(job, preparation.id)
            or job.claim_token != claim.claim_token
            or job.status != "running"
            or job.step != claim.step
            or preparation.current_step != claim.step
            or preparation.status
            != (
                "preparing_sources"
                if claim.step == "evidence_index"
                else "building_model"
            )
        ):
            return
        now = self._utcnow()
        job.status = "cancelled"
        job.error = _SAFE_STALE_ERROR
        job.finished_at = now
        job.claim_token = None
        preparation.status = "blocked"
        preparation.last_error_code = _SAFE_STALE_ERROR
        preparation.updated_at = now
        self._jobs.append_event(
            job_id=job.id,
            seq=self._jobs.next_event_seq(job.id),
            status="cancelled",
            step=claim.step,
            message=_SAFE_STALE_ERROR,
        )
        self._repository.append_event(
            preparation_id=preparation.id,
            event_type="stale_output_discarded",
            payload={"stage": claim.step},
            created_at=now,
        )
        self._session.flush()

    def _recoverable_failure(self, claim: CompanyResearchClaim) -> None:
        current = self._current_claim(claim)
        if current is None:
            return
        job, preparation = current
        now = self._utcnow()
        if job.attempt >= MAX_ATTEMPTS:
            job.status = "failed"
            job.finished_at = now
            job.claim_token = None
            preparation.status = "blocked"
            preparation.next_attempt_at = None
        else:
            job.status = "queued"
            job.attempt += 1
            job.started_at = None
            job.claim_token = None
            preparation.status = "recoverable_failure"
            preparation.attempt += 1
            preparation.next_attempt_at = now + timedelta(
                seconds=BACKOFF_SECONDS[job.attempt - 2]
            )
        job.error = _SAFE_PROVIDER_ERROR
        preparation.last_error_code = _SAFE_PROVIDER_ERROR
        preparation.updated_at = now
        self._jobs.append_event(
            job_id=job.id,
            seq=self._jobs.next_event_seq(job.id),
            status=job.status,
            step=claim.step,
            message=_SAFE_PROVIDER_ERROR,
        )
        self._repository.append_event(
            preparation_id=preparation.id,
            event_type=(
                "source_provider_failed"
                if claim.step == "evidence_index"
                else "model_provider_failed"
            ),
            payload={
                "code": _SAFE_PROVIDER_ERROR,
                "recoverable": job.status == "queued",
            },
            created_at=now,
        )
        self._session.flush()

    def _block(
        self,
        claim: CompanyResearchClaim,
        *,
        error_code: str = "validation_failed",
        error_message: str | None = None,
    ) -> None:
        current = self._current_claim(claim)
        if current is None:
            return
        job, preparation = current
        now = self._utcnow()
        event_message = _safe_validation_message(error_message)
        if event_message is None:
            job_error = error_code
            job_event_message = error_code
        else:
            prefix = f"{error_code}: "
            job_error = prefix + event_message[: _VALIDATION_MESSAGE_LIMIT - len(prefix)]
            job_event_message = event_message
        job.status = "failed"
        job.error = job_error
        job.claim_token = None
        job.finished_at = now
        preparation.status = "blocked"
        preparation.last_error_code = error_code
        preparation.updated_at = now
        self._jobs.append_event(
            job_id=job.id,
            seq=self._jobs.next_event_seq(job.id),
            status="failed",
            step=claim.step,
            message=job_event_message,
        )
        event_payload = {"code": error_code}
        if event_message is not None:
            event_payload["message"] = event_message
        self._repository.append_event(
            preparation_id=preparation.id,
            event_type=(
                "source_preparation_blocked"
                if claim.step == "evidence_index"
                else "model_preparation_blocked"
            ),
            payload=event_payload,
            created_at=now,
        )
        self._session.flush()

    def _provider_input(
        self, preparation: CompanyResearchPreparation
    ) -> CompanyResearchProviderInput:
        project = self._session.get(UnderwritingResearchProject, preparation.project_id)
        if project is None:
            raise ValidationError("company research preparation project is missing")
        company = self._session.scalar(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id == project.primary_company_id
            )
        )
        if company is None:
            raise ValidationError("company research preparation company is missing")
        return CompanyResearchProviderInput(
            preparation_id=preparation.id,
            project_id=project.id,
            company_external_key=company.external_key,
            request_hash=preparation.request_hash,
            strategy_version=preparation.strategy_version,
        )

    def _compile(
        self, provider_input: CompanyResearchProviderInput
    ) -> CompanyResearchEvidenceCompilation:
        if self._provider is not None:
            return self._provider(provider_input)
        return CompanyResearchSourceCompiler().compile_evidence_index(provider_input)

    def _governed_inputs(
        self, *, project_id: UUID, cutoff_at: datetime
    ) -> CompanyResearchGovernedInputs:
        return CompanyResearchInitializer(
            self._session, now=self._now
        ).governed_inputs(project_id=project_id, cutoff_at=cutoff_at)

    def _model_input(self, claim: CompanyResearchClaim) -> _ModelBuildBoundary:
        preparation = self._session.get(
            CompanyResearchPreparation, claim.preparation_id
        )
        if preparation is None:
            raise ValidationError("company research preparation is missing")
        project = self._session.get(
            UnderwritingResearchProject, preparation.project_id
        )
        if project is None:
            raise ValidationError("company research preparation project is missing")
        evidence = self._repository.current_artifact(
            preparation.project_id, "evidence_index"
        )
        gaps = self._repository.current_artifact(
            preparation.project_id, "research_gaps"
        )
        if evidence is None or gaps is None:
            raise ValidationError("reviewed evidence and research gaps are required")
        cutoff = self._repository.evidence_cutoff(evidence)
        initializer = CompanyResearchInitializer(self._session, now=self._now)
        preview = initializer.preview(
            company_id=project.primary_company_id,
            cutoff_at=cutoff,
        )
        governed = self._governed_inputs(
            project_id=preparation.project_id,
            cutoff_at=cutoff,
        )
        expected_boundary = resolve_alphabet_company_research_boundary(cutoff)
        draft = WorkspaceDraftService(self._session, now=self._now).read(
            preparation.project_id
        )
        if draft is None:
            raise ValidationError("company research workspace draft is missing")
        if draft.content.historical_basis_id is None:
            raise ValidationError("company research historical basis is missing")
        basis = ProductRepository(self._session).product_basis(
            draft.content.historical_basis_id
        )
        if basis is None:
            raise ValidationError("company research historical basis is invalid")
        historical_basis_id = draft.content.historical_basis_id
        try:
            authenticated_basis = (
                self._repository.authenticate_governed_historical_basis(
                    basis,
                    expected_input=expected_boundary.basis_input,
                    expected_content_hash=expected_boundary.basis_content_hash,
                )
            )
        except CompanyResearchGovernedBasisMismatch as exc:
            raise ValidationError(
                "company research historical basis does not match governed model contract"
            ) from exc
        if authenticated_basis.id != historical_basis_id:
            raise ValidationError(
                "company research historical basis does not match governed model contract"
            )
        bindings = (
            governed.market_context.snapshot_bindings
            if governed.market_context is not None
            else ()
        )
        source_refs = self._repository.expected_model_source_refs(
            evidence=evidence,
            predecessor_gaps=gaps,
            market_snapshot_bindings=bindings,
        )
        build_input = CompanyResearchBuildInput(
            project_id=preparation.project_id,
            identity_set=CompanyResearchIdentitySet(
                company=preview.company,
                securities=preview.securities,
            ),
            cutoff_at=cutoff,
            required_return=preview.required_return,
            evidence_artifact_id=evidence.id,
            evidence_content_hash=canonical_hash(evidence.payload),
            evidence_payload=evidence.payload,
            gap_payload=gaps.payload,
            source_refs=tuple(dict(value) for value in evidence.source_refs),
            model_template=governed.model_template,
            strategy_assumptions=governed.strategy_assumptions,
            market_context=governed.market_context,
        )
        return _ModelBuildBoundary(
            build_input=build_input,
            evidence_artifact_id=evidence.id,
            evidence_content_hash=evidence.content_hash,
            research_gaps_artifact_id=gaps.id,
            research_gaps_content_hash=gaps.content_hash,
            workspace_draft_id=draft.id,
            workspace_draft_lock_version=draft.lock_version,
            historical_basis_id=historical_basis_id,
            historical_basis_content_hash=authenticated_basis.content_hash,
            source_refs=source_refs,
        )

    def _compile_model(
        self, build_input: CompanyResearchBuildInput
    ) -> CompanyResearchBuildResult:
        if self._model_provider is not None:
            return self._model_provider(build_input)
        return CompanyResearchModelBuilder().build(build_input)

    @staticmethod
    def _persisted_bundle(
        boundary: _ModelBuildBoundary,
        result: CompanyResearchBuildResult,
    ) -> CompanyResearchPersistedBundle:
        if type(result) is not CompanyResearchBuildResult:
            raise ValidationError("company research model result is invalid")
        if (
            type(result.assessment) is not CompanyResearchAssessment
            or type(result.memo) is not CompanyResearchMemoArtifact
        ):
            raise ValidationError("company research model result is invalid")
        if result.assessment.status != result.memo.assessment_status:
            raise ValidationError("company research model assessment is inconsistent")
        CompanyResearchModelBuilder.validate_governed_gap_projection(
            boundary.build_input,
            result.gaps,
        )
        values: dict[str, object] = {
            "business_map": result.business_map,
            "driver_map": result.driver_map,
            "financial_bridge": result.financial_bridge,
            "scenario_set": result.scenario_set,
            "judgment_context": result.judgment_context,
            "research_gaps": result.gaps,
            "memo": result.memo,
        }
        if result.valuation_set is not None:
            values["valuation_set"] = result.valuation_set
        payloads = {
            kind: CompanyResearchArtifactCodec.encode(kind, artifact)
            for kind, artifact in values.items()
        }
        market_context = boundary.build_input.market_context
        return CompanyResearchPersistedBundle(
            evidence_artifact_id=boundary.evidence_artifact_id,
            evidence_content_hash=boundary.evidence_content_hash,
            research_gaps_artifact_id=boundary.research_gaps_artifact_id,
            research_gaps_content_hash=boundary.research_gaps_content_hash,
            workspace_draft_id=boundary.workspace_draft_id,
            workspace_draft_lock_version=boundary.workspace_draft_lock_version,
            historical_basis_id=boundary.historical_basis_id,
            historical_basis_content_hash=boundary.historical_basis_content_hash,
            business_map=payloads["business_map"],
            driver_map=payloads["driver_map"],
            financial_bridge=payloads["financial_bridge"],
            scenario_set=payloads["scenario_set"],
            valuation_set=payloads.get("valuation_set"),
            judgment_context=payloads["judgment_context"],
            research_gaps=payloads["research_gaps"],
            memo=payloads["memo"],
            source_refs=boundary.source_refs,
            market_snapshot_bindings=(
                market_context.snapshot_bindings
                if market_context is not None
                else ()
            ),
        )

    @staticmethod
    def _is_stale_model_error(exc: ValidationError) -> bool:
        message = str(exc)
        return any(
            marker in message
            for marker in (
                "claim is stale",
                "model inputs are stale",
                "workspace draft is stale",
                "historical basis is stale",
                "market bindings do not match workspace draft",
            )
        )

    def run_claim(
        self, claim: CompanyResearchClaim
    ) -> Literal[
        "awaiting_evidence_review",
        "awaiting_judgment_review",
        "recoverable_failure",
        "discarded",
    ]:
        """Run provider work without locks, then fence its immutable commit."""
        current = self._current_claim(claim)
        if current is None:
            self._discard(claim)
            return "discarded"
        _job, preparation = current
        if claim.step == "model_bundle":
            # The claimed lease is durable before governed/provider reads.  No
            # Job or preparation row lock is held while the model is compiled.
            self._session.commit()
            try:
                boundary = self._model_input(claim)
                # Governed market preparation may append exact snapshot inputs;
                # publish those immutable inputs and release their transaction
                # before invoking a potentially slow provider.
                self._session.commit()
            except ValidationError as exc:
                self._session.rollback()
                self._block(claim, error_message=str(exc))
                return "discarded"
            except CompanyResearchValidationError:
                self._session.rollback()
                self._block(claim)
                return "discarded"
            except Exception:
                self._session.rollback()
                raise
            try:
                result = self._compile_model(boundary.build_input)
            except RETRYABLE_PROVIDER_ERRORS:
                self._session.rollback()
                self._recoverable_failure(claim)
                return "recoverable_failure"
            except (
                AlphabetGoldenCaseFixtureError,
                CompanyResearchValidationError,
                ValidationError,
            ):
                self._session.rollback()
                self._block(claim)
                return "discarded"
            except Exception:
                self._session.rollback()
                raise
            try:
                bundle = self._persisted_bundle(boundary, result)
            except ValidationError as exc:
                self._session.rollback()
                self._block(claim, error_message=str(exc))
                return "discarded"
            except CompanyResearchValidationError:
                self._session.rollback()
                self._block(claim)
                return "discarded"
            except Exception:
                self._session.rollback()
                raise
            try:
                self._repository.complete_model_bundle(
                    claim.preparation_id,
                    bundle=bundle,
                    created_at=self._utcnow(),
                    expected_claim_token=claim.claim_token,
                    expected_request_hash=claim.request_hash,
                    expected_strategy_version=claim.strategy_version,
                )
                self._session.commit()
            except StaleParentError:
                self._session.rollback()
                self._discard(claim)
                return "discarded"
            except ValidationError as exc:
                self._session.rollback()
                if self._is_stale_model_error(exc):
                    self._discard(claim)
                else:
                    self._block(claim, error_message=str(exc))
                return "discarded"
            return "awaiting_judgment_review"
        if claim.step != "evidence_index":
            self._discard(claim)
            return "discarded"
        provider_input = self._provider_input(preparation)
        # Publish the fenced claim before file/provider work.  The completion
        # path below takes fresh locks and checks the same token again.
        self._session.commit()
        try:
            compiled = self._compile(provider_input)
        except RETRYABLE_PROVIDER_ERRORS:
            self._recoverable_failure(claim)
            return "recoverable_failure"
        except (
            AlphabetGoldenCaseFixtureError,
            CompanyResearchValidationError,
            ValidationError,
        ):
            self._block(claim)
            return "discarded"
        except Exception:
            self._session.rollback()
            raise

        # The provider/result boundary is explicit: publish no database write
        # until the output has been assembled, then reacquire the claim fence.
        try:
            self._repository.complete_evidence_preparation(
                claim.preparation_id,
                input_hash=compiled.input_hash,
                evidence_index_payload=compiled.evidence_index_payload,
                research_gaps_payload=compiled.research_gaps_payload,
                source_refs=compiled.source_refs,
                created_at=self._utcnow(),
                expected_claim_token=claim.claim_token,
                expected_request_hash=claim.request_hash,
                expected_strategy_version=claim.strategy_version,
            )
            self._session.commit()
        except (StaleParentError, ValidationError):
            self._session.rollback()
            self._discard(claim)
            return "discarded"
        return "awaiting_evidence_review"
