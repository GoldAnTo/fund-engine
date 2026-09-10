"""Thin HTTP boundary for high-level company research initialization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import (
    ConflictError as HttpConflictError,
)
from app.errors import (
    NotFoundError,
    ValidationFailedError,
)
from app.models.ledger import ConflictError as DomainConflictError
from app.models.ledger import ValidationError
from app.underwriting.api.company_research_schemas import (
    CompanyResearchAgendaModuleResponse,
    CompanyResearchArtifactResponse,
    CompanyResearchEvidenceReviewResponse,
    CompanyResearchFrozenArtifactDescriptorResponse,
    CompanyResearchFrozenAssessmentResponse,
    CompanyResearchFrozenMemoIdentityResponse,
    CompanyResearchFrozenRevisionResponse,
    CompanyResearchFrozenSecurityResponse,
    CompanyResearchIdentityResponse,
    CompanyResearchJudgmentConfirmationResponse,
    CompanyResearchMarkdownExportResponse,
    CompanyResearchPreparationErrorResponse,
    CompanyResearchPreparationResponse,
    CompanyResearchPreviewRequest,
    CompanyResearchPreviewResponse,
    CompanyResearchProjectResponse,
    CompanyResearchPublicationDraftResponse,
    CompanyResearchPublicationPreparationResponse,
    CompanyResearchPublicationPreviewResponse,
    CompanyResearchRunResponse,
    CompanyResearchSecurityIdentityResponse,
    CompanyResearchWorkbenchModuleResponse,
    CompanyResearchWorkspaceCompanyResponse,
    CompanyResearchWorkspaceDraftResponse,
    CompanyResearchWorkspacePreparationResponse,
    CompanyResearchWorkspaceResponse,
    ConfirmCompanyResearchJudgmentRequest,
    DecideCompanyResearchCriticalInputRequest,
    InitializeCompanyResearchRequest,
    PreviewCompanyResearchPublicationRequest,
    PublishCompanyResearchRequest,
    ReviewCompanyEvidenceRequest,
)
from app.underwriting.api.schemas import UnderwritingErrorEnvelope
from app.underwriting.api.transactions import commit_write
from app.underwriting.domain.company_research import CompanyResearchPreview
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CriticalInputDecision,
)
from app.underwriting.services.company_research_critical_input_confirmation import (
    CompanyResearchCriticalInputConfirmationService,
)
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitialization,
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
    CompanyResearchProjectStatus,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchFrozenRevision,
    CompanyResearchJudgmentConfirmation,
    CompanyResearchMarkdownExport,
    CompanyResearchPublicationPreview,
    CompanyResearchPublicationService,
)
from app.underwriting.services.company_research_run import (
    CompanyResearchRun,
    CompanyResearchRunService,
    authenticated_company_research_workspace,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
    CompanyResearchWorkspace,
    WorkbenchArtifact,
)

router = APIRouter(prefix="/product/company-research", tags=["company-research-v1"])
DbSession = Annotated[Session, Depends(get_db)]
WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
READ_ERROR_RESPONSES = {
    404: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_utc(value: datetime) -> datetime:
    """SQLite returns timezone columns as naive values; persisted values are UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _preview_response(value: CompanyResearchPreview) -> CompanyResearchPreviewResponse:
    return CompanyResearchPreviewResponse(
        company=CompanyResearchIdentityResponse(**value.company.canonical_payload()),
        securities=tuple(
            CompanyResearchSecurityIdentityResponse(
                object_id=security.object_id,
                external_key=security.external_key,
                canonical_name=security.canonical_name,
                symbol=security.symbol,
                exchange=security.exchange,
                share_class=security.share_class,
                trading_currency=security.trading_currency,
            )
            for security in value.securities
        ),
        strategy_version=value.strategy_version,
        horizon_years=value.horizon_years,
        base_currency=value.base_currency,
        required_return=value.required_return,
        permanent_loss_limit=value.permanent_loss_limit,
        cutoff_at=value.cutoff_at,
        focus_question=value.focus_question,
        agenda=tuple(
            CompanyResearchAgendaModuleResponse(**module.canonical_payload())
            for module in value.generic_modules
        ),
        preview_hash=value.input_hash,
    )


def _project_response(
    value: CompanyResearchProjectStatus,
) -> CompanyResearchProjectResponse:
    preparation = value.preparation
    return CompanyResearchProjectResponse(
        project_id=value.project.id,
        company_id=value.project.primary_company_id,
        preparation=CompanyResearchPreparationResponse(
            id=preparation.id,
            project_id=preparation.project_id,
            request_hash=preparation.request_hash,
            strategy_version=preparation.strategy_version,
            status=preparation.status,
            current_step=preparation.current_step,
            progress=preparation.progress,
            attempt=preparation.attempt,
            next_attempt_at=(
                _stored_utc(preparation.next_attempt_at)
                if preparation.next_attempt_at is not None
                else None
            ),
            last_error_code=preparation.last_error_code,
        ),
    )


