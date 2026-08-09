from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    DocumentVersion,
    ImmutableLedgerError,
    SourceSpan,
    SourceStatement,
)
from app.services.atomic_claims import AtomicClaimService


def test_atomic_candidate_and_review_are_append_only(session) -> None:
    now = datetime.now(timezone.utc)
    document = DocumentVersion(
        content_sha256="a" * 64,
        source_url="https://license-safe.example.org/report",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="公司预计下一季度交付量同比增长。",
    )
    session.add(span)
    session.flush()

    service = AtomicClaimService(session)
    quote = "公司预计下一季度交付量同比增长。"
    candidate = service.admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=0,
            quote_end=len(quote),
            normalized_text="公司预计下一季度交付量同比增长",
            claim_type="forecast",
            assertion_actor="company_management",
            subject="目标公司",
            predicate="预计交付量增长",
            object_text=None,
            numeric_value=None,
            unit=None,
            observed_period=None,
            scope={"company_id": "company-a"},
        ),
        authority_level="primary_disclosure",
        run_ref="test:atomic-claim-v1",
    )
    review = service.review(
        candidate.id,
        outcome="rejected",
        reviewer="human:reviewer",
        reason="预测不能直接作为已披露事实。",
        idempotency_key="review-1",
    )

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(type(candidate))
            .where(type(candidate).id == candidate.id)
            .values(quote="被改写")
        )
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(type(review))
            .where(type(review).id == review.id)
            .values(reason="被改写")
        )


def test_confirmed_atomic_claim_is_the_only_path_that_publishes_a_statement(session) -> None:
    now = datetime.now(timezone.utc)
    document = DocumentVersion(content_sha256="b" * 64, source_url="https://license-safe.example.org/confirmed", available_at=now, acquired_at=now, parser_version="fixture-v1")
    session.add(document)
    session.flush()
    quote = "公司预计下一季度交付量同比增长。"
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text=quote)
    session.add(span)
    session.flush()
    service = AtomicClaimService(session)
    candidate = service.admit(AtomicClaimDraft(source_span_id=span.id, quote=quote, quote_start=0, quote_end=len(quote), normalized_text="公司预计下一季度交付量同比增长", claim_type="forecast", assertion_actor="company_management", subject="目标公司", predicate="预计交付量增长", object_text=None, numeric_value=None, unit=None, observed_period=None, scope={"company_id": "company-a"}), authority_level="primary_disclosure", run_ref="test:publish-v1")

    rejected = service.review(candidate.id, outcome="rejected", reviewer="human:reviewer", reason="不构成正式陈述", idempotency_key="reject-1")
    assert rejected.published_source_statement_id is None
    assert list(session.scalars(select(SourceStatement))) == []

    confirmed = service.review(candidate.id, outcome="confirmed", reviewer="human:reviewer", reason="逐字原文与分类已核对", idempotency_key="confirm-1")
    statement = session.get(SourceStatement, confirmed.published_source_statement_id)
    assert statement is not None
    assert statement.atomic_claim_candidate_id == candidate.id
    assert statement.kind == "forecast"
