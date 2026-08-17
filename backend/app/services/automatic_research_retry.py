"""Locked, exact-scope retry orchestration for automatic research."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.automatic_research import (
    AUTOMATIC_RESEARCH_ACTIVE_RUN_STATUSES,
    AUTOMATIC_RESEARCH_UNMANAGED_RUN_MESSAGE,
)
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models.acquisition import AcquisitionJob
from app.models.event_research import EventResearchBrief
from app.models.ledger import ResearchCase
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_monitor import ResearchRunEvent
from app.queries.automatic_research import AutomaticResearchQueries
from app.services.auto_research import AutoResearchService
from app.services.automatic_research_scope import (
    AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE,
    AutomaticResearchScopeError,
    validate_automatic_research_scope,
)
from app.services.case_tenant_access import CaseTenantAccess


@dataclass(frozen=True, slots=True)
class AutomaticResearchRetry:
    case_id: str
    run_id: str


class AutomaticResearchRetryService:
    """Authorize, lock, validate, clone, and commit one safe retry."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def retry(self, case_id: uuid.UUID, *, tenant_id: str) -> AutomaticResearchRetry:
        try:
            result = self._retry(case_id, tenant_id=tenant_id)
            self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise

    def _retry(self, case_id: uuid.UUID, *, tenant_id: str) -> AutomaticResearchRetry:
        CaseTenantAccess(self._session).require_case(case_id, tenant_id)
        self._session.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        brief = self._session.scalar(
            select(EventResearchBrief).where(
                EventResearchBrief.research_case_id == case_id
            )
        )
        if brief is None or brief.workflow_mode != "automatic":
            raise NotFoundError("automatic research case not found")
        lifecycle = self._session.scalar(
            select(EventResearchLifecycle)
            .where(EventResearchLifecycle.research_case_id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lifecycle is None or lifecycle.active_run_id is None:
            raise NotFoundError("automatic research case not found")
        old_run = self._session.scalar(
            select(ResearchRun)
            .where(
                ResearchRun.id == lifecycle.active_run_id,
                ResearchRun.research_case_id == case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if old_run is None:
            raise NotFoundError("automatic research case not found")
        active_runs = list(
            self._session.scalars(
                select(ResearchRun)
                .where(
                    ResearchRun.research_case_id == case_id,
                    ResearchRun.status.in_(AUTOMATIC_RESEARCH_ACTIVE_RUN_STATUSES),
                )
                .order_by(ResearchRun.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if active_runs:
            raise ConflictError(AUTOMATIC_RESEARCH_UNMANAGED_RUN_MESSAGE)
        list(
            self._session.scalars(
                select(AcquisitionJob)
                .where(AcquisitionJob.research_run_id == old_run.id)
                .order_by(AcquisitionJob.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        view = AutomaticResearchQueries(self._session).get(case_id, tenant_id)
        if view.status != "failed":
            raise ConflictError("only failed automatic research can be retried")

        scope_event = self._session.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == old_run.id,
                ResearchRunEvent.stage == "scope",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        payload = scope_event.payload_json if scope_event is not None else None
        try:
            validated_scope = validate_automatic_research_scope(
                self._session, old_run, payload
            )
        except AutomaticResearchScopeError as exc:
            raise ConflictError(
                AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE
            ) from exc

        frozen = copy.deepcopy(validated_scope.payload)
        expected_payload = copy.deepcopy(frozen)
        expected_payload["trigger"] = "retry"
        expected_payload["retried_from_run_id"] = str(old_run.id)
        try:
            new_run = AutoResearchService(self._session).start(
                case_id,
                max_rounds=old_run.max_rounds,
                budget=old_run.budget,
                commit=False,
                thesis_ids=list(validated_scope.factor_ids),
                monitor_version_id=old_run.monitor_version_id,
                trigger="retry",
                allowed_source_types=(
                    list(frozen["allowed_source_types"])
                    if old_run.monitor_version_id is not None
                    else None
                ),
                scope_context=copy.deepcopy(expected_payload),
            )
        except (ValueError, ValidationFailedError) as exc:
            raise ConflictError(
                AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE
            ) from exc
        new_scope_event = self._session.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == new_run.id,
                ResearchRunEvent.stage == "scope",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        new_payload = (
            copy.deepcopy(new_scope_event.payload_json)
            if new_scope_event is not None
            else None
        )
        try:
            new_validated = validate_automatic_research_scope(
                self._session, new_run, new_payload
            )
        except AutomaticResearchScopeError as exc:
            raise ConflictError(
                AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE
            ) from exc
        if (
            new_payload != expected_payload
            or new_run.scope_thesis_ids != old_run.scope_thesis_ids
            or new_run.max_rounds != old_run.max_rounds
            or new_run.budget != old_run.budget
            or new_validated.factor_ids != validated_scope.factor_ids
        ):
            raise ConflictError(
                "automatic research retry scope differs from failed run"
            )

        lifecycle.status = "researching"
        lifecycle.active_run_id = new_run.id
        lifecycle.current_round = 1
        lifecycle.status_summary = "自动研究已重新排队"
        lifecycle.current_gap = None
        lifecycle.next_human_action = None
        lifecycle.updated_at = new_run.updated_at
        return AutomaticResearchRetry(case_id=str(case_id), run_id=str(new_run.id))
