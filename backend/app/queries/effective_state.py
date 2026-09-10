"""Effective review-state derivation for append-only evidence links.

Every ledger table is append-only: ``EvidenceLink.review_state`` is frozen at
insert (AI-proposed links stay ``machine_generated`` forever). Human review
outcomes live in append-only ``evidence_reviews`` rows, so read models must
derive the *effective* state from the latest review rather than the frozen
column — otherwise human-confirmed links would never enter the 已复核 dossier,
the reviewed knowledge layer, or the non-research-mode graph.

Mapping: ``confirmed`` -> ``reviewed``, ``rejected`` -> ``rejected``,
``needs_more_evidence`` -> ``machine_generated`` (still pending).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import EvidenceReview

OUTCOME_TO_STATE = {
    "confirmed": "reviewed",
    "rejected": "rejected",
    "needs_more_evidence": "machine_generated",
}


def effective_review_state(link_state: str, latest_outcome: str | None) -> str:
    """Fold the latest human review outcome over the frozen column value."""
    if latest_outcome is None:
        return link_state
    return OUTCOME_TO_STATE.get(latest_outcome, link_state)


def _naive(dt: datetime) -> datetime:
    # Ledger datetimes may mix naive/aware; compare as naive UTC.
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def latest_review_outcomes(
    db: Session,
    link_ids: list[uuid.UUID],
    *,
    cutoff: datetime | None = None,
) -> dict[uuid.UUID, str]:
    """Latest review outcome per link id, optionally bounded by cutoff.

    Reviews created after the cutoff do not exist for historical replay, so
    they are excluded when ``cutoff`` is given.
    """
    if not link_ids:
        return {}
    query = select(
        EvidenceReview.evidence_link_id,
        EvidenceReview.outcome,
        EvidenceReview.created_at,
    ).where(EvidenceReview.evidence_link_id.in_(link_ids))
    latest: dict[uuid.UUID, tuple[str, datetime]] = {}
    for link_id, outcome, created_at in db.execute(query):
        if cutoff is not None and _naive(created_at) > _naive(cutoff):
            continue
        prev = latest.get(link_id)
        if prev is None or _naive(created_at) > _naive(prev[1]):
            latest[link_id] = (outcome, created_at)
    return {link_id: outcome for link_id, (outcome, _) in latest.items()}
