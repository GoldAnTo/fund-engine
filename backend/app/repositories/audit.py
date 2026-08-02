"""Append-only audit log repository.

The ``audit_logs`` table is itself append-only (see
``app.models.ledger.IMMUTABLE_TABLES``), so the only operations exposed
here are writes and reads — no update or delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import AuditLog


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        payload: dict[str, Any],
        result: str,
        entity_id: uuid.UUID | None = None,
        error_message: str | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        """Append one audit row. Called from every command endpoint."""
        log = AuditLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            result=result,
            error_message=error_message,
            request_id=request_id,
            created_at=_utcnow(),
        )
        self._session.add(log)
        self._session.flush()
        return log

    def for_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Recent audit rows for one entity, newest first."""
        return list(
            self._session.scalars(
                select(AuditLog)
                .where(AuditLog.entity_type == entity_type)
                .where(AuditLog.entity_id == entity_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )

    def all_logs(self, *, limit: int = 100) -> list[AuditLog]:
        return list(
            self._session.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
            )
        )
