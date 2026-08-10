from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
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
    def list_tools(self) -> list[dict]:
        return [
            {"name": "FinQuery"},
            {"name": "AnnouncementData"},
        ]

    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        table = (
            "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
            "|---|---|---|---|---|---|\n"
            "|易方达蓝筹精选混合|005827.OF|2025-06-30|腾讯控股|00700.HK|9.50|"
            if name == "FinQuery"
            else "公告标题：易方达蓝筹精选混合型证券投资基金2025年第1季度报告；\n发布时间：2025-04-21；"
        )
        return json.dumps({"code": "0", "results": [{"table_markdown": table}]}, ensure_ascii=False)

    def close(self) -> None:
        return None


class _CapabilityUnavailableClient(_UnmatchedFundClient):
    def list_tools(self) -> list[dict]:
        raise RuntimeError("provider tool catalog unavailable")


class _MatchedFundClient(_UnmatchedFundClient):
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        table = (
            "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
            "|---|---|---|---|---|---|\n"
            "|易方达蓝筹精选混合|005827.OF|2025-06-30|腾讯控股|00700.HK|9.50|"
            if name == "FinQuery"
            else "公告标题：易方达蓝筹精选混合型证券投资基金2025年第2季度报告；\n发布时间：2025-07-21；"
        )
        return json.dumps({"code": "0", "results": [{"table_markdown": table}]}, ensure_ascii=False)


def test_fund_disclosure_sync_ignores_display_permission_toggle_in_v1(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    config = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="第一版统一展示已核验的历史披露",
        allow_display=False,
    )

    run = service.run_now(case.id, client=_MatchedFundClient())

    assert config.allow_display is True
    assert run.events[-1].payload_json["pending_permission_rows"] == 0
    assert run.events[-1].payload_json["holding_disclosures_written"] == 1
    assert session.query(HoldingDisclosure).count() == 1


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
    capability = next(event for event in run.events if event.stage == "provider_capability")
    assert capability.payload_json == {
        "provider": "gildata",
        "used_tools": ["FinQuery", "AnnouncementData"],
        "required_fields": [
            "fund_code",
            "stock_code",
            "report_period",
            "publish_date",
        ],
        "unverified_capabilities": ["实时持仓", "基金筛选/推荐"],
    }
    assert run.events[-1].payload_json["pending_match_rows"] == 1
    assert session.query(HoldingDisclosure).count() == 0


def test_capability_probe_failure_is_replayable_and_stops_fund_sync(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="核验能力边界",
        allow_display=True,
    )

    run = service.run_now(case.id, client=_CapabilityUnavailableClient())

    assert [(event.stage, event.status) for event in run.events] == [
        ("scope", "completed"),
        ("provider_capability", "failed"),
        ("failed", "failed"),
    ]
    assert run.events[1].payload_json["used_tools"] == []
    assert run.events[1].payload_json["error_type"] == "RuntimeError"
    assert session.query(HoldingDisclosure).count() == 0


def _admitted_case(cmd_session) -> ResearchCase:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="接口基金补充", industry_topic="事件", created_by="tester", created_at=now)
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://example.test/source",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    cmd_session.add_all([case, document])
    cmd_session.flush()
    cmd_session.add_all([
        CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now),
        CaseTenantAdmission(research_case_id=case.id, tenant_id="test-team", initial_document_version_id=document.id, admitted_by="test", admitted_at=now),
    ])
    cmd_session.commit()
    return case


def test_case_scoped_api_exposes_saved_scope_replay_and_hides_other_tenants(cmd_client, cmd_session, monkeypatch) -> None:
    case = _admitted_case(cmd_session)
    payload = {
        "actor": "human:researcher",
        "fund_codes": ["005827"],
        "frequency": "monthly",
        "allow_display": True,
        "change_reason": "按月核验官方季报",
    }

    saved = cmd_client.put(f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config", json=payload)
    detail = cmd_client.get(f"/api/v1/research-cases/{case.id}/fund-disclosure-sync")
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"other-token":"other-team","test-tenant-token":"test-team"}')
    foreign = cmd_client.get(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync",
        headers={"Authorization": "Bearer other-token"},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert detail.status_code == 200, detail.text
    assert detail.json()["effective_config"]["frequency"] == "monthly"
    assert detail.json()["effective_config"]["fund_codes"] == ["005827"]
    assert foreign.status_code == 404


def test_active_fund_disclosure_runs_are_visible_across_the_research_workspace(
    cmd_client, cmd_session
) -> None:
    case = _admitted_case(cmd_session)
    service = FundDisclosureSyncService(cmd_session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        change_reason="展示正在进行的基金披露补充",
    )
    run = service.start_manual_run(case.id)
    service._append_event(
        run.id,
        stage="query_holdings",
        status="started",
        message="开始查询指定基金的历史股票持仓披露",
        payload_json={"fund_codes": ["005827"]},
    )
    cmd_session.commit()

    response = cmd_client.get("/api/v1/fund-disclosure-sync-runs/active")

    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {
            "run_id": str(run.id),
            "case_id": str(case.id),
            "case_title": case.title,
            "trigger": "manual",
            "status": "started",
            "stage": "query_holdings",
            "message": "开始查询指定基金的历史股票持仓披露",
            "fund_codes": ["005827"],
            "stock_codes": [],
            "updated_at": response.json()["items"][0]["updated_at"],
        }
    ]


def test_case_scoped_api_records_immediate_unmatched_run_and_retries_frozen_scope(cmd_client, cmd_session, monkeypatch) -> None:
    from app.api.v1 import fund_disclosure_sync

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "allow_display": True,
            "change_reason": "立即核验历史持仓季报",
        },
    )
    assert configured.status_code == 200, configured.text
    monkeypatch.setattr(fund_disclosure_sync, "get_fund_disclosure_client", _UnmatchedFundClient)

    started = cmd_client.post(f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs")
    retried = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs/{started.json()['id']}/retry"
    )

    assert started.status_code == 201, started.text
    assert started.json()["events"][-1]["stage"] == "finished"
    assert started.json()["events"][-1]["payload"]["pending_match_rows"] == 1
    assert retried.status_code == 201, retried.text
    assert retried.json()["trigger"] == "retry"
    assert retried.json()["fund_codes"] == ["005827"]
