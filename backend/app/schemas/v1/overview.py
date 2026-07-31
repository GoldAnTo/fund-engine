"""Honest research overview v1 wire DTOs.

No reliability percentage, maturity score or ready_for_review flag is
fabricated; task_queue/evidence_changes/activity stay empty until their
projections exist (delivery 3).
"""

from typing import Any, Literal

from app.schemas.v1.cases import AssessmentDTO, CaseSummaryDTO
from app.schemas.v1.common import HistoricalBasisDTO, V1Model


class OverviewTotalsDTO(V1Model):
    evidence_total: int
    pending_review: int
    major_gaps: int


class KeyChangeDTO(V1Model):
    id: str
    tag: Literal["新增", "更新", "风险", "缺口"]
    text: str
    occurred_at: str
    source_label: str


class OverviewResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    case: CaseSummaryDTO
    thesis: dict[str, Any] | None
    assessment: AssessmentDTO | None
    key_changes: list[KeyChangeDTO]
    framework: list[dict[str, Any]]
    totals: OverviewTotalsDTO
    task_queue: list[dict[str, Any]]
    evidence_changes: list[dict[str, Any]]
    activity: list[dict[str, Any]]
