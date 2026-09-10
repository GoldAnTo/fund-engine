"""Forecast verdicts stay machine-auditable and human-published."""
from __future__ import annotations

from decimal import Decimal
import uuid
from datetime import date, datetime, timezone

from app.models.ledger import CaseDocumentVersion, Company, DocumentVersion, Fund, HoldingDisclosure, SourceSpan, SourceStatement, Stock
from app.models.research_expression import KeyFactor, ReportClaim
from app.services.source_governance import SourceGovernanceService


def test_within_tolerance_forecast_miss_is_contradicted() -> None:
    from app.services.forecast_verdicts import evaluate_numeric_forecast

    result = evaluate_numeric_forecast(
        expected_value=Decimal("455000000"),
        actual_value=Decimal("247245713.03"),
        comparator="within_tolerance",
        relative_tolerance=Decimal("0.10"),
    )

    assert result.outcome == "contradicted"
    assert result.rule_version == "forecast-numeric-v1"
    assert result.inputs["expected_value"] == "455000000"
    assert result.inputs["actual_value"] == "247245713.03"


def test_within_tolerance_forecast_hit_is_supported() -> None:
    from app.services.forecast_verdicts import evaluate_numeric_forecast

    result = evaluate_numeric_forecast(
        expected_value=Decimal("455000000"),
        actual_value=Decimal("470000000"),
        comparator="within_tolerance",
        relative_tolerance=Decimal("0.10"),
    )

    assert result.outcome == "supported"


def _case_with_frozen_sources(cmd_client, cmd_session) -> tuple[uuid.UUID, SourceStatement, SourceStatement, ReportClaim, KeyFactor]:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json={
        "raw_input": "验证历史研报的利润预测", "event_title": "火星人利润预测验证",
        "company_name": "火星人", "ticker": "300894.SZ", "research_question": "预测是否兑现？",
        "candidate_factors": ["归母净利润", "毛利率", "渠道费用"], "created_by": "tester",
    }).json()["case_id"])
    forecast_at = datetime(2023, 4, 25, 8, 0, tzinfo=timezone.utc)
    actual_at = datetime(2024, 4, 22, 8, 0, tzinfo=timezone.utc)
    documents = []
    for title, source_url, available_at, source_sha256 in (
        ("开源证券火星人研报", "https://pdf.dfcfw.com/pdf/H3_AP202304251585791096_1.pdf", forecast_at, "8e5d8d05e960d0e901f90a39a0c6734b05e63f64372ff0d135768dc3613eaef2"),
        ("火星人2023年年度报告", "https://static.cninfo.com.cn/finalpage/2024-04-22/1219704396.PDF", actual_at, "3d83bb6584788877b4cb4f9ba4b2d011211d381b07600ba889af935ead3920d3"),
    ):
        document = DocumentVersion(content_sha256=source_sha256, source_url=source_url, title=title, available_at=available_at, acquired_at=available_at, parser_version="fixture-v1", parse_state="success")
        cmd_session.add(document)
        cmd_session.flush()
        SourceGovernanceService(cmd_session).record_event_intake(
            document=document, source_type="licensed_provider",
            source_metadata={"provider_name": "fixture", "permissions": {"ai_processing": True, "display": True}},
            declared_by="tester",
        )
        cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=available_at))
        documents.append(document)
    forecast_span = SourceSpan(document_version_id=documents[0].id, locator={"page": 1, "table": "财务摘要"}, verbatim_text="归母净利润(百万元) 315 455")
    actual_span = SourceSpan(document_version_id=documents[1].id, locator={"page": 123, "row": "归属于母公司股东的净利润"}, verbatim_text="归属于母公司股东的净利润 247,245,713.03")
    cmd_session.add_all([forecast_span, actual_span])
    cmd_session.flush()
    forecast_statement = SourceStatement(source_span_id=forecast_span.id, kind="forecast", normalized_text="预计2023年归母净利润455百万元，2022年315百万元", created_at=forecast_at)
    actual_statement = SourceStatement(source_span_id=actual_span.id, kind="disclosed_fact", normalized_text="2023年归属于母公司股东的净利润247,245,713.03元", created_at=actual_at)
    cmd_session.add_all([forecast_statement, actual_statement])
    cmd_session.flush()
    claim = ReportClaim(research_case_id=case_id, source_statement_id=forecast_statement.id, text="预计2023年归母净利润455百万元", claim_kind="forecast", asserted_period=date(2023, 12, 31), asserted_by="开源证券", review_state="reviewed", reviewed_by="human:reviewer", review_reason="冻结券商预测，不作为公司披露事实。", reviewed_at=forecast_at, created_at=forecast_at)
    cmd_session.add(claim)
    cmd_session.flush()
    factor = KeyFactor(research_case_id=case_id, report_claim_id=claim.id, name="2023年归母净利润预测", expected_direction="positive", metric_name="归母净利润", allowed_source_types=["company_disclosure"], verification_window_start=date(2023, 1, 1), verification_window_end=date(2023, 12, 31), support_condition="实际值处于容差内", refutation_condition="实际值超出容差", next_verification_event="2023年年度报告", review_state="reviewed", reviewed_by="human:reviewer", review_reason="预测与验证窗完整。", reviewed_at=forecast_at, created_at=forecast_at)
    cmd_session.add(factor)
    cmd_session.commit()
    return case_id, forecast_statement, actual_statement, claim, factor


