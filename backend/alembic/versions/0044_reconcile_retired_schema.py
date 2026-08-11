"""Reconcile a stamped legacy PostgreSQL schema with the active research model.

Revision ID: 0044
Revises: 0043

Older local PostgreSQL databases were created from now-retired impact/report
models before the current Alembic chain became authoritative.  Their rows must
never be silently discarded: this migration stops before changing anything if
one of those retired tables contains a record or if a current table depends on
it.  Empty retired tables can then be removed in foreign-key-safe order.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RETIRED_TABLES = (
    "china_industry_index_memberships",
    "china_industry_index_snapshots",
    "china_industry_indexes",
    "company_identity_aliases",
    "company_impact_observations",
    "company_impact_relation_reviews",
    "company_impact_relations",
    "document_blobs",
    "document_source_records",
    "document_supplement_links",
    "embed_grant_revocations",
    "embed_grants",
    "event_impact_hypotheses",
    "event_impact_hypothesis_assessments",
    "event_impact_refresh_claims",
    "recovery_supplement_snapshots",
    "report_case_source_spans",
    "report_claims_legacy_pre_0021",
    "report_confounder_assessments",
    "report_extraction_claims",
    "report_fund_exposures",
    "report_market_confounders",
    "report_market_observations",
    "report_relations",
    "report_research_scope_claims",
    "report_research_scope_relations",
    "report_research_scope_versions",
    "source_contract_versions",
)


def assert_retired_tables_empty(
    table_names: Sequence[str], row_count: Callable[[str], int]
) -> None:
    """Refuse cleanup rather than silently discard any legacy evidence."""
    nonempty = [table_name for table_name in table_names if row_count(table_name)]
    if nonempty:
        raise RuntimeError(
            "Cannot remove retired schema with historical records: "
            + ", ".join(sorted(nonempty))
            + ". Archive or migrate the records explicitly before retrying."
        )


def retired_drop_order(
    outgoing_foreign_keys: Mapping[str, Set[str]],
) -> list[str]:
    """Return children before the retired parents they reference."""
    remaining = set(outgoing_foreign_keys)
    order: list[str] = []
    while remaining:
        leaves = sorted(
            table_name
            for table_name in remaining
            if not any(
                table_name in outgoing_foreign_keys[other]
                for other in remaining
            )
        )
        if not leaves:
            raise RuntimeError(
                "Cannot safely order retired schema cleanup because its foreign keys cycle: "
                + ", ".join(sorted(remaining))
            )
        order.extend(leaves)
        remaining.difference_update(leaves)
    return order


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        return
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("constrained_columns") == [column_name]:
            op.drop_constraint(
                foreign_key["name"], table_name, type_="foreignkey"
            )
    for constraint in inspector.get_unique_constraints(table_name):
        if column_name in (constraint.get("column_names") or []):
            op.drop_constraint(constraint["name"], table_name, type_="unique")
    op.drop_column(table_name, column_name)


def _drop_retired_tables() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())
    retired = tuple(table_name for table_name in RETIRED_TABLES if table_name in present)
    if not retired:
        return
    retired_set = set(retired)

    for table_name in present:
        if table_name in retired_set:
            continue
        for foreign_key in inspector.get_foreign_keys(table_name):
            if foreign_key.get("referred_table") in retired_set:
                raise RuntimeError(
                    f"Cannot remove retired table {foreign_key['referred_table']}: "
                    f"current table {table_name} still depends on it."
                )

    def row_count(table_name: str) -> int:
        return int(
            bind.execute(sa.text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        )

    assert_retired_tables_empty(retired, row_count)
    outgoing = {
        table_name: {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys(table_name)
            if foreign_key.get("referred_table") in retired_set
        }
        for table_name in retired
    }
    for table_name in retired_drop_order(outgoing):
        op.drop_table(table_name)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _drop_retired_tables()
    _drop_column_if_present("companies", "canonical_identity")
    _drop_column_if_present("task_items", "scope_version_id")
    _drop_column_if_present("valuation_snapshots", "available_at")
    op.alter_column("document_versions", "source_authority", server_default=None)
    op.alter_column("event_research_briefs", "source_type", server_default=None)
    op.alter_column("event_research_lifecycles", "current_round", server_default=None)


def downgrade() -> None:
    raise RuntimeError(
        "0044 removes only verified-empty retired schema and cannot recreate its retired model."
    )
