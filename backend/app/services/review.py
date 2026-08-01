"""Link-level human review of AI-proposed evidence (prototype 审核工作区).

One EvidenceReview judges exactly one EvidenceLink.  The four fields the
prototype marks 必填 — 关系选择 (relation), 因素角色 (factor_role), 适用边界
(scope_boundary), 审核理由 (reason) — are validated here, not in the router,
so the same guarantees hold for CLI and test callers.  The action is
``outcome``: 确认并写入审核知识 (confirmed) / 驳回 (rejected) / 要求补充证据
(needs_more_evidence).

The reviewed link is never mutated (append-only ledger).  A ``confirmed``
review whose ``relation`` differs from the link's AI-proposed ``role`` is a
*modification* expressed as data: readers resolve the latest review per link.
"""
from __future__ import annotations

import uuid

from app.errors import NotFoundError
from app.models.ledger import EvidenceLink, EvidenceReview, ValidationError
from app.repositories.research import ResearchRepository

_LINK_REVIEW_OUTCOMES = frozenset({"confirmed", "rejected", "needs_more_evidence"})
_LINK_REVIEW_RELATIONS = frozenset(
    {"supports", "contradicts", "contextualizes", "evidence_gap"}
)


class ReviewService:
    """Admits link-level reviews with service-layer validation."""

    def __init__(self, repository: ResearchRepository) -> None:
        self._repo = repository

    def review_link(
        self,
        evidence_link_id: uuid.UUID,
        *,
        outcome: str,
        relation: str | None,
        factor_role: str,
        scope_boundary: str,
        reason: str,
        reviewer: str,
    ) -> EvidenceReview:
        link = self._repo.get_evidence_link(evidence_link_id)
        if link is None:
            raise NotFoundError(f"evidence link {evidence_link_id} not found")
        if outcome not in _LINK_REVIEW_OUTCOMES:
            raise ValidationError(f"invalid link review outcome: {outcome}")
        if outcome == "confirmed":
            # 确认写入时必须给出关系选择；驳回/要求补证据可不选。
            if relation is None:
                raise ValidationError(
                    "relation is required when outcome is confirmed"
                )
            if relation not in _LINK_REVIEW_RELATIONS - {"evidence_gap"}:
                raise ValidationError(
                    f"invalid relation for confirmed review: {relation}"
                )
        elif relation is not None and relation not in _LINK_REVIEW_RELATIONS:
            raise ValidationError(f"invalid link review relation: {relation}")
        for field, value in (
            ("factor_role", factor_role),
            ("scope_boundary", scope_boundary),
            ("reason", reason),
        ):
            if not value or not value.strip():
                raise ValidationError(f"{field} must not be empty")
        if not reviewer.strip():
            raise ValidationError("reviewer must not be empty")
        return self._repo.insert_evidence_review(
            evidence_link_id=link.id,
            outcome=outcome,
            relation=relation,
            factor_role=factor_role.strip(),
            scope_boundary=scope_boundary.strip(),
            reason=reason.strip(),
            reviewer=reviewer.strip(),
        )

    def reviewed_link_ids(self, link_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Links that already carry at least one human review."""
        reviews = self._repo.evidence_reviews_for_links(link_ids)
        return {review.evidence_link_id for review in reviews}
