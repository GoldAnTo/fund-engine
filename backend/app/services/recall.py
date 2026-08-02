"""Retrieval-scoped recall for AI evidence proposals.

The evidence proposer must not feed the LLM an arbitrary slice of the whole
ledger (``SELECT ... LIMIT 20``): that leaks unrelated cases and future
information into the proposal context.  This module recalls candidate
``SourceStatement`` rows that are

1. *visible* at the proposal cutoff (document ``available_at`` and statement
   ``created_at`` not later than the cutoff — no hindsight leakage), and
2. *relevant* to the thesis being assessed.

Ranking is a hybrid of two legs fused with reciprocal rank fusion (RRF),
followed by a lexical-overlap rerank, ported from the
Verifiable-Company-Research-Agent hybrid pipeline
(``services/rag/hybrid_retrieval.py`` / ``reranker.py``):

1. *Sparse leg* — BM25 (Okapi) over coarse tokens.
2. *Dense leg* — TF-IDF cosine over character n-grams.  The coarse tokenizer
   treats a whole run of CJK characters as one token, so "资本开支" and
   "云厂商资本开支高增长" share no token and BM25 scores them 0; character
   bigrams recover exactly these sub-word/synonym-adjacent matches.  This
   leg is fully local and deterministic — no embedding service required —
   and can later be swapped for a real embedding backend behind the same
   ``tfidf_rank`` contract without touching the fusion logic.

Scores are used only for ranking inside this module and are never persisted
as evidence strength.
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
DEFAULT_MODE = "hybrid"
_BM25_K1 = 1.5
_BM25_B = 0.75
# RRF smoothing constant.  The literature default is 60, but with our
# shortlist=40 that flattens the top of each leg too much: a dense-leg
# tail hit can out-vote a sparse-leg top hit, which produced a per-thesis
# recall dip on 锂电储能链 T2 (碳酸锂 thesis: 0.5 → 0.25 at @10).  Grid
# search across (ngram in {(2,), (3,), (2,3)}, rrf_k in {30, 60, 100},
# lex_w in {1.0, 1.5, 2.0}) via tune_recall_params.py picks rrf_k=30 as
# the only setting with 0 per-thesis dips across the three frozen cases;
# overall@10 climbs 0.5625 → 0.5833, @20 unchanged.  See the eval report
# in docs/evaluation/reports/ for the per-thesis breakdown.
_RRF_K = 30

_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[a-zA-Z0-9]{2,}")  # CJK: 一-鿿
_CJK_RUN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")


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


def char_ngrams(text: str, n: int = 2) -> list[str]:
    """Tokenize into character n-grams for the dense leg.

    Runs of CJK characters are decomposed into overlapping n-grams (a run
    shorter than ``n`` keeps the whole run); alphanumeric runs stay whole
    tokens.  This recovers sub-word matches the coarse ``tokenize`` misses
    (e.g. query "资本开支" vs document "云厂商资本开支高增长").
    """
    grams: list[str] = []
    for run in _CJK_RUN_RE.findall((text or "").lower()):
        if re.fullmatch(r"[a-z0-9]+", run):
            grams.append(run)
        elif len(run) <= n:
            grams.append(run)
        else:
            grams.extend(run[i : i + n] for i in range(len(run) - n + 1))
    return grams


def tfidf_rank(
    doc_ids: list[uuid.UUID],
    doc_texts: list[str],
    query: str,
    top_k: int,
) -> list[uuid.UUID]:
    """Rank ``doc_ids`` by TF-IDF cosine similarity against ``query``.

    The dense leg of the hybrid pipeline.  IDF is computed over the candidate
    corpus (same convention as ``bm25_rank``); documents with zero similarity
    are excluded.  Deterministic and fully local — a future embedding backend
    can replace this function behind the same contract.
    """
    if top_k <= 0 or not doc_ids:
        return []
    query_grams = Counter(char_ngrams(query))
    if not query_grams:
        return []

    corpus = [Counter(char_ngrams(text)) for text in doc_texts]
    doc_count = len(corpus)

    def idf(term: str) -> float:
        df = sum(1 for doc in corpus if doc.get(term, 0) > 0)
        if df == 0:
            return 0.0
        return math.log(1 + doc_count / df)

    q_weights = {t: tf * idf(t) for t, tf in query_grams.items()}
    q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
    if q_norm == 0:
        return []

    scores: list[float] = []
    for doc in corpus:
        dot = sum(doc.get(t, 0) * w for t, w in q_weights.items())
        if dot == 0:
            scores.append(0.0)
            continue
        d_norm = math.sqrt(
            sum((tf * idf(t)) ** 2 for t, tf in doc.items() if idf(t) > 0)
        )
        scores.append(dot / (d_norm * q_norm) if d_norm > 0 else 0.0)

    ranked = sorted(range(doc_count), key=lambda i: scores[i], reverse=True)
    return [doc_ids[i] for i in ranked[:top_k] if scores[i] > 0]


def rrf_fuse(*ranked_lists: list[uuid.UUID], k: int = _RRF_K) -> list[uuid.UUID]:
    """Fuse ranked id lists with reciprocal rank fusion.

    Each list contributes ``1 / (k + rank)`` per document; ids absent from a
    list contribute nothing.  Union semantics: a document surfaced by only
    one leg still enters the fused ranking — this is what lets the dense leg
    recover candidates the sparse leg scored 0.
    """
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)


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
        mode: str = DEFAULT_MODE,
        exclude_linked: bool = True,
    ) -> list[SourceStatement]:
        """Return ranked candidate statements for proposing evidence links.

        Candidates already linked to this thesis are excluded so re-running
        the proposer does not pile duplicate links onto the same evidence.
        ``mode="bm25"`` keeps the legacy sparse-only pipeline (used by the
        recall A/B evaluation as the baseline); ``mode="hybrid"`` fuses the
        BM25 leg with the char-n-gram dense leg via RRF before reranking.
        ``exclude_linked=False`` is reserved for evaluation runs that must
        score recall against already-linked gold statements.
        """
        if mode not in ("hybrid", "bm25"):
            raise ValueError(f"unknown recall mode: {mode!r}")
        cutoff = _ensure_aware(cutoff)
        candidates = self._visible_candidates(
            thesis, cutoff, exclude_linked=exclude_linked
        )
        if not candidates:
            return []

        case = self._session.get(ResearchCase, thesis.research_case_id)
        query = thesis.statement
        if case is not None and case.industry_topic:
            query = f"{query} {case.industry_topic}"

        candidate_ids = [s.id for s in candidates]
        candidate_texts = [s.normalized_text for s in candidates]

        bm25_leg = bm25_rank(candidate_ids, candidate_texts, query, shortlist)
        if mode == "hybrid":
            dense_leg = tfidf_rank(candidate_ids, candidate_texts, query, shortlist)
            shortlisted_ids = rrf_fuse(bm25_leg, dense_leg)[:shortlist]
        else:
            shortlisted_ids = bm25_leg
        if not shortlisted_ids:
            return []

        by_id = {s.id: s for s in candidates}
        shortlist_texts = [by_id[sid].normalized_text for sid in shortlisted_ids]
        reranked = lexical_rerank(query, shortlist_texts, len(shortlisted_ids))
        lexical_order = [shortlisted_ids[idx] for idx, _ in reranked]
        if mode == "hybrid":
            # Fuse the lexical signal in as a third leg instead of letting it
            # truncate: a dense-only paraphrase with zero token overlap must
            # not be dropped at the final stage.
            final_ids = rrf_fuse(shortlisted_ids, lexical_order)[:top_k]
        else:
            final_ids = lexical_order[:top_k]
        return [by_id[sid] for sid in final_ids]

    def _visible_candidates(
        self,
        thesis: Thesis,
        cutoff: datetime,
        *,
        exclude_linked: bool,
    ) -> list[SourceStatement]:
        """Statements visible at ``cutoff`` (no hindsight leakage)."""
        rows = self._session.execute(
            select(SourceStatement, DocumentVersion)
            .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
            .join(
                DocumentVersion,
                SourceSpan.document_version_id == DocumentVersion.id,
            )
        ).all()

        linked_statement_ids: set = set()
        if exclude_linked:
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
        return candidates
