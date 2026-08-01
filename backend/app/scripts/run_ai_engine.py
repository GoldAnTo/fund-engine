"""End-to-end AI research engine script.

Runs ``extract -> propose -> assess`` on a seeded case.  Without
``LLM_API_KEY`` the engine runs in mock mode, producing deterministic
machine-generated statements, links, and assessments.

Usage::

    # auto-seed then run (mock mode, SQLite)
    python -m app.scripts.run_ai_engine --seed

    # run on an existing seeded case
    python -m app.scripts.run_ai_engine --case-id <uuid>
"""
from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, exists, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.ai.extraction import StatementExtractor
from app.ai.proposal import EvidenceProposer
from app.env import load_local_env
from app.models.ledger import (
    AIRun,
    Base,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.services.compliance import ComplianceRefusedError


def _pending_versions(session: Session) -> list[DocumentVersion]:
    """Versions that have source spans but no extracted statements yet.

    Pending, not parser-labelled: ingestion may attach any parser_version,
    and a seeded version that already carries statements must be skipped so
    repeated runs stay idempotent.
    """
    has_span = exists().where(SourceSpan.document_version_id == DocumentVersion.id)
    has_statement = (
        select(SourceStatement.id)
        .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
        .where(SourceSpan.document_version_id == DocumentVersion.id)
        .exists()
    )
    return list(
        session.scalars(select(DocumentVersion).where(has_span, ~has_statement))
    )


def run_engine(session: Session, case: ResearchCase, skip_extract: bool = False) -> None:
    """Run extract -> propose -> assess for *case* and print a summary."""
    client = LLMClient.from_env()
    extractor = StatementExtractor(client)
    proposer = EvidenceProposer(client)
    generator = AssessmentGenerator(client)

    mode = "mock" if client._mock else f"live ({client.model_version})"
    print(f"AI research engine - mode: {mode}")
    print(f"Case: {case.title} ({case.id})\n")

    # 1. Extract statements from every pending document version (has spans,
    # no statements yet).
    if not skip_extract:
        versions = _pending_versions(session)
        total_statements = 0
        for version in versions:
            statements = extractor.extract(version.id, session)
            total_statements += len(statements)
        session.commit()
        print(
            f"[extract] {total_statements} statements from "
            f"{len(versions)} pending documents"
        )
    else:
        print("[extract] skipped (using existing statements)")

    # 2. Propose evidence links for every thesis in the case.
    theses = list(
        session.scalars(
            select(Thesis).where(Thesis.research_case_id == case.id)
        )
    )
    total_links = 0
    for thesis in theses:
        links = proposer.propose(thesis.id, session)
        total_links += len(links)
    session.commit()  # persist proposals before the assess loop
    print(f"[propose] {total_links} evidence links for {len(theses)} theses")

    # 3. Generate an AI assessment for every thesis.  A compliance refusal
    # on one thesis must not kill the run: roll back that thesis's
    # half-frozen snapshot + failed AIRun, report it, and continue.
    cutoff = datetime.now(timezone.utc)
    for thesis in theses:
        label = thesis.statement[:50]
        try:
            assessment = generator.generate(thesis.id, cutoff, session)
        except ComplianceRefusedError as exc:
            # Snapshot already deleted by the generator; keep the failed
            # AIRun as the audit trail and continue with the next thesis.
            session.commit()
            print(f"[assess]  {label}… → COMPLIANCE REFUSED ({exc})")
            continue
        session.commit()
        print(
            f"[assess]  {label}… → {assessment.conclusion} "
            f"(gaps: {len(assessment.gaps)})"
        )

    # 4. AIRun audit summary.
    runs = list(session.scalars(select(AIRun)))
    print(f"\nAIRun records: {len(runs)}")
    for run in runs:
        status_tag = "OK" if run.status == "success" else "FAIL"
        print(
            f"  [{status_tag}] {run.kind:<8} "
            f"model={run.model_version}  prompt={run.prompt_version}  "
            f"{run.output_summary}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the AI research engine (extract → propose → assess)."
    )
    parser.add_argument(
        "--case-id",
        type=str,
        default=None,
        help="ResearchCase UUID (defaults to the first case found).",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed the frozen AI-compute case before running the engine.",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip extraction (use existing statements in the ledger).",
    )
    args = parser.parse_args()

    load_local_env()  # backend/.env (gitignored); shell env still wins

    url = os.getenv("DATABASE_URL", "sqlite:///./evidence_ai.db")
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as session:
        if args.seed:
            from app.scripts.seed_ai_compute_case import seed

            seed(session)
            session.commit()

        if args.case_id:
            case = session.get(ResearchCase, uuid.UUID(args.case_id))
            if case is None:
                raise SystemExit(f"case {args.case_id} not found")
        else:
            case = session.scalar(select(ResearchCase).limit(1))
            if case is None:
                raise SystemExit(
                    "no ResearchCase found — pass --seed to create one first"
                )

        run_engine(session, case, skip_extract=args.skip_extract)
        session.commit()


if __name__ == "__main__":
    main()
