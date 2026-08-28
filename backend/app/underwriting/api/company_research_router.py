"""Thin HTTP boundary for high-level company research initialization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import ValidationError
from app.underwriting.api.company_research_schemas import (
    CompanyResearchAgendaModuleResponse,
    CompanyResearchArtifactResponse,
    CompanyResearchEvidenceReviewResponse,
    CompanyResearchIdentityResponse,
    CompanyResearchPreparationResponse,
    CompanyResearchPreviewRequest,
    CompanyResearchPreviewResponse,
    CompanyResearchProjectResponse,
    CompanyResearchSecurityIdentityResponse,
    CompanyResearchWorkbenchModuleResponse,
    CompanyResearchWorkspaceCompanyResponse,
    CompanyResearchWorkspaceDraftResponse,
    CompanyResearchWorkspacePreparationResponse,
    CompanyResearchPreparationErrorResponse,
    CompanyResearchWorkspaceResponse,
    InitializeCompanyResearchRequest,
    ReviewCompanyEvidenceRequest,
)
from app.underwriting.api.schemas import UnderwritingErrorEnvelope
from app.underwriting.api.transactions import commit_write
from app.underwriting.domain.company_research import CompanyResearchPreview
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitialization,
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
    CompanyResearchProjectStatus,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
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
    currency: str,
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


def _metric_metadata(key: str) -> tuple[str, str]:
    lowered = key.lower()
    if "rate" in lowered or "margin" in lowered or "return" in lowered:
        return "ratio", "N/A"
    if "multiplier" in lowered or "multiple" in lowered:
        return "multiplier", "N/A"
    if "per_share" in lowered:
        return "USD_per_share", "USD"
    return "USD_million", "USD"


def _project_payload(value: WorkbenchArtifact, context: dict) -> dict:
    payload = value.payload
    kind = value.kind
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
                }
            }
            source = {
                "fact_key": fact["fact_key"],
                "source_role": fact["source_role"],
                "source_url": fact["source_url"],
                "source_locator": fact["source_locator"],
                "raw_hash": fact["raw_hash"],
            }
            projected["observation"] = _observation(
                key=fact["metric_key"],
                value=fact["value"],
                unit=fact["unit"],
                currency=fact["currency"],
                period=f'{fact["period_start"]}/{fact["period_end"]}',
                state=fact["value_kind"],
                source_ref=_external_source(source),
            )
            facts.append(projected)
        return {**payload, "facts": facts}
    if kind == "business_map" and "_lineage" in payload:
        modules = []
        for module in payload["modules"]:
            classified = []
            for item in module["classified_evidence"]:
                projected = {
                    key: entry
                    for key, entry in item.items()
                    if key not in {"value", "currency", "unit"}
                }
                projected["observation"] = _observation(
                    key=item["metric_key"],
                    value=item["value"],
                    unit=item["unit"],
                    currency=item["currency"],
                    period=f'{item["period_start"]}/{item["period_end"]}',
                    state="reported",
                    source_ref=_external_source(item["fact_ref"]),
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
                    fact = evidence_facts.get(source["fact_key"])
                    if not isinstance(fact, dict) or any(
                        fact.get(key) != source.get(key)
                        for key in (
                            "fact_key",
                            "source_role",
                            "source_url",
                            "source_locator",
                            "raw_hash",
                        )
                    ):
                        raise ValidationError(
                            "reported driver fact provenance is ambiguous"
                        )
                    values.append(
                        _observation(
                            key=driver["output_metric"],
                            value=item,
                            unit=fact["unit"],
                            currency=fact["currency"],
                            period=f'{fact["period_start"]}/{fact["period_end"]}',
                            state=state,
                            source_ref=_external_source(source),
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
                unit, currency = _metric_metadata(driver["output_metric"])
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
            period = f'FY{row["fiscal_year"]}'
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
                unit, currency = _metric_metadata(metric)
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
        period = (
            f"FY{years[0]}/FY{years[-1]}" if years else "forecast_horizon"
        )
        scenarios = []
        for scenario in payload["scenarios"]:
            overrides = []
            for override in scenario["driver_overrides"]:
                state = override.get("state")
                if state is None:
                    raise ValidationError("scenario override requires an explicit state")
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
        period = f'as_of:{context.get("cutoff", "unknown")}'
        computation = _computation_source(payload, "valuation_set.v1")

        def derived(key, item, unit="USD_million", currency="USD"):
            return _observation(
                key=key,
                value=item,
                unit=unit,
                currency=currency,
                period=period,
                state="derived",
                source_ref=computation,
            )

        def value_range(item, prefix, unit, currency):
            return {
                "minimum": derived(f"{prefix}_minimum", item["minimum"], unit, currency),
                "maximum": derived(f"{prefix}_maximum", item["maximum"], unit, currency),
            }

        dcf = [
            {
                "scenario_id": item["scenario_id"],
                "enterprise_value": derived(
                    f'{item["scenario_id"]}_enterprise_value',
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
                    "reverse_dcf_implied_value", reverse["implied_value"], "multiplier", "N/A"
                ),
                "achieved_residual": derived(
                    "reverse_dcf_residual", reverse["achieved_residual"]
                ),
                "iteration_count": derived(
                    "reverse_dcf_iteration_count", reverse["iteration_count"], "count", "N/A"
                ),
            }
        ranges = []
        for item in payload["security_value_ranges"]:
            ranges.append(
                {
                    "security_external_key": item["security_external_key"],
                    "usd_per_share": value_range(
                        item["usd_per_share"], "usd_per_share", "USD_per_share", "USD"
                    ),
                    "cny_return": value_range(item["cny_return"], "cny_return", "ratio", "CNY"),
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
                        currency="CNY",
                        period=period,
                        state="assumption",
                        assumption_key=f'{context["strategy_version"]}:required_return',
                    ),
                    "achieved_return_range": value_range(
                        item["achieved_return_range"], "achieved_return", "ratio", "CNY"
                    ),
                    "meets_required_return": item["meets_required_return"],
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
                currency="CNY",
                period=period,
                state="assumption",
                assumption_key=f'{context["strategy_version"]}:required_return',
            ),
            "required_return_comparisons": comparisons,
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
    context = {
        "cutoff": evidence.payload.get("cutoff") if evidence else None,
        "strategy_version": value.preparation.strategy_version,
        "forecast_years": tuple(
            row["fiscal_year"] for row in financial.payload.get("rows", ())
        ) if financial else (),
        "driver_provenance": {
            driver["driver_key"]: driver
            for driver in driver_map.payload.get("drivers", ())
        } if driver_map else {},
        "evidence_facts": {
            fact["fact_key"]: fact
            for fact in evidence.payload.get("facts", ())
        } if evidence else {},
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
    except ValidationError as exc:
        message = str(exc)
        if message in {
            "company research project not found",
            "company research preparation not found",
        }:
            raise NotFoundError(message) from exc
        raise ValidationFailedError(message) from exc


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
                company_id=payload.company_id, cutoff_at=payload.cutoff_at
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
    "/projects/{project_id}/workspace",
    response_model=CompanyResearchWorkspaceResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_workspace(
    project_id: UUID, db: DbSession
) -> CompanyResearchWorkspaceResponse:
    return _read(
        lambda: _workspace_response(
            CompanyResearchWorkbench(db, now=_now).workspace(project_id=project_id),
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
    artifact = _artifact_response(
        result.evidence_artifact, project_id=project_id
    ).root
    if artifact.kind != "evidence_index":
        raise ValidationFailedError("evidence review returned the wrong artifact kind")
    return CompanyResearchEvidenceReviewResponse(evidence_artifact=artifact)