def _initialization_response(
    value: CompanyResearchInitialization,
) -> CompanyResearchProjectResponse:
    return _project_response(
        CompanyResearchProjectStatus(
            project=value.project, preparation=value.preparation
        )
    )


def _external_source(ref: dict) -> dict:
    return {"kind": "external", **ref}


def _evidence_source(fact: dict) -> dict:
    if fact["value_kind"] == "derived":
        return {
            "kind": "evidence_derivation",
            "equation_id": fact["equation_id"],
            "parent_fact_keys": fact["parent_fact_keys"],
        }
    return _external_source(
        {
            "fact_key": fact["fact_key"],
            "source_role": fact["source_role"],
            "source_url": fact["source_url"],
            "source_locator": fact["source_locator"],
            "raw_hash": fact["raw_hash"],
        }
    )


def _exact_evidence_fact(evidence_facts: dict, ref: dict) -> dict:
    fact = evidence_facts.get(ref["fact_key"])
    if not isinstance(fact, dict) or any(
        fact.get(key) != ref.get(key)
        for key in (
            "fact_key",
            "source_role",
            "source_url",
            "source_locator",
            "raw_hash",
        )
    ):
        raise ValidationError("reported model fact provenance is ambiguous")
    return fact


def _computation_source(payload: dict, equation_id: str) -> dict:
    lineage = payload.get("_lineage") or {}
    return {
        "kind": "artifact_computation",
        "artifact_refs": lineage.get("artifact_refs", ()),
        "market_snapshot_ids": lineage.get("market_snapshot_ids", ()),
        "equation_id": equation_id,
    }


def _observation(
    *,
    key: str,
    value,
    unit: str,
    currency: str | None,
    period: str,
    state: str,
    source_ref: dict | None = None,
    gap_key: str | None = None,
    assumption_key: str | None = None,
) -> dict:
    return {
        "key": key,
        "value": str(value),
        "unit": unit,
        "currency": currency,
        "period": period,
        "state": state,
        "source_ref": source_ref,
        "gap_key": gap_key,
        "assumption_key": assumption_key,
    }


def _metric_metadata(key: str, value_currency: str) -> tuple[str, str]:
    lowered = key.lower()
    if "rate" in lowered or "margin" in lowered or "return" in lowered:
        return "ratio", "N/A"
    if "multiplier" in lowered or "multiple" in lowered:
        return "multiplier", "N/A"
    if "per_share" in lowered:
        return f"{value_currency}_per_share", value_currency
    return f"{value_currency}_million", value_currency


