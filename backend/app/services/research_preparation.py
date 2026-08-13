"""In-process command state machine for pre-authorization research work."""
from __future__ import annotations

import copy
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.research_preparation import (
    ArtifactKind,
    MAX_CANDIDATES,
    PreparationStep,
    candidate_context_fingerprint,
    preparation_input_fingerprint,
)
from app.errors import ConflictError, NotFoundError
from app.models.ledger import (
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseTenantAdmission,
    SourceStatement,
    ValidationError,
)
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
)
from app.repositories.research_preparation import ResearchPreparationRepository
from app.services.atomic_claims import AtomicClaimService


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    candidate_id: uuid.UUID
    outcome: Literal["confirmed", "modified", "rejected"]
    reason: str
    normalized_text: str | None = None


@dataclass(frozen=True, slots=True)
class ProtocolConfirmation:
    draft_sequence: int
    edits: dict[str, object]


_STEP_FIELDS: dict[PreparationStep, str] = {
    "parse_claims": "parse_claims_state",
    "draft_protocol": "draft_protocol_state",
    "draft_evidence_plan": "draft_evidence_plan_state",
}
_STEP_ARTIFACTS: dict[PreparationStep, ArtifactKind] = {
    "parse_claims": "atomic_claim_candidates",
    "draft_protocol": "research_protocol_draft",
    "draft_evidence_plan": "evidence_acquisition_plan",
}
PreparationFailureCode = Literal[
    "provider_unavailable",
    "invalid_response",
    "retry_exhausted",
]
_PREPARATION_FAILURE_CODES = frozenset(
    {"provider_unavailable", "invalid_response", "retry_exhausted"}
)
_PERSISTED_PROVIDER_FAILURE = "preparation_provider_unavailable"
_CLAIM_OUTCOMES = frozenset({"confirmed", "modified", "rejected"})
_EDITABLE_PROTOCOL_KEYS = frozenset(
    {
        "outcomes",
        "baseline",
        "horizon",
        "mechanisms",
        "verification_rules",
        "rationale",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchPreparationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ResearchPreparationRepository(session)
        self._claims = AtomicClaimService(session)

    def create_for_case(
        self, case_id: uuid.UUID, *, input_fingerprint: str, actor: str
    ) -> ResearchPreparation:
        preparation = self._lock(case_id)
        if preparation is None:
            now = _utcnow()
            preparation = ResearchPreparation(
                research_case_id=case_id,
                version=1,
                input_fingerprint=input_fingerprint,
                status="preparing",
                parse_claims_state="queued",
                draft_protocol_state="queued",
                draft_evidence_plan_state="queued",
                claim_review_state="locked",
                protocol_review_state="locked",
                plan_review_state="locked",
                research_run_id=None,
                next_attempt_at=None,
                last_error_code=None,
                created_at=now,
                updated_at=now,
            )
            self._session.add(preparation)
            self._session.flush()
            self._repo.append_event(
                preparation,
                research_case_id=case_id,
                type="preparation_created",
                step=None,
                message="research preparation created",
                detail={"actor": actor},
            )
            self._repo.queue_step_job(
                preparation, research_case_id=case_id, step="parse_claims"
            )
            return preparation
        if preparation.input_fingerprint == input_fingerprint:
            return preparation

        preparation.version += 1
        preparation.input_fingerprint = input_fingerprint
        self._invalidate_current_artifacts(preparation, reason="input_changed")
        # The check constraint permits this only as one atomic UPDATE with the
        # following status transition; never flush an authorized row without
        # its required run reference.
        preparation.research_run_id = None
        preparation.status = "preparing"
        preparation.parse_claims_state = "queued"
        preparation.draft_protocol_state = "queued"
        preparation.draft_evidence_plan_state = "queued"
        preparation.claim_review_state = "locked"
        preparation.protocol_review_state = "locked"
        preparation.plan_review_state = "locked"
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_inputs_replaced",
            step=None,
            message="research preparation inputs replaced",
            detail={"actor": actor, "version": preparation.version},
        )
        self._repo.queue_step_job(
            preparation, research_case_id=case_id, step="parse_claims"
        )
        return preparation

    def invalidate_from_scope_change(
        self,
        case_id: uuid.UUID,
        new_scope_id: uuid.UUID,
        *,
        actor: str,
        case_locked: bool = False,
    ) -> ResearchPreparation | None:
        """Invalidate scope-dependent drafts without replacing claim review.

        Scope changes use the same frozen admitted document.  Claim candidates
        and their human decisions stay intact; only protocol and evidence-plan
        drafts become stale.  This command never creates a ResearchRun.
        """
        preparation = self._lock(case_id, case_locked=case_locked)
        if preparation is None:
            return None
        document_id = self._session.scalar(
            select(CaseTenantAdmission.initial_document_version_id).where(
                CaseTenantAdmission.research_case_id == case_id
            )
        )
        if document_id is None:
            raise ConflictError("research preparation requires an admitted document")
        input_fingerprint = preparation_input_fingerprint(document_id, new_scope_id)
        if preparation.input_fingerprint == input_fingerprint:
            return preparation

        preparation.version += 1
        preparation.input_fingerprint = input_fingerprint
        self._invalidate_downstream(preparation, reason="scope_changed")
        # Keep this change in one flush: an authorized preparation cannot
        # temporarily exist without its required run reference.
        preparation.research_run_id = None
        preparation.status = "preparing"
        preparation.draft_protocol_state = (
            "queued"
            if preparation.claim_review_state == "confirmed"
            else "stale"
        )
        preparation.draft_evidence_plan_state = "stale"
        preparation.protocol_review_state = "locked"
        preparation.plan_review_state = "locked"
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_scope_changed",
            step=None,
            message="preparation drafts invalidated by scope change",
            detail={"actor": actor, "version": preparation.version},
        )
        if preparation.claim_review_state == "confirmed":
            self._repo.queue_step_job(
                preparation, research_case_id=case_id, step="draft_protocol"
            )
        elif preparation.parse_claims_state != "succeeded":
            # The predecessor's queued job is version-guarded and will be
            # discarded, so ensure this new preparation version remains live.
            preparation.parse_claims_state = "queued"
            self._repo.queue_step_job(
                preparation, research_case_id=case_id, step="parse_claims"
            )
        self._set_aggregate_status(preparation)
        return preparation

    def complete_system_step(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        payload: dict[str, object],
        *,
        expected_version: int,
        expected_fingerprint: str,
        expected_context_fingerprint: str | None = None,
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        if expected_version is None or expected_fingerprint is None:
            raise ConflictError("preparation output guards are required")
        if (
            expected_version != preparation.version
        ) or (
            expected_fingerprint != preparation.input_fingerprint
        ):
            self._repo.append_event(
                preparation,
                research_case_id=case_id,
                type="preparation_output_discarded",
                step=step,
                message="stale preparation output discarded",
                detail={"current_version": preparation.version},
            )
            return preparation
        allowed_states = {"queued", "running", "retrying"}
        if step == "draft_protocol":
            allowed_states.add("stale")
        self._require_eligible_step(preparation, step, allowed_states=allowed_states)
        context_fingerprint = None
        if step == "parse_claims":
            if expected_context_fingerprint is not None:
                raise ConflictError("parse claim output cannot carry a context fingerprint")
        else:
            current_context_fingerprint = self.current_candidate_context_fingerprint(
                case_id
            )
            if (
                expected_context_fingerprint is None
                or expected_context_fingerprint != current_context_fingerprint
            ):
                self._repo.append_event(
                    preparation,
                    research_case_id=case_id,
                    type="preparation_output_discarded",
                    step=step,
                    message="stale preparation output discarded",
                    detail={"current_version": preparation.version},
                )
                return preparation
            context_fingerprint = current_context_fingerprint
        artifact = self._repo.append_artifact(
            preparation,
            research_case_id=case_id,
            kind=_STEP_ARTIFACTS[step],
            input_fingerprint=preparation.input_fingerprint,
            payload=payload,
            context_fingerprint=context_fingerprint,
        )
        setattr(preparation, _STEP_FIELDS[step], "succeeded")
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        if step == "parse_claims":
            preparation.claim_review_state = "awaiting_review"
        elif step == "draft_protocol":
            preparation.protocol_review_state = "awaiting_review"
        else:
            preparation.plan_review_state = "awaiting_review"
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_step_completed",
            step=step,
            message="preparation system step completed",
            detail={"artifact_sequence": artifact.sequence},
        )
        return preparation

    def current_candidate_context_fingerprint(self, case_id: uuid.UUID) -> str:
        """Return the ordered current-review context used to guard drafts."""
        preparation = self._require_preparation(case_id)
        artifact = self._repo.current_artifact(
            preparation.id, "atomic_claim_candidates"
        )
        if artifact is None:
            return candidate_context_fingerprint(None, ())
        candidate_ids = self._ordered_candidate_ids(artifact)
        if not candidate_ids:
            return candidate_context_fingerprint(artifact.sequence, ())
        candidates = {
            candidate.id: candidate
            for candidate in self._session.scalars(
                select(AtomicClaimCandidate).where(
                    AtomicClaimCandidate.id.in_(candidate_ids)
                )
            )
        }
        if set(candidates) != set(candidate_ids):
            raise ConflictError("current claim candidates are missing")
        latest_reviews: dict[uuid.UUID, AtomicClaimReview] = {}
        for review in self._session.scalars(
            select(AtomicClaimReview)
            .where(AtomicClaimReview.atomic_claim_candidate_id.in_(candidate_ids))
            .order_by(
                AtomicClaimReview.atomic_claim_candidate_id,
                AtomicClaimReview.created_at.desc(),
                AtomicClaimReview.id.desc(),
            )
        ):
            latest_reviews.setdefault(review.atomic_claim_candidate_id, review)
        decisions: list[tuple[str, str, str, str | None, str | None]] = []
        for candidate_id in candidate_ids:
            review = latest_reviews.get(candidate_id)
            if review is None:
                continue
            candidate = candidates[candidate_id]
            normalized_text: str | None = None
            if review.outcome == "confirmed":
                normalized_text = candidate.normalized_text
            elif review.outcome == "modified":
                statement = self._session.get(
                    SourceStatement, review.published_source_statement_id
                )
                if (
                    statement is None
                    or statement.atomic_claim_candidate_id != candidate.id
                    or statement.source_span_id != candidate.source_span_id
                    or not statement.normalized_text.strip()
                ):
                    raise ConflictError("modified claim context is unavailable")
                normalized_text = statement.normalized_text
            decisions.append((
                str(candidate_id),
                str(review.id),
                review.outcome,
                str(review.published_source_statement_id)
                if review.published_source_statement_id else None,
                hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
                if normalized_text is not None else None,
            ))
        return candidate_context_fingerprint(artifact.sequence, tuple(decisions))

    def mark_step_failed(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        *,
        error_code: PreparationFailureCode,
        retry_at: datetime | None,
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        self._require_eligible_step(preparation, step, allowed_states={"queued", "running"})
        # Provider adapters pass a semantic category, never exception text.
        # Keep the durable projection deliberately coarser so credentials,
        # endpoint URLs, and provider response bodies cannot enter activity.
        if error_code not in _PREPARATION_FAILURE_CODES:
            raise ConflictError("preparation failure code is invalid")
        safe_error_code = _PERSISTED_PROVIDER_FAILURE
        setattr(preparation, _STEP_FIELDS[step], "retrying" if retry_at else "failed")
        preparation.next_attempt_at = retry_at
        preparation.last_error_code = safe_error_code
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_step_failed",
            step=step,
            message="preparation system step failed",
            detail={"error_code": safe_error_code, "retry_scheduled": retry_at is not None},
        )
        return preparation

    def mark_step_internal_failure(
        self, case_id: uuid.UUID, step: PreparationStep
    ) -> ResearchPreparation:
        """Finish an owned unexpected worker failure without exposing details."""
        preparation = self._require_preparation(case_id)
        self._require_eligible_step(preparation, step, allowed_states={"running"})
        setattr(preparation, _STEP_FIELDS[step], "failed")
        preparation.next_attempt_at = None
        preparation.last_error_code = "preparation_internal_error"
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_step_internal_failed",
            step=step,
            message="preparation system step failed",
            detail={"error_code": "preparation_internal_error"},
        )
        return preparation

    def mark_backfill_candidate_limit(
        self, case_id: uuid.UUID
    ) -> ResearchPreparation:
        """Stop an oversized historical reuse without invoking a provider."""
        preparation = self._require_preparation(case_id)
        self._require_eligible_step(preparation, "parse_claims", allowed_states={"queued"})
        preparation.parse_claims_state = "failed"
        preparation.next_attempt_at = None
        preparation.last_error_code = "preparation_backfill_candidate_limit"
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_backfill_candidate_limit",
            step="parse_claims",
            message="preparation backfill candidate limit exceeded",
            detail={
                "error_code": "preparation_backfill_candidate_limit",
                "candidate_limit": MAX_CANDIDATES,
            },
        )
        return preparation

    def start_system_step(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        *,
        expected_version: int,
        expected_fingerprint: str,
    ) -> ResearchPreparation:
        """Mark a freshly claimed worker step running under the Case lock."""
        preparation = self._require_preparation(case_id)
        if (
            preparation.version != expected_version
            or preparation.input_fingerprint != expected_fingerprint
        ):
            raise ConflictError("preparation job is stale")
        # A stale worker is recovered by re-queuing its *same* Job.  The
        # projection may still say running, so accepting that state here is
        # the narrow, idempotent recovery path (the Job claim itself remains
        # the concurrency authority).
        self._require_eligible_step(preparation, step, allowed_states={"queued", "retrying", "running"})
        setattr(preparation, _STEP_FIELDS[step], "running")
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_step_started",
            step=step,
            message="preparation system step started",
            detail={},
        )
        return preparation

    def reuse_existing_claim_candidates(
        self,
        case_id: uuid.UUID,
        *,
        candidate_ids: list[uuid.UUID],
        every_candidate_reviewed: bool,
    ) -> ResearchPreparation:
        """Attach already-admitted candidates without re-running an LLM.

        Backfill calls this immediately after ``create_for_case``.  It keeps
        candidate ledger rows immutable and deliberately does *not* queue the
        protocol step: a person must still advance the preparation explicitly.
        """
        preparation = self._require_preparation(case_id)
        if preparation.parse_claims_state != "queued":
            raise ConflictError("claim candidates cannot be reused for this preparation")
        payload = {"candidates": [{"candidate_id": str(candidate_id)} for candidate_id in candidate_ids]}
        preparation = self.complete_system_step(
            case_id,
            "parse_claims",
            payload,
            expected_version=preparation.version,
            expected_fingerprint=preparation.input_fingerprint,
        )
        if every_candidate_reviewed:
            preparation.claim_review_state = "confirmed"
            self._set_aggregate_status(preparation)
            preparation.updated_at = _utcnow()
            self._repo.append_event(
                preparation,
                research_case_id=case_id,
                type="preparation_claim_candidates_reused",
                step="parse_claims",
                message="existing reviewed claim candidates attached",
                detail={"candidate_count": len(candidate_ids)},
            )
            self._repo.queue_step_job(
                preparation, research_case_id=case_id, step="draft_protocol"
            )
        return preparation

    def record_worker_output_discarded(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        *,
        job_id: uuid.UUID,
        reason: Literal[
            "version_changed",
            "input_changed",
            "candidate_context_changed",
            "cancelled",
            "step_no_longer_eligible",
        ],
    ) -> ResearchPreparation:
        """Audit a worker output that lost its guarded output slot."""
        preparation = self._require_preparation(case_id)
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_output_discarded",
            step=step,
            message="stale preparation output discarded",
            detail={"job_id": str(job_id), "step": step, "reason": reason},
        )
        return preparation

    def confirm_claims(
        self,
        case_id: uuid.UUID,
        *,
        actor: str,
        revision: int,
        decisions: list[ClaimDecision],
    ) -> ResearchPreparation:
        preparation = self._repo.preparation_for_case(case_id)
        if preparation is None:
            raise NotFoundError(f"research preparation for case {case_id} not found")
        self._require_revision(preparation, revision)
        if preparation.claim_review_state != "awaiting_review":
            raise ConflictError("claim review is not awaiting review")
        artifact = self._repo.current_artifact(preparation.id, "atomic_claim_candidates")
        if artifact is None:
            raise ConflictError("current claim candidates are missing")
        candidate_ids = self._candidate_ids(artifact)
        self._validate_claim_decisions(
            actor=actor, candidate_ids=candidate_ids, decisions=decisions
        )
        persisted_candidate_ids = set(
            self._session.scalars(
                select(AtomicClaimCandidate.id).where(
                    AtomicClaimCandidate.id.in_(candidate_ids)
                )
            )
        )
        if persisted_candidate_ids != candidate_ids:
            raise ConflictError("current claim candidates are missing")
        locked_candidate_ids = {
            candidate.id for candidate in self._repo.lock_candidate_rows(candidate_ids)
        }
        if locked_candidate_ids != candidate_ids:
            raise ConflictError("current claim candidates are missing")

        # Candidate locks are acquired before the Case lock. Recheck all
        # mutable preparation state after taking the normal Case → prep lock.
        preparation = self._require_preparation(case_id)
        self._require_revision(preparation, revision)
        if preparation.claim_review_state != "awaiting_review":
            raise ConflictError("claim review is not awaiting review")
        artifact = self._repo.current_artifact(preparation.id, "atomic_claim_candidates")
        if artifact is None or self._candidate_ids(artifact) != candidate_ids:
            raise ConflictError("current claim candidates are missing")
        self._validate_claim_decisions(
            actor=actor, candidate_ids=candidate_ids, decisions=decisions
        )
        for decision in decisions:
            self._claims.review(
                decision.candidate_id,
                outcome=decision.outcome,
                reviewer=actor,
                reason=decision.reason,
                normalized_text=decision.normalized_text,
                idempotency_key=self._claim_review_idempotency_key(decision),
                preparation_locking="already_locked",
            )
        modified_count = sum(decision.outcome == "modified" for decision in decisions)
        rejected_count = sum(decision.outcome == "rejected" for decision in decisions)
        preparation.claim_review_state = "confirmed"
        if modified_count or rejected_count:
            preparation.version += 1
            self._invalidate_downstream(preparation, reason="claim_decisions_changed")
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(
            preparation, research_case_id=case_id, step="draft_protocol"
        )
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_claims_confirmed",
            step="parse_claims",
            message="claim review confirmed",
            detail={
                "confirmed_count": len(decisions) - modified_count - rejected_count,
                "modified_count": modified_count,
                "rejected_count": rejected_count,
            },
        )
        return preparation

    @staticmethod
    def _claim_review_idempotency_key(decision: ClaimDecision) -> str:
        """Deduplicate the same global candidate decision across preparations."""
        normalized_text = (decision.normalized_text or "").strip()
        decision_hash = hashlib.sha256(
            f"{decision.outcome}\n{normalized_text}".encode("utf-8")
        ).hexdigest()
        return f"preparation:claim:{decision.candidate_id}:{decision_hash}"

    def confirm_protocol(
        self,
        case_id: uuid.UUID,
        *,
        actor: str,
        revision: int,
        payload: ProtocolConfirmation,
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        self._require_revision(preparation, revision)
        if preparation.protocol_review_state != "awaiting_review":
            raise ConflictError("protocol review is not awaiting review")
        artifact = self._repo.current_artifact(preparation.id, "research_protocol_draft")
        if artifact is None or artifact.sequence != payload.draft_sequence:
            raise ConflictError("protocol draft revision is stale")
        if (
            artifact.context_fingerprint is None
            or artifact.context_fingerprint
            != self.current_candidate_context_fingerprint(case_id)
        ):
            self._invalidate_protocol_context(preparation, artifact, case_id)
            raise ConflictError("protocol draft candidate context changed; refresh required")
        self._validate_protocol_edits(artifact.payload, payload.edits)
        confirmed_draft_sequence = artifact.sequence
        if payload.edits:
            merged_payload = self._merge_protocol_edits(
                artifact.payload, payload.edits
            )
            successor = self._repo.append_artifact(
                preparation,
                research_case_id=case_id,
                kind="research_protocol_draft",
                input_fingerprint=preparation.input_fingerprint,
                payload=merged_payload,
                context_fingerprint=artifact.context_fingerprint,
            )
            confirmed_draft_sequence = successor.sequence
        preparation.protocol_review_state = "confirmed"
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(
            preparation, research_case_id=case_id, step="draft_evidence_plan"
        )
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_protocol_confirmed",
            step="draft_protocol",
            message="protocol review confirmed",
            detail={
                "source_draft_sequence": artifact.sequence,
                "confirmed_draft_sequence": confirmed_draft_sequence,
                "edit_count": len(payload.edits),
            },
        )
        return preparation

    def _invalidate_protocol_context(
        self,
        preparation: ResearchPreparation,
        artifact: ResearchPreparationArtifact,
        case_id: uuid.UUID,
    ) -> None:
        """Durably invalidate a draft whose reviewed claims have changed.

        ``confirm_protocol`` must return a 409 for this conflict.  Normal API
        request cleanup rolls back a session after that exception, so this
        narrow boundary commits the already-validated invalidation, retry job,
        and audit event before the caller raises.  Other confirmation failures
        intentionally retain normal unit-of-work rollback semantics.
        """
        artifact.state = "stale"
        artifact.invalidated_reason = "candidate_context_changed"
        preparation.draft_protocol_state = "stale"
        preparation.protocol_review_state = "locked"
        preparation.draft_evidence_plan_state = "stale"
        preparation.plan_review_state = "locked"
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(
            preparation, research_case_id=case_id, step="draft_protocol"
        )
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_protocol_context_stale",
            step="draft_protocol",
            message="protocol draft invalidated by changed claim context",
            detail={"source_draft_sequence": artifact.sequence},
        )
        self._session.commit()

    def retry_failed_step(
        self, case_id: uuid.UUID, *, actor: str, revision: int
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        self._require_revision(preparation, revision)
        failed_step = next(
            (
                step
                for step, field in _STEP_FIELDS.items()
                if getattr(preparation, field) == "failed"
            ),
            None,
        )
        if failed_step is None:
            raise ConflictError("no failed preparation step to retry")
        setattr(preparation, _STEP_FIELDS[failed_step], "queued")
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.status = "preparing"
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(
            preparation, research_case_id=case_id, step=failed_step
        )
        self._repo.append_event(
            preparation,
            research_case_id=case_id,
            type="preparation_step_retried",
            step=failed_step,
            message="preparation step requeued",
            detail={"actor": actor},
        )
        return preparation

    def _lock(
        self, case_id: uuid.UUID, *, case_locked: bool = False
    ) -> ResearchPreparation | None:
        if case_locked:
            return self._repo.lock_for_case(case_id, case_locked=True)
        return self._repo.lock_for_case(case_id)

    def _require_preparation(self, case_id: uuid.UUID) -> ResearchPreparation:
        preparation = self._lock(case_id)
        if preparation is None:
            raise NotFoundError(f"research preparation for case {case_id} not found")
        return preparation

    def _require_revision(self, preparation: ResearchPreparation, revision: int) -> None:
        if preparation.version != revision:
            raise ConflictError("preparation revision is stale")

    def _require_eligible_step(
        self,
        preparation: ResearchPreparation,
        step: PreparationStep,
        *,
        allowed_states: set[str],
    ) -> None:
        if step not in _STEP_FIELDS:
            raise ConflictError("unknown preparation step")
        if getattr(preparation, _STEP_FIELDS[step]) not in allowed_states:
            raise ConflictError(f"preparation step {step} is not eligible")
        if step == "draft_protocol" and preparation.claim_review_state != "confirmed":
            raise ConflictError("protocol drafting requires confirmed claims")
        if step == "draft_evidence_plan" and preparation.protocol_review_state != "confirmed":
            raise ConflictError("evidence planning requires confirmed protocol")

    def _invalidate_current_artifacts(
        self, preparation: ResearchPreparation, *, reason: str
    ) -> None:
        artifacts = self._session.scalars(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.state == "current",
            )
            .with_for_update()
        )
        for artifact in artifacts:
            artifact.state = "stale"
            artifact.invalidated_reason = reason

    def _invalidate_downstream(
        self, preparation: ResearchPreparation, *, reason: str
    ) -> None:
        artifacts = self._session.scalars(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind.in_(
                    ("research_protocol_draft", "evidence_acquisition_plan")
                ),
                ResearchPreparationArtifact.state == "current",
            )
            .with_for_update()
        )
        for artifact in artifacts:
            artifact.state = "stale"
            artifact.invalidated_reason = reason
        # A new protocol must be produced for this preparation version; the
        # plan remains stale until its predecessor has been re-confirmed.
        preparation.draft_protocol_state = "queued"
        preparation.draft_evidence_plan_state = "stale"
        preparation.protocol_review_state = "locked"
        preparation.plan_review_state = "locked"

    def _candidate_ids(self, artifact: ResearchPreparationArtifact) -> set[uuid.UUID]:
        return set(self._ordered_candidate_ids(artifact))

    def _ordered_candidate_ids(
        self, artifact: ResearchPreparationArtifact
    ) -> list[uuid.UUID]:
        candidates = artifact.payload.get("candidates")
        if not isinstance(candidates, list):
            raise ConflictError("claim candidate artifact is malformed")
        ids: list[uuid.UUID] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("candidate_id"), str):
                raise ConflictError("claim candidate artifact is malformed")
            try:
                candidate_id = uuid.UUID(candidate["candidate_id"])
            except ValueError as exc:
                raise ConflictError("claim candidate artifact is malformed") from exc
            if candidate_id in ids:
                raise ConflictError("claim candidate artifact has duplicate candidates")
            ids.append(candidate_id)
        return ids

    def _validate_claim_decisions(
        self,
        *,
        actor: str,
        candidate_ids: set[uuid.UUID],
        decisions: list[ClaimDecision],
    ) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise ConflictError("claim reviewer is invalid")
        decision_ids = [decision.candidate_id for decision in decisions]
        if any(not isinstance(candidate_id, uuid.UUID) for candidate_id in decision_ids):
            raise ConflictError("claim decision candidate is invalid")
        if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != candidate_ids:
            raise ConflictError("claim decisions must cover every current candidate exactly once")
        for decision in decisions:
            if decision.outcome not in _CLAIM_OUTCOMES:
                raise ConflictError("claim decision outcome is invalid")
            if not isinstance(decision.reason, str) or not decision.reason.strip():
                raise ConflictError("claim decision reason is invalid")
            if decision.outcome == "modified":
                if not isinstance(decision.normalized_text, str) or not decision.normalized_text.strip():
                    raise ConflictError("modified claim requires normalized text")
            elif decision.normalized_text is not None:
                raise ConflictError("only modified claim may provide normalized text")

    def _validate_protocol_edits(
        self, source_payload: object, edits: object
    ) -> None:
        if not isinstance(source_payload, dict) or not isinstance(edits, dict):
            raise ValidationError("protocol edits must be an object")

        def validate_object(
            current: dict[str, object], patch: dict[object, object], *, top_level: bool
        ) -> None:
            for key, value in patch.items():
                if not isinstance(key, str):
                    raise ValidationError("protocol edit keys must be strings")
                if top_level and key not in _EDITABLE_PROTOCOL_KEYS:
                    raise ValidationError("protocol edit key is not allowed")
                if key not in current:
                    raise ValidationError("protocol edit does not match draft structure")
                if isinstance(value, dict):
                    existing = current[key]
                    if not isinstance(existing, dict):
                        raise ValidationError("protocol edit changes object structure")
                    validate_object(existing, value, top_level=False)

        validate_object(source_payload, edits, top_level=True)

    def _merge_protocol_edits(
        self, source_payload: dict[str, object], edits: dict[str, object]
    ) -> dict[str, object]:
        merged = copy.deepcopy(source_payload)

        def overlay(target: dict[str, object], patch: dict[str, object]) -> None:
            for key in sorted(patch):
                incoming = copy.deepcopy(patch[key])
                previous = target.get(key)
                if isinstance(previous, dict) and isinstance(incoming, dict):
                    overlay(previous, incoming)
                elif key not in target or previous != incoming:
                    target[key] = incoming

        overlay(merged, edits)
        return merged

    def _set_aggregate_status(self, preparation: ResearchPreparation) -> None:
        if any(
            getattr(preparation, field) == "failed"
            for field in _STEP_FIELDS.values()
        ) and preparation.next_attempt_at is None:
            preparation.status = "recoverable_failure"
        elif preparation.claim_review_state == "awaiting_review":
            preparation.status = "awaiting_claim_review"
        elif preparation.protocol_review_state == "awaiting_review":
            preparation.status = "awaiting_protocol_confirmation"
        elif preparation.plan_review_state == "awaiting_review":
            preparation.status = "awaiting_plan_authorization"
        else:
            preparation.status = "preparing"
