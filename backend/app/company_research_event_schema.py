"""Stable database-level authentication for company-research event history.

This module is intentionally ORM-free because Alembic 0070 and unmanaged
database adoption must authenticate the same durable rows and trigger bodies.
Its canonical hash and trigger contracts are migration inputs; change them only
with a new schema revision and matching golden tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import re
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection


EVENTS_TABLE = "uw_company_research_events"
PREPARATIONS_TABLE = "uw_company_research_preparations"
UPDATE_TRIGGER = f"no_update_{EVENTS_TABLE}"
DELETE_TRIGGER = f"no_delete_{EVENTS_TABLE}"
POSTGRES_FUNCTION = "reject_company_research_event_mutation"
_TRIGGER_ERROR = "immutable company research table is append-only"
_SPACE = re.compile(r"\s+")


class CompanyResearchEventSchemaError(RuntimeError):
    """Durable event history or its database immutability is invalid."""


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
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        ) from exc


def _utc(value: object) -> datetime:
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        ) from exc
    if not isinstance(value, datetime):
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        )
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value.utcoffset() is None:
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        )
    return value.astimezone(UTC)


def _payload(value: object) -> dict[str, object]:
    try:
        if isinstance(value, str):
            value = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        ) from exc
    if not isinstance(value, Mapping):
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        )
    return dict(value)


def _event_hash(row: Mapping[str, object]) -> str:
    version = row["hash_version"]
    if type(version) is not int or version not in {1, 2}:
        raise CompanyResearchEventSchemaError(
            "cannot authenticate company research event history"
        )
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


def authenticate_company_event_history(connection: Connection) -> None:
    preparation_ids = {
        _uuid(row[0])
        for row in connection.execute(
            sa.text(f"SELECT id FROM {PREPARATIONS_TABLE}")
        )
    }
    rows = connection.execute(
        sa.text(
            f"SELECT id, preparation_id, sequence, hash_version, "
            f"previous_event_hash, event_type, payload, content_hash, created_at "
            f"FROM {EVENTS_TABLE} ORDER BY preparation_id, sequence"
        )
    ).mappings()
    active_preparation: str | None = None
    expected_sequence = 1
    expected_previous_hash: str | None = None
    previous_created_at: datetime | None = None
    v2_started = False
    for row in rows:
        preparation_id = _uuid(row["preparation_id"])
        if preparation_id not in preparation_ids:
            raise CompanyResearchEventSchemaError(
                "cannot authenticate company research event history"
            )
        if preparation_id != active_preparation:
            active_preparation = preparation_id
            expected_sequence = 1
            expected_previous_hash = None
            previous_created_at = None
            v2_started = False
        created_at = _utc(row["created_at"])
        version = row["hash_version"]
        if (
            type(row["sequence"]) is not int
            or row["sequence"] != expected_sequence
            or row["previous_event_hash"] != expected_previous_hash
            or row["content_hash"] != _event_hash(row)
            or (version == 1 and v2_started)
            or (
                previous_created_at is not None
                and created_at < previous_created_at
            )
        ):
            raise CompanyResearchEventSchemaError(
                "cannot authenticate company research event history"
            )
        expected_sequence += 1
        expected_previous_hash = row["content_hash"]
        previous_created_at = created_at
        v2_started = v2_started or version == 2


def _sqlite_trigger_sql(*, operation: str) -> str:
    trigger = UPDATE_TRIGGER if operation == "UPDATE" else DELETE_TRIGGER
    return (
        f"CREATE TRIGGER {trigger} BEFORE {operation} ON {EVENTS_TABLE} "
        f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
    )


def _normalized_sql(value: str) -> str:
    return _SPACE.sub(" ", value.strip().rstrip(";")).lower()


def install_canonical_company_event_triggers(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        for trigger in (UPDATE_TRIGGER, DELETE_TRIGGER):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.exec_driver_sql(_sqlite_trigger_sql(operation="UPDATE"))
        connection.exec_driver_sql(_sqlite_trigger_sql(operation="DELETE"))
        return
    if connection.dialect.name != "postgresql":
        raise CompanyResearchEventSchemaError(
            "company research event immutability is unsupported"
        )
    for trigger in (UPDATE_TRIGGER, DELETE_TRIGGER):
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {trigger} ON {EVENTS_TABLE}"
        )
    connection.exec_driver_sql(
        f"""
        CREATE OR REPLACE FUNCTION {POSTGRES_FUNCTION}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{_TRIGGER_ERROR}';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for operation, trigger in (
        ("UPDATE", UPDATE_TRIGGER),
        ("DELETE", DELETE_TRIGGER),
    ):
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger} BEFORE {operation} ON {EVENTS_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {POSTGRES_FUNCTION}()"
        )


