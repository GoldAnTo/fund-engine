"""Tests for the research-operations KPI endpoint (研究效能度量).

Covers the three KPI blocks against the frozen AI-compute slice and against
hand-built review activity: 审核吞吐 (throughput + pending queue using
effective review state), 人机一致率 (assessment- and link-level agreement),
判断时滞 (evidence→assessment and assessment→review latency), plus
point-in-time replay via ``as_of`` and case scoping.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.ledger import EvidenceLink, EvidenceReview


def test_kpis_on_seeded_slice(api_client, seeded_session):
    resp = api_client.get("/api/v1/research-ops/kpis")
    assert resp.status_code == 200
    body = resp.json()

    throughput = body["throughput"]
    # frozen slice: 3 assessments each with one confirming human review
    assert throughput["assessment_reviews_total"] == 3
    assert throughput["assessment_reviews_last_7d"] == 3
    assert throughput["pending_assessment_reviews"] == 0
    assert throughput["reviews_by_reviewer"] == {"seed-human-reviewer": 3}
    # seeded gold links are human-curated but frozen as machine_generated
    # with no EvidenceReview rows -> they count as pending link reviews
    assert throughput["pending_link_reviews"] == 15
    assert throughput["link_reviews_total"] == 0

    agreement = body["agreement"]
    assert agreement["assessment_outcomes"] == {"confirmed": 3}
    assert agreement["assessment_agreement_rate"] == 1.0
    assert agreement["conclusion_changed"] == 0
    # no link-level reviews in the seed -> rates are null, never fabricated
    assert agreement["link_agreement_rate"] is None
    assert agreement["link_outcomes"] == {}

    latency = body["latency"]
    # seed freezes evidence and assesses within the same run -> near-zero lag
    assert latency["evidence_to_assessment_avg_days"] is not None
    assert latency["evidence_to_assessment_avg_days"] >= 0
    assert latency["assessment_to_review_avg_days"] is not None
    assert latency["assessment_to_review_avg_days"] >= 0


def test_kpis_point_in_time_replay(api_client, seeded_session):
    """as_of replay: records created after the boundary do not exist."""
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    resp = api_client.get("/api/v1/research-ops/kpis", params={"as_of": past})
    assert resp.status_code == 200
    body = resp.json()
    # the seed ran moments ago, so one day back the ledger is empty
    assert body["throughput"]["assessment_reviews_total"] == 0
    assert body["throughput"]["pending_assessment_reviews"] == 0
    assert body["agreement"]["assessment_agreement_rate"] is None
    assert body["latency"]["evidence_to_assessment_avg_days"] is None


def test_kpis_case_scoping(api_client, seeded_session):
    from app.models.ledger import ResearchCase

    case_id = seeded_session.scalar(select(ResearchCase.id))
    resp = api_client.get(
        "/api/v1/research-ops/kpis", params={"case_id": str(case_id)}
    )
    assert resp.status_code == 200
    assert resp.json()["case_id"] == str(case_id)
    assert resp.json()["throughput"]["assessment_reviews_total"] == 3

    resp = api_client.get(
        "/api/v1/research-ops/kpis", params={"case_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


def test_kpis_link_level_agreement_and_pending_queue(api_client, seeded_session):
    """Link reviews move items out of the pending queue and feed agreement:
    confirmed-with-same-relation agrees, confirmed-with-different-relation
    is a modification, rejection disagrees."""
    links = list(seeded_session.scalars(select(EvidenceLink)).all())
    assert len(links) >= 3

    reviews = [
        # same relation as the AI-proposed role -> agreement
        EvidenceReview(
            id=uuid.uuid4(),
            evidence_link_id=links[0].id,
            outcome="confirmed",
            relation=links[0].role,
            factor_role="直接证据",
            scope_boundary="2026年内",
            reason="关系判定一致",
            reviewer="analyst-a",
            created_at=datetime.now(UTC),
        ),
        # confirmed but relation changed -> modification
        EvidenceReview(
            id=uuid.uuid4(),
            evidence_link_id=links[1].id,
            outcome="confirmed",
            relation="contextualizes"
            if links[1].role != "contextualizes"
            else "supports",
            factor_role="背景证据",
            scope_boundary="仅限公司口径",
            reason="降级为背景证据",
            reviewer="analyst-a",
            created_at=datetime.now(UTC),
        ),
        # rejection -> disagreement
        EvidenceReview(
            id=uuid.uuid4(),
            evidence_link_id=links[2].id,
            outcome="rejected",
            relation=None,
            factor_role="不适用",
            scope_boundary="不适用",
            reason="证据与命题无关",
            reviewer="analyst-b",
            created_at=datetime.now(UTC),
        ),
    ]
    for review in reviews:
        seeded_session.add(review)
    seeded_session.flush()

    resp = api_client.get("/api/v1/research-ops/kpis")
    assert resp.status_code == 200
    body = resp.json()

    throughput = body["throughput"]
    assert throughput["link_reviews_total"] == 3
    # confirmed and rejected reviews close the item; all three reviewed
    assert throughput["pending_link_reviews"] == 15 - 3
    assert throughput["reviews_by_reviewer"]["analyst-a"] == 2
    assert throughput["reviews_by_reviewer"]["analyst-b"] == 1

    agreement = body["agreement"]
    assert agreement["link_outcomes"] == {"confirmed": 2, "rejected": 1}
    assert agreement["link_agreement_rate"] == round(1 / 3, 4)
    assert agreement["link_modified"] == 1


def test_kpis_needs_more_evidence_stays_pending(api_client, seeded_session):
    """A needs_more_evidence review does not close the pending item."""
    link = seeded_session.scalars(select(EvidenceLink)).first()
    seeded_session.add(
        EvidenceReview(
            id=uuid.uuid4(),
            evidence_link_id=link.id,
            outcome="needs_more_evidence",
            relation="evidence_gap",
            factor_role="待补证据",
            scope_boundary="分部数据缺失",
            reason="需要分部披露",
            reviewer="analyst-a",
            created_at=datetime.now(UTC),
        )
    )
    seeded_session.flush()

    resp = api_client.get("/api/v1/research-ops/kpis")
    assert resp.status_code == 200
    throughput = resp.json()["throughput"]
    assert throughput["link_reviews_total"] == 1
    assert throughput["pending_link_reviews"] == 15  # still pending
