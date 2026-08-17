"""Shared validation for immutable automatic-research scope snapshots."""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import (
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, Thesis
from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent


_OBJECTIVES = ["support", "contradict", "alternative_explanation"]
_SOURCE_ROLES = ["company_disclosure", "licensed_provider"]
AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE = "自动研究范围不可用，请稍后重试"


class AutomaticResearchScopeReason(str, Enum):
    FROZEN_INVALID = "frozen_invalid"
    CURRENT_MISSING = "current_missing"
    CURRENT_MISMATCH = "current_mismatch"


class AutomaticResearchScopeError(ValueError):
    def __init__(
        self,
        reason: AutomaticResearchScopeReason,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.reason = reason


def _frozen_invalid(detail: str) -> AutomaticResearchScopeError:
    return AutomaticResearchScopeError(
        AutomaticResearchScopeReason.FROZEN_INVALID,
        detail,
    )


@dataclass(frozen=True, slots=True)
class AutomaticResearchFactorScope:
    thesis_id: uuid.UUID
    statement: str
    objectives: tuple[str, ...]
    allowed_source_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedAutomaticResearchScope:
    _snapshot_json: str
    factors: tuple[AutomaticResearchFactorScope, ...]
    current_scope_id: uuid.UUID
    max_rounds: int
    budget: int
    allowed_source_types: tuple[str, ...]
    monitor_version_id: uuid.UUID | None
    research_question: str
    input_kind: str
    material_document_version_id: uuid.UUID | None

    @property
    def factor_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(factor.thesis_id for factor in self.factors)

    @property
    def factor_statements(self) -> tuple[str, ...]:
        return tuple(factor.statement for factor in self.factors)

    def snapshot(self) -> dict:
        """Return an isolated JSON copy for retry persistence or stale-claim checks."""
        return json.loads(self._snapshot_json)

    def factor(self, thesis_id: uuid.UUID) -> AutomaticResearchFactorScope:
        for factor in self.factors:
            if factor.thesis_id == thesis_id:
                return factor
        raise KeyError(thesis_id)


def load_automatic_research_scope(
    session: Session,
    run: ResearchRun,
) -> ValidatedAutomaticResearchScope:
    """Load and exactly validate a run's latest immutable scope snapshot."""
    event = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run.id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    if event is None or not isinstance(event.payload_json, dict):
        raise _frozen_invalid("automatic research scope event is missing")
    return validate_automatic_research_scope(session, run, event.payload_json)


def validate_automatic_research_scope(
    session: Session,
    run: ResearchRun,
    payload: dict,
) -> ValidatedAutomaticResearchScope:
    """Validate one run's frozen protocol, plan, theses, and current scope."""
    if type(run.max_rounds) is not int or not 1 <= run.max_rounds <= 3:
        raise _frozen_invalid("automatic research max rounds is invalid")
    if type(run.budget) is not int or run.budget < 1:
        raise _frozen_invalid("automatic research budget is invalid")
    if not isinstance(payload, dict) or payload.get("workflow_mode") != "automatic":
        raise _frozen_invalid("automatic research scope event is invalid")
    factor_values = payload.get("factor_ids")
    statements = payload.get("factor_statements")
    if not isinstance(factor_values, list) or not isinstance(statements, list):
        raise _frozen_invalid("automatic research factor scope is invalid")
    try:
        factor_ids = tuple(uuid.UUID(str(value)) for value in factor_values)
        run_ids = tuple(
            uuid.UUID(str(value)) for value in (run.scope_thesis_ids or [])
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise _frozen_invalid("automatic research factor identity is invalid") from exc
    factor_statements = tuple(statements)
    if (
        not factor_ids
        or factor_ids != run_ids
        or len(factor_ids) != len(set(factor_ids))
        or len(factor_ids) != len(factor_statements)
        or any(not isinstance(value, str) or not value for value in factor_statements)
        or len(factor_statements) != len(set(factor_statements))
    ):
        raise _frozen_invalid("automatic research factor scope differs from the run")
    for thesis_id, statement in zip(factor_ids, factor_statements):
        thesis = session.get(Thesis, thesis_id)
        if (
            thesis is None
            or thesis.research_case_id != run.research_case_id
            or thesis.statement != statement
        ):
            raise _frozen_invalid("automatic research factor thesis is invalid")

    protocol = payload.get("automatic_protocol")
    if (
        not isinstance(protocol, dict)
        or protocol.get("generated_by") != "system"
        or not isinstance(protocol.get("research_question"), str)
        or not protocol["research_question"].strip()
        or protocol.get("factors") != list(factor_statements)
    ):
        raise _frozen_invalid("automatic research protocol is invalid")

    if type(payload.get("budget")) is not int or payload["budget"] != run.budget:
        raise _frozen_invalid("automatic research top-level budget is invalid")

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
        raise _frozen_invalid("automatic research evidence plan is invalid")
    factor_scopes: list[AutomaticResearchFactorScope] = []
    for thesis_id, item, statement in zip(factor_ids, plan["items"], factor_statements):
        if not isinstance(item, dict) or item.get("factor") != statement:
            raise _frozen_invalid("automatic research evidence plan factor is invalid")
        objectives = item.get("objectives")
        if not isinstance(objectives, list) or any(
            not isinstance(value, str) for value in objectives
        ):
            raise _frozen_invalid("automatic research evidence plan objective is invalid")
        if len(objectives) != len(_OBJECTIVES):
            raise _frozen_invalid("automatic research evidence plan objective missing")
        if objectives != _OBJECTIVES:
            raise _frozen_invalid("automatic research evidence plan objective is invalid")
        roles = item.get("allowed_source_roles")
        if roles != _SOURCE_ROLES:
            raise _frozen_invalid("automatic research evidence plan source roles are invalid")
        factor_scopes.append(
            AutomaticResearchFactorScope(
                thesis_id=thesis_id,
                statement=statement,
                objectives=tuple(objectives),
                allowed_source_roles=tuple(roles),
            )
        )
    allowed_source_types = payload.get("allowed_source_types")
    if (
        not isinstance(allowed_source_types, list)
        or any(not isinstance(value, str) for value in allowed_source_types)
        or len(allowed_source_types) != len(set(allowed_source_types))
    ):
        raise _frozen_invalid("automatic research source scope is invalid")
    if "source_scope" in payload and not isinstance(payload["source_scope"], dict):
        raise _frozen_invalid("automatic research source scope is invalid")

    input_kind = payload.get("input_kind", "topic")
    material_document_version_id: uuid.UUID | None = None
    if input_kind == "material":
        try:
            material_document_version_id = uuid.UUID(
                str(payload["intake_material_document_version_id"])
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _frozen_invalid("automatic material source binding is invalid") from exc
        admission = session.scalar(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == run.research_case_id,
                CaseTenantAdmission.initial_document_version_id
                == material_document_version_id,
            )
        )
        attachment = session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == run.research_case_id,
                CaseDocumentVersion.document_version_id
                == material_document_version_id,
            )
        )
        if admission is None or attachment is None:
            raise _frozen_invalid("automatic material document crosses its Case")
    elif input_kind == "topic":
        if "intake_material_document_version_id" in payload:
            raise _frozen_invalid("automatic topic scope cannot bind intake material")
    else:
        raise _frozen_invalid("automatic research input kind is invalid")

    expected_monitor_id = (
        str(run.monitor_version_id) if run.monitor_version_id is not None else None
    )
    if (
        "monitor_version_id" not in payload
        or payload["monitor_version_id"] != expected_monitor_id
    ):
        raise _frozen_invalid("automatic research monitor identity is invalid")
    monitor_fields = (
        "frequency",
        "next_verification_event",
        "configured_by",
        "configuration_change_reason",
    )
    if run.monitor_version_id is None:
        if allowed_source_types != [] or any(
            field not in payload or payload[field] is not None
            for field in monitor_fields
        ):
            raise _frozen_invalid("automatic research monitor scope is invalid")
    else:
        monitor = session.get(CaseMonitorVersion, run.monitor_version_id)
        if (
            monitor is None
            or monitor.research_case_id != run.research_case_id
            or not isinstance(monitor.allowed_source_types, list)
            or any(
                not isinstance(value, str) for value in monitor.allowed_source_types
            )
            or len(monitor.allowed_source_types)
            != len(set(monitor.allowed_source_types))
        ):
            raise _frozen_invalid("automatic research monitor scope is invalid")
        expected_monitor_fields = {
            "frequency": monitor.frequency,
            "next_verification_event": monitor.next_verification_event,
            "configured_by": monitor.changed_by,
            "configuration_change_reason": monitor.change_reason,
        }
        if allowed_source_types != monitor.allowed_source_types or any(
            payload.get(field) != value
            for field, value in expected_monitor_fields.items()
        ):
            raise _frozen_invalid("automatic research monitor scope is invalid")

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
        raise AutomaticResearchScopeError(
            AutomaticResearchScopeReason.CURRENT_MISSING,
            "automatic research current scope is missing",
        )
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
        raise AutomaticResearchScopeError(
            AutomaticResearchScopeReason.CURRENT_MISMATCH,
            "automatic research current scope has drifted",
        )
    return ValidatedAutomaticResearchScope(
        _snapshot_json=json.dumps(copy.deepcopy(payload), ensure_ascii=False),
        factors=tuple(factor_scopes),
        current_scope_id=current_scope.id,
        max_rounds=run.max_rounds,
        budget=run.budget,
        allowed_source_types=tuple(allowed_source_types),
        monitor_version_id=run.monitor_version_id,
        research_question=protocol["research_question"],
        input_kind=input_kind,
        material_document_version_id=material_document_version_id,
    )