def _verify_trigger_behavior(connection: Connection) -> None:
    event_id = connection.scalar(sa.text(f"SELECT id FROM {EVENTS_TABLE} LIMIT 1"))
    if event_id is None:
        return
    savepoint = connection.begin_nested()
    try:
        connection.execute(
            sa.text(
                f"UPDATE {EVENTS_TABLE} SET event_type = event_type WHERE id = :id"
            ),
            {"id": event_id},
        )
    except sa.exc.DBAPIError as exc:
        savepoint.rollback()
        if _TRIGGER_ERROR not in str(exc):
            raise CompanyResearchEventSchemaError(
                "company research event immutability behavior is invalid"
            ) from exc
        return
    savepoint.rollback()
    raise CompanyResearchEventSchemaError(
        "company research event immutability behavior is invalid"
    )


def verify_canonical_company_event_triggers(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        actual = {
            row.name: row.sql
            for row in connection.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = :table_name"
                ),
                {"table_name": EVENTS_TABLE},
            )
        }
        expected = {
            UPDATE_TRIGGER: _sqlite_trigger_sql(operation="UPDATE"),
            DELETE_TRIGGER: _sqlite_trigger_sql(operation="DELETE"),
        }
        if set(actual) != set(expected) or any(
            not isinstance(actual[name], str)
            or _normalized_sql(actual[name]) != _normalized_sql(sql)
            for name, sql in expected.items()
        ):
            raise CompanyResearchEventSchemaError(
                "company research event immutability definition is invalid"
            )
    elif connection.dialect.name == "postgresql":
        rows = tuple(
            connection.execute(
                sa.text(
                    "SELECT t.tgname, t.tgenabled, p.proname, p.prosrc, "
                    "p.oid = to_regprocedure(:function_signature) "
                    "AS expected_function, "
                    "pg_get_triggerdef(t.oid) AS definition "
                    "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                    "WHERE t.tgrelid = CAST(:table_name AS regclass) "
                    "AND NOT t.tgisinternal"
                ),
                {
                    "table_name": EVENTS_TABLE,
                    "function_signature": f"{POSTGRES_FUNCTION}()",
                },
            ).mappings()
        )
        by_name = {row["tgname"]: row for row in rows}
        expected_body = _normalized_sql(
            f"BEGIN RAISE EXCEPTION '{_TRIGGER_ERROR}'; END;"
        )
        if set(by_name) != {UPDATE_TRIGGER, DELETE_TRIGGER}:
            raise CompanyResearchEventSchemaError(
                "company research event immutability definition is invalid"
            )
        for operation, trigger in (
            ("UPDATE", UPDATE_TRIGGER),
            ("DELETE", DELETE_TRIGGER),
        ):
            row = by_name[trigger]
            if (
                row["tgenabled"] != "O"
                or row["proname"] != POSTGRES_FUNCTION
                or not row["expected_function"]
                or _normalized_sql(row["prosrc"]) != expected_body
                or f"BEFORE {operation}" not in row["definition"].upper()
            ):
                raise CompanyResearchEventSchemaError(
                    "company research event immutability definition is invalid"
                )
    else:
        raise CompanyResearchEventSchemaError(
            "company research event immutability is unsupported"
        )
    _verify_trigger_behavior(connection)


def repair_company_event_schema(connection: Connection) -> None:
    authenticate_company_event_history(connection)
    install_canonical_company_event_triggers(connection)
    verify_canonical_company_event_triggers(connection)
