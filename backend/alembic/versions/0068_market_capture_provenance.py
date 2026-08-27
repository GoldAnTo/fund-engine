"""Persist immutable source provenance for frozen market snapshots.

Revision ID: 0068
Revises: 0067
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_TABLE = "uw_market_capture_envelopes"
_LEGACY_LOCATOR = "legacy snapshot: exact source locator was not captured"
_LEGACY_POLICY = "legacy_snapshot_without_exact_provenance.v1"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_array_constraint(column: str, dialect: str) -> str:
    if dialect == "sqlite":
        return f"json_type({column}) = 'array'"
    if dialect == "postgresql":
        return f"json_typeof({column}) = 'array'"
    raise RuntimeError("market provenance requires SQLite or PostgreSQL")


def _install_immutable_triggers(dialect: str) -> None:
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER no_{action.lower()}_{_TABLE}
                BEFORE {action} ON {_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'immutable market capture is append-only');
                END;
            """)
        return
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER no_{action.lower()}_{_TABLE} BEFORE {action} ON {_TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def _drop_immutable_triggers(dialect: str) -> None:
    for action in ("delete", "update"):
        suffix = f" ON {_TABLE}" if dialect == "postgresql" else ""
        op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{_TABLE}{suffix};")


def _backfill_legacy_rows() -> None:
    connection = op.get_bind()
    sources = (
        ("price", "uw_price_snapshots", "available_at"),
        ("fx", "uw_fx_snapshots", "available_at"),
        ("capital_structure", "uw_capital_structure_snapshots", "available_at"),
        ("security_rights", "uw_security_rights_versions", "created_at"),
    )
    rows: list[dict[str, object]] = []
    for kind, table, availability_column in sources:
        for item in connection.execute(
            sa.text(
                f"SELECT id, source_id, raw_hash, created_at, "
                f"{availability_column} AS authenticated_available_at FROM {table}"
            )
        ).mappings():
            snapshot_uuid = uuid.UUID(str(item["id"]))
            snapshot_id = str(snapshot_uuid)
            acquired_at = item["created_at"]
            if isinstance(acquired_at, str):
                acquired_at = datetime.fromisoformat(acquired_at)
            authenticated_available_at = item["authenticated_available_at"]
            if isinstance(authenticated_available_at, str):
                authenticated_available_at = datetime.fromisoformat(
                    authenticated_available_at
                )
            payload = {
                "schema_version": "product.market-capture-envelope.v1",
                "snapshot_kind": kind,
                "snapshot_id": snapshot_id,
                "provenance_role": "primary",
                "source_url": item["source_id"],
                "source_locator": _LEGACY_LOCATOR,
                "provider_policy_version": _LEGACY_POLICY,
                "raw_hash": item["raw_hash"],
                "raw_components": [],
                "authenticated_available_at": authenticated_available_at.isoformat(),
            }
            rows.append(
                {
                    "id": uuid.uuid5(uuid.NAMESPACE_URL, f"market-capture:{kind}:{snapshot_id}:primary"),
                    **{key: payload[key] for key in payload if key != "schema_version"},
                    "snapshot_id": snapshot_uuid,
                    "content_hash": _canonical_hash(payload),
                    "authenticated_available_at": authenticated_available_at,
                    "acquired_at": acquired_at,
                }
            )
    if rows:
        table = sa.table(
            _TABLE,
            sa.column("id", sa.Uuid()),
            sa.column("snapshot_kind", sa.String()),
            sa.column("snapshot_id", sa.Uuid()),
            sa.column("provenance_role", sa.String()),
            sa.column("source_url", sa.Text()),
            sa.column("source_locator", sa.Text()),
            sa.column("provider_policy_version", sa.String()),
            sa.column("raw_hash", sa.String()),
            sa.column("raw_components", sa.JSON()),
            sa.column("content_hash", sa.String()),
            sa.column("authenticated_available_at", sa.DateTime(timezone=True)),
            sa.column("acquired_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(table, rows)


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_kind", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("provenance_role", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("provider_policy_version", sa.String(length=128), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_components", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("authenticated_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "snapshot_kind IN ('price', 'fx', 'capital_structure', 'security_rights')",
            name="ck_uw_market_capture_kind",
        ),
        sa.CheckConstraint(
            "provenance_role IN ('primary', 'class_b_legal_rights', 'class_b_units')",
            name="ck_uw_market_capture_role",
        ),
        sa.CheckConstraint("length(raw_hash) = 64", name="ck_uw_market_capture_raw_hash"),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_market_capture_content_hash"
        ),
        sa.CheckConstraint(
            _json_array_constraint("raw_components", dialect),
            name="ck_uw_market_capture_components_array",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_uw_market_capture_envelopes"),
        sa.UniqueConstraint(
            "snapshot_kind",
            "snapshot_id",
            "provenance_role",
            name="uq_uw_market_capture_snapshot_role",
        ),
    )
    op.create_index(
        "ix_uw_market_capture_snapshot",
        _TABLE,
        ["snapshot_kind", "snapshot_id"],
    )
    _backfill_legacy_rows()
    _install_immutable_triggers(dialect)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect)
    op.drop_index("ix_uw_market_capture_snapshot", table_name=_TABLE)
    op.drop_table(_TABLE)
