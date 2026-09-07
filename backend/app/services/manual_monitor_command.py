"""Version-bound manual runs with atomic retry identity and frozen actor audit."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.orm import Session

from app.errors import ConflictError, ValidationFailedError
from app.queries.case_monitor import CaseMonitorQuery
from app.repositories.operational import IdempotencyRepository
from app.schemas.v1.case_monitor import StartManualMonitorRunRequest
from app.services.auto_research import AutoResearchService
from app.services.event_research_scope_evidence import lock_event_scope_case


def start_manual_monitor_command(db: Session, case_id: uuid.UUID, tenant_id: str,
                                 payload: StartManualMonitorRunRequest):
    if not payload.actor.strip() or not payload.change_reason.strip() or not payload.idempotency_key.strip():
        raise ValidationFailedError('actor, reason and idempotency key must not be blank')
    canonical = json.dumps({'case_id': str(case_id), **payload.model_dump()},
                           sort_keys=True, separators=(',', ':'))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    key = 'manual-monitor:' + hashlib.sha256(json.dumps(
        [tenant_id, payload.idempotency_key], separators=(',', ':')).encode()).hexdigest()
    repo = IdempotencyRepository(db)
    # Keep SQLite's savepoint inside an outer transaction so failed validation
    # cannot leave a committed in-progress slot behind.
    connection = db.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    try:
        slot, acquired = repo.acquire(key=key, request_fingerprint=fingerprint)
        service = AutoResearchService(db)
        if not acquired:
            if slot.request_fingerprint != fingerprint or slot.status != 'completed':
                raise ConflictError('idempotency_conflict')
            run_id = uuid.UUID(slot.response_payload['run_id'])
            db.expire_all()
            # Recompute the current display-filtered response; do not replay a
            # cached source-bearing DTO after permissions or run state change.
            response = service.detail(run_id)
            db.commit()
            return response
        lock_event_scope_case(db, case_id)
        monitor = CaseMonitorQuery(db).effective(case_id)
        if monitor is None or monitor.version != payload.expected_version:
            raise ConflictError('monitor configuration changed; reload before starting')
        run = service.start_from_monitor(case_id, commit=False, scope_context={
            'requested_by': payload.actor.strip(),
            'request_reason': payload.change_reason.strip(),
            'monitor_expected_version': payload.expected_version,
        })
        response = service.detail(run.id)
        repo.complete(slot, response_status=201, response_payload={'run_id': str(run.id)})
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise
