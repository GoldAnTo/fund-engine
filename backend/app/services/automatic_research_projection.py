"""Pure read-model projection for one-click automatic research."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from app.models.acquisition import AcquisitionJob, AcquisitionJobEvent
from app.models.ledger import DocumentVersion, EvidenceLink, SourceStatement
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_monitor import ResearchRunEvent
from app.models.source_governance import SourceContract
from app.schemas.v1.automatic_research import (
    AutomaticResearchExceptionDTO,
    AutomaticResearchResultDTO,
    AutomaticResearchSourceDTO,
    AutomaticResearchStageDTO,
    AutomaticResearchStatsDTO,
)
from app.services.automatic_research_conclusion import AutomaticConclusionProjection
from app.services.source_admission import source_contract_is_active


_STAGES = (
    ("acquire", "资料获取"),
    ("parse", "内容解析"),
    ("admit", "证据校验"),
    ("analyze", "分析判断"),
    ("conclude", "生成结论"),
)
_ACQUISITION_STAGE = {
    "queued": 0,
    "searching": 0,
    "fetching": 0,
    "freezing": 1,
    "extracting": 1,
    "admitting": 2,
    "succeeded": 2,
    "partial": 2,
    "failed": 2,
    "cancelled": 2,
}
_RESEARCH_STAGE = {
    "planning": 0,
    "retrieve": 0,
    "waiting_for_sources": 2,
    "analyze": 3,
    "assessing": 3,
    "conclude": 4,
    "complete": 4,
    "failed": 4,
    "stopped": 4,
}
_EXCEPTION_MESSAGES = {
    "search_failed": ("来源检索暂时失败", "acquire"),
    "search_item_rejected": ("部分检索结果不符合来源要求", "acquire"),
    "source_unavailable": ("部分来源暂时不可用", "acquire"),
    "fetch_failed": ("部分来源获取失败", "acquire"),
    "reference_restore_failed": ("来源引用恢复失败", "acquire"),
    "parser_failure": ("部分材料解析失败", "parse"),
    "invalid_fetch_checkpoint": ("材料处理状态无效", "parse"),
    "automatic_admission_quarantined": ("部分证据未通过自动准入", "admit"),
    "incompatible_source_contract": ("部分来源不允许用于本次研究", "admit"),
    "unsupported_source_policy_version": ("来源策略版本暂不支持", "admit"),
    "variant_conflict": ("来源版本存在冲突", "admit"),
}


@dataclass(frozen=True, slots=True)
class AutomaticResearchProgressProjection:
    status: str
    stages: list[AutomaticResearchStageDTO]
    stats: AutomaticResearchStatsDTO
    recent_activity: list[str]
    exceptions: list[AutomaticResearchExceptionDTO]
    failure_reason: str | None


def automatic_research_status(
    run: ResearchRun,
    lifecycle: EventResearchLifecycle,
) -> str:
    if run.status == "queued":
        return "queued"
    if (
        run.status == "succeeded"
        and run.stage == "complete"
        and lifecycle.status == "completed"
    ):
        return "completed"
    if run.status in {"failed", "cancelled"}:
        return "failed"
    return "running"


def project_automatic_research_progress(
    *,
    status: str,
    run: ResearchRun,
    jobs: Sequence[AcquisitionJob],
    run_events: Sequence[ResearchRunEvent],
    acquisition_events: Sequence[AcquisitionJobEvent],
    admitted_evidence_count: int,
    exception_reason_codes: Sequence[str],
    projection_failure_stage: int | None = None,
    now: datetime | None = None,
) -> AutomaticResearchProgressProjection:
    effective_status = (
        "failed"
        if status == "completed" and projection_failure_stage is not None
        else status
    )
    stages = _project_stages(
        effective_status,
        run,
        jobs,
        run_events,
        acquisition_events,
        projection_failure_stage,
    )
    started_at = _as_utc(run.created_at)
    ended_at = (
        _as_utc(run.updated_at)
        if effective_status in {"completed", "failed"}
        else _as_utc(now or datetime.now(timezone.utc))
    )
    duration = max(0.0, (ended_at - started_at).total_seconds())
    exception_counts = Counter(exception_reason_codes)
    return AutomaticResearchProgressProjection(
        status=effective_status,
        stages=stages,
        stats=AutomaticResearchStatsDTO(
            source_count=sum(max(0, job.reference_count) for job in jobs),
            admitted_evidence_count=admitted_evidence_count,
            skipped_count=sum(max(0, job.exception_count) for job in jobs),
            duration_seconds=int(duration),
        ),
        recent_activity=_activity(run_events, acquisition_events),
        exceptions=[
            AutomaticResearchExceptionDTO(
                reason=_EXCEPTION_MESSAGES.get(code, ("部分材料处理异常", "admit"))[0],
                stage=_EXCEPTION_MESSAGES.get(code, ("部分材料处理异常", "admit"))[1],
                count=count,
            )
            for code, count in sorted(exception_counts.items())
        ],
        failure_reason=(
            "自动研究未能完成，请稍后重试" if effective_status == "failed" else None
        ),
    )


def project_automatic_research_result(
    *,
    conclusion_text: str,
    built: AutomaticConclusionProjection,
    ordered_links: Sequence[
        tuple[EvidenceLink, SourceStatement, DocumentVersion, SourceContract | None]
    ],
    at: datetime | None = None,
) -> AutomaticResearchResultDTO:
    sources: list[AutomaticResearchSourceDTO] = []
    source_keys: set[tuple[str | None, str | None, str]] = set()
    counter_evidence: list[str] = []
    for link, statement, document, contract in ordered_links:
        display_allowed = bool(
            contract is not None
            and contract.allow_display
            and source_contract_is_active(contract, at=at)
        )
        title = document.title if display_allowed else None
        url = document.source_url if display_allowed else None
        key = (title, url, link.role)
        if key not in source_keys:
            source_keys.add(key)
            sources.append(
                AutomaticResearchSourceDTO(
                    title=title,
                    url=url,
                    role=link.role,
                    review_state="automatically_admitted",
                )
            )
        if link.role == "contradicts" and display_allowed:
            counter_evidence.append(statement.normalized_text)
    return AutomaticResearchResultDTO(
        label="系统生成，未经人工审核",
        human_reviewed=False,
        conclusion=conclusion_text,
        key_findings=_dedupe(list(built.key_findings)),
        counter_evidence=_dedupe(counter_evidence),
        limitations=list(built.limitations),
        sources=sources,
    )


def _project_stages(
    overall: str,
    run: ResearchRun,
    jobs: Sequence[AcquisitionJob],
    run_events: Sequence[ResearchRunEvent],
    acquisition_events: Sequence[AcquisitionJobEvent],
    projection_failure_stage: int | None,
) -> list[AutomaticResearchStageDTO]:
    if overall == "queued":
        current = 0
    elif overall == "completed":
        current = 5
    elif overall == "failed":
        current = _failure_stage(
            run,
            jobs,
            run_events,
            acquisition_events,
            projection_failure_stage,
        )
    elif jobs and any(
        job.status not in {"succeeded", "partial", "failed", "cancelled"}
        for job in jobs
    ):
        current = min(
            _ACQUISITION_STAGE.get(job.stage, 0)
            for job in jobs
            if job.status not in {"succeeded", "partial", "failed", "cancelled"}
        )
    elif overall == "running" and jobs:
        current = 3
    else:
        current = _RESEARCH_STAGE.get(run.stage, 3)
    timestamps: dict[int, list[datetime]] = {index: [] for index in range(5)}
    for event in acquisition_events:
        timestamps[_ACQUISITION_STAGE.get(event.stage, 0)].append(event.created_at)
    for event in run_events:
        if event.stage in {"failed", "stopped"}:
            continue
        index = _RESEARCH_STAGE.get(event.stage)
        if index is not None:
            timestamps[index].append(event.created_at)
    projected: list[AutomaticResearchStageDTO] = []
    for index, (key, label) in enumerate(_STAGES):
        if overall == "completed" or index < current:
            state = "completed"
        elif overall == "failed" and index == current:
            state = "failed"
        elif overall == "running" and index == current:
            state = "running"
        else:
            state = "pending"
        values = timestamps[index]
        projected.append(
            AutomaticResearchStageDTO(
                key=key,
                label=label,
                status=state,
                summary={
                    "pending": f"{label}尚未开始",
                    "running": f"正在{label}",
                    "completed": f"{label}已完成",
                    "failed": f"{label}未能完成",
                }[state],
                started_at=min(values) if values and state != "pending" else None,
                completed_at=(
                    max(values) if values and state in {"completed", "failed"} else None
                ),
            )
        )
    return projected


def _failure_stage(
    run: ResearchRun,
    jobs: Sequence[AcquisitionJob],
    run_events: Sequence[ResearchRunEvent],
    acquisition_events: Sequence[AcquisitionJobEvent],
    projection_failure_stage: int | None,
) -> int:
    if projection_failure_stage is not None:
        return projection_failure_stage
    if run.status == "cancelled":
        active_jobs = [
            job
            for job in jobs
            if job.status not in {"succeeded", "partial", "failed", "cancelled"}
        ]
        if active_jobs:
            active_ids = {job.id for job in active_jobs}
            for event in reversed(acquisition_events):
                if (
                    event.job_id in active_ids
                    and event.status not in {"succeeded", "partial", "failed", "cancelled"}
                    and event.stage in _ACQUISITION_STAGE
                ):
                    return _ACQUISITION_STAGE[event.stage]
            latest = max(active_jobs, key=lambda job: (job.updated_at, str(job.id)))
            return _ACQUISITION_STAGE.get(latest.stage, 0)
        if not jobs:
            return 0
        for event in reversed(run_events):
            if event.status == "cancelled" and event.stage in {"analyze", "assessing", "conclude"}:
                return _RESEARCH_STAGE[event.stage]
        return 3
    concrete_run_stages = [
        _RESEARCH_STAGE[event.stage]
        for event in run_events
        if event.status == "failed"
        and event.stage in _RESEARCH_STAGE
        and event.stage != "failed"
    ]
    if concrete_run_stages:
        return concrete_run_stages[-1]
    acquisition_failure_stages = [
        _ACQUISITION_STAGE[event.stage]
        for event in acquisition_events
        if event.status in {"failed", "cancelled"} and event.stage in _ACQUISITION_STAGE
    ]
    if acquisition_failure_stages:
        return acquisition_failure_stages[-1]
    reason = run.stop_reason or ""
    if reason == "no_usable_evidence":
        return 2
    if "assessment" in reason or "analy" in reason:
        return 3
    if "conclusion" in reason or "result" in reason:
        return 4
    if not jobs:
        return 0
    failed_job_stages = [
        _ACQUISITION_STAGE.get(job.stage, 2)
        for job in jobs
        if job.status in {"failed", "cancelled"}
    ]
    if failed_job_stages:
        return failed_job_stages[-1]
    return 3


def _activity(
    run_events: Sequence[ResearchRunEvent],
    acquisition_events: Sequence[AcquisitionJobEvent],
) -> list[str]:
    rows = [
        (event.created_at, f"研究阶段更新：{event.stage}") for event in run_events
    ] + [
        (event.created_at, f"来源处理更新：{event.stage}")
        for event in acquisition_events
    ]
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [message for _, message in rows[:20]]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
