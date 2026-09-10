"""Shared, fail-closed setup for auditable walkthrough commands."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.api.v1.tenant_context import _configured_tokens
from app.datasources.gildata.governance import GildataEvidenceRights


class WalkthroughConfigurationError(RuntimeError):
    """A required host-owned walkthrough setting is unavailable."""


@dataclass(frozen=True)
class WalkthroughPaths:
    state: Path
    jsonl: Path
    summary: Path


def configured_research_headers() -> dict[str, str]:
    """Return one configured bearer credential without exposing its value."""
    configured = _configured_tokens()
    if not configured:
        raise WalkthroughConfigurationError(
            "RESEARCH_TENANT_TOKENS must configure a bearer token for the walkthrough"
        )
    token, _actor = configured[0]
    return {"Authorization": f"Bearer {token}"}


def configured_gildata_evidence_rights() -> GildataEvidenceRights:
    """Require an explicit deployment grant before a live evidence walkthrough."""
    rights = GildataEvidenceRights.from_env()
    if not rights.formal_evidence_allowed:
        raise WalkthroughConfigurationError(
            "GILDATA_ALLOW_AI_PROCESSING and GILDATA_ALLOW_DISPLAY must both be true"
        )
    return rights


def walkthrough_paths(output_dir: Path, run_id: str) -> WalkthroughPaths:
    """Keep each invocation's state and audit files separate by run id."""
    stem = f"cambricon_walkthrough_{run_id}"
    return WalkthroughPaths(
        state=output_dir / f"{stem}_state.json",
        jsonl=output_dir / f"{stem}.jsonl",
        summary=output_dir / f"{stem}_summary.json",
    )


def walkthrough_database_path(backend_dir: Path, run_id: str) -> Path:
    """Return the run-scoped SQLite database path used by a walkthrough."""
    return backend_dir / f"evidence_walkthrough_{run_id}.db"


def classify_historical_case_read(status: int, body: object) -> str | None:
    """Classify the expected absence of a Case before it was created.

    Point-in-time reads must not invent a dossier before its ledger creation;
    their 404 is an observation, not an operational failure.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if status == 404 and isinstance(error, dict) and error.get("code") == "not_found":
        return "case_not_created_at_cutoff"
    return None


def atomic_claim_review_payload(claim_id: str) -> dict[str, str]:
    """Build the explicit human decision that releases one atomic claim."""
    return {
        "outcome": "confirmed",
        "reviewer": "walkthrough-reviewer",
        "reason": "人工核对原文连续引文后确认发布",
        "idempotency_key": f"walkthrough-atomic-claim-{claim_id}",
    }


def proposal_review_payload(
    version: int, *, can_accept: bool
) -> dict[str, str | int]:
    """Build the decision that respects the source-admission gate."""
    outcome = "confirmed" if can_accept else "rejected"
    reason = (
        "人工核对来源与命题关联后确认发布"
        if can_accept
        else "人工复核：该来源不满足正式证据准入条件，保留提案审计记录但不发布"
    )
    return {
        "outcome": outcome,
        "reason": reason,
        "reviewer_id": "walkthrough-reviewer",
        "expected_version": version,
    }


def assessment_review_payload(
    ai_conclusion: str, *, evidence_count: int
) -> dict[str, str | None]:
    """Confirm, but never fabricate, an assessment conclusion in the walkthrough."""
    if evidence_count == 0 and ai_conclusion == "insufficient_evidence":
        reason = (
            "人工复核：当前冻结快照没有已发布证据，确认证据不足结论；"
            "历史交叉验证不改写该快照。"
        )
    else:
        reason = "人工复核：已按当前冻结证据快照核对，确认 AI 结论。"
    return {
        "outcome": "confirmed",
        "conclusion": None,
        "reason": reason,
        "reviewer": "walkthrough-reviewer",
    }
