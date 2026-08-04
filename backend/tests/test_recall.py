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
from app.services.recall import (
    RecallService,
    bm25_rank,
    char_ngrams,
    lexical_rerank,
    rrf_fuse,
    tfidf_rank,
    tokenize,
)


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
# Dense leg + fusion primitives
# ---------------------------------------------------------------------------


def test_char_ngrams_decompose_cjk_runs():
    grams = char_ngrams("资本开支")
    assert grams == ["资本", "本开", "开支"]
    # short CJK run kept whole; alnum run kept whole
    assert char_ngrams("AI 需求") == ["ai", "需求"]
    assert char_ngrams("") == []


def test_tfidf_rank_recovers_subword_match_bm25_misses():
    ids = [uuid.uuid4(), uuid.uuid4()]
    texts = ["云厂商资本开支高增长指引上调", "白酒消费疲软库存高企"]
    query = "资本开支"
    # coarse tokenizer sees "资本开支" as one token absent from both docs
    assert bm25_rank(ids, texts, query, top_k=2) == []
    # char bigrams recover the sub-word match
    ranked = tfidf_rank(ids, texts, query, top_k=2)
    assert ranked[0] == ids[0]


def test_tfidf_rank_empty_inputs():
    assert tfidf_rank([], [], "query", 5) == []
    assert tfidf_rank([uuid.uuid4()], ["text"], "", 5) == []
    assert tfidf_rank([uuid.uuid4()], ["text"], "query", 0) == []


def test_rrf_fuse_union_semantics():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf_fuse([a, b], [c, a])
    # union of both legs; ``a`` ranks first (present in both lists)
    assert set(fused) == {a, b, c}
    assert fused[0] == a
    assert rrf_fuse([], []) == []


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


def test_recall_hybrid_recovers_dense_only_candidate(
    session, document_service, research_service, document
):
    """A statement with only sub-word overlap is BM25-invisible (the coarse
    tokenizer keeps each CJK run whole) but recoverable via the dense leg."""
    case = research_service.add_case(
        title="capex cycle", industry_topic="capex", created_by="tester"
    )
    zh_thesis = research_service.add_thesis(
        case.id, statement="云厂商资本开支高增长将驱动算力需求", created_by="tester"
    )
    dense_only = _add_statement_with_text(
        document_service,
        research_service,
        document,
        "主要厂商上调年度资本开支指引",
    )
    now = datetime.now(UTC)
    baseline = RecallService(session).for_thesis(zh_thesis, cutoff=now, mode="bm25")
    hybrid = RecallService(session).for_thesis(zh_thesis, cutoff=now, mode="hybrid")
    assert dense_only.id not in {s.id for s in baseline}
    assert dense_only.id in {s.id for s in hybrid}


def test_recall_invalid_mode_rejected(session, thesis):
    import pytest

    with pytest.raises(ValueError, match="unknown recall mode"):
        RecallService(session).for_thesis(
            thesis, cutoff=datetime.now(UTC), mode="dense"
        )


def test_recall_no_per_thesis_dip_vs_bm25():
    """Grid-search-tuned invariant: across all seeded cases, hybrid recall
    must match or beat the BM25 baseline at every (thesis, k) pair.

    Pinned after the 锂电储能链 T2 (碳酸锂 thesis) regression at rrf_k=60,
    which dipped hybrid@10 from 0.5 to 0.25.  rrf_k=30 (see
    ``app.services.recall._RRF_K``) is the only setting that satisfies this
    invariant across the three frozen cases; if a future tuning round
    regresses it, this test fails before ``eval_recall_ab.py`` even runs.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models.ledger import Base, EvidenceLink, ResearchCase, Thesis
    from app.scripts.seed_ai_compute_case import CUTOFF
    from app.scripts.seed_ai_compute_case import seed as seed_ai_compute
    from app.scripts.seed_semiconductor_case import seed as seed_semiconductor
    from app.scripts.seed_storage_chain_case import seed as seed_storage_chain

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine, future=True)
    session = sm()
    for fn in (seed_ai_compute, seed_storage_chain, seed_semiconductor):
        fn(session)
    session.commit()

    service = RecallService(session)
    cases = list(session.scalars(select(ResearchCase)))

    for case in cases:
        theses = list(
            session.scalars(select(Thesis).where(Thesis.research_case_id == case.id))
        )
        for thesis in theses:
            gold = set(
                session.scalars(
                    select(EvidenceLink.source_statement_id).where(
                        EvidenceLink.thesis_id == thesis.id
                    )
                )
            )
            if not gold:
                continue
            base = service.for_thesis(
                thesis, cutoff=CUTOFF, top_k=20, mode="bm25", exclude_linked=False
            )
            hybrid = service.for_thesis(
                thesis, cutoff=CUTOFF, top_k=20, mode="hybrid", exclude_linked=False
            )
            base_ids = [s.id for s in base]
            hybrid_ids = [s.id for s in hybrid]
            for k in (10, 20):
                b_hits = len(gold & set(base_ids[:k])) / len(gold)
                h_hits = len(gold & set(hybrid_ids[:k])) / len(gold)
                assert h_hits >= b_hits - 1e-9, (
                    f"hybrid recall@{k} regressed for {case.title} / "
                    f"{thesis.statement!r}: bm25={b_hits:.4f} hybrid={h_hits:.4f}"
                )


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

    proposal_ids = proposer.propose(thesis.id, session)
    assert proposal_ids

    from app.models.ledger import AIRun
    from app.models.proposals import Proposal

    proposals = [session.get(Proposal, pid) for pid in proposal_ids]
    run = session.scalars(select(AIRun).where(AIRun.kind == "propose")).one()
    assert "cutoff" in run.input_ref
    assert set(run.input_ref["statement_ids"]) == {
        p.payload["source_statement_id"] for p in proposals
    }
    assert str(relevant.id) in run.input_ref["statement_ids"]


def test_proposer_excludes_irrelevant_statements(
    session, document_service, research_service, thesis, document
):
    _add_statement_with_text(
        document_service, research_service, document, "白酒 消费 疲软 库存"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    proposal_ids = EvidenceProposer(client).propose(thesis.id, session)

    from app.models.proposals import Proposal

    proposals = [session.get(Proposal, pid) for pid in proposal_ids]
    assert all("白酒" not in str(p.payload.get("reason")) for p in proposals)


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
        proposal_ids = EvidenceProposer(client).propose(thesis.id, session)
    assert len(proposal_ids) == 1

    from app.models.proposals import Proposal

    proposal = session.get(Proposal, proposal_ids[0])
    assert proposal.payload["scope"] == {"industry_topic": "ai_compute"}


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
    # Re-running must not re-propose statements already covered by a Proposal:
    # recall excludes them even though no EvidenceLink exists yet (design §9.2).
    second = proposer.propose(thesis.id, session)
    assert second == []

    from app.models.proposals import Proposal

    total = list(
        session.scalars(
            select(Proposal).where(Proposal.kind == "evidence_link")
        )
    )
    assert len(total) == len(first)
