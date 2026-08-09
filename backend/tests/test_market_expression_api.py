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
from app.services.source_governance import SourceGovernanceService


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
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
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
        after_hours_treatment="未记录盘后处理",
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
        report_period=date(2025, 12, 31),
        published_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
        acquired_at=now,
        source="licensed_provider",
        source_document_version_id=document.id,
        coverage_status="complete",
        created_at=now,
    ))
    unlinked_document = DocumentVersion(
        content_sha256=hashlib.sha256(b"other-case-fund-disclosure").hexdigest(),
        source_url="https://licensed.example/funds/other-case",
        title="其他 Case 的基金披露",
        available_at=now,
        acquired_at=now,
        parser_version="provider-v1",
        parse_state="success",
    )
    cmd_session.add(unlinked_document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=unlinked_document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    other_fund = Fund(code="000002", name="其他 Case 基金", fund_type="equity", created_at=now)
    cmd_session.add(other_fund)
    cmd_session.flush()
    cmd_session.add(HoldingDisclosure(
        fund_id=other_fund.id,
        stock_id=stock.id,
        weight=Decimal("0.032"),
        report_period=date(2025, 12, 31),
        published_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
        acquired_at=now,
        source="licensed_provider",
        source_document_version_id=unlinked_document.id,
        coverage_status="complete",
        created_at=now,
    ))
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/research-cases/{case_id}/market-expression?as_of=2026-08-09&cutoff=2026-08-09T23:59:59Z"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["claims"][0]["claim_kind"] == "research_opinion"
    assert payload["claims"][0]["source"]["document_version_id"] == str(document.id)
    assert payload["claims"][0]["source"]["permission_status"] == "admitted"
    assert payload["claims"][0]["source"]["locator"] == {"page": 12, "paragraph": 3}
    assert payload["factors"][0]["verification"]["outcome"] == "supported"
    assert payload["factors"][0]["allowed_source_types"] == ["company_disclosure", "licensed_provider"]
    assert payload["fundamentals"][0]["metric_name"] == "订单金额"
    assert payload["market_observations"][0]["benchmark"] == "中证全指"
    assert "causal_result" not in payload["market_observations"][0]
    position = next(item for item in payload["fund_exposure"] if item["fund_code"] == "000001")["positions"][0]
    assert position["report_period"] == "2025-12-31"
    assert position["published_at"].startswith("2026-01-20")
    assert position["acquired_at"].startswith("2026-08-09")
    assert position["source"] == "licensed_provider"
    assert position["coverage_status"] == "complete"
    assert position["source_document_version_id"] == str(document.id)
    assert position["source_visible_in_case"] is True
    assert position["freshness_status"] == "stale_disclosure"
    # Complete coverage does not rescue an expired disclosure.  Do not promote
    # it into a precise current fund exposure.
    assert next(item for item in payload["fund_exposure"] if item["fund_code"] == "000001")["disclosed_exposure"] is None
    unlinked_position = next(item for item in payload["fund_exposure"] if item["fund_code"] == "000002")["positions"][0]
    assert unlinked_position["source_document_version_id"] == str(unlinked_document.id)
    assert unlinked_position["source_visible_in_case"] is False


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


def test_researcher_can_register_a_reviewed_claim_and_key_factor_from_an_admitted_case_source(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"reviewed-source").hexdigest(),
        source_url="https://licensed.example/report/reviewed-source",
        title="已准入研报",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 8, "paragraph": 2}, verbatim_text="管理层预计下半年订单加速。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="research_opinion", normalized_text="管理层预计下半年订单加速", created_at=now)
    cmd_session.add(statement)
    cmd_session.commit()

    options = cmd_client.get(f"/api/v1/research-cases/{case_id}/source-statements")

    assert options.status_code == 200
    assert len(options.json()["items"]) == 1
    source = options.json()["items"][0]
    assert source["id"] == str(statement.id)
    assert source["document_version_id"] == str(document.id)
    assert source["locator"] == {"page": 8, "paragraph": 2}
    assert source["available_at"].startswith("2026-08-09T09:00:00")
    assert source["permission_status"] == "admitted"

    claim_response = cmd_client.post(f"/api/v1/research-cases/{case_id}/report-claims", json={
        "source_statement_id": str(statement.id),
        "text": "管理层预计下半年订单加速",
        "claim_kind": "research_opinion",
        "asserted_by": "管理层（经研究员转述）",
        "reviewed_by": "human:researcher",
        "review_reason": "逐句核对冻结原文，作为研究意见保留。",
    })

    assert claim_response.status_code == 201
    claim = claim_response.json()
    assert claim["source"]["source_statement_id"] == str(statement.id)
    assert claim["review_reason"] == "逐句核对冻结原文，作为研究意见保留。"

    factor_response = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors", json={
        "report_claim_id": claim["id"],
        "name": "下半年订单增速",
        "expected_direction": "positive",
        "metric_name": "订单同比增速",
        "allowed_source_types": ["company_disclosure", "licensed_provider"],
        "verification_window_start": "2026-07-01",
        "verification_window_end": "2026-12-31",
        "support_condition": "公司在定期报告中披露订单同比增长。",
        "refutation_condition": "订单增速未达预期或出现延后。",
        "next_verification_event": "2026 年三季报",
        "reviewed_by": "human:researcher",
        "review_reason": "指标、窗口和反证条件均已明确。",
    })

    assert factor_response.status_code == 201
    factor = factor_response.json()
    assert factor["report_claim_id"] == claim["id"]
    assert factor["allowed_source_types"] == ["company_disclosure", "licensed_provider"]
    assert factor["verification"] is None


