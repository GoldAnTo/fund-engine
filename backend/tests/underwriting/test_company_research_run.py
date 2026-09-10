import pytest

from app.underwriting.domain.company_research_run import (
    ResearchRunPreparation,
    ResearchRunStageStatus,
    ResearchRunStatus,
    project_research_run,
    project_research_run_status,
)


def test_exports_run_projection_types_from_the_domain_package() -> None:
    from app.underwriting.domain import (
        ResearchRunPreparation,
        ResearchRunProjection,
        ResearchRunProjectionError,
        ResearchRunStage,
        ResearchRunStageStatus,
        ResearchRunStatus,
        project_research_run,
        project_research_run_status,
    )

    assert ResearchRunPreparation.__name__ == "ResearchRunPreparation"
    assert ResearchRunProjection.__name__ == "ResearchRunProjection"
    assert ResearchRunProjectionError.__name__ == "ResearchRunProjectionError"
    assert ResearchRunStage.__name__ == "ResearchRunStage"
    assert ResearchRunStageStatus.__name__ == "ResearchRunStageStatus"
    assert ResearchRunStatus.__name__ == "ResearchRunStatus"
    assert callable(project_research_run)
    assert callable(project_research_run_status)


@pytest.mark.parametrize(
    ("internal_status", "progress", "product_status"),
    [
        ("queued", 0, ResearchRunStatus.QUEUED),
        ("preparing_sources", 10, ResearchRunStatus.COLLECTING_SOURCES),
        ("building_model", 35, ResearchRunStatus.ANALYZING_COMPANY),
        ("building_model", 60, ResearchRunStatus.BUILDING_FORECAST),
        ("building_model", 80, ResearchRunStatus.GENERATING_REPORT),
        ("awaiting_judgment_review", 85, ResearchRunStatus.NEEDS_INPUT),
        ("ready_to_freeze", 95, ResearchRunStatus.COMPLETED),
        ("completed", 100, ResearchRunStatus.COMPLETED),
        ("recoverable_failure", 30, ResearchRunStatus.FAILED),
    ],
)
def test_projects_internal_lifecycle_to_closed_product_status(
    internal_status: str, progress: int, product_status: ResearchRunStatus
) -> None:
    assert (
        project_research_run_status(
            internal_status=internal_status,
            progress=progress,
            error_code=None,
        )
        is product_status
    )


def test_projects_ordered_stages_from_validated_lifecycle_inputs() -> None:
    projection = project_research_run(
        ResearchRunPreparation("awaiting_judgment_review", 85, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "evidence_reviewed",
            "model_stage_claimed",
        ),
        has_machine_memo=True,
        confirmation_complete=False,
    )

    assert projection.status is ResearchRunStatus.NEEDS_INPUT
    assert tuple(stage.key for stage in projection.stages) == (
        "identity",
        "sources",
        "analysis",
        "forecast",
        "report",
    )
    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.NEEDS_INPUT,
    )


def test_projects_a_governed_source_gap_to_the_source_stage() -> None:
    projection = project_research_run(
        ResearchRunPreparation("blocked", 10, "missing_critical_baseline"),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_preparation_blocked",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.status is ResearchRunStatus.NEEDS_INPUT
    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.NEEDS_INPUT,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
    )


def test_projects_a_governed_model_gap_without_a_machine_memo() -> None:
    projection = project_research_run(
        ResearchRunPreparation("blocked", 35, "missing_key_baseline"),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_preparation_blocked",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.NEEDS_INPUT,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
    )


@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("missing_critical_baseline", ResearchRunStatus.NEEDS_INPUT),
        ("permission_denied", ResearchRunStatus.FAILED),
        ("company_research_source_unavailable", ResearchRunStatus.FAILED),
        ("alphabet_source_unavailable", ResearchRunStatus.FAILED),
        ("integrity_failed", ResearchRunStatus.FAILED),
        ("model_provider_failed", ResearchRunStatus.FAILED),
    ],
)
def test_projects_blocked_runs_only_from_closed_error_semantics(
    error_code: str, expected_status: ResearchRunStatus
) -> None:
    assert (
        project_research_run_status(
            internal_status="blocked", progress=30, error_code=error_code
        )
        is expected_status
    )


@pytest.mark.parametrize("error_code", (None, "raw_provider_error"))
def test_rejects_blocked_runs_without_a_governed_error_semantic(
    error_code: str | None,
) -> None:
    with pytest.raises(ValueError, match="known error code"):
        project_research_run_status(
            internal_status="blocked", progress=30, error_code=error_code
        )


