from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.ai.client import LLMProviderError
from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchProcessWarning,
    CompanyResearchProcessWarningCode,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_ai_memo import (
    CompanyResearchAIMemoAdapter,
)
from app.underwriting.services.company_research_model_builder import (
    EvidenceBuildMode,
)
from tests.underwriting.test_company_research_model_builder import _build_input


class _Client:
    provider = "test-provider"
    model_version = "provider-model-v7"

    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls: list[tuple[list[dict], str]] = []

    def chat_json(self, messages: list[dict], schema_hint: str = "") -> dict:
        self.calls.append((messages, schema_hint))
        return self.response_factory(messages)


def _valid_response(messages: list[dict]) -> dict:
    request = json.loads(messages[-1]["content"])
    authenticated_input = request["authenticated_input"]
    artifact_roles = authenticated_input["artifact_roles"]
    driver_keys = authenticated_input["driver_keys"][:3]

    def claim(
        template: str,
        relation: str,
        subject_key: str,
        object_key: str,
        citations: list[str],
    ) -> dict:
        return {
            "template": template,
            "relation": relation,
            "subject_key": subject_key,
            "object_key": object_key,
            "citations": sorted(citations),
        }

    return {
        "summary": claim(
            "assessment_summary",
            "summarizes",
            artifact_roles["business_map"],
            artifact_roles["financial_bridge"],
            [artifact_roles["business_map"], artifact_roles["financial_bridge"]],
        ),
        "business_explanation": claim(
            "business_overview",
            "uses",
            artifact_roles["business_map"],
            artifact_roles["driver_map"],
            [artifact_roles["business_map"], artifact_roles["driver_map"]],
        ),
        "driver_explanations": [
            {
                "driver_key": key,
                **claim(
                    "driver_path",
                    "drives",
                    key,
                    artifact_roles["driver_map"],
                    [artifact_roles["driver_map"]],
                ),
            }
            for key in driver_keys
        ],
        "counterevidence": [],
        "gaps": [],
        "next_checks": [],
    }


def test_ai_memo_accepts_only_closed_cited_structure_and_freezes_provenance() -> None:
    value = _build_input()
    value = replace(
        value,
        evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
    )
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)
    client = _Client(_valid_response)

    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    narrative = outcome.memo.narrative
    assert narrative is not None
    assert narrative.generator_kind == "authenticated_ai"
    assert narrative.schema_version == "company-research-memo-narrative.v2"
    assert narrative.provider == "test-provider"
    assert narrative.model == "provider-model-v7"
    assert narrative.provider_model_identifier is None
    assert narrative.prompt_version == "company-research-ai-memo.v2"
    assert len(narrative.driver_explanations) == 3
    assert (
        len(narrative.prompt_hash)
        == len(narrative.input_hash)
        == len(narrative.output_hash)
        == 64
    )
    assert outcome.warnings == ()
    payload = CompanyResearchArtifactCodec.encode("memo", outcome.memo)
    assert payload["narrative"]["schema_version"] == "company-research-memo-narrative.v2"
    assert "provider_model_identifier" not in payload["narrative"]
    assert CompanyResearchArtifactCodec.decode("memo", payload) == outcome.memo
    messages = client.calls[0][0]
    prompt = messages[-1]["content"]
    assert "source_url" not in prompt
    assert "raw_hash" not in prompt
    request = json.loads(prompt)
    assert narrative.prompt_hash == canonical_hash(
        {
            "system": messages[0]["content"],
            "prompt_version": request["prompt_version"],
            "instructions": request["instructions"],
            "response_contract": request["response_contract"],
        }
    )
    assert "text" not in json.dumps(request["response_contract"])
    assert narrative.output_hash == canonical_hash(narrative.content_payload())


def test_ai_memo_excludes_quarantined_derived_value_fact_and_source() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    unsupported = evidence["facts"][-1]
    unsupported.update(
        {
            "fact_key": "quarantined_derived_revenue",
            "value": "987654321",
            "value_kind": "derived",
            "source_role": "quarantined_publisher",
            "source_url": "https://quarantined.example/research",
            "source_locator": "quarantined-derived",
            "raw_hash": "f" * 64,
        }
    )
    source_refs = tuple(
        {
            field: str(fact[field])
            for field in ("source_role", "source_url", "source_locator", "raw_hash")
        }
        for fact in evidence["facts"]
    )
    source_refs = tuple(
        dict(items)
        for items in sorted(
            {tuple(sorted(item.items())) for item in source_refs}
        )
    )
    value = replace(
        value,
        evidence_payload=evidence,
        evidence_content_hash=canonical_hash(evidence),
        source_refs=source_refs,
        evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
    )
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)
    client = _Client(_valid_response)

    CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    request = json.loads(client.calls[0][0][-1]["content"])
    authenticated_input = request["authenticated_input"]
    assert "quarantined_derived_revenue" not in authenticated_input["fact_keys"]
    assert all(
        fact["value"] != "987654321"
        for fact in authenticated_input["facts"]
    )
    assert all(
        source["source_label"] != "quarantined publisher"
        and source["source_domain"] != "quarantined.example"
        for source in authenticated_input["citation_sources"]
    )


