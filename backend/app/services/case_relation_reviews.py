"""Append-only human resolution of AI-proposed Case relations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import CaseRelation, CaseRelationReview
from app.models.ledger import ValidationError


_OUTCOMES = frozenset({"confirmed", "modified", "rejected", "needs_more_evidence"})
_RELATION_TYPES = frozenset(
    {"shared_driver", "follow_up_validation", "potential_conflict", "shared_material"}
)
_TERMINAL_OUTCOMES = frozenset({"confirmed", "modified", "rejected"})


class CaseRelationReviewService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def review(
        self,
        candidate_id: uuid.UUID,
        *,
        outcome: str,
        relation_type: str,
        reviewer: str,
        reason: str,
        idempotency_key: str,
    ) -> CaseRelationReview:
        candidate = self._session.get(CaseRelation, candidate_id)
        if candidate is None:
            raise ValidationError("case relation candidate not found")
        if candidate.review_state != "machine_generated":
            raise ValidationError("only a machine-generated relation candidate can be reviewed")
        if (
            outcome not in _OUTCOMES
            or relation_type not in _RELATION_TYPES
            or not reviewer.strip()
            or not reason.strip()
            or not idempotency_key.strip()
        ):
            raise ValidationError("case relation review is incomplete or invalid")
        if outcome == "confirmed" and relation_type != candidate.relation_type:
            raise ValidationError("use modified to change the candidate relation type")

        existing = self._session.scalar(
            select(CaseRelationReview).where(
                CaseRelationReview.case_relation_id == candidate_id,
                CaseRelationReview.idempotency_key == idempotency_key.strip(),
            )
        )
        if existing is not None:
            return existing

        latest = self._session.scalar(
            select(CaseRelationReview)
            .where(CaseRelationReview.case_relation_id == candidate_id)
            .order_by(CaseRelationReview.created_at.desc(), CaseRelationReview.id.desc())
            .limit(1)
        )
        if latest is not None and latest.outcome in _TERMINAL_OUTCOMES:
            raise ValidationError("case relation candidate already has a terminal review")

        now = datetime.now(timezone.utc)
        reviewed_relation = None
        if outcome in {"confirmed", "modified"}:
            reviewed_relation = CaseRelation(
                source_case_id=candidate.source_case_id,
                target_case_id=candidate.target_case_id,
                relation_type=relation_type,
                reason=reason.strip(),
                created_by=reviewer.strip(),
                review_state="reviewed",
                created_at=now,
            )
            self._session.add(reviewed_relation)
            self._session.flush()

        review = CaseRelationReview(
            case_relation_id=candidate_id,
            outcome=outcome,
            relation_type=relation_type,
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            idempotency_key=idempotency_key.strip(),
            reviewed_relation_id=reviewed_relation.id if reviewed_relation else None,
            created_at=now,
        )
        self._session.add(review)
        self._session.flush()
        return review
