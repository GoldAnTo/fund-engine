"""Commands for transparent, Case-scoped fund-disclosure replenishment."""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fund_disclosure_sync import (
    FundDisclosureSyncConfigVersion,
    FundDisclosureSyncRun,
    FundDisclosureSyncRunEvent,
)
from app.models.ledger import ResearchCase, Stock
from app.models.research_expression import MarketInstrumentBinding
from app.queries.fund_disclosure_sync import FundDisclosureSyncDetail, FundDisclosureSyncQuery
from app.scripts.ingest_gildata_fund_holdings import ingest


_FUND_CODE = re.compile(r"^[0-9A-Z]{4,12}(?:\.[A-Z]{2})?$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FundDisclosureSyncExecution:
    run: FundDisclosureSyncRun
    events: list[FundDisclosureSyncRunEvent]

    @property
    def id(self) -> uuid.UUID:
        return self.run.id

    @property
    def trigger(self) -> str:
        return self.run.trigger


class FundDisclosureSyncService:
    _timezone = ZoneInfo("Asia/Shanghai")

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_config(
        self,
        case_id: uuid.UUID,
        *,
        actor: str,
        fund_codes: list[str],
        frequency: str,
        change_reason: str,
        allow_display: bool = False,
    ) -> FundDisclosureSyncConfigVersion:
        normalized_codes = self._validate(
            case_id,
            actor=actor,
            fund_codes=fund_codes,
            frequency=frequency,
            change_reason=change_reason,
        )
        last_version = self._session.scalar(
            select(func.max(FundDisclosureSyncConfigVersion.version)).where(
                FundDisclosureSyncConfigVersion.research_case_id == case_id
            )
        )
        config = FundDisclosureSyncConfigVersion(
            research_case_id=case_id,
            version=(last_version or 0) + 1,
            frequency=frequency.strip().lower(),
            fund_codes=normalized_codes,
            stock_codes=self._case_stock_codes(case_id),
            allow_display=allow_display,
            changed_by=actor.strip(),
            change_reason=change_reason.strip(),
            created_at=_utcnow(),
        )
        self._session.add(config)
        self._session.flush()
        return config

    def start_manual_run(self, case_id: uuid.UUID) -> FundDisclosureSyncRun:
        return self._start_run(case_id, trigger="manual")

    def start_scheduled_run(self, case_id: uuid.UUID) -> FundDisclosureSyncRun:
        return self._start_run(case_id, trigger="scheduled")

    def run_now(self, case_id: uuid.UUID, *, client: object) -> FundDisclosureSyncExecution:
        """Execute one transparent, bounded run against its frozen config."""
        run = self.start_manual_run(case_id)
        return self.execute(run.id, client=client)

    def start_retry(self, case_id: uuid.UUID, run_id: uuid.UUID) -> FundDisclosureSyncRun:
        previous = self._session.get(FundDisclosureSyncRun, run_id)
        if previous is None or previous.research_case_id != case_id:
            raise ValueError("fund disclosure sync run not found")
        run = FundDisclosureSyncRun(
            research_case_id=case_id,
            config_version_id=previous.config_version_id,
            trigger="retry",
            fund_codes=list(previous.fund_codes),
            stock_codes=list(previous.stock_codes),
            allow_display=previous.allow_display,
            created_at=_utcnow(),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def execute(self, run_id: uuid.UUID, *, client: object) -> FundDisclosureSyncExecution:
        run = self._session.get(FundDisclosureSyncRun, run_id)
        if run is None:
            raise ValueError("fund disclosure sync run not found")
        self._append_event(
            run.id,
            stage="scope",
            status="completed",
            message="已冻结基金与当前 Case 股票范围",
            payload_json={
                "config_version_id": str(run.config_version_id),
                "fund_codes": list(run.fund_codes),
                "stock_codes": list(run.stock_codes),
                "allow_display": run.allow_display,
            },
        )
        self._append_event(
            run.id,
            stage="query_holdings",
            status="started",
            message="开始查询指定基金的历史股票持仓披露",
            payload_json={"fund_codes": list(run.fund_codes)},
        )
        try:
            stats = ingest(
                self._session,
                client,
                fund_codes=list(run.fund_codes),
                permissions={"display": run.allow_display},
                case_id=run.research_case_id,
            )
        except Exception as exc:
            self._append_event(
                run.id,
                stage="failed",
                status="failed",
                message="基金披露补充失败；可在本记录基础上重试",
                payload_json={"error_type": type(exc).__name__, "error": str(exc)},
            )
            return self._run(run.id)
        payload = asdict(stats)
        self._append_event(
            run.id,
            stage="match_report",
            status="completed",
            message="已按同基金、同报告期季报规则核验来源",
            payload_json={
                "holding_rows_seen": stats.holding_rows_seen,
                "matched_reports": stats.matched_reports,
                "pending_match_rows": stats.pending_match_rows,
                "pending_permission_rows": stats.pending_permission_rows,
            },
        )
        self._append_event(
            run.id,
            stage="write_disclosure",
            status="completed",
            message="已写入符合展示条件的历史基金披露",
            payload_json={
                "holding_disclosures_written": stats.holding_disclosures_written,
                "holding_disclosures_skipped_duplicate": stats.holding_disclosures_skipped_duplicate,
                "invalid_rows": stats.invalid_rows,
            },
        )
        self._append_event(
            run.id,
            stage="finished",
            status="completed",
            message="本次基金披露补充已完成；结果仅代表历史披露",
            payload_json=payload,
        )
        return self._run(run.id)

    def record_failure(
        self, run_id: uuid.UUID, *, error: Exception, message: str = "基金披露补充失败；可在本记录基础上重试"
    ) -> FundDisclosureSyncExecution:
        run = self._session.get(FundDisclosureSyncRun, run_id)
        if run is None:
            raise ValueError("fund disclosure sync run not found")
        self._append_event(
            run.id,
            stage="failed",
            status="failed",
            message=message,
            payload_json={"error_type": type(error).__name__, "error": str(error)},
        )
        return self._run(run.id)

    def detail(self, case_id: uuid.UUID) -> FundDisclosureSyncDetail:
        if self._session.get(ResearchCase, case_id) is None:
            raise ValueError("research case not found")
        return FundDisclosureSyncQuery(self._session).detail(case_id)

    @classmethod
    def next_due_at(cls, frequency: str, *, now: datetime | None = None) -> datetime | None:
        if frequency not in {"weekly", "monthly"}:
            return None
        local_now = (now or _utcnow()).astimezone(cls._timezone)
        if frequency == "weekly":
            days_until_monday = (7 - local_now.weekday()) % 7
            candidate = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
            if days_until_monday == 0 and candidate <= local_now:
                days_until_monday = 7
            candidate += timedelta(days=days_until_monday)
        else:
            if local_now.day == 1 and (local_now.hour, local_now.minute, local_now.second) < (9, 0, 0):
                candidate = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                year = local_now.year + (1 if local_now.month == 12 else 0)
                month = 1 if local_now.month == 12 else local_now.month + 1
                candidate = local_now.replace(year=year, month=month, day=1, hour=9, minute=0, second=0, microsecond=0)
        return candidate.astimezone(timezone.utc)

    def _start_run(self, case_id: uuid.UUID, *, trigger: str) -> FundDisclosureSyncRun:
        config = self._latest_config(case_id)
        if config is None:
            raise ValueError("fund disclosure sync configuration not found")
        run = FundDisclosureSyncRun(
            research_case_id=case_id,
            config_version_id=config.id,
            trigger=trigger,
            fund_codes=list(config.fund_codes),
            stock_codes=list(config.stock_codes),
            allow_display=config.allow_display,
            created_at=_utcnow(),
        )
        self._session.add(run)
        self._session.flush()
        return run

    def _run(self, run_id: uuid.UUID) -> FundDisclosureSyncExecution:
        run = self._session.get(FundDisclosureSyncRun, run_id)
        assert run is not None
        events = list(
            self._session.scalars(
                select(FundDisclosureSyncRunEvent)
                .where(FundDisclosureSyncRunEvent.run_id == run_id)
                .order_by(FundDisclosureSyncRunEvent.seq)
            )
        )
        return FundDisclosureSyncExecution(run=run, events=events)

    def _append_event(
        self,
        run_id: uuid.UUID,
        *,
        stage: str,
        status: str,
        message: str,
        payload_json: dict,
    ) -> FundDisclosureSyncRunEvent:
        seq = (
            self._session.scalar(
                select(func.max(FundDisclosureSyncRunEvent.seq)).where(
                    FundDisclosureSyncRunEvent.run_id == run_id
                )
            )
            or 0
        ) + 1
        event = FundDisclosureSyncRunEvent(
            run_id=run_id,
            seq=seq,
            stage=stage,
            status=status,
            message=message,
            payload_json=payload_json,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def _latest_config(self, case_id: uuid.UUID) -> FundDisclosureSyncConfigVersion | None:
        return self._session.scalar(
            select(FundDisclosureSyncConfigVersion)
            .where(FundDisclosureSyncConfigVersion.research_case_id == case_id)
            .order_by(FundDisclosureSyncConfigVersion.version.desc())
            .limit(1)
        )

    def _case_stock_codes(self, case_id: uuid.UUID) -> list[str]:
        return list(
            self._session.scalars(
                select(Stock.code)
                .join(MarketInstrumentBinding, MarketInstrumentBinding.stock_id == Stock.id)
                .where(MarketInstrumentBinding.research_case_id == case_id)
                .where(MarketInstrumentBinding.review_state == "reviewed")
                .where(MarketInstrumentBinding.stock_id.is_not(None))
                .distinct()
                .order_by(Stock.code)
            )
        )

    def _validate(
        self,
        case_id: uuid.UUID,
        *,
        actor: str,
        fund_codes: list[str],
        frequency: str,
        change_reason: str,
    ) -> list[str]:
        if self._session.get(ResearchCase, case_id) is None:
            raise ValueError("research case not found")
        if not actor.strip():
            raise ValueError("actor must not be empty")
        if frequency.strip().lower() not in {"weekly", "monthly"}:
            raise ValueError("frequency must be weekly or monthly")
        if not change_reason.strip():
            raise ValueError("change reason must not be empty")
        codes = sorted({code.strip().upper() for code in fund_codes if code.strip()})
        if not 1 <= len(codes) <= 50:
            raise ValueError("fund codes must contain between 1 and 50 values")
        invalid = next((code for code in codes if not _FUND_CODE.fullmatch(code)), None)
        if invalid is not None:
            raise ValueError(f"invalid fund code: {invalid}")
        return codes
