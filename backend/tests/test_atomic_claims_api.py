from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
)
from app.services.atomic_claims import AtomicClaimService


def _candidate_for_case(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="原子陈述审核 Case",
        industry_topic="ai",
        created_by="human:owner",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://disclosure.example.org/atomic-claim",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    session.add_all([case, document])
    session.flush()
    session.add(CaseDocumentVersion(
        research_case_id=case.id,
        document_version_id=document.id,
        linked_at=now,
    ))
    session.add(CaseTenantAdmission(
        research_case_id=case.id,
        tenant_id="test-team",
        initial_document_version_id=document.id,
        admitted_by="test-fixture",
        admitted_at=now,
    ))
    source_text = "公司公告：2026年第一季度订单同比增长20%。"
    quote = "订单同比增长20%"
    quote_start = source_text.index(quote)
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 2, "paragraph": 3},
        verbatim_text=source_text,
    )
    session.add(span)
    session.flush()
    candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=quote_start,
            quote_end=quote_start + len(quote),
            normalized_text="公司披露 2026 年第一季度订单同比增长 20%",
            claim_type="disclosed_fact",
            assertion_actor="公司",
            subject="订单",
            predicate="同比增长",
            object_text="20%",
            numeric_value="20",
            unit="%",
            observed_period=None,
            scope={"company": "测试公司"},
        ),
        authority_level="primary_disclosure",
        run_ref="extract:fixture",
    )
    session.commit()
    return case, candidate


def test_atomic_claim_queue_exposes_source_quote_and_review_history(cmd_client, cmd_session) -> None:
    case, candidate = _candidate_for_case(cmd_session)

    response = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == str(candidate.id)
    assert item["quote"] == "订单同比增长20%"
    assert item["quote_start"] == 14
    assert item["quote_end"] == 23
    assert item["locator"] == {"page": 2, "paragraph": 3}
    assert item["review_state"] == "awaiting_review"
    assert item["review_history"] == []


def test_atomic_claim_review_api_publishes_only_after_human_decision(cmd_client, cmd_session) -> None:
    case, candidate = _candidate_for_case(cmd_session)

    response = cmd_client.post(
        f"/api/v1/atomic-claims/{candidate.id}/reviews",
        json={
            "outcome": "modified",
            "normalized_text": "审核后确认：2026 年第一季度订单同比增长 20%",
            "reviewer": "human:reviewer",
            "reason": "已复核原文、主体和期间",
            "idempotency_key": "atomic-review-1",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "modified"
    assert body["published_source_statement"]["normalized_text"] == "审核后确认：2026 年第一季度订单同比增长 20%"

    queue = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims")
    item = queue.json()["items"][0]
    assert item["review_state"] == "modified"
    assert item["review_history"][0]["reason"] == "已复核原文、主体和期间"
    assert item["published_source_statement"]["id"] == body["published_source_statement"]["id"]
