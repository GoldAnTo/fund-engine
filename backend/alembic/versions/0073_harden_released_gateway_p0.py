"""Harden the released FundClaw Gateway 0072 schema without rewriting it.

Revision ID: 0073
Revises: 0072

0072 is a released append-only contract.  This successor authenticates that
exact historical boundary before any DDL, rejects legacy rows that cannot meet
the hardened safe-projection contract, and then applies the additive guards.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROLE_KEYS = (
    "scope_identity",
    "sources_evidence",
    "analysis_counter_evidence",
    "compilation_checks",
)
_ROLE_KEY_CHECK = "role_key IN (" + ", ".join(f"'{key}'" for key in _ROLE_KEYS) + ")"
_ROLE_STATUS_CHECK = (
    "status IN ('queued', 'running', 'blocked', 'completed', 'failed', 'cancelled')"
)
_IMMUTABLE_TABLES = (
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_events",
    "gateway_commands",
)
_GATEWAY_TABLES = (
    "research_conversations",
    "gateway_idempotency_requests",
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_runs",
    "role_events",
    "gateway_commands",
)
_TRIGGER_ERROR = "immutable gateway table is append-only"
_ARTIFACT_REFERENCE_KINDS = (
    "decision",
    "document_version",
    "draft",
    "evidence_link",
    "research_case",
    "research_run",
    "review",
    "source_contract",
    "validation",
)
_MAX_GATEWAY_ARTIFACT_REFERENCES = 32
_MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES = 4096
_SHA256_DIGEST_LENGTH = 64
_SHA256_HEX_ALPHABET = "0123456789abcdef"
_ROLE_EVENT_SOURCE_KEY_PREFIX = "sha256:"
_ROLE_EVENT_SOURCE_KEY_LENGTH = len(_ROLE_EVENT_SOURCE_KEY_PREFIX) + 64
_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH = 96
_SAFE_ROLE_EVENT_DISPLAY_TEXTS = (
    "Approved evidence available",
    "Candidate recorded",
    "Command accepted",
    "Command rejected",
    "Draft artifact available",
    "Evidence gap recorded",
    "Identity decision recorded",
    "Role completed",
    "Role failed",
    "Role is blocked",
    "Role progress recorded",
    "Role queued",
    "Role started",
    "Source rejected by policy",
    "Validation completed",
    "Waiting for approved source",
)
_SAFE_ROLE_EVENT_REASON_CODES = (
    "authorization_required",
    "command_rejected",
    "cutoff_unavailable",
    "evidence_gap",
    "native_execution_failed",
    "source_policy_blocked",
    "source_unavailable",
    "unsupported_in_gateway_p0",
    "validation_failed",
)
_SAFE_ROLE_EVENT_DISPLAY_TEXT_CHECK = (
    "display_text IS NULL OR (CAST(display_text AS TEXT) = display_text AND "
    "display_text IN ("
    + ", ".join(f"'{text}'" for text in _SAFE_ROLE_EVENT_DISPLAY_TEXTS)
    + "))"
)
_SAFE_ROLE_EVENT_REASON_CODE_CHECK = (
    "reason_code IS NULL OR (CAST(reason_code AS TEXT) = reason_code AND "
    "reason_code IN ("
    + ", ".join(f"'{reason}'" for reason in _SAFE_ROLE_EVENT_REASON_CODES)
    + "))"
)
_SAFE_GATEWAY_REASON_CODE_CHECK = _SAFE_ROLE_EVENT_REASON_CODE_CHECK
_ARTIFACT_REFERENCE_VALIDATORS = (
    (
        "research_run_specs",
        "input_artifact_refs_json",
        "validate_insert_research_run_specs_input_artifact_refs",
    ),
    (
        "role_events",
        "artifact_refs_json",
        "validate_insert_role_events_artifact_refs",
    ),
)
_SQLITE_REPLACE_GUARDS = (
    (
        "research_messages",
        "reject_replace_research_messages",
        "id = NEW.id OR (conversation_id = NEW.conversation_id AND sequence = NEW.sequence)",
    ),
    (
        "research_intents",
        "reject_replace_research_intents",
        (
            "id = NEW.id OR (NEW.gateway_request_id IS NOT NULL "
            "AND gateway_request_id = NEW.gateway_request_id)"
        ),
    ),
    (
        "research_run_specs",
        "reject_replace_research_run_specs",
        (
            "id = NEW.id OR native_run_id = NEW.native_run_id "
            "OR (NEW.gateway_request_id IS NOT NULL "
            "AND gateway_request_id = NEW.gateway_request_id)"
        ),
    ),
    (
        "role_events",
        "reject_replace_role_events",
        (
            "id = NEW.id OR (conversation_id = NEW.conversation_id "
            "AND sequence = NEW.sequence) OR (run_spec_id = NEW.run_spec_id "
            "AND run_sequence = NEW.run_sequence) OR (run_spec_id = NEW.run_spec_id "
            "AND source_key = NEW.source_key)"
        ),
    ),
    (
        "gateway_commands",
        "reject_replace_gateway_commands",
        "id = NEW.id OR gateway_request_id = NEW.gateway_request_id",
    ),
)
_POSTGRESQL_ARTIFACT_REFERENCE_VALIDATOR = "validate_gateway_artifact_references"
_SQLITE_REBUILD_SAVEPOINT = "gateway_0073_hardening"
_POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256 = (
    "54768e2026d0b82b7b7a5ee019dc4520e3a894c4510a02e27ebbb2842a3885ed"
)
_LEGACY_SQLITE_TRIGGER_CONTRACTS = {
    "no_update_research_messages": (
        "research_messages",
        "2caabe1e5b7f604ad4bea7c7c15f7c885d1c6981271e2bc238d70899579b862e",
    ),
    "no_delete_research_messages": (
        "research_messages",
        "da686a4509b66ca53587fcc69058328371a7fea3b16a1db22628ab0efd094440",
    ),
    "no_update_research_intents": (
        "research_intents",
        "4617a38af9dbcb9b99900daaf13ffa9393cc36bf52286e21f20d1ee24c55dd46",
    ),
    "no_delete_research_intents": (
        "research_intents",
        "70c59c9f0899f07513f3fd55371543b3d5707cc3d02026608ce57369c82a2c6e",
    ),
    "no_update_research_run_specs": (
        "research_run_specs",
        "2d00de9f5773624170a1c7503c56d113e9873dd242ea5761d44a6741961d76ac",
    ),
    "no_delete_research_run_specs": (
        "research_run_specs",
        "a2d5fbf07812cfedfb520279a79d41a0d0b4f5e7e07f9bbcdfa7bfef9ad3007b",
    ),
    "no_update_role_events": (
        "role_events",
        "fc4d7f5b39001136d8d2516667957a16fb93e12253edf6f20db9e68dc49555b2",
    ),
    "no_delete_role_events": (
        "role_events",
        "73413257fb877350f48f6530cded721cbeae02c5ce2e6bb62769e8670c28fbad",
    ),
    "no_update_gateway_commands": (
        "gateway_commands",
        "bd6fc6bf8ded2d0547660313aeb1fbb4fe65c4720a95cde7e833b31c44bdf9c4",
    ),
    "no_delete_gateway_commands": (
        "gateway_commands",
        "34cec6a9634872e0d290c3f50f922d711aaec616c2f23b122dfe9c8de246d1fa",
    ),
}


def _normalized_sqlite_ddl(value: str) -> str:
    """Normalize SQL formatting without changing quoted text."""
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


def _sha256_digest_check(column_name: str) -> str:
    """Portable text-only lowercase SHA-256 constraint expression."""
    remainder = column_name
    for hex_character in _SHA256_HEX_ALPHABET:
        remainder = f"replace({remainder}, '{hex_character}', '')"
    return (
        f"CAST({column_name} AS TEXT) = {column_name} "
        f"AND length({column_name}) = {_SHA256_DIGEST_LENGTH} "
        f"AND length({remainder}) = 0"
    )


def _role_event_source_key_check() -> str:
    remainder = "substr(source_key, 8)"
    for hex_character in _SHA256_HEX_ALPHABET:
        remainder = f"replace({remainder}, '{hex_character}', '')"
    return (
        "CAST(source_key AS TEXT) = source_key "
        f"AND length(source_key) = {_ROLE_EVENT_SOURCE_KEY_LENGTH} "
        "AND substr(source_key, 1, 7) = 'sha256:' "
        f"AND length({remainder}) = 0"
    )


def _legacy_trigger_error() -> RuntimeError:
    return RuntimeError("legacy Gateway 0072 append-only trigger contract is not canonical")


def _require_legacy_sqlite_gateway_triggers(bind) -> None:
    parameters = {
        f"table_{index}": table_name for index, table_name in enumerate(_GATEWAY_TABLES)
    }
    placeholders = ", ".join(f":{name}" for name in parameters)
    trigger_rows = bind.execute(
        sa.text(
            "SELECT name, tbl_name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name IN ("
            f"{placeholders})"
        ),
        parameters,
    ).mappings()
    trigger_by_name = {
        row["name"]: (row["tbl_name"], row["sql"])
        for row in trigger_rows
        if isinstance(row["name"], str)
        and isinstance(row["tbl_name"], str)
        and isinstance(row["sql"], str)
    }
    if set(trigger_by_name) != set(_LEGACY_SQLITE_TRIGGER_CONTRACTS):
        raise _legacy_trigger_error()
    for trigger_name, (expected_table, expected_hash) in (
        _LEGACY_SQLITE_TRIGGER_CONTRACTS.items()
    ):
        actual_table, trigger_sql = trigger_by_name[trigger_name]
        if actual_table != expected_table:
            raise _legacy_trigger_error()
        if hashlib.sha256(
            _normalized_sqlite_ddl(trigger_sql).encode("utf-8")
        ).hexdigest() != expected_hash:
            raise _legacy_trigger_error()


def _require_legacy_postgresql_gateway_triggers(bind) -> None:
    schema_name = bind.scalar(sa.text("SELECT current_schema()"))
    if not isinstance(schema_name, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", schema_name
    ):
        raise _legacy_trigger_error()
    states = bind.execute(
        sa.text(
            "SELECT trigger_rel.relname AS table_name, trigger_rel.relowner AS table_owner, "
            "trigger_row.tgname AS trigger_name, trigger_row.tgenabled AS enabled, "
            "trigger_row.tgtype AS trigger_type, trigger_row.tgqual AS trigger_qual, "
            "trigger_row.tgattr <> ''::int2vector AS is_column_specific, "
            "pg_get_triggerdef(trigger_row.oid, true) AS trigger_definition, "
            "function_namespace.nspname AS function_schema, "
            "function_row.proname AS function_name, function_row.proowner AS function_owner, "
            "function_row.prosrc AS function_source, language.lanname AS function_language, "
            "pg_get_function_result(function_row.oid) AS function_result, "
            "pg_get_function_arguments(function_row.oid) AS function_arguments, "
            "encode(trigger_row.tgargs, 'escape') AS trigger_arguments "
            "FROM pg_trigger AS trigger_row "
            "JOIN pg_class AS trigger_rel ON trigger_rel.oid = trigger_row.tgrelid "
            "JOIN pg_namespace AS trigger_namespace "
            "ON trigger_namespace.oid = trigger_rel.relnamespace "
            "JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid "
            "JOIN pg_namespace AS function_namespace "
            "ON function_namespace.oid = function_row.pronamespace "
            "JOIN pg_language AS language ON language.oid = function_row.prolang "
            "WHERE NOT trigger_row.tgisinternal "
            "AND trigger_namespace.nspname = :schema_name "
            "AND trigger_rel.relname IN ('research_conversations', "
            "'gateway_idempotency_requests', 'research_messages', "
            "'research_intents', 'research_run_specs', 'role_runs', "
            "'role_events', 'gateway_commands')"
        ),
        {"schema_name": schema_name},
    ).mappings()
    expected = {
        (table_name, f"no_update_{table_name}"): 19
        for table_name in _IMMUTABLE_TABLES
    } | {
        (table_name, f"no_delete_{table_name}"): 11
        for table_name in _IMMUTABLE_TABLES
    }
    states_by_key: dict[tuple[str, str], object] = {}
    for state in states:
        table_name = state.get("table_name")
        trigger_name = state.get("trigger_name")
        key = (table_name, trigger_name)
        if (
            not isinstance(table_name, str)
            or not isinstance(trigger_name, str)
            or key not in expected
            or key in states_by_key
        ):
            raise _legacy_trigger_error()
        states_by_key[key] = state
    if set(states_by_key) != set(expected):
        raise _legacy_trigger_error()
    for (table_name, trigger_name), trigger_type in expected.items():
        state = states_by_key[(table_name, trigger_name)]
        assert hasattr(state, "get")
        operation = "update" if trigger_type == 19 else "delete"
        escaped_schema = re.escape(schema_name)
        definition_pattern = (
            rf"create trigger {re.escape(trigger_name)} before {operation} on "
            rf"(?:{escaped_schema}\.)?{re.escape(table_name)} for each row execute "
            rf"(?:function|procedure) (?:{escaped_schema}\.)?reject_mutable_ledger\(\)"
        )
        source = state.get("function_source")
        if not (
            state.get("table_name") == table_name
            and state.get("trigger_name") == trigger_name
            and state.get("enabled") == "O"
            and state.get("trigger_type") == trigger_type
            and state.get("trigger_qual") is None
            and state.get("is_column_specific") is False
            and isinstance(state.get("trigger_definition"), str)
            and re.fullmatch(
                definition_pattern,
                _normalized_sqlite_ddl(state["trigger_definition"]),
            )
            is not None
            and state.get("function_schema") == schema_name
            and state.get("function_name") == "reject_mutable_ledger"
            and state.get("function_language") == "plpgsql"
            and state.get("function_result") == "trigger"
            and state.get("function_arguments") == ""
            and state.get("trigger_arguments") == ""
            and isinstance(state.get("table_owner"), int)
            and state.get("table_owner") == state.get("function_owner")
            and isinstance(source, str)
            and hashlib.sha256(
                _normalized_sqlite_ddl(source).encode("utf-8")
            ).hexdigest()
            == _POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256
        ):
            raise _legacy_trigger_error()


def _require_legacy_gateway_triggers(bind) -> None:
    if bind.dialect.name == "sqlite":
        _require_legacy_sqlite_gateway_triggers(bind)
        return
    if bind.dialect.name == "postgresql":
        _require_legacy_postgresql_gateway_triggers(bind)
        return
    raise _legacy_trigger_error()


def _is_canonical_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _SHA256_DIGEST_LENGTH
        and all(character in _SHA256_HEX_ALPHABET for character in value)
    )


def _is_canonical_role_event_source_key(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _ROLE_EVENT_SOURCE_KEY_LENGTH
        and value.startswith(_ROLE_EVENT_SOURCE_KEY_PREFIX)
        and all(character in _SHA256_HEX_ALPHABET for character in value[7:])
    )


def _legacy_history_error(table_name: str, column_name: str) -> RuntimeError:
    return RuntimeError(
        "unsafe legacy Gateway history "
        f"in {table_name}.{column_name}; refusing to rewrite append-only rows"
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _is_canonical_uuid(value: object) -> bool:
    return bool(
        type(value) is str
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value,
        )
    )


def _artifact_references_are_safe(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        if len(value.encode("utf-8")) > _MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES:
            return False
        references = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if type(references) is not list or len(references) > _MAX_GATEWAY_ARTIFACT_REFERENCES:
        return False
    allowed_keys = {"kind", "id", "case_id", "locator_available"}
    for reference in references:
        if type(reference) is not dict or not 2 <= len(reference) <= 4:
            return False
        if set(reference) - allowed_keys or not {"kind", "id"}.issubset(reference):
            return False
        if reference["kind"] not in _ARTIFACT_REFERENCE_KINDS:
            return False
        if not _is_canonical_uuid(reference["id"]):
            return False
        if "case_id" in reference and not _is_canonical_uuid(reference["case_id"]):
            return False
        if "locator_available" in reference and type(reference["locator_available"]) is not bool:
            return False
    return True


def _raw_json_column(column_name: str, dialect_name: str) -> str:
    if dialect_name == "postgresql":
        return f"{column_name}::text AS {column_name}"
    return column_name


def _require_safe_legacy_gateway_history(bind) -> None:
    """Reject records that would need mutation or unsafe grandfathering."""
    for table_name, column_name in (
        ("gateway_idempotency_requests", "request_fingerprint"),
        ("research_messages", "input_sha256"),
    ):
        for row in bind.execute(
            sa.text(f"SELECT id, {column_name} FROM {table_name}")
        ).mappings():
            if not _is_canonical_sha256(row[column_name]):
                raise _legacy_history_error(table_name, column_name)

    for row in bind.execute(
        sa.text("SELECT id, input_sha256, reason_code FROM research_intents")
    ).mappings():
        if not _is_canonical_sha256(row["input_sha256"]):
            raise _legacy_history_error("research_intents", "input_sha256")
        if row["reason_code"] is not None and (
            type(row["reason_code"]) is not str
            or row["reason_code"] not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _legacy_history_error("research_intents", "reason_code")
    duplicate_intent_request = bind.scalar(
        sa.text(
            "SELECT gateway_request_id FROM research_intents "
            "WHERE gateway_request_id IS NOT NULL "
            "GROUP BY gateway_request_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    )
    if duplicate_intent_request is not None:
        raise _legacy_history_error("research_intents", "gateway_request_id")

    for row in bind.execute(
        sa.text(
            "SELECT id, reason_code, display_text, "
            f"{_raw_json_column('artifact_refs_json', bind.dialect.name)}, source_key "
            "FROM role_events"
        )
    ).mappings():
        display_text = row["display_text"]
        if display_text is not None and (
            type(display_text) is not str
            or len(display_text) > _MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH
            or display_text not in _SAFE_ROLE_EVENT_DISPLAY_TEXTS
        ):
            raise _legacy_history_error("role_events", "display_text")
        reason_code = row["reason_code"]
        if reason_code is not None and (
            type(reason_code) is not str
            or reason_code not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _legacy_history_error("role_events", "reason_code")
        if not _is_canonical_role_event_source_key(row["source_key"]):
            raise _legacy_history_error("role_events", "source_key")
        if not _artifact_references_are_safe(row["artifact_refs_json"]):
            raise _legacy_history_error("role_events", "artifact_refs_json")

    for row in bind.execute(
        sa.text(
            "SELECT id, "
            f"{_raw_json_column('input_artifact_refs_json', bind.dialect.name)} "
            "FROM research_run_specs"
        )
    ).mappings():
        if not _artifact_references_are_safe(row["input_artifact_refs_json"]):
            raise _legacy_history_error("research_run_specs", "input_artifact_refs_json")

    for row in bind.execute(
        sa.text("SELECT id, target_hash, target_version, reason_code FROM gateway_commands")
    ).mappings():
        if not _is_canonical_sha256(row["target_hash"]):
            raise _legacy_history_error("gateway_commands", "target_hash")
        if row["target_version"] is not None:
            raise _legacy_history_error("gateway_commands", "target_version")
        if row["reason_code"] is not None and (
            type(row["reason_code"]) is not str
            or row["reason_code"] not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _legacy_history_error("gateway_commands", "reason_code")

def _sqlite_artifact_reference_uuid_predicate(value: str) -> str:
    """Return a portable canonical-lowercase UUID check for SQLite JSON text."""
    non_hyphen_segments = (
        f"substr({value}, 1, 8) || substr({value}, 10, 4) || "
        f"substr({value}, 15, 4) || substr({value}, 20, 4) || "
        f"substr({value}, 25, 12)"
    )
    remainder = non_hyphen_segments
    for hex_character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{hex_character}', '')"
    return (
        f"length({value}) = 36 "
        f"AND lower({value}) = {value} "
        f"AND substr({value}, 9, 1) = '-' "
        f"AND substr({value}, 14, 1) = '-' "
        f"AND substr({value}, 19, 1) = '-' "
        f"AND substr({value}, 24, 1) = '-' "
        f"AND length({remainder}) = 0"
    )


def _sqlite_artifact_reference_uuid_predicate_for_path(
    document: str, path: str, value: str
) -> str:
    """Check JSON type before extracting UUID text to avoid reparsing text as JSON."""
    return (
        f"json_type({document}, '{path}') = 'text' "
        f"AND {_sqlite_artifact_reference_uuid_predicate(value)}"
    )


def _sqlite_artifact_reference_validator_sql(
    *,
    table_name: str,
    column_name: str,
    trigger_name: str,
) -> str:
    kind_values = ", ".join(f"'{kind}'" for kind in _ARTIFACT_REFERENCE_KINDS)
    allowed_key_values = "'kind', 'id', 'case_id', 'locator_available'"
    artifact_value = "artifact_ref.value"
    id_value = f"json_extract({artifact_value}, '$.id')"
    case_id_value = f"json_extract({artifact_value}, '$.case_id')"
    id_is_uuid = _sqlite_artifact_reference_uuid_predicate_for_path(
        artifact_value, "$.id", id_value
    )
    case_id_is_uuid = _sqlite_artifact_reference_uuid_predicate_for_path(
        artifact_value, "$.case_id", case_id_value
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON {table_name}
BEGIN
    SELECT CASE
        WHEN NEW.{column_name} IS NULL OR typeof(NEW.{column_name}) <> 'text'
        OR json_valid(NEW.{column_name}) <> 1
        THEN RAISE(ABORT, 'Gateway artifact references must be a valid JSON array')
    END;
    SELECT CASE
        WHEN json_type(NEW.{column_name}) <> 'array'
        THEN RAISE(ABORT, 'Gateway artifact references must be a JSON array')
    END;
    SELECT CASE
        WHEN length(CAST(NEW.{column_name} AS BLOB)) > {_MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES}
        THEN RAISE(ABORT, 'Gateway artifact references exceed the safe byte limit')
    END;
    SELECT CASE
        WHEN (SELECT COUNT(*) FROM json_each(NEW.{column_name})) > {_MAX_GATEWAY_ARTIFACT_REFERENCES}
        THEN RAISE(ABORT, 'Gateway artifact references exceed the safe item limit')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE artifact_ref.type <> 'object'
        )
        THEN RAISE(ABORT, 'Gateway artifact reference items must be objects')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE (SELECT COUNT(*) FROM json_each(artifact_ref.value)) < 2
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)) > 4
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)
                   WHERE key NOT IN ({allowed_key_values})) > 0
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)
                   WHERE key = 'kind') <> 1
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)
                   WHERE key = 'id') <> 1
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)
                   WHERE key = 'case_id') > 1
               OR (SELECT COUNT(*) FROM json_each(artifact_ref.value)
                   WHERE key = 'locator_available') > 1
        )
        THEN RAISE(ABORT, 'Gateway artifact reference keys must be exact and unique')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE json_type({artifact_value}, '$.kind') <> 'text'
               OR json_extract({artifact_value}, '$.kind') NOT IN ({kind_values})
        )
        THEN RAISE(ABORT, 'Gateway artifact reference kind is not allowed')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE NOT ({id_is_uuid})
        )
        THEN RAISE(ABORT, 'Gateway artifact reference id must be a canonical UUID')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE json_type({artifact_value}, '$.case_id') IS NOT NULL
              AND NOT ({case_id_is_uuid})
        )
        THEN RAISE(ABORT, 'Gateway artifact reference case_id must be a canonical UUID')
    END;
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.{column_name}) AS artifact_ref
            WHERE json_type({artifact_value}, '$.locator_available') IS NOT NULL
              AND json_type({artifact_value}, '$.locator_available') NOT IN ('true', 'false')
        )
        THEN RAISE(ABORT, 'Gateway artifact locator availability must be boolean')
    END;
END
"""


