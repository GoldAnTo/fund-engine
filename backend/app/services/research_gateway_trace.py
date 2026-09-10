"""Read persisted source-task lifecycle within the private frozen authority."""
from datetime import UTC
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor
from app.errors import NotFoundError
from app.models.acquisition import AcquisitionException, AcquisitionJobEvent
from app.models.operational import ResearchRun
from app.models.source_governance import SourceContract
from app.schemas.v1.research_gateway_trace import (
    GatewayTaskTraceDTO,
    GatewayTaskTraceEventDTO,
    GatewayTaskTraceExceptionDTO,
)
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.research_gateway import ResearchGateway
from app.services.research_gateway_artifacts import validated_gateway_source_bindings
from app.services.source_admission import source_contract_is_active

_NOT_FOUND = "research task trace not found"
_STAGE_LABELS = {
    "queued": "任务已排队",
    "searching": "来源检索阶段",
    "fetching": "来源获取阶段",
    "freezing": "材料冻结阶段",
    "extracting": "内容解析阶段",
    "admitting": "证据准入校验阶段",
    "succeeded": "来源任务已完成",
    "partial": "来源任务部分完成",
    "failed": "来源任务失败",
    "cancelled": "来源任务已取消",
    "unknown": "来源任务状态已更新",
}
_STATUSES = frozenset({
    "queued", "running", "retry_wait", "succeeded", "partial", "failed", "cancelled",
})
_EXCEPTION_CATEGORIES = {
    "search_failed": "source_unavailable",
    "source_unavailable": "source_unavailable",
    "fetch_failed": "source_unavailable",
    "reference_restore_failed": "source_unavailable",
    "search_item_rejected": "source_policy_blocked",
    "incompatible_source_contract": "source_policy_blocked",
    "unsupported_source_policy_version": "source_policy_blocked",
    "parser_failure": "parsing_failed",
    "parser_failed": "parsing_failed",
    "extraction_failed": "parsing_failed",
    "automatic_admission_quarantined": "evidence_not_admitted",
    "variant_conflict": "source_version_conflict",
}
_EXCEPTION_LABELS = {
    "source_unavailable": ("部分来源暂时不可用", "check_execution"),
    "source_policy_blocked": ("部分来源不符合当前使用权限", "check_source_policy"),
    "parsing_failed": ("部分材料解析失败", "review_sources"),
    "evidence_not_admitted": ("部分材料未通过证据准入", "review_sources"),
    "source_version_conflict": ("部分来源版本存在冲突", "review_sources"),
    "processing_error": ("部分材料处理异常", "check_execution"),
}


def read_gateway_task_trace(
    session: Session,
    actor: ResearchActor,
    *,
    conversation_id: UUID,
    run_spec_id: UUID,
    task_id: UUID,
) -> GatewayTaskTraceDTO:
    """Lifecycle events describe processing, never a factual evidence verdict."""
    spec = ResearchGateway(session).read_run_spec(actor, run_spec_id)
    if spec.conversation_id != conversation_id:
        raise NotFoundError(_NOT_FOUND)
    run = session.get(ResearchRun, spec.native_run_id, populate_existing=True)
    if run is None:
        raise NotFoundError(_NOT_FOUND)
    try:
        scope = load_automatic_research_scope(session, run)
        bindings = validated_gateway_source_bindings(session, spec, run, scope)
    except (ValueError, TypeError, KeyError) as exc:
        raise NotFoundError(_NOT_FOUND) from exc
    job = bindings.jobs_by_task_id.get(task_id)
    if job is None or task_id not in bindings.tasks_by_id:
        raise NotFoundError(_NOT_FOUND)
    if bindings.material_job_ids:
        contract = session.scalar(select(SourceContract).where(
            SourceContract.document_version_id == scope.material_document_version_id,
        ).execution_options(populate_existing=True))
        if (contract is None or not contract.allow_display
                or not contract.allow_ai_processing or not source_contract_is_active(contract)):
            raise NotFoundError(_NOT_FOUND)

    rows = session.execute(select(
        AcquisitionJobEvent.seq, AcquisitionJobEvent.stage,
        AcquisitionJobEvent.status, AcquisitionJobEvent.created_at,
    ).where(AcquisitionJobEvent.job_id == job.id)
        .order_by(AcquisitionJobEvent.seq.desc()).limit(101)).all()
    events = []
    for sequence, native_stage, native_status, occurred_at in reversed(rows[:100]):
        stage = native_stage if native_stage in _STAGE_LABELS else "unknown"
        events.append(GatewayTaskTraceEventDTO(
            sequence=sequence,
            stage=stage,
            status=native_status if native_status in _STATUSES else "unknown",
            label=_STAGE_LABELS[stage],
            occurred_at=(occurred_at.replace(tzinfo=UTC) if occurred_at.tzinfo is None
                         else occurred_at.astimezone(UTC)),
        ))
    # Aggregate in SQL into a fixed vocabulary: neither unknown reason text nor
    # provider details need to enter the projection, even for large histories.
    category = case(
        _EXCEPTION_CATEGORIES, value=AcquisitionException.reason_code,
        else_="processing_error",
    )
    exception_counts = session.execute(select(category, func.count())
        .where(AcquisitionException.job_id == job.id)
        .group_by(category).order_by(category)).all()
    retry_is_scheduled = (
        run.status in {"queued", "running", "waiting_for_sources"}
        and job.status == "retry_wait"
        and job.retry_at is not None
    )
    exceptions = [GatewayTaskTraceExceptionDTO(
        reason_code=reason, message=_EXCEPTION_LABELS[reason][0],
        next_action=("wait_for_retry" if reason == "source_unavailable" and retry_is_scheduled
                     else _EXCEPTION_LABELS[reason][1]), count=count,
    ) for reason, count in exception_counts]
    return GatewayTaskTraceDTO(
        conversation_id=conversation_id, run_spec_id=run_spec_id, task_id=task_id,
        events=events, exceptions=exceptions, truncated=len(rows) > 100,
    )
