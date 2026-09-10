"""Deployment-owned downstream-use rights for Gildata material."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


@dataclass(frozen=True)
class GildataEvidenceRights:
    """Rights required before licensed material can become formal evidence."""

    allow_ai_processing: bool
    allow_display: bool

    @property
    def formal_evidence_allowed(self) -> bool:
        return self.allow_ai_processing and self.allow_display

    @classmethod
    def from_env(cls) -> "GildataEvidenceRights":
        return cls(
            allow_ai_processing=_env_true("GILDATA_ALLOW_AI_PROCESSING"),
            allow_display=_env_true("GILDATA_ALLOW_DISPLAY"),
        )
