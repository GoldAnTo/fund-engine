from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.automatic_research_conclusion import AutomaticConclusionProjection
from app.services.automatic_research_projection import (
    automatic_research_status,
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
        "stage": "parse",
        "count": 2,
    }


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
