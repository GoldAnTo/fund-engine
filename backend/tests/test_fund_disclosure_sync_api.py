from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)
from app.models.fund_disclosure_sync import FundDisclosureSyncConfigVersion, FundDisclosureSyncRun, FundDisclosureSyncRunEvent
from app.models.ledger import Company, Fund, HoldingDisclosure, Stock
from app.models.research_expression import MarketInstrumentBinding
from app.datasources.gildata.client import GildataMCPError
from app.scripts.ingest_gildata_fund_holdings import ingest
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
        report_period=date(2025, 6, 30),
        change_reason="每周核验",
    )
    second = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827", "110011"],
        frequency="monthly",
        report_period=date(2025, 6, 30),
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
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url=f"https://example.test/{code}",
        title=f"{code} 关联依据",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
        parse_state="success",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        locator={"kind": "fixture"},
        verbatim_text=f"{code} 是当前事件相关股票",
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="market_instrument_binding",
        normalized_text=f"{code} 是当前事件相关股票",
        observed_period=None,
        created_at=now,
    )
    session.add(statement)
    session.flush()
    session.add(MarketInstrumentBinding(
        research_case_id=case.id,
        company_id=company.id,
        stock_id=stock.id,
        source_statement_id=statement.id,
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
        raise GildataMCPError("provider tool catalog unavailable")


class _CapabilityProgrammingErrorClient(_UnmatchedFundClient):
    def list_tools(self) -> list[dict]:
        raise TypeError("programming defect")


class _IngestProgrammingErrorClient(_UnmatchedFundClient):
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        raise TypeError("programming defect")


class _IngestProviderErrorClient(_UnmatchedFundClient):
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        raise GildataMCPError("provider failure sentinel-secret")


class _MalformedIngestClient(_UnmatchedFundClient):
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        return "not-json sentinel-secret"


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


class _ScopedFundClient(_UnmatchedFundClient):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        query = str(arguments.get("query", ""))
        self.queries.append(query)
        table = (
            "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
            "|---|---|---|---|---|---|\n"
            "|示例ETF|515050.OF|2024-12-31|工业富联|601138|5.33|\n"
            "|示例ETF|515050.SH|2024-12-31|立讯精密|002475|1.20|\n"
            "|示例ETF|515050.OF|2025-03-31|未来样本|300001|3.00|\n"
            "|其他基金|999999.OF|2024-12-31|错误范围|600000|2.00|"
            if name == "FinQuery"
            else "公告标题：示例ETF2024年第4季度报告；\n发布时间：2025-01-21；"
        )
        return json.dumps({"code": "0", "results": [{"table_markdown": table}]}, ensure_ascii=False)


def test_ingest_normalizes_provider_suffixes_and_discards_out_of_scope_rows(session) -> None:
    client = _ScopedFundClient()

    stats = ingest(
        session,
        client,
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )

    assert stats.holding_rows_seen == 4
    assert stats.holding_disclosures_written == 2
    assert stats.out_of_scope_rows == 2
    assert session.scalar(select(HoldingDisclosure.weight).order_by(HoldingDisclosure.weight.desc())) == Decimal("0.0533")
    assert all("2024年第4季度" in query for query in client.queries)
    assert not any("2025年第1季度" in query for query in client.queries)


class _AliasFundClient(_UnmatchedFundClient):
    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        table = (
            "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
            "|---|---|---|---|---|---|\n"
            "|通信ETF华夏|515050.SH|2024-12-31|工业富联|601138.SH|5.33|\n"
            "|华夏中证5G通信主题ETF|515050.OF|2024-12-31|立讯精密|002475.SZ|1.20|"
            if name == "FinQuery"
            else "公告标题：华夏中证5G通信主题交易型开放式指数证券投资基金2024年第4季度报告；\n证券简称：华夏中证5G通信主题ETF；\n发布时间：2025-01-22；"
        )
        return json.dumps({"code": "0", "results": [{"table_markdown": table}]}, ensure_ascii=False)


def test_ingest_accepts_provider_announced_alias_but_still_requires_exact_quarter(session) -> None:
    stats = ingest(
        session,
        _AliasFundClient(),
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )

    assert stats.matched_reports == 1
    assert stats.holding_disclosures_written == 2


def test_fund_disclosure_sync_ignores_display_permission_toggle_in_v1(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    config = service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        report_period=date(2025, 6, 30),
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
        report_period=date(2025, 6, 30),
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
        report_period=date(2025, 6, 30),
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
    assert run.events[1].payload_json["error_type"] == "operation_failure"
    assert session.query(HoldingDisclosure).count() == 0


def test_recorded_fund_sync_failure_does_not_persist_exception_details(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        report_period=date(2025, 6, 30),
        change_reason="验证错误安全",
    )
    run = service.start_manual_run(case.id)

    failed = service.record_failure(
        run.id,
        error=RuntimeError(
            "https://provider.invalid?token=sentinel-secret response body"
        ),
    )

    event = failed.events[-1]
    assert event.payload_json == {
        "error_type": "operation_failure",
        "error": "provider operation failed",
    }
    assert "sentinel-secret" not in str(service.detail(case.id))


def test_fund_sync_execution_failure_does_not_persist_exception_details(
    session, monkeypatch
) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        report_period=date(2025, 6, 30),
        change_reason="验证执行错误安全",
    )

    monkeypatch.setattr(
        "app.services.fund_disclosure_sync.ingest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            GildataMCPError(
                "https://provider.invalid?token=sentinel-secret response body"
            )
        ),
    )
    failed = service.run_now(case.id, client=_UnmatchedFundClient())

    assert failed.events[-1].payload_json == {
        "error_type": "operation_failure",
        "error": "provider operation failed",
    }
    assert "sentinel-secret" not in str(service.detail(case.id))


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
        "report_period": "2025-06-30",
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


