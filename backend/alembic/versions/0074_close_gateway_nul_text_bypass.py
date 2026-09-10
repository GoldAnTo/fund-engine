"""Close SQLite NUL-text bypasses without rewriting released Gateway history.

Revision ID: 0074
Revises: 0073

0073 is already a migration boundary: this revision first authenticates its
exact trigger contract and revalidates immutable Gateway history.  It then
adds SQLite-only guards for embedded NUL bytes, which SQLite's ``length`` and
JSON1 functions otherwise treat as a string terminator.  Mutable malformed
receipt caches are discarded because recovery derives receipts from immutable
records rather than trusting that cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
_IMMUTABLE_TABLES = (
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_events",
    "gateway_commands",
)
_SHA256_HEX_ALPHABET = "0123456789abcdef"
_SHA256_DIGEST_LENGTH = 64
_ROLE_EVENT_SOURCE_KEY_PREFIX = "sha256:"
_ROLE_EVENT_SOURCE_KEY_LENGTH = 71
_MAX_GATEWAY_ARTIFACT_REFERENCES = 32
_MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES = 4096
_MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH = 96
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

# The immutable 0073 SQLite contract is release-owned.  Do not derive these
# hashes from source at runtime: direct Alembic callers must be fail-closed too.
_SQLITE_0073_TRIGGER_CONTRACTS = {
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
    "validate_insert_research_run_specs_input_artifact_refs": (
        "research_run_specs",
        "748f4398b3e3e38aa17846276b093159f4dcee5b0e115924c0f374a04d42d95a",
    ),
    "validate_insert_role_events_artifact_refs": (
        "role_events",
        "776ec8047aec7583d6e8d9264a150831fb771b8a190f84253636084e7fba6fe2",
    ),
    "reject_replace_research_messages": (
        "research_messages",
        "9dd0cf405f7a4a0fa5047c3e1dade116671e738306130e28aa15766ddd92872a",
    ),
    "reject_replace_research_intents": (
        "research_intents",
        "98ef8c9a280dfac9f46f638a3fc3193ea4c9c6f263794b021e58f460a3672263",
    ),
    "reject_replace_research_run_specs": (
        "research_run_specs",
        "6ee8bf885d1f674248791a10e8806e357c9557a03317f4f6200aef2e29938403",
    ),
    "reject_replace_role_events": (
        "role_events",
        "a5cea8f556a364fec758272ca262dd1853d93f5a0beb99e6b298078d8dc1a31c",
    ),
    "reject_replace_gateway_commands": (
        "gateway_commands",
        "5a5c5c2c8cdb5b226594cbba760993b2d1b35882b1192112953d51d38de0d3a5",
    ),
}
# The successor contract is also static: downgrade must never discard the
# NUL-text protections from a partially altered 0074 database and then stamp
# it as 0073.  Keep the hashes local to this revision so direct Alembic calls
# do not rely on current application source.
_SQLITE_0074_TRIGGER_CONTRACTS = {
    **_SQLITE_0073_TRIGGER_CONTRACTS,
    "validate_insert_gateway_commands_target_hash_nul": (
        "gateway_commands",
        "c3398faa5fe8e39404b00d736ca094b87fe5c4b3de0cacdeaa38299470a9aec2",
    ),
    "validate_insert_gateway_idempotency_receipt_cache": (
        "gateway_idempotency_requests",
        "8dabb2697b511f02c135c796be2c0ba6b85e37179e9a47535360e968e0210927",
    ),
    "validate_insert_gateway_idempotency_request_fingerprint_nul": (
        "gateway_idempotency_requests",
        "6f5421f1a9dcabc9f557077f3c9aabd0f896667fbe0246222125e91b01d59636",
    ),
    "validate_insert_research_intents_input_sha256_nul": (
        "research_intents",
        "75fc21bfc643ac05a7c084e58de378893ae1cc87d4d1bfd36587e2d2eb7bfb99",
    ),
    "validate_insert_research_messages_input_sha256_nul": (
        "research_messages",
        "b5d5540791c9215f27467c0f8f1d7eae138a1e2c74f6ae04797953929287ec40",
    ),
    "validate_insert_research_run_specs_input_artifact_refs_nul": (
        "research_run_specs",
        "990b669aad91ec845b2c2a460f15e7fd0bd2b9aacf9705825860b5ad599e3851",
    ),
    "validate_insert_role_events_artifact_refs_nul": (
        "role_events",
        "3eb5a18d6e250094397608a79bbe583ae8516070126be9472e105035749b4205",
    ),
    "validate_insert_role_events_source_key_nul": (
        "role_events",
        "37bae1f4dbe829482e69ae2b642a0960930119f8c028462d237138e182f179a4",
    ),
    "validate_update_gateway_idempotency_receipt_cache": (
        "gateway_idempotency_requests",
        "086b8fbdca75e91c91b74a6ef86951a7d7617c947938f9631e028a28ccea0cb9",
    ),
    "validate_update_gateway_idempotency_request_fingerprint_nul": (
        "gateway_idempotency_requests",
        "ec8bd7a429fec38e8a28dd0d692e6aa4a536659886b2a8daef6bb59a87ca56f0",
    ),
}
_POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256 = (
    "54768e2026d0b82b7b7a5ee019dc4520e3a894c4510a02e27ebbb2842a3885ed"
)
_POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256 = (
    "fae79049395fb3174bb5405a2d7e8681edbc1b87c3642bf1ca95a673bbf6e55e"
)
_POSTGRESQL_TRIGGER_OPERATIONS = {7: "insert", 11: "delete", 19: "update"}
_POSTGRESQL_0073_TRIGGER_CONTRACTS = {
    **{
        (table_name, f"no_update_{table_name}"): (
            19,
            "reject_mutable_ledger",
            _POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256,
        )
        for table_name in _IMMUTABLE_TABLES
    },
    **{
        (table_name, f"no_delete_{table_name}"): (
            11,
            "reject_mutable_ledger",
            _POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256,
        )
        for table_name in _IMMUTABLE_TABLES
    },
    (
        "research_run_specs",
        "validate_insert_research_run_specs_input_artifact_refs",
    ): (
        7,
        "validate_gateway_artifact_references",
        _POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256,
    ),
    ("role_events", "validate_insert_role_events_artifact_refs"): (
        7,
        "validate_gateway_artifact_references",
        _POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256,
    ),
}
_POSTGRESQL_TRIGGER_STATE_SQL = """
SELECT trigger_rel.relname AS table_name, trigger_rel.relowner AS table_owner,
       trigger_row.tgname AS trigger_name, trigger_row.tgenabled AS enabled,
       trigger_row.tgtype AS trigger_type, trigger_row.tgqual AS trigger_qual,
       trigger_row.tgattr <> ''::int2vector AS is_column_specific,
       pg_get_triggerdef(trigger_row.oid, true) AS trigger_definition,
       function_namespace.nspname AS function_schema,
       function_row.proname AS function_name, function_row.proowner AS function_owner,
       function_row.prosrc AS function_source, language.lanname AS function_language,
       pg_get_function_result(function_row.oid) AS function_result,
       pg_get_function_arguments(function_row.oid) AS function_arguments,
       encode(trigger_row.tgargs, 'escape') AS trigger_arguments
