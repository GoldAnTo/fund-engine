from __future__ import annotations

from dataclasses import replace

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import CompanyResearchValidationError
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


def _machine_memo_payload() -> dict[str, object]:
    return CompanyResearchArtifactCodec.encode("memo", _artifacts()["memo"])


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


def test_artifact_codec_reads_a_legacy_memo_without_derived_gaps() -> None:
    payload = _machine_memo_payload()
    payload.pop("research_gaps")

    decoded = CompanyResearchArtifactCodec.decode("memo", payload)

    assert decoded.research_gaps == ()
    assert CompanyResearchArtifactCodec.validate_payload("memo", payload) == payload


def test_human_confirmation_decodes_and_round_trips_exactly() -> None:
    payload = {
        **_machine_memo_payload(),
        "candidate_status": "human_confirmed",
        "reviewer": "human:local-user",
        "markdown": "Evidence remains insufficient.\n",
    }

    decoded = CompanyResearchArtifactCodec.decode("memo", payload)

    assert decoded.candidate_status == "human_confirmed"
    assert decoded.reviewer == "human:local-user"
    assert decoded.markdown == "Evidence remains insufficient.\n"
    assert CompanyResearchArtifactCodec.encode("memo", decoded) == payload


@pytest.mark.parametrize(
    ("candidate_status", "reviewer", "markdown"),
    (
        ("machine_draft", "human:local-user", None),
        ("machine_draft", None, "Evidence remains insufficient."),
        ("human_confirmed", None, "Evidence remains insufficient."),
        ("human_confirmed", "human:local-user", " \n\t "),
        ("human_confirmed", "another-reviewer", "Evidence remains insufficient."),
    ),
)
def test_mixed_or_open_confirmation_shapes_are_rejected(
    candidate_status: str, reviewer: str | None, markdown: str | None
) -> None:
    memo = CompanyResearchArtifactCodec.decode("memo", _machine_memo_payload())

    with pytest.raises(CompanyResearchValidationError):
        replace(
            memo,
            candidate_status=candidate_status,
            reviewer=reviewer,
            markdown=markdown,
        )


def test_machine_memo_encoding_omits_unset_human_confirmation_fields() -> None:
    payload = _machine_memo_payload()

    assert "reviewer" not in payload
    assert "markdown" not in payload
