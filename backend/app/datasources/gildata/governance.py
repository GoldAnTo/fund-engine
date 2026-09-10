"""Evidence-use rights declared for the Gildata data source."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _literal_true(name: str) -> bool:
    """Return true only when an environment value is literally ``true``."""

    return os.getenv(name, "").lower() == "true"


@dataclass(frozen=True)
class GildataEvidenceRights:
    """Immutable, fail-closed evidence rights for Gildata content."""

    allow_ai_processing: bool
    allow_display: bool

    def __post_init__(self) -> None:
        for name in ("allow_ai_processing", "allow_display"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")

    @property
    def formal_evidence_allowed(self) -> bool:
        return self.allow_ai_processing and self.allow_display

    @classmethod
    def from_env(cls) -> GildataEvidenceRights:
        return cls(
            allow_ai_processing=_literal_true("GILDATA_ALLOW_AI_PROCESSING"),
            allow_display=_literal_true("GILDATA_ALLOW_DISPLAY"),
        )