def _project_payload(value: WorkbenchArtifact, context: dict) -> dict:
    payload = value.payload
    kind = value.kind
    if kind == "memo":
        return {key: item for key, item in payload.items() if key != "research_gaps"}
    if kind == "evidence_index":
        facts = []
        for fact in payload["facts"]:
            projected = {
                key: item
                for key, item in fact.items()
                if key
                not in {
                    "value",
                    "value_kind",
                    "currency",
                    "unit",
                    "equation_id",
                    "parent_fact_keys",
                }
            }
            projected["observation"] = _observation(
                key=fact["metric_key"],
                value=fact["value"],
                unit=fact["unit"],
                currency=fact["currency"],
                period=f"{fact['period_start']}/{fact['period_end']}",
                state=fact["value_kind"],
                source_ref=_evidence_source(fact),
            )
            facts.append(projected)
        return {**payload, "facts": facts}
    if kind == "business_map" and "_lineage" in payload:
        evidence_facts = context.get("evidence_facts", {})
        modules = []
        for module in payload["modules"]:
            classified = []
            for item in module["classified_evidence"]:
                projected = {
                    key: entry
                    for key, entry in item.items()
                    if key not in {"value", "currency", "unit"}
                }
                fact = _exact_evidence_fact(evidence_facts, item["fact_ref"])
                projected["observation"] = _observation(
                    key=item["metric_key"],
                    value=item["value"],
                    unit=item["unit"],
                    currency=item["currency"],
                    period=f"{item['period_start']}/{item['period_end']}",
                    state=fact["value_kind"],
                    source_ref=_evidence_source(fact),
                )
                classified.append(projected)
            modules.append({**module, "classified_evidence": classified})
        return {**payload, "modules": modules}
    if kind == "driver_map":
        years = context.get("forecast_years", ())
        evidence_facts = context.get("evidence_facts", {})
        drivers = []
        for driver in payload["drivers"]:
            state = driver["input_state"]
            if state == "reported":
                if len(driver["values"]) != len(driver["fact_refs"]):
                    raise ValidationError(
                        "reported driver values require one exact fact each"
                    )
                values = []
                for item, source in zip(
                    driver["values"], driver["fact_refs"], strict=True
                ):
                    fact = _exact_evidence_fact(evidence_facts, source)
                    values.append(
                        _observation(
                            key=driver["output_metric"],
                            value=item,
                            unit=fact["unit"],
                            currency=fact["currency"],
                            period=f"{fact['period_start']}/{fact['period_end']}",
                            state=fact["value_kind"],
                            source_ref=_evidence_source(fact),
                        )
                    )
            elif state == "derived":
                provenance = {
                    "source_ref": _computation_source(
                        payload, driver["equation_id"] or driver["equation"]
                    )
                }
            else:
                provenance = {"assumption_key": driver["assumption_key"]}
            if state != "reported":
                if len(driver["values"]) != len(years):
                    raise ValidationError(
                        "model driver periods cannot be mapped exactly"
                    )
                unit, currency = _metric_metadata(
                    driver["output_metric"],
                    context.get("financial_currency", "N/A"),
                )
                values = [
                    _observation(
                        key=driver["output_metric"],
                        value=item,
                        unit=unit,
                        currency=currency,
                        period=f"FY{years[index]}",
                        state=state,
                        **provenance,
                    )
                    for index, item in enumerate(driver["values"])
                ]
            drivers.append(
                {
                    key: item
                    for key, item in driver.items()
                    if key not in {"input_state", "assumption_key", "values"}
                }
                | {"values": values}
            )
        return {**payload, "drivers": drivers}
    if kind == "financial_bridge":
        rows = []
        computation = _computation_source(payload, "financial_bridge.v1")
        driver_provenance = context.get("driver_provenance", {})
        for row in payload["rows"]:
            period = f"FY{row['fiscal_year']}"
            observations = {}
            for metric in (
                "revenue",
                "operating_income",
                "cash_tax_rate",
                "depreciation",
                "capex",
                "working_capital_change",
                "fcff",
            ):
                unit, currency = _metric_metadata(
                    metric, context.get("financial_currency", "N/A")
                )
                if metric in {"operating_income", "fcff"}:
                    state = "derived"
                    provenance = {"source_ref": computation}
                else:
                    driver = driver_provenance.get(metric)
                    if not isinstance(driver, dict):
                        raise ValidationError(
                            "financial observation requires exact driver provenance"
                        )
                    state = driver.get("input_state")
                    if state == "assumption":
                        provenance = {"assumption_key": driver.get("assumption_key")}
                    elif state == "reported" and driver.get("fact_refs"):
                        provenance = {
                            "source_ref": _external_source(driver["fact_refs"][0])
                        }
                    elif state == "derived":
                        provenance = {
                            "source_ref": _computation_source(
                                payload,
                                driver.get("equation_id")
                                or driver.get("equation")
                                or "financial_driver.v1",
                            )
                        }
                    else:
                        raise ValidationError(
                            "financial observation provenance is ambiguous"
                        )
                observations[metric] = _observation(
                    key=metric,
                    value=row[metric],
                    unit=unit,
                    currency=currency,
                    period=period,
                    state=state,
                    **provenance,
                )
            rows.append(
                {
                    "period": period,
                    **observations,
                    "fact_refs": row["fact_refs"],
                    "assumption_refs": row["assumption_refs"],
                }
            )
        return {**payload, "rows": rows}
    if kind == "scenario_set":
        years = context.get("forecast_years", ())
        period = f"FY{years[0]}/FY{years[-1]}" if years else "forecast_horizon"
        scenarios = []
        for scenario in payload["scenarios"]:
            overrides = []
            for override in scenario["driver_overrides"]:
                state = override.get("state")
                if state is None:
                    raise ValidationError(
                        "scenario override requires an explicit state"
                    )
                if state == "assumption":
                    provenance = {"assumption_key": override.get("assumption_key")}
                elif state == "derived":
                    provenance = {
                        "source_ref": _computation_source(
                            payload, override.get("equation") or "scenario_override.v1"
                        )
                    }
                else:
                    raise ValidationError(
                        "scenario override cannot claim reported provenance without a fact ref"
                    )
                overrides.append(
                    {
                        "driver_key": override["driver_key"],
                        "observation": _observation(
                            key=override["driver_key"],
                            value=override["value"],
                            unit="multiplier",
                            currency="N/A",
                            period=period,
                            state=state,
                            **provenance,
                        ),
                        "rationale": override.get("rationale"),
                        "equation": override.get("equation"),
                    }
                )
            scenarios.append({**scenario, "driver_overrides": overrides})
        return {**payload, "scenarios": scenarios}
    if kind == "valuation_set":
        period = f"as_of:{context.get('cutoff', 'unknown')}"
        computation = _computation_source(payload, "valuation_set.v1")
        financial_currency = context.get("financial_currency", "N/A")
        base_currency = context.get("base_currency", "CNY")

        def derived(key, item, unit=None, currency=None):
            resolved_currency = currency or financial_currency
            return _observation(
                key=key,
                value=item,
                unit=unit or f"{resolved_currency}_million",
                currency=resolved_currency,
                period=period,
                state="derived",
                source_ref=computation,
            )

        def value_range(item, prefix, unit, currency):
            return {
                "minimum": derived(
                    f"{prefix}_minimum", item["minimum"], unit, currency
                ),
                "maximum": derived(
                    f"{prefix}_maximum", item["maximum"], unit, currency
                ),
            }

        dcf = [
            {
                "scenario_id": item["scenario_id"],
                "enterprise_value": derived(
                    f"{item['scenario_id']}_enterprise_value",
                    item["enterprise_value"],
                ),
            }
            for item in payload["scenario_dcf_values"]
        ]
        reverse = payload["reverse_dcf"]
        if reverse is not None:
            reverse = {
                "driver_key": reverse["driver_key"],
                "implied_value": derived(
                    "reverse_dcf_implied_value",
                    reverse["implied_value"],
                    "multiplier",
                    "N/A",
                ),
                "achieved_residual": derived(
                    "reverse_dcf_residual", reverse["achieved_residual"]
                ),
                "iteration_count": derived(
                    "reverse_dcf_iteration_count",
                    reverse["iteration_count"],
                    "count",
                    "N/A",
                ),
            }
        ranges = []
        for item in payload["security_value_ranges"]:
            value_currency = item["value_currency"]
            ranges.append(
                {
                    "security_external_key": item["security_external_key"],
                    "value_per_share": value_range(
                        item["value_per_share"],
                        "value_per_share",
                        f"{value_currency}_per_share",
                        value_currency,
                    ),
                    "value_currency": value_currency,
                    "base_currency_return": value_range(
                        item["base_currency_return"],
                        "base_currency_return",
                        "ratio",
                        base_currency,
                    ),
                }
            )
        comparisons = []
        for item in payload["required_return_comparisons"]:
            comparisons.append(
                {
                    "security_external_key": item["security_external_key"],
                    "required_return": _observation(
                        key="required_return",
                        value=item["required_return"],
                        unit="ratio",
                        currency=base_currency,
                        period=period,
                        state="assumption",
                        assumption_key=f"{context['strategy_version']}:required_return",
                    ),
                    "achieved_return_range": value_range(
                        item["achieved_return_range"],
                        "achieved_return",
                        "ratio",
                        base_currency,
                    ),
                    "meets_required_return": item["meets_required_return"],
                }
            )
        sensitivities = []
        for item in payload.get("sensitivity_analyses", ()):
            value_currency = item["value_currency"]
            sensitivity_values = [
                {
                    "security_external_key": value["security_external_key"],
                    "low_input_value_per_share": derived(
                        f"{item['variable_key']}_low_value_per_share",
                        value["low_input_value_per_share"],
                        f"{value_currency}_per_share",
                        value_currency,
                    ),
                    "high_input_value_per_share": derived(
                        f"{item['variable_key']}_high_value_per_share",
                        value["high_input_value_per_share"],
                        f"{value_currency}_per_share",
                        value_currency,
                    ),
                }
                for value in item["security_values"]
            ]
            sensitivities.append(
                {
                    "variable_key": item["variable_key"],
                    "low_input": _observation(
                        key=f"{item['variable_key']}_low_input",
                        value=item["low_input"],
                        unit="ratio",
                        currency="N/A",
                        period=period,
                        state="assumption",
                        assumption_key=item["low_assumption_key"],
                    ),
                    "high_input": _observation(
                        key=f"{item['variable_key']}_high_input",
                        value=item["high_input"],
                        unit="ratio",
                        currency="N/A",
                        period=period,
                        state="assumption",
                        assumption_key=item["high_assumption_key"],
                    ),
                    "security_values": sensitivity_values,
                    "value_currency": value_currency,
                    "equation_id": item["equation_id"],
                }
            )
        return {
            **payload,
            "scenario_dcf_values": dcf,
            "reverse_dcf": reverse,
            "security_value_ranges": ranges,
            "required_return": _observation(
                key="required_return",
                value=payload["required_return"],
                unit="ratio",
                currency=base_currency,
                period=period,
                state="assumption",
                assumption_key=f"{context['strategy_version']}:required_return",
            ),
            "required_return_comparisons": comparisons,
            "sensitivity_analyses": sensitivities,
        }
    return payload


