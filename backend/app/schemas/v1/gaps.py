"""Case-level evidence-gap v1 wire DTOs (prototype 研究计划 · 证据缺口)."""
from __future__ import annotations

from app.schemas.v1.common import V1Model


class CaseGapDTO(V1Model):
    """One open gap from the latest provisional assessment of a thesis."""

    thesis_id: str
    thesis_statement: str
    conclusion: str
    gap: str
    assessment_id: str


class CaseGapsResponse(V1Model):
    """证据缺口聚合: every open gap across the case's latest assessments."""

    case_id: str
    cutoff: str
    gaps: list[CaseGapDTO]
