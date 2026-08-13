"""In-process command state machine for pre-authorization research work."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.research_preparation import ArtifactKind, PreparationStep
from app.errors import ConflictError, NotFoundError
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
)
from app.repositories.research_preparation import ResearchPreparationRepository
from app.services.atomic_claims import AtomicClaimService
from app.services.event_research_scope_evidence import lock_event_scope_case


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
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


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
                type="preparation_created",
                step=None,
                message="research preparation created",
                detail={"actor": actor},
            )
            self._repo.queue_step_job(preparation, "parse_claims")
            return preparation
        if preparation.input_fingerprint == input_fingerprint:
            return preparation

        preparation.version += 1
        preparation.input_fingerprint = input_fingerprint
        self._invalidate_current_artifacts(preparation, reason="input_changed")
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
            type="preparation_inputs_replaced",
            step=None,
            message="research preparation inputs replaced",
            detail={"actor": actor, "version": preparation.version},
        )
        self._repo.queue_step_job(preparation, "parse_claims")
        return preparation

    def complete_system_step(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        payload: dict[str, object],
        *,
        expected_version: int | None = None,
        expected_fingerprint: str | None = None,
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        if (
            expected_version is not None and expected_version != preparation.version
        ) or (
            expected_fingerprint is not None
            and expected_fingerprint != preparation.input_fingerprint
        ):
            self._repo.append_event(
                preparation,
                type="preparation_output_discarded",
                step=step,
                message="stale preparation output discarded",
                detail={"current_version": preparation.version},
            )
            return preparation
        self._require_eligible_step(preparation, step, allowed_states={"queued", "running", "retrying"})
        artifact = self._repo.append_artifact(
            preparation,
            kind=_STEP_ARTIFACTS[step],
            input_fingerprint=preparation.input_fingerprint,
            payload=payload,
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
            type="preparation_step_completed",
            step=step,
            message="preparation system step completed",
            detail={"artifact_sequence": artifact.sequence},
        )
        return preparation

    def mark_step_failed(
        self,
        case_id: uuid.UUID,
        step: PreparationStep,
        *,
        error_code: str,
        retry_at: datetime | None,
    ) -> ResearchPreparation:
        preparation = self._require_preparation(case_id)
        self._require_eligible_step(preparation, step, allowed_states={"queued", "running"})
        safe_error_code = error_code if _SAFE_ERROR_CODE.fullmatch(error_code) else "preparation_step_failed"
        setattr(preparation, _STEP_FIELDS[step], "retrying" if retry_at else "failed")
        preparation.next_attempt_at = retry_at
        preparation.last_error_code = safe_error_code
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.append_event(
            preparation,
            type="preparation_step_failed",
            step=step,
            message="preparation system step failed",
            detail={"error_code": safe_error_code, "retry_scheduled": retry_at is not None},
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
        preparation = self._require_preparation(case_id)
        self._require_revision(preparation, revision)
        if preparation.claim_review_state != "awaiting_review":
            raise ConflictError("claim review is not awaiting review")
        artifact = self._repo.current_artifact(preparation.id, "atomic_claim_candidates")
        if artifact is None:
            raise ConflictError("current claim candidates are missing")
        candidate_ids = self._candidate_ids(artifact)
        decision_ids = [decision.candidate_id for decision in decisions]
        if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != candidate_ids:
            raise ConflictError("claim decisions must cover every current candidate exactly once")
        for decision in decisions:
            self._claims.review(
                decision.candidate_id,
                outcome=decision.outcome,
                reviewer=actor,
                reason=decision.reason,
                normalized_text=decision.normalized_text,
                idempotency_key=f"preparation:{preparation.id}:{revision}:claim:{decision.candidate_id}",
            )
        modified_count = sum(decision.outcome == "modified" for decision in decisions)
        rejected_count = sum(decision.outcome == "rejected" for decision in decisions)
        preparation.claim_review_state = "confirmed"
        if modified_count or rejected_count:
            preparation.version += 1
            self._invalidate_downstream(preparation, reason="claim_decisions_changed")
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(preparation, "draft_protocol")
        self._repo.append_event(
            preparation,
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
        preparation.protocol_review_state = "confirmed"
        self._set_aggregate_status(preparation)
        preparation.updated_at = _utcnow()
        self._repo.queue_step_job(preparation, "draft_evidence_plan")
        self._repo.append_event(
            preparation,
            type="preparation_protocol_confirmed",
            step="draft_protocol",
            message="protocol review confirmed",
            detail={"actor": actor, "edit_count": len(payload.edits)},
        )
        return preparation

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
        self._repo.queue_step_job(preparation, failed_step)
        self._repo.append_event(
            preparation,
            type="preparation_step_retried",
            step=failed_step,
            message="preparation step requeued",
            detail={"actor": actor},
        )
        return preparation

    def _lock(self, case_id: uuid.UUID) -> ResearchPreparation | None:
        case = lock_event_scope_case(self._session, case_id)
        if case is None:
            raise NotFoundError(f"research case {case_id} not found")
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
        candidates = artifact.payload.get("candidates")
        if not isinstance(candidates, list):
            raise ConflictError("claim candidate artifact is malformed")
        ids: set[uuid.UUID] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("candidate_id"), str):
                raise ConflictError("claim candidate artifact is malformed")
            try:
                candidate_id = uuid.UUID(candidate["candidate_id"])
            except ValueError as exc:
                raise ConflictError("claim candidate artifact is malformed") from exc
            if candidate_id in ids:
                raise ConflictError("claim candidate artifact has duplicate candidates")
            ids.add(candidate_id)
        return ids

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
