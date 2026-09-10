"""Native intake and durable, safe Gateway execution-stage projection.

These four keys describe engine work, not professional analyst identities.
No provider trace, source body, or task result is copied to Gateway events.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
from app.models.acquisition import AcquisitionJob, AcquisitionJobEvent
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_gateway import (
    ROLE_KEYS,
    ArtifactReference,
    ResearchConversation,
    ResearchRunSpec,
    RoleEvent,
    RoleRun,
    SafeRoleEventReason,
    canonical_role_event_source_key,
)
from app.models.research_monitor import ResearchRunEvent
from app.repositories.research_gateway import ResearchGatewayRepository
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.automatic_research_scope import (
    AutomaticResearchScopeError,
    load_automatic_research_scope,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.services.research_gateway_artifacts import (
    native_artifacts,
    validated_gateway_source_bindings,
)


@dataclass(frozen=True, slots=True)
class NativeRunRef:
    case_id: uuid.UUID
    research_run_id: uuid.UUID


class NativeResearchRuntime(Protocol):
    def start(self, *, text: str, tenant_id: str, actor_subject_id: str,
              commit: bool) -> NativeRunRef: ...


class AutomaticResearchRuntime:
    def __init__(self, session: Session, *, extractor=None) -> None:
        self._intake = AutomaticResearchIntakeService(session, extractor=extractor)

    def start(self, *, text: str, tenant_id: str, actor_subject_id: str,
              commit: bool) -> NativeRunRef:
        started = self._intake.start(
            text, tenant_id=tenant_id, actor_subject_id=actor_subject_id, commit=commit,
        )
        return NativeRunRef(uuid.UUID(started.case_id), uuid.UUID(started.run_id))


_STAGE_ROLE = {
    "scope": "scope_identity", "planning": "scope_identity",
    "retrieve": "sources_evidence", "waiting_for_sources": "sources_evidence",
    "acquire": "sources_evidence", "analyze": "analysis_counter_evidence",
    "assessing": "analysis_counter_evidence", "conclude": "compilation_checks",
    "complete": "compilation_checks", "failed": "compilation_checks",
}
_ACTIVE = frozenset({"running", "waiting_for_sources"})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class GatewayAutomaticResearchAdapter:
    """One replayable projector; caller owns its short write transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ResearchGatewayRepository(session)

    def sync_native_run(self, native_run_id: uuid.UUID) -> None:
        for spec_id in self._session.scalars(
            select(ResearchRunSpec.id).where(ResearchRunSpec.native_run_id == native_run_id)
        ):
            self.sync(run_spec_id=spec_id)

    def sync_conversation(self, conversation_id: uuid.UUID) -> None:
        for spec_id in self._session.scalars(
            select(ResearchRunSpec.id)
            .where(ResearchRunSpec.conversation_id == conversation_id)
            .order_by(ResearchRunSpec.created_at, ResearchRunSpec.id)
        ):
            self.sync(run_spec_id=spec_id)

    def sync(self, *, run_spec_id: uuid.UUID) -> None:
        spec = self._session.get(ResearchRunSpec, run_spec_id)
        if spec is None:
            raise NotFoundError("research conversation not found")
        # Serialize projector snapshots before reading mutable native state.
        # Otherwise an older reader could acquire the event lock after a newer
        # reader and regress a RoleRun. The no-op UPDATE also locks on SQLite,
        # without making an unchanged polling conversation look recently edited.
        self._session.execute(update(ResearchConversation)
            .where(ResearchConversation.id == spec.conversation_id)
            .values(next_event_sequence=ResearchConversation.next_event_sequence))
        self._session.get(ResearchConversation, spec.conversation_id, populate_existing=True)
        CaseTenantAccess(self._session).require_case(spec.native_case_id, spec.tenant_id)
        run = self._session.get(ResearchRun, spec.native_run_id, populate_existing=True)
        if run is None or run.research_case_id != spec.native_case_id:
            raise NotFoundError("research conversation not found")
        try:
            scope = load_automatic_research_scope(self._session, run)
        except AutomaticResearchScopeError as exc:
            raise ConflictError("research scope is unavailable") from exc
        if scope.snapshot() != spec.frozen_scope:
            raise ConflictError("research scope differs from frozen Gateway scope")
        existing_roles = set(self._session.scalars(select(RoleRun.role_key).where(RoleRun.run_spec_id == spec.id)))
        if existing_roles != set(ROLE_KEYS):
            self._repo.initialize_role_runs(run_spec_id=spec.id)
        # The conversation writer lock above makes this one transaction-local
        # de-duplication snapshot safe across API polls and worker catch-up.
        # An existing append is a no-op in the repository; avoid repeating its
        # lock/savepoint queries once for every historical native event. This
        # set is never reused by a later poll and does not cache authorization.
        projected_sources = set(self._session.scalars(select(RoleEvent.source_key)
            .where(RoleEvent.run_spec_id == spec.id)))

        def append_once(*, source_key: str, **values) -> None:
            stored_key = canonical_role_event_source_key(source_key)
            if stored_key not in projected_sources:
                self._repo.append_role_event(source_key=source_key, **values)
                projected_sources.add(stored_key)

        events = list(self._session.scalars(
            select(ResearchRunEvent).where(ResearchRunEvent.run_id == run.id)
            .order_by(ResearchRunEvent.seq, ResearchRunEvent.id)
        ))
        for event in events:
            key = _STAGE_ROLE.get(event.stage)
            if key is None or event.stage in {"failed", "complete"}:
                # Terminal state is validated below, not inferred from a message.
                continue
            kind = "role_completed" if event.stage == "scope" else (
                "source_waiting" if event.status in {"waiting", "waiting_for_sources"}
                else "role_progress"
            )
            append_once(
                run_spec_id=spec.id, role_key=key, event_type=kind,
                source_key=f"native:run-event:{event.id}",
                reason_code=(SafeRoleEventReason.SOURCE_UNAVAILABLE if kind == "source_waiting" else None),
            )
        tasks = list(self._session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.research_case_id == run.research_case_id,
                ResearchTask.thesis_id.in_(scope.factor_ids),
            ).order_by(ResearchTask.created_at, ResearchTask.id).execution_options(populate_existing=True)
        ))
        source_jobs: list[AcquisitionJob] = []
        source_events: list[AcquisitionJobEvent] = []
        if run.round >= 1:
            list(self._session.scalars(select(AcquisitionJob)
                .where(AcquisitionJob.research_run_id == run.id)
                .execution_options(populate_existing=True)))
            try:
                bindings = validated_gateway_source_bindings(self._session, spec, run, scope)
            except ValueError:
                bindings = None
            if bindings is not None:
                source_jobs = sorted(bindings.jobs_by_task_id.values(), key=lambda job: str(job.id))
                source_events = list(self._session.scalars(select(AcquisitionJobEvent)
                    .where(AcquisitionJobEvent.job_id.in_(bindings.job_ids))
                    .order_by(AcquisitionJobEvent.created_at, AcquisitionJobEvent.id)))
                role = self._session.scalar(select(RoleRun).where(
                    RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence",
                ))
                for event in source_events:
                    append_once(
                        run_spec_id=spec.id, role_key="sources_evidence", event_type="role_progress",
                        source_key=f"native:acquisition-event:{event.id}", status=role.status,
                    )
        artifacts = native_artifacts(self._session, spec)
        for ref in artifacts:
            if ref.kind not in {"evidence_link", "draft"}:
                continue
            key = "sources_evidence" if ref.kind == "evidence_link" else "compilation_checks"
            current_role = self._session.scalar(select(RoleRun).where(
                RoleRun.run_spec_id == spec.id, RoleRun.role_key == key,
            ))
            append_once(
                run_spec_id=spec.id, role_key=key,
                event_type="evidence_available" if ref.kind == "evidence_link" else "draft_ref",
                status=current_role.status,
                source_key=f"native:artifact:{run.id}:{ref.kind}:{ref.id}", artifact_refs=[ref],
            )
        states = self._states(run, tasks, source_jobs,
                              verified_draft=any(ref.kind == "draft" for ref in artifacts))
        # State fingerprint includes native revision, so waiting->running->waiting
        # remains observable while repeated snapshot/restart sync is idempotent.
        revision = hashlib.sha256(json.dumps({
            "run": [run.status, run.stage, _as_utc(run.updated_at).isoformat(), run.round],
            "events": [str(e.id) for e in events],
            "tasks": [[str(t.id), t.status, t.stage, _as_utc(t.updated_at).isoformat()] for t in tasks],
            "source_jobs": [[str(j.id), j.status, j.stage, _as_utc(j.updated_at).isoformat()] for j in source_jobs],
            "source_events": [str(e.id) for e in source_events],
        }, sort_keys=True).encode()).hexdigest()
        for key, state, reason in states:
            kind = {"queued": "role_queued", "running": "role_progress",
                    "blocked": "role_blocked", "completed": "role_completed",
                    "failed": "role_failed", "cancelled": "role_failed"}[state]
            append_once(
                run_spec_id=spec.id, role_key=key, event_type=kind,
                status=state, reason_code=reason,
                source_key=f"native:state:{run.id}:{key}:{revision}:{state}",
                artifact_refs=([ArtifactReference(
                    kind="research_run", id=run.id, case_id=run.research_case_id,
                )] if key == "scope_identity" else []),
            )

    def _states(self, run: ResearchRun, tasks: list[ResearchTask], source_jobs: list[AcquisitionJob],
                *, verified_draft: bool):
        states = {key: ("queued", None) for key in ROLE_KEYS}
        states["scope_identity"] = ("completed", None)
        if run.status in {"failed", "cancelled"}:
            terminal = "failed" if run.status == "failed" else "cancelled"
            for key in ROLE_KEYS[1:]:
                states[key] = (terminal, SafeRoleEventReason.NATIVE_EXECUTION_FAILED)
        elif run.status in _ACTIVE or run.status == "succeeded":
            states["sources_evidence"] = (
                ("blocked", SafeRoleEventReason.SOURCE_UNAVAILABLE)
                if run.status == "waiting_for_sources" else ("running", None)
            )
            if run.stage in {"analyze", "assessing", "conclude", "complete"}:
                states["analysis_counter_evidence"] = ("running", None)
            if run.stage in {"conclude", "complete"}:
                states["compilation_checks"] = ("running", None)
            # waiting_for_sources is native orchestration, not necessarily a
            # blocked acquisition. Show the actual authorized worker state.
            if source_jobs:
                job_states = {job.status for job in source_jobs}
                if "running" in job_states:
                    states["sources_evidence"] = ("running", None)
                elif "queued" in job_states:
                    states["sources_evidence"] = ("queued", None)
                elif "retry_wait" in job_states:
                    states["sources_evidence"] = ("blocked", SafeRoleEventReason.SOURCE_UNAVAILABLE)
                elif job_states <= {"succeeded", "partial", "failed", "cancelled"}:
                    states["sources_evidence"] = ("completed", None)
        if (any(t.task_type == "result" and t.status == "running" for t in tasks)
                and run.status not in {"failed", "cancelled"}):
            states["analysis_counter_evidence"] = ("running", None)
        # A native success marker alone is not a validated research draft.
        if run.status == "succeeded":
            if verified_draft:
                for key in ROLE_KEYS[1:]:
                    states[key] = ("completed", None)
            else:
                states["compilation_checks"] = ("blocked", SafeRoleEventReason.VALIDATION_FAILED)
        return [(key, *states[key]) for key in ROLE_KEYS]
