"""Durable bounded dispatch; stable Gateway keys recover the commit/receipt gap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update

from app.ai.llm_config import bounded_number
from app.api.v1.tenant_context import ResearchActor
from app.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.company_study import CompanyStudy, CompanyStudyActivity, utcnow
from app.services.company_study import (
    SHANGHAI,
    CompanyStudyService,
    aware,
    next_monitor_due,
)
from app.services.research_gateway import ResearchGateway


@dataclass(frozen=True)
class ActivityClaim:
    activity_id: UUID
    token: UUID


def activity_intent_key(activity_id):
    return f"company-study-activity:{activity_id}"


class CompanyStudyWorker:
    def __init__(
        self,
        session_factory,
        *,
        now=utcnow,
        lease_seconds=300,
        gateway_factory=ResearchGateway,
    ):
        bounded_number("lease_seconds", lease_seconds, 1, 3600)
        self.sessions = session_factory
        self.now = now
        self.lease_seconds = lease_seconds
        self.gateway_factory = gateway_factory

    def claim_next(self):
        now = self.now()
        eligible = or_(
            CompanyStudyActivity.status == "queued",
            and_(
                CompanyStudyActivity.status == "starting",
                CompanyStudyActivity.lease_expires_at <= now,
            ),
        )
        with self.sessions() as session:
            # Repeated process crashes do not retry providers forever.
            session.execute(
                update(CompanyStudyActivity)
                .where(
                    eligible,
                    CompanyStudyActivity.attempt >= 5,
                    CompanyStudyActivity.run_spec_id.is_(None),
                )
                .values(
                    status="failed",
                    error_code="research_start_retry_exhausted",
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            identifiers = list(
                session.scalars(
                    select(CompanyStudyActivity.id)
                    .where(
                        eligible,
                        CompanyStudyActivity.run_spec_id.is_(None),
                        CompanyStudyActivity.attempt < 5,
                    )
                    .order_by(CompanyStudyActivity.created_at, CompanyStudyActivity.id)
                    .limit(20)
                )
            )
            for identifier in identifiers:
                token = uuid4()
                changed = session.execute(
                    update(CompanyStudyActivity)
                    .where(
                        CompanyStudyActivity.id == identifier,
                        eligible,
                        CompanyStudyActivity.run_spec_id.is_(None),
                        CompanyStudyActivity.attempt < 5,
                    )
                    .values(
                        status="starting",
                        error_code=None,
                        lease_token=token,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        attempt=CompanyStudyActivity.attempt + 1,
                        updated_at=now,
                    )
                )
                if changed.rowcount == 1:
                    session.commit()
                    return ActivityClaim(identifier, token)
            session.commit()
        return None

    def _owned(self, claim):
        return (
            CompanyStudyActivity.id == claim.activity_id,
            CompanyStudyActivity.status == "starting",
            CompanyStudyActivity.lease_token == claim.token,
            CompanyStudyActivity.lease_expires_at > self.now(),
        )

    def run_claim(self, claim):
        with self.sessions() as session:
            activity = session.scalar(
                select(CompanyStudyActivity).where(*self._owned(claim))
            )
            if activity is None:
                return False
            study = session.get(CompanyStudy, activity.study_id)
            actor = ResearchActor(study.tenant_id, frozenset(), study.subject_id)
            prompt = activity.prompt
            context_revision = activity.context_revision
            study_id = study.id
            session.rollback()
            try:
                service = CompanyStudyService(session, now=self.now)
                study = service.require(actor, study_id)
                if prompt is None:
                    activity = session.get(CompanyStudyActivity, claim.activity_id)
                    prompt = service.research_prompt(
                        study,
                        kind=activity.kind,
                        text=activity.text,
                        context_revision=context_revision,
                    )
                    changed = session.execute(
                        update(CompanyStudyActivity)
                        .where(
                            *self._owned(claim), CompanyStudyActivity.prompt.is_(None)
                        )
                        .values(prompt=prompt)
                    )
                    if changed.rowcount != 1:
                        session.rollback()
                        return False
                    session.commit()
                else:
                    service.adopted_context(study, context_revision)
                    session.rollback()
                receipt = self.gateway_factory(session).send_message(
                    actor=actor,
                    conversation_id=None,
                    text=prompt,
                    idempotency_key=activity_intent_key(claim.activity_id),
                )
                conversation_id, run_spec_id = (
                    UUID(str(receipt.conversation_id)),
                    UUID(str(receipt.run_spec_id)),
                )
                # Verify native provenance before saving the link, including on replay.
                spec = ResearchGateway(session).read_run_spec(actor, run_spec_id)
                if spec.conversation_id != conversation_id:
                    raise NotFoundError("research not found")
                changed = session.execute(
                    update(CompanyStudyActivity)
                    .where(*self._owned(claim))
                    .values(
                        status="running",
                        conversation_id=conversation_id,
                        run_spec_id=run_spec_id,
                        error_code=None,
                        lease_token=None,
                        lease_expires_at=None,
                        updated_at=self.now(),
                    )
                )
                session.commit()
                return changed.rowcount == 1
            except Exception as exc:  # noqa: BLE001 -- bounded public failure without provider diagnostics
                session.rollback()
                if isinstance(exc, ConflictError) and str(exc) in {
                    "gateway_request_in_progress",
                    "gateway_request_lease_lost",
                }:
                    from app.models.research_gateway import GatewayIdempotencyRequest

                    request = session.scalar(
                        select(GatewayIdempotencyRequest).where(
                            GatewayIdempotencyRequest.tenant_id == actor.tenant_id,
                            GatewayIdempotencyRequest.subject_id == actor.subject_id,
                            GatewayIdempotencyRequest.operation == "send_message",
                            GatewayIdempotencyRequest.client_key
                            == activity_intent_key(claim.activity_id),
                        )
                    )
                    due = self.now() + timedelta(seconds=30)
                    if request is not None and request.lease_expires_at is not None:
                        due = max(
                            self.now() + timedelta(seconds=1),
                            aware(request.lease_expires_at) + timedelta(seconds=1),
                        )
                    changed = session.execute(
                        update(CompanyStudyActivity)
                        .where(*self._owned(claim))
                        .values(
                            status="starting",
                            error_code="research_start_recovering",
                            lease_token=None,
                            lease_expires_at=due,
                            updated_at=self.now(),
                        )
                    )
                    session.commit()
                    return changed.rowcount == 1
                code = "research_start_unavailable"
                state = "failed"
                if isinstance(exc, (NotFoundError, PermissionDeniedError)):
                    state, code = "blocked", "research_access_unavailable"
                elif isinstance(exc, ConflictError):
                    state, code = "blocked", "research_start_conflict"
                # No provider response, exception text, SQL or secrets enter public state.
                changed = session.execute(
                    update(CompanyStudyActivity)
                    .where(*self._owned(claim))
                    .values(
                        status=state,
                        error_code=code,
                        lease_token=None,
                        lease_expires_at=None,
                        updated_at=self.now(),
                    )
                )
                session.commit()
                return changed.rowcount == 1

    def schedule_due(self):
        now = self.now()
        with self.sessions() as session:
            identifiers = list(
                session.scalars(
                    select(CompanyStudy.id)
                    .where(CompanyStudy.monitor_next_due_at <= now)
                    .order_by(CompanyStudy.monitor_next_due_at, CompanyStudy.id)
                    .limit(50)
                )
            )
        dispatched = 0
        for identifier in identifiers:
            with self.sessions() as session:
                # This writer lock also serializes monitor configuration/pause.
                changed = session.execute(
                    update(CompanyStudy)
                    .where(
                        CompanyStudy.id == identifier,
                        CompanyStudy.monitor_next_due_at <= now,
                    )
                    .values(updated_at=CompanyStudy.updated_at)
                )
                if changed.rowcount != 1:
                    session.rollback()
                    continue
                study = session.get(CompanyStudy, identifier)
                actor = ResearchActor(study.tenant_id, frozenset(), study.subject_id)
                service = CompanyStudyService(session, now=self.now)
                monitor = service.current_monitor(study)
                if monitor is None or monitor.status != "active":
                    study.monitor_next_due_at = None
                    session.commit()
                    continue
                due = aware(study.monitor_next_due_at)
                window = (
                    f"{monitor.frequency}:{due.astimezone(SHANGHAI).date().isoformat()}"
                )
                existing = session.scalar(
                    select(CompanyStudyActivity.id).where(
                        CompanyStudyActivity.study_id == identifier,
                        CompanyStudyActivity.schedule_key == window,
                    )
                )
                outstanding = False
                candidates = list(
                    session.scalars(
                        select(CompanyStudyActivity).where(
                            CompanyStudyActivity.study_id == identifier,
                            CompanyStudyActivity.schedule_key.is_not(None),
                            CompanyStudyActivity.status.not_in(("failed", "completed")),
                        )
                    )
                )
                for activity in candidates:
                    try:
                        state, error, _ = service.execution_state(actor, activity)
                    except (NotFoundError, PermissionDeniedError):
                        state, error = "blocked", "research_access_unavailable"
                    activity.status, activity.error_code = state, error
                    if state in {"queued", "starting", "running", "blocked"}:
                        outstanding = True
                if existing is None and not outstanding:
                    service.enqueue(
                        study,
                        kind="refresh",
                        text=f"监控窗口 {window}。{monitor.focus}",
                        schedule_key=window,
                    )
                    dispatched += 1
                # Skip missed windows instead of producing a backlog after downtime.
                study.monitor_next_due_at = next_monitor_due(now, monitor.frequency)
                session.commit()
        return dispatched

    def run_once(self):
        scheduled = self.schedule_due()
        claim = self.claim_next()
        if claim is None:
            return bool(scheduled)
        self.run_claim(claim)
        return True