def test_researcher_can_append_a_verification_to_a_reviewed_key_factor(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"verification-source").hexdigest(),
        source_url="https://licensed.example/disclosure/orders",
        title="订单披露",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 4}, verbatim_text="订单同比增长 20%。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="disclosed_fact", normalized_text="订单同比增长 20%", created_at=now)
    factor = KeyFactor(
        research_case_id=case_id, report_claim_id=None, thesis_id=None,
        name="订单同比增速", expected_direction="positive", metric_name="订单同比增速",
        allowed_source_types=["company_disclosure"], verification_window_start=None,
        verification_window_end=None, support_condition="订单增长", refutation_condition="订单下降",
        next_verification_event="下一次财报", review_state="reviewed",
        reviewed_by="human:researcher", review_reason="口径已固定", reviewed_at=now, created_at=now,
    )
    cmd_session.add_all([statement, factor])
    cmd_session.commit()

    response = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors/{factor.id}/verifications", json={
        "source_statement_id": str(statement.id),
        "outcome": "supported",
        "rationale": "冻结披露中的订单同比增长满足支持条件。",
        "reviewed_by": "human:researcher",
        "review_reason": "已核对期间、指标和原文定位。",
    })

    assert response.status_code == 201
    assert response.json()["outcome"] == "supported"
    assert response.json()["source"]["document_version_id"] == str(document.id)


def test_researcher_can_bind_a_case_to_an_explicit_company_and_stock_from_an_admitted_source(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"instrument-binding-source").hexdigest(),
        source_url="https://licensed.example/disclosure/supplier",
        title="供应商订单披露",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 3, "paragraph": 1}, verbatim_text="供应商确认进入本期订单范围。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="disclosed_fact", normalized_text="供应商确认进入本期订单范围", created_at=now)
    company = Company(code="688001", name="供应链公司", type="listed", created_at=now)
    cmd_session.add_all([statement, company])
    cmd_session.flush()
    stock = Stock(company_id=company.id, code="688001.SH", name="供应链公司", market="SSE", created_at=now)
    cmd_session.add(stock)
    cmd_session.commit()

    response = cmd_client.post(f"/api/v1/research-cases/{case_id}/market-instruments", json={
        "company_id": str(company.id),
        "stock_id": str(stock.id),
        "source_statement_id": str(statement.id),
        "relationship_role": "supply_chain",
        "reviewed_by": "human:researcher",
        "review_reason": "原文明确提及该公司与订单传导范围。",
    })

    assert response.status_code == 201
    binding = response.json()
    assert binding["company_id"] == str(company.id)
    assert binding["stock_code"] == "688001.SH"
    assert binding["source"]["locator"] == {"page": 3, "paragraph": 1}

    listed = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-instruments")

    assert listed.status_code == 200
    assert listed.json()["items"] == [binding]


def test_researcher_can_append_a_source_backed_fundamental_impact_only_after_binding_the_instrument(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"fundamental-impact-source").hexdigest(),
        source_url="https://licensed.example/disclosure/fundamental-impact",
        title="订单与收入披露",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 6}, verbatim_text="订单增长将确认在后续收入中。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="disclosed_fact", normalized_text="订单增长将确认在后续收入中", created_at=now)
    factor = KeyFactor(
        research_case_id=case_id, report_claim_id=None, thesis_id=None,
        name="订单转收入", expected_direction="positive", metric_name="收入同比增速",
        allowed_source_types=["company_disclosure"], verification_window_start=None,
        verification_window_end=None, support_condition="收入增长", refutation_condition="收入未增长",
        next_verification_event="下一次财报", review_state="reviewed",
        reviewed_by="human:researcher", review_reason="口径已固定", reviewed_at=now, created_at=now,
    )
    company = Company(code="688002", name="传导公司", type="listed", created_at=now)
    cmd_session.add_all([statement, factor, company])
    cmd_session.flush()
    stock = Stock(company_id=company.id, code="688002.SH", name="传导公司", market="SSE", created_at=now)
    cmd_session.add(stock)
    cmd_session.commit()

    unbound = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors/{factor.id}/fundamental-impacts", json={
        "market_instrument_binding_id": str(uuid.uuid4()),
        "source_statement_id": str(statement.id),
        "metric_name": "收入同比增速",
        "expected_direction": "positive",
        "rationale": "未审核标的不应进入传导。",
        "reviewed_by": "human:researcher",
        "review_reason": "尝试绕过标的绑定。",
    })
    assert unbound.status_code == 422

    binding = cmd_client.post(f"/api/v1/research-cases/{case_id}/market-instruments", json={
        "company_id": str(company.id), "stock_id": str(stock.id),
        "source_statement_id": str(statement.id), "relationship_role": "supply_chain",
        "reviewed_by": "human:researcher", "review_reason": "冻结原文明确该公司处于订单传导范围。",
    })
    assert binding.status_code == 201

    response = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors/{factor.id}/fundamental-impacts", json={
        "market_instrument_binding_id": binding.json()["id"],
        "source_statement_id": str(statement.id),
        "metric_name": "收入同比增速",
        "expected_direction": "positive",
        "rationale": "订单增长通过履约和确认节奏传导至收入。",
        "reviewed_by": "human:researcher",
        "review_reason": "已核对标的关系、指标口径和原文定位。",
    })

    assert response.status_code == 201
    impact = response.json()
    assert impact["company_id"] == str(company.id)
    assert impact["stock_id"] == str(stock.id)
    assert impact["metric_name"] == "收入同比增速"

    expression = cmd_client.get(f"/api/v1/research-cases/{case_id}/market-expression")

    assert expression.status_code == 200
    assert expression.json()["fundamentals"] == [impact]


