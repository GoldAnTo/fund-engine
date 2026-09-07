"""Commands for auditable Case monitor configuration and run activity."""
from __future__ import annotations

from app.services.monitor_frequencies import MONITOR_TARGETS
from app.services.event_research_scope_evidence import lock_event_scope_case

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.models.ledger import ResearchCase, Thesis
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent


SUPPORTED_SOURCE_TYPES = frozenset(
    {"licensed_provider", "company_disclosure", "uploaded_file", "pasted_snapshot"}
)


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
        expected_version: int | None = None,
    ) -> CaseMonitorVersion:
        lock_event_scope_case(self._session, case_id)
        self._validate(case_id, actor=actor, config=config)
        previous_version = self._session.scalar(
            select(func.max(CaseMonitorVersion.version)).where(
                CaseMonitorVersion.research_case_id == case_id
            )
        )
        if expected_version is not None and expected_version != (previous_version or 0):
            raise ConflictError("monitor configuration changed; reload before saving")
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

    def set_status(self, case_id: uuid.UUID, *, actor: str, status: str, reason: str, expected_version: int | None = None) -> CaseMonitorVersion:
        lock_event_scope_case(self._session, case_id)
        previous = self._session.scalar(select(CaseMonitorVersion).where(CaseMonitorVersion.research_case_id == case_id).order_by(CaseMonitorVersion.version.desc()).limit(1))
        if previous is None:
            raise ValueError("case monitor not found")
        if expected_version is not None and expected_version != previous.version:
            raise ConflictError("monitor configuration changed; reload before changing status")
        if status not in {"active", "paused"}:
            raise ValueError("unsupported monitor status")
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and change reason must not be empty")
        if status == "active" and previous.frequency not in MONITOR_TARGETS:
            raise ValueError("unsupported monitor frequency; save a supported configuration before resuming")
        monitor = CaseMonitorVersion(research_case_id=case_id, version=previous.version + 1, status=status, frequency=previous.frequency, factor_ids=list(previous.factor_ids), allowed_source_types=list(previous.allowed_source_types), next_verification_event=previous.next_verification_event, budget=previous.budget, changed_by=actor.strip(), change_reason=reason.strip(), created_at=_utcnow())
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
        if config.frequency.strip() not in MONITOR_TARGETS:
            raise ValueError("unsupported monitor frequency")
        if not config.factor_ids:
            raise ValueError("at least one confirmed factor is required")
        if not config.allowed_source_types or not all(
            source.strip() for source in config.allowed_source_types
        ):
            raise ValueError("at least one allowed source is required")
        unsupported = set(config.allowed_source_types).difference(SUPPORTED_SOURCE_TYPES)
        if unsupported:
            raise ValueError(f"unsupported allowed source type: {sorted(unsupported)[0]}")
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
