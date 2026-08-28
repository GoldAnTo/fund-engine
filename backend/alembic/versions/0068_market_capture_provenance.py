"""Persist immutable source provenance for frozen market snapshots.

Revision ID: 0068
Revises: 0067
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
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
_BUSINESS_UNIQUE_INDEXES = (
    (
        "uq_uw_price_snapshot_business_time",
        "uw_price_snapshots",
        ("security_identity_id", "price_type", "adjustment_basis", "market_at"),
    ),
    (
        "uq_uw_fx_snapshot_business_time",
        "uw_fx_snapshots",
        ("base_currency", "quote_currency", "quote_direction", "market_at"),
    ),
    (
        "uq_uw_capital_structure_business_time",
        "uw_capital_structure_snapshots",
        ("company_id", "report_period_start", "report_period_end", "market_at"),
    ),
    (
        "uq_uw_security_rights_business_time",
        "uw_security_rights_versions",
        ("security_identity_id", "effective_from"),
    ),
)


def _snapshot_update_trigger(dialect: str, table: str, *, install: bool) -> None:
    name = f"no_update_{table}"
    suffix = f" ON {table}" if dialect == "postgresql" else ""
    op.execute(f"DROP TRIGGER IF EXISTS {name}{suffix}")
    if not install:
        return
    if dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER {name}
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END
        """)
        return
    op.execute(
        f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
    )


def _grandfather_legacy_business_conflicts(dialect: str) -> None:
    for _name, table, columns in _BUSINESS_UNIQUE_INDEXES:
        _snapshot_update_trigger(dialect, table, install=False)
        column_list = ", ".join(columns)
        op.execute(f"""
            UPDATE {table}
            SET legacy_business_conflict = true
            WHERE ({column_list}) IN (
                SELECT {column_list}
                FROM {table}
                GROUP BY {column_list}
                HAVING COUNT(*) > 1
            )
        """)
        _snapshot_update_trigger(dialect, table, install=True)