def _artifact_response(
    value: WorkbenchArtifact,
    *,
    project_id: UUID,
    context: dict | None = None,
) -> CompanyResearchArtifactResponse:
    context = context or {}
    return CompanyResearchArtifactResponse.model_validate(
        {
            "id": value.id,
            "project_id": project_id,
            "kind": value.kind,
            "version": value.version,
            "input_hash": value.input_hash,
            "content_hash": value.content_hash,
            "payload": _project_payload(value, context),
            "source_refs": value.source_refs,
        }
    )


def _workspace_response(
    value, *, expected_project_id: UUID
) -> CompanyResearchWorkspaceResponse:
    if value.project_id != expected_project_id:
        raise ValidationError("company research workspace project identity mismatch")
    raw_by_kind = {item.kind: item for item in value.artifacts}
    evidence = raw_by_kind.get("evidence_index")
    financial = raw_by_kind.get("financial_bridge")
    driver_map = raw_by_kind.get("driver_map")
    valuation = raw_by_kind.get("valuation_set")
    valuation_ranges = (
        valuation.payload.get("security_value_ranges", ()) if valuation else ()
    )
    evidence_currencies = (
        {
            fact.get("currency")
            for fact in evidence.payload.get("facts", ())
            if isinstance(fact, dict) and isinstance(fact.get("currency"), str)
        }
        if evidence
        else set()
    )
    financial_currency = (
        valuation_ranges[0].get("value_currency")
        if valuation_ranges and isinstance(valuation_ranges[0], dict)
        else next(iter(evidence_currencies), "N/A")
    )
    context = {
        "cutoff": evidence.payload.get("cutoff") if evidence else None,
        "strategy_version": value.preparation.strategy_version,
        "financial_currency": financial_currency,
        "base_currency": "CNY",
        "forecast_years": tuple(
            row["fiscal_year"] for row in financial.payload.get("rows", ())
        )
        if financial
        else (),
        "driver_provenance": {
            driver["driver_key"]: driver
            for driver in driver_map.payload.get("drivers", ())
        }
        if driver_map
        else {},
        "evidence_facts": {
            fact["fact_key"]: fact for fact in evidence.payload.get("facts", ())
        }
        if evidence
        else {},
    }
    artifacts = tuple(
        _artifact_response(item, project_id=value.project_id, context=context)
        for item in value.artifacts
    )
    return CompanyResearchWorkspaceResponse(
        project_id=value.project_id,
        company=CompanyResearchWorkspaceCompanyResponse(
            id=value.company.id,
            object_id=value.company.id,
            external_key=value.company.external_key,
            canonical_name=value.company.canonical_name,
        ),
        preparation=CompanyResearchWorkspacePreparationResponse(
            id=value.preparation.id,
            status=value.preparation.status,
            current_step=value.preparation.current_step,
            progress=value.preparation.progress,
            error=(
                CompanyResearchPreparationErrorResponse(
                    code=value.preparation.error.code,
                    failed_step=value.preparation.error.failed_step,
                    retryable=value.preparation.error.retryable,
                    next_attempt_at=(
                        _stored_utc(value.preparation.error.next_attempt_at)
                        if value.preparation.error.next_attempt_at is not None
                        else None
                    ),
                )
                if value.preparation.error is not None
                else None
            ),
        ),
        artifacts=artifacts,
        modules=tuple(
            CompanyResearchWorkbenchModuleResponse(
                key=item.key,
                state=item.state,
                artifact_refs=tuple(
                    {
                        "id": artifact.id,
                        "kind": artifact.kind,
                        "content_hash": artifact.content_hash,
                    }
                    for artifact in item.artifacts
                ),
                valuation_state=item.valuation_state,
            )
            for item in value.modules
        ),
        source_count=value.source_count,
        gap_count=value.gap_count,
        draft=CompanyResearchWorkspaceDraftResponse(
            id=value.draft.id,
            lock_version=value.draft.lock_version,
            base_revision_id=value.draft.base_revision_id,
        ),
        selected_revision=value.selected_revision,
        change_summary=value.change_summary,
    )