FROM pg_trigger AS trigger_row
JOIN pg_class AS trigger_rel ON trigger_rel.oid = trigger_row.tgrelid
JOIN pg_namespace AS trigger_namespace ON trigger_namespace.oid = trigger_rel.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_namespace ON function_namespace.oid = function_row.pronamespace
JOIN pg_language AS language ON language.oid = function_row.prolang
WHERE NOT trigger_row.tgisinternal
  AND trigger_namespace.nspname = :schema_name
  AND trigger_rel.relname IN ('research_conversations', 'gateway_idempotency_requests',
      'research_messages', 'research_intents', 'research_run_specs', 'role_runs',
      'role_events', 'gateway_commands')
"""
_SQLITE_NUL_TEXT_GUARDS = (
    (
        "validate_insert_gateway_idempotency_request_fingerprint_nul",
        "gateway_idempotency_requests",
        "request_fingerprint",
        "INSERT",
        "ck_gateway_idempotency_requests_request_fingerprint",
    ),
    (
        "validate_update_gateway_idempotency_request_fingerprint_nul",
        "gateway_idempotency_requests",
        "request_fingerprint",
        "UPDATE",
        "ck_gateway_idempotency_requests_request_fingerprint",
    ),
    (
        "validate_insert_research_messages_input_sha256_nul",
        "research_messages",
        "input_sha256",
        "INSERT",
        "ck_research_messages_input_sha256",
    ),
    (
        "validate_insert_research_intents_input_sha256_nul",
        "research_intents",
        "input_sha256",
        "INSERT",
        "ck_research_intents_input_sha256",
    ),
    (
        "validate_insert_gateway_commands_target_hash_nul",
        "gateway_commands",
        "target_hash",
        "INSERT",
        "ck_gateway_commands_target_hash",
    ),
    (
        "validate_insert_role_events_source_key_nul",
        "role_events",
        "source_key",
        "INSERT",
        "ck_role_events_source_key_digest",
    ),
    (
        "validate_insert_research_run_specs_input_artifact_refs_nul",
        "research_run_specs",
        "input_artifact_refs_json",
        "INSERT",
        "Gateway artifact references must be NUL-free JSON text",
    ),
    (
        "validate_insert_role_events_artifact_refs_nul",
        "role_events",
        "artifact_refs_json",
        "INSERT",
        "Gateway artifact references must be NUL-free JSON text",
    ),
)
_SQLITE_RECEIPT_CACHE_GUARDS = (
    "validate_insert_gateway_idempotency_receipt_cache",
    "validate_update_gateway_idempotency_receipt_cache",
)


def _normalized_sqlite_ddl(value: str) -> str:
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
            normalized.append(character.lower() if "A" <= character <= "Z" else character)
            final_character_is_unquoted = True
        index += 1
    result = "".join(normalized)
    return result[:-1] if final_character_is_unquoted and result.endswith(";") else result


def _trigger_contract_error(revision: str = "0073") -> RuntimeError:
    return RuntimeError(f"Gateway {revision} trigger contract is not canonical")


def _require_sqlite_gateway_trigger_contract(
    bind,
    *,
    contracts: dict[str, tuple[str, str]],
    revision: str,
) -> None:
    parameters = {
        f"table_{index}": table_name for index, table_name in enumerate(_GATEWAY_TABLES)
    }
    placeholders = ", ".join(f":{name}" for name in parameters)
    rows = bind.execute(
        sa.text(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' "
            f"AND tbl_name IN ({placeholders})"
        ),
        parameters,
    ).mappings()
    triggers = {
        row["name"]: (row["tbl_name"], row["sql"])
        for row in rows
        if isinstance(row["name"], str)
        and isinstance(row["tbl_name"], str)
        and isinstance(row["sql"], str)
    }
    if set(triggers) != set(contracts):
        raise _trigger_contract_error(revision)
    for name, (expected_table, expected_hash) in contracts.items():
        table_name, trigger_sql = triggers[name]
        if table_name != expected_table or (
            hashlib.sha256(_normalized_sqlite_ddl(trigger_sql).encode()).hexdigest()
            != expected_hash
        ):
            raise _trigger_contract_error(revision)


def _require_0073_sqlite_gateway_triggers(bind) -> None:
    _require_sqlite_gateway_trigger_contract(
        bind,
        contracts=_SQLITE_0073_TRIGGER_CONTRACTS,
        revision="0073",
    )


def _require_0074_sqlite_gateway_triggers(bind) -> None:
    _require_sqlite_gateway_trigger_contract(
        bind,
        contracts=_SQLITE_0074_TRIGGER_CONTRACTS,
        revision="0074",
    )


def _postgresql_trigger_is_canonical(
    state,
    *,
    table_name: str,
    trigger_name: str,
    trigger_type: int,
    function_name: str,
    function_source_sha256: str,
    schema_name: str,
) -> bool:
    operation = _POSTGRESQL_TRIGGER_OPERATIONS.get(trigger_type)
    definition = state.get("trigger_definition")
    source = state.get("function_source")
    if operation is None or not isinstance(definition, str) or not isinstance(source, str):
        return False
    expected_definition = (
        rf"create trigger {re.escape(trigger_name)} before {operation} on "
        rf"(?:{re.escape(schema_name)}\.)?{re.escape(table_name)} for each row execute "
        rf"(?:function|procedure) (?:{re.escape(schema_name)}\.)?{re.escape(function_name)}\(\)"
    )
    return bool(
        state.get("table_name") == table_name
        and state.get("trigger_name") == trigger_name
        and state.get("enabled") == "O"
        and state.get("trigger_type") == trigger_type
        and state.get("trigger_qual") is None
        and state.get("is_column_specific") is False
        and re.fullmatch(expected_definition, _normalized_sqlite_ddl(definition))
        and state.get("function_schema") == schema_name
        and state.get("function_name") == function_name
        and state.get("function_language") == "plpgsql"
        and state.get("function_result") == "trigger"
        and state.get("function_arguments") == ""
        and state.get("trigger_arguments") == ""
        and isinstance(state.get("table_owner"), int)
        and state.get("table_owner") == state.get("function_owner")
        and hashlib.sha256(_normalized_sqlite_ddl(source).encode()).hexdigest()
        == function_source_sha256
    )


def _require_0073_postgresql_gateway_triggers(bind) -> None:
    schema_name = bind.scalar(sa.text("SELECT current_schema()"))
    version_relation = bind.scalar(
        sa.text(
            "SELECT to_regclass(format('%I.%I', CAST(:schema_name AS text), "
            "'alembic_version'))"
        ),
        {"schema_name": schema_name},
    )
    if (
        not isinstance(schema_name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", schema_name)
        or version_relation is None
    ):
        raise _trigger_contract_error()
    states = bind.execute(
        sa.text(_POSTGRESQL_TRIGGER_STATE_SQL), {"schema_name": schema_name}
    ).mappings()
    states_by_key = {}
    for state in states:
        table_name = state.get("table_name")
        trigger_name = state.get("trigger_name")
        key = (table_name, trigger_name)
        if (
            not isinstance(table_name, str)
            or not isinstance(trigger_name, str)
            or key not in _POSTGRESQL_0073_TRIGGER_CONTRACTS
            or key in states_by_key
        ):
            raise _trigger_contract_error()
        states_by_key[key] = state
    if set(states_by_key) != set(_POSTGRESQL_0073_TRIGGER_CONTRACTS):
        raise _trigger_contract_error()
    for (table_name, trigger_name), (
        trigger_type,
        function_name,
        source_hash,
    ) in _POSTGRESQL_0073_TRIGGER_CONTRACTS.items():
        if not _postgresql_trigger_is_canonical(
            states_by_key[(table_name, trigger_name)],
            table_name=table_name,
            trigger_name=trigger_name,
            trigger_type=trigger_type,
            function_name=function_name,
            function_source_sha256=source_hash,
            schema_name=schema_name,
        ):
            raise _trigger_contract_error()


def _require_0073_gateway_triggers(bind) -> None:
    if bind.dialect.name == "sqlite":
        _require_0073_sqlite_gateway_triggers(bind)
        return
    if bind.dialect.name == "postgresql":
        _require_0073_postgresql_gateway_triggers(bind)
        return
    raise _trigger_contract_error()


def _history_error(table_name: str, column_name: str) -> RuntimeError:
    return RuntimeError(
        "unsafe Gateway history "
        f"in {table_name}.{column_name}; refusing to grandfather immutable rows"
    )


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
    return f"{column_name}::text AS {column_name}" if dialect_name == "postgresql" else column_name


def _require_safe_0073_gateway_history(bind) -> None:
    """Recheck every immutable safe-projection invariant before 0074 DDL."""
    for table_name, column_name in (
        ("gateway_idempotency_requests", "request_fingerprint"),
        ("research_messages", "input_sha256"),
    ):
        for row in bind.execute(sa.text(f"SELECT id, {column_name} FROM {table_name}")).mappings():
            if not _is_canonical_sha256(row[column_name]):
                raise _history_error(table_name, column_name)

    for row in bind.execute(
        sa.text("SELECT id, input_sha256, reason_code FROM research_intents")
    ).mappings():
        if not _is_canonical_sha256(row["input_sha256"]):
            raise _history_error("research_intents", "input_sha256")
        if row["reason_code"] is not None and (
            type(row["reason_code"]) is not str
            or row["reason_code"] not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _history_error("research_intents", "reason_code")
    if bind.scalar(
        sa.text(
            "SELECT gateway_request_id FROM research_intents "
            "WHERE gateway_request_id IS NOT NULL GROUP BY gateway_request_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ) is not None:
        raise _history_error("research_intents", "gateway_request_id")

    for row in bind.execute(
        sa.text(
            "SELECT id, reason_code, display_text, "
            f"{_raw_json_column('artifact_refs_json', bind.dialect.name)}, source_key "
            "FROM role_events"
        )
    ).mappings():
        if row["display_text"] is not None and (
            type(row["display_text"]) is not str
            or len(row["display_text"]) > _MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH
            or row["display_text"] not in _SAFE_ROLE_EVENT_DISPLAY_TEXTS
        ):
            raise _history_error("role_events", "display_text")
        if row["reason_code"] is not None and (
            type(row["reason_code"]) is not str
            or row["reason_code"] not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _history_error("role_events", "reason_code")
        if not _is_canonical_role_event_source_key(row["source_key"]):
            raise _history_error("role_events", "source_key")
        if not _artifact_references_are_safe(row["artifact_refs_json"]):
            raise _history_error("role_events", "artifact_refs_json")

    for row in bind.execute(
        sa.text(
            "SELECT id, "
            f"{_raw_json_column('input_artifact_refs_json', bind.dialect.name)} "
            "FROM research_run_specs"
        )
    ).mappings():
        if not _artifact_references_are_safe(row["input_artifact_refs_json"]):
            raise _history_error("research_run_specs", "input_artifact_refs_json")

    for row in bind.execute(
        sa.text("SELECT id, target_hash, target_version, reason_code FROM gateway_commands")
    ).mappings():
        if not _is_canonical_sha256(row["target_hash"]):
            raise _history_error("gateway_commands", "target_hash")
        if row["target_version"] is not None:
            raise _history_error("gateway_commands", "target_version")
        if row["reason_code"] is not None and (
            type(row["reason_code"]) is not str
            or row["reason_code"] not in _SAFE_ROLE_EVENT_REASON_CODES
        ):
            raise _history_error("gateway_commands", "reason_code")


def _clear_unsafe_sqlite_receipt_caches(bind) -> None:
    """Discard non-authoritative cache values that SQLAlchemy cannot parse."""
    bind.execute(
        sa.text(
            "UPDATE gateway_idempotency_requests SET receipt_json = NULL "
            "WHERE receipt_json IS NOT NULL AND CASE "
            "WHEN typeof(receipt_json) <> 'text' THEN 1 "
            "WHEN instr(receipt_json, char(0)) <> 0 THEN 1 "
            "WHEN json_valid(receipt_json) <> 1 THEN 1 "
            "WHEN json_type(receipt_json) <> 'object' THEN 1 "
            "ELSE 0 END = 1"
        )
    )


def _drop_sqlite_nul_guards() -> None:
    for trigger_name, _table_name, _column_name, _operation, _error in _SQLITE_NUL_TEXT_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    for trigger_name in _SQLITE_RECEIPT_CACHE_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _install_sqlite_nul_guards() -> None:
    _drop_sqlite_nul_guards()
    for trigger_name, table_name, column_name, operation, error in _SQLITE_NUL_TEXT_GUARDS:
        escaped_error = error.replace("'", "''")
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {operation} ON {table_name} "
            "BEGIN SELECT CASE WHEN "
            f"typeof(NEW.{column_name}) <> 'text' "
            f"OR instr(NEW.{column_name}, char(0)) <> 0 "
            f"THEN RAISE(ABORT, '{escaped_error}') END; END"
        )
    for trigger_name, operation in zip(_SQLITE_RECEIPT_CACHE_GUARDS, ("INSERT", "UPDATE"), strict=True):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {operation} ON "
            "gateway_idempotency_requests BEGIN SELECT CASE WHEN "
            "NEW.receipt_json IS NOT NULL AND CASE "
            "WHEN typeof(NEW.receipt_json) <> 'text' THEN 1 "
            "WHEN instr(NEW.receipt_json, char(0)) <> 0 THEN 1 "
            "WHEN json_valid(NEW.receipt_json) <> 1 THEN 1 "
            "WHEN json_type(NEW.receipt_json) <> 'object' THEN 1 "
            "ELSE 0 END = 1 THEN RAISE(ABORT, "
            "'Gateway receipt cache must be a NUL-free JSON object') END; END"
        )


def _lock_postgresql_gateway_tables(bind) -> None:
    for table_name in _GATEWAY_TABLES:
        bind.execute(sa.text(f"LOCK TABLE {table_name} IN ACCESS EXCLUSIVE MODE"))


def _upgrade_sqlite() -> None:
    bind = op.get_bind()
    # This is the first transactional statement.  It keeps the writer lock
    # through authentication, semantic preflight, cache cleanup, guard DDL,
    # and Alembic's version stamp.
    bind.exec_driver_sql("BEGIN IMMEDIATE")
    _require_0073_sqlite_gateway_triggers(bind)
    _require_safe_0073_gateway_history(bind)
    _clear_unsafe_sqlite_receipt_caches(bind)
    _install_sqlite_nul_guards()
    _require_0074_sqlite_gateway_triggers(bind)


def _downgrade_sqlite() -> None:
    bind = op.get_bind()
    # Alembic treats SQLite DDL as non-transactional.  Start an explicit write
    # transaction before authenticating and dropping any guard so a failure
    # cannot leave a 0074 version stamp paired with a partial 0074 contract.
    bind.exec_driver_sql("BEGIN IMMEDIATE")
    _require_0074_sqlite_gateway_triggers(bind)
    _drop_sqlite_nul_guards()
    _require_0073_sqlite_gateway_triggers(bind)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite()
        return
    if bind.dialect.name == "postgresql":
        _lock_postgresql_gateway_tables(bind)
        _require_0073_postgresql_gateway_triggers(bind)
        _require_safe_0073_gateway_history(bind)
        return
    raise RuntimeError("0074 supports only SQLite and PostgreSQL")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _downgrade_sqlite()
        return
    if op.get_bind().dialect.name == "postgresql":
        return
    raise RuntimeError("0074 supports only SQLite and PostgreSQL")
