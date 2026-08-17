"""Shared vocabulary for one-click automatic event research."""
from __future__ import annotations

from typing import Final, Literal, TypeAlias


AutomaticResearchStatus: TypeAlias = Literal[
    "queued", "running", "completed", "failed"
]
AutomaticResearchStage: TypeAlias = Literal[
    "acquire", "parse", "admit", "analyze", "conclude"
]

AUTOMATIC_STAGE_ORDER: Final = (
    "acquire",
    "parse",
    "admit",
    "analyze",
    "conclude",
)
AUTOMATIC_SOURCE_JOB_TERMINAL: Final = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)
