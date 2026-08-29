"""Repair company event immutability and index worker candidates.

Revision ID: 0070
Revises: 0069

The authentication code is deliberately frozen in this migration.  Alembic
history must remain runnable even if the application implementation changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Union
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None

_EVENTS = "uw_company_research_events"
_PREPARATIONS = "uw_company_research_preparations"
_UPDATE_TRIGGER = f"no_update_{_EVENTS}"
_DELETE_TRIGGER = f"no_delete_{_EVENTS}"
_POSTGRES_FUNCTION = "reject_company_research_event_mutation"
_TRIGGER_ERROR = "immutable company research table is append-only"
_WORKER_INDEX = "ix_jobs_company_research_worker_candidates"
_WORKER_PREDICATE = (
    "kind = 'prepare_company_research' AND "
    "target_type = 'company_research_preparation' AND research_case_id IS NULL"
)
_SPACE = re.compile(r"\s+")


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _uuid(value: object) -> str:
    return str(value if isinstance(value, UUID) else UUID(str(value)))


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise RuntimeError("cannot authenticate company research event history")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value.utcoffset() is None:
        raise RuntimeError("cannot authenticate company research event history")
    return value.astimezone(UTC)


def _payload(value: object) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise RuntimeError("cannot authenticate company research event history")
    return dict(value)


def _event_hash(row: Mapping[str, object]) -> str:
    version = row["hash_version"]
    if type(version) is not int or version not in {1, 2}:
        raise RuntimeError("cannot authenticate company research event history")
    value: dict[str, object] = {
        "schema_version": f"company-research-event.v{version}",
        "preparation_id": _uuid(row["preparation_id"]),
        "sequence": row["sequence"],
        "previous_event_hash": row["previous_event_hash"],
        "event_type": row["event_type"],
        "payload": _payload(row["payload"]),
    }
    if version == 2:
        value["created_at"] = _utc(row["created_at"]).isoformat()
    return _canonical_hash(value)


def _authenticate_history() -> None:
    bind = op.get_bind()
    preparation_ids = {
        _uuid(row[0])
        for row in bind.execute(sa.text(f"SELECT id FROM {_PREPARATIONS}"))
    }
    rows = bind.execute(
        sa.text(
            f"SELECT preparation_id, sequence, hash_version, previous_event_hash, "
            f"event_type, payload, content_hash, created_at FROM {_EVENTS} "
            "ORDER BY preparation_id, sequence"
        )
    ).mappings()
    active_preparation: str | None = None
    expected_sequence = 1
    expected_previous_hash: str | None = None
    previous_created_at: datetime | None = None
    v2_started = False
    try:
        for row in rows:
            preparation_id = _uuid(row["preparation_id"])
            if preparation_id != active_preparation:
                active_preparation = preparation_id
                expected_sequence = 1
                expected_previous_hash = None
                previous_created_at = None
                v2_started = False
            created_at = _utc(row["created_at"])
            version = row["hash_version"]
            if (
                preparation_id not in preparation_ids
                or type(row["sequence"]) is not int
                or row["sequence"] != expected_sequence
                or row["previous_event_hash"] != expected_previous_hash
                or row["content_hash"] != _event_hash(row)
                or (version == 1 and v2_started)
                or (
                    previous_created_at is not None
                    and created_at < previous_created_at
                )
            ):
                raise RuntimeError(
                    "cannot authenticate company research event history"
                )
            expected_sequence += 1
            expected_previous_hash = row["content_hash"]
            previous_created_at = created_at
            v2_started = v2_started or version == 2
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "cannot authenticate company research event history"
        ) from exc


def _drop_triggers(dialect_name: str) -> None:
    suffix = "" if dialect_name == "sqlite" else f" ON {_EVENTS}"
    op.execute(f"DROP TRIGGER IF EXISTS {_UPDATE_TRIGGER}{suffix}")
    op.execute(f"DROP TRIGGER IF EXISTS {_DELETE_TRIGGER}{suffix}")


def _install_triggers(dialect_name: str) -> None:
    _drop_triggers(dialect_name)
    if dialect_name == "sqlite":
        op.execute(
            f"CREATE TRIGGER {_UPDATE_TRIGGER} BEFORE UPDATE ON {_EVENTS} "
            f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
        )
        op.execute(
            f"CREATE TRIGGER {_DELETE_TRIGGER} BEFORE DELETE ON {_EVENTS} "
            f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
        )
        return
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_POSTGRES_FUNCTION}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{_TRIGGER_ERROR}';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for operation, trigger in (
        ("UPDATE", _UPDATE_TRIGGER),
        ("DELETE", _DELETE_TRIGGER),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE {operation} ON {_EVENTS} "
            f"FOR EACH ROW EXECUTE FUNCTION {_POSTGRES_FUNCTION}()"
        )


def _normalized_sql(value: str) -> str:
    return _SPACE.sub(" ", value.strip().rstrip(";")).lower()


def _verify_triggers(dialect_name: str) -> None:
    bind = op.get_bind()
    if dialect_name == "sqlite":
        actual = {
            row.name: row.sql
            for row in bind.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = :table_name"
                ),
                {"table_name": _EVENTS},
            )
        }
        expected = {
            _UPDATE_TRIGGER: (
                f"CREATE TRIGGER {_UPDATE_TRIGGER} BEFORE UPDATE ON {_EVENTS} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            ),
            _DELETE_TRIGGER: (
                f"CREATE TRIGGER {_DELETE_TRIGGER} BEFORE DELETE ON {_EVENTS} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            ),
        }
        if set(actual) != set(expected) or any(
            not isinstance(actual[name], str)
            or _normalized_sql(actual[name]) != _normalized_sql(sql)
            for name, sql in expected.items()
        ):
            raise RuntimeError("company research event immutability is invalid")
    else:
        rows = tuple(
            bind.execute(
                sa.text(
                    "SELECT t.tgname, t.tgenabled, p.proname, p.prosrc, "
                    "p.oid = to_regprocedure(:function_signature) "
                    "AS expected_function, "
                    "pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t "
                    "JOIN pg_proc p ON p.oid = t.tgfoid "
                    "WHERE t.tgrelid = CAST(:table_name AS regclass) "
                    "AND NOT t.tgisinternal"
                ),
                {
                    "table_name": _EVENTS,
                    "function_signature": f"{_POSTGRES_FUNCTION}()",
                },
            ).mappings()
        )
        by_name = {row["tgname"]: row for row in rows}
        expected_body = _normalized_sql(
            f"BEGIN RAISE EXCEPTION '{_TRIGGER_ERROR}'; END;"
        )
        if set(by_name) != {_UPDATE_TRIGGER, _DELETE_TRIGGER}:
            raise RuntimeError("company research event immutability is invalid")
        for operation, trigger in (
            ("UPDATE", _UPDATE_TRIGGER),
            ("DELETE", _DELETE_TRIGGER),
        ):
            row = by_name[trigger]
            if (
                row["tgenabled"] != "O"
                or row["proname"] != _POSTGRES_FUNCTION
                or not row["expected_function"]
                or _normalized_sql(row["prosrc"]) != expected_body
                or f"BEFORE {operation}" not in row["definition"].upper()
            ):
                raise RuntimeError(
                    "company research event immutability is invalid"
                )
    event_id = bind.scalar(sa.text(f"SELECT id FROM {_EVENTS} LIMIT 1"))
    if event_id is None:
        return
    savepoint = bind.begin_nested()
    try:
        bind.execute(
            sa.text(
                f"UPDATE {_EVENTS} SET event_type = event_type WHERE id = :id"
            ),
            {"id": event_id},
        )
    except sa.exc.DBAPIError as exc:
        savepoint.rollback()
        if _TRIGGER_ERROR not in str(exc):
            raise RuntimeError(
                "company research event immutability behavior is invalid"
            ) from exc
        return
    savepoint.rollback()
    raise RuntimeError("company research event immutability behavior is invalid")


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _authenticate_history()
    _install_triggers(dialect_name)
    _verify_triggers(dialect_name)
    op.create_index(
        _WORKER_INDEX,
        "jobs",
        ["status", "created_at", "id"],
        unique=False,
        sqlite_where=sa.text(_WORKER_PREDICATE),
        postgresql_where=sa.text(_WORKER_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(_WORKER_INDEX, table_name="jobs")
