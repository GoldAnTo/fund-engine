from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    Fund,
    HoldingDisclosure,
    SourceSpan,
    SourceStatement,
    Stock,
    ImmutableLedgerError,
)
from app.models.research_expression import (
    ClaimVerification,
    FundamentalImpact,
    KeyFactor,
    MarketObservation,
    ReportClaim,
)


def _event_payload() -> dict:
    return {
        "raw_input": "台积电更新资本开支指引，市场重新评估相关供应链。",
        "event_title": "台积电资本开支验证",
        "company_name": "台积电",
        "ticker": "TSM",
        "research_question": "资本开支是否会通过订单传导至供应链公司？",
        "candidate_factors": ["客户资本开支", "订单转化", "交付与毛利率"],
        "created_by": "tester",
    }


def test_market_expression_separates_reviewed_claims_observations_and_disclosed_holdings(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"broker-report").hexdigest(),
        source_url="https://licensed.example/report/tsm",
        title="供应链研报",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 12, "paragraph": 3}, verbatim_text="券商预计客户资本开支提升将带动订单。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="research_opinion", normalized_text="客户资本开支提升将带动订单", created_at=now)
    cmd_session.add(statement)
    cmd_session.flush()
    claim = ReportClaim(
        research_case_id=case_id,
        source_statement_id=statement.id,
        text="客户资本开支提升将带动订单",
        claim_kind="research_opinion",
        asserted_by="某券商",
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="保留为已审核的研报观点，不升级为披露事实。",
        reviewed_at=now,
        created_at=now,
    )
    cmd_session.add(claim)
    cmd_session.flush()
    factor = KeyFactor(
        research_case_id=case_id,
        report_claim_id=claim.id,
        name="客户资本开支转化为订单",
        expected_direction="positive",
        metric_name="订单金额",
        allowed_source_types=["company_disclosure", "licensed_provider"],
        verification_window_start=date(2026, 7, 1),
        verification_window_end=date(2026, 10, 31),
        support_condition="公司披露订单增长",
        refutation_condition="订单未增长或延期",
        next_verification_event="下一次财报",
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="指标和反证条件完整。",
        reviewed_at=now,
        created_at=now,
    )
    cmd_session.add(factor)
    cmd_session.flush()
    cmd_session.add(ClaimVerification(
        key_factor_id=factor.id,
        source_statement_id=statement.id,
        outcome="supported",
        rationale="披露订单同比增长。",
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="与冻结原文一致。",
        reviewed_at=now,
        created_at=now,
    ))
    company = Company(code="688000", name="供应链公司", type="listed", created_at=now)
    cmd_session.add(company)
    cmd_session.flush()
    stock = Stock(company_id=company.id, code="688000.SH", name="供应链公司", market="SSE", created_at=now)
    fund = Fund(code="000001", name="示例成长基金", fund_type="equity", created_at=now)
    cmd_session.add_all([stock, fund])
    cmd_session.flush()
    cmd_session.add(FundamentalImpact(
        research_case_id=case_id,
        key_factor_id=factor.id,
        company_id=company.id,
        stock_id=stock.id,
        metric_name="订单金额",
        expected_direction="positive",
        rationale="已审核因素的公司层传导关系。",
        source_statement_id=statement.id,
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="传导范围已确认。",
        reviewed_at=now,
        created_at=now,
    ))
    cmd_session.add(MarketObservation(
        research_case_id=case_id,
        key_factor_id=factor.id,
        stock_id=stock.id,
        event_at=datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc),
        available_at=now,
        window_label="T0 至 T+5",
        benchmark="中证全指",
        price_source="licensed_provider",
        relative_return=Decimal("0.034"),
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="仅保留市场观测，不作因果归因。",
        reviewed_at=now,
        created_at=now,
    ))
    cmd_session.add(HoldingDisclosure(
        fund_id=fund.id,
        stock_id=stock.id,
        weight=Decimal("0.056"),
        report_period=date(2026, 6, 30),
        published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        acquired_at=now,
        source="licensed_provider",
        created_at=now,
    ))
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/market-expression?as_of=2026-08-09&cutoff=2026-08-09T23:59:59Z"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["claims"][0]["claim_kind"] == "research_opinion"
    assert payload["claims"][0]["source"]["locator"] == {"page": 12, "paragraph": 3}
    assert payload["factors"][0]["verification"]["outcome"] == "supported"
    assert payload["factors"][0]["allowed_source_types"] == ["company_disclosure", "licensed_provider"]
    assert payload["fundamentals"][0]["metric_name"] == "订单金额"
    assert payload["market_observations"][0]["benchmark"] == "中证全指"
    assert "causal_result" not in payload["market_observations"][0]
    position = payload["fund_exposure"][0]["positions"][0]
    assert position["report_period"] == "2026-06-30"
    assert position["published_at"].startswith("2026-07-20")
    assert position["acquired_at"].startswith("2026-08-09")
    assert position["source"] == "licensed_provider"
    assert position["coverage_status"] == "not_recorded"
    assert position["freshness_status"] == "unknown"


def test_market_expression_excludes_machine_candidates_from_every_expression_layer(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    claim = ReportClaim(
        research_case_id=case_id,
        source_statement_id=None,
        text="未经审核的候选主张",
        claim_kind="forecast",
        asserted_by="AI",
        review_state="reviewed",
        reviewed_by="human:researcher",
        review_reason="因素本身已审核，但其上游研报主张仍未审核。",
        reviewed_at=now,
        created_at=now,
    )
    cmd_session.add(claim)
    cmd_session.flush()
    cmd_session.add(KeyFactor(
        research_case_id=case_id,
        report_claim_id=claim.id,
        name="未经审核因素",
        expected_direction="positive",
        metric_name="订单",
        allowed_source_types=["licensed_provider"],
        verification_window_start=None,
        verification_window_end=None,
        support_condition="支持",
        refutation_condition="反证",
        next_verification_event="财报",
        review_state="machine_generated",
        reviewed_by=None,
        review_reason=None,
        reviewed_at=None,
        created_at=now,
    ))
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-expression")

    assert response.status_code == 200
    assert response.json()["claims"] == []
    assert response.json()["factors"] == []
    assert response.json()["fundamentals"] == []
    assert response.json()["market_observations"] == []
    assert response.json()["fund_exposure"] == []


def test_market_expression_records_are_append_only(cmd_client, cmd_session) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    claim = ReportClaim(
        research_case_id=case_id,
        source_statement_id=None,
        text="候选记录",
        claim_kind="forecast",
        asserted_by="AI",
        review_state="machine_generated",
        reviewed_by=None,
        review_reason=None,
        reviewed_at=None,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    cmd_session.add(claim)
    cmd_session.commit()

    with pytest.raises(ImmutableLedgerError):
        cmd_session.execute(update(ReportClaim).where(ReportClaim.id == claim.id).values(text="被改写"))
