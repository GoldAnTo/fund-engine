"""Owner-authorized professional task definitions and lifecycle."""
from __future__ import annotations

import hashlib
import json
import uuid
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models.research_gateway import ResearchRunSpec
from app.models.research_team import (
    PROFESSIONAL_ROLES,
    ProfessionalDependency,
    ProfessionalTask,
    ResearchTeam,
    utcnow,
)


class ResearchTeamService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_team(self, spec: ResearchRunSpec) -> ResearchTeam:
        """Called inside native/Gateway creation's transaction; never commits intake."""
        team = self.session.get(ResearchTeam, spec.id)
        if team is not None:
            return team
        team = ResearchTeam(run_spec_id=spec.id)
        self.session.add(team)
        self.session.flush()
        tasks = {role: ProfessionalTask(run_spec_id=spec.id, revision=1, role=role) for role in PROFESSIONAL_ROLES}
        self.session.add_all(tasks.values())
        self.session.flush()
        for child, parents in {'strategy': ('industry', 'finance'), 'quality': ('industry', 'finance', 'strategy')}.items():
            self.session.add_all(ProfessionalDependency(task_id=tasks[child].id, parent_task_id=tasks[parent].id, run_spec_id=spec.id) for parent in parents)
        self.session.flush()
        return team

    def authorize(self, actor: ResearchActor, conversation_id: UUID, run_spec_id: UUID) -> ResearchRunSpec:
        from app.services.research_gateway import ResearchGateway
        self.session.expire_all()
        spec = ResearchGateway(self.session).read_run_spec(actor, run_spec_id)
        if spec.conversation_id != conversation_id:
            raise NotFoundError('research conversation not found')
        return spec

    def read(self, actor: ResearchActor, conversation_id: UUID, run_spec_id: UUID) -> dict:
        self.authorize(actor, conversation_id, run_spec_id)
        from app.services.research_team_read import read_team
        return read_team(self.session, actor, conversation_id, run_spec_id)

    def _begin_write(self, actor, conversation_id, run_spec_id, *, key, payload, expected_revision, allow_start=False):
        from app.models.research_gateway import ResearchConversation
        from app.models.research_team import ProfessionalRequest
        from app.services.research_gateway import _bounded_text
        _bounded_text(key, name='idempotency key', limit=512)
        spec = self.authorize(actor, conversation_id, run_spec_id)
        # Coordinate writes with both intake and other team commands. Owner
        # authorization is checked again after obtaining the writer lock.
        self.session.execute(update(ResearchConversation).where(ResearchConversation.id == conversation_id).values(updated_at=ResearchConversation.updated_at))
        spec = self.authorize(actor, conversation_id, run_spec_id)
        team = self.session.get(ResearchTeam, run_spec_id)
        if team is not None:
            self.session.execute(update(ResearchTeam).where(ResearchTeam.run_spec_id == run_spec_id).values(updated_at=ResearchTeam.updated_at))
            self.session.refresh(team)
        existing = self.session.scalar(select(ProfessionalRequest).where(ProfessionalRequest.run_spec_id == run_spec_id, ProfessionalRequest.key_sha256 == fingerprint(key)))
        if existing is not None:
            if existing.payload_sha256 != fingerprint(payload):
                raise ConflictError('team_idempotency_payload_conflict')
            return spec, team, dict(existing.receipt)
        if type(expected_revision) is not int or expected_revision != (team.revision if team else 0):
            raise ConflictError('team_revision_changed')
        if team is None:
            if not allow_start:
                raise ConflictError('professional_team_not_started')
            team = self.ensure_team(spec)
        return spec, team, None

    def _receipt(self, actor, spec, team, *, key, payload, kind, task_ids):
        from app.models.research_team import ProfessionalRequest
        request_id = uuid.uuid4()
        receipt = {'request_id': str(request_id), 'conversation_id': str(spec.conversation_id), 'run_spec_id': str(spec.id), 'revision': team.revision, 'task_ids': [str(value) for value in task_ids]}
        self.session.add(ProfessionalRequest(id=request_id, run_spec_id=spec.id, key_sha256=fingerprint(key), payload_sha256=fingerprint(payload), kind=kind, receipt=receipt))
        self.session.commit()
        return receipt

    def _successors(self, team, roles: set[str], instruction: str | None, *, retry_targets: dict[str, ProfessionalTask] | None = None) -> list[ProfessionalTask]:
        current = latest_tasks(self.session, team.run_spec_id)
        affected = set(roles)
        if affected & {'industry', 'finance'}:
            affected.update({'strategy', 'quality'})
        if 'strategy' in affected:
            affected.add('quality')
        team.revision += 1
        team.updated_at = utcnow()
        created = {}
        for role in PROFESSIONAL_ROLES:
            if role not in affected:
                continue
            previous = current[role]
            if previous.status in {'queued', 'running', 'blocked'}:
                previous.status, previous.reason_code = 'cancelled', 'superseded'
                previous.lease_token, previous.lease_expires_at = None, None
                previous.updated_at = utcnow()
            # Preserve prior directed instructions when regenerating affected
            # downstream roles, while a new target instruction stays explicit.
            task = ProfessionalTask(run_spec_id=team.run_spec_id, revision=team.revision, role=role, instruction=instruction if role in roles and instruction is not None else previous.instruction)
            self.session.add(task)
            created[role] = task
        self.session.flush()
        parents = {**current, **created}
        for role, task in created.items():
            for parent_role in {'industry': (), 'finance': (), 'strategy': ('industry', 'finance'), 'quality': ('industry', 'finance', 'strategy')}[role]:
                self.session.add(ProfessionalDependency(task_id=task.id, parent_task_id=parents[parent_role].id, run_spec_id=team.run_spec_id))
            explicit_directed_target = role in roles and instruction is not None
            history_parent_ids = set()
            if explicit_directed_target:
                # A directed question about the prior answer needs that exact
                # immutable answer as input, not just a new prompt with no context.
                if current[role].status == 'succeeded':
                    history_parent_ids.add(current[role].id)
            elif retry_targets is not None and role in retry_targets:
                history_parent_ids.update(same_role_history(self.session, retry_targets[role]))
            else:
                history_parent_ids.update(same_role_history(self.session, current[role]))
            for parent_id in sorted(history_parent_ids):
                self.session.add(ProfessionalDependency(task_id=task.id, parent_task_id=parent_id, run_spec_id=team.run_spec_id))
            append_event(self.session, team, 'task_queued', task=task)
        return list(created.values())

    def message(self, actor, conversation_id, run_spec_id, *, text, recipient, expected_revision, idempotency_key):
        from app.services.research_gateway import _bounded_text
        _bounded_text(text, name='team message', limit=20_000, multiline=True)
        if recipient not in {'team', *PROFESSIONAL_ROLES}:
            raise ValidationFailedError('invalid professional recipient')
        payload = {'kind': 'message', 'text': text, 'recipient': recipient, 'expected_revision': expected_revision}
        try:
            spec, team, receipt = self._begin_write(actor, conversation_id, run_spec_id, key=idempotency_key, payload=payload, expected_revision=expected_revision)
            if receipt is not None:
                self.session.commit()
                return receipt
            if team.status == 'cancelled':
                raise ConflictError('cancelled_research_requires_new_scope')
            roles = set(PROFESSIONAL_ROLES) if recipient == 'team' else {recipient}
            tasks = self._successors(team, roles, text)
            return self._receipt(actor, spec, team, key=idempotency_key, payload=payload, kind='message', task_ids=[task.id for task in tasks])
        except Exception:
            self.session.rollback()
            raise

    def command(self, actor, conversation_id, run_spec_id, *, kind, expected_revision, idempotency_key, task_id=None):
        if kind not in {'start', 'pause', 'resume', 'cancel', 'retry'}:
            raise ValidationFailedError('invalid team command')
        if task_id is not None and kind != 'retry':
            raise ValidationFailedError('only retry accepts a task target')
        payload = {'kind': kind, 'expected_revision': expected_revision, 'task_id': str(task_id) if task_id else None}
        try:
            spec, team, receipt = self._begin_write(actor, conversation_id, run_spec_id, key=idempotency_key, payload=payload, expected_revision=expected_revision, allow_start=kind == 'start')
            if receipt is not None:
                self.session.commit()
                return receipt
            current = latest_tasks(self.session, spec.id)
            changed = list(current.values())
            if kind == 'start':
                if expected_revision != 0:
                    raise ConflictError('professional_team_already_started')
            elif kind == 'pause':
                if team.status == 'cancelled':
                    raise ConflictError('professional_team_cancelled')
                team.status = 'paused'
                for task in current.values():
                    if task.status == 'running':
                        task.status, task.reason_code = 'queued', 'team_paused'
                        task.lease_token, task.lease_expires_at = None, None
                        task.updated_at = utcnow()
            elif kind == 'resume':
                if team.status != 'paused':
                    raise ConflictError('professional_team_not_paused')
                team.status = 'active'
            elif kind == 'cancel':
                from app.models.operational import ResearchRun
                from app.services.auto_research import AutoResearchService
                team.status = 'cancelled'
                for task in current.values():
                    if task.status != 'succeeded':
                        task.status, task.reason_code = 'cancelled', 'user_cancelled'
                        task.lease_token, task.lease_expires_at = None, None
                        task.updated_at = utcnow()
                native = self.session.get(ResearchRun, spec.native_run_id)
                if native.status in {'queued', 'running', 'waiting_for_sources', 'waiting_for_review'}:
                    AutoResearchService(self.session).cancel_run(native.id, actor=f'human:{actor.subject_id}', change_reason='研究员取消本轮研究及专业任务', commit=False)
            else:
                if team.status == 'cancelled':
                    raise ConflictError('cancelled_research_requires_new_scope')
                candidates = [task for task in current.values() if task.status in {'failed', 'blocked'} and (task_id is None or task.id == task_id)]
                if not candidates:
                    raise ConflictError('no_retryable_current_task')
                changed = self._successors(team, {task.role for task in candidates}, None, retry_targets={task.role: task for task in candidates})
            append_event(self.session, team, f'team_{kind}')
            return self._receipt(actor, spec, team, key=idempotency_key, payload=payload, kind=kind, task_ids=[task.id for task in changed])
        except Exception:
            self.session.rollback()
            raise

    def review(self, actor, conversation_id, run_spec_id, *, expected_revision, output_ids, decision, comment, idempotency_key):
        from app.models.research_team import ProfessionalReview
        from app.services.research_gateway import _bounded_text
        _bounded_text(comment, name='review comment', limit=4000, multiline=True)
        if decision not in {'approved', 'changes_requested'} or len(output_ids) != 4 or len(set(output_ids)) != 4:
            raise ValidationFailedError('invalid professional review')
        payload = {'kind': 'review', 'expected_revision': expected_revision, 'output_ids': sorted(str(value) for value in output_ids), 'decision': decision, 'comment': comment}
        try:
            spec, team, receipt = self._begin_write(actor, conversation_id, run_spec_id, key=idempotency_key, payload=payload, expected_revision=expected_revision)
            if receipt is not None:
                self.session.commit()
                return receipt
            view = self.read(actor, conversation_id, run_spec_id)
            current = {}
            for task in view['tasks']:
                current[task['role']] = task
            if len(current) != 4 or any(task['output_state'] != 'available' for task in current.values()):
                raise ConflictError('review_requires_complete_authorized_outputs')
            if {task['output']['id'] for task in current.values()} != set(payload['output_ids']):
                raise ConflictError('review_output_version_changed')
            self.session.add(ProfessionalReview(run_spec_id=spec.id, revision=team.revision, decision=decision, comment=comment, reviewed_by=actor.subject_id, output_ids=payload['output_ids']))
            append_event(self.session, team, 'human_reviewed')
            return self._receipt(actor, spec, team, key=idempotency_key, payload=payload, kind='review', task_ids=[])
        except Exception:
            self.session.rollback()
            raise


def fingerprint(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def latest_tasks(session: Session, run_spec_id: UUID) -> dict[str, ProfessionalTask]:
    result = {}
    for task in session.scalars(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == run_spec_id).order_by(ProfessionalTask.revision, ProfessionalTask.id)):
        result[task.role] = task
    return result


def same_role_history(session: Session, task: ProfessionalTask) -> set[UUID]:
    return set(session.scalars(
        select(ProfessionalDependency.parent_task_id)
        .join(ProfessionalTask, ProfessionalTask.id == ProfessionalDependency.parent_task_id)
        .where(
            ProfessionalDependency.task_id == task.id,
            ProfessionalDependency.run_spec_id == task.run_spec_id,
            ProfessionalTask.role == task.role,
            ProfessionalTask.run_spec_id == task.run_spec_id,
        )
    ))


def append_event(session: Session, team: ResearchTeam, kind: str, *, task: ProfessionalTask | None = None, reason: str | None = None) -> None:
    from app.models.research_team import ProfessionalEvent
    team.event_sequence += 1
    team.updated_at = utcnow()
    session.add(ProfessionalEvent(run_spec_id=team.run_spec_id, task_id=task.id if task else None, sequence=team.event_sequence, kind=kind, reason_code=reason))