def test_config_requires_and_freezes_a_report_period_for_the_run(cmd_client, cmd_session) -> None:
    case = _admitted_case(cmd_session)
    url = f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config"

    missing_period = cmd_client.put(
        url,
        json={
            "actor": "human:researcher",
            "fund_codes": ["515050"],
            "frequency": "monthly",
            "change_reason": "历史回放",
        },
    )
    saved = cmd_client.put(
        url,
        json={
            "actor": "human:researcher",
            "fund_codes": ["515050"],
            "frequency": "monthly",
            "report_period": "2024-12-31",
            "change_reason": "历史回放",
        },
    )

    assert missing_period.status_code == 422
    assert saved.status_code == 200, saved.text
    assert saved.json()["report_period"] == "2024-12-31"

    run = FundDisclosureSyncService(cmd_session).start_manual_run(case.id)
    assert run.report_period == date(2024, 12, 31)


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
        report_period=date(2025, 6, 30),
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
            "report_period": "2025-06-30",
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


def test_case_scoped_api_redacts_client_factory_failure(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import fund_disclosure_sync

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "report_period": "2025-06-30",
            "change_reason": "验证工厂错误安全",
        },
    )
    assert configured.status_code == 200

    def fail_factory():
        raise GildataMCPError(
            "https://provider.invalid?token=sentinel-secret response body"
        )

    monkeypatch.setattr(
        fund_disclosure_sync, "get_fund_disclosure_client", fail_factory
    )
    response = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs"
    )

    assert response.status_code == 201
    failed_event = response.json()["events"][-1]
    assert failed_event["payload"] == {
        "error_type": "operation_failure",
        "error": "provider operation failed",
    }
    assert "sentinel-secret" not in response.text


def test_run_executor_does_not_convert_client_factory_programming_error(
    session,
) -> None:
    from app.api.v1.fund_disclosure_sync import _execute_run

    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["005827"],
        frequency="weekly",
        report_period=date(2025, 6, 30),
        change_reason="验证编程错误边界",
    )
    run = service.start_manual_run(case.id)

    def broken_factory():
        raise TypeError("programming defect")

    with pytest.raises(TypeError, match="programming defect"):
        _execute_run(service, run.id, client_factory=broken_factory)


def test_run_route_does_not_convert_client_factory_programming_error_to_422(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import fund_disclosure_sync

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "report_period": "2025-06-30",
            "change_reason": "验证路由编程错误边界",
        },
    )
    assert configured.status_code == 200

    def broken_factory():
        raise TypeError("programming defect")

    monkeypatch.setattr(
        fund_disclosure_sync, "get_fund_disclosure_client", broken_factory
    )
    response = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs"
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


