"""Closed vocabulary for frozen-boundary coverage decisions."""

from __future__ import annotations

from typing import Final


BOUNDARY_REASON_CODES: Final = {
    "source_policy": frozenset({"authority_requirement_unmet"}),
    "time_window": frozenset({"cutoff_violation"}),
    "entity_scope": frozenset({"entity_mismatch", "security_mismatch"}),
    "metric_scope": frozenset(
        {
            "metric_mismatch",
            "period_mismatch",
            "expected_period_unspecified",
            "conflicting_evidence_periods",
        }
    ),
    "unit_semantics": frozenset(
        {
            "unit_unknown",
            "unit_mismatch",
            "expected_unit_unspecified",
            "conflicting_evidence_units",
        }
    ),
}

COVERAGE_REASON_CODES: Final = frozenset(
    {
        *set().union(*BOUNDARY_REASON_CODES.values()),
        "independent_source_requirement_unmet",
        "conflicting_document_variants",
        "search_not_successfully_completed",
        "role_mismatch",
        "source_identity_unknown",
        "publication_identity_unknown",
        "canonical_identity_unknown",
        "content_identity_unknown",
        "untraceable_admission",
    }
)

BOUNDARY_DECISION_REASON: Final = "继续补证需要改变已冻结的研究边界。"
BOUNDARY_DECISION_RECOMMENDATION: Final = {
    "kind": "revise_scope",
    "label": "调整研究边界",
    "impact": "创建新的冻结范围版本后，系统才能按新边界继续补证；现有运行与证据记录保持不变。",
}
BOUNDARY_DECISION_ALTERNATIVES: Final = (
    {
        "kind": "keep_scope",
        "label": "保持当前研究范围",
        "impact": "不改变已冻结边界，未解决目标继续明确保留为未知。",
    },
    {
        "kind": "stop",
        "label": "停止本次研究",
        "impact": "停止后续自动补证，并保留当前证据、缺口与运行记录。",
    },
)
