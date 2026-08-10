"""Forecast verdict records must remain separate from ordinary claim review."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal


def test_forecast_verdict_records_are_immutable_ledger_entries() -> None:
    from app.models.ledger import Base, IMMUTABLE_TABLES
    from app.models.research_expression import (
        ActualMetricObservation,
        ForecastEvaluationCandidate,
        ForecastTargetVersion,
        ForecastVerdict,
    )

    records = (
        ForecastTargetVersion,
        ActualMetricObservation,
        ForecastEvaluationCandidate,
        ForecastVerdict,
    )
    assert {record.__tablename__ for record in records} <= set(Base.metadata.tables)
    assert {record.__tablename__ for record in records} <= IMMUTABLE_TABLES


def test_numeric_forecast_candidate_preserves_rule_and_inputs() -> None:
    from app.services.forecast_verdicts import evaluate_numeric_forecast

    result = evaluate_numeric_forecast(
        expected_value=Decimal("455000000"),
        actual_value=Decimal("247245713.03"),
        comparator="within_tolerance",
        relative_tolerance=Decimal("0.10"),
    )

    assert result.outcome == "contradicted"
    assert result.rule_version == "forecast-numeric-v1"
    assert result.inputs == {
        "expected_value": "455000000",
        "actual_value": "247245713.03",
        "comparator": "within_tolerance",
        "relative_tolerance": "0.10",
    }


def test_service_freezes_matching_admitted_forecast_evidence(cmd_client, cmd_session) -> None:
    from app.models.ledger import CaseDocumentVersion, DocumentVersion, SourceSpan, SourceStatement
    from app.models.research_expression import KeyFactor, ReportClaim
    from app.services.forecast_verdicts import (
        ActualObservationInput,
        ForecastTargetInput,
        ForecastVerdictInput,
        ForecastVerdictService,
    )
    from app.services.source_governance import SourceGovernanceService

    case_response = cmd_client.post("/api/v1/event-research", json={
        "raw_input": "验证历史研报的利润预测",
        "event_title": "火星人利润预测验证",
        "company_name": "火星人",
        "ticker": "300894.SZ",
        "research_question": "预测是否兑现？",
        "candidate_factors": ["归母净利润", "毛利率", "渠道费用"],
        "created_by": "tester",
    })
    assert case_response.status_code == 201, case_response.text
    case_id = uuid.UUID(case_response.json()["case_id"])
    forecast_at = datetime(2023, 4, 25, 8, 0, tzinfo=timezone.utc)
    actual_at = datetime(2024, 4, 22, 8, 0, tzinfo=timezone.utc)
    documents = []
    for title, available_at, digest in (
        ("冻结券商预测", forecast_at, "a" * 64),
        ("冻结公司年报", actual_at, "b" * 64),
    ):
        document = DocumentVersion(
            content_sha256=digest,
            source_url=f"https://example.test/{title}",
            title=title,
            available_at=available_at,
            acquired_at=available_at,
            parser_version="fixture-v1",
            parse_state="success",
        )
        cmd_session.add(document)
        cmd_session.flush()
        SourceGovernanceService(cmd_session).record_event_intake(
            document=document,
            source_type="licensed_provider",
            source_metadata={"provider_name": "fixture", "permissions": {"ai_processing": True, "display": True}},
            declared_by="tester",
        )
        cmd_session.add(CaseDocumentVersion(
            research_case_id=case_id,
            document_version_id=document.id,
            linked_at=available_at,
        ))
        documents.append(document)
    forecast_span = SourceSpan(document_version_id=documents[0].id, locator={"page": 1}, verbatim_text="预计归母净利润455百万元")
    actual_span = SourceSpan(document_version_id=documents[1].id, locator={"page": 123}, verbatim_text="归母净利润247245713.03元")
    cmd_session.add_all([forecast_span, actual_span])
    cmd_session.flush()
    forecast_statement = SourceStatement(source_span_id=forecast_span.id, kind="forecast", normalized_text="预计2023年归母净利润455百万元", created_at=forecast_at)
    actual_statement = SourceStatement(source_span_id=actual_span.id, kind="disclosed_fact", normalized_text="2023年归母净利润247245713.03元", created_at=actual_at)
    cmd_session.add_all([forecast_statement, actual_statement])
    cmd_session.flush()
    claim = ReportClaim(
        research_case_id=case_id,
        source_statement_id=forecast_statement.id,
        text="预计2023年归母净利润455百万元",
        claim_kind="forecast",
        asserted_period=date(2023, 12, 31),
        asserted_by="券商",
        review_state="reviewed",
        reviewed_by="human:reviewer",
        review_reason="冻结预测，不升级为公司事实。",
        reviewed_at=forecast_at,
        created_at=forecast_at,
    )
    cmd_session.add(claim)
    cmd_session.flush()
    factor = KeyFactor(
        research_case_id=case_id,
        report_claim_id=claim.id,
        name="2023年归母净利润预测",
        expected_direction="positive",
        metric_name="归母净利润",
        allowed_source_types=["company_disclosure"],
        verification_window_start=date(2023, 1, 1),
        verification_window_end=date(2023, 12, 31),
        support_condition="实际值处于容差内",
        refutation_condition="实际值超出容差",
        next_verification_event="2023年年度报告",
        review_state="reviewed",
        reviewed_by="human:reviewer",
        review_reason="预测与窗口均已审核。",
        reviewed_at=forecast_at,
        created_at=forecast_at,
    )
    cmd_session.add(factor)
    cmd_session.commit()

    service = ForecastVerdictService(cmd_session)
    target = service.create_target(case_id, ForecastTargetInput(
        key_factor_id=factor.id,
        report_claim_id=claim.id,
        forecast_source_statement_id=forecast_statement.id,
        baseline_source_statement_id=forecast_statement.id,
        metric_name="归母净利润",
        entity_key="300894.SZ",
        baseline_value=Decimal("315000000"),
        expected_value=Decimal("455000000"),
        unit="CNY",
        forecast_period_start=date(2023, 1, 1),
        forecast_period_end=date(2023, 12, 31),
        comparator="within_tolerance",
        relative_tolerance=Decimal("0.10"),
        reviewed_by="human:reviewer",
        review_reason="冻结研报表格数值。",
    ))
    actual = service.record_actual(target.id, ActualObservationInput(
        source_statement_id=actual_statement.id,
        entity_key="300894.SZ",
        observed_value=Decimal("247245713.03"),
        unit="CNY",
        observed_period_start=date(2023, 1, 1),
        observed_period_end=date(2023, 12, 31),
        available_at=actual_at,
        recorded_by="human:reviewer",
        record_reason="年报第123页审计口径。",
    ))
    candidate = service.evaluate(target.id, actual.id, cutoff=datetime(2024, 4, 22, 23, 59, tzinfo=timezone.utc))
    verdict = service.create_verdict(candidate.id, ForecastVerdictInput(
        decision="confirmed",
        outcome=None,
        reason="实际值显著低于冻结预测，确认未兑现。",
        reviewed_by="human:reviewer",
    ))

    assert candidate.outcome == "contradicted"
    assert candidate.review_state == "machine_generated"
    assert verdict.outcome == "contradicted"
