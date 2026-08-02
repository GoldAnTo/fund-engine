"""Recall A/B evaluation: sparse-only baseline vs hybrid (BM25 + dense + RRF).

Replays all frozen gold slices offline (AI 算力链 + 锂电储能链 +
半导体设备国产化) and measures, per thesis and per case, whether the
human-curated gold EvidenceLink statements are recalled by

- **baseline** — the legacy pipeline (``mode="bm25"``: BM25 shortlist,
  lexical rerank), and
- **hybrid** — the fused pipeline (``mode="hybrid"``: BM25 leg + char-n-gram
  TF-IDF dense leg fused with RRF, lexical signal fused as a third leg).

Ground truth: the seeded EvidenceLinks per thesis are the human-curated
evidence set feeding the confirmed assessments, so their source statements
are the relevance gold standard.  Candidates are all cutoff-visible
statements **across all seeded cases** with ``exclude_linked=False`` —
cross-case material acts as realistic ranking noise, making this a harder
and more honest evaluation than a single-case replay.

Usage::

    python scripts/eval_recall_ab.py

Reads ``DATABASE_URL`` (default ``sqlite:///./recall_eval.db``), recreates
the schema, seeds the frozen slices, and writes a summary JSON to
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
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import Base, EvidenceLink, ResearchCase, Thesis
from app.scripts.seed_ai_compute_case import CUTOFF
from app.scripts.seed_ai_compute_case import seed as seed_ai_compute
from app.scripts.seed_semiconductor_case import seed as seed_semiconductor
from app.scripts.seed_storage_chain_case import seed as seed_storage_chain
from app.services.recall import DEFAULT_TOP_K, RecallService

TRACKED_KS = (10, DEFAULT_TOP_K)  # recall@10 and recall@20

SEEDS = (seed_ai_compute, seed_storage_chain, seed_semiconductor)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _unique_path(reports_dir: Path, timestamp: str) -> Path:
    path = reports_dir / f"recall_ab_{timestamp}.json"
    suffix = 1
    while path.exists():
        path = reports_dir / f"recall_ab_{timestamp}_{suffix}.json"
        suffix += 1
    return path


def _eval_case(
    case: ResearchCase, session: Session, recall: RecallService, totals: dict
) -> dict:
    """Evaluate one seeded case and fold its counts into ``totals``."""
    theses = list(
        session.scalars(select(Thesis).where(Thesis.research_case_id == case.id))
    )
    case_totals = {
        mode: {k: {"recalled": 0, "gold": 0} for k in TRACKED_KS}
        for mode in ("bm25", "hybrid")
    }
    per_thesis: list[dict] = []

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
        for mode in ("bm25", "hybrid"):
            ids = [s.id for s in ranked[mode]]
            mode_stats = {}
            for k in TRACKED_KS:
                hits = gold_ids & set(ids[:k])
                mode_stats[f"recall_at_{k}"] = round(len(hits) / len(gold_ids), 4)
                for bucket in (totals, case_totals):
                    bucket[mode][k]["recalled"] += len(hits)
                    bucket[mode][k]["gold"] += len(gold_ids)
            entry["modes"][mode] = mode_stats
        # Statements the hybrid recovered at top_k that the baseline missed.
        base_top = {s.id for s in ranked["bm25"][: DEFAULT_TOP_K]}
        hybrid_top = {s.id for s in ranked["hybrid"][: DEFAULT_TOP_K]}
        entry["recovered_by_hybrid"] = [
            str(i) for i in (gold_ids & hybrid_top) - base_top
        ]
        entry["lost_by_hybrid"] = [
            str(i) for i in (gold_ids & base_top) - hybrid_top
        ]
        per_thesis.append(entry)

    return {
        "case_id": str(case.id),
        "case": case.title,
        "overall": {
            mode: {
                f"recall_at_{k}": round(
                    case_totals[mode][k]["recalled"] / case_totals[mode][k]["gold"], 4
                )
                if case_totals[mode][k]["gold"]
                else None
                for k in TRACKED_KS
            }
            for mode in ("bm25", "hybrid")
        },
        "per_thesis": per_thesis,
    }


def main() -> int:
    url = os.getenv("DATABASE_URL", "sqlite:///./recall_eval.db")
    engine = create_engine(url, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)

    with session_local() as session:
        for seed_fn in SEEDS:
            seed_fn(session)
        session.commit()

        cases = list(session.scalars(select(ResearchCase)))
        recall = RecallService(session)
        totals = {
            mode: {k: {"recalled": 0, "gold": 0} for k in TRACKED_KS}
            for mode in ("bm25", "hybrid")
        }
        per_case = [_eval_case(case, session, recall, totals) for case in cases]

    summary: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "tracked_ks": list(TRACKED_KS),
        "cases_seeded": [c.title for c in cases],
        "candidate_pool": "all cutoff-visible statements across all seeded cases",
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
        "per_case": per_case,
    }

    reports_dir = _project_root() / "docs" / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = _unique_path(reports_dir, timestamp)
    report_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str)
    )

    print(json.dumps(summary["overall"], indent=2, ensure_ascii=False))
    for case_entry in per_case:
        print(f"[{case_entry['case']}] overall={case_entry['overall']}")
        for entry in case_entry["per_thesis"]:
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
