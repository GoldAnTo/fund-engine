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
    AutomaticResearchActivityDTO,
    AutomaticResearchActivityDetailDTO,
    AutomaticResearchExceptionDTO,
    AutomaticResearchFactorDTO,
    AutomaticResearchNarrativeDTO,
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
    "search_failed": ("来源检索暂时失败", "这些来源未进入后续处理。", "系统已跳过失败检索，并继续尝试其余研究任务。"),
    "search_item_rejected": ("部分检索结果不符合来源要求", "这些结果不会进入证据判断。", "系统已排除不符合要求的结果。"),
    "source_unavailable": ("部分来源暂时不可用", "这些来源未进入后续处理。", "系统已跳过暂不可用来源，并继续处理可用资料。"),
    "fetch_failed": ("部分来源获取失败", "这些来源不会被作为结论依据。", "系统已跳过获取失败的来源，并继续处理其余资料。"),
    "reference_restore_failed": ("来源引用恢复失败", "这些来源不会被作为结论依据。", "系统已隔离无法恢复的引用。"),
    "parser_failure": ("部分材料解析失败", "这些材料不会被用作本次结论依据。", "系统已跳过异常材料，并继续处理其余可用来源。"),
    "invalid_fetch_checkpoint": ("材料处理状态无效", "这些材料不会被用作本次结论依据。", "系统已隔离状态无效的材料。"),
    "automatic_admission_quarantined": ("部分证据未通过自动准入", "这些证据不会被纳入结论。", "系统已保留未通过项的记录，并继续评估其余证据。"),
    "incompatible_source_contract": ("部分来源不允许用于本次研究", "这些来源不会被纳入结论。", "系统已遵守来源使用限制，并继续处理允许使用的来源。"),
    "unsupported_source_policy_version": ("来源策略版本暂不支持", "这些来源不会被纳入结论。", "系统已跳过暂不支持的来源策略。"),
    "variant_conflict": ("来源版本存在冲突", "冲突版本不会被纳入结论。", "系统已隔离冲突版本，避免混入研究结论。"),
}
_ACTIVITY_LABELS = {
    "queued": "正在准备来源处理",
    "searching": "正在查找可信来源",
    "fetching": "正在获取已找到的来源",
    "freezing": "正在冻结来源原件",
    "extracting": "正在解析 {count} 个来源",
    "admitting": "正在校验证据是否可引用",
    "succeeded": "已完成 {count} 个来源处理",
    "partial": "已完成部分来源处理",
    "failed": "部分来源处理未完成",
    "cancelled": "来源处理已停止",
    "planning": "正在准备研究范围",
    "retrieve": "开始收集研究资料",
    "waiting_for_sources": "等待资料处理完成",
    "analyze": "正在分析关键因素",
    "assessing": "正在评估因素证据",
    "conclude": "正在生成研究结论",
    "complete": "研究结论已生成",
}
_TERMINAL_JOB_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class AutomaticResearchProgressProjection:
    status: str
    stages: list[AutomaticResearchStageDTO]
    stats: AutomaticResearchStatsDTO
    narrative: AutomaticResearchNarrativeDTO
    activities: list[AutomaticResearchActivityDTO]
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
        narrative=_narrative(
            status=effective_status,
            run=run,
            jobs=jobs,
            elapsed_seconds=int(duration),
        ),
        activities=_activities(run_events, acquisition_events, jobs),
        exceptions=[
            AutomaticResearchExceptionDTO(
                reason=_EXCEPTION_MESSAGES.get(
                    code,
                    (
                        "部分材料处理异常",
                        "这些材料不会被用作本次结论依据。",
                        "系统已跳过异常材料，并继续处理其余可用来源。",
                    ),
                )[0],
                count=count,
                impact=_EXCEPTION_MESSAGES.get(
                    code,
                    (
                        "部分材料处理异常",
                        "这些材料不会被用作本次结论依据。",
                        "系统已跳过异常材料，并继续处理其余可用来源。",
                    ),
                )[1],
                system_action=_EXCEPTION_MESSAGES.get(
                    code,
                    (
                        "部分材料处理异常",
                        "这些材料不会被用作本次结论依据。",
                        "系统已跳过异常材料，并继续处理其余可用来源。",
                    ),
                )[2],
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


def _narrative(
    *,
    status: str,
    run: ResearchRun,
    jobs: Sequence[AcquisitionJob],
    elapsed_seconds: int,
) -> AutomaticResearchNarrativeDTO:
    total_count = len(jobs)
    completed_count = sum(job.status in _TERMINAL_JOB_STATUSES for job in jobs)
    active_stage = _active_user_stage(status, run, jobs)
    current_action, next_action = {
        "queued": ("正在准备研究范围", "随后开始收集研究资料"),
        "searching": ("正在查找可信来源", "随后获取已找到的来源"),
        "fetching": ("正在获取已找到的来源", "随后解析已获取的材料"),
        "freezing": ("正在冻结来源原件", "随后解析已获取的材料"),
        "extracting": ("正在解析已找到的材料", "随后校验证据是否可引用"),
        "admitting": ("正在校验证据是否可引用", "随后分析各项因素的影响"),
        "analyze": ("正在分析各项因素的影响", "随后生成研究结论"),
        "conclude": ("正在汇总证据并生成结论", "完成后展示系统生成的研究结论"),
        "completed": ("研究结论已生成", "可查看关键因素、证据与局限"),
        "failed": ("自动研究未能完成", "可检查已处理内容后重新开始"),
    }[active_stage]
    eta_min, eta_max = _eta_bounds(jobs, total_count - completed_count)
    return AutomaticResearchNarrativeDTO(
        current_action=current_action,
        completed_count=completed_count,
        total_count=total_count,
        next_action=next_action,
        elapsed_seconds=elapsed_seconds,
        estimated_remaining_seconds_min=eta_min,
        estimated_remaining_seconds_max=eta_max,
    )


def _active_user_stage(status: str, run: ResearchRun, jobs: Sequence[AcquisitionJob]) -> str:
    if status == "queued":
        return "queued"
    if status in {"completed", "failed"}:
        return status
    active_jobs = [job for job in jobs if job.status not in _TERMINAL_JOB_STATUSES]
    if active_jobs:
        return min(active_jobs, key=lambda job: _ACQUISITION_STAGE.get(job.stage, 0)).stage
    if run.stage in {"analyze", "assessing"}:
        return "analyze"
    if run.stage in {"conclude", "complete"}:
        return "conclude"
    return "searching"


def _eta_bounds(
    jobs: Sequence[AcquisitionJob], remaining_count: int
) -> tuple[int | None, int | None]:
    durations = sorted(
        max(0.0, (_as_utc(job.finished_at) - _as_utc(job.started_at)).total_seconds())
        for job in jobs
        if job.status in _TERMINAL_JOB_STATUSES
        and getattr(job, "started_at", None) is not None
        and getattr(job, "finished_at", None) is not None
    )
    if remaining_count <= 0 or len(durations) < 2:
        return None, None
    midpoint = len(durations) // 2
    median = (
        durations[midpoint]
        if len(durations) % 2
        else (durations[midpoint - 1] + durations[midpoint]) / 2
    )
    estimate = median * remaining_count
    return int(estimate * 0.8), int(estimate * 1.2)


def _activities(
    run_events: Sequence[ResearchRunEvent],
    acquisition_events: Sequence[AcquisitionJobEvent],
    jobs: Sequence[AcquisitionJob],
) -> list[AutomaticResearchActivityDTO]:
    job_items = {
        getattr(job, "id", None): _work_item(job)
        for job in jobs
    }
    grouped: dict[tuple[str, str], list[AutomaticResearchActivityDetailDTO]] = {}
    for event in acquisition_events:
        detail = AutomaticResearchActivityDetailDTO(
            occurred_at=event.created_at,
            work_item=job_items.get(event.job_id, "研究资料"),
            internal_status=event.stage,
        )
        grouped.setdefault(("acquisition", event.stage), []).append(detail)
    for event in run_events:
        detail = AutomaticResearchActivityDetailDTO(
            occurred_at=event.created_at,
            work_item="研究流程",
            internal_status=event.stage,
        )
        grouped.setdefault(("research", event.stage), []).append(detail)

    activities: list[tuple[datetime, AutomaticResearchActivityDTO]] = []
    for (_, stage), details in grouped.items():
        details.sort(key=lambda item: item.occurred_at, reverse=True)
        label = _ACTIVITY_LABELS.get(stage, "系统正在处理研究资料")
        activities.append(
            (
                details[0].occurred_at,
                AutomaticResearchActivityDTO(
                    label=label.format(count=len(details)),
                    count=len(details),
                    technical_details=details,
                ),
            )
        )
    activities.sort(key=lambda row: (row[0], row[1].label), reverse=True)
    return [activity for _, activity in activities[:8]]


def _work_item(job: AcquisitionJob) -> str:
    snapshot = getattr(job, "request_snapshot", None)
    if isinstance(snapshot, dict):
        objective = snapshot.get("objective")
        if isinstance(objective, str) and objective.strip():
            return objective.strip()
    return "研究资料"


def project_factor_judgement(
    *,
    statement: str,
    assessment: object | None,
    support_count: int,
    counter_evidence_count: int,
) -> AutomaticResearchFactorDTO:
    raw = getattr(assessment, "factor_judgement", None) if assessment else None
    gaps = getattr(assessment, "gaps", []) if assessment else []
    evidence_gap = next(
        (gap for gap in gaps if isinstance(gap, str) and gap.strip()), None
    )
    if not isinstance(raw, dict):
        return _pending_factor(
            statement, support_count, counter_evidence_count, evidence_gap
        )
    expected = {
        "relevance", "causal_impact", "evidence_strength", "counter_evidence",
        "classification", "ranking_reason",
    }
    if set(raw) != expected or not all(isinstance(raw[key], str) for key in expected):
        return _pending_factor(
            statement, support_count, counter_evidence_count, evidence_gap
        )
    classification = raw["classification"]
    valid = (
        classification in {"key", "secondary", "pending", "excluded"}
        and raw["relevance"] in {"direct", "indirect", "unclear"}
        and raw["causal_impact"] in {"high", "medium", "low", "unclear"}
        and raw["evidence_strength"] in {"strong", "moderate", "weak", "none"}
        and raw["counter_evidence"] in {"none", "mixed", "material", "unknown"}
        and bool(raw["ranking_reason"].strip())
    )
    is_key = (
        classification == "key"
        and getattr(assessment, "conclusion", None) == "supported"
        and raw["relevance"] == "direct"
        and raw["causal_impact"] == "high"
        and raw["evidence_strength"] == "strong"
        and raw["counter_evidence"] == "none"
        and counter_evidence_count == 0
    )
    if not valid or (classification == "key" and not is_key):
        return _pending_factor(
            statement, support_count, counter_evidence_count, evidence_gap
        )
    return AutomaticResearchFactorDTO(
        statement=statement,
        classification=classification,
        ranking_reason=raw["ranking_reason"].strip(),
        support_count=support_count,
        counter_evidence_count=counter_evidence_count,
        evidence_gap=evidence_gap,
    )


def _pending_factor(
    statement: str,
    support_count: int,
    counter_evidence_count: int,
    evidence_gap: str | None,
) -> AutomaticResearchFactorDTO:
    return AutomaticResearchFactorDTO(
        statement=statement,
        classification="pending",
        ranking_reason="尚未确认关键因素：存在反证或因素判断未通过完整校验。",
        support_count=support_count,
        counter_evidence_count=counter_evidence_count,
        evidence_gap=evidence_gap,
    )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
