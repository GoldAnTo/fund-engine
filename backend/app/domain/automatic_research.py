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
AUTOMATIC_RESEARCH_ACTIVE_RUN_STATUSES: Final = (
    "queued",
    "running",
    "waiting_for_sources",
    "waiting_for_review",
)
AUTOMATIC_RESEARCH_MANAGED_START_MESSAGE: Final = (
    "自动研究 Case 必须通过自动研究重试接口重新运行"
)
AUTOMATIC_RESEARCH_UNMANAGED_RUN_MESSAGE: Final = (
    "自动研究存在其他进行中的运行，请稍后重试"
)
