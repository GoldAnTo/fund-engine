"""Retrieval-scoped recall for AI evidence proposals.

The evidence proposer must not feed the LLM an arbitrary slice of the whole
ledger (``SELECT ... LIMIT 20``): that leaks unrelated cases and future
information into the proposal context.  This module recalls candidate
``SourceStatement`` rows that are

1. *visible* at the proposal cutoff (document ``available_at`` and statement
   ``created_at`` not later than the cutoff — no hindsight leakage), and
2. *relevant* to the thesis being assessed.

Ranking is BM25 shortlist followed by a lexical-overlap rerank, ported from
the Verifiable-Company-Research-Agent hybrid pipeline
(``services/rag/hybrid_retrieval.py`` / ``reranker.py``).  Reciprocal rank
fusion and a dense/vector path are intentionally deferred until an embedding
backend is introduced; scores are used only for ranking inside this module
and are never persisted as evidence strength.
"""
from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)

DEFAULT_SHORTLIST = 40
DEFAULT_TOP_K = 20
_BM25_K1 = 1.5
_BM25_B = 0.75

_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[a-zA-Z0-9]{2,}")  # CJK: 一-鿿


def tokenize(text: str) -> list[str]:
    """Split mixed Chinese/English text into ranking tokens.

    Runs of 2+ CJK characters or 2+ alphanumeric characters count as tokens;
    single characters and punctuation are dropped to keep the corpus clean.
    """
    return _TOKEN_RE.findall((text or "").lower())


def bm25_rank(
    doc_ids: list[uuid.UUID],
    doc_texts: list[str],
    query: str,
    top_k: int,
) -> list[uuid.UUID]:
    """Rank ``doc_ids`` by BM25 (Okapi) score against ``query``.

    Self-contained implementation so the recall path has no extra runtime
    dependency; when a dense retrieval path lands, this becomes one leg of a
    reciprocal-rank-fusion hybrid.
    """
    if top_k <= 0 or not doc_ids:
        return []
    query_terms = tokenize(query)
    if not query_terms:
        return []

    corpus = [Counter(tokenize(text)) for text in doc_texts]
    doc_count = len(corpus)
    avg_len = sum(sum(doc.values()) for doc in corpus) / max(doc_count, 1)
    if avg_len == 0:
        return []

    # document frequency per query term
    scores = [0.0] * doc_count
    for term in set(query_terms):
        df = sum(1 for doc in corpus if doc.get(term, 0) > 0)
        if df == 0:
            continue
        idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
        for idx, doc in enumerate(corpus):
            tf = doc.get(term, 0)
            if tf == 0:
                continue
            doc_len = sum(doc.values())
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / avg_len)
            scores[idx] += idf * (tf * (_BM25_K1 + 1)) / denom

    ranked = sorted(range(doc_count), key=lambda i: scores[i], reverse=True)
    return [doc_ids[i] for i in ranked[:top_k] if scores[i] > 0]


def lexical_rerank(
    query: str, texts: list[str], top_k: int
) -> list[tuple[int, float]]:
    """Rerank by query-token overlap, ported from VCRA's LexicalReranker.

    Returns ``(original_index, score)`` pairs sorted by score descending.
    """
    limit = min(max(top_k, 0), len(texts))
    if limit == 0:
        return []
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return [(idx, 0.0) for idx in range(limit)]
    scored: list[tuple[int, float]] = []
    for idx, text in enumerate(texts):
        c_tokens = set(tokenize(text))
        overlap = len(q_tokens & c_tokens) / len(q_tokens) if c_tokens else 0.0
        scored.append((idx, float(overlap)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class RecallService:
    """Recalls relevant, cutoff-visible SourceStatements for a Thesis."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def for_thesis(
        self,
        thesis: Thesis,
        *,
        cutoff: datetime,
        top_k: int = DEFAULT_TOP_K,
        shortlist: int = DEFAULT_SHORTLIST,
    ) -> list[SourceStatement]:
        """Return ranked candidate statements for proposing evidence links.

        Candidates already linked to this thesis are excluded so re-running
        the proposer does not pile duplicate links onto the same evidence.
        """
        cutoff = _ensure_aware(cutoff)
        rows = self._session.execute(
            select(SourceStatement, DocumentVersion)
            .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
            .join(
                DocumentVersion,
                SourceSpan.document_version_id == DocumentVersion.id,
            )
        ).all()

        linked_statement_ids = set(
            self._session.scalars(
                select(EvidenceLink.source_statement_id).where(
                    EvidenceLink.thesis_id == thesis.id
                )
            )
        )

        candidates: list[SourceStatement] = []
        for statement, version in rows:
            if statement.id in linked_statement_ids:
                continue
            if _ensure_aware(statement.created_at) > cutoff:
                continue
            if _ensure_aware(version.available_at) > cutoff:
                continue
            candidates.append(statement)

        if not candidates:
            return []

        case = self._session.get(ResearchCase, thesis.research_case_id)
        query = thesis.statement
        if case is not None and case.industry_topic:
            query = f"{query} {case.industry_topic}"

        candidate_ids = [s.id for s in candidates]
        candidate_texts = [s.normalized_text for s in candidates]

        shortlisted_ids = bm25_rank(candidate_ids, candidate_texts, query, shortlist)
        if not shortlisted_ids:
            return []

        by_id = {s.id: s for s in candidates}
        shortlist_texts = [by_id[sid].normalized_text for sid in shortlisted_ids]
        reranked = lexical_rerank(query, shortlist_texts, top_k)
        return [by_id[shortlisted_ids[idx]] for idx, _ in reranked]