def _read(operation):
    try:
        return operation()
    except DomainConflictError as exc:
        raise HttpConflictError(str(exc)) from exc
    except ValidationError as exc:
        message = str(exc)
        if message in {
            "company research project not found",
            "company research preparation not found",
        }:
            raise NotFoundError(message) from exc
        raise ValidationFailedError(message) from exc


def _publication_security_response(value) -> CompanyResearchFrozenSecurityResponse:
    return CompanyResearchFrozenSecurityResponse(
        object_id=value.object_id,
        company_id=value.company_id,
        external_key=value.external_key,
        canonical_name=value.canonical_name,
        symbol=value.symbol,
        exchange=value.exchange,
        share_class=value.share_class,
        trading_currency=value.trading_currency,
    )


def _publication_assessment_response(value) -> CompanyResearchFrozenAssessmentResponse:
    return CompanyResearchFrozenAssessmentResponse(
        answerability=value.answerability,
        direction=value.direction,
        confidence=value.confidence,
        content_hash=value.content_hash,
    )


def _publication_artifact_responses(
    values,
) -> tuple[CompanyResearchFrozenArtifactDescriptorResponse, ...]:
    return tuple(
        CompanyResearchFrozenArtifactDescriptorResponse(
            kind=value.kind,
            id=value.id,
            version=value.version,
            input_hash=value.input_hash,
            content_hash=value.content_hash,
        )
        for value in values
    )