def test_market_instrument_catalog_searches_only_explicit_ledger_instruments(cmd_client, cmd_session) -> None:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    company = Company(code="688003", name="可选择的公司", type="listed", created_at=now)
    cmd_session.add(company)
    cmd_session.flush()
    cmd_session.add(Stock(company_id=company.id, code="688003.SH", name="可选择的公司", market="SSE", created_at=now))
    cmd_session.commit()

    response = cmd_client.get("/api/v1/market-instruments?query=688003")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["company_id"] == str(company.id)
    assert item["company_code"] == "688003"
    assert item["stocks"][0]["code"] == "688003.SH"


def test_researcher_can_append_a_reviewed_market_observation_from_a_stock_binding(
    cmd_client, cmd_session
) -> None:
    case_id = uuid.UUID(cmd_client.post("/api/v1/event-research", json=_event_payload()).json()["case_id"])
    now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=hashlib.sha256(b"market-observation-source").hexdigest(),
        source_url="https://licensed.example/disclosure/market-observation",
        title="事件与标的披露",
        available_at=now,
        acquired_at=now,
        parser_version="docling-v1",
        parse_state="success",
    )
    cmd_session.add(document)
    cmd_session.flush()
    SourceGovernanceService(cmd_session).record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata={"provider_name": "licensed.example", "permissions": {"ai_processing": True, "display": True}},
        declared_by="tester",
    )
    cmd_session.add(CaseDocumentVersion(research_case_id=case_id, document_version_id=document.id, linked_at=now))
    span = SourceSpan(document_version_id=document.id, locator={"page": 9}, verbatim_text="公司披露订单变化。")
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(source_span_id=span.id, kind="disclosed_fact", normalized_text="公司披露订单变化", created_at=now)
    factor = KeyFactor(
        research_case_id=case_id, report_claim_id=None, thesis_id=None,
        name="订单变化", expected_direction="positive", metric_name="订单同比增速",
        allowed_source_types=["company_disclosure"], verification_window_start=None,
        verification_window_end=None, support_condition="订单增长", refutation_condition="订单下降",
        next_verification_event="下一次财报", review_state="reviewed",
        reviewed_by="human:researcher", review_reason="口径已固定", reviewed_at=now, created_at=now,
    )
    company = Company(code="688004", name="观测公司", type="listed", created_at=now)
    cmd_session.add_all([statement, factor, company])
    cmd_session.flush()
    stock = Stock(company_id=company.id, code="688004.SH", name="观测公司", market="SSE", created_at=now)
    cmd_session.add(stock)
    cmd_session.commit()
    binding = cmd_client.post(f"/api/v1/research-cases/{case_id}/market-instruments", json={
        "company_id": str(company.id), "stock_id": str(stock.id),
        "source_statement_id": str(statement.id), "relationship_role": "directly_affected",
        "reviewed_by": "human:researcher", "review_reason": "已审核该股票适用于本 Case。",
    })
    assert binding.status_code == 201

    response = cmd_client.post(f"/api/v1/research-cases/{case_id}/key-factors/{factor.id}/market-observations", json={
        "market_instrument_binding_id": binding.json()["id"],
        "event_at": "2026-08-08T20:00:00Z",
        "available_at": "2026-08-09T00:00:00Z",
        "window_label": "T0 至 T+5",
        "benchmark": "中证全指",
        "price_source": "licensed_provider",
        "after_hours_treatment": "事件发生在盘后，窗口从下一交易日开盘开始",
        "relative_return": 0.034,
        "reviewed_by": "human:researcher",
        "review_reason": "只核对窗口、基准和价格来源，不作因果归因。",
    })

    assert response.status_code == 201
    observation = response.json()
    assert observation["stock_id"] == str(stock.id)
    assert observation["window_label"] == "T0 至 T+5"
    assert observation["relative_return"] == 0.034
    assert observation["after_hours_treatment"] == "事件发生在盘后，窗口从下一交易日开盘开始"
    assert "causal_result" not in observation
