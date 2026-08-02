"""Offline grid-search for hybrid recall params (one-off tuning harness).

Reuses the production ranking functions from app.services.recall, but
reimplements the fusion stages with tunable knobs:

- ``ngram``: dense-leg char n-gram size (2, 3, or mixed 2+3)
- ``rrf_k``: RRF smoothing constant at the shortlist stage
- ``lex_weight``: weight of the lexical leg at the final fusion stage
  (shortlist leg weight fixed at 1.0)

Constraint for a "winning" config: for EVERY thesis and k in {10, 20},
hybrid recall >= bm25 recall (no per-thesis dip); tie-break by overall
hybrid recall@10, then recall@20.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, EvidenceLink, ResearchCase, Thesis
from app.scripts.seed_ai_compute_case import CUTOFF
from app.scripts.seed_ai_compute_case import seed as seed_ai_compute
from app.scripts.seed_semiconductor_case import seed as seed_semiconductor
from app.scripts.seed_storage_chain_case import seed as seed_storage_chain
from app.services.recall import (
    RecallService,
    bm25_rank,
    char_ngrams,
    lexical_rerank,
    tfidf_rank,
)

_CJK_RUN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")


def mixed_ngrams(text: str, ns: tuple[int, ...]) -> list[str]:
    grams: list[str] = []
    for n in ns:
        grams.extend(char_ngrams(text, n))
    return grams


def tfidf_rank_mixed(doc_ids, doc_texts, query, top_k, ns):
    query_grams = Counter(mixed_ngrams(query, ns))
    if not query_grams:
        return []
    corpus = [Counter(mixed_ngrams(t, ns)) for t in doc_texts]
    doc_count = len(corpus)

    def idf(term):
        df = sum(1 for doc in corpus if doc.get(term, 0) > 0)
        return math.log(1 + doc_count / df) if df else 0.0

    q_weights = {t: tf * idf(t) for t, tf in query_grams.items()}
    q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
    if q_norm == 0:
        return []
    scores = []
    for doc in corpus:
        dot = sum(doc.get(t, 0) * w for t, w in q_weights.items())
        if dot == 0:
            scores.append(0.0)
            continue
        d_norm = math.sqrt(sum((tf * idf(t)) ** 2 for t, tf in doc.items() if idf(t) > 0))
        scores.append(dot / (d_norm * q_norm) if d_norm > 0 else 0.0)
    ranked = sorted(range(doc_count), key=lambda i: scores[i], reverse=True)
    return [doc_ids[i] for i in ranked[:top_k] if scores[i] > 0]


def weighted_rrf(lists_with_weights, k):
    scores: dict = {}
    for ranked, w in lists_with_weights:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return sorted(scores, key=lambda d: scores[d], reverse=True)


def hybrid_pipeline(ids, texts, query, shortlist, top_k, ns, rrf_k, lex_weight):
    bm25_leg = bm25_rank(ids, texts, query, shortlist)
    dense_leg = tfidf_rank_mixed(ids, texts, query, shortlist, ns)
    shortlisted = weighted_rrf([(bm25_leg, 1.0), (dense_leg, 1.0)], rrf_k)[:shortlist]
    if not shortlisted:
        return []
    by_id = dict(zip(ids, texts))
    short_texts = [by_id[sid] for sid in shortlisted]
    reranked = lexical_rerank(query, short_texts, len(shortlisted))
    lexical_order = [shortlisted[idx] for idx, _ in reranked]
    final = weighted_rrf(
        [(shortlisted, 1.0), (lexical_order, lex_weight)], rrf_k
    )[:top_k]
    return final


def main() -> None:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine, future=True)
    session = sm()
    for fn in (seed_ai_compute, seed_storage_chain, seed_semiconductor):
        fn(session)
    session.commit()

    service = RecallService(session)
    cases = list(session.scalars(select(ResearchCase)))
    tasks = []
    for case in cases:
        theses = list(session.scalars(select(Thesis).where(Thesis.research_case_id == case.id)))
        for thesis in theses:
            gold = set(session.scalars(select(EvidenceLink.source_statement_id).where(EvidenceLink.thesis_id == thesis.id)))
            if not gold:
                continue
            cands = service._visible_candidates(thesis, CUTOFF, exclude_linked=False)
            query = thesis.statement
            if case.industry_topic:
                query = f"{query} {case.industry_topic}"
            tasks.append({
                "case": case.title, "thesis": thesis.statement, "gold": gold,
                "ids": [s.id for s in cands], "texts": [s.normalized_text for s in cands],
                "query": query,
            })
    print(f"theses={len(tasks)} candidates_per_thesis={len(tasks[0]['ids'])}")

    # Baseline per thesis
    base = {}
    for t in tasks:
        ids, texts, q = t["ids"], t["texts"], t["query"]
        bm_leg = bm25_rank(ids, texts, q, 40)
        reranked = lexical_rerank(q, [dict(zip(ids, texts))[i] for i in bm_leg], len(bm_leg))
        order = [bm_leg[idx] for idx, _ in reranked]
        base[id(t)] = order

    def recalls(order, gold):
        return {k: len(gold & set(order[:k])) / len(gold) for k in (10, 20)}

    configs = []
    for ns in [(2,), (3,), (2, 3)]:
        for rrf_k in (30, 60, 100):
            for lex_w in (1.0, 1.5, 2.0):
                configs.append((ns, rrf_k, lex_w))

    results = []
    for ns, rrf_k, lex_w in configs:
        per_thesis = []
        dips = 0
        tot = {10: [0, 0], 20: [0, 0]}
        tot_b = {10: [0, 0], 20: [0, 0]}
        for t in tasks:
            order = hybrid_pipeline(t["ids"], t["texts"], t["query"], 40, 20, ns, rrf_k, lex_w)
            rh = recalls(order, t["gold"])
            rb = recalls(base[id(t)], t["gold"])
            g = len(t["gold"])
            for k in (10, 20):
                tot[k][0] += rh[k] * g; tot[k][1] += g
                tot_b[k][0] += rb[k] * g; tot_b[k][1] += g
            if rh[10] < rb[10] - 1e-9 or rh[20] < rb[20] - 1e-9:
                dips += 1
                per_thesis.append((t["case"], t["thesis"][:20], rb, rh))
        o10 = tot[10][0] / tot[10][1]; o20 = tot[20][0] / tot[20][1]
        results.append((dips, -o10, -o20, ns, rrf_k, lex_w, per_thesis, o10, o20))

    results.sort(key=lambda r: (r[0], r[1], r[2]))
    print(f"{'ns':7} {'rrf_k':6} {'lex_w':5} {'dips':4} {'overall@10':10} {'overall@20':10}")
    for dips, _, _, ns, rrf_k, lex_w, per_thesis, o10, o20 in results:
        print(f"{str(ns):7} {rrf_k:<6} {lex_w:<5} {dips:<4} {o10:<10.4f} {o20:<10.4f}")
    best = results[0]
    print("\nBEST:", best[3], "rrf_k=", best[4], "lex_w=", best[5], "dips=", best[0])
    for case, th, rb, rh in best[6]:
        print("  dip:", case, th, "base=", rb, "hybrid=", rh)


if __name__ == "__main__":
    main()
