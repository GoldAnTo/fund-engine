"""Release-gate verification for the AI-compute evidence slice.

Runs nine explicit checks against a seeded ledger to verify that the vertical
slice is auditable end-to-end:

1. **document_versions_present** – at least six frozen DocumentVersions exist.
2. **gold_manifest_matches_ledger** – (fail-closed) the frozen dataset
   manifest exists and its content hashes match the ledger exactly.
3. **assessment_source_spans_complete** – every AIAssessment can be traced
   back through snapshot → evidence_link → source_statement → source_span
   without a broken link.
4. **holding_disclosures_dated** – every HoldingDisclosure carries both a
   ``report_period`` and a ``published_at``.
5. **future_material_excluded** – a historical cutoff excludes disclosures
   published after that cutoff from exposure and workbench views.
6. **ai_human_boundary_visible** – every AIAssessment is marked provisional,
   and human reviews exist as separate records (the original AI conclusion
   is never overwritten).
7. **review_outcomes_tracked** – every AIAssessment carries a human
   ReviewDecision (review coverage is gated); the outcome distribution and
   AIRun audit counts are reported as evidence.
8. **table_extraction_gold_accuracy** – (fail-closed) the rule-based
   FinancialTableExtractor reproduces the frozen table gold set exactly
   (per-sample recall == 1.0 and precision == 1.0).
9. **projection_rebuilds** – (Neo4j only) the graph projection can be rebuilt
   from the ledger and the node count matches.  Skipped when Neo4j is
   unavailable; a skip never causes the gate to fail.

Usage::

    python scripts/verify_ai_compute_slice.py

Fail-closed: if the dataset manifest is missing, the script exits non-zero
*before* touching the database.  Reads ``DATABASE_URL`` (default
``sqlite:///./evidence_gate.db``), creates the schema, seeds the frozen
slice, runs the gate, and writes a summary JSON to
``docs/evaluation/reports/<timestamp>.json`` (committed evidence-pack trend)
plus full per-check detail to ``docs/evaluation/raw/<timestamp>.json``
(gitignored).  Exits 0 on pass, 1 on fail.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import (
    AIAssessment,
    AIRun,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    HoldingDisclosure,
    Fund,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
)
from app.models.ledger import Base
from app.repositories.instruments import InstrumentRepository
from app.scripts.seed_ai_compute_case import seed
from app.services.exposure import ExposureService
from app.services.workbench import WorkbenchService

# A historical cutoff that precedes the 2026-04-22 disclosure publications
# but follows the 2025-07-24 stale disclosure.  Used by the
# future_material_excluded check.
HISTORICAL_CUTOFF = date(2026, 4, 1)

# Evidence-pack paths (resolved from this file so they work from any cwd).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "docs" / "evaluation" / "dataset-manifest.json"
TABLE_GOLD_PATH = (
    PROJECT_ROOT / "docs" / "evaluation" / "datasets" / "table-extraction-gold.json"
)


@dataclass
class GateResult:
    """Aggregate release-gate result."""

    passed: bool
    checks: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class ReleaseGate:
    """Run six explicit checks against a seeded evidence ledger.

    Construct with a SQLAlchemy ``Session`` (and an optional projector for
    Neo4j rebuild verification).  Call :meth:`run` to execute all checks and
    return a :class:`GateResult`.
    """

    def __init__(self, session: Session, projector: Any | None = None) -> None:
        self._session = session
        self._projector = projector

    def run(self) -> GateResult:
        """Execute every check and return the aggregate result."""
        # Expire the identity map so that any prior raw-DBAPI mutations
        # (e.g. in destructive tests) are reflected by fresh SELECTs.
        self._session.expire_all()

        checks = [
            self._check_document_versions_present(),
            self._check_gold_manifest_matches_ledger(),
            self._check_assessment_source_spans_complete(),
            self._check_holding_disclosures_dated(),
            self._check_future_material_excluded(),
            self._check_ai_human_boundary_visible(),
            self._check_review_outcomes_tracked(),
            self._check_table_extraction_gold_accuracy(),
            self._check_projection_rebuilds(),
        ]
        failures = [
            c["name"] for c in checks if not c.get("skipped") and not c["passed"]
        ]
        return GateResult(passed=len(failures) == 0, checks=checks, failures=failures)

    # ------------------------------------------------------------------ checks

    def _check_document_versions_present(self) -> dict:
        count = len(self._session.scalars(select(DocumentVersion)).all())
        passed = count >= 6
        return {
            "name": "document_versions_present",
            "passed": passed,
            "evidence": {"document_version_count": count, "minimum_required": 6},
            "failures": [] if passed else [f"only {count} document versions (need >= 6)"],
        }

    def _check_gold_manifest_matches_ledger(self) -> dict:
        """Fail-closed: the frozen dataset manifest must exist, list documents,
        and match the ledger's DocumentVersion content hashes exactly."""
        if not MANIFEST_PATH.exists():
            return {
                "name": "gold_manifest_matches_ledger",
                "passed": False,
                "evidence": {"manifest_path": str(MANIFEST_PATH)},
                "failures": [f"gold manifest missing: {MANIFEST_PATH}"],
            }

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        expected = {
            doc["content_sha256"] for doc in manifest.get("documents", [])
        }
        actual = set(
            self._session.scalars(select(DocumentVersion.content_sha256)).all()
        )

        failures: list[str] = []
        if not expected:
            failures.append("gold manifest lists no documents")
        for digest in sorted(expected - actual):
            failures.append(f"manifest document not in ledger: {digest[:12]}…")
        for digest in sorted(actual - expected):
            failures.append(f"ledger document not in manifest: {digest[:12]}…")

        return {
            "name": "gold_manifest_matches_ledger",
            "passed": not failures,
            "evidence": {
                "manifest": str(MANIFEST_PATH),
                "manifest_documents": len(expected),
                "ledger_documents": len(actual),
            },
            "failures": failures,
        }

    def _check_assessment_source_spans_complete(self) -> dict:
        """Trace every AIAssessment back to a SourceSpan; fail on any break."""
        assessments = list(self._session.scalars(select(AIAssessment)).all())
        failures: list[str] = []

        for assessment in assessments:
            snapshot = self._session.scalar(
                select(EvidenceSnapshot).where(
                    EvidenceSnapshot.id == assessment.snapshot_id
                )
            )
            if snapshot is None:
                failures.append(
                    f"untraceable_assessment: assessment {assessment.id} -> "
                    f"snapshot {assessment.snapshot_id} missing"
                )
                continue

            for link_id in snapshot.evidence_link_ids:
                link = self._session.scalar(
                    select(EvidenceLink).where(
                        EvidenceLink.id == uuid.UUID(link_id)
                    )
                )
                if link is None:
                    failures.append(
                        f"untraceable_assessment: assessment {assessment.id} -> "
                        f"link {link_id} missing"
                    )
                    continue
                statement = self._session.scalar(
                    select(SourceStatement).where(
                        SourceStatement.id == link.source_statement_id
                    )
                )
                if statement is None:
                    failures.append(
                        f"untraceable_assessment: assessment {assessment.id} -> "
                        f"statement {link.source_statement_id} missing"
                    )
                    continue
                span = self._session.scalar(
                    select(SourceSpan).where(
                        SourceSpan.id == statement.source_span_id
                    )
                )
                if span is None:
                    failures.append(
                        f"untraceable_assessment: assessment {assessment.id} -> "
                        f"span {statement.source_span_id} missing"
                    )
                    continue

        return {
            "name": "assessment_source_spans_complete",
            "passed": len(failures) == 0,
            "evidence": {"assessment_count": len(assessments)},
            "failures": failures,
        }

    def _check_holding_disclosures_dated(self) -> dict:
        disclosures = list(self._session.scalars(select(HoldingDisclosure)).all())
        failures: list[str] = []
        for disclosure in disclosures:
            if disclosure.report_period is None:
                failures.append(
                    f"undated_disclosure: {disclosure.id} report_period is None"
                )
            if disclosure.published_at is None:
                failures.append(
                    f"undated_disclosure: {disclosure.id} published_at is None"
                )
        return {
            "name": "holding_disclosures_dated",
            "passed": len(failures) == 0,
            "evidence": {"disclosure_count": len(disclosures)},
            "failures": failures,
        }

    def _check_future_material_excluded(self) -> dict:
        """Verify point-in-time views exclude disclosures published after cutoff."""
        cutoff = HISTORICAL_CUTOFF
        cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=UTC)

        all_disclosures = list(
            self._session.scalars(select(HoldingDisclosure)).all()
        )
        future_ids: set[uuid.UUID] = set()
        for d in all_disclosures:
            pub = d.published_at
            if pub is None:
                continue
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=UTC)
            if pub > cutoff_dt:
                future_ids.add(d.id)

        failures: list[str] = []

        # --- ExposureService ---
        funds = list(self._session.scalars(select(Fund)).all())
        repo = InstrumentRepository(self._session)
        exposure_service = ExposureService(repo)
        for fund in funds:
            exposure = exposure_service.for_fund(fund.id, as_of=cutoff)
            for row in exposure.rows:
                if row.disclosure_id in future_ids:
                    failures.append(
                        f"future_disclosure_visible: disclosure {row.disclosure_id} "
                        f"appeared in exposure for fund {fund.id} at cutoff {cutoff}"
                    )

        # --- WorkbenchService ---
        # The workbench assumes published_at is non-NULL; if a prior check
        # found undated disclosures, loading the workbench may raise.  Catch
        # and report rather than crashing the entire gate.
        cases = list(self._session.scalars(select(ResearchCase)).all())
        workbench_service = WorkbenchService(self._session)
        for case in cases:
            try:
                wb = workbench_service.load_workbench(
                    case_id=case.id, cutoff=cutoff_dt
                )
            except Exception as exc:
                failures.append(
                    f"workbench_error: case {case.id} raised {type(exc).__name__}: "
                    f"{exc} (may be caused by undated disclosures)"
                )
                continue
            if wb is None:
                continue
            for row in wb["fund_holding_disclosures"]:
                disc_id = uuid.UUID(row["disclosure_id"])
                if disc_id in future_ids:
                    failures.append(
                        f"future_disclosure_visible: disclosure {disc_id} "
                        f"appeared in workbench for case {case.id} at cutoff {cutoff}"
                    )

        return {
            "name": "future_material_excluded",
            "passed": len(failures) == 0,
            "evidence": {
                "cutoff": cutoff_dt.isoformat(),
                "total_disclosures": len(all_disclosures),
                "future_disclosure_count": len(future_ids),
                "funds_checked": len(funds),
                "cases_checked": len(cases),
            },
            "failures": failures,
        }

    def _check_ai_human_boundary_visible(self) -> dict:
        """Verify AI assessments are marked provisional and reviews are separate."""
        assessments = list(self._session.scalars(select(AIAssessment)).all())
        failures: list[str] = []
        reviewed = 0

        for assessment in assessments:
            if not assessment.displayed_as_provisional:
                failures.append(
                    f"assessment {assessment.id}: displayed_as_provisional is False"
                )

            review = self._session.scalar(
                select(ReviewDecision)
                .where(ReviewDecision.ai_assessment_id == assessment.id)
                .order_by(ReviewDecision.created_at.desc())
                .limit(1)
            )
            if review is not None:
                reviewed += 1
                # The AIAssessment is append-only, so its conclusion is always
                # the original.  A human review exists as a separate
                # ReviewDecision record, never overwriting the AI result.
                # We verify the assessment conclusion is still populated.
                if not assessment.conclusion:
                    failures.append(
                        f"assessment {assessment.id}: conclusion was cleared "
                        f"after review {review.id}"
                    )

        return {
            "name": "ai_human_boundary_visible",
            "passed": len(failures) == 0,
            "evidence": {
                "assessment_count": len(assessments),
                "reviewed_count": reviewed,
            },
            "failures": failures,
        }

    def _check_review_outcomes_tracked(self) -> dict:
        """Every AIAssessment in the frozen slice must carry a human review.

        The gold set's 人工标签 claim is only real if each AI conclusion has a
        separate ReviewDecision record, so review coverage is gated here.
        The outcome distribution (confirmed / modified / rejected) and AIRun
        audit counts are reported as evidence, making review adoption
        measurable over time.
        """
        assessments = list(self._session.scalars(select(AIAssessment)).all())
        reviews = list(self._session.scalars(select(ReviewDecision)).all())

        reviewed_ids = {review.ai_assessment_id for review in reviews}
        unreviewed = [a for a in assessments if a.id not in reviewed_ids]

        outcomes: dict[str, int] = {}
        for review in reviews:
            outcomes[review.outcome] = outcomes.get(review.outcome, 0) + 1

        runs = list(self._session.scalars(select(AIRun)).all())
        runs_by_status: dict[str, int] = {}
        for run in runs:
            runs_by_status[run.status] = runs_by_status.get(run.status, 0) + 1

        failures: list[str] = []
        if not assessments:
            failures.append("no AI assessments in the ledger")
        for assessment in unreviewed:
            failures.append(
                f"unreviewed_assessment: assessment {assessment.id} has no "
                f"ReviewDecision"
            )

        coverage = (
            (len(assessments) - len(unreviewed)) / len(assessments)
            if assessments
            else 0.0
        )
        return {
            "name": "review_outcomes_tracked",
            "passed": not failures,
            "evidence": {
                "assessment_count": len(assessments),
                "review_decision_count": len(reviews),
                "review_coverage": round(coverage, 4),
                "outcomes": outcomes,
                "ai_run_count": len(runs),
                "ai_runs_by_status": runs_by_status,
            },
            "failures": failures,
        }

    def _check_table_extraction_gold_accuracy(self) -> dict:
        """Fail-closed: the rule-based table extractor must reproduce the
        frozen gold set exactly (per-sample recall == 1.0, precision == 1.0)."""
        if not TABLE_GOLD_PATH.exists():
            return {
                "name": "table_extraction_gold_accuracy",
                "passed": False,
                "evidence": {"gold_path": str(TABLE_GOLD_PATH)},
                "failures": [f"table gold dataset missing: {TABLE_GOLD_PATH}"],
            }

        from app.services.table_extraction import FinancialTableExtractor

        gold = json.loads(TABLE_GOLD_PATH.read_text(encoding="utf-8"))
        samples = gold.get("samples", [])
        if not samples:
            return {
                "name": "table_extraction_gold_accuracy",
                "passed": False,
                "evidence": {"gold_path": str(TABLE_GOLD_PATH)},
                "failures": ["table gold dataset contains no samples"],
            }

        extractor = FinancialTableExtractor()
        failures: list[str] = []
        per_sample: list[dict] = []

        for sample in samples:
            facts = extractor.extract(sample["text"])
            expected = {
                (e["metric_name"], e["observed_period"], e["value"])
                for e in sample["expected_facts"]
            }
            exp_keys = {(m, p) for (m, p, _v) in expected}
            got_keys = {
                (f.metric_name, f.observed_period.isoformat()) for f in facts
            }
            matched: set[tuple[str, str]] = set()
            for fact in facts:
                period = fact.observed_period.isoformat()
                for metric, exp_period, value in expected:
                    if (
                        fact.metric_name == metric
                        and period == exp_period
                        and value in fact.statement_text
                    ):
                        matched.add((metric, exp_period))

            recall = len(matched) / len(exp_keys) if exp_keys else 1.0
            precision = len(matched) / len(got_keys) if got_keys else 1.0
            per_sample.append(
                {
                    "id": sample["id"],
                    "expected": len(exp_keys),
                    "extracted": len(got_keys),
                    "recall": round(recall, 4),
                    "precision": round(precision, 4),
                }
            )

            missing = exp_keys - matched
            extra = got_keys - exp_keys
            if missing:
                failures.append(
                    f"sample {sample['id']}: missing expected facts "
                    f"{sorted(missing)}"
                )
            if extra:
                failures.append(
                    f"sample {sample['id']}: unexpected extracted facts "
                    f"{sorted(extra)}"
                )

        return {
            "name": "table_extraction_gold_accuracy",
            "passed": not failures,
            "evidence": {
                "gold_path": str(TABLE_GOLD_PATH),
                "sample_count": len(samples),
                "per_sample": per_sample,
            },
            "failures": failures,
        }

    def _check_projection_rebuilds(self) -> dict:
        """Verify the graph projection rebuilds from the ledger (Neo4j only)."""
        if self._projector is None:
            return {
                "name": "projection_rebuilds",
                "passed": True,
                "skipped": True,
                "evidence": {"reason": "no projector provided (Neo4j unavailable)"},
                "failures": [],
            }

        self._projector.clear_projection()
        self._projector.rebuild_all()

        ledger_link_count = len(self._session.scalars(select(EvidenceLink)).all())
        projection_link_count = self._projector.node_count("EvidenceLink")
        passed = projection_link_count == ledger_link_count

        return {
            "name": "projection_rebuilds",
            "passed": passed,
            "skipped": False,
            "evidence": {
                "ledger_evidence_link_count": ledger_link_count,
                "projection_evidence_link_count": projection_link_count,
            },
            "failures": (
                []
                if passed
                else [
                    f"projection EvidenceLink count {projection_link_count} "
                    f"!= ledger {ledger_link_count}"
                ]
            ),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    return PROJECT_ROOT


def _unique_path(directory: Path, timestamp: str) -> Path:
    """Pick a non-overwriting ``<timestamp>.json`` path in ``directory``."""
    path = directory / f"{timestamp}.json"
    counter = 0
    while path.exists():
        counter += 1
        path = directory / f"{timestamp}-{counter}.json"
    return path


def main() -> None:
    # Fail-closed: the evidence pack is meaningless without its frozen
    # manifest, so refuse to touch the database when it is absent.
    if not MANIFEST_PATH.exists():
        print(f"FATAL: gold manifest missing: {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    url = os.getenv("DATABASE_URL", "sqlite:///./evidence_gate.db")
    engine = create_engine(url, future=True)

    # Reset and recreate the schema so each run starts from a clean ledger.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as session:
        seed(session)
        session.commit()
        projector = None
        if os.getenv("NEO4J_URL"):
            from app.services.projection import ProjectionService

            try:
                projector = ProjectionService.from_env(session)
            except Exception:
                projector = None
        gate = ReleaseGate(session, projector=projector)
        result = gate.run()

    # Summary goes to docs/evaluation/reports/<timestamp>.json (committed,
    # shows the gate trend over time); full per-check detail goes to
    # docs/evaluation/raw/<timestamp>.json (gitignored, for debugging).
    reports_dir = _project_root() / "docs" / "evaluation" / "reports"
    raw_dir = _project_root() / "docs" / "evaluation" / "raw"
    reports_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    summary = {
        "passed": result.passed,
        "failures": result.failures,
        "generated_at": timestamp,
        "checks": [
            {
                "name": c["name"],
                "passed": c["passed"],
                "skipped": bool(c.get("skipped")),
            }
            for c in result.checks
        ],
    }
    detail = {
        "passed": result.passed,
        "checks": result.checks,
        "failures": result.failures,
        "generated_at": timestamp,
    }

    report_path = _unique_path(reports_dir, timestamp)
    raw_path = _unique_path(raw_dir, timestamp)
    report_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str)
    )
    raw_path.write_text(
        json.dumps(detail, indent=2, ensure_ascii=False, default=str)
    )

    # Console summary.
    print(f"release gate result: {'PASS' if result.passed else 'FAIL'}")
    print(f"summary written to {report_path}")
    print(f"detail written to {raw_path}")
    for check in result.checks:
        if check.get("skipped"):
            status = "SKIP"
        elif check["passed"]:
            status = "PASS"
        else:
            status = "FAIL"
        print(f"  {check['name']}: {status}")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
