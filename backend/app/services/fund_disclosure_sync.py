"""Commands for transparent, Case-scoped fund-disclosure replenishment."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fund_disclosure_sync import FundDisclosureSyncConfigVersion, FundDisclosureSyncRun
from app.models.ledger import ResearchCase, Stock
from app.models.research_expression import MarketInstrumentBinding


_FUND_CODE = re.compile(r"^[0-9A-Z]{4,12}(?:\.[A-Z]{2})?$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FundDisclosureSyncService:
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
        config = self._latest_config(case_id)
        if config is None:
            raise ValueError("fund disclosure sync configuration not found")
        run = FundDisclosureSyncRun(
            research_case_id=case_id,
            config_version_id=config.id,
            trigger="manual",
            fund_codes=list(config.fund_codes),
            stock_codes=list(config.stock_codes),
            allow_display=config.allow_display,
            created_at=_utcnow(),
        )
        self._session.add(run)
        self._session.flush()
        return run

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
