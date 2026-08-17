"""Shared validation for immutable automatic-research scope snapshots."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import (
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import Thesis
from app.models.operational import ResearchRun


_OBJECTIVES = ["support", "contradict", "alternative_explanation"]
_SOURCE_ROLES = ["company_disclosure", "licensed_provider"]


@dataclass(frozen=True, slots=True)
class ValidatedAutomaticResearchScope:
    payload: dict
    factor_ids: tuple[uuid.UUID, ...]
    factor_statements: tuple[str, ...]
    current_scope_id: uuid.UUID


def validate_automatic_research_scope(
    session: Session,
    run: ResearchRun,
    payload: dict,
) -> ValidatedAutomaticResearchScope:
    """Validate one run's frozen protocol, plan, theses, and current scope."""
    if not isinstance(payload, dict) or payload.get("workflow_mode") != "automatic":
        raise ValueError("automatic research scope event is invalid")
    factor_values = payload.get("factor_ids")
    statements = payload.get("factor_statements")
    if not isinstance(factor_values, list) or not isinstance(statements, list):
        raise ValueError("automatic research factor scope is invalid")
    try:
        factor_ids = tuple(uuid.UUID(str(value)) for value in factor_values)
        run_ids = tuple(
            uuid.UUID(str(value)) for value in (run.scope_thesis_ids or [])
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("automatic research factor identity is invalid") from exc
    factor_statements = tuple(statements)
    if (
        not factor_ids
        or factor_ids != run_ids
        or len(factor_ids) != len(set(factor_ids))
        or len(factor_ids) != len(factor_statements)
        or any(not isinstance(value, str) or not value for value in factor_statements)
        or len(factor_statements) != len(set(factor_statements))
    ):
        raise ValueError("automatic research factor scope differs from the run")
    for thesis_id, statement in zip(factor_ids, factor_statements):
        thesis = session.get(Thesis, thesis_id)
        if (
            thesis is None
            or thesis.research_case_id != run.research_case_id
            or thesis.statement != statement
        ):
            raise ValueError("automatic research factor thesis is invalid")

    protocol = payload.get("automatic_protocol")
    if (
        not isinstance(protocol, dict)
        or protocol.get("generated_by") != "system"
        or not isinstance(protocol.get("research_question"), str)
        or not protocol["research_question"].strip()
        or protocol.get("factors") != list(factor_statements)
    ):
        raise ValueError("automatic research protocol is invalid")

    plan = payload.get("automatic_evidence_plan")
    if (
        not isinstance(plan, dict)
        or type(plan.get("max_rounds")) is not int
        or plan.get("max_rounds") != run.max_rounds
        or type(plan.get("budget")) is not int
        or plan.get("budget") != run.budget
        or not isinstance(plan.get("items"), list)
        or len(plan["items"]) != len(factor_statements)
    ):
        raise ValueError("automatic research evidence plan is invalid")
    for item, statement in zip(plan["items"], factor_statements):
        if (
            not isinstance(item, dict)
            or item.get("factor") != statement
            or item.get("objectives") != _OBJECTIVES
            or item.get("allowed_source_roles") != _SOURCE_ROLES
        ):
            raise ValueError("automatic research evidence plan item is invalid")
    allowed_source_types = payload.get("allowed_source_types")
    if (
        not isinstance(allowed_source_types, list)
        or any(not isinstance(value, str) for value in allowed_source_types)
        or len(allowed_source_types) != len(set(allowed_source_types))
    ):
        raise ValueError("automatic research source scope is invalid")
    if "source_scope" in payload and not isinstance(payload["source_scope"], dict):
        raise ValueError("automatic research source scope is invalid")

    current_scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(
            EventResearchScopeVersion.version.desc(),
            EventResearchScopeVersion.id.desc(),
        )
        .limit(1)
    )
    if current_scope is None:
        raise ValueError("automatic research current scope is missing")
    current_statements = tuple(
        session.scalars(
            select(EventResearchScopeFactor.statement)
            .where(EventResearchScopeFactor.scope_version_id == current_scope.id)
            .order_by(
                EventResearchScopeFactor.position,
                EventResearchScopeFactor.id,
            )
        )
    )
    if current_statements != factor_statements:
        raise ValueError("automatic research current scope has drifted")
    return ValidatedAutomaticResearchScope(
        payload=copy.deepcopy(payload),
        factor_ids=factor_ids,
        factor_statements=factor_statements,
        current_scope_id=current_scope.id,
    )