def test_process_projection_exposes_only_safe_typed_messages() -> None:
    projection = project_research_run(
        ResearchRunPreparation("recoverable_failure", 30, "provider_unavailable"),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_provider_failed",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )
    assert tuple((entry.code, entry.message) for entry in projection.process) == (
        ("initialized", "Research run initialized"),
        ("source_stage_claimed", "Source collection started"),
        ("source_provider_failed", "Source provider unavailable"),
    )


@pytest.mark.parametrize(
    "unsafe_event_type",
    (
        "TimeoutError: Authorization: Bearer super-secret",
        '{"api_key":"super-secret","query":"private company request"}',
        "/Users/analyst/private/company-source.pdf",
    ),
)
def test_rejects_raw_process_text_instead_of_projecting_it(
    unsafe_event_type: str,
) -> None:
    with pytest.raises(ValueError, match="process events are invalid"):
        project_research_run(
            ResearchRunPreparation("queued", 0, None),
            event_types=("initialized", unsafe_event_type),
            has_machine_memo=False,
            confirmation_complete=False,
        )


def test_rejects_a_queued_projection_with_started_source_work() -> None:
    with pytest.raises(ValueError, match="history is inconsistent"):
        project_research_run(
            ResearchRunPreparation("queued", 0, None),
            event_types=("initialized", "source_stage_claimed"),
            has_machine_memo=False,
            confirmation_complete=False,
        )


def test_rejects_a_blocked_source_failure_at_a_terminal_progress() -> None:
    with pytest.raises(ValueError, match="progress is inconsistent"):
        project_research_run(
            ResearchRunPreparation("blocked", 100, "missing_critical_baseline"),
            event_types=(
                "initialized",
                "source_stage_claimed",
                "source_preparation_blocked",
            ),
            has_machine_memo=False,
            confirmation_complete=False,
        )


def test_projects_the_current_model_failure_after_source_recovery() -> None:
    projection = project_research_run(
        ResearchRunPreparation("recoverable_failure", 30, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_provider_failed",
            "retry_queued",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_provider_failed",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.FAILED,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
    )


def test_projects_a_blocked_model_provider_failure_at_analysis_checkpoint() -> None:
    projection = project_research_run(
        ResearchRunPreparation("blocked", 35, "provider_unavailable"),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_provider_failed",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.status is ResearchRunStatus.FAILED
    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.FAILED,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
    )


def test_projects_the_current_model_input_gap_after_source_recovery() -> None:
    projection = project_research_run(
        ResearchRunPreparation("blocked", 35, "missing_key_baseline"),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_preparation_blocked",
            "retry_queued",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_preparation_blocked",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert tuple(stage.status for stage in projection.stages) == (
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.COMPLETED,
        ResearchRunStageStatus.NEEDS_INPUT,
        ResearchRunStageStatus.PENDING,
        ResearchRunStageStatus.PENDING,
    )


@pytest.mark.parametrize(
    "event_types",
    (
        (
            "initialized",
            "source_stage_claimed",
            "source_preparation_blocked",
        ),
        (
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_preparation_blocked",
        ),
    ),
)
def test_rejects_blocked_only_terminal_events_for_recoverable_failures(
    event_types: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="history is inconsistent"):
        project_research_run(
            ResearchRunPreparation("recoverable_failure", 30, None),
            event_types=event_types,
            has_machine_memo=False,
            confirmation_complete=False,
        )


def test_projects_an_authentic_completed_publication_chain() -> None:
    projection = project_research_run(
        ResearchRunPreparation("completed", 100, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "evidence_reviewed",
            "model_stage_claimed",
            "judgment_confirmed",
            "company_research_published",
        ),
        has_machine_memo=True,
        confirmation_complete=True,
    )

    assert projection.status is ResearchRunStatus.COMPLETED
    assert tuple(entry.message for entry in projection.process) == (
        "Research run initialized",
        "Source collection started",
        "Source collection completed",
        "Source review recorded",
        "Company analysis started",
        "Judgment confirmed",
        "Research report published",
    )


def test_accepts_historical_basis_recovery_then_model_retry() -> None:
    projection = project_research_run(
        ResearchRunPreparation("building_model", 35, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_preparation_blocked",
            "historical_basis_recovered",
            "retry_queued",
            "model_stage_claimed",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.process[-2].message == "Research retry queued"


def test_accepts_manual_source_retry_after_provider_failure() -> None:
    projection = project_research_run(
        ResearchRunPreparation("preparing_sources", 10, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_provider_failed",
            "retry_queued",
            "source_stage_claimed",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.process[-1].message == "Source collection started"


@pytest.mark.parametrize(
    "event_types",
    (
        ("initialized", "retry_queued"),
        ("initialized", "source_claim_recovered"),
        (
            "initialized",
            "source_stage_claimed",
            "source_provider_failed",
            "model_stage_claimed",
        ),
        ("initialized", "model_stage_claimed"),
        (
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "judgment_confirmed",
            "company_research_published",
            "evidence_reviewed",
        ),
    ),
)
def test_rejects_orphan_or_illegal_lifecycle_transitions(
    event_types: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="history is inconsistent"):
        project_research_run(
            ResearchRunPreparation("completed", 100, None),
            event_types=event_types,
            has_machine_memo=True,
            confirmation_complete=True,
        )


def test_projects_a_recovered_source_claim_while_queued() -> None:
    projection = project_research_run(
        ResearchRunPreparation("queued", 0, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "source_claim_recovered",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.status is ResearchRunStatus.QUEUED


def test_projects_a_recovered_model_claim_at_its_persisted_checkpoint() -> None:
    projection = project_research_run(
        ResearchRunPreparation("building_model", 25, None),
        event_types=(
            "initialized",
            "source_stage_claimed",
            "evidence_index_prepared",
            "model_stage_claimed",
            "model_claim_recovered",
        ),
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.status is ResearchRunStatus.ANALYZING_COMPANY


@pytest.mark.parametrize(
    ("preparation", "event_types"),
    (
        (
            ResearchRunPreparation("preparing_sources", 10, None),
            (
                "initialized",
                "source_stage_claimed",
                "source_provider_failed",
                "source_stage_claimed",
            ),
        ),
        (
            ResearchRunPreparation("building_model", 35, None),
            (
                "initialized",
                "source_stage_claimed",
                "evidence_index_prepared",
                "model_stage_claimed",
                "model_provider_failed",
                "model_stage_claimed",
            ),
        ),
    ),
)
def test_accepts_same_stage_automatic_provider_retries(
    preparation: ResearchRunPreparation, event_types: tuple[str, ...]
) -> None:
    projection = project_research_run(
        preparation,
        event_types=event_types,
        has_machine_memo=False,
        confirmation_complete=False,
    )

    assert projection.status in {
        ResearchRunStatus.COLLECTING_SOURCES,
        ResearchRunStatus.ANALYZING_COMPANY,
    }