@pytest.mark.parametrize(
    "client_type",
    [_CapabilityProgrammingErrorClient, _IngestProgrammingErrorClient],
)
@pytest.mark.parametrize("is_retry", [False, True])
def test_start_and_retry_routes_do_not_convert_service_programming_errors(
    cmd_client, cmd_session, monkeypatch, client_type, is_retry
) -> None:
    from app.api.v1 import fund_disclosure_sync

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "report_period": "2025-06-30",
            "change_reason": "验证服务编程错误边界",
        },
    )
    assert configured.status_code == 200
    url = f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs"
    if is_retry:
        parent = FundDisclosureSyncService(cmd_session).start_manual_run(case.id)
        cmd_session.commit()
        url += f"/{parent.id}/retry"
    before_ids = set(
        cmd_session.scalars(
            select(FundDisclosureSyncRun.id).where(
                FundDisclosureSyncRun.research_case_id == case.id
            )
        )
    )
    monkeypatch.setattr(
        fund_disclosure_sync, "get_fund_disclosure_client", client_type
    )

    response = cmd_client.post(url)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    cmd_session.rollback()
    new_runs = list(
        cmd_session.scalars(
            select(FundDisclosureSyncRun)
            .where(FundDisclosureSyncRun.research_case_id == case.id)
            .where(FundDisclosureSyncRun.id.not_in(before_ids))
        )
    )
    assert len(new_runs) == 1
    assert new_runs[0].trigger == ("retry" if is_retry else "manual")
    events = list(
        cmd_session.scalars(
            select(FundDisclosureSyncRunEvent).where(
                FundDisclosureSyncRunEvent.run_id == new_runs[0].id
            )
        )
    )
    assert all(event.status != "failed" for event in events)


@pytest.mark.parametrize(
    "client_type",
    [_CapabilityUnavailableClient, _IngestProviderErrorClient, _MalformedIngestClient],
)
def test_real_provider_failures_remain_transparent_replayable_runs(
    cmd_client, cmd_session, monkeypatch, client_type
) -> None:
    from app.api.v1 import fund_disclosure_sync

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "report_period": "2025-06-30",
            "change_reason": "验证真实供应商失败契约",
        },
    )
    assert configured.status_code == 200
    monkeypatch.setattr(
        fund_disclosure_sync, "get_fund_disclosure_client", client_type
    )

    response = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs"
    )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["events"][-1]["payload"] == {
        "error_type": "operation_failure",
        "error": "provider operation failed",
    }
    assert "sentinel-secret" not in response.text


@pytest.mark.parametrize(
    "result",
    [
        1,
        {},
        {"table_markdown": {}},
        {"table_markdown": "totally malformed sentinel-secret"},
    ],
)
@pytest.mark.parametrize("is_retry", [False, True])
def test_malformed_fund_provider_results_are_safe_failed_runs(
    cmd_client, cmd_session, monkeypatch, result, is_retry
) -> None:
    from app.api.v1 import fund_disclosure_sync

    class Client(_UnmatchedFundClient):
        def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
            return json.dumps({"code": "0", "results": [result]})

    case = _admitted_case(cmd_session)
    configured = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/config",
        json={
            "actor": "human:researcher",
            "fund_codes": ["005827"],
            "frequency": "weekly",
            "report_period": "2025-06-30",
            "change_reason": "验证供应商结果结构",
        },
    )
    assert configured.status_code == 200
    monkeypatch.setattr(
        fund_disclosure_sync, "get_fund_disclosure_client", Client
    )
    url = f"/api/v1/research-cases/{case.id}/fund-disclosure-sync/runs"
    if is_retry:
        parent = FundDisclosureSyncService(cmd_session).start_manual_run(case.id)
        cmd_session.commit()
        url += f"/{parent.id}/retry"

    response = cmd_client.post(url)

    assert response.status_code == 201
    assert response.json()["trigger"] == ("retry" if is_retry else "manual")
    assert response.json()["status"] == "failed"
    assert response.json()["events"][-1]["payload"] == {
        "error_type": "operation_failure",
        "error": "provider operation failed",
    }
    assert "sentinel-secret" not in response.text


def test_stale_run_is_interrupted_and_retry_preserves_frozen_period(session) -> None:
    case = _case(session)
    service = FundDisclosureSyncService(session)
    service.save_config(
        case.id,
        actor="human:researcher",
        fund_codes=["515050"],
        frequency="monthly",
        report_period=date(2024, 12, 31),
        change_reason="历史回放",
    )
    config = service._latest_config(case.id)
    assert config is not None
    run = FundDisclosureSyncRun(
        research_case_id=case.id,
        config_version_id=config.id,
        trigger="manual",
        report_period=config.report_period,
        fund_codes=list(config.fund_codes),
        stock_codes=list(config.stock_codes),
        allow_display=config.allow_display,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    session.add(FundDisclosureSyncRunEvent(
        run_id=run.id,
        seq=1,
        stage="query_holdings",
        status="started",
        message="开始查询指定基金的历史股票持仓披露",
        payload_json={"report_period": "2024-12-31"},
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    ))
    session.flush()

    assert service.recover_interrupted_runs(case.id, now=datetime(2025, 1, 1, 0, 10, tzinfo=timezone.utc)) == 1
    recovered = service._run(run.id)
    assert recovered.events[-1].stage == "interrupted"
    assert recovered.events[-1].status == "failed"
    retry = service.start_retry(case.id, run.id)
    assert retry.report_period == date(2024, 12, 31)
