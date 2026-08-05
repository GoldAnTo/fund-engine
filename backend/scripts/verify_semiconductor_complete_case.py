"""Quality gate for the semiconductor complete-theme research case.

Fail-closed checks for ledger integrity of a six-dimension research case.
This does NOT claim that example.test fixtures are real-world primary sources;
it only enforces that the case structure is internally consistent and that
placeholder sources remain visibly marked as non-primary.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import (
    AIAssessment,
    CausalEdge,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    HoldingDisclosure,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    ThemeRole,
    Thesis,
    ValuationSnapshot,
)
from app.repositories.research import ResearchRepository
from app.scripts.seed_semiconductor_complete_theme_case import CASE_TITLE, THEME_TAG
from app.services.themes import ThemeService


EXPECTED_DIMENSIONS = (
    "需求传导",
    "持续性",
    "盈利质量",
    "机构持仓",
    "估值",
    "反向证据",
)


def _result(name: str, ok: bool, detail: dict) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run(session: Session) -> list[dict]:
    case = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE))
    if case is None:
        return [_result("case_exists", False, {"title": CASE_TITLE})]

    theses = list(
        session.scalars(
            select(Thesis)
            .where(Thesis.research_case_id == case.id)
            .order_by(Thesis.created_at)
        )
    )
    checks: list[dict] = []

    checks.append(
        _result(
            "case_exists",
            case.period_start is not None
            and case.period_end is not None
            and case.evidence_cutoff is not None,
            {
                "case_id": str(case.id),
                "period_start": str(case.period_start),
                "period_end": str(case.period_end),
                "evidence_cutoff": str(case.evidence_cutoff),
            },
        )
    )

    titles = [t.title or "" for t in theses]
    missing = [d for d in EXPECTED_DIMENSIONS if not any(d in title for title in titles)]
    checks.append(
        _result(
            "six_dimensions_present",
            len(theses) >= 6 and not missing,
            {"thesis_count": len(theses), "missing": missing, "titles": titles},
        )
    )

    theme_tags = ThemeService(ResearchRepository(session)).effective_tags_for_case(case.id)
    checks.append(
        _result(
            "theme_tag_attached",
            THEME_TAG in theme_tags,
            {"effective_tags": sorted(theme_tags)},
        )
    )

    empty_snapshots = []
    missing_reviews = []
    for thesis in theses:
        latest_snapshot = session.scalar(
            select(EvidenceSnapshot)
            .where(EvidenceSnapshot.thesis_id == thesis.id)
            .order_by(EvidenceSnapshot.created_at.desc())
        )
        if latest_snapshot is None or not latest_snapshot.evidence_link_ids:
            empty_snapshots.append(thesis.title)
            continue
        assessment = session.scalar(
            select(AIAssessment).where(AIAssessment.snapshot_id == latest_snapshot.id)
        )
        if assessment is None:
            empty_snapshots.append(f"{thesis.title}:no_assessment")
            continue
        review_count = session.scalar(
            select(ReviewDecision).where(ReviewDecision.ai_assessment_id == assessment.id)
        )
        if review_count is None:
            missing_reviews.append(thesis.title)

    checks.append(
        _result(
            "latest_snapshots_non_empty",
            not empty_snapshots,
            {"empty": empty_snapshots},
        )
    )
    checks.append(
        _result(
            "latest_assessments_reviewed",
            not missing_reviews,
            {"missing_reviews": missing_reviews},
        )
    )

    roles = list(
        session.scalars(select(ThemeRole).where(ThemeRole.research_case_id == case.id))
    )
    company_ids = {role.company_id for role in roles}
    checks.append(
        _result(
            "company_roles_present",
            len(roles) >= 3,
            {"role_count": len(roles), "company_count": len(company_ids)},
        )
    )

    valuations = list(session.scalars(select(ValuationSnapshot)))
    checks.append(
        _result(
            "valuation_snapshots_present",
            any(v.source == "historical_semiconductor_fixture" for v in valuations),
            {
                "count": sum(
                    1 for v in valuations if v.source == "historical_semiconductor_fixture"
                )
            },
        )
    )

    holdings = list(session.scalars(select(HoldingDisclosure)))
    checks.append(
        _result(
            "holding_disclosures_present",
            len(holdings) >= 4,
            {"count": len(holdings)},
        )
    )

    demo_urls = list(
        session.scalars(
            select(DocumentVersion.source_url).where(
                DocumentVersion.source_url.like("%example.test%")
            )
        )
    )
    checks.append(
        _result(
            "placeholder_sources_flagged",
            len(demo_urls) > 0,
            {
                "note": "current case still uses demo fixture URLs; they must remain visible as non-primary",
                "demo_url_count": len(demo_urls),
            },
        )
    )

    causal_steps = []
    causal_edges = []
    for thesis in theses:
        if thesis.title and "反向证据" in thesis.title:
            causal_steps = list(
                session.scalars(
                    select(CausalStep)
                    .where(CausalStep.thesis_id == thesis.id)
                    .order_by(CausalStep.sequence)
                )
            )
            causal_edges = list(
                session.scalars(
                    select(CausalEdge)
                    .where(CausalEdge.source_step_id.in_([s.id for s in causal_steps] or [thesis.id]))
                )
            )
            break
    checks.append(
        _result(
            "counter_causal_chain_present",
            len(causal_steps) >= 5 and len(causal_edges) >= 4,
            {"steps": len(causal_steps), "edges": len(causal_edges)},
        )
    )

    return checks


def main() -> int:
    url = os.getenv("DATABASE_URL", "sqlite:///./evidence_seed.db")
    engine = create_engine(url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as session:
        checks = run(session)
    failed = [c for c in checks if not c["ok"]]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_url": url,
        "case_title": CASE_TITLE,
        "checks": checks,
        "passed": len(failed) == 0,
        "failed_count": len(failed),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
