"""Scope may be derived from the Case, but an explicit malformed scope is rejected."""
import pytest
from app.ai.client import LLMMalformedResponseError
from app.ai.output_schema import ProposalOutput, validate_output


@pytest.mark.parametrize("scope", [None, [], "scope", 123])
def test_explicit_invalid_proposal_scope_is_not_coerced(scope):
    with pytest.raises(LLMMalformedResponseError):
        validate_output({"links": [{"source_statement_id": "known-statement", "role": "supports", "reason": "reason", "scope": scope}]}, ProposalOutput)