def _install_legacy_conflict_insert_guards(dialect: str) -> None:
    for _name, table, columns in _BUSINESS_UNIQUE_INDEXES:
        trigger = f"reject_legacy_conflict_insert_{table}"
        identity = " AND ".join(f"existing.{column} = NEW.{column}" for column in columns)
        conflict_exists = (
            f"EXISTS (SELECT 1 FROM {table} AS existing "
            f"WHERE existing.legacy_business_conflict = true AND {identity})"
        )
        if dialect == "sqlite":
            op.execute(f"""
                CREATE TRIGGER {trigger}
                BEFORE INSERT ON {table}
                BEGIN
                    SELECT CASE WHEN NEW.legacy_business_conflict <> 0
                        OR {conflict_exists}
                    THEN RAISE(ABORT, 'legacy market conflict keys are quarantined') END;
                END
            """)
            continue
        function = f"{trigger}_fn"
        op.execute(f"""
            CREATE OR REPLACE FUNCTION {function}()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.legacy_business_conflict OR {conflict_exists} THEN
                    RAISE EXCEPTION 'legacy market conflict keys are quarantined';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(f"""
            CREATE TRIGGER {trigger}
            BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION {function}()
        """)


def _drop_legacy_conflict_insert_guards(dialect: str) -> None:
    for _name, table, _columns in _BUSINESS_UNIQUE_INDEXES:
        trigger = f"reject_legacy_conflict_insert_{table}"
        suffix = f" ON {table}" if dialect == "postgresql" else ""
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}{suffix}")
        if dialect == "postgresql":
            op.execute(f"DROP FUNCTION IF EXISTS {trigger}_fn()")


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


def _install_capture_reference_trigger(dialect: str) -> None:
    if dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER validate_{_TABLE}_insert
            BEFORE INSERT ON {_TABLE}
            BEGIN
                SELECT CASE WHEN
                    (NEW.provenance_role IN ('class_b_legal_rights', 'class_b_units')
                     AND NEW.snapshot_kind <> 'capital_structure')
                    OR (NEW.snapshot_kind = 'price' AND NOT EXISTS
                        (SELECT 1 FROM uw_price_snapshots WHERE id = NEW.snapshot_id))
                    OR (NEW.snapshot_kind = 'fx' AND NOT EXISTS
                        (SELECT 1 FROM uw_fx_snapshots WHERE id = NEW.snapshot_id))
                    OR (NEW.snapshot_kind = 'capital_structure' AND NOT EXISTS
                        (SELECT 1 FROM uw_capital_structure_snapshots WHERE id = NEW.snapshot_id))
                    OR (NEW.snapshot_kind = 'security_rights' AND NOT EXISTS
                        (SELECT 1 FROM uw_security_rights_versions WHERE id = NEW.snapshot_id))
                THEN RAISE(ABORT, 'invalid market capture snapshot kind/role reference') END;
            END;
        """)
        return
    op.execute("""
        CREATE OR REPLACE FUNCTION validate_market_capture_reference()
        RETURNS trigger AS $$
        BEGIN
            IF (NEW.provenance_role IN ('class_b_legal_rights', 'class_b_units')
                AND NEW.snapshot_kind <> 'capital_structure')
               OR (NEW.snapshot_kind = 'price' AND NOT EXISTS
                   (SELECT 1 FROM uw_price_snapshots WHERE id = NEW.snapshot_id))
               OR (NEW.snapshot_kind = 'fx' AND NOT EXISTS
                   (SELECT 1 FROM uw_fx_snapshots WHERE id = NEW.snapshot_id))
               OR (NEW.snapshot_kind = 'capital_structure' AND NOT EXISTS
                   (SELECT 1 FROM uw_capital_structure_snapshots WHERE id = NEW.snapshot_id))
               OR (NEW.snapshot_kind = 'security_rights' AND NOT EXISTS
                   (SELECT 1 FROM uw_security_rights_versions WHERE id = NEW.snapshot_id)) THEN
                RAISE EXCEPTION 'invalid market capture snapshot kind/role reference';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(f"""
        CREATE TRIGGER validate_{_TABLE}_insert
        BEFORE INSERT ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION validate_market_capture_reference()
    """)


def _drop_capture_reference_trigger(dialect: str) -> None:
    suffix = f" ON {_TABLE}" if dialect == "postgresql" else ""
    op.execute(f"DROP TRIGGER IF EXISTS validate_{_TABLE}_insert{suffix};")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS validate_market_capture_reference();")


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
            acquired_at = _utc(acquired_at)
            authenticated_available_at = item["authenticated_available_at"]
            if isinstance(authenticated_available_at, str):
                authenticated_available_at = datetime.fromisoformat(
                    authenticated_available_at
                )
            authenticated_available_at = _utc(authenticated_available_at)
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
    for _name, table, _columns in _BUSINESS_UNIQUE_INDEXES:
        op.add_column(
            table,
            sa.Column(
                "legacy_business_conflict",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
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
    _grandfather_legacy_business_conflicts(dialect)
    for name, table, columns in _BUSINESS_UNIQUE_INDEXES:
        op.execute(
            f"CREATE UNIQUE INDEX {name} ON {table} ({', '.join(columns)}) "
            "WHERE legacy_business_conflict = false"
        )
    _install_legacy_conflict_insert_guards(dialect)
    _install_capture_reference_trigger(dialect)
    _install_immutable_triggers(dialect)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect)
    _drop_capture_reference_trigger(dialect)
    _drop_legacy_conflict_insert_guards(dialect)
    for name, table, _columns in reversed(_BUSINESS_UNIQUE_INDEXES):
        op.drop_index(name, table_name=table)
    for _name, table, _columns in reversed(_BUSINESS_UNIQUE_INDEXES):
        op.drop_column(table, "legacy_business_conflict")
    op.drop_index("ix_uw_market_capture_snapshot", table_name=_TABLE)
    op.drop_table(_TABLE)
