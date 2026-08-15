"""Shared vocabulary for the independent research-preparation stage."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal


PreparationStatus = Literal[
    "preparing",
    "awaiting_claim_review",
    "awaiting_protocol_confirmation",
    "awaiting_plan_authorization",
    "recoverable_failure",
    "authorized",
]
PreparationStep = Literal["parse_claims", "draft_protocol", "draft_evidence_plan"]
StepState = Literal["queued", "running", "succeeded", "retrying", "failed", "stale"]
ReviewState = Literal["locked", "awaiting_review", "confirmed", "stale"]
ArtifactKind = Literal[
    "atomic_claim_candidates",
    "research_protocol_draft",
    "evidence_acquisition_plan",
]

_FINGERPRINT_VERSION = "research-preparation-input:v1"
_CANDIDATE_CONTEXT_FINGERPRINT_VERSION = "research-preparation-candidates:v1"
# Applies to every draft-input consumer.  Keep this domain limit shared by
# worker input validation and historical backfill reuse.
MAX_CANDIDATES = 100


def preparation_input_fingerprint(
    document_version_id: uuid.UUID, scope_version_id: uuid.UUID
) -> str:
    """Return the stable fingerprint for one frozen preparation input pair."""
    payload = f"{_FINGERPRINT_VERSION}|{document_version_id}|{scope_version_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_context_fingerprint(
    parse_artifact_sequence: int | None,
    decisions: tuple[tuple[str, str, str, str | None, str | None], ...],
) -> str:
    """Hash the ordered, effective human-review context for a draft.

    Each record is candidate ID, latest review ID/outcome, published statement
    ID, and the SHA-256 of the exact normalized text sent to the model.
    """
    payload = {
        "version": _CANDIDATE_CONTEXT_FINGERPRINT_VERSION,
        "parse_artifact_sequence": parse_artifact_sequence,
        "decisions": decisions,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
