"""Align the stamped PostgreSQL indexes with the active ORM metadata.

Revision ID: 0045
Revises: 0044
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_INDEX_RENAMES = (
    ("case_monitor_versions", "ix_case_monitor_versions_case", "ix_case_monitor_versions_research_case_id"),
    ("case_relations", "ix_case_relations_source", "ix_case_relations_source_case_id"),
    ("case_relations", "ix_case_relations_target", "ix_case_relations_target_case_id"),
    ("claim_verifications", "ix_claim_verifications_factor", "ix_claim_verifications_key_factor_id"),
    ("event_research_briefs", "ix_event_research_briefs_case", "ix_event_research_briefs_research_case_id"),
    ("event_research_conclusions", "ix_event_research_conclusions_case", "ix_event_research_conclusions_research_case_id"),
    ("event_research_conclusions", "ix_event_research_conclusions_scope_version", "ix_event_research_conclusions_scope_version_id"),
    ("event_research_factor_drafts", "ix_event_research_factor_drafts_case", "ix_event_research_factor_drafts_research_case_id"),
    ("event_research_scope_evidence_assignments", "ix_event_research_scope_evidence_assignments_scope_version", "ix_event_research_scope_evidence_assignments_scope_version_id"),
    ("event_research_scope_factors", "ix_event_research_scope_factors_scope_version", "ix_event_research_scope_factors_scope_version_id"),
    ("event_research_scope_versions", "ix_event_research_scope_versions_case", "ix_event_research_scope_versions_research_case_id"),
    ("fundamental_impacts", "ix_fundamental_impacts_case", "ix_fundamental_impacts_research_case_id"),
    ("key_factors", "ix_key_factors_case", "ix_key_factors_research_case_id"),
    ("key_factors", "ix_key_factors_thesis", "ix_key_factors_thesis_id"),
    ("market_instrument_bindings", "ix_market_instrument_bindings_case", "ix_market_instrument_bindings_research_case_id"),
    ("market_observations", "ix_market_observations_case", "ix_market_observations_research_case_id"),
    ("provider_records", "ix_provider_records_document", "ix_provider_records_document_version_id"),
    ("report_claims", "ix_report_claims_case", "ix_report_claims_research_case_id"),
    ("research_run_events", "ix_research_run_events_run", "ix_research_run_events_run_id"),
    ("source_contracts", "ix_source_contracts_document", "ix_source_contracts_document_version_id"),
)

RETIRED_ACTIVE_INDEXES = (
    ("case_document_versions", "ix_case_document_versions_case_document"),
    ("holding_disclosures", "ix_holding_disclosures_stock_published_report"),
    ("research_runs", "ix_research_runs_case_created"),
    ("research_tasks", "ix_research_tasks_run_status"),
    ("research_tasks", "ix_research_tasks_thesis_type"),
    ("source_spans", "ix_source_spans_document_version"),
    ("stocks", "ix_stocks_company_market"),
    ("valuation_snapshots", "ix_valuation_snapshots_stock_metric_as_of"),
)


def _index_names(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name, old_name, new_name in ACTIVE_INDEX_RENAMES:
        names = _index_names(table_name)
        if old_name not in names:
            continue
        if new_name in names:
            op.drop_index(old_name, table_name=table_name)
        else:
            op.execute(f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"')
    for table_name, index_name in RETIRED_ACTIVE_INDEXES:
        if index_name in _index_names(table_name):
            op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    raise RuntimeError("0045 normalizes operational index names and is intentionally irreversible.")
