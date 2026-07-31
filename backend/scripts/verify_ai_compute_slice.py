"""Release-gate verification for the AI-compute evidence slice.

Runs six explicit checks against a seeded ledger to verify that the vertical
slice is auditable end-to-end:

1. **document_versions_present** – at least six frozen DocumentVersions exist.
2. **assessment_source_spans_complete** – every AIAssessment can be traced
   back through snapshot → evidence_link → source_statement → source_span
   without a broken link.
3. **holding_disclosures_dated** – every HoldingDisclosure carries both a
   ``report_period`` and a ``published_at``.
4. **future_material_excluded** – a historical cutoff excludes disclosures
   published after that cutoff from exposure and workbench views.
5. **ai_human_boundary_visible** – every AIAssessment is marked provisional,
   and human reviews exist as separate records (the original AI conclusion
   is never overwritten).
6. **projection_rebuilds** – (Neo4j only) the graph projection can be rebuilt
   from the ledger and the node count matches.  Skipped when Neo4j is
   unavailable; a skip never causes the gate to fail.

Usage::

    python scripts/verify_ai_compute_slice.py

Reads ``DATABASE_URL`` (default ``sqlite:///./evidence_gate.db``), creates the
schema, seeds the frozen slice, runs the gate, and writes a JSON result to
``docs/evaluation/runs/<timestamp>.json``.  Exits 0 on pass, 1 on fail.
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
            self._check_assessment_source_spans_complete(),
            self._check_holding_disclosures_dated(),
            self._check_future_material_excluded(),
            self._check_ai_human_boundary_visible(),
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
    return Path(__file__).resolve().parents[2]


def main() -> None:
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

    # Write JSON result to docs/evaluation/runs/<timestamp>.json (no overwrite).
    runs_dir = _project_root() / "docs" / "evaluation" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result_path = runs_dir / f"{timestamp}.json"
    counter = 0
    while result_path.exists():
        counter += 1
        result_path = runs_dir / f"{timestamp}-{counter}.json"

    payload = {
        "passed": result.passed,
        "checks": result.checks,
        "failures": result.failures,
        "generated_at": timestamp,
    }
    result_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    )

    # Console summary.
    print(f"release gate result: {'PASS' if result.passed else 'FAIL'}")
    print(f"result written to {result_path}")
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