def test_http_forecast_candidate_stays_hidden_until_human_verdict(cmd_client, cmd_session) -> None:
    case_id, forecast_statement, actual_statement, claim, factor = _case_with_frozen_sources(cmd_client, cmd_session)
    target = cmd_client.post(f"/api/v1/research-cases/{case_id}/forecast-targets", json={
        "key_factor_id": str(factor.id), "report_claim_id": str(claim.id),
        "forecast_source_statement_id": str(forecast_statement.id), "baseline_source_statement_id": str(forecast_statement.id),
        "metric_name": "归母净利润", "entity_key": "300894.SZ", "baseline_value": "315000000",
        "expected_value": "455000000", "unit": "CNY", "forecast_period_start": "2023-01-01", "forecast_period_end": "2023-12-31",
        "comparator": "within_tolerance", "relative_tolerance": "0.10", "reviewed_by": "human:reviewer", "review_reason": "冻结研报表格数值。",
    })
    assert target.status_code == 201, target.text
    wrong_entity = cmd_client.post(f"/api/v1/forecast-targets/{target.json()['id']}/actual-metric-observations", json={
        "source_statement_id": str(actual_statement.id), "entity_key": "000001.SZ", "observed_value": "247245713.03", "unit": "CNY",
        "observed_period_start": "2023-01-01", "observed_period_end": "2023-12-31", "available_at": "2024-04-22T08:00:00Z",
        "recorded_by": "human:reviewer", "record_reason": "错误实体必须被拒绝。",
    })
    assert wrong_entity.status_code == 422
    actual = cmd_client.post(f"/api/v1/forecast-targets/{target.json()['id']}/actual-metric-observations", json={
        "source_statement_id": str(actual_statement.id), "entity_key": "300894.SZ", "observed_value": "247245713.03", "unit": "CNY",
        "observed_period_start": "2023-01-01", "observed_period_end": "2023-12-31", "available_at": "2024-04-22T08:00:00Z",
        "recorded_by": "human:reviewer", "record_reason": "年报第123页审计口径。",
    })
    assert actual.status_code == 201, actual.text
    candidate = cmd_client.post(f"/api/v1/forecast-targets/{target.json()['id']}/evaluate", json={"actual_observation_id": actual.json()["id"], "cutoff": "2024-04-22T23:59:59Z"})
    assert candidate.status_code == 201, candidate.text
    assert candidate.json()["outcome"] == "contradicted"
    expression = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-expression?as_of=2024-04-22&cutoff=2024-04-22T23:59:59Z")
    assert expression.status_code == 200
    assert expression.json()["factors"][0]["forecast_verdict"] is None
    verdict = cmd_client.post(f"/api/v1/forecast-evaluations/{candidate.json()['id']}/verdicts", json={"decision": "confirmed", "reason": "年报实际值显著低于冻结预测，确认未兑现。", "reviewed_by": "human:reviewer"})
    assert verdict.status_code == 201, verdict.text
    fund_at = datetime(2024, 8, 30, 8, 0, tzinfo=timezone.utc)
    fund_document = DocumentVersion(
        content_sha256="98eb181d73cff199f6169aea238b32661559e4ee2b77ddfdd70f9cb10c740031",
        source_url="https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-08-30/560010_20240830_0EVT.pdf",
        title="广发中证1000ETF 2024年中期报告", available_at=fund_at, acquired_at=fund_at,
        parser_version="fixture-v1", parse_state="success",
    )
    cmd_session.add(fund_document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=fund_document, source_type="licensed_provider",
        source_metadata={"provider_name": "sse", "permissions": {"ai_processing": True, "display": True}}, declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=fund_document.id, linked_at=fund_at))
    fund_span = SourceSpan(document_version_id=fund_document.id, locator={"page": 100, "row": 907}, verbatim_text="300894 火星人 349,600 4,481,872.00 0.04")
    cmd_session.add(fund_span)
    cmd_session.flush()
    fund_statement = SourceStatement(source_span_id=fund_span.id, kind="disclosed_fact", normalized_text="560010 于2024-06-30持有300894火星人349600股，市值4481872元。", created_at=fund_at)
    company = Company(code="300894", name="火星人", type="listed", created_at=fund_at)
    cmd_session.add_all([fund_statement, company])
    cmd_session.flush()
    stock = Stock(company_id=company.id, code="300894.SZ", name="火星人", market="SZSE", created_at=fund_at)
    fund = Fund(code="560010", name="广发中证1000ETF", fund_type="equity", created_at=fund_at)
    cmd_session.add_all([stock, fund])
    cmd_session.flush()
    cmd_session.add(HoldingDisclosure(
        fund_id=fund.id, stock_id=stock.id, weight=Decimal("0.0004"), report_period=date(2024, 6, 30),
        published_at=fund_at, acquired_at=fund_at, source="sse_fund_report",
        source_document_version_id=fund_document.id, source_span_id=fund_span.id, coverage_status="complete", created_at=fund_at,
    ))
    cmd_session.commit()
    binding = cmd_client.post(f"/api/v1/research-cases/{case_id}/market-instruments", json={
        "company_id": str(company.id), "stock_id": str(stock.id), "source_statement_id": str(forecast_statement.id),
        "relationship_role": "directly_affected", "reviewed_by": "human:reviewer", "review_reason": "预测主体即该股票。",
    })
    assert binding.status_code == 201, binding.text
    impact = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors/{factor.id}/fundamental-impacts", json={
        "market_instrument_binding_id": binding.json()["id"], "source_statement_id": str(actual_statement.id),
        "metric_name": "归母净利润", "expected_direction": "positive", "rationale": "以年报实际指标验证研报预测。",
        "reviewed_by": "human:reviewer", "review_reason": "公司与指标口径已核对。",
    })
    assert impact.status_code == 201, impact.text
    formal = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-expression?as_of=2026-08-10&cutoff=2026-08-10T23:59:59Z")
    forecast_verdict = formal.json()["factors"][0]["forecast_verdict"]
    assert forecast_verdict["outcome"] == "contradicted"
    assert forecast_verdict["actual_value"] == 247245713.03
    assert forecast_verdict["actual_source"]["locator"] == {"page": 123, "row": "归属于母公司股东的净利润"}
    assert formal.json()["fundamentals"][0]["stock_code"] == "300894.SZ"
    fund_position = formal.json()["fund_exposure"][0]["positions"][0]
    assert formal.json()["fund_exposure"][0]["fund_code"] == "560010"
    assert fund_position["report_period"] == "2024-06-30"
    assert fund_position["source_locator"] == {"page": 100, "row": 907}
    assert fund_position["freshness_status"] == "stale_disclosure"
    listed = cmd_client.get(f"/api/v1/research-cases/{case_id}/forecast-verdicts?cutoff=2026-08-10T23:59:59Z")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["outcome"] == "contradicted"
    before_actual = cmd_client.get(f"/api/v1/research-cases/{case_id}/forecast-verdicts?cutoff=2024-04-21T23:59:59Z")
    assert before_actual.status_code == 200
    assert before_actual.json()["items"] == []
    rejected_successor = cmd_client.post(f"/api/v1/forecast-evaluations/{candidate.json()['id']}/verdicts", json={
        "decision": "rejected", "reason": "撤回已发布结论以待补充审查。", "reviewed_by": "human:reviewer", "supersedes_id": verdict.json()["id"],
    })
    assert rejected_successor.status_code == 201, rejected_successor.text
    withdrawn = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-expression?as_of=2026-08-10&cutoff=2026-08-10T23:59:59Z")
    assert withdrawn.json()["factors"][0]["forecast_verdict"] is None
