"""Read projections for the effective Case monitor and its latest run."""
from __future__ import annotations

import uuid

from sqlalchemy import case as sql_case, select
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

    def history(self, case_id: uuid.UUID) -> list[CaseMonitorVersion]:
        return list(self._session.scalars(select(CaseMonitorVersion).where(CaseMonitorVersion.research_case_id == case_id).order_by(CaseMonitorVersion.version.desc())))

    def latest_run(self, case_id: uuid.UUID) -> ResearchRun | None:
        # A cancellation updates its historical row.  Do not let that write
        # hide a newer queued/running run that the researcher still needs to
        # monitor in real time.
        active_first = sql_case(
            (ResearchRun.status.in_(("queued", "running", "waiting_for_review")), 0),
            else_=1,
        )
        return self._session.scalar(
            select(ResearchRun)
            .where(ResearchRun.research_case_id == case_id)
            .order_by(active_first, ResearchRun.updated_at.desc(), ResearchRun.id.desc())
            .limit(1)
        )

    def available_confirmed_factors(self, case_id: uuid.UUID) -> list[Thesis]:
        statement = (
            select(Thesis)
            .where(Thesis.research_case_id == case_id)
            .where(Thesis.review_state == "confirmed")
        )
        return list(
            self._session.scalars(statement.order_by(Thesis.created_at, Thesis.id))
        )

    def confirmed_factors(
        self, case_id: uuid.UUID, monitor: CaseMonitorVersion | None
    ) -> list[Thesis]:
        if monitor is None:
            return self.available_confirmed_factors(case_id)

        raw_factor_ids = monitor.factor_ids if isinstance(monitor.factor_ids, list) else []
        factor_ids: list[uuid.UUID] = []
        for raw_factor_id in raw_factor_ids:
            try:
                factor_id = uuid.UUID(str(raw_factor_id))
            except (TypeError, ValueError, AttributeError):
                continue
            if factor_id not in factor_ids:
                factor_ids.append(factor_id)
        if not factor_ids:
            return []

        factors = self._session.scalars(
            select(Thesis)
            .where(Thesis.research_case_id == case_id)
            .where(Thesis.review_state == "confirmed")
            .where(Thesis.id.in_(factor_ids))
        ).all()
        factors_by_id = {factor.id: factor for factor in factors}
        return [
            factors_by_id[factor_id]
            for factor_id in factor_ids
            if factor_id in factors_by_id
        ]
