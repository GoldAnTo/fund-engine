"""Stable event-research action vocabulary shared by writers and read models."""
from __future__ import annotations

from typing import Literal, TypeAlias


EventNextActionKind: TypeAlias = Literal[
    "wait",
    "review_intake",
    "review_evidence",
    "review_conclusion",
    "edit_factors",
    "complete_research_protocol",
    "view_conclusion_change",
    "review_preparation_claims",
    "review_preparation_protocol",
    "authorize_preparation_plan",
    "recover_preparation",
]

# Stored in the lifecycle projection until it gains a dedicated action-code
# column. Keeping the phrase here prevents the writer and reader drifting.
PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION = "完成新增因素的研究协议后再启动补证"
