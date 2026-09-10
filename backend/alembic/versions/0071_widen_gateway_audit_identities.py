"""Widen persisted audit identities for Gateway subjects.

Revision ID: 0071
Revises: 0070
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_AUDIT_IDENTITY_LENGTH = 128
_GATEWAY_AUDIT_IDENTITY_LENGTH = 256
_AUDIT_COLUMNS = (
    ("research_cases", "created_by"),
    ("theses", "created_by"),
    ("event_research_scope_versions", "changed_by"),
    ("source_contracts", "declared_by"),
    ("document_upload_artifacts", "uploaded_by"),
    ("case_tenant_admissions", "admitted_by"),
)
_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER = "trg_ai_assessments_protocol_scope"
_SQLITE_AUDIT_IDENTITY_SAVEPOINT = "audit_identity_resize"
_IRREVERSIBLE_AUDIT_DATA_ERROR = (
    "cannot downgrade 0071: immutable audit data exceeds 128 characters"
)
_SQLITE_FOREIGN_KEY_CHECK_ERROR = (
    "SQLite audit identity resize introduced foreign key violations"
)
_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_ERROR = (
    "SQLite assessment protocol trigger is missing or noncanonical"
)
# SHA-256 of the 0051 SQLite trigger DDL after quote-safe ASCII normalization.
# This freezes the 0070 contract without importing mutable application metadata.
_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_SHA256 = (
    "af7b328f12148d8aeb80d570af3d5086febe2c4a93e5962b4a57b7868224ff13"
)


def _audit_columns_exceeding_legacy_length() -> tuple[str, ...]:
    bind = op.get_bind()
    offending_columns = []
    for table_name, column_name in _AUDIT_COLUMNS:
        unsafe_value_predicate = f"length({column_name}) > :max_length"
        if bind.dialect.name == "sqlite":
            # SQLite's length(TEXT) stops at the first NUL byte.  Historical
            # data can bypass application validation, so inspect for that byte
            # explicitly before narrowing immutable audit columns.
            unsafe_value_predicate = (
                f"{unsafe_value_predicate} "
                f"OR instr({column_name}, char(0)) > 0"
            )
        has_overlong_value = bind.execute(
            sa.text(
                f"SELECT 1 FROM {table_name} "
                f"WHERE {unsafe_value_predicate} LIMIT 1"
            ),
            {"max_length": _LEGACY_AUDIT_IDENTITY_LENGTH},
        ).scalar_one_or_none()
        if has_overlong_value is not None:
            offending_columns.append(f"{table_name}.{column_name}")
    return tuple(offending_columns)


def _refuse_audit_data_truncation() -> None:
    offending_columns = _audit_columns_exceeding_legacy_length()
    if offending_columns:
        columns = ", ".join(offending_columns)
        raise RuntimeError(
            f"{_IRREVERSIBLE_AUDIT_DATA_ERROR} in {columns}; "
            "refusing to truncate immutable audit history"
        )


def _sqlite_foreign_keys_enabled(bind) -> bool:
    return bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one())


def _set_sqlite_foreign_keys(bind, enabled: bool) -> None:
    setting = "ON" if enabled else "OFF"
    bind.exec_driver_sql(f"PRAGMA foreign_keys={setting}")
    if _sqlite_foreign_keys_enabled(bind) is not enabled:
        raise RuntimeError(
            f"could not set SQLite foreign_keys={setting} for audit identity resize"
        )


def _sqlite_foreign_key_violations(bind) -> frozenset[tuple[object, ...]]:
    return frozenset(
        tuple(row) for row in bind.exec_driver_sql("PRAGMA foreign_key_check")
    )


def _normalized_sqlite_ddl(value: str) -> str:
    """Normalize formatting without changing quoted SQL literals or identifiers."""
    normalized: list[str] = []
    quote_end: str | None = None
    pending_space = False
    final_character_is_unquoted = False
    index = 0
    while index < len(value):
        character = value[index]
        if quote_end is not None:
            normalized.append(character)
            if quote_end == "]":
                if character == "]":
                    quote_end = None
            elif character == quote_end:
                if index + 1 < len(value) and value[index + 1] == quote_end:
                    normalized.append(value[index + 1])
                    index += 2
                    continue
                quote_end = None
            index += 1
            continue

        if character in " \t\n\r\f\v":
            if normalized:
                pending_space = True
            index += 1
            continue
        if pending_space:
            normalized.append(" ")
            pending_space = False

        if character in ("'", '"', "`"):
            normalized.append(character)
            quote_end = character
            final_character_is_unquoted = False
        elif character == "[":
            normalized.append(character)
            quote_end = "]"
            final_character_is_unquoted = False
        else:
            normalized.append(
                character.lower() if "A" <= character <= "Z" else character
            )
            final_character_is_unquoted = True
        index += 1

    result = "".join(normalized)
    if final_character_is_unquoted and result.endswith(";"):
        return result[:-1]
    return result


def _canonical_sqlite_assessment_protocol_trigger_sql(bind) -> str:
    trigger_sql = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = :trigger_name"
        ),
        {"trigger_name": _SQLITE_ASSESSMENT_PROTOCOL_TRIGGER},
    ).scalar_one_or_none()
    if (
        not isinstance(trigger_sql, str)
        or hashlib.sha256(
            _normalized_sqlite_ddl(trigger_sql).encode("utf-8")
        ).hexdigest()
        != _SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_SHA256
    ):
        raise RuntimeError(_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_ERROR)
    return trigger_sql


def _set_audit_identity_length(length: int, existing_length: int) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        trigger_sql = _canonical_sqlite_assessment_protocol_trigger_sql(bind)
        foreign_keys_were_enabled = _sqlite_foreign_keys_enabled(bind)
        foreign_key_violations_before = _sqlite_foreign_key_violations(bind)
        # SQLite batch migration rebuilds tables by dropping the originals.
        # That cannot succeed while a child table enforces a foreign key to one
        # of the originals, so suspend enforcement only for this rebuild and
        # restore the caller's setting even on failure.
        if foreign_keys_were_enabled:
            _set_sqlite_foreign_keys(bind, False)
        savepoint_started = False
        try:
            # pysqlite treats DDL outside an explicit transaction as immediately
            # committed.  Keep the trigger drop and every batch rebuild inside one
            # savepoint so a failed rebuild restores the installed trigger when the
            # savepoint rolls back.
            bind.exec_driver_sql(
                f"SAVEPOINT {_SQLITE_AUDIT_IDENTITY_SAVEPOINT}"
            )
            savepoint_started = True
            # The trigger's protocol validation joins theses.  SQLite batch
            # rebuilding drops that table briefly, so preserve and restore the
            # frozen canonical definition rather than weakening the guard.
            op.execute(
                f"DROP TRIGGER IF EXISTS {_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER}"
            )
            for table_name, column_name in _AUDIT_COLUMNS:
                with op.batch_alter_table(table_name) as batch:
                    batch.alter_column(
                        column_name,
                        existing_type=sa.String(length=existing_length),
                        type_=sa.String(length=length),
                        existing_nullable=False,
                    )
            op.execute(trigger_sql)
            if _sqlite_foreign_key_violations(bind) != foreign_key_violations_before:
                raise RuntimeError(_SQLITE_FOREIGN_KEY_CHECK_ERROR)
        except BaseException:
            if savepoint_started:
                bind.exec_driver_sql(
                    f"ROLLBACK TO SAVEPOINT {_SQLITE_AUDIT_IDENTITY_SAVEPOINT}"
                )
                bind.exec_driver_sql(
                    f"RELEASE SAVEPOINT {_SQLITE_AUDIT_IDENTITY_SAVEPOINT}"
                )
            raise
        else:
            bind.exec_driver_sql(
                f"RELEASE SAVEPOINT {_SQLITE_AUDIT_IDENTITY_SAVEPOINT}"
            )
        finally:
            if foreign_keys_were_enabled:
                _set_sqlite_foreign_keys(bind, True)
        return

    for table_name, column_name in _AUDIT_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.String(length=existing_length),
            type_=sa.String(length=length),
            existing_nullable=False,
        )


def upgrade() -> None:
    _set_audit_identity_length(
        _GATEWAY_AUDIT_IDENTITY_LENGTH,
        _LEGACY_AUDIT_IDENTITY_LENGTH,
    )


def downgrade() -> None:
    _refuse_audit_data_truncation()
    _set_audit_identity_length(
        _LEGACY_AUDIT_IDENTITY_LENGTH,
        _GATEWAY_AUDIT_IDENTITY_LENGTH,
    )
