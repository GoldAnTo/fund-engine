"""Reauthorize frozen professional outputs independently of operational status."""
from __future__ import annotations

import hashlib
from datetime import UTC
from uuid import UUID

from sqlalchemy import select

from app.errors import ConflictError, NotFoundError
from app.models.research_team import (
    ProfessionalAttempt,
    ProfessionalDependency,
    ProfessionalOutput,
    ProfessionalReview,
    ProfessionalTask,
    ResearchTeam,
)
from app.services.research_gateway_content import ResearchGatewayContent
from app.services.research_team_generation import validate_role_result


def _date(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def read_team(session, actor, conversation_id, run_spec_id, *, company_context_checked=False):
    context_available = True
    if not company_context_checked:
        from app.errors import PermissionDeniedError
        from app.models.research_gateway import ResearchRunSpec
        from app.services.company_study import professional_company_context
        try:
            spec = session.get(ResearchRunSpec, run_spec_id)
            if spec is None:
                raise NotFoundError("research not found")
            professional_company_context(session, actor, spec)
        except (NotFoundError, PermissionDeniedError, ConflictError, ValueError, TypeError, KeyError):
            context_available = False
    team = session.get(ResearchTeam, run_spec_id)
    tasks = list(session.scalars(select(ProfessionalTask).where(ProfessionalTask.run_spec_id == run_spec_id).order_by(ProfessionalTask.revision, ProfessionalTask.created_at, ProfessionalTask.id)))
    edges = list(session.scalars(select(ProfessionalDependency).where(ProfessionalDependency.run_spec_id == run_spec_id)))
    outputs = {row.task_id: row for row in session.scalars(select(ProfessionalOutput).where(ProfessionalOutput.run_spec_id == run_spec_id))}
    attempts = list(session.scalars(select(ProfessionalAttempt).join(ProfessionalTask, ProfessionalAttempt.task_id == ProfessionalTask.id).where(ProfessionalTask.run_spec_id == run_spec_id).order_by(ProfessionalAttempt.created_at, ProfessionalAttempt.id)))
    content_service = ResearchGatewayContent(session)
    details = {}
    rejected = set()

    def authorized_detail(identifier):
        if identifier in rejected:
            raise NotFoundError('evidence no longer available')
        if identifier not in details:
            try:
                details[identifier] = content_service.read_evidence(actor, conversation_id, run_spec_id, UUID(identifier))
            except (NotFoundError, ConflictError, ValueError, TypeError):
                rejected.add(identifier)
                raise
        return details[identifier]

    def visible_output(task, output):
        if not context_available or task.status != 'succeeded' or not output.evidence_manifest:
            return None
        try:
            parents = {edge.parent_task_id for edge in edges if edge.task_id == task.id}
            expected_outputs = {str(outputs[parent].id) for parent in parents}
            if set(output.dependency_output_ids) != expected_outputs:
                return None
            quotes = {}
            for entry in output.evidence_manifest:
                identifier = entry['evidence_link_id']
                if identifier in quotes:
                    return None
                detail = authorized_detail(identifier)
                if detail.content_sha256 != entry['content_sha256'] or hashlib.sha256(detail.quote.encode()).hexdigest() != entry['quote_sha256']:
                    return None
                quotes[identifier] = detail.quote
            # A downstream result inherits every source its dependencies used,
            # including sources not repeated in the final prose.
            for parent in parents:
                if any(item['evidence_link_id'] not in quotes for item in outputs[parent].evidence_manifest):
                    return None
            parsed = validate_role_result(output.content, role=task.role, evidence=quotes, dependency_ids={str(parent) for parent in parents})
            return {'id': str(output.id), 'content': parsed.model_dump(mode='json'), 'evidence_ids': sorted(quotes), 'dependency_output_ids': output.dependency_output_ids, 'created_at': _date(output.created_at)}
        except (KeyError, ValueError, TypeError, NotFoundError, ConflictError):
            return None

    rows = []
    for task in tasks:
        stored = outputs.get(task.id)
        output = visible_output(task, stored) if stored is not None else None
        rows.append({
            'id': str(task.id), 'role': task.role, 'revision': task.revision, 'status': task.status,
            'reason_code': task.reason_code, 'attempt': task.attempt, 'instruction': task.instruction,
            'dependency_ids': [str(edge.parent_task_id) for edge in edges if edge.task_id == task.id],
            'created_at': _date(task.created_at), 'updated_at': _date(task.updated_at),
            'output_state': 'available' if output else ('withheld' if stored else 'none'), 'output': output,
            'attempts': [attempt.details for attempt in attempts if attempt.task_id == task.id],
        })
    reviews = list(session.scalars(select(ProfessionalReview).where(ProfessionalReview.run_spec_id == run_spec_id).order_by(ProfessionalReview.created_at, ProfessionalReview.id)))
    return {
        'conversation_id': str(conversation_id), 'run_spec_id': str(run_spec_id),
        'status': team.status if team else 'not_started', 'revision': team.revision if team else 0,
        'event_sequence': team.event_sequence if team else 0, 'tasks': rows,
        'reviews': [{'id': str(row.id), 'revision': row.revision, 'decision': row.decision, 'comment': row.comment, 'reviewed_by': row.reviewed_by, 'output_ids': row.output_ids, 'created_at': _date(row.created_at)} for row in reviews],
    }
