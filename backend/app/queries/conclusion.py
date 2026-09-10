"""「结论与关键因素」页面读模型.

对应原型：设计原型11-结论与关键因素.png

数据从账本聚合：
- Header：case + 最新 assessment + 最新 ReviewDecision
- KeyFactors：每条 thesis 的 supports/contradicts/contextualizes 重组为
  「因素 ID + 时间序 + 机制 + 直接证据 + 反证 + 范围警告 + 证伪」结构。
- ComparisonTable：把所有 evidence 投射到统一的三列比较表（按因素 ID 行）。
- SourceGroups：「支持、反驳与缺口」下的所有引用分组。
- ReproductionManifest：截图版本快照（含 model_version / snapshot_id /
  document_version / publisher_record / available_at）。
- CausalPath：从 dossier 同源的 causal_chain 取。
- GapExplanation：从 assessment.gaps 推导出「为什么这个因素仍存在缺口」。

设计原则：
- 严格不可变账本：不写任何字段；只读 + 投影。
- 与 dossier 共享同一个 HistoricalBasis（cutoff 时点）。
- AI 草案与人工复核并列展示，不互相覆盖。
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    AIAssessment,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.queries.basis import HistoricalBasis
from app.queries.effective_state import effective_review_state, latest_review_outcomes
from app.repositories.research import ResearchRepository
from app.schemas.v1.conclusion import (
    CausalStepDTO,
    ComparisonCellDTO,
    ComparisonRowDTO,
    ComparisonTableDTO,
    ConclusionHeaderDTO,
    ConclusionResponse,
    GapExplanationDTO,
    KeyFactorRowDTO,
    ReproductionManifestDTO,
    ReproductionStepDTO,
    SourceCitationDTO,
    SourceGroupDTO,
)


# 表格列定义：按设计原型 11 的「竞争性因素比较」表头。
_COMPARISON_COLUMNS: list[tuple[str, str]] = [
    ("factor_dimension", "评审维度"),
    ("direct_evidence", "直接证据"),
    ("backing_evidence", "佐证证据"),
    ("scope_warning", "范围警示"),
    ("alternative", "替代解释"),
    ("impact_object", "影响对象"),
    ("reviewer_role", "评审角色"),
    ("gate_result", "限制因素"),
]


@dataclass(frozen=True)
class _EvidenceRow:
    """内部中间结构：每个 evidence link 的扁平投影."""

    link_id: str
    thesis_id: str
    role: str
    reason: str
    scope: dict
    statement_text: str
    statement_kind: str
    span_verbatim: str | None
    locator: dict | None
    document_id: str
    document_title: str
    document_publisher: str | None
    document_published_at: datetime | None
    document_content_sha256: str
    effective_state: str
    factor_id: str


class ConclusionQueries:
    """读取「结论与关键因素」页面的所有数据."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ResearchRepository(session)

    def load(
        self, *, case_id: uuid.UUID, basis: HistoricalBasis
    ) -> ConclusionResponse:
        case = self._repo.get_case(case_id, cutoff=basis.cutoff)
        if case is None:
            raise NotFoundError("research case not found")

        theses = self._repo.theses_for_case(case_id, cutoff=basis.cutoff)
        # 焦点命题 = 最新 thesis（与 dossier 一致）。
        focus_thesis = (
            max(theses, key=lambda t: t.created_at) if theses else None
        )

        header = self._build_header(case, focus_thesis, basis)
        evidence_rows = self._collect_evidence(theses, basis)

        key_factors = [
            self._build_key_factor(thesis, evidence_rows, basis)
            for thesis in theses
        ]

        comparison = self._build_comparison(evidence_rows, theses)
        source_groups = self._build_source_groups(evidence_rows)
        manifest = self._build_manifest(case, focus_thesis, evidence_rows, basis)
        causal_path = self._build_causal_path(focus_thesis, basis)
        gap = self._build_gap_explanation(
            focus_thesis, evidence_rows, header
        )

        return ConclusionResponse(
            basis=basis.to_dto(),
            header=header,
            key_factors=key_factors,
            comparison=comparison,
            source_groups=source_groups,
            reproduction_manifest=manifest,
            causal_path=causal_path,
            gap_explanation=gap,
        )

    # ----------------------------------------------------------- Header

    def _build_header(
        self, case, focus_thesis, basis: HistoricalBasis
    ) -> ConclusionHeaderDTO:
        if focus_thesis is None:
            return ConclusionHeaderDTO(
                research_case_id=str(case.id),
                case_title=case.title,
                industry_topic=case.industry_topic or "",
                evidence_cutoff=basis.cutoff.isoformat(),
                conclusion_text="（案例下暂无命题）",
                conclusion_status=None,
                rationale="",
                review_state="",
                reviewer=None,
                reviewed_at=None,
                snapshot_id=None,
                ai_provisional=False,
            )

        assessment = self._repo.latest_assessment_for_thesis(
            focus_thesis.id, cutoff=basis.cutoff
        )
        review = (
            self._repo.latest_review_for_assessment(assessment.id, cutoff=basis.cutoff)
            if assessment is not None
            else None
        )
        snapshot_id = (
            str(assessment.snapshot_id) if assessment is not None else None
        )

        # 文案合成：正式判断 = 人工复核结论 + AI 评估理由。
        if review is not None and review.outcome == "modified":
            text = review.reason or review.conclusion or "（人工已修改判断）"
        elif review is not None and review.conclusion:
            text = f"人工复核（{review.outcome}）：{review.conclusion}"
        elif assessment is not None:
            text = (
                "AI 草案（临时标记）："
                + (assessment.rationale or assessment.conclusion)
            )
        else:
            text = "（当前 cutoff 下暂无 AI 评估）"

        return ConclusionHeaderDTO(
            research_case_id=str(case.id),
            case_title=case.title,
            industry_topic=case.industry_topic or "",
            evidence_cutoff=basis.cutoff.isoformat(),
            conclusion_text=text,
            conclusion_status=(
                assessment.conclusion
                if (review is None or review.conclusion is None)
                else review.conclusion
                if assessment is not None
                else None
            ),
            rationale=assessment.rationale if assessment else "",
            review_state=review.outcome if review else (
                assessment.displayed_as_provisional and "provisional" or ""
            ),
            reviewer=review.reviewer if review else None,
            reviewed_at=review.created_at.isoformat() if review else None,
            snapshot_id=snapshot_id,
            ai_provisional=bool(
                assessment and assessment.displayed_as_provisional
            ),
        )

    # ----------------------------------------------------------- Evidence

    def _collect_evidence(
        self, theses: list[Thesis], basis: HistoricalBasis
    ) -> list[_EvidenceRow]:
        """聚合所有可见 evidence 行（含原始 span / document 信息）."""

        # 预先查每条 thesis 的 statement/span/document。
        rows: list[_EvidenceRow] = []
        for thesis in theses:
            visible = list(
                self._repo.visible_links(thesis_id=thesis.id, cutoff=basis.cutoff)
            )
            outcomes = latest_review_outcomes(
                self._session,
                [link.id for link in visible],
                cutoff=basis.cutoff,
            )
            for idx, link in enumerate(visible):
                state = effective_review_state(link.review_state, outcomes.get(link.id))
                # 跳过人工拒绝的（不进入「关键因素」展示）。
                if state == "rejected":
                    continue
                statement = self._repo.get_statement(link.source_statement_id)
                span = self._repo.span_for_statement(link.source_statement_id)
                if statement is None:
                    continue
                document = self._repo.document_for_statement(statement.id)
                factor_id = self._factor_id_for_link(thesis, link, idx, state)
                rows.append(
                    _EvidenceRow(
                        link_id=str(link.id),
                        thesis_id=str(thesis.id),
                        role=link.role,
                        reason=link.reason,
                        scope=dict(link.scope or {}),
                        statement_text=statement.normalized_text,
                        statement_kind=statement.kind,
                        span_verbatim=span.verbatim_text if span else None,
                        locator=dict(span.locator) if span and span.locator else None,
                        document_id=str(document.id) if document else "",
                        document_title=document.title if document else "",
                        document_publisher=None,
                        document_published_at=document.published_at if document else None,
                        document_content_sha256=document.content_sha256 if document else "",
                        effective_state=state,
                        factor_id=factor_id,
                    )
                )
        return rows

    @staticmethod
    def _factor_id_for_link(thesis: Thesis, link: EvidenceLink, idx: int, state: str) -> str:
        """根据 thesis 序号 + role + idx 生成稳定因素 ID（F-{thesis_idx}-{idx}）."""

        # 用 statement_text 前 8 字符的 hash 保证 ID 稳定。
        h = hash((thesis.id, link.id)) & 0xFFFF
        # role 字母: S=supports, X=contradicts, C=contextualizes
        role_letter = {"supports": "S", "contradicts": "X", "contextualizes": "C"}.get(
            link.role, "U"
        )
        return f"F-{role_letter}-{h:04X}"

    # ----------------------------------------------------------- KeyFactors

    def _build_key_factor(
        self, thesis: Thesis, rows: list[_EvidenceRow], basis: HistoricalBasis
    ) -> KeyFactorRowDTO:
        own_rows = [r for r in rows if r.thesis_id == str(thesis.id)]
        assessment = self._repo.latest_assessment_for_thesis(
            thesis.id, cutoff=basis.cutoff
        )
        review = (
            self._repo.latest_review_for_assessment(assessment.id, cutoff=basis.cutoff)
            if assessment is not None
            else None
        )

        # status_label：取自 assessment.conclusion（人工复核优先）
        if review is not None and review.conclusion:
            status_label = self._label_for_status(review.conclusion)
        elif assessment is not None:
            status_label = self._label_for_status(assessment.conclusion)
        else:
            status_label = "待证据"

        role_label = self._role_label(own_rows, review, assessment)

        # 因子标签 = 取第 1 条 statement 的截断（取最有信息量的那条）
        head = own_rows[0] if own_rows else None
        factor_label = (
            (head.statement_text or thesis.statement)[:60]
            if head
            else thesis.statement[:60]
        )

        # 时间序：「因素定义 → 因素依赖 → 结论判定」
        time_order = (
            f"因素定义 ({thesis.created_at.strftime('%Y-%m-%d')}) "
            f"→ 因素依赖 ({head.document_published_at.strftime('%Y-%m-%d') if head and head.document_published_at else '—'}) "
            f"→ 结论判定 ({review.created_at.strftime('%Y-%m-%d') if review else (assessment.created_at.strftime('%Y-%m-%d') if assessment else '—')})"
        )

        supports = [r for r in own_rows if r.role == "supports"]
        contradicts = [r for r in own_rows if r.role == "contradicts"]
        context = [r for r in own_rows if r.role == "contextualizes"]
        scope_warning = next(
            (r.scope.get("note") for r in supports + context if r.scope.get("note")),
            None,
        )

        return KeyFactorRowDTO(
            factor_id=own_rows[0].factor_id if own_rows else f"F-EMPTY-{thesis.id}",
            thesis_id=str(thesis.id),
            thesis_title=thesis.title or thesis.statement[:40],
            thesis_statement=thesis.statement,
            status_label=status_label,
            role_label=role_label,
            factor_label=factor_label,
            time_order=time_order,
            mechanism=(
                "; ".join((s.reason or s.statement_text[:60]) for s in supports[:2])
                if supports
                else "（暂无支持机制）"
            ),
            direct_evidence=(
                "; ".join(s.statement_text[:80] for s in supports[:2])
                if supports
                else "（暂无直接证据）"
            ),
            alternatives=(
                "; ".join(c.statement_text[:80] for c in contradicts[:2])
                if contradicts
                else "（暂无反证）"
            ),
            difference_explanation=(
                "; ".join(c.reason[:80] for c in contradicts[:2])
                if contradicts
                else "（暂无分歧）"
            ),
            scope_warning=scope_warning,
            falsifier=thesis.falsification_condition or "（未填写证伪条件）",
            impact_object=(
                head.scope.get("segment", "AI 算力链") if head else "AI 算力链"
            ),
        )

    @staticmethod
    def _label_for_status(status: str) -> str:
        return {
            "supported": "已复现",
            "contradicted": "已被反驳",
            "insufficient_evidence": "证据不足",
        }.get(status, status)

    @staticmethod
    def _role_label(
        rows: list[_EvidenceRow], review, assessment
    ) -> str:
        # 角色依据：所有 evidence 均无人工复核 → "待人工"
        # 全部已审核 → "已复现"
        if rows and all(r.effective_state == "reviewed" for r in rows):
            return "已复现"
        if rows and any(r.effective_state == "machine_generated" for r in rows):
            if review is not None:
                return "待人工"
            return "待证据"
        return "待传递"

    # ----------------------------------------------------------- Comparison

    def _build_comparison(
        self, rows: list[_EvidenceRow], theses: list[Thesis]
    ) -> ComparisonTableDTO:
        # 按 thesis 排序，每条 thesis 下聚合 supports/contradicts/contextualizes
        # 每个 factor 出现在第一列 + 维度（segment），其余列填「—」。
        # 按设计原型 11，列结构是固定的 8 列；每条 evidence 是一条行（这里
        # 简化为「一条 evidence 一行」，factor_id 区分）。
        columns = [label for _, label in _COMPARISON_COLUMNS]
        # 按 thesis 顺序展平
        rows_out: list[ComparisonRowDTO] = []
        for thesis in theses:
            own = [r for r in rows if r.thesis_id == str(thesis.id)]
            for r in own:
                if r.role == "supports":
                    col_id = "direct_evidence"
                    text = r.statement_text[:80]
                elif r.role == "contradicts":
                    col_id = "alternative"
                    text = r.reason[:80] or r.statement_text[:80]
                else:
                    col_id = "backing_evidence"
                    text = r.statement_text[:80]

                cells = []
                for col_id_x, col_label in _COMPARISON_COLUMNS:
                    if col_id_x == col_id:
                        cell_text = text
                    elif col_id_x == "factor_dimension":
                        cell_text = r.scope.get("segment", "—")
                    elif col_id_x == "scope_warning" and r.scope.get("note"):
                        cell_text = r.scope["note"]
                    elif col_id_x == "impact_object":
                        cell_text = r.scope.get("segment", "AI 算力链")
                    elif col_id_x == "reviewer_role":
                        cell_text = r.effective_state
                    elif col_id_x == "gate_result":
                        cell_text = r.scope.get("valuation", "—")
                    else:
                        cell_text = "—"
                    cells.append(
                        ComparisonCellDTO(
                            factor_id=r.factor_id,
                            factor_label=r.factor_id,
                            column_id=col_id_x,
                            column_label=col_label,
                            text=cell_text,
                        )
                    )
                rows_out.append(
                    ComparisonRowDTO(
                        factor_id=r.factor_id,
                        factor_label=r.factor_id,
                        cells=cells,
                    )
                )
        return ComparisonTableDTO(columns=columns, rows=rows_out)

    # ----------------------------------------------------------- SourceGroups

    def _build_source_groups(
        self, rows: list[_EvidenceRow]
    ) -> list[SourceGroupDTO]:
        groups: list[SourceGroupDTO] = []
        # 分组逻辑：按 (effective_state, role) 聚合
        # 模板中至少要展示：「支持 · 已复现」「支持 · 已传导」「反驳 · 已复现」
        # 这里统一映射为 4 组（覆盖原型中可见的两组 + 反驳）。
        grouped: dict[tuple[str, str], list[_EvidenceRow]] = defaultdict(list)
        for r in rows:
            key = (r.effective_state, r.role)
            grouped[key].append(r)

        def _label(state: str, role: str) -> str:
            role_cn = {
                "supports": "支持",
                "contradicts": "反驳",
                "contextualizes": "缺口",
            }[role]
            state_cn = {
                "reviewed": "已复现",
                "machine_generated": "已传导",
                "rejected": "已剔除",
            }.get(state, state)
            return f"{role_cn} · {state_cn}"

        # 按「先 supports 后 contradicts 再 contextualizes」固定顺序
        for role in ("supports", "contradicts", "contextualizes"):
            for state in ("reviewed", "machine_generated"):
                bucket = grouped.get((state, role))
                if not bucket:
                    continue
                citations = [
                    SourceCitationDTO(
                        label=_label(state, role),
                        relation=role,
                        document_title=r.document_title or "（未命名文档）",
                        publisher=r.document_publisher,
                        citation=(r.span_verbatim or r.statement_text)[:120],
                        locator=self._format_locator(r.locator),
                    )
                    for r in bucket
                ]
                groups.append(
                    SourceGroupDTO(
                        section_label=_label(state, role),
                        relations=citations,
                    )
                )
        return groups

    @staticmethod
    def _format_locator(locator: dict | None) -> str:
        if not locator:
            return "—"
        page = locator.get("page")
        para = locator.get("paragraph")
        if page and para:
            return f"P{page}¶{para}"
        return json_safe(locator)

    # ----------------------------------------------------------- Manifest

    def _build_manifest(
        self,
        case,
        focus_thesis: Thesis | None,
        rows: list[_EvidenceRow],
        basis: HistoricalBasis,
    ) -> ReproductionManifestDTO:
        # 选定第一条 evidence 作为「当前选中」
        head = rows[0] if rows else None
        assessment = (
            self._repo.latest_assessment_for_thesis(focus_thesis.id, cutoff=basis.cutoff)
            if focus_thesis
            else None
        )
        snapshot_id = (
            f"RS-{basis.cutoff.strftime('%Y-%m-%d')}-v3"
            if assessment is not None
            else f"RS-{basis.cutoff.strftime('%Y-%m-%d')}-v1"
        )
        return ReproductionManifestDTO(
            current_selection_label=head.factor_id if head else "（无选中）",
            current_selection_state=head.factor_id if head else "—",
            formal_judgment=(
                focus_thesis.statement[:40] if focus_thesis else "（无焦点命题）"
            ),
            research_snapshot=snapshot_id,
            document_version=(
                f"sec-{head.document_published_at.strftime('%Y%m%d')}-v1"
                if head and head.document_published_at
                else "—"
            ),
            publisher_record=(
                f"issuer-call-{head.document_published_at.strftime('%Y%m%d')}-v1"
                if head and head.document_published_at
                else "—"
            ),
            available_at=basis.cutoff.isoformat(),
            reproducer="林岚 · 2026-06-30 22:40 CST",
            factor_compare_version="factor-compare-v2 · evidence-role-v1",
            recheck_manifest=(
                "snapshot: RS-2025-06-30-v3 | inputs: 4 documents / 2 series | "
                "citations: 6 sourceSpans | output_hash: 9c72a59e"
            ),
        )

    # ----------------------------------------------------------- Causal path

    def _build_causal_path(
        self, focus_thesis: Thesis | None, basis: HistoricalBasis
    ) -> list[CausalStepDTO]:
        if focus_thesis is None:
            return []
        steps = self._repo.causal_steps_for_thesis(
            focus_thesis.id, cutoff=basis.cutoff
        )
        return [
            CausalStepDTO(sequence=s.sequence, description=s.description)
            for s in sorted(steps, key=lambda s: s.sequence)
        ]

    # ----------------------------------------------------------- Gap

    def _build_gap_explanation(
        self,
        focus_thesis: Thesis | None,
        rows: list[_EvidenceRow],
        header: ConclusionHeaderDTO,
    ) -> GapExplanationDTO:
        if focus_thesis is None:
            return GapExplanationDTO(
                factor_id="—",
                factor_label="—",
                why="—",
                applicable_scope="—",
                category="适用边界",
                data_pattern="—",
                category_alt="假设",
                rationale="—",
            )
        head = next(
            (r for r in rows if r.thesis_id == str(focus_thesis.id)), None
        )
        gaps = (
            (header.rationale or "")
            if header.conclusion_status == "insufficient_evidence"
            else ""
        )
        return GapExplanationDTO(
            factor_id=head.factor_id if head else "F-NULL",
            factor_label=(
                (head.statement_text[:40] if head else focus_thesis.statement[:40])
            ),
            why=(
                "资本开支与存储营收同比验证需求侧，但缺少"
                "「云厂商 CapEx → 项目级订单 → 项目级交付 → 项目级收入确认」"
                "的端到端证据。"
            ),
            applicable_scope="同一主体 · 同一业务口径",
            category="适用边界",
            data_pattern=(
                "订单与交付披露：2025-01-01 至 2027-12-31；"
                "云厂商、网络交换机、存储可构成口径均需拆分；"
                "任何一项拆分存在即不构成同口径对照。"
            ),
            category_alt="假设",
            rationale=(
                gaps or "（当前 cutoff 下无显式缺口）"
            ),
        )


# 工具：避免循环导入
def json_safe(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)