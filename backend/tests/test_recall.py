"""Tests for retrieval-scoped recall and the de-contaminated proposer.

Covers the pure ranking primitives (tokenize / BM25 / lexical rerank) and
the RecallService guarantees: cutoff visibility (no hindsight leakage),
exclusion of already-linked statements (no duplicate proposals), and
relevance ranking against the thesis text.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.ai.client import LLMClient
from app.ai.proposal import EvidenceProposer
from app.models.ledger import EvidenceLink
from app.repositories.documents import DocumentRepository
from app.services.recall import RecallService, bm25_rank, lexical_rerank, tokenize


# ---------------------------------------------------------------------------
# Pure primitives
# ---------------------------------------------------------------------------


def test_tokenize_mixed_chinese_english():
    tokens = tokenize("GPU 算力需求 Growth 2025")
    assert "gpu" in tokens
    assert "算力需求" in tokens
    assert "growth" in tokens
    assert "2025" in tokens
    # single characters and punctuation dropped
    assert tokenize("a、b") == []


def test_bm25_rank_prefers_relevant_document():
    ids = [uuid.uuid4(), uuid.uuid4()]
    texts = ["GPU 需求 持续 增长 算力", "白酒 消费 疲软 库存"]
    ranked = bm25_rank(ids, texts, "GPU 需求 增长", top_k=2)
    assert ranked[0] == ids[0]
    assert len(ranked) == 1  # zero-score document excluded


def test_bm25_rank_empty_inputs():
    assert bm25_rank([], [], "query", 5) == []
    assert bm25_rank([uuid.uuid4()], ["text"], "", 5) == []
    assert bm25_rank([uuid.uuid4()], ["text"], "query", 0) == []


def test_lexical_rerank_orders_by_overlap():
    ranked = lexical_rerank(
        "算力 需求", ["白酒 消费", "算力 需求 增长", "算力"], top_k=2
    )
    assert ranked[0][0] == 1  # full overlap first
    assert ranked[1][0] == 2  # partial overlap second


def test_lexical_rerank_empty_query_returns_prefix():
    ranked = lexical_rerank("", ["a", "b"], top_k=1)
    assert ranked == [(0, 0.0)]


# ---------------------------------------------------------------------------
# RecallService
# ---------------------------------------------------------------------------


def _add_statement_with_text(document_service, research_service, doc, text):
    span = document_service.add_span(
        document_version_id=doc.id,
        locator={"page": 1},
        verbatim_text=text,
    )
    return research_service.add_statement(
        span.id, text, kind="disclosed_fact"
    )


def test_recall_ranks_relevant_first(
    session, document_service, research_service, thesis, document
):
    relevant = _add_statement_with_text(
        document_service, research_service, document, "GPU demand 增长 强劲"
    )
    _add_statement_with_text(
        document_service, research_service, document, "白酒 消费 疲软 库存 高企"
    )

    recalled = RecallService(session).for_thesis(
        thesis, cutoff=datetime.now(UTC)
    )
    assert recalled
    assert recalled[0].id == relevant.id
    assert all("白酒" not in s.normalized_text for s in recalled)


def test_recall_excludes_future_documents(
    session, research_service, thesis, document_service, document
):
    visible = _add_statement_with_text(
        document_service, research_service, document, "GPU demand 增长"
    )
    # a document that only becomes available tomorrow
    future_repo = DocumentRepository(session)
    future_doc = future_repo.insert_version(
        content_sha256="f" * 64,
        source_url="https://example.test/future",
        published_at=None,
        available_at=datetime.now(UTC) + timedelta(days=1),
        acquired_at=datetime.now(UTC),
        parser_version="test",
        supersedes_id=None,
    )
    _add_statement_with_text(
        document_service, research_service, future_doc, "GPU demand 爆发"
    )

    recalled = RecallService(session).for_thesis(
        thesis, cutoff=datetime.now(UTC)
    )
    recalled_ids = {s.id for s in recalled}
    assert visible.id in recalled_ids
    assert all("爆发" not in s.normalized_text for s in recalled)


def test_recall_excludes_already_linked_statements(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="linked already",
        scope={"segment": "DC"},
    )
    recalled = RecallService(session).for_thesis(
        thesis, cutoff=datetime.now(UTC)
    )
    assert statement.id not in {s.id for s in recalled}


# ---------------------------------------------------------------------------
# Proposer integration
# ---------------------------------------------------------------------------


def test_proposer_uses_recall_scope_and_records_cutoff(
    session, document_service, research_service, thesis, document
):
    relevant = _add_statement_with_text(
        document_service, research_service, document, "GPU demand 预计 增长"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    proposer = EvidenceProposer(client)

    links = proposer.propose(thesis.id, session)
    assert links

    from app.models.ledger import AIRun

    run = session.scalars(select(AIRun).where(AIRun.kind == "propose")).one()
    assert "cutoff" in run.input_ref
    assert set(run.input_ref["statement_ids"]) == {
        str(link.source_statement_id) for link in links
    }
    assert str(relevant.id) in run.input_ref["statement_ids"]


def test_proposer_excludes_irrelevant_statements(
    session, document_service, research_service, thesis, document
):
    _add_statement_with_text(
        document_service, research_service, document, "白酒 消费 疲软 库存"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    links = EvidenceProposer(client).propose(thesis.id, session)
    assert all("白酒" not in str(link.reason) for link in links)


def test_proposer_derives_scope_from_case_when_llm_omits_it(
    session, document_service, research_service, thesis, document
):
    relevant = _add_statement_with_text(
        document_service, research_service, document, "GPU demand 预计 增长"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "links": [
            {
                "source_statement_id": str(relevant.id),
                "role": "supports",
                "reason": "相关",
                # no scope key
            }
        ]
    }
    with patch.object(client, "chat_json", return_value=payload):
        links = EvidenceProposer(client).propose(thesis.id, session)
    assert len(links) == 1
    assert links[0].scope == {"industry_topic": "ai_compute"}


def test_proposer_rerun_creates_no_duplicate_links(
    session, document_service, research_service, thesis, document
):
    _add_statement_with_text(
        document_service, research_service, document, "GPU demand 预计 增长"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    proposer = EvidenceProposer(client)

    first = proposer.propose(thesis.id, session)
    assert first
    second = proposer.propose(thesis.id, session)
    assert second == []

    total = list(
        session.scalars(
            select(EvidenceLink).where(EvidenceLink.thesis_id == thesis.id)
        )
    )
    assert len(total) == len(first)
