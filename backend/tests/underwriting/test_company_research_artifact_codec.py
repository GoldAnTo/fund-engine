from __future__ import annotations

from dataclasses import replace

import pytest

from app.models.ledger import ValidationError
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelBuilder,
)
from app.underwriting.services.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from tests.underwriting.test_company_research_model_builder import _build_input


def _artifacts() -> dict[str, object]:
    result = CompanyResearchModelBuilder().build(_build_input())
    return {
        "business_map": result.business_map,
        "driver_map": result.driver_map,
        "financial_bridge": result.financial_bridge,
        "scenario_set": result.scenario_set,
        "valuation_set": result.valuation_set,
        "judgment_context": result.judgment_context,
        "research_gaps": result.gaps,
        "memo": result.memo,
    }


@pytest.mark.parametrize("kind", tuple(_artifacts()))
def test_artifact_codec_round_trips_the_real_domain_artifact(kind: str) -> None:
    artifact = _artifacts()[kind]

    payload = CompanyResearchArtifactCodec.encode(kind, artifact)

    assert CompanyResearchArtifactCodec.decode(kind, payload) == artifact
    assert CompanyResearchArtifactCodec.validate_payload(kind, payload) == payload


@pytest.mark.parametrize("mutation", ("missing", "extra", "noncanonical"))
def test_artifact_codec_rejects_invalid_payload_shapes(mutation: str) -> None:
    payload = CompanyResearchArtifactCodec.encode(
        "business_map", _artifacts()["business_map"]
    )
    if mutation == "missing":
        payload = {}
    elif mutation == "extra":
        payload = {**payload, "fabricated": True}
    else:
        payload = {**payload, "modules": tuple(payload["modules"])}

    with pytest.raises(ValidationError, match="business_map payload is invalid"):
        CompanyResearchArtifactCodec.validate_payload("business_map", payload)


def test_artifact_codec_rejects_an_illegal_domain_value() -> None:
    judgment = _artifacts()["judgment_context"]
    payload = CompanyResearchArtifactCodec.encode(
        "judgment_context",
        replace(judgment, market_security_bridge_available=False),
    )
    payload["operating_baseline_available"] = "yes"

    with pytest.raises(ValidationError, match="judgment_context payload is invalid"):
        CompanyResearchArtifactCodec.validate_payload("judgment_context", payload)


def test_artifact_codec_rejects_a_noncanonical_decimal_representation() -> None:
    payload = CompanyResearchArtifactCodec.encode(
        "financial_bridge", _artifacts()["financial_bridge"]
    )
    canonical = payload["rows"][0]["revenue"]
    payload["rows"][0]["revenue"] = f"{canonical}.0"

    with pytest.raises(ValidationError, match="financial_bridge payload is invalid"):
        CompanyResearchArtifactCodec.validate_payload("financial_bridge", payload)
