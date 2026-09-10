"""Bounded, server-owned lifecycle labels for private source-task history."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.v1.common import V1Model

TraceStage = Literal[
    "queued", "searching", "fetching", "freezing", "extracting", "admitting",
    "succeeded", "partial", "failed", "cancelled", "unknown",
]
TraceStatus = Literal[
    "queued", "running", "retry_wait", "succeeded", "partial", "failed",
    "cancelled", "unknown",
]
TraceLabel = Literal[
    "任务已排队", "来源检索阶段", "来源获取阶段", "材料冻结阶段", "内容解析阶段",
    "证据准入校验阶段", "来源任务已完成", "来源任务部分完成", "来源任务失败",
    "来源任务已取消", "来源任务状态已更新",
]


class GatewayTaskTraceEventDTO(V1Model):
    sequence: int = Field(ge=0, strict=True)
    stage: TraceStage
    status: TraceStatus
    label: TraceLabel
    occurred_at: datetime


class GatewayTaskTraceExceptionDTO(V1Model):
    reason_code: Literal[
        "source_unavailable", "source_policy_blocked", "parsing_failed",
        "evidence_not_admitted", "source_version_conflict", "processing_error",
    ]
    message: Literal[
        "部分来源暂时不可用", "部分来源不符合当前使用权限", "部分材料解析失败",
        "部分材料未通过证据准入", "部分来源版本存在冲突", "部分材料处理异常",
    ]
    next_action: Literal[
        "wait_for_retry", "check_source_policy", "review_sources", "check_execution",
    ]
    count: int = Field(ge=1, strict=True)


class GatewayTaskTraceDTO(V1Model):
    conversation_id: UUID
    run_spec_id: UUID
    task_id: UUID
    events: list[GatewayTaskTraceEventDTO] = Field(max_length=100)
    exceptions: list[GatewayTaskTraceExceptionDTO] = Field(max_length=6)
    truncated: bool
