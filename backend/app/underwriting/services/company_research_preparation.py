"""Lease-fenced execution of the review-gated company-research source stage.

Only ``prepare_sources`` is currently executable: it produces the immutable
evidence index and research gaps, then deliberately stops for human review.
The remaining model stages are never inferred from unreviewed source material.
"""

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
    CompanyResearchRepository,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.company_research_sources import (
    CompanyResearchEvidenceCompilation,
    CompanyResearchProviderInput,
    CompanyResearchSourceCompiler,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import UnderwritingResearchProject
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetGoldenCaseFixtureError,
)
from app.underwriting.domain.company_research import CompanyResearchValidationError


COMPANY_RESEARCH_STAGES = (
    "prepare_sources",
    "build_business_map",
    "build_driver_map",
    "build_financial_bridge",
    "build_scenarios",
    "build_valuation",
    "evaluate_readiness",
)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (30, 120, 600)
_SAFE_PROVIDER_ERROR = "provider_unavailable"
_SAFE_UNKNOWN_PROVIDER_ERROR = "provider_failed"
_SAFE_STALE_ERROR = "stale_output_discarded"


@dataclass(frozen=True, slots=True)
class CompanyResearchClaim:
    job_id: UUID
    preparation_id: UUID
    claim_token: str
    request_hash: str
    strategy_version: str
    step: str


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
    ) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._jobs = JobRepository(session)
        self._provider = provider

    def _utcnow(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    def claim_next(self) -> CompanyResearchClaim | None:
        """Atomically claim only an explicitly executable company stage."""
        self._repository._reserve_sqlite_writer_before_ownership_read()
        now = self._utcnow()
        row = self._session.execute(
            select(Job, CompanyResearchPreparation)
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
                        Job.step == "business_map",
                        CompanyResearchPreparation.status == "building_model",
                        CompanyResearchPreparation.current_step == "business_map",
                    ),
                ),
                (CompanyResearchPreparation.next_attempt_at.is_(None))
                | (CompanyResearchPreparation.next_attempt_at <= now),
            )
            .order_by(Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if row is None:
            return None
        job, preparation = row
        if job.target_id != preparation.id:
            return None
        token = secrets.token_hex(16)
        job.status = "running"
        job.started_at = now
        job.claim_token = token
        stage = job.step
        preparation.status = (
            "preparing_sources" if stage == "evidence_index" else "building_model"
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

    def recover_stale_claims(self, *, before: datetime) -> int:
        """Make an abandoned source claim eligible again without cloning it."""
        before = before.astimezone(UTC)
        jobs = tuple(
            self._session.execute(
                select(Job, CompanyResearchPreparation)
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
                    Job.step == "evidence_index",
                    Job.started_at.is_not(None),
                    Job.started_at < before,
                    CompanyResearchPreparation.status == "preparing_sources",
                    CompanyResearchPreparation.current_step == "evidence_index",
                )
                .with_for_update(skip_locked=True)
            )
        )
        recovered = 0
        for job, preparation in jobs:
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
                preparation.status = "queued"
                preparation.progress = 0
                preparation.last_error_code = None
                event_type = "source_claim_recovered"
            job.claim_token = None
            job.finished_at = now if job.status == "cancelled" else None
            preparation.updated_at = now
            self._jobs.append_event(
                job_id=job.id,
                seq=self._jobs.next_event_seq(job.id),
                status=job.status,
                step="evidence_index",
                message=event_type,
            )
            self._repository.append_event(
                preparation_id=preparation.id,
                event_type=event_type,
                payload={"stage": "prepare_sources"},
                created_at=now,
            )
            recovered += 1
        self._session.flush()
        return recovered

    def cancel_queued_claims(self) -> int:
        """Honor cancellation before a queued job can reach provider work."""
        jobs = tuple(
            self._session.execute(
                select(Job, CompanyResearchPreparation)
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
                    Job.step == "evidence_index",
                    CompanyResearchPreparation.status.in_(
                        ("queued", "recoverable_failure")
                    ),
                    CompanyResearchPreparation.current_step == "evidence_index",
                )
                .with_for_update(skip_locked=True)
            )
        )
        cancelled = 0
        for job, preparation in jobs:
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
                step="evidence_index",
                message=_SAFE_STALE_ERROR,
            )
            self._repository.append_event(
                preparation_id=preparation.id,
                event_type="stale_output_discarded",
                payload={"stage": "prepare_sources"},
                created_at=now,
            )
            cancelled += 1
        self._session.flush()
        return cancelled

    def _current_claim(
        self, claim: CompanyResearchClaim
    ) -> tuple[Job, CompanyResearchPreparation] | None:
        job = self._session.scalar(
            select(Job).where(Job.id == claim.job_id).with_for_update()
        )
        preparation = self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.id == claim.preparation_id)
            .with_for_update()
        )
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
        job = self._session.scalar(
            select(Job).where(Job.id == claim.job_id).with_for_update()
        )
        preparation = self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.id == claim.preparation_id)
            .with_for_update()
        )
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
            step="evidence_index",
            message=_SAFE_PROVIDER_ERROR,
        )
        self._repository.append_event(
            preparation_id=preparation.id,
            event_type="source_provider_failed",
            payload={
                "code": _SAFE_PROVIDER_ERROR,
                "recoverable": job.status == "queued",
            },
            created_at=now,
        )
        self._session.flush()

    def _block(
        self, claim: CompanyResearchClaim, *, error_code: str = "validation_failed"
    ) -> None:
        current = self._current_claim(claim)
        if current is None:
            return
        job, preparation = current
        now = self._utcnow()
        job.status = "failed"
        job.error = error_code
        job.claim_token = None
        job.finished_at = now
        preparation.status = "blocked"
        preparation.last_error_code = error_code
        preparation.updated_at = now
        self._jobs.append_event(
            job_id=job.id,
            seq=self._jobs.next_event_seq(job.id),
            status="failed",
            step="evidence_index",
            message=error_code,
        )
        self._repository.append_event(
            preparation_id=preparation.id,
            event_type="source_preparation_blocked",
            payload={"code": error_code},
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
        if claim.step == "business_map":
            try:
                self._repository.complete_business_map_preparation(
                    claim.preparation_id,
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
        except (OSError, TimeoutError, ConnectionError):
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
            self._block(claim, error_code=_SAFE_UNKNOWN_PROVIDER_ERROR)
            return "discarded"

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
