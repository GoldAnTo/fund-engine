"""Closed AI narrative adapter over authenticated company-research results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from app.ai.client import LLMClient, LLMMalformedResponseError, LLMProviderError
from app.underwriting.domain.company_research import (
    COMPANY_RESEARCH_AI_MEMO_INSTRUCTIONS,
    COMPANY_RESEARCH_AI_MEMO_PROMPT_VERSION,
    COMPANY_RESEARCH_AI_MEMO_SYSTEM_PROMPT,
    CompanyResearchDriverExplanation,
    CompanyResearchMemoArtifact,
    CompanyResearchMemoNarrative,
    CompanyResearchNarrativeClaim,
    CompanyResearchProcessWarning,
    CompanyResearchProcessWarningCode,
    canonical_decimal_string,
    company_research_ai_memo_prompt_hash,
    company_research_ai_memo_response_contract,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchBuildInput,
    CompanyResearchBuildResult,
    CompanyResearchModelBuilder,
)

PROMPT_VERSION = COMPANY_RESEARCH_AI_MEMO_PROMPT_VERSION
_MAX_ATTEMPTS = 3
_RESPONSE_FIELDS = frozenset(
    {
        "summary",
        "business_explanation",
        "driver_explanations",
        "counterevidence",
        "gaps",
        "next_checks",
    }
)
_CLAIM_FIELDS = frozenset(
    {"template", "relation", "subject_key", "object_key", "citations"}
)
_DRIVER_FIELDS = _CLAIM_FIELDS | {"driver_key"}
_SECTION_CONTRACTS = {
    "summary": ("assessment_summary", "summarizes"),
    "business_explanation": ("business_overview", "uses"),
    "driver_explanations": ("driver_path", "drives"),
    "counterevidence": ("counterevidence_fact", "challenges"),
    "gaps": ("open_gap", "blocks"),
    "next_checks": ("verify_gap", "verifies"),
}


@dataclass(frozen=True, slots=True)
class CompanyResearchAIMemoOutcome:
    memo: CompanyResearchMemoArtifact
    warnings: tuple[CompanyResearchProcessWarning, ...]


class _NarrativeContractError(ValueError):
    pass


def _artifact_roles(result: CompanyResearchBuildResult) -> dict[str, str]:
    memo = result.memo
    refs = {
        "business_map": memo.business_map_ref,
        "driver_map": memo.driver_map_ref,
        "financial_bridge": memo.financial_bridge_ref,
        "scenario_set": memo.scenario_set_ref,
    }
    if memo.valuation_set_ref is not None:
        refs["valuation_set"] = memo.valuation_set_ref
    return {
        role: f"artifact:{reference.artifact_kind}:{reference.content_hash}"
        for role, reference in refs.items()
    }


def _source_domain(value: str) -> str | None:
    parsed = urlsplit(value if "://" in value else f"//{value}")
    host = parsed.hostname
    if host is None:
        return None
    normalized = host.casefold().removeprefix("www.").rstrip(".")
    return normalized or None


def _display_label(value: str) -> str:
    return " ".join(value.replace("_", " ").split())


def _authenticated_input(
    build_input: CompanyResearchBuildInput,
    result: CompanyResearchBuildResult,
) -> dict[str, object]:
    allowed_fact_rows = CompanyResearchModelBuilder.effective_evidence_facts(
        build_input
    )
    allowed_facts = tuple(
        {
            "fact_key": str(fact["fact_key"]),
            "business_module": str(fact["business_module"]),
            "metric_key": str(fact["metric_key"]),
            "value": str(fact["value"]),
            "currency": str(fact["currency"]),
            "unit": str(fact["unit"]),
            "period_start": str(fact["period_start"]),
            "period_end": str(fact["period_end"]),
        }
        for fact in allowed_fact_rows
    )
    artifact_roles = _artifact_roles(result)
    artifact_keys = tuple(artifact_roles.values())
    fact_keys = tuple(str(item["fact_key"]) for item in allowed_facts)
    driver_keys = tuple(driver.driver_key for driver in result.driver_map.drivers)
    gap_keys = tuple(gap.code for gap in result.gaps)
    citation_sources = tuple(
        {
            "fact_key": str(fact["fact_key"]),
            "source_label": _display_label(str(fact["source_role"])),
            "source_domain": _source_domain(str(fact["source_url"])),
        }
        for fact in allowed_fact_rows
    )
    display_labels = {
        **{
            key: _display_label(role)
            for role, key in artifact_roles.items()
        },
        **{
            str(fact["fact_key"]): " ".join(
                (
                    _display_label(str(fact["business_module"])),
                    _display_label(str(fact["metric_key"])),
                )
            )
            for fact in allowed_facts
        },
        **{key: _display_label(key) for key in driver_keys},
        **{gap.code: gap.message for gap in result.gaps},
    }
    gap_summaries = tuple(
        {
            "gap_key": gap.code,
            "module_key": gap.module_key,
            "severity": gap.severity.value,
            "message": gap.message,
        }
        for gap in result.gaps
    )
    valuation: tuple[dict[str, object], ...] = ()
    if result.valuation_set is not None:
        valuation = tuple(
            {
                "security_external_key": item.security_external_key,
                "value_per_share_minimum": canonical_decimal_string(
                    item.value_per_share.minimum
                ),
                "value_per_share_maximum": canonical_decimal_string(
                    item.value_per_share.maximum
                ),
                "value_currency": item.value_currency,
                "base_currency_return_minimum": canonical_decimal_string(
                    item.base_currency_return.minimum
                ),
                "base_currency_return_maximum": canonical_decimal_string(
                    item.base_currency_return.maximum
                ),
            }
            for item in result.valuation_set.security_value_ranges
        )
    authenticated: dict[str, object] = {
        "schema_version": "company-research-ai-memo-input.v2",
        "artifact_roles": artifact_roles,
        "artifact_keys": artifact_keys,
        "fact_keys": fact_keys,
        "gap_keys": gap_keys,
        "driver_keys": driver_keys,
        "display_labels": dict(sorted(display_labels.items())),
        "citation_sources": citation_sources,
        "assessment_status": result.assessment.status,
        "facts": allowed_facts,
        "financial_bridge": tuple(
            {
                "fiscal_year": row.fiscal_year,
                "revenue": canonical_decimal_string(row.revenue),
                "operating_income": canonical_decimal_string(row.operating_income),
                "fcff": canonical_decimal_string(row.fcff),
            }
            for row in result.financial_bridge.rows
        ),
        "valuation": valuation,
        "gaps": gap_summaries,
        "next_checks": result.judgment_context.next_verification_events,
    }
    if build_input.critical_input_replacements:
        authenticated["critical_input_overlays"] = tuple(
            {
                "key": item.key,
                "value": (
                    canonical_decimal_string(item.value)
                    if not isinstance(item.value, str)
                    else item.value
                ),
                "unit": item.unit,
                "currency": item.currency,
                "period": item.period,
                "provenance_role": "user_assumption",
                "decision_artifact_id": str(
                    build_input.critical_inputs_artifact_id
                ),
                "decision_content_hash": build_input.critical_inputs_content_hash,
            }
            for item in build_input.critical_input_replacements
        )
    return authenticated


def _string_values(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise _NarrativeContractError(f"authenticated {name} are invalid")
    return tuple(value)


def _mapping_value(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _NarrativeContractError(f"authenticated {name} is invalid")
    return value


def _claim_binding(
    section: str,
    *,
    authenticated_input: Mapping[str, object],
    driver_key: str | None,
) -> tuple[frozenset[str], str, tuple[str, ...]]:
    roles = _mapping_value(authenticated_input.get("artifact_roles"), "artifact roles")
    business_map = roles.get("business_map")
    driver_map = roles.get("driver_map")
    financial_bridge = roles.get("financial_bridge")
    if not all(
        isinstance(item, str) and item
        for item in (business_map, driver_map, financial_bridge)
    ):
        raise _NarrativeContractError("authenticated artifact roles are invalid")
    if section == "summary":
        return frozenset({business_map}), financial_bridge, tuple(
            sorted((business_map, financial_bridge))
        )
    if section == "business_explanation":
        return frozenset({business_map}), driver_map, tuple(
            sorted((business_map, driver_map))
        )
    if section == "driver_explanations":
        if driver_key is None:
            raise _NarrativeContractError("driver key is required")
        return frozenset({driver_key}), driver_map, (driver_map,)
    if section == "counterevidence":
        return (
            frozenset(_string_values(authenticated_input.get("fact_keys"), "fact keys")),
            business_map,
            (),
        )
    if section == "gaps":
        return (
            frozenset(_string_values(authenticated_input.get("gap_keys"), "gap keys")),
            business_map,
            (),
        )
    if section == "next_checks":
        gap_keys = frozenset(
            _string_values(authenticated_input.get("gap_keys"), "gap keys")
        )
        return gap_keys, "", ()
    raise _NarrativeContractError("narrative section is invalid")


def _citation_suffix(
    citations: tuple[str, ...], authenticated_input: Mapping[str, object]
) -> str:
    raw_sources = authenticated_input.get("citation_sources")
    if not isinstance(raw_sources, (list, tuple)):
        raise _NarrativeContractError("authenticated citation sources are invalid")
    sources: dict[str, str] = {}
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise _NarrativeContractError("authenticated citation source is invalid")
        fact_key = raw.get("fact_key")
        label = raw.get("source_label")
        domain = raw.get("source_domain")
        if (
            not isinstance(fact_key, str)
            or not isinstance(label, str)
            or not label
            or (domain is not None and not isinstance(domain, str))
        ):
            raise _NarrativeContractError("authenticated citation source is invalid")
        sources[fact_key] = f"{label} ({domain})" if domain else label
    displays = tuple(dict.fromkeys(sources[key] for key in citations if key in sources))
    return f" Sources: {'; '.join(displays)}." if displays else ""


def _render_claim(
    section: str,
    *,
    subject_key: str,
    object_key: str,
    citations: tuple[str, ...],
    authenticated_input: Mapping[str, object],
) -> CompanyResearchNarrativeClaim:
    labels = _mapping_value(authenticated_input.get("display_labels"), "display labels")

    def label(key: str) -> str:
        value = labels.get(key)
        if not isinstance(value, str) or not value:
            raise _NarrativeContractError("authenticated display label is missing")
        return value

    if section == "summary":
        status = authenticated_input.get("assessment_status")
        if not isinstance(status, str) or not status:
            raise _NarrativeContractError("authenticated assessment status is invalid")
        text = (
            f"Assessment {_display_label(status)} uses {label(subject_key)} "
            f"and {label(object_key)}."
        )
    elif section == "business_explanation":
        text = f"{label(subject_key)} uses {label(object_key)}."
    elif section == "driver_explanations":
        text = f"{label(subject_key)} is represented in {label(object_key)}."
    elif section == "counterevidence":
        text = f"Counterevidence: {label(subject_key)}."
    elif section == "gaps":
        text = f"Open gap: {label(subject_key).rstrip('.')}."
    elif section == "next_checks":
        text = f"Next check: verify {label(subject_key).rstrip('.')}."
    else:
        raise _NarrativeContractError("narrative section is invalid")
    return CompanyResearchNarrativeClaim(
        text=text + _citation_suffix(citations, authenticated_input),
        citations=citations,
    )


def _structured_claim(
    value: object,
    *,
    section: str,
    authenticated_input: Mapping[str, object],
    driver_key: str | None = None,
    fields: frozenset[str] = _CLAIM_FIELDS,
) -> CompanyResearchNarrativeClaim:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise _NarrativeContractError("structured claim fields are invalid")
    template = value.get("template")
    relation = value.get("relation")
    subject_key = value.get("subject_key")
    object_key = value.get("object_key")
    citations = value.get("citations")
    expected_template, expected_relation = _SECTION_CONTRACTS[section]
    allowed_subjects, expected_object, expected_citations = _claim_binding(
        section,
        authenticated_input=authenticated_input,
        driver_key=driver_key,
    )
    if (
        template != expected_template
        or relation != expected_relation
        or not isinstance(subject_key, str)
        or subject_key not in allowed_subjects
        or not isinstance(object_key, str)
        or not isinstance(citations, list)
        or not citations
        or not all(isinstance(item, str) and item for item in citations)
        or citations != sorted(set(citations))
    ):
        raise _NarrativeContractError("structured claim contract is invalid")
    if section in {"counterevidence", "gaps"}:
        expected_citations = (subject_key,)
    elif section == "next_checks":
        expected_object = subject_key
        expected_citations = (subject_key,)
    if object_key != expected_object or tuple(citations) != expected_citations:
        raise _NarrativeContractError("structured claim key pairing is invalid")
    return _render_claim(
        section,
        subject_key=subject_key,
        object_key=object_key,
        citations=tuple(citations),
        authenticated_input=authenticated_input,
    )


def _narrative(
    response: object,
    *,
    authenticated_input: Mapping[str, object],
    provider: str,
    model: str,
    prompt_hash: str,
    generator_kind: str,
) -> CompanyResearchMemoNarrative:
    if not isinstance(response, Mapping) or set(response) != _RESPONSE_FIELDS:
        raise _NarrativeContractError("AI memo fields are invalid")
    summary = _structured_claim(
        response["summary"],
        section="summary",
        authenticated_input=authenticated_input,
    )
    business = _structured_claim(
        response["business_explanation"],
        section="business_explanation",
        authenticated_input=authenticated_input,
    )
    raw_drivers = response["driver_explanations"]
    driver_keys = frozenset(str(item) for item in authenticated_input["driver_keys"])
    if not isinstance(raw_drivers, list) or len(raw_drivers) != 3:
        raise _NarrativeContractError("AI memo requires exactly three drivers")
    drivers: list[CompanyResearchDriverExplanation] = []
    for raw in raw_drivers:
        if not isinstance(raw, Mapping) or set(raw) != _DRIVER_FIELDS:
            raise _NarrativeContractError("AI memo driver fields are invalid")
        driver_key = raw.get("driver_key")
        if not isinstance(driver_key, str) or driver_key not in driver_keys:
            raise _NarrativeContractError("AI memo driver key is invalid")
        claim = _structured_claim(
            raw,
            section="driver_explanations",
            authenticated_input=authenticated_input,
            driver_key=driver_key,
            fields=_DRIVER_FIELDS,
        )
        drivers.append(
            CompanyResearchDriverExplanation(
                driver_key=driver_key,
                text=claim.text,
                citations=claim.citations,
            )
        )
    if len({item.driver_key for item in drivers}) != 3:
        raise _NarrativeContractError("AI memo driver keys must be unique")

    def claims(name: str) -> tuple[CompanyResearchNarrativeClaim, ...]:
        raw_values = response[name]
        if not isinstance(raw_values, list):
            raise _NarrativeContractError(f"AI memo {name} must be an array")
        return tuple(
            _structured_claim(
                item,
                section=name,
                authenticated_input=authenticated_input,
            )
            for item in raw_values
        )

    counterevidence = claims("counterevidence")
    gaps = claims("gaps")
    next_checks = claims("next_checks")
    content = {
        "summary": summary.canonical_payload(),
        "business_explanation": business.canonical_payload(),
        "driver_explanations": tuple(item.canonical_payload() for item in drivers),
        "counterevidence": tuple(
            item.canonical_payload() for item in counterevidence
        ),
        "gaps": tuple(item.canonical_payload() for item in gaps),
        "next_checks": tuple(item.canonical_payload() for item in next_checks),
    }
    return CompanyResearchMemoNarrative(
        schema_version="company-research-memo-narrative.v2",
        summary=summary,
        business_explanation=business,
        driver_explanations=tuple(drivers),
        counterevidence=counterevidence,
        gaps=gaps,
        next_checks=next_checks,
        generator_kind=generator_kind,
        prompt_version=PROMPT_VERSION,
        input_hash=canonical_hash(authenticated_input),
        output_hash=canonical_hash(content),
        provider=provider,
        model=model,
        prompt_hash=prompt_hash,
    )


def _fallback_response(authenticated_input: Mapping[str, object]) -> dict[str, object]:
    roles = _mapping_value(authenticated_input.get("artifact_roles"), "artifact roles")
    driver_keys = tuple(str(item) for item in authenticated_input["driver_keys"])
    gap_keys = tuple(str(item) for item in authenticated_input["gap_keys"])
    business_map = str(roles["business_map"])
    driver_map = str(roles["driver_map"])
    financial_bridge = str(roles["financial_bridge"])

    def claim(
        template: str,
        relation: str,
        subject_key: str,
        object_key: str,
        citations: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "template": template,
            "relation": relation,
            "subject_key": subject_key,
            "object_key": object_key,
            "citations": list(citations),
        }

    return {
        "summary": claim(
            "assessment_summary",
            "summarizes",
            business_map,
            financial_bridge,
            tuple(sorted((business_map, financial_bridge))),
        ),
        "business_explanation": claim(
            "business_overview",
            "uses",
            business_map,
            driver_map,
            tuple(sorted((business_map, driver_map))),
        ),
        "driver_explanations": [
            {
                "driver_key": key,
                **claim(
                    "driver_path",
                    "drives",
                    key,
                    driver_map,
                    (driver_map,),
                ),
            }
            for key in driver_keys[:3]
        ],
        "counterevidence": [],
        "gaps": [
            claim("open_gap", "blocks", gap_key, business_map, (gap_key,))
            for gap_key in gap_keys
        ],
        "next_checks": [
            claim("verify_gap", "verifies", gap_key, gap_key, (gap_key,))
            for gap_key in gap_keys
        ],
    }


class CompanyResearchAIMemoAdapter:
    """Let an LLM explain deterministic results without changing any of them."""

    def __init__(self, client: LLMClient | None) -> None:
        self._client = client

    def enrich(
        self,
        *,
        build_input: CompanyResearchBuildInput,
        build_result: CompanyResearchBuildResult,
    ) -> CompanyResearchAIMemoOutcome:
        authenticated_input = _authenticated_input(build_input, build_result)
        provider = (
            str(getattr(self._client, "provider", "openai_compatible"))
            if self._client is not None
            else "unavailable"
        )
        model = self._client.model_version if self._client is not None else "unavailable"
        request = {
            "prompt_version": PROMPT_VERSION,
            "instructions": COMPANY_RESEARCH_AI_MEMO_INSTRUCTIONS,
            "response_contract": company_research_ai_memo_response_contract(),
            "authenticated_input": authenticated_input,
        }
        prompt_hash = company_research_ai_memo_prompt_hash()
        messages = [
            {
                "role": "system",
                "content": COMPANY_RESEARCH_AI_MEMO_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        warning_code: CompanyResearchProcessWarningCode | None = None
        if self._client is not None:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = self._client.chat_json(
                        messages,
                        schema_hint="company_research_ai_memo_v2",
                    )
                    narrative = _narrative(
                        response,
                        authenticated_input=authenticated_input,
                        provider=provider,
                        model=model,
                        prompt_hash=prompt_hash,
                        generator_kind="authenticated_ai",
                    )
                    return CompanyResearchAIMemoOutcome(
                        memo=replace(build_result.memo, narrative=narrative),
                        warnings=(),
                    )
                except LLMMalformedResponseError:
                    if attempt == _MAX_ATTEMPTS - 1:
                        warning_code = (
                            CompanyResearchProcessWarningCode.AI_NARRATIVE_VALIDATION_EXHAUSTED
                        )
                except LLMProviderError:
                    warning_code = (
                        CompanyResearchProcessWarningCode.AI_NARRATIVE_PROVIDER_UNAVAILABLE
                    )
                    break
                except _NarrativeContractError:
                    if attempt == _MAX_ATTEMPTS - 1:
                        warning_code = (
                            CompanyResearchProcessWarningCode.AI_NARRATIVE_VALIDATION_EXHAUSTED
                        )
        else:
            warning_code = (
                CompanyResearchProcessWarningCode.AI_NARRATIVE_PROVIDER_UNAVAILABLE
            )
        fallback = _narrative(
            _fallback_response(authenticated_input),
            authenticated_input=authenticated_input,
            provider="fund-engine",
            model="deterministic-company-research-memo.v1",
            prompt_hash=prompt_hash,
            generator_kind="deterministic_fallback",
        )
        return CompanyResearchAIMemoOutcome(
            memo=replace(build_result.memo, narrative=fallback),
            warnings=(
                CompanyResearchProcessWarning(
                    code=warning_code
                    or CompanyResearchProcessWarningCode.AI_NARRATIVE_VALIDATION_EXHAUSTED,
                ),
            ),
        )
