"""Commands for auditable Case monitor configuration and run activity."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import ResearchCase, Thesis
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CaseMonitorConfig:
    frequency: str
    factor_ids: list[uuid.UUID]
    allowed_source_types: list[str]
    next_verification_event: str
    budget: int
    change_reason: str


class ResearchRunEventRepository:
    """Append structured run events without exposing the mutable run row."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        run_id: uuid.UUID,
        *,
        stage: str,
        status: str,
        message: str,
        payload_json: dict | None = None,
    ) -> ResearchRunEvent:
        seq = (
            self._session.scalar(
                select(func.max(ResearchRunEvent.seq)).where(ResearchRunEvent.run_id == run_id)
            )
            or 0
        ) + 1
        event = ResearchRunEvent(
            run_id=run_id,
            seq=seq,
            stage=stage,
            status=status,
            message=message,
            payload_json=payload_json or {},
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event


class CaseMonitorService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        case_id: uuid.UUID,
        *,
        actor: str,
        config: CaseMonitorConfig,
    ) -> CaseMonitorVersion:
        self._validate(case_id, actor=actor, config=config)
        previous_version = self._session.scalar(
            select(func.max(CaseMonitorVersion.version)).where(
                CaseMonitorVersion.research_case_id == case_id
            )
        )
        monitor = CaseMonitorVersion(
            research_case_id=case_id,
            version=(previous_version or 0) + 1,
            status="active",
            frequency=config.frequency.strip(),
            factor_ids=[str(factor_id) for factor_id in config.factor_ids],
            allowed_source_types=[source.strip() for source in config.allowed_source_types],
            next_verification_event=config.next_verification_event.strip(),
            budget=config.budget,
            changed_by=actor.strip(),
            change_reason=config.change_reason.strip(),
            created_at=_utcnow(),
        )
        self._session.add(monitor)
        self._session.flush()
        return monitor

    def _validate(
        self, case_id: uuid.UUID, *, actor: str, config: CaseMonitorConfig
    ) -> None:
        if self._session.get(ResearchCase, case_id) is None:
            raise ValueError("research case not found")
        if not actor.strip():
            raise ValueError("actor must not be empty")
        if not config.frequency.strip():
            raise ValueError("frequency must not be empty")
        if not config.factor_ids:
            raise ValueError("at least one confirmed factor is required")
        if not config.allowed_source_types or not all(
            source.strip() for source in config.allowed_source_types
        ):
            raise ValueError("at least one allowed source is required")
        if not config.next_verification_event.strip():
            raise ValueError("next verification event is required")
        if not config.change_reason.strip():
            raise ValueError("change reason must not be empty")
        if config.budget < 1:
            raise ValueError("budget must be positive")
        confirmed_count = self._session.scalar(
            select(func.count(Thesis.id))
            .where(Thesis.research_case_id == case_id)
            .where(Thesis.id.in_(config.factor_ids))
            .where(Thesis.review_state == "confirmed")
        )
        if confirmed_count != len(set(config.factor_ids)):
            raise ValueError("every monitor factor must be a confirmed factor in this case")
