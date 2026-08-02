"""Recall A/B evaluation: sparse-only baseline vs hybrid (BM25 + dense + RRF).

Replays the frozen AI-compute gold slice offline and measures, per thesis,
whether the human-curated gold EvidenceLink statements are recalled by

- **baseline** — the legacy pipeline (``mode="bm25"``: BM25 shortlist,
  lexical rerank), and
- **hybrid** — the fused pipeline (``mode="hybrid"``: BM25 leg + char-n-gram
  TF-IDF dense leg fused with RRF, lexical signal fused as a third leg).

Ground truth: the seeded EvidenceLinks per thesis are the human-curated
evidence set feeding the confirmed assessments, so their source statements
are the relevance gold standard.  Candidates are all cutoff-visible
statements with ``exclude_linked=False`` (otherwise the gold statements
would be filtered out before ranking).

Usage::

    python scripts/eval_recall_ab.py

Reads ``DATABASE_URL`` (default ``sqlite:///./recall_eval.db``), recreates
the schema, seeds the frozen slice, and writes a summary JSON to
``docs/evaluation/reports/recall_ab_<timestamp>.json``.  Exits 1 if the
hybrid pipeline recalls *fewer* gold statements than the baseline at any
tracked k (regression guard); otherwise 0.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base, EvidenceLink, ResearchCase, Thesis
from app.scripts.seed_ai_compute_case import CUTOFF, seed
from app.services.recall import DEFAULT_TOP_K, RecallService

TRACKED_KS = (10, DEFAULT_TOP_K)  # recall@10 and recall@20


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _unique_path(reports_dir: Path, timestamp: str) -> Path:
    path = reports_dir / f"recall_ab_{timestamp}.json"
    suffix = 1
    while path.exists():
        path = reports_dir / f"recall_ab_{timestamp}_{suffix}.json"
        suffix += 1
    return path


def main() -> int:
    url = os.getenv("DATABASE_URL", "sqlite:///./recall_eval.db")
    engine = create_engine(url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)

    with session_local() as session:
        seed(session)
        session.commit()

        case = session.scalars(select(ResearchCase)).one()
        theses = list(
            session.scalars(
                select(Thesis).where(Thesis.research_case_id == case.id)
            )
        )

        recall = RecallService(session)
        per_thesis: list[dict] = []
        totals = {
            mode: {k: {"recalled": 0, "gold": 0} for k in TRACKED_KS}
            for mode in ("bm25", "hybrid")
        }

        for thesis in theses:
            gold_ids = set(
                session.scalars(
                    select(EvidenceLink.source_statement_id).where(
                        EvidenceLink.thesis_id == thesis.id
                    )
                )
            )
            if not gold_ids:
                continue

            ranked: dict[str, list] = {}
            for mode in ("bm25", "hybrid"):
                ranked[mode] = recall.for_thesis(
                    thesis,
                    cutoff=CUTOFF,
                    top_k=max(TRACKED_KS),
                    mode=mode,
                    exclude_linked=False,
                )

            entry: dict = {
                "thesis_id": str(thesis.id),
                "thesis": thesis.statement,
                "gold_count": len(gold_ids),
                "modes": {},
            }
            recovered: list[str] = []
            for mode in ("bm25", "hybrid"):
                ids = [s.id for s in ranked[mode]]
                mode_stats = {}
                for k in TRACKED_KS:
                    hits = gold_ids & set(ids[:k])
                    mode_stats[f"recall_at_{k}"] = round(len(hits) / len(gold_ids), 4)
                    totals[mode][k]["recalled"] += len(hits)
                    totals[mode][k]["gold"] += len(gold_ids)
                entry["modes"][mode] = mode_stats
            # Statements the hybrid recovered at top_k that the baseline missed.
            base_top = {s.id for s in ranked["bm25"][: DEFAULT_TOP_K]}
            hybrid_top = {s.id for s in ranked["hybrid"][: DEFAULT_TOP_K]}
            recovered = [str(i) for i in (gold_ids & hybrid_top) - base_top]
            lost = [str(i) for i in (gold_ids & base_top) - hybrid_top]
            entry["recovered_by_hybrid"] = recovered
            entry["lost_by_hybrid"] = lost
            per_thesis.append(entry)

    summary: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "tracked_ks": list(TRACKED_KS),
        "overall": {
            mode: {
                f"recall_at_{k}": round(
                    totals[mode][k]["recalled"] / totals[mode][k]["gold"], 4
                )
                if totals[mode][k]["gold"]
                else None
                for k in TRACKED_KS
            }
            for mode in ("bm25", "hybrid")
        },
        "per_thesis": per_thesis,
    }

    reports_dir = _project_root() / "docs" / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = _unique_path(reports_dir, timestamp)
    report_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str)
    )

    print(json.dumps(summary["overall"], indent=2, ensure_ascii=False))
    for entry in per_thesis:
        print(
            f"  {entry['thesis'][:40]}…  "
            f"bm25={entry['modes']['bm25']} hybrid={entry['modes']['hybrid']} "
            f"recovered={len(entry['recovered_by_hybrid'])} "
            f"lost={len(entry['lost_by_hybrid'])}"
        )
    print(f"report: {report_path}")

    # Regression guard: hybrid must never recall less gold than baseline.
    for k in TRACKED_KS:
        if (
            totals["hybrid"][k]["recalled"]
            < totals["bm25"][k]["recalled"]
        ):
            print(f"REGRESSION: hybrid recall@{k} below baseline", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
