"""Shared vocabulary for the independent research-preparation stage."""
from __future__ import annotations

import hashlib
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


def preparation_input_fingerprint(
    document_version_id: uuid.UUID, scope_version_id: uuid.UUID
) -> str:
    """Return the stable fingerprint for one frozen preparation input pair."""
    payload = f"{_FINGERPRINT_VERSION}|{document_version_id}|{scope_version_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
