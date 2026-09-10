"""Provider-run (AIRun audit) v1 wire DTOs (prototype 数据中心/研究计划)."""
from __future__ import annotations

from typing import Any

from app.schemas.v1.common import V1Model


class ProviderRunDTO(V1Model):
    """One AI/provider invocation audit record.

    成功 = 保留来源版本；失败 = 本次没有新数据；错误信息原样带出，
    不掩饰、不推测（prototype Provider 运行记录的失败含义约定）。
    """

    id: str
    kind: str
    model_version: str
    prompt_version: str
    status: str
    output_summary: str
    error: str | None
    input_ref: dict[str, Any]
    started_at: str
    finished_at: str | None


class ProviderRunsResponse(V1Model):
    runs: list[ProviderRunDTO]
