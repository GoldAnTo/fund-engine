"""Durable orchestration state, transition history, and coverage projections."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class ResearchOrchestration(Base):
    """Mutable checkpoint for one tenant's event-research case."""

    __tablename__ = "research_orchestrations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "research_case_id",
            name="uq_research_orchestrations_tenant_case",
        ),
        CheckConstraint(
            "trim(tenant_id) <> ''",
            name="ck_research_orchestrations_tenant_nonblank",
        ),
        CheckConstraint(
            "state IN ('intake', 'awaiting_scope_confirmation', "
            "'planning_acquisition', 'acquiring', 'freezing_sources', "
            "'assessing_coverage', 'synthesizing_evidence', "
            "'adjudicating_thesis', 'generating_report', 'monitoring', "
            "'retry_wait', 'recovering', 'needs_scope_decision', 'exhausted', "
            "'cancelled', 'failed')",
            name="ck_research_orchestrations_state",
        ),
        CheckConstraint(
            "user_stage IN ('intake', 'scope_confirmation', 'acquisition', "
            "'evidence_synthesis', 'thesis_adjudication', 'report_monitoring')",
            name="ck_research_orchestrations_user_stage",
        ),
        CheckConstraint(
            "(next_action_kind IS NULL AND next_action_label IS NULL) OR "
            "(next_action_kind IS NOT NULL AND next_action_label IS NOT NULL)",
            name="ck_research_orchestrations_next_action",
        ),
        CheckConstraint(
            "recovery_status IS NULL OR recovery_status IN "
            "('healthy', 'stale', 'recovering', 'failed')",
            name="ck_research_orchestrations_recovery_status",
        ),
        CheckConstraint(
            "version >= 0",
            name="ck_research_orchestrations_version_non_negative",
        ),
        Index(
            "ix_research_orchestrations_current_scope_version",
            "current_scope_version_id",
        ),
        Index(
            "ix_research_orchestrations_current_research_run",
            "current_research_run_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_cases.id",
            name="fk_research_orchestrations_research_case_id",
        ),
        nullable=False,
    )
    current_scope_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "event_research_scope_versions.id",
            name="fk_research_orchestrations_current_scope_version_id",
        ),
        nullable=True,
    )
    current_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "research_runs.id",
            name="fk_research_orchestrations_current_research_run_id",
        ),
        nullable=True,
    )
    state: Mapped[str] = mapped_column(
        String(64), nullable=False, default="intake", server_default="intake"
    )
    user_stage: Mapped[str] = mapped_column(
        String(64), nullable=False, default="intake", server_default="intake"
    )
    current_system_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_action_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    next_action_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_action_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchOrchestrationEvent(Base):
    """Immutable transition record for an orchestration checkpoint."""

    __tablename__ = "research_orchestration_events"
    __table_args__ = (
        UniqueConstraint(
            "orchestration_id",
            "sequence",
            name="uq_research_orchestration_events_sequence",
        ),
        UniqueConstraint(
            "orchestration_id",
            "idempotency_key",
            name="uq_research_orchestration_events_idempotency",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_research_orchestration_events_sequence_positive",
        ),
        CheckConstraint(
            "trim(transition) <> ''",
            name="ck_research_orchestration_events_transition_nonblank",
        ),
        CheckConstraint(
            "trim(actor) <> ''",
            name="ck_research_orchestration_events_actor_nonblank",
        ),
        CheckConstraint(
            "trim(message) <> ''",
            name="ck_research_orchestration_events_message_nonblank",
        ),
        CheckConstraint(
            "trim(idempotency_key) <> ''",
            name="ck_research_orchestration_events_idempotency_key_nonblank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    orchestration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_orchestrations.id",
            name="fk_research_orchestration_events_orchestration_id",
        ),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transition: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionSeries(Base):
    """Stable identity shared by every acquisition round for one goal."""

    __tablename__ = "acquisition_series"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "research_case_id",
            "research_run_id",
            "scope_version_id",
            "thesis_id",
            "goal_id",
            name="uq_acquisition_series_goal_identity",
        ),
        CheckConstraint(
            "trim(tenant_id) <> ''",
            name="ck_acquisition_series_tenant_nonblank",
        ),
        CheckConstraint(
            "trim(goal_id) <> ''",
            name="ck_acquisition_series_goal_nonblank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_cases.id",
            name="fk_acquisition_series_research_case_id",
        ),
        nullable=False,
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_runs.id",
            name="fk_acquisition_series_research_run_id",
        ),
        nullable=False,
    )
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "event_research_scope_versions.id",
            name="fk_acquisition_series_scope_version_id",
        ),
        nullable=False,
    )
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("theses.id", name="fk_acquisition_series_thesis_id"),
        nullable=False,
    )
    goal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionQueryPlan(Base):
    """Immutable ordered query plan for one acquisition round."""

    __tablename__ = "acquisition_query_plans"
    __table_args__ = (
        UniqueConstraint(
            "acquisition_job_id",
            name="uq_acquisition_query_plans_job",
        ),
        UniqueConstraint(
            "series_id",
            "acquisition_round",
            name="uq_acquisition_query_plans_series_round",
        ),
        UniqueConstraint(
            "id",
            "series_id",
            "acquisition_round",
            name="uq_acquisition_query_plans_lineage_target",
        ),
        ForeignKeyConstraint(
            (
                "previous_query_plan_id",
                "series_id",
                "previous_acquisition_round",
            ),
            (
                "acquisition_query_plans.id",
                "acquisition_query_plans.series_id",
                "acquisition_query_plans.acquisition_round",
            ),
            name="fk_acquisition_query_plans_previous_plan_lineage",
        ),
        CheckConstraint(
            "acquisition_round >= 1",
            name="ck_acquisition_query_plans_round_positive",
        ),
        CheckConstraint(
            "trim(goal_id) <> ''",
            name="ck_acquisition_query_plans_goal_id_nonblank",
        ),
        CheckConstraint(
            "trim(planner_version) <> ''",
            name="ck_acquisition_query_plans_planner_version_nonblank",
        ),
        CheckConstraint(
            "trim(policy_version) <> ''",
            name="ck_acquisition_query_plans_policy_version_nonblank",
        ),
        CheckConstraint(
            "(acquisition_round = 1 AND previous_query_plan_id IS NULL "
            "AND previous_acquisition_round IS NULL) OR "
            "(acquisition_round > 1 AND previous_query_plan_id IS NOT NULL "
            "AND previous_acquisition_round IS NOT NULL "
            "AND previous_acquisition_round = acquisition_round - 1 "
            "AND expansion_trigger IS NOT NULL "
            "AND trim(expansion_trigger) <> '')",
            name="ck_acquisition_query_plans_round_lineage",
        ),
        Index(
            "ix_acquisition_query_plans_previous_query_plan",
            "previous_query_plan_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "acquisition_series.id",
            name="fk_acquisition_query_plans_series_id",
        ),
        nullable=False,
    )
    acquisition_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "acquisition_jobs.id",
            name="fk_acquisition_query_plans_acquisition_job_id",
        ),
        nullable=False,
    )
    acquisition_round: Mapped[int] = mapped_column(Integer, nullable=False)
    goal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    planner_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    frozen_inputs_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    ordered_queries_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    previous_query_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True
    )
    previous_acquisition_round: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    expansion_trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionGoalCoverage(Base):
    """Mutable readiness projection for one run, scope, and acquisition goal."""

    __tablename__ = "acquisition_goal_coverages"
    __table_args__ = (
        UniqueConstraint(
            "research_run_id",
            "scope_version_id",
            "goal_id",
            name="uq_acquisition_goal_coverages_run_scope_goal",
        ),
        CheckConstraint(
            "trim(goal_id) <> ''",
            name="ck_acquisition_goal_coverages_goal_id_nonblank",
        ),
        CheckConstraint(
            "trim(objective) <> ''",
            name="ck_acquisition_goal_coverages_objective_nonblank",
        ),
        CheckConstraint(
            "trim(policy_version) <> ''",
            name="ck_acquisition_goal_coverages_policy_version_nonblank",
        ),
        CheckConstraint(
            "status IN ('ready', 'unmet', 'exhausted')",
            name="ck_acquisition_goal_coverages_status",
        ),
        CheckConstraint(
            "required_authority_count >= 0 AND observed_authority_count >= 0 "
            "AND required_independent_source_count >= 0 "
            "AND observed_independent_source_count >= 0",
            name="ck_acquisition_goal_coverages_counts_non_negative",
        ),
        CheckConstraint(
            "evaluation_round >= 1 AND zero_new_independent_source_rounds >= 0",
            name="ck_acquisition_goal_coverages_rounds_non_negative",
        ),
        Index("ix_acquisition_goal_coverages_thesis", "thesis_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_runs.id",
            name="fk_acquisition_goal_coverages_research_run_id",
        ),
        nullable=False,
    )
    scope_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "event_research_scope_versions.id",
            name="fk_acquisition_goal_coverages_scope_version_id",
        ),
        nullable=False,
    )
    thesis_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "theses.id",
            name="fk_acquisition_goal_coverages_thesis_id",
        ),
        nullable=True,
    )
    goal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="event-goal-coverage-v1",
        server_default="event-goal-coverage-v1",
    )
    required_authority_levels_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    observed_authority_levels_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unmet", server_default="unmet"
    )
    required_authority_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    observed_authority_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    required_independent_source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    observed_independent_source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    contrary_search_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    reason_codes_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    evidence_link_ids_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    independent_source_identities_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    conflict_details_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    unknown_details_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    evaluation_round: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    zero_new_independent_source_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
