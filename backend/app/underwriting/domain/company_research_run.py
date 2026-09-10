"""Fail-closed product projections for company-research preparation runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchRunProjectionError(ValueError):
    """A persisted preparation cannot safely produce a product run projection."""


class ResearchRunStatus(StrEnum):
    QUEUED = "queued"
    COLLECTING_SOURCES = "collecting_sources"
    ANALYZING_COMPANY = "analyzing_company"
    BUILDING_FORECAST = "building_forecast"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class ResearchRunStageStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


NEEDS_INPUT_ERROR_CODES = frozenset(
    {
        "critical_input_pending",
        "missing_critical_baseline",
        "critical_input_unknown",
        "conflicting_sources",
        "missing_key_baseline",
        "mechanism_unidentified",
        "missing_market_bridge",
    }
)

FAILED_ERROR_CODES = frozenset(
    {
        "permission_denied",
        "source_unavailable",
        "company_research_source_unavailable",
        "alphabet_source_unavailable",
        "provider_unavailable",
        "model_provider_failed",
        "validation_failed",
        "integrity_failed",
        "stale_output_discarded",
    }
)


@dataclass(frozen=True, slots=True)
class ResearchRunPreparation:
    """The minimal trusted lifecycle state needed for a run projection."""

    internal_status: str
    progress: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ResearchRunStage:
    key: str
    status: ResearchRunStageStatus


@dataclass(frozen=True, slots=True)
class ResearchRunProcessEntry:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ResearchRunProjection:
    status: ResearchRunStatus
    stages: tuple[ResearchRunStage, ...]
    process: tuple[ResearchRunProcessEntry, ...]


@dataclass(frozen=True, slots=True)
class _EventMetadata:
    """Closed lifecycle vocabulary: public message, stage, and legal transitions."""

    message: str | None
    stage: str
    transitions: dict[str, str]
    terminal: str | None = None


_EVENT_VOCABULARY = {
    "initialized": _EventMetadata(
        "Research run initialized", "identity", {"new": "source_ready"}
    ),
    "source_stage_claimed": _EventMetadata(
        "Source collection started",
        "sources",
        {
            "source_ready": "source_active",
            "source_auto_retry": "source_active",
            "source_recovered": "source_active",
        },
    ),
    "source_provider_failed": _EventMetadata(
        "Source provider unavailable",
        "sources",
        {"source_active": "source_auto_retry"},
        "recoverable_failure",
    ),
    "source_preparation_failed": _EventMetadata(
        "Source preparation failed",
        "sources",
        {"source_active": "source_manual_retry"},
        "recoverable_failure",
    ),
    "source_claim_recovered": _EventMetadata(
        "Source collection claim recovered",
        "sources",
        {"source_active": "source_recovered"},
    ),
    "evidence_index_prepared": _EventMetadata(
        "Source collection completed", "sources", {"source_active": "evidence_review"}
    ),
    "evidence_reviewed": _EventMetadata(
        "Source review recorded", "sources", {"evidence_review": "evidence_review"}
    ),
    "model_stage_claimed": _EventMetadata(
        "Company analysis started",
        "analysis",
        {
            "evidence_review": "model_active",
            "model_ready": "model_active",
            "model_auto_retry": "model_active",
            "model_recovered": "model_active",
        },
    ),
    "model_provider_failed": _EventMetadata(
        "Model provider unavailable",
        "analysis",
        {"model_active": "model_auto_retry"},
        "recoverable_failure",
    ),
    "model_claim_recovered": _EventMetadata(
        "Company analysis claim recovered",
        "analysis",
        {"model_active": "model_recovered"},
    ),
    "process_warning": _EventMetadata(
        "AI narrative unavailable; deterministic report retained",
        "report",
        {"model_active": "model_active"},
    ),
    "business_map_prepared": _EventMetadata(
        "Company analysis completed", "analysis", {"model_active": "review_ready"}
    ),
    "critical_input_decided": _EventMetadata(
        "Critical research input decided",
        "report",
        {"model_active": "review_ready", "review_ready": "review_ready"},
    ),
    "model_rebuild_queued": _EventMetadata(
        "Forecast rebuild queued",
        "forecast",
        {"review_ready": "model_ready"},
    ),
    "source_preparation_blocked": _EventMetadata(
        "Source preparation blocked",
        "sources",
        {"source_active": "source_blocked"},
        "blocked_failure",
    ),
    "model_preparation_blocked": _EventMetadata(
        "Model preparation blocked",
        "analysis",
        {"model_active": "model_blocked"},
        "blocked_failure",
    ),
    "stale_output_discarded": _EventMetadata(
        "Stale output discarded",
        "analysis",
        {"source_active": "source_blocked", "model_active": "model_blocked"},
        "blocked_failure",
    ),
    "historical_basis_recovered": _EventMetadata(
        "Historical basis recovered", "analysis", {"model_blocked": "basis_recovered"}
    ),
    "retry_queued": _EventMetadata(
        "Research retry queued",
        "sources",
        {
            "source_auto_retry": "source_ready",
            "source_manual_retry": "source_ready",
            "model_auto_retry": "model_ready",
            "source_blocked": "source_ready",
            "model_blocked": "model_ready",
            "basis_recovered": "model_ready",
        },
    ),
    "judgment_confirmed": _EventMetadata(
        "Judgment confirmed",
        "report",
        {"model_active": "confirmed", "review_ready": "confirmed"},
    ),
    "company_research_published": _EventMetadata(
        "Research report published", "report", {"confirmed": "published"}, "published"
    ),
}

_STAGE_KEYS = ("identity", "sources", "analysis", "forecast", "report")
_RECOVERABLE_TERMINAL_FAILURE_EVENTS = frozenset(
    event_type
    for event_type, metadata in _EVENT_VOCABULARY.items()
    if metadata.terminal == "recoverable_failure"
)
_BLOCKED_TERMINAL_FAILURE_EVENTS = frozenset(
    event_type
    for event_type, metadata in _EVENT_VOCABULARY.items()
    if metadata.terminal in {"recoverable_failure", "blocked_failure"}
)
_STAGE_INDEX = {"sources": 1, "analysis": 2, "forecast": 3, "report": 4}
_FAILURE_STAGE_PROGRESS = {1: 10, 2: 35}


def project_research_run_status(
    *, internal_status: str, progress: int, error_code: str | None
) -> ResearchRunStatus:
    if type(progress) is not int or not 0 <= progress <= 100:
        raise ResearchRunProjectionError("research preparation progress is invalid")
    if not isinstance(internal_status, str):
        raise ResearchRunProjectionError("research preparation status is invalid")
    if error_code is not None and not isinstance(error_code, str):
        raise ResearchRunProjectionError("research preparation error code is invalid")

    direct_mappings = {
        ("queued", 0): ResearchRunStatus.QUEUED,
        ("preparing_sources", 10): ResearchRunStatus.COLLECTING_SOURCES,
        ("building_model", 25): ResearchRunStatus.ANALYZING_COMPANY,
        ("building_model", 35): ResearchRunStatus.ANALYZING_COMPANY,
        ("building_model", 60): ResearchRunStatus.BUILDING_FORECAST,
        ("building_model", 80): ResearchRunStatus.GENERATING_REPORT,
        ("awaiting_judgment_review", 85): ResearchRunStatus.NEEDS_INPUT,
        ("ready_to_freeze", 95): ResearchRunStatus.COMPLETED,
        ("completed", 100): ResearchRunStatus.COMPLETED,
    }
    direct = direct_mappings.get((internal_status, progress))
    if direct is not None:
        if error_code is not None:
            raise ResearchRunProjectionError(
                "non-failed research preparation cannot expose an error"
            )
        return direct
    if internal_status == "recoverable_failure" and progress == 30:
        return ResearchRunStatus.FAILED
    if internal_status == "blocked":
        if error_code in NEEDS_INPUT_ERROR_CODES:
            return ResearchRunStatus.NEEDS_INPUT
        if error_code in FAILED_ERROR_CODES:
            return ResearchRunStatus.FAILED
        raise ResearchRunProjectionError(
            "blocked research preparation requires a known error code"
        )
    raise ResearchRunProjectionError("research preparation state is not projectable")


def project_research_run(
    preparation: ResearchRunPreparation,
    event_types: tuple[str, ...],
    has_machine_memo: bool,
    confirmation_complete: bool,
) -> ResearchRunProjection:
    """Project trusted lifecycle inputs into a public, secret-free run view."""
    if type(preparation) is not ResearchRunPreparation:
        raise ResearchRunProjectionError("research preparation is invalid")
    if type(event_types) is not tuple or any(
        type(event_type) is not str or event_type not in _EVENT_VOCABULARY
        for event_type in event_types
    ):
        raise ResearchRunProjectionError("research process events are invalid")
    if type(has_machine_memo) is not bool or type(confirmation_complete) is not bool:
        raise ResearchRunProjectionError("research confirmation state is invalid")
    if confirmation_complete and not has_machine_memo:
        raise ResearchRunProjectionError(
            "confirmed research run requires a machine memo"
        )
    lifecycle_state = _validate_event_history(event_types)
    status = project_research_run_status(
        internal_status=preparation.internal_status,
        progress=preparation.progress,
        error_code=preparation.error_code,
    )
    _validate_status_history(
        preparation.internal_status, preparation.progress, event_types, lifecycle_state
    )
    _validate_confirmation_state(
        internal_status=preparation.internal_status,
        status=status,
        event_types=event_types,
        has_machine_memo=has_machine_memo,
        confirmation_complete=confirmation_complete,
    )
    return ResearchRunProjection(
        status=status,
        stages=_stages_for(status, event_types),
        process=tuple(
            ResearchRunProcessEntry(event_type, _EVENT_VOCABULARY[event_type].message)
            for event_type in event_types
            if _EVENT_VOCABULARY[event_type].message is not None
        ),
    )


def _validate_event_history(event_types: tuple[str, ...]) -> str:
    if not event_types:
        raise ResearchRunProjectionError("research process history is invalid")
    state = "new"
    for event_type in event_types:
        metadata = _EVENT_VOCABULARY[event_type]
        next_state = metadata.transitions.get(state)
        if next_state is None:
            raise ResearchRunProjectionError("research process history is inconsistent")
        if state == "published":
            raise ResearchRunProjectionError("research process history is inconsistent")
        state = next_state
    return state


def _validate_confirmation_state(
    *,
    internal_status: str,
    status: ResearchRunStatus,
    event_types: tuple[str, ...],
    has_machine_memo: bool,
    confirmation_complete: bool,
) -> None:
    has_confirmation_event = "judgment_confirmed" in event_types
    if has_confirmation_event != confirmation_complete:
        raise ResearchRunProjectionError("research confirmation state is inconsistent")
    if internal_status == "awaiting_judgment_review":
        if not has_machine_memo:
            raise ResearchRunProjectionError("research memo state is inconsistent")
        if confirmation_complete:
            raise ResearchRunProjectionError(
                "research confirmation state is inconsistent"
            )
    elif internal_status == "building_model" and "model_rebuild_queued" in event_types:
        if not has_machine_memo or confirmation_complete:
            raise ResearchRunProjectionError(
                "research rebuild memo state is inconsistent"
            )
    elif status is ResearchRunStatus.NEEDS_INPUT:
        if has_machine_memo or confirmation_complete:
            raise ResearchRunProjectionError("research memo state is inconsistent")
    elif status is ResearchRunStatus.COMPLETED:
        if not has_machine_memo or not confirmation_complete:
            raise ResearchRunProjectionError(
                "completed research run requires confirmation"
            )
    elif has_machine_memo or confirmation_complete:
        raise ResearchRunProjectionError("research memo state is inconsistent")


def _validate_status_history(
    internal_status: str,
    progress: int,
    event_types: tuple[str, ...],
    lifecycle_state: str,
) -> None:
    terminal_event_types = {
        "queued": {"initialized", "retry_queued", "source_claim_recovered"},
        "preparing_sources": {"source_stage_claimed"},
        "building_model": {
            "model_stage_claimed",
            "model_claim_recovered",
            "model_rebuild_queued",
        },
        "awaiting_judgment_review": {
            "model_stage_claimed",
            "process_warning",
            "critical_input_decided",
        },
        "ready_to_freeze": {"judgment_confirmed"},
        "completed": {"company_research_published"},
        "recoverable_failure": _RECOVERABLE_TERMINAL_FAILURE_EVENTS,
        "blocked": _BLOCKED_TERMINAL_FAILURE_EVENTS,
    }
    if event_types[-1] not in terminal_event_types[internal_status]:
        raise ResearchRunProjectionError("research process history is inconsistent")
    expected_states = {
        "queued": {"source_ready", "source_recovered"},
        "preparing_sources": {"source_active"},
        "building_model": {"model_ready", "model_active", "model_recovered"},
        "awaiting_judgment_review": {"model_active", "review_ready"},
        "ready_to_freeze": {"confirmed"},
        "completed": {"published"},
        "recoverable_failure": {
            "source_auto_retry",
            "source_manual_retry",
            "model_auto_retry",
        },
        "blocked": {
            "source_blocked",
            "model_blocked",
            "source_auto_retry",
            "source_manual_retry",
            "model_auto_retry",
        },
    }
    if lifecycle_state not in expected_states[internal_status]:
        raise ResearchRunProjectionError("research process history is inconsistent")
    if internal_status == "blocked":
        expected_progress = _FAILURE_STAGE_PROGRESS[
            _terminal_failure_stage_index(event_types)
        ]
        if progress != expected_progress:
            raise ResearchRunProjectionError(
                "blocked research failure progress is inconsistent"
            )


def _stages_for(
    status: ResearchRunStatus, event_types: tuple[str, ...]
) -> tuple[ResearchRunStage, ...]:
    states = [ResearchRunStageStatus.PENDING] * len(_STAGE_KEYS)
    states[0] = ResearchRunStageStatus.COMPLETED
    if status is ResearchRunStatus.COLLECTING_SOURCES:
        states[1] = ResearchRunStageStatus.ACTIVE
    elif status is ResearchRunStatus.ANALYZING_COMPANY:
        states[1:3] = [ResearchRunStageStatus.COMPLETED, ResearchRunStageStatus.ACTIVE]
    elif status is ResearchRunStatus.BUILDING_FORECAST:
        states[1:4] = [
            ResearchRunStageStatus.COMPLETED,
            ResearchRunStageStatus.COMPLETED,
            ResearchRunStageStatus.ACTIVE,
        ]
    elif status is ResearchRunStatus.GENERATING_REPORT:
        states[1:] = [
            ResearchRunStageStatus.COMPLETED,
            ResearchRunStageStatus.COMPLETED,
            ResearchRunStageStatus.COMPLETED,
            ResearchRunStageStatus.ACTIVE,
        ]
    elif status in {ResearchRunStatus.NEEDS_INPUT, ResearchRunStatus.COMPLETED}:
        if status is ResearchRunStatus.COMPLETED:
            states[1:] = [ResearchRunStageStatus.COMPLETED] * 4
        else:
            input_index = _needs_input_stage_index(event_types)
            states[1:input_index] = [ResearchRunStageStatus.COMPLETED] * (
                input_index - 1
            )
            states[input_index] = ResearchRunStageStatus.NEEDS_INPUT
    elif status is ResearchRunStatus.FAILED:
        failed_index = _terminal_failure_stage_index(event_types)
        states[1:failed_index] = [ResearchRunStageStatus.COMPLETED] * (failed_index - 1)
        states[failed_index] = ResearchRunStageStatus.FAILED
    return tuple(
        ResearchRunStage(key, stage_status)
        for key, stage_status in zip(_STAGE_KEYS, states, strict=True)
    )


def _needs_input_stage_index(event_types: tuple[str, ...]) -> int:
    terminal_metadata = _EVENT_VOCABULARY[event_types[-1]]
    if terminal_metadata.terminal == "blocked_failure":
        return _STAGE_INDEX[terminal_metadata.stage]
    if event_types[-1] in {
        "model_stage_claimed",
        "process_warning",
        "critical_input_decided",
    }:
        return 4
    raise ResearchRunProjectionError("research process input stage is inconsistent")


def _terminal_failure_stage_index(event_types: tuple[str, ...]) -> int:
    terminal_event = event_types[-1]
    terminal_metadata = _EVENT_VOCABULARY[terminal_event]
    if (
        terminal_metadata.terminal is not None
        and terminal_event != "stale_output_discarded"
    ):
        return _STAGE_INDEX[terminal_metadata.stage]
    if terminal_event == "stale_output_discarded":
        for event_type in reversed(event_types[:-1]):
            if event_type == "model_stage_claimed":
                return 2
            if event_type == "source_stage_claimed":
                return 1
    raise ResearchRunProjectionError("research process failure stage is inconsistent")