def _judgment_confirmation_response(
    value: CompanyResearchJudgmentConfirmation,
) -> CompanyResearchJudgmentConfirmationResponse:
    return CompanyResearchJudgmentConfirmationResponse(
        project_id=value.project_id,
        preparation=CompanyResearchPublicationPreparationResponse(
            id=value.preparation_id,
            status="ready_to_freeze",
            current_step="memo",
            progress=95,
        ),
        draft=CompanyResearchPublicationDraftResponse(
            id=value.draft_id,
            lock_version=value.draft_lock_version,
        ),
        machine_memo=CompanyResearchFrozenMemoIdentityResponse(
            id=value.machine_memo_id,
            content_hash=value.machine_memo_content_hash,
        ),
        confirmed_memo=CompanyResearchFrozenMemoIdentityResponse(
            id=value.confirmed_memo_id,
            content_hash=value.confirmed_memo_content_hash,
        ),
        assessment_status=value.assessment_status,
        reviewer=value.reviewer,
        markdown=value.markdown,
        confirmed_at=_stored_utc(value.confirmed_at),
    )


def _publication_preview_response(
    value: CompanyResearchPublicationPreview,
) -> CompanyResearchPublicationPreviewResponse:
    return CompanyResearchPublicationPreviewResponse(
        project_id=value.project_id,
        expected_lock_version=value.expected_lock_version,
        company=CompanyResearchIdentityResponse(**value.company.canonical_payload()),
        securities=tuple(
            _publication_security_response(item) for item in value.securities
        ),
        cutoff_at=_stored_utc(value.cutoff_at),
        historical_basis_id=value.historical_basis_id,
        historical_basis_content_hash=value.historical_basis_content_hash,
        strategy_version=value.strategy_version,
        model_version=value.model_version,
        assessment=_publication_assessment_response(value.assessment),
        value_range=value.value_range,
        return_range=value.return_range,
        blockers=value.blockers,
        strongest_counterevidence=value.strongest_counterevidence,
        next_verification_events=value.next_verification_events,
        memo_markdown=value.memo_markdown,
        artifacts=_publication_artifact_responses(value.artifacts),
        manifest_hash=value.manifest_hash,
    )


def _frozen_revision_response(
    value: CompanyResearchFrozenRevision,
) -> CompanyResearchFrozenRevisionResponse:
    return CompanyResearchFrozenRevisionResponse(
        id=value.id,
        project_id=value.project_id,
        sequence=value.sequence,
        published_at=_stored_utc(value.published_at),
        boundary_id=value.boundary_id,
        manifest_id=value.manifest_id,
        manifest_hash=value.manifest_hash,
        company=CompanyResearchIdentityResponse(**value.company.canonical_payload()),
        securities=tuple(
            _publication_security_response(item) for item in value.securities
        ),
        cutoff_at=_stored_utc(value.cutoff_at),
        historical_basis_id=value.historical_basis_id,
        historical_basis_content_hash=value.historical_basis_content_hash,
        strategy_version=value.strategy_version,
        model_version=value.model_version,
        assessment=_publication_assessment_response(value.assessment),
        value_range=value.value_range,
        return_range=value.return_range,
        blockers=value.blockers,
        strongest_counterevidence=value.strongest_counterevidence,
        next_verification_events=value.next_verification_events,
        memo_markdown=value.memo_markdown,
        artifacts=_publication_artifact_responses(value.artifacts),
        preparation_status=value.preparation_status,
        current_step=value.current_step,
        progress=value.progress,
    )


def _export_response(
    value: CompanyResearchMarkdownExport,
) -> CompanyResearchMarkdownExportResponse:
    return CompanyResearchMarkdownExportResponse(
        filename=value.filename,
        media_type=value.media_type,
        content=value.content,
        content_hash=value.content_hash,
    )


def _require_company_research_revision(
    db: Session, *, project_id: UUID, revision_id: UUID
) -> None:
    from app.underwriting.persistence.models import UnderwritingResearchVersion

    row = db.get(UnderwritingResearchVersion, revision_id)
    if (
        row is None
        or row.project_id != project_id
        or row.version_kind != "company_research"
    ):
        raise NotFoundError("company research revision not found")


def _authenticated_workspace(
    db: Session, *, project_id: UUID
) -> CompanyResearchWorkspace:
    return authenticated_company_research_workspace(
        db, project_id=project_id, now=_now
    ).workspace


