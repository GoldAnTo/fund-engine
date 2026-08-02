"""Research-operations KPI v1 wire DTOs (研究效能度量).

Management-facing read model over the append-only ledger: how fast human
review clears the AI proposal queue (吞吐), how often humans agree with the
machine (一致率), and how long judgments lag the evidence they rest on
(时滞).  All figures are derived from ledger records only — no self-reported
numbers, no persisted scores.
"""
from __future__ import annotations

from app.schemas.v1.common import V1Model


class ReviewThroughputDTO(V1Model):
    """审核吞吐: review output vs the pending machine-generated queue."""

    link_reviews_total: int
    link_reviews_last_7d: int
    assessment_reviews_total: int
    assessment_reviews_last_7d: int
    reviews_by_reviewer: dict[str, int]
    pending_link_reviews: int
    pending_assessment_reviews: int


class HumanAiAgreementDTO(V1Model):
    """人机一致率: agreement between AI drafts and human decisions.

    Assessment level: a ``confirmed`` ReviewDecision is agreement; a
    ``modified``/``rejected`` one is not; ``conclusion_changed`` counts
    reviews whose conclusion differs from the original AI conclusion.

    Link level: a ``confirmed`` EvidenceReview whose ``relation`` equals the
    link's AI-proposed ``role`` is agreement; confirmed with a different
    relation is a modification (counted separately); ``rejected`` and
    ``needs_more_evidence`` are disagreement / deferral.
    """

    assessment_outcomes: dict[str, int]
    assessment_agreement_rate: float | None
    conclusion_changed: int
    link_outcomes: dict[str, int]
    link_agreement_rate: float | None
    link_modified: int


class JudgmentLatencyDTO(V1Model):
    """判断时滞 (days): evidence -> AI judgment, and AI judgment -> human review."""

    evidence_to_assessment_avg_days: float | None
    evidence_to_assessment_max_days: float | None
    assessment_to_review_avg_days: float | None
    assessment_to_review_max_days: float | None


class ResearchOpsResponse(V1Model):
    """研究效能 KPI 快照. ``as_of`` 为时点回放边界：之后创建的记录不参与统计。"""

    as_of: str
    case_id: str | None
    throughput: ReviewThroughputDTO
    agreement: HumanAiAgreementDTO
    latency: JudgmentLatencyDTO
