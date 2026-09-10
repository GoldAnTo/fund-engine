"""Case and private Gateway provenance for compatibility operational routes.

Native workers keep using tenant admissions directly. External compatibility
routes additionally respect private Gateway ownership and refuse raw Gateway
operational feeds, which are available only through the safe Gateway projection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor
from app.errors import NotFoundError, ValidationFailedError
from app.models.events import DomainEvent
from app.models.ledger import (
    AIAssessment,
    AtomicClaimCandidate,
    CaseDocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.operational import Job, ResearchRun, TaskItem
from app.models.proposals import Proposal
from app.models.research_gateway import ResearchConversation, ResearchRunSpec
from app.models.research_preparation import ResearchPreparation
from app.services.case_tenant_access import CaseTenantAccess


def parse_operational_uuid(
    value: str | None, *, required: bool = False
) -> uuid.UUID | None:
    if value is None and not required:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationFailedError("invalid operational resource identifier") from exc


class OperationalAccess:
    def __init__(self, session: Session) -> None:
        self._session = session

    def require_case(self, actor: ResearchActor, case_id: uuid.UUID) -> bool:
        """Authorize the Case and return whether Gateway owns its private access."""
        try:
            CaseTenantAccess(self._session).require_case(case_id, actor.tenant_id)
        except NotFoundError as exc:
            raise NotFoundError("operational resource not found") from exc
        if self._session.get(ResearchCase, case_id) is None:
            raise NotFoundError("operational resource not found")
        specs = list(
            self._session.scalars(
                select(ResearchRunSpec).where(ResearchRunSpec.native_case_id == case_id)
            )
        )
        for spec in specs:
            conversation = self._session.get(ResearchConversation, spec.conversation_id)
            native_run = self._session.get(ResearchRun, spec.native_run_id)
            if (
                not actor.subject_id
                or spec.tenant_id != actor.tenant_id
                or spec.subject_id != actor.subject_id
                or conversation is None
                or conversation.tenant_id != actor.tenant_id
                or conversation.owner_subject_id != actor.subject_id
                or conversation.visibility != "private"
                or native_run is None
                or native_run.research_case_id != case_id
            ):
                raise NotFoundError("operational resource not found")
        return bool(specs)

    def require_compatibility_case(
        self, actor: ResearchActor, case_id: uuid.UUID
    ) -> None:
        if self.require_case(actor, case_id):
            raise NotFoundError("operational resource not found")

    def require_job(
        self, actor: ResearchActor, job_id: uuid.UUID, *, summary: bool = False
    ) -> tuple[Job, bool]:
        job = self._session.get(Job, job_id)
        if job is None or job.research_case_id is None:
            raise NotFoundError("job not found")
        try:
            gateway_owned = self.require_case(actor, job.research_case_id)
            if gateway_owned and not summary:
                raise NotFoundError("job not found")
            if job.target_type is not None or job.target_id is not None:
                self.require_reference(
                    job.research_case_id, job.target_type, job.target_id
                )
        except (ValidationFailedError, NotFoundError) as exc:
            raise NotFoundError("job not found") from exc
        return job, gateway_owned

    def require_reference(
        self, case_id: uuid.UUID, ref_type: str | None, ref_id: uuid.UUID | None
    ) -> None:
        if ref_type is None and ref_id is None:
            return
        if ref_type is None or ref_id is None:
            raise ValidationFailedError(
                "operational reference requires type and identifier"
            )
        direct_models = {
            "thesis": Thesis,
            "research_run": ResearchRun,
            "research_preparation": ResearchPreparation,
            "proposal": Proposal,
            "job": Job,
        }
        belongs = False
        if ref_type in {"research_case", "case"}:
            belongs = ref_id == case_id
        elif ref_type in direct_models:
            row = self._session.get(direct_models[ref_type], ref_id)
            belongs = row is not None and row.research_case_id == case_id
        elif ref_type in {
            "ai_assessment",
            "evidence_snapshot",
            "snapshot",
            "evidence_link",
        }:
            if ref_type == "ai_assessment":
                assessment = self._session.get(AIAssessment, ref_id)
                row = (
                    self._session.get(EvidenceSnapshot, assessment.snapshot_id)
                    if assessment
                    else None
                )
            else:
                row = self._session.get(
                    EvidenceLink if ref_type == "evidence_link" else EvidenceSnapshot,
                    ref_id,
                )
            thesis = self._session.get(Thesis, row.thesis_id) if row else None
            belongs = thesis is not None and thesis.research_case_id == case_id
        elif ref_type in {"document_version", "atomic_claim_candidate"}:
            document_id = ref_id
            if ref_type == "atomic_claim_candidate":
                claim = self._session.get(AtomicClaimCandidate, ref_id)
                span = (
                    self._session.get(SourceSpan, claim.source_span_id)
                    if claim
                    else None
                )
                document_id = span.document_version_id if span else None
            belongs = (
                document_id is not None
                and self._session.scalar(
                    select(CaseDocumentVersion.id).where(
                        CaseDocumentVersion.research_case_id == case_id,
                        CaseDocumentVersion.document_version_id == document_id,
                    )
                )
                is not None
            )
        else:
            raise ValidationFailedError("unsupported operational reference type")
        if not belongs:
            raise NotFoundError("operational resource not found")

    def require_task(
        self,
        actor: ResearchActor,
        task_id: uuid.UUID,
        *,
        case_id: uuid.UUID | None = None,
    ) -> TaskItem:
        task = self._session.get(TaskItem, task_id)
        if (
            task is None
            or task.research_case_id is None
            or (case_id is not None and task.research_case_id != case_id)
        ):
            raise NotFoundError("task not found")
        try:
            self.require_compatibility_case(actor, task.research_case_id)
            self.require_reference(task.research_case_id, task.ref_type, task.ref_id)
        except (ValidationFailedError, NotFoundError) as exc:
            raise NotFoundError("task not found") from exc
        return task

    def require_event_cursor(
        self, case_id: uuid.UUID, event_id: uuid.UUID | None
    ) -> None:
        if event_id is None:
            return
        event = self._session.get(DomainEvent, event_id)
        if event is None or event.aggregate_id != str(case_id):
            raise NotFoundError("activity cursor not found")