def _run_response(value: CompanyResearchRun) -> CompanyResearchRunResponse:
    critical_inputs = None
    if value.critical_inputs is not None:
        payload = CompanyResearchArtifactCodec.encode(
            "critical_inputs", value.critical_inputs.value
        )
        critical_inputs = {
            "artifact_id": value.critical_inputs.artifact_id,
            "version": value.critical_inputs.version,
            "input_hash": value.critical_inputs.input_hash,
            "content_hash": value.critical_inputs.content_hash,
            "inputs": payload["inputs"],
        }
    return CompanyResearchRunResponse.model_validate(
        {
            "project_id": value.project_id,
            "company": value.company.canonical_payload(),
            "securities": tuple(
                {
                    "object_id": item.object_id,
                    "external_key": item.external_key,
                    "canonical_name": item.canonical_name,
                    "symbol": item.symbol,
                    "exchange": item.exchange,
                    "share_class": item.share_class,
                    "trading_currency": item.trading_currency,
                }
                for item in value.securities
            ),
            "status": value.status.value,
            "progress": value.progress,
            "started_at": value.started_at,
            "updated_at": value.updated_at,
            "stages": tuple(
                {"key": item.key, "status": item.status.value} for item in value.stages
            ),
            "recent_process": tuple(
                {
                    "code": item.code,
                    "message": item.message,
                    "occurred_at": item.occurred_at,
                    "retry": (
                        {
                            "retryable": item.retry.retryable,
                            "next_attempt_at": item.retry.next_attempt_at,
                        }
                        if item.retry is not None
                        else None
                    ),
                }
                for item in value.recent_process
            ),
            "workspace": _workspace_response(
                value.workspace, expected_project_id=value.project_id
            ),
            "critical_inputs": critical_inputs,
            "selected_revision": value.selected_revision,
        }
    )


@router.post(
    "/preview",
    response_model=CompanyResearchPreviewResponse,
    responses=READ_ERROR_RESPONSES,
)
def preview_company_research(
    payload: CompanyResearchPreviewRequest, db: DbSession
) -> CompanyResearchPreviewResponse:
    return _preview_response(
        _read(
            lambda: CompanyResearchInitializer(db, now=_now).preview(
                company_id=payload.company_id,
                cutoff_at=payload.cutoff_at,
                focus_question=payload.focus_question,
            )
        )
    )