@pytest.mark.parametrize(
    "prose",
    (
        "According to Reuters, the deterministic model is bounded.",
        "as reuters explains, the deterministic model is bounded.",
        "根据路透社，该模型有边界。",
    ),
)
def test_ai_memo_rejects_every_provider_authored_prose_field(
    prose: str,
) -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def invented(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        response["summary"]["text"] = prose
        return response

    client = _Client(invented)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 3
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"


def test_ai_memo_renders_authenticated_citation_display_without_provider_prose() -> (
    None
):
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def attributed(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        request = json.loads(messages[-1]["content"])
        authenticated_input = request["authenticated_input"]
        fact_key = authenticated_input["fact_keys"][0]
        business_map = authenticated_input["artifact_roles"]["business_map"]
        response["counterevidence"] = [
            {
                "template": "counterevidence_fact",
                "relation": "challenges",
                "subject_key": fact_key,
                "object_key": business_map,
                "citations": [fact_key],
            }
        ]
        return response

    client = _Client(attributed)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 1
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "authenticated_ai"
    rendered = outcome.memo.narrative.counterevidence[0].text
    assert "regulatory filing" in rendered
    assert "sec.gov" in rendered
    assert "Reuters" not in rendered


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("template", "invented_template"),
        ("relation", "reported_by"),
        ("subject_key", "fact:invented"),
        ("object_key", "artifact:invented"),
    ),
)
def test_ai_memo_rejects_unknown_structure_and_unsupported_key_pairings(
    field: str,
    replacement: str,
) -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def invented(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        response["summary"][field] = replacement
        return response

    client = _Client(invented)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 3
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"


def test_ai_memo_codec_reads_legacy_opaque_provider_provenance() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)
    outcome = CompanyResearchAIMemoAdapter(_Client(_valid_response)).enrich(
        build_input=value,
        build_result=result,
    )
    payload = CompanyResearchArtifactCodec.encode("memo", outcome.memo)
    narrative = payload["narrative"]
    narrative["schema_version"] = "company-research-memo-narrative.v1"
    narrative["provider_model_identifier"] = "legacy-provider-model-v1"
    narrative.pop("provider")
    narrative.pop("model")
    narrative.pop("prompt_hash")

    decoded = CompanyResearchArtifactCodec.decode("memo", payload)

    assert decoded.narrative is not None
    assert decoded.narrative.provider_model_identifier == "legacy-provider-model-v1"
    assert CompanyResearchArtifactCodec.encode("memo", decoded) == payload


def test_ai_memo_codec_rejects_tampered_prompt_hash() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)
    outcome = CompanyResearchAIMemoAdapter(_Client(_valid_response)).enrich(
        build_input=value,
        build_result=result,
    )
    payload = CompanyResearchArtifactCodec.encode("memo", outcome.memo)
    payload["narrative"]["prompt_hash"] = "0" * 64

    with pytest.raises(ValidationError, match="memo payload is invalid"):
        CompanyResearchArtifactCodec.decode("memo", payload)


def test_ai_memo_retries_extra_free_text_three_total_attempts_then_falls_back() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def invented(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        response["summary"]["text"] = "Invented unsupported value 987654321."
        return response

    client = _Client(invented)

    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 3
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"
    assert outcome.warnings == (
        CompanyResearchProcessWarning(
            code=CompanyResearchProcessWarningCode.AI_NARRATIVE_VALIDATION_EXHAUSTED,
        ),
    )


def test_ai_memo_retries_malformed_nested_citations_then_falls_back() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def malformed(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        response["summary"]["citations"] = [["unexpected"]]
        return response

    client = _Client(malformed)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 3
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"
    assert outcome.warnings == (
        CompanyResearchProcessWarning(
            code=CompanyResearchProcessWarningCode.AI_NARRATIVE_VALIDATION_EXHAUSTED,
        ),
    )


def test_ai_memo_rejects_an_invented_stable_fact_key() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def invented(messages: list[dict]) -> dict:
        response = _valid_response(messages)
        response["summary"]["subject_key"] = "invented_fact_key"
        return response

    client = _Client(invented)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 3
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"


def test_ai_memo_provider_failure_preserves_deterministic_memo() -> None:
    value = _build_input()
    result = __import__(
        "app.underwriting.services.company_research_model_builder",
        fromlist=["CompanyResearchModelBuilder"],
    ).CompanyResearchModelBuilder().build(value)

    def unavailable(_messages: list[dict]) -> dict:
        raise LLMProviderError("LLM provider request failed")

    client = _Client(unavailable)
    outcome = CompanyResearchAIMemoAdapter(client).enrich(
        build_input=value,
        build_result=result,
    )

    assert len(client.calls) == 1
    assert outcome.memo.assessment_status == result.memo.assessment_status
    assert outcome.memo.narrative is not None
    assert outcome.memo.narrative.generator_kind == "deterministic_fallback"
    assert outcome.memo.narrative.provider == "fund-engine"
    assert outcome.memo.narrative.model == "deterministic-company-research-memo.v1"
    assert outcome.warnings[0].code is (
        CompanyResearchProcessWarningCode.AI_NARRATIVE_PROVIDER_UNAVAILABLE
    )
