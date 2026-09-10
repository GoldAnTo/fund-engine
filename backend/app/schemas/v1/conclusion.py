"""「结论与关键因素」页面 v1 wire DTO.

对应原型：设计原型11-结论与关键因素.png

四个区域：
  1) Header — 结论摘要 + 截止/审核状态/版本元信息
  2) KeyFactors — 每个命题下的关键因素列表（含复盘链路）
  3) FactorComparison — 竞争性因素比较表
  4) Reproduction + Manifest — 解释与重现 + 复现清单

设计原则：
- 不编造：所有数字都从账本聚合，不自动结论。
- AI / 人工边界可见：displayed_as_provisional 与人工复核并列展示。
- 时点可回放：所有数字共享同一个 HistoricalBasis。
"""
from __future__ import annotations

from typing import Literal

from app.schemas.v1.common import HistoricalBasisDTO, V1Model


ConclusionStatus = Literal["supported", "contradicted", "insufficient_evidence"]
EvidenceRole = Literal["supports", "contradicts", "contextualizes"]
StatementKind = Literal[
    "disclosed_fact", "management_attribution", "forecast", "research_opinion"
]
SourceRelation = Literal["direct_evidence", "backing_evidence", "scope_warning"]


class ConclusionHeaderDTO(V1Model):
    """顶部「结论与关键因素」摘要 + 元信息."""

    research_case_id: str
    case_title: str
    industry_topic: str
    evidence_cutoff: str
    conclusion_text: str
    conclusion_status: ConclusionStatus | None
    rationale: str
    review_state: str
    reviewer: str | None
    reviewed_at: str | None
    snapshot_id: str | None
    ai_provisional: bool


class KeyFactorRowDTO(V1Model):
    """单个命题下的「关键因素」行（含 AI / 人工角色 + 复盘链路）."""

    factor_id: str
    thesis_id: str
    thesis_title: str
    thesis_statement: str
    status_label: str
    role_label: str  # 「待人工」「待证据」「待传递」「已复现」
    factor_label: str
    time_order: str  # 「因素定义 → 因素依赖 → 结论判定」
    mechanism: str
    direct_evidence: str
    alternatives: str
    difference_explanation: str
    scope_warning: str | None
    falsifier: str
    impact_object: str


class ComparisonCellDTO(V1Model):
    """竞争性因素比较表的单个单元格."""

    factor_id: str
    factor_label: str
    column_id: str
    column_label: str
    text: str


class ComparisonRowDTO(V1Model):
    """竞争性因素比较表中按因素聚合的行."""

    factor_id: str
    factor_label: str
    cells: list[ComparisonCellDTO]


class ComparisonTableDTO(V1Model):
    """竞争性因素比较表（含表头 + 行）."""

    columns: list[str]
    rows: list[ComparisonRowDTO]


class ReproductionStepDTO(V1Model):
    """「结论形成路径」中的单步."""

    sequence: int
    description: str


class SourceCitationDTO(V1Model):
    """「支持、反驳与缺口」下的一条支持/反驳引用."""

    label: str  # 「支持 · 已复现」/「反驳 · 已复现」
    relation: EvidenceRole
    document_title: str
    publisher: str | None
    citation: str  # 逐字引用片段
    locator: str  # 页码定位


class SourceGroupDTO(V1Model):
    """「支持、反驳与缺口」分组."""

    section_label: str  # 「支持 · 已复现」「支持 · 已传导」
    relations: list[SourceCitationDTO]


class ReproductionManifestDTO(V1Model):
    """「解释与重现」右栏的全部内容."""

    current_selection_label: str  # 当前选中因素
    current_selection_state: str  # F-1-01
    formal_judgment: str  # 人工正式判断的当前条目
    research_snapshot: str  # RS-2025-06-30-v3
    document_version: str  # sec-10q-2025-05-28-v1
    publisher_record: str  # issuer-call-2025-04-30-v1
    available_at: str
    reproducer: str
    factor_compare_version: str
    recheck_manifest: str


class GapExplanationDTO(V1Model):
    """「所选因素解释」右栏的因子机制说明."""

    factor_id: str
    factor_label: str
    why: str
    applicable_scope: str
    category: str  # 「适用边界」
    data_pattern: str  # 「数据模式」
    category_alt: str  # 「假设」
    rationale: str


class CausalStepDTO(V1Model):
    """「结论形成路径」中的单步因果."""

    sequence: int
    description: str


class ConclusionResponse(V1Model):
    """「结论与关键因素」页面完整响应."""

    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    header: ConclusionHeaderDTO
    key_factors: list[KeyFactorRowDTO]
    comparison: ComparisonTableDTO
    source_groups: list[SourceGroupDTO]
    reproduction_manifest: ReproductionManifestDTO
    causal_path: list[CausalStepDTO]
    gap_explanation: GapExplanationDTO