@router.post(
    "/initializations",
    response_model=CompanyResearchProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def initialize_company_research(
    payload: InitializeCompanyResearchRequest,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
    db: DbSession,
) -> CompanyResearchProjectResponse:
    return _initialization_response(
        commit_write(
            db,
            lambda: CompanyResearchInitializer(db, now=_now).initialize(
                preview_hash=payload.preview_hash,
                company_id=payload.company_id,
                cutoff_at=payload.cutoff_at,
                focus_question=payload.focus_question,
                idempotency_key=idempotency_key,
            ),
        )
    )


@router.get(
    "/projects/{project_id}",
    response_model=CompanyResearchProjectResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_project(
    project_id: UUID, db: DbSession
) -> CompanyResearchProjectResponse:
    return _project_response(
        _read(
            lambda: CompanyResearchPreparationService(db, now=_now).status(
                project_id=project_id
            )
        )
    )


@router.post(
    "/projects/{project_id}/retry",
    response_model=CompanyResearchProjectResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def retry_company_research_project(
    project_id: UUID, db: DbSession
) -> CompanyResearchProjectResponse:
    return _project_response(
        commit_write(
            db,
            lambda: _read(
                lambda: CompanyResearchPreparationService(db, now=_now).retry(
                    project_id=project_id
                )
            ),
        )
    )


@router.get(
    "/projects/{project_id}/run",
    response_model=CompanyResearchRunResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_run(
    project_id: UUID,
    db: DbSession,
    include_process: Literal["true", "false"] = "false",
) -> CompanyResearchRunResponse:
    return _run_response(
        _read(
            lambda: CompanyResearchRunService(db, now=_now).read(
                project_id, include_process=include_process == "true"
            )
        )
    )


@router.post(
    "/projects/{project_id}/critical-input-decisions",
    response_model=CompanyResearchRunResponse,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def decide_company_research_critical_input(
    project_id: UUID,
    payload: DecideCompanyResearchCriticalInputRequest,
    db: DbSession,
) -> CompanyResearchRunResponse:
    value = commit_write(
        db,
        lambda: _read(
            lambda: CompanyResearchCriticalInputConfirmationService(
                db, now=_now
            ).decide(
                project_id=project_id,
                critical_input_key=payload.critical_input_key,
                expected_artifact_id=payload.expected_artifact_id,
                expected_input_fingerprint=payload.expected_input_fingerprint,
                decision=CriticalInputDecision(payload.decision),
                replacement_value=payload.replacement_value,
                replacement_unit=payload.replacement_unit,
                replacement_rationale=payload.replacement_rationale,
            )
        ),
    )
    return _run_response(value)


@router.get(
    "/projects/{project_id}/workspace",
    response_model=CompanyResearchWorkspaceResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_workspace(
    project_id: UUID, db: DbSession
) -> CompanyResearchWorkspaceResponse:
    return _read(
        lambda: _workspace_response(
            _authenticated_workspace(db, project_id=project_id),
            expected_project_id=project_id,
        )
    )


@router.post(
    "/projects/{project_id}/evidence-reviews",
    response_model=CompanyResearchEvidenceReviewResponse,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def review_company_evidence(
    project_id: UUID, payload: ReviewCompanyEvidenceRequest, db: DbSession
) -> CompanyResearchEvidenceReviewResponse:
    result = commit_write(
        db,
        lambda: _read(
            lambda: CompanyResearchWorkbench(db, now=_now).review_evidence(
                project_id=project_id,
                evidence_artifact_id=payload.evidence_artifact_id,
                fact_key=payload.fact_key,
                decision=payload.decision,
                expected_head_id=payload.expected_head_id,
            )
        ),
    )
    artifact = _artifact_response(result.evidence_artifact, project_id=project_id).root
    if artifact.kind != "evidence_index":
        raise ValidationFailedError("evidence review returned the wrong artifact kind")
    return CompanyResearchEvidenceReviewResponse(evidence_artifact=artifact)


@router.post(
    "/projects/{project_id}/judgment-confirmations",
    response_model=CompanyResearchJudgmentConfirmationResponse,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def confirm_company_research_judgment(
    project_id: UUID,
    payload: ConfirmCompanyResearchJudgmentRequest,
    db: DbSession,
) -> CompanyResearchJudgmentConfirmationResponse:
    value = commit_write(
        db,
        lambda: _read(
            lambda: CompanyResearchPublicationService(db, now=_now).confirm_judgment(
                project_id=project_id,
                expected_lock_version=payload.expected_lock_version,
                expected_memo_id=payload.expected_memo_id,
                expected_memo_content_hash=payload.expected_memo_content_hash,
                markdown=payload.markdown,
            )
        ),
    )
    return _judgment_confirmation_response(value)


@router.post(
    "/projects/{project_id}/publication-preview",
    response_model=CompanyResearchPublicationPreviewResponse,
    responses={**READ_ERROR_RESPONSES, 409: {"model": UnderwritingErrorEnvelope}},
)
def preview_company_research_publication(
    project_id: UUID,
    payload: PreviewCompanyResearchPublicationRequest,
    db: DbSession,
) -> CompanyResearchPublicationPreviewResponse:
    return _publication_preview_response(
        _read(
            lambda: CompanyResearchPublicationService(db, now=_now).preview(
                project_id=project_id,
                expected_lock_version=payload.expected_lock_version,
            )
        )
    )


@router.post(
    "/projects/{project_id}/publish",
    response_model=CompanyResearchFrozenRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def publish_company_research(
    project_id: UUID,
    payload: PublishCompanyResearchRequest,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=120)
    ],
    db: DbSession,
) -> CompanyResearchFrozenRevisionResponse:
    value = commit_write(
        db,
        lambda: _read(
            lambda: CompanyResearchPublicationService(db, now=_now).publish(
                project_id=project_id,
                expected_lock_version=payload.expected_lock_version,
                expected_manifest_hash=payload.expected_manifest_hash,
                idempotency_key=idempotency_key,
            )
        ),
    )
    return _frozen_revision_response(value)


@router.get(
    "/projects/{project_id}/revisions/{revision_id}",
    response_model=CompanyResearchFrozenRevisionResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_revision(
    project_id: UUID, revision_id: UUID, db: DbSession
) -> CompanyResearchFrozenRevisionResponse:
    _require_company_research_revision(
        db, project_id=project_id, revision_id=revision_id
    )
    return _frozen_revision_response(
        _read(
            lambda: CompanyResearchPublicationService(db, now=_now).revision(
                project_id, revision_id
            )
        )
    )


@router.get(
    "/projects/{project_id}/revisions/{revision_id}/export",
    response_model=CompanyResearchMarkdownExportResponse,
    responses=READ_ERROR_RESPONSES,
)
def export_company_research_revision(
    project_id: UUID, revision_id: UUID, db: DbSession
) -> CompanyResearchMarkdownExportResponse:
    _require_company_research_revision(
        db, project_id=project_id, revision_id=revision_id
    )
    return _export_response(
        _read(
            lambda: CompanyResearchPublicationService(db, now=_now).export(
                project_id, revision_id
            )
        )
    )
