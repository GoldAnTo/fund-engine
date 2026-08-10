from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.ledger import ResearchCase
from app.models.fund_disclosure_sync import FundDisclosureSyncConfigVersion, FundDisclosureSyncRun
from app.models.ledger import Company, Fund, HoldingDisclosure, Stock
from app.models.research_expression import MarketInstrumentBinding
from app.services.fund_disclosure_sync import FundDisclosureSyncService


def _case(session) -> ResearchCase:
    case = ResearchCase(
        title="基金披露补充配置验证",
        industry_topic="事件研究",
        created_by="tester",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    return case


def test_saving_configurations_appends_versions_and_manual_run_uses_latest(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)

    first = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="每周核验",
    )
    second = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827", "110011"],
        frequency="monthly",
        change_reason="调整范围",
    )
    run = service.start_manual_run(case.id)
    session.commit()

    assert (first.version, second.version, run.config_version_id) == (1, 2, second.id)
    assert session.get(FundDisclosureSyncConfigVersion, first.id).fund_codes == ["005827"]
    assert session.get(FundDisclosureSyncConfigVersion, second.id).frequency == "monthly"
    assert session.get(FundDisclosureSyncRun, run.id).trigger == "manual"
    assert run.fund_codes == ["005827", "110011"]


def _bind_stock(session, case: ResearchCase, *, code: str) -> Stock:
    now = datetime.now(timezone.utc)
    company = Company(code=code, name=f"公司 {code}", type="listed", created_at=now)
    session.add(company)
    session.flush()
    stock = Stock(company_id=company.id, code=code, name=f"股票 {code}", market="SZSE", created_at=now)
    session.add(stock)
    session.flush()
    session.add(MarketInstrumentBinding(
        research_case_id=case.id,
        company_id=company.id,
        stock_id=stock.id,
        source_statement_id=uuid.uuid4(),
        relationship_role="directly_affected",
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="已审核",
        reviewed_at=now,
        created_at=now,
    ))
    session.flush()
    return stock


def test_suggestions_only_use_historical_disclosures_for_current_case_stocks(session) -> None:
    case = _case(session)
    related_stock = _bind_stock(session, case, code="300894.SZ")
    other_stock = _bind_stock(session, _case(session), code="00700.HK")
    now = datetime.now(timezone.utc)
    related_fund = Fund(code="560010", name="相关基金", fund_type="ETF", scale=None, establish_date=None, management_company_id=None, created_at=now)
    unrelated_fund = Fund(code="005827", name="无关基金", fund_type="混合", scale=None, establish_date=None, management_company_id=None, created_at=now)
    session.add_all([related_fund, unrelated_fund])
    session.flush()
    session.add_all([
        HoldingDisclosure(fund_id=related_fund.id, stock_id=related_stock.id, weight=Decimal("4.2"), report_period=date(2025, 6, 30), published_at=now, acquired_at=now, source="fixture", coverage_status="partial", created_at=now),
        HoldingDisclosure(fund_id=unrelated_fund.id, stock_id=other_stock.id, weight=Decimal("8.3"), report_period=date(2025, 6, 30), published_at=now, acquired_at=now, source="fixture", coverage_status="partial", created_at=now),
    ])
    session.flush()

    detail = FundDisclosureSyncService(session).detail(case.id)

    assert [item.fund_code for item in detail.suggestions] == ["560010"]
    assert detail.manual_code_fallback is False
    assert FundDisclosureSyncService(session).detail(_case(session).id).manual_code_fallback is True


class _UnmatchedFundClient:
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        table = (
            "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
            "|---|---|---|---|---|---|\n"
            "|易方达蓝筹精选混合|005827.OF|2025-06-30|腾讯控股|00700.HK|9.50|"
            if name == "FinQuery"
            else "公告标题：易方达蓝筹精选混合型证券投资基金2025年第1季度报告；\n发布时间：2025-04-21；"
        )
        return json.dumps({"code": "0", "results": [{"table_markdown": table}]}, ensure_ascii=False)


def test_unmatched_report_is_recorded_as_pending_and_never_becomes_exposure(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="核验季报",
        allow_display=True,
    )

    run = service.run_now(case.id, client=_UnmatchedFundClient())

    assert run.events[-1].stage == "finished"
    assert run.events[-1].payload_json["pending_match_rows"] == 1
    assert session.query(HoldingDisclosure).count() == 0
