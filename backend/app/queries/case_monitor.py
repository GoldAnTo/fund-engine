"""Read projections for the effective Case monitor and its latest run."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion
from app.models.ledger import Thesis


class CaseMonitorQuery:
    def __init__(self, session: Session) -> None:
        self._session = session

    def effective(self, case_id: uuid.UUID) -> CaseMonitorVersion | None:
        return self._session.scalar(
            select(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case_id)
            .order_by(CaseMonitorVersion.version.desc())
            .limit(1)
        )

    def latest_run(self, case_id: uuid.UUID) -> ResearchRun | None:
        return self._session.scalar(
            select(ResearchRun)
            .where(ResearchRun.research_case_id == case_id)
            .order_by(ResearchRun.updated_at.desc(), ResearchRun.id.desc())
            .limit(1)
        )

    def confirmed_factors(self, case_id: uuid.UUID) -> list[Thesis]:
        return list(
            self._session.scalars(
                select(Thesis)
                .where(Thesis.research_case_id == case_id)
                .where(Thesis.review_state == "confirmed")
                .order_by(Thesis.created_at, Thesis.id)
            )
        )
