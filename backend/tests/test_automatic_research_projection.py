from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.automatic_research_conclusion import AutomaticConclusionProjection
from app.services.automatic_research_projection import (
    automatic_research_status,
    project_factor_judgement,
    project_automatic_research_progress,
    project_automatic_research_result,
)


def test_progress_projection_hides_status_stage_timing_and_counter_rules() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    run = SimpleNamespace(
        status="failed",
        stage="failed",
        stop_reason="no_usable_evidence",
        created_at=datetime(2026, 8, 17, 11, 59),
        updated_at=now,
    )
    lifecycle = SimpleNamespace(status="exhausted")
    job = SimpleNamespace(reference_count=4, exception_count=2, status="failed", stage="admitting")

    status = automatic_research_status(run, lifecycle)
    projection = project_automatic_research_progress(
        status=status,
        run=run,
        jobs=[job],
        run_events=[],
        acquisition_events=[],
        admitted_evidence_count=1,
        exception_reason_codes=["parser_failure", "parser_failure"],
        now=now,
    )

    assert projection.status == "failed"
    assert [stage.status for stage in projection.stages] == [
        "completed",
        "completed",
        "failed",
        "pending",
        "pending",
    ]
    assert projection.stats.model_dump() == {
        "source_count": 4,
        "admitted_evidence_count": 1,
        "skipped_count": 2,
        "duration_seconds": 60,
    }
    assert projection.exceptions[0].model_dump() == {
        "reason": "部分材料解析失败",
        "count": 2,
        "impact": "这些材料不会被用作本次结论依据。",
        "system_action": "系统已跳过异常材料，并继续处理其余可用来源。",
    }


def test_progress_projection_aggregates_raw_worker_events_into_user_narrative() -> None:
    now = datetime(2026, 8, 18, 12, 2, tzinfo=timezone.utc)
    run = SimpleNamespace(
        status="running",
        stage="retrieve",
        stop_reason=None,
        created_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
        updated_at=now,
    )
    jobs = [
        SimpleNamespace(
            id="one",
            reference_count=2,
            exception_count=0,
            status="succeeded",
            stage="succeeded",
            started_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 18, 12, 0, 30, tzinfo=timezone.utc),
            request_snapshot={"objective": "核验订单变化"},
        ),
        SimpleNamespace(
            id="two",
            reference_count=3,
            exception_count=0,
            status="partial",
            stage="partial",
            started_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 8, 18, 12, 1, 30, tzinfo=timezone.utc),
            request_snapshot={"objective": "核验供给约束"},
        ),
        SimpleNamespace(
            id="three",
            reference_count=1,
            exception_count=0,
            status="running",
            stage="fetching",
            started_at=datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc),
            finished_at=None,
            request_snapshot={"objective": "核验替代影响"},
        ),
    ]
    events = [
        SimpleNamespace(
            job_id="one",
            stage="extracting",
            status="running",
            message="extracting",
            created_at=datetime(2026, 8, 18, 12, 0, 10, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            job_id="two",
            stage="extracting",
            status="running",
            message="extracting",
            created_at=datetime(2026, 8, 18, 12, 0, 20, tzinfo=timezone.utc),
        ),
    ]

    projection = project_automatic_research_progress(
        status="running",
        run=run,
        jobs=jobs,
        run_events=[],
        acquisition_events=events,
        admitted_evidence_count=0,
        exception_reason_codes=[],
        now=now,
    )

    assert projection.narrative.model_dump() == {
        "current_action": "正在获取已找到的来源",
        "completed_count": 2,
        "total_count": 3,
        "next_action": "随后解析已获取的材料",
        "elapsed_seconds": 120,
        "estimated_remaining_seconds_min": 48,
        "estimated_remaining_seconds_max": 72,
    }
    assert projection.activities[0].label == "正在解析 2 个来源"
    assert projection.activities[0].count == 2
    assert projection.activities[0].technical_details[0].work_item == "核验供给约束"
    assert projection.activities[0].technical_details[0].internal_status == "extracting"


def test_factor_projection_refuses_legacy_or_invalid_key_judgement() -> None:
    assessment = SimpleNamespace(
        conclusion="supported",
        factor_judgement={
            "relevance": "direct",
            "causal_impact": "high",
            "evidence_strength": "strong",
            "counter_evidence": "none",
            "classification": "key",
            "ranking_reason": "模型遗漏了反证。",
        },
        gaps=["需要补充行业交叉验证"],
    )

    factor = project_factor_judgement(
        statement="供给约束",
        assessment=assessment,
        support_count=3,
        counter_evidence_count=1,
    )

    assert factor.classification == "pending"
    assert factor.ranking_reason == "尚未确认关键因素：存在反证或因素判断未通过完整校验。"
    assert factor.support_count == 3
    assert factor.counter_evidence_count == 1
    assert factor.evidence_gap == "需要补充行业交叉验证"


def test_result_projection_keeps_display_policy_and_deduplication_local() -> None:
    built = AutomaticConclusionProjection(
        text="结论",
        primary_factor=None,
        evidence_link_ids=(),
        key_findings=("发现", "发现"),
        limitations=("限制",),
    )
    link = SimpleNamespace(role="contradicts")
    statement = SimpleNamespace(normalized_text="反证")
    document = SimpleNamespace(title="公告", source_url="https://example.test/a")
    contract = SimpleNamespace(
        allow_display=True,
        effective_from=None,
        effective_until=None,
    )

    result = project_automatic_research_result(
        conclusion_text="结论",
        built=built,
        ordered_links=[
            (link, statement, document, contract),
            (link, statement, document, contract),
        ],
        at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert result.key_findings == ["发现"]
    assert result.counter_evidence == ["反证"]
    assert len(result.sources) == 1
    assert result.sources[0].title == "公告"
