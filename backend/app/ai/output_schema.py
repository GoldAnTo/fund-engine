"""Validate provider output before compliance checks or ledger writes."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.client import LLMMalformedResponseError, LLM_MALFORMED_RESPONSE_MESSAGE


class _Output(BaseModel):
    model_config = ConfigDict(strict=True)


class ProposedLink(_Output):
    source_statement_id: str = Field(min_length=1)
    role: Literal["supports", "contradicts", "contextualizes"]
    reason: str = Field(min_length=1)
    # Omission uses the trusted Case-derived scope in EvidenceProposer.
    # Explicit values still undergo strict dictionary validation.
    scope: dict[str, Any] = Field(default_factory=dict)


class ProposalOutput(_Output):
    links: list[ProposedLink]


class AssessmentOutput(_Output):
    conclusion: Literal["supported", "contradicted", "insufficient_evidence"]
    rationale: str = Field(min_length=1)
    gaps: list[str]


def validate_output(output: Any, schema: type[_Output]) -> dict:
    try:
        return schema.model_validate(output).model_dump()
    except ValidationError:
        # Pydantic errors embed provider values; keep them out of traceback.
        raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE) from None
