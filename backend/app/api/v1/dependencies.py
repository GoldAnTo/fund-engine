"""Central FastAPI dependencies for Case-scoped authorization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.errors import ValidationFailedError
from app.services.case_authorization import CaseAuthorizationService, CasePermission


# Every public operation with a literal ``{case_id}`` path is classified here.
# Collection routes and routes that resolve a Case through another resource ID
# are enforced separately because they do not expose a Case path parameter.
CASE_ROUTE_PERMISSIONS: Final[dict[str, CasePermission]] = {
    # view (28)
    "workbench": "view",
    "dossier": "view",
    "gaps": "view",
    "conclusion": "view",
    "compare_case": "view",
    "case_snapshots": "view",
    "graph": "view",
    "case_fund_exposure": "view",
    "list_atomic_claims": "view",
    "list_runs": "view",
    "event_case_documents": "view",
    "event_case_document_detail": "view",
    "event_research_relations": "view",
    "event_research_workbench": "view",
    "event_conclusion_history": "view",
    "event_scope_history": "view",
    "event_review_queue": "view",
    "get_monitor": "view",
    "get_market_expression": "view",
    "admitted_source_statements": "view",
    "key_factor_candidate_runs": "view",
    "market_instruments": "view",
    "case_mechanism_protocol": "view",
    "get_research_workflow": "view",
    "get_research_workflow_events": "view",
    "get_research_workflow_ledger": "view",
    "case_runtime_status": "view",
    "get_fund_disclosure_sync": "view",
    "get_forecast_verdicts": "view",
    # edit (19)
    "add_thesis": "edit",
    "update_theme_tags": "edit",
    "propose_atomic_claim": "edit",
    "start_run": "edit",
    "update_event_research_scope": "edit",
    "attach_event_material": "edit",
    "upload_event_material": "edit",
    "save_monitor": "edit",
    "start_manual_monitor_run": "edit",
    "start_factor_monitor_run": "edit",
    "parse_key_factor_candidates": "edit",
    "register_market_instrument_binding": "edit",
    "create_verification_rule": "edit",
    "save_fund_disclosure_sync_config": "edit",
    "start_fund_disclosure_sync": "edit",
    "retry_fund_disclosure_sync": "edit",
    "create_forecast_target": "edit",
    "record_actual_metric_observation": "edit",
    "create_acquisition_job": "edit",
    # review (14): authoritative research judgments and workflow decisions.
    "publish_event_conclusion": "review",
    "continue_event_research": "review",
    "decide_published_material": "review",
    "decide_published_uploaded_material": "review",
    "set_monitor_status": "review",
    "register_fundamental_impact": "review",
    "register_market_observation": "review",
    "register_report_claim": "review",
    "register_key_factor": "review",
    "register_claim_verification": "review",
    "select_mechanism_template": "review",
    "confirm_research_workflow": "review",
    "decide_research_workflow": "review",
    "resume_protocol_workflow": "review",
    # admin (5)
    "admit_legacy_event_case": "admin",
    "list_case_access_grants": "admin",
    "grant_case_access": "admin",
    "change_case_access_role": "admin",
    "revoke_case_access": "admin",
}

# Routes below resolve their Case through a query/body field or another
# resource (run, job, thesis, document, proposal, task, binding, candidate).
# Their handlers enforce this declaration after resolving that resource; the
# inventory test ensures every declared operation remains present and rejects
# unauthenticated input before handler validation.
INDIRECT_CASE_ROUTE_PERMISSIONS: Final[dict[str, CasePermission]] = {
    "list_cases": "view",
    "list_event_research": "view",
    "event_research_network": "view",
    "overview": "view",
    "knowledge_layer": "view",
    "research_ops_kpis": "view",
    "list_themes": "view",
    "theme_view": "view",
    "list_companies": "view",
    "company_dossier": "view",
    "list_documents": "view",
    "document_detail": "view",
    "search": "view",
    "get_activity": "view",
    "get_evidence_changes": "view",
    "get_tasks": "view",
    "create_task": "edit",
    "update_task": "edit",
    "get_job": "view",
    "get_job_events": "view",
    "cancel_job": "edit",
    "retry_job": "edit",
    "list_active_runs": "view",
    "list_run_archive": "view",
    "get_run": "view",
    "get_run_events": "view",
    "cancel_run": "edit",
    "acquisition_job_detail": "view",
    "acquisition_job_events": "view",
    "acquisition_job_evidence": "view",
    "acquisition_job_exceptions": "view",
    "review_atomic_claim": "review",
    "evaluate_forecast_target": "edit",
    "create_forecast_verdict": "review",
    "create_binding": "edit",
    "approve_binding": "review",
    "researchability": "view",
    "list_active_fund_disclosure_sync_runs": "view",
    "review_case_relation": "review",
    "create_causal_step": "edit",
    "create_causal_edge": "edit",
    "create_document_supplement": "edit",
    "rerun_assessment": "edit",
    "propose_evidence": "edit",
    "extract_statements": "edit",
    "ingest_documents": "edit",
    "create_theme_role": "edit",
    "list_proposals": "review",
    "claim_proposal": "review",
    "decide_proposal": "review",
    "review_queue": "review",
    "review_link": "review",
    "review_assessment": "review",
    "legacy_case_admission_queue": "admin",
}

# Operations that intentionally do not resolve an existing Case.  Keeping
# this allow-list beside both Case policy tables makes the route inventory
# exhaustive: every new API operation must choose one of the three classes.
UNSCOPED_CASE_ROUTE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "health_v1",
        "create_case",
        "fund_composition",
        "create_company",
        "metric_catalog",
        "metric_series",
        "create_stock",
        "create_fund",
        "create_holding_disclosure",
        "create_valuation_snapshot",
        "worker_status",
        "create_event_research",
        "extract_event",
        "market_instrument_catalog",
        "list_metrics",
        "create_metric",
        "list_mechanism_templates",
        "research_session",
        "runtime_status",
    }
)


@dataclass(frozen=True, slots=True)
class CaseRoutePolicy:
    """Permission declared for the currently matched API operation."""

    actor: ResearchActor
    authorization: CaseAuthorizationService
    permission: CasePermission | None

    def require(self, case_id: uuid.UUID):
        if self.permission is None:
            raise ValidationFailedError("unscoped route cannot authorize a Case")
        return self.authorization.require(case_id, self.actor, self.permission)

    def require_locked(self, case_id: uuid.UUID):
        if self.permission is None:
            raise ValidationFailedError("unscoped route cannot authorize a Case")
        return self.authorization.require_locked(
            case_id,
            self.actor,
            self.permission,
        )

    def authorized_case_ids(self):
        if self.permission is None:
            raise ValidationFailedError("unscoped route has no Case collection")
        return self.authorization.authorized_case_ids(
            self.actor,
            self.permission,
        )


def require_case_route_permission(
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> CaseRoutePolicy:
    """Authorize a route whose matched path contains a concrete Case ID.

    The dependency is attached only to routers that expose Case resources.
    Routes in those routers without a literal ``case_id`` remain authenticated
    but are filtered through authorized Case IDs by their collection boundary.
    """
    route = request.scope.get("route")
    operation_name = getattr(route, "name", None)
    raw_case_id = request.path_params.get("case_id")
    if raw_case_id is not None:
        permission = CASE_ROUTE_PERMISSIONS.get(operation_name)
    else:
        permission = INDIRECT_CASE_ROUTE_PERMISSIONS.get(operation_name)
    if permission is None and operation_name not in UNSCOPED_CASE_ROUTE_OPERATIONS:
        raise ValidationFailedError(
            "Case route is missing an authorization policy declaration"
        )
    policy = CaseRoutePolicy(
        actor=actor,
        authorization=CaseAuthorizationService(db),
        permission=permission,
    )
    if raw_case_id is None:
        query_case_id = request.query_params.get("case_id")
        if query_case_id is not None and permission is not None:
            try:
                policy.require(uuid.UUID(query_case_id))
            except ValueError as exc:
                raise RequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("query", "case_id"),
                            "msg": "Value error, invalid UUID",
                            "input": query_case_id,
                            "ctx": {"error": "invalid UUID"},
                        }
                    ]
                ) from exc
        return policy
    if operation_name == "admit_legacy_event_case":
        # This command intentionally targets a Case that has no admission row
        # yet, so the regular CaseAuthorizationService cannot resolve it.  The
        # endpoint's existing require_case_administrator dependency is the
        # pre-admission authority; all post-admission admin actions use grants.
        return policy
    try:
        case_id = uuid.UUID(str(raw_case_id))
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("path", "case_id"),
                    "msg": "Value error, invalid UUID",
                    "input": raw_case_id,
                    "ctx": {"error": "invalid UUID"},
                }
            ]
        ) from exc
    policy.require(case_id)
    return policy


RequireCaseRoute = Annotated[
    CaseRoutePolicy,
    Depends(require_case_route_permission),
]
