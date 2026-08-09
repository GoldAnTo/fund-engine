"""Append-only persistence and effective-version reads for research protocol."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research_protocol import (
    CaseMechanismSelectionVersion,
    MechanismTemplateVersion,
    MetricDefinitionVersion,
    OutcomeBindingVersion,
    VerificationRuleVersion,
)


class ResearchProtocolRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def effective_metric(self, metric_id: str) -> MetricDefinitionVersion | None:
        return self._session.scalar(
            select(MetricDefinitionVersion)
            .where(MetricDefinitionVersion.metric_id == metric_id)
            .order_by(MetricDefinitionVersion.version.desc(), MetricDefinitionVersion.id.desc())
            .limit(1)
        )

    def add_metric_version(
        self,
        *,
        metric_id: str,
        display_name: str,
        canonical_definition: str,
        entity_scope: str,
        unit: str,
        frequency: str,
        period_semantics: str,
        allowed_source_roles: list[str],
        role_eligibility: list[str],
        approved_by: str,
        reason: str,
        created_at: datetime,
        supersedes_id: uuid.UUID | None = None,
    ) -> MetricDefinitionVersion:
        previous = self.effective_metric(metric_id)
        if supersedes_id is not None and (previous is None or previous.id != supersedes_id):
            raise ValueError("metric supersedes_id must reference the effective prior version")
        metric = MetricDefinitionVersion(
            metric_id=metric_id,
            version=(previous.version + 1) if previous else 1,
            display_name=display_name,
            canonical_definition=canonical_definition,
            entity_scope=entity_scope,
            unit=unit,
            frequency=frequency,
            period_semantics=period_semantics,
            allowed_source_roles=allowed_source_roles,
            role_eligibility=role_eligibility,
            supersedes_id=supersedes_id,
            approved_by=approved_by,
            reason=reason,
            created_at=created_at,
        )
        self._session.add(metric)
        self._session.flush()
        return metric

    def add_outcome_binding_version(
        self,
        *,
        thesis_id: uuid.UUID,
        metric_definition_id: uuid.UUID,
        entity_scope: dict,
        direction: str,
        baseline: dict,
        horizon_start: date,
        horizon_end: date,
        state: str,
        reviewer: str,
        reason: str,
        created_at: datetime,
        supersedes_id: uuid.UUID | None = None,
    ) -> OutcomeBindingVersion:
        binding = OutcomeBindingVersion(
            thesis_id=thesis_id,
            metric_definition_id=metric_definition_id,
            entity_scope=entity_scope,
            direction=direction,
            baseline=baseline,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            state=state,
            supersedes_id=supersedes_id,
            reviewer=reviewer,
            reason=reason,
            created_at=created_at,
        )
        self._session.add(binding)
        self._session.flush()
        return binding

    def effective_binding(self, thesis_id: uuid.UUID) -> OutcomeBindingVersion | None:
        return self._session.scalar(
            select(OutcomeBindingVersion)
            .where(OutcomeBindingVersion.thesis_id == thesis_id)
            .order_by(OutcomeBindingVersion.created_at.desc(), OutcomeBindingVersion.id.desc())
            .limit(1)
        )

    def effective_case_template(
        self, research_case_id: uuid.UUID
    ) -> CaseMechanismSelectionVersion | None:
        return self._session.scalar(
            select(CaseMechanismSelectionVersion)
            .where(CaseMechanismSelectionVersion.research_case_id == research_case_id)
            .order_by(CaseMechanismSelectionVersion.created_at.desc(), CaseMechanismSelectionVersion.id.desc())
            .limit(1)
        )

    def add_case_template_selection(
        self,
        *,
        research_case_id: uuid.UUID,
        template_version_id: uuid.UUID,
        reviewer: str,
        reason: str,
        created_at: datetime,
    ) -> CaseMechanismSelectionVersion:
        prior = self.effective_case_template(research_case_id)
        selection = CaseMechanismSelectionVersion(
            research_case_id=research_case_id,
            template_version_id=template_version_id,
            supersedes_id=prior.id if prior else None,
            reviewer=reviewer,
            reason=reason,
            created_at=created_at,
        )
        self._session.add(selection)
        self._session.flush()
        return selection

    def template(self, template_version_id: uuid.UUID) -> MechanismTemplateVersion | None:
        return self._session.get(MechanismTemplateVersion, template_version_id)

    def add_verification_rule_version(
        self,
        *,
        research_case_id: uuid.UUID,
        mechanism_edge_id: uuid.UUID,
        metric_definition_id: uuid.UUID,
        expected_direction: str,
        support_predicate: str,
        contradiction_predicate: str,
        allowed_source_roles: list[str],
        observed_period_start: date,
        observed_period_end: date,
        available_at_deadline: date,
        next_verification_event: str,
        reviewer: str,
        reason: str,
        created_at: datetime,
    ) -> VerificationRuleVersion:
        prior = self.effective_rule(research_case_id, mechanism_edge_id)
        rule = VerificationRuleVersion(
            research_case_id=research_case_id,
            mechanism_edge_id=mechanism_edge_id,
            metric_definition_id=metric_definition_id,
            expected_direction=expected_direction,
            support_predicate=support_predicate,
            contradiction_predicate=contradiction_predicate,
            allowed_source_roles=allowed_source_roles,
            observed_period_start=observed_period_start,
            observed_period_end=observed_period_end,
            available_at_deadline=available_at_deadline,
            next_verification_event=next_verification_event,
            supersedes_id=prior.id if prior else None,
            reviewer=reviewer,
            reason=reason,
            created_at=created_at,
        )
        self._session.add(rule)
        self._session.flush()
        return rule

    def effective_rule(
        self, research_case_id: uuid.UUID, mechanism_edge_id: uuid.UUID
    ) -> VerificationRuleVersion | None:
        return self._session.scalar(
            select(VerificationRuleVersion)
            .where(
                VerificationRuleVersion.research_case_id == research_case_id,
                VerificationRuleVersion.mechanism_edge_id == mechanism_edge_id,
            )
            .order_by(VerificationRuleVersion.created_at.desc(), VerificationRuleVersion.id.desc())
            .limit(1)
        )

    def rule_history(
        self, research_case_id: uuid.UUID, mechanism_edge_ids: list[uuid.UUID]
    ) -> list[VerificationRuleVersion]:
        if not mechanism_edge_ids:
            return []
        return list(self._session.scalars(
            select(VerificationRuleVersion)
            .where(
                VerificationRuleVersion.research_case_id == research_case_id,
                VerificationRuleVersion.mechanism_edge_id.in_(mechanism_edge_ids),
            )
            .order_by(VerificationRuleVersion.created_at.desc(), VerificationRuleVersion.id.desc())
        ))