def _drop_artifact_reference_validation_triggers(dialect_name: str) -> None:
    for table_name, _column_name, trigger_name in _ARTIFACT_REFERENCE_VALIDATORS:
        suffix = "" if dialect_name == "sqlite" else f" ON {table_name}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}{suffix}")
    if dialect_name != "sqlite":
        op.execute(
            f"DROP FUNCTION IF EXISTS {_POSTGRESQL_ARTIFACT_REFERENCE_VALIDATOR}()"
        )


def _install_artifact_reference_validation_triggers(dialect_name: str) -> None:
    _drop_artifact_reference_validation_triggers(dialect_name)
    if dialect_name == "sqlite":
        for table_name, column_name, trigger_name in _ARTIFACT_REFERENCE_VALIDATORS:
            op.execute(
                _sqlite_artifact_reference_validator_sql(
                    table_name=table_name,
                    column_name=column_name,
                    trigger_name=trigger_name,
                )
            )
        return
    kind_values = ", ".join(f"'{kind}'" for kind in _ARTIFACT_REFERENCE_KINDS)
    op.execute(
        f"""
CREATE FUNCTION {_POSTGRESQL_ARTIFACT_REFERENCE_VALIDATOR}()
RETURNS trigger AS $$
DECLARE
    artifact_references json;
    artifact_reference json;
    artifact_key_count integer;
    artifact_distinct_key_count integer;
    artifact_keys_are_allowed boolean;
    artifact_kind_key_count integer;
    artifact_id_key_count integer;
    artifact_case_id_key_count integer;
    artifact_locator_key_count integer;
BEGIN
    IF TG_TABLE_NAME = 'research_run_specs' THEN
        artifact_references := to_json(NEW) -> 'input_artifact_refs_json';
    ELSIF TG_TABLE_NAME = 'role_events' THEN
        artifact_references := to_json(NEW) -> 'artifact_refs_json';
    ELSE
        RAISE EXCEPTION 'unexpected Gateway artifact validator table %', TG_TABLE_NAME;
    END IF;

    -- Keep this as JSON, not JSONB: json_each preserves duplicate object keys
    -- so a raw duplicate cannot hide a second payload value from validation.
    IF artifact_references IS NULL OR json_typeof(artifact_references) <> 'array' THEN
        RAISE EXCEPTION 'Gateway artifact references must be a JSON array';
    END IF;
    IF octet_length(artifact_references::text) > {_MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES} THEN
        RAISE EXCEPTION 'Gateway artifact references exceed the safe byte limit';
    END IF;
    IF json_array_length(artifact_references) > {_MAX_GATEWAY_ARTIFACT_REFERENCES} THEN
        RAISE EXCEPTION 'Gateway artifact references exceed the safe item limit';
    END IF;

    FOR artifact_reference IN SELECT value FROM json_array_elements(artifact_references)
    LOOP
        IF json_typeof(artifact_reference) <> 'object' THEN
            RAISE EXCEPTION 'Gateway artifact reference items must be objects';
        END IF;
        SELECT
            count(*),
            count(DISTINCT key),
            bool_and(key IN ('kind', 'id', 'case_id', 'locator_available')),
            count(*) FILTER (WHERE key = 'kind'),
            count(*) FILTER (WHERE key = 'id'),
            count(*) FILTER (WHERE key = 'case_id'),
            count(*) FILTER (WHERE key = 'locator_available')
        INTO
            artifact_key_count,
            artifact_distinct_key_count,
            artifact_keys_are_allowed,
            artifact_kind_key_count,
            artifact_id_key_count,
            artifact_case_id_key_count,
            artifact_locator_key_count
        FROM json_each(artifact_reference);
        IF artifact_key_count < 2
           OR artifact_key_count > 4
           OR artifact_key_count <> artifact_distinct_key_count
           OR artifact_keys_are_allowed IS NOT TRUE
           OR artifact_kind_key_count <> 1
           OR artifact_id_key_count <> 1
           OR artifact_case_id_key_count > 1
           OR artifact_locator_key_count > 1 THEN
            RAISE EXCEPTION 'Gateway artifact reference keys must be exact and unique';
        END IF;
        IF json_typeof(artifact_reference -> 'kind') <> 'string'
           OR artifact_reference ->> 'kind' NOT IN ({kind_values}) THEN
            RAISE EXCEPTION 'Gateway artifact reference kind is not allowed';
        END IF;
        IF json_typeof(artifact_reference -> 'id') <> 'string'
           OR artifact_reference ->> 'id' !~
              '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
            RAISE EXCEPTION 'Gateway artifact reference id must be a canonical UUID';
        END IF;
        IF json_typeof(artifact_reference -> 'case_id') IS NOT NULL
           AND (json_typeof(artifact_reference -> 'case_id') <> 'string'
                OR artifact_reference ->> 'case_id' !~
                   '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$') THEN
            RAISE EXCEPTION 'Gateway artifact reference case_id must be a canonical UUID';
        END IF;
        IF json_typeof(artifact_reference -> 'locator_available') IS NOT NULL
           AND json_typeof(artifact_reference -> 'locator_available') <> 'boolean' THEN
            RAISE EXCEPTION 'Gateway artifact locator availability must be boolean';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    for table_name, _column_name, trigger_name in _ARTIFACT_REFERENCE_VALIDATORS:
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION {_POSTGRESQL_ARTIFACT_REFERENCE_VALIDATOR}();"
        )


def _drop_sqlite_replace_guards(dialect_name: str) -> None:
    if dialect_name != "sqlite":
        return
    for _table_name, trigger_name, _predicate in _SQLITE_REPLACE_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _install_sqlite_replace_guards(dialect_name: str) -> None:
    _drop_sqlite_replace_guards(dialect_name)
    if dialect_name != "sqlite":
        return
    for table_name, trigger_name, predicate in _SQLITE_REPLACE_GUARDS:
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON {table_name} "
            f"WHEN EXISTS (SELECT 1 FROM {table_name} WHERE {predicate}) "
            f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
        )


def _drop_immutable_triggers(dialect_name: str) -> None:
    for table_name in _IMMUTABLE_TABLES:
        suffix = "" if dialect_name == "sqlite" else f" ON {table_name}"
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name}{suffix}")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name}{suffix}")


def _install_immutable_triggers(dialect_name: str) -> None:
    _drop_immutable_triggers(dialect_name)
    for table_name in _IMMUTABLE_TABLES:
        if dialect_name == "sqlite":
            op.execute(
                f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            )
            op.execute(
                f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            )
            continue
        op.execute(
            f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def _sqlite_foreign_keys_enabled(bind) -> bool:
    return bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one())


def _set_sqlite_foreign_keys(bind, enabled: bool) -> None:
    setting = "ON" if enabled else "OFF"
    bind.exec_driver_sql(f"PRAGMA foreign_keys={setting}")
    if _sqlite_foreign_keys_enabled(bind) is not enabled:
        raise RuntimeError(f"could not set SQLite foreign_keys={setting} for 0073")


def _sqlite_foreign_key_violations(bind) -> frozenset[tuple[object, ...]]:
    return frozenset(
        tuple(row) for row in bind.exec_driver_sql("PRAGMA foreign_key_check")
    )


def _rebuild_sqlite_hardened_tables() -> None:
    """Apply every 0073 table constraint through SQLite's recreate protocol."""
    with op.batch_alter_table("gateway_idempotency_requests", recreate="always") as batch:
        batch.create_check_constraint(
            "ck_gateway_idempotency_requests_request_fingerprint",
            _sha256_digest_check("request_fingerprint"),
        )
    with op.batch_alter_table("research_messages", recreate="always") as batch:
        batch.drop_constraint("ck_research_messages_input_sha256", type_="check")
        batch.create_check_constraint(
            "ck_research_messages_input_sha256",
            _sha256_digest_check("input_sha256"),
        )
    with op.batch_alter_table("research_intents", recreate="always") as batch:
        batch.drop_constraint("ck_research_intents_input_sha256", type_="check")
        batch.create_check_constraint(
            "ck_research_intents_input_sha256",
            _sha256_digest_check("input_sha256"),
        )
        batch.create_check_constraint(
            "ck_research_intents_reason_code_safe",
            _SAFE_GATEWAY_REASON_CODE_CHECK,
        )
        batch.create_unique_constraint(
            "uq_research_intents_gateway_request", ["gateway_request_id"]
        )
    with op.batch_alter_table("role_events", recreate="always") as batch:
        batch.alter_column(
            "reason_code",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
        batch.alter_column(
            "display_text",
            existing_type=sa.Text(),
            type_=sa.String(length=_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH),
            existing_nullable=True,
        )
        batch.alter_column(
            "source_key",
            existing_type=sa.String(length=256),
            type_=sa.String(length=_ROLE_EVENT_SOURCE_KEY_LENGTH),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "ck_role_events_display_text_bounded",
            "display_text IS NULL OR (CAST(display_text AS TEXT) = display_text "
            f"AND length(display_text) <= {_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH})",
        )
        batch.create_check_constraint(
            "ck_role_events_display_text_safe",
            _SAFE_ROLE_EVENT_DISPLAY_TEXT_CHECK,
        )
        batch.create_check_constraint(
            "ck_role_events_reason_code_safe",
            _SAFE_ROLE_EVENT_REASON_CODE_CHECK,
        )
        batch.create_check_constraint(
            "ck_role_events_source_key_digest",
            _role_event_source_key_check(),
        )
    with op.batch_alter_table("gateway_commands", recreate="always") as batch:
        batch.drop_constraint("ck_gateway_commands_target_hash", type_="check")
        batch.create_check_constraint(
            "ck_gateway_commands_target_hash",
            _sha256_digest_check("target_hash"),
        )
        batch.create_check_constraint(
            "ck_gateway_commands_target_version_absent", "target_version IS NULL"
        )
        batch.create_check_constraint(
            "ck_gateway_commands_reason_code_safe",
            _SAFE_GATEWAY_REASON_CODE_CHECK,
        )


def _rebuild_sqlite_legacy_tables() -> None:
    """Restore the original 0072 table contracts while retaining all rows."""
    with op.batch_alter_table("gateway_idempotency_requests", recreate="always") as batch:
        batch.drop_constraint(
            "ck_gateway_idempotency_requests_request_fingerprint", type_="check"
        )
    with op.batch_alter_table("research_messages", recreate="always") as batch:
        batch.drop_constraint("ck_research_messages_input_sha256", type_="check")
        batch.create_check_constraint(
            "ck_research_messages_input_sha256", "length(input_sha256) = 64"
        )
    with op.batch_alter_table("research_intents", recreate="always") as batch:
        batch.drop_constraint("ck_research_intents_input_sha256", type_="check")
        batch.drop_constraint("ck_research_intents_reason_code_safe", type_="check")
        batch.drop_constraint(
            "uq_research_intents_gateway_request", type_="unique"
        )
        batch.create_check_constraint(
            "ck_research_intents_input_sha256", "length(input_sha256) = 64"
        )
    with op.batch_alter_table("role_events", recreate="always") as batch:
        batch.drop_constraint("ck_role_events_display_text_bounded", type_="check")
        batch.drop_constraint("ck_role_events_display_text_safe", type_="check")
        batch.drop_constraint("ck_role_events_reason_code_safe", type_="check")
        batch.drop_constraint("ck_role_events_source_key_digest", type_="check")
        batch.alter_column(
            "reason_code",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
        batch.alter_column(
            "display_text",
            existing_type=sa.String(length=_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch.alter_column(
            "source_key",
            existing_type=sa.String(length=_ROLE_EVENT_SOURCE_KEY_LENGTH),
            type_=sa.String(length=256),
            existing_nullable=False,
        )
    with op.batch_alter_table("gateway_commands", recreate="always") as batch:
        batch.drop_constraint("ck_gateway_commands_target_hash", type_="check")
        batch.drop_constraint(
            "ck_gateway_commands_target_version_absent", type_="check"
        )
        batch.drop_constraint("ck_gateway_commands_reason_code_safe", type_="check")
        batch.create_check_constraint(
            "ck_gateway_commands_target_hash", "length(target_hash) = 64"
        )


def _run_sqlite_rebuild(*, harden: bool, manage_foreign_keys: bool = True) -> None:
    bind = op.get_bind()
    foreign_keys_were_enabled = (
        _sqlite_foreign_keys_enabled(bind) if manage_foreign_keys else False
    )
    foreign_key_violations_before = _sqlite_foreign_key_violations(bind)
    if foreign_keys_were_enabled:
        _set_sqlite_foreign_keys(bind, False)
    savepoint_started = False
    try:
        bind.exec_driver_sql(f"SAVEPOINT {_SQLITE_REBUILD_SAVEPOINT}")
        savepoint_started = True
        _drop_artifact_reference_validation_triggers("sqlite")
        _drop_sqlite_replace_guards("sqlite")
        _drop_immutable_triggers("sqlite")
        if harden:
            _rebuild_sqlite_hardened_tables()
            _install_artifact_reference_validation_triggers("sqlite")
            _install_sqlite_replace_guards("sqlite")
        else:
            _rebuild_sqlite_legacy_tables()
        _install_immutable_triggers("sqlite")
        if _sqlite_foreign_key_violations(bind) != foreign_key_violations_before:
            raise RuntimeError("0073 SQLite rebuild changed foreign key violations")
    except BaseException:
        if savepoint_started:
            bind.exec_driver_sql(f"ROLLBACK TO SAVEPOINT {_SQLITE_REBUILD_SAVEPOINT}")
            bind.exec_driver_sql(f"RELEASE SAVEPOINT {_SQLITE_REBUILD_SAVEPOINT}")
        raise
    else:
        bind.exec_driver_sql(f"RELEASE SAVEPOINT {_SQLITE_REBUILD_SAVEPOINT}")
    finally:
        if foreign_keys_were_enabled:
            _set_sqlite_foreign_keys(bind, True)


def _upgrade_sqlite_hardening() -> None:
    """Serialize legacy validation and 0073 DDL in SQLite's writer transaction.

    SQLite batch recreation must temporarily disable foreign-key enforcement: a
    recreated parent table is seen as deleted by existing child constraints
    even when the replacement has identical rows.  ``foreign_keys`` can only
    be changed outside a transaction, so turn it off before acquiring the
    immediate writer transaction.  Alembic's per-revision transaction stamps
    ``alembic_version`` and commits immediately after this callable returns;
    the migration connection is NullPool-scoped and closes then, so its
    connection-local pragma cannot leak to application connections.
    """
    bind = op.get_bind()
    if _sqlite_foreign_keys_enabled(bind):
        _set_sqlite_foreign_keys(bind, False)
    # This must be the first transactional statement.  It locks out another
    # writer before trigger authentication and legacy-history preflight, and
    # remains held through the Alembic version stamp.
    bind.exec_driver_sql("BEGIN IMMEDIATE")
    _require_legacy_gateway_triggers(bind)
    _require_safe_legacy_gateway_history(bind)
    _run_sqlite_rebuild(harden=True, manage_foreign_keys=False)


def _lock_postgresql_gateway_tables_for_hardening(bind) -> None:
    """Hold an exclusive lock across 0073 preflight, DDL, and version stamp."""
    for table_name in _GATEWAY_TABLES:
        bind.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))


def _harden_postgresql_tables() -> None:
    op.execute(
        "ALTER TABLE gateway_idempotency_requests ADD CONSTRAINT "
        "ck_gateway_idempotency_requests_request_fingerprint CHECK "
        f"({_sha256_digest_check('request_fingerprint')})"
    )
    op.execute(
        "ALTER TABLE research_messages DROP CONSTRAINT "
        "ck_research_messages_input_sha256"
    )
    op.execute(
        "ALTER TABLE research_messages ADD CONSTRAINT "
        "ck_research_messages_input_sha256 CHECK "
        f"({_sha256_digest_check('input_sha256')})"
    )
    op.execute(
        "ALTER TABLE research_intents DROP CONSTRAINT ck_research_intents_input_sha256"
    )
    op.execute(
        "ALTER TABLE research_intents ADD CONSTRAINT "
        "ck_research_intents_input_sha256 CHECK "
        f"({_sha256_digest_check('input_sha256')})"
    )
    op.execute(
        "ALTER TABLE research_intents ADD CONSTRAINT "
        "ck_research_intents_reason_code_safe CHECK "
        f"({_SAFE_GATEWAY_REASON_CODE_CHECK})"
    )
    op.execute(
        "ALTER TABLE research_intents ADD CONSTRAINT "
        "uq_research_intents_gateway_request UNIQUE (gateway_request_id)"
    )
    op.execute("ALTER TABLE role_events ALTER COLUMN reason_code TYPE VARCHAR(64)")
    op.execute("ALTER TABLE role_events ALTER COLUMN display_text TYPE VARCHAR(96)")
    op.execute("ALTER TABLE role_events ALTER COLUMN source_key TYPE VARCHAR(71)")
    op.execute(
        "ALTER TABLE role_events ADD CONSTRAINT ck_role_events_display_text_bounded "
        "CHECK (display_text IS NULL OR (CAST(display_text AS TEXT) = display_text "
        f"AND length(display_text) <= {_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH}))"
    )
    op.execute(
        "ALTER TABLE role_events ADD CONSTRAINT ck_role_events_display_text_safe "
        f"CHECK ({_SAFE_ROLE_EVENT_DISPLAY_TEXT_CHECK})"
    )
    op.execute(
        "ALTER TABLE role_events ADD CONSTRAINT ck_role_events_reason_code_safe "
        f"CHECK ({_SAFE_ROLE_EVENT_REASON_CODE_CHECK})"
    )
    op.execute(
        "ALTER TABLE role_events ADD CONSTRAINT ck_role_events_source_key_digest "
        f"CHECK ({_role_event_source_key_check()})"
    )
    op.execute(
        "ALTER TABLE gateway_commands DROP CONSTRAINT ck_gateway_commands_target_hash"
    )
    op.execute(
        "ALTER TABLE gateway_commands ADD CONSTRAINT ck_gateway_commands_target_hash "
        f"CHECK ({_sha256_digest_check('target_hash')})"
    )
    op.execute(
        "ALTER TABLE gateway_commands ADD CONSTRAINT "
        "ck_gateway_commands_target_version_absent CHECK (target_version IS NULL)"
    )
    op.execute(
        "ALTER TABLE gateway_commands ADD CONSTRAINT "
        "ck_gateway_commands_reason_code_safe CHECK "
        f"({_SAFE_GATEWAY_REASON_CODE_CHECK})"
    )


def _restore_postgresql_legacy_tables() -> None:
    op.execute(
        "ALTER TABLE gateway_idempotency_requests DROP CONSTRAINT "
        "ck_gateway_idempotency_requests_request_fingerprint"
    )
    op.execute(
        "ALTER TABLE research_messages DROP CONSTRAINT ck_research_messages_input_sha256"
    )
    op.execute(
        "ALTER TABLE research_messages ADD CONSTRAINT "
        "ck_research_messages_input_sha256 CHECK (length(input_sha256) = 64)"
    )
    op.execute(
        "ALTER TABLE research_intents DROP CONSTRAINT ck_research_intents_input_sha256"
    )
    op.execute(
        "ALTER TABLE research_intents DROP CONSTRAINT ck_research_intents_reason_code_safe"
    )
    op.execute(
        "ALTER TABLE research_intents DROP CONSTRAINT uq_research_intents_gateway_request"
    )
    op.execute(
        "ALTER TABLE research_intents ADD CONSTRAINT "
        "ck_research_intents_input_sha256 CHECK (length(input_sha256) = 64)"
    )
    for constraint_name in (
        "ck_role_events_display_text_bounded",
        "ck_role_events_display_text_safe",
        "ck_role_events_reason_code_safe",
        "ck_role_events_source_key_digest",
    ):
        op.execute(f"ALTER TABLE role_events DROP CONSTRAINT {constraint_name}")
    op.execute("ALTER TABLE role_events ALTER COLUMN reason_code TYPE VARCHAR(128)")
    op.execute("ALTER TABLE role_events ALTER COLUMN display_text TYPE TEXT")
    op.execute("ALTER TABLE role_events ALTER COLUMN source_key TYPE VARCHAR(256)")
    for constraint_name in (
        "ck_gateway_commands_target_hash",
        "ck_gateway_commands_target_version_absent",
        "ck_gateway_commands_reason_code_safe",
    ):
        op.execute(f"ALTER TABLE gateway_commands DROP CONSTRAINT {constraint_name}")
    op.execute(
        "ALTER TABLE gateway_commands ADD CONSTRAINT "
        "ck_gateway_commands_target_hash CHECK (length(target_hash) = 64)"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite_hardening()
        return
    if bind.dialect.name == "postgresql":
        _lock_postgresql_gateway_tables_for_hardening(bind)
        _require_legacy_gateway_triggers(bind)
        _require_safe_legacy_gateway_history(bind)
        _harden_postgresql_tables()
        _install_artifact_reference_validation_triggers("postgresql")
        _install_immutable_triggers("postgresql")
        return
    raise RuntimeError("0073 supports only SQLite and PostgreSQL")


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "sqlite":
        _run_sqlite_rebuild(harden=False)
        return
    if dialect_name == "postgresql":
        _drop_artifact_reference_validation_triggers("postgresql")
        _drop_immutable_triggers("postgresql")
        _restore_postgresql_legacy_tables()
        _install_immutable_triggers("postgresql")
        return
    raise RuntimeError("0073 supports only SQLite and PostgreSQL")
