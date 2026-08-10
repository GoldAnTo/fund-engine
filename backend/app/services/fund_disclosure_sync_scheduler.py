"""Dispatch due weekly/monthly fund-disclosure jobs with visible outcomes."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasources.gildata.client import GildataMCPClient
from app.models.fund_disclosure_sync import FundDisclosureSyncConfigVersion, FundDisclosureSyncRun
from app.services.fund_disclosure_sync import (
    FundDisclosureSyncExecution,
    FundDisclosureSyncService,
)


class FundDisclosureSyncScheduler:
    def __init__(
        self,
        session: Session,
        *,
        client_factory: Callable[[], GildataMCPClient] = GildataMCPClient.from_env,
    ) -> None:
        self._session = session
        self._client_factory = client_factory

    def dispatch_due(self, *, now: datetime | None = None) -> list[FundDisclosureSyncExecution]:
        now = now or datetime.now(timezone.utc)
        latest_by_case: dict[object, FundDisclosureSyncConfigVersion] = {}
        for config in self._session.scalars(
            select(FundDisclosureSyncConfigVersion).order_by(
                FundDisclosureSyncConfigVersion.research_case_id,
                FundDisclosureSyncConfigVersion.version.desc(),
            )
        ):
            latest_by_case.setdefault(config.research_case_id, config)
        created: list[FundDisclosureSyncExecution] = []
        for config in latest_by_case.values():
            if not self._is_due(config.frequency, now) or self._already_dispatched(
                config.research_case_id, now
            ):
                continue
            service = FundDisclosureSyncService(self._session)
            run = service.start_scheduled_run(config.research_case_id)
            try:
                client = self._client_factory()
            except Exception as exc:
                created.append(
                    service.record_failure(
                        run.id,
                        error=exc,
                        message="定时基金披露补充未连接数据源；本次范围已保存，可重试",
                    )
                )
                continue
            try:
                created.append(service.execute(run.id, client=client))
            finally:
                client.close()
        return created

    def _already_dispatched(self, case_id: object, now: datetime) -> bool:
        local_now = now.astimezone(FundDisclosureSyncService._timezone)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(timezone.utc)
        return self._session.scalar(
            select(FundDisclosureSyncRun.id)
            .where(FundDisclosureSyncRun.research_case_id == case_id)
            .where(FundDisclosureSyncRun.trigger == "scheduled")
            .where(FundDisclosureSyncRun.created_at >= utc_start)
            .limit(1)
        ) is not None

    @classmethod
    def _is_due(cls, frequency: str, now: datetime) -> bool:
        local = now.astimezone(FundDisclosureSyncService._timezone)
        if frequency == "weekly":
            return local.weekday() == 0 and (local.hour, local.minute) >= (9, 0)
        return frequency == "monthly" and local.day == 1 and (local.hour, local.minute) >= (9, 0)

    @classmethod
    def next_due_at(cls, frequency: str, *, now: datetime | None = None) -> datetime | None:
        return FundDisclosureSyncService.next_due_at(frequency, now=now)
