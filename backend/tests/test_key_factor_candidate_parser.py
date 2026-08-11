from datetime import date

from app.services.key_factor_candidates import KeyFactorCandidateParser


def test_parser_proposes_a_frozen_numeric_forecast_factor_from_report_text() -> None:
    result = KeyFactorCandidateParser().parse(
        "海通证券预计工业富联2024年归母净利润为251.49亿元。"
    )

    assert result.parser_version == "key-factor-rules-v1"
    assert result.skipped_reason is None
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.name == "2024 年归母净利润预测兑现"
    assert candidate.metric_name == "归母净利润"
    assert candidate.expected_direction == "neutral"
    assert candidate.verification_window_start == date(2024, 1, 1)
    assert candidate.verification_window_end == date(2024, 12, 31)
    assert candidate.evidence_excerpt == "预计工业富联2024年归母净利润为251.49亿元"
    assert candidate.rule_id == "cn_numeric_forecast_v1"


def test_parser_does_not_invent_a_measurable_factor_from_a_vague_opinion() -> None:
    result = KeyFactorCandidateParser().parse("研报认为 AI 业务表现强劲。")

    assert result.candidates == ()
    assert result.skipped_reason == "原文未出现可冻结的数值预测或明确业务指标；未生成候选因素。"


def test_parser_proposes_source_bound_business_growth_factors_with_statement_period() -> None:
    result = KeyFactorCandidateParser().parse(
        "云计算业务收入3193.77亿元，同比增长64.37%；AI服务器收入同比超过150%；"
        "400G、800G高速交换机同比增长数倍。",
        observed_period=date(2024, 12, 31),
    )

    assert result.skipped_reason is None
    assert [(candidate.name, candidate.metric_name) for candidate in result.candidates] == [
        ("2024 年云计算业务收入同比增长", "云计算业务收入"),
        ("2024 年AI服务器收入同比增长", "AI服务器收入"),
        ("2024 年400G、800G高速交换机同比增长", "400G、800G高速交换机"),
    ]
    assert all(candidate.expected_direction == "positive" for candidate in result.candidates)
    assert all(candidate.verification_window_start == date(2024, 1, 1) for candidate in result.candidates)
    assert all(candidate.verification_window_end == date(2024, 12, 31) for candidate in result.candidates)
    assert [candidate.evidence_excerpt for candidate in result.candidates] == [
        "云计算业务收入3193.77亿元，同比增长64.37%",
        "AI服务器收入同比超过150%",
        "400G、800G高速交换机同比增长数倍",
    ]
    assert all(candidate.rule_id == "cn_business_growth_v1" for candidate in result.candidates)
