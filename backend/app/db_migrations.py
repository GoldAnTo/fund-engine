"""Explicit schema bootstrap for runnable research environments.

Application services never create tables implicitly. A runnable database is
always brought to the Alembic head first so its version is inspectable and a
subsequent release has a well-defined upgrade path.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401 - ensure the complete metadata is registered
from alembic import command
from app.company_research_event_schema import (
    CompanyResearchEventSchemaError,
    repair_company_event_schema,
)
from app.company_study_schema import COMPANY_STUDY_TABLES, require_company_study_guards
from app.models.ledger import Base


class UnmanagedDatabaseSchemaError(RuntimeError):
    """An existing database cannot be safely adopted as the current schema."""


_COMPANY_EVENTS = "uw_company_research_events"
_COMPANY_WORKER_INDEX = "ix_jobs_company_research_worker_candidates"
_COMPANY_WORKER_INDEX_COLUMNS = ("status", "created_at", "id")
_COMPANY_WORKER_INDEX_PREDICATE = (
    "kind = 'prepare_company_research' AND "
    "target_type = 'company_research_preparation' AND research_case_id IS NULL"
)
_LEGACY_AUDIT_IDENTITY_LENGTH = 128
_CURRENT_AUDIT_IDENTITY_LENGTH = 256
_AUDIT_IDENTITY_COLUMNS = (
    ("research_cases", "created_by"),
    ("theses", "created_by"),
    ("event_research_scope_versions", "changed_by"),
    ("source_contracts", "declared_by"),
    ("document_upload_artifacts", "uploaded_by"),
    ("case_tenant_admissions", "admitted_by"),
)
_GATEWAY_TABLE_NAMES = (
    "research_conversations",
    "gateway_idempotency_requests",
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_runs",
    "role_events",
    "gateway_commands",
)
_GATEWAY_TABLES = frozenset(_GATEWAY_TABLE_NAMES)
_GATEWAY_IMMUTABLE_TABLES = (
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_events",
    "gateway_commands",
)
_PROFESSIONAL_TEAM_TABLE_NAMES = (
    "research_teams",
    "professional_tasks",
    "professional_dependencies",
    "professional_outputs",
    "professional_attempts",
    "professional_events",
    "professional_requests",
    "professional_reviews",
)
_PROFESSIONAL_TEAM_TABLES = frozenset(_PROFESSIONAL_TEAM_TABLE_NAMES)
_GATEWAY_IMMUTABLE_TRIGGER_ERROR = "immutable gateway table is append-only"
# SHA-256 of the frozen released-0072 SQLite Gateway append-only trigger DDL
# after quote-safe ASCII normalization.  Do not calculate these from the
# migration source at startup: a database's managed revision must be
# authenticated against a release-owned contract before any repair or DDL can
# run.  0073 is a successor hardening migration, so old managed databases
# must authenticate against this exact ten-trigger boundary first.
_LEGACY_0072_SQLITE_GATEWAY_TRIGGER_CONTRACTS = {
    # The ten immutable UPDATE/DELETE guards.
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

# 0073 adds JSON validation and SQLite REPLACE guards.  Their exact bodies are
# part of the safe projection boundary, rather than merely a migration
# implementation.  Keep the historical contract above separate: an already
# released 0072 database cannot have these triggers until it safely reaches
# 0073.
_SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS = {
    **_LEGACY_0072_SQLITE_GATEWAY_TRIGGER_CONTRACTS,
    # JSON validation guards. Their exact bodies are part of the safe
    # projection boundary, rather than merely a migration implementation.
    "validate_insert_research_run_specs_input_artifact_refs": (
        "research_run_specs",
        "748f4398b3e3e38aa17846276b093159f4dcee5b0e115924c0f374a04d42d95a",
    ),
    "validate_insert_role_events_artifact_refs": (
        "role_events",
        "776ec8047aec7583d6e8d9264a150831fb771b8a190f84253636084e7fba6fe2",
    ),
    # SQLite's recursive-triggers default is OFF, so these make INSERT OR
    # REPLACE fail before it can delete/reinsert an immutable record.
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

# 0074 preserves the immutable 0073 contract and adds NUL-text guards.
# SQLite's ``length`` and JSON1 functions stop at embedded NUL bytes, so all
# ten new trigger bodies are release-owned security contracts too.  The two
# receipt-cache guards cover the mutable, non-authoritative JSON cache before
# an ORM can try to deserialize it.
_SQLITE_0074_GATEWAY_TRIGGER_CONTRACTS = {
    **_SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS,
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
# The unqualified alias always means the current head contract.  Existing
# callers and defensive tests use it to authenticate an already-head database.
_SQLITE_GATEWAY_TRIGGER_CONTRACTS = _SQLITE_0074_GATEWAY_TRIGGER_CONTRACTS
_POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256 = (
    "54768e2026d0b82b7b7a5ee019dc4520e3a894c4510a02e27ebbb2842a3885ed"
)
_POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256 = (
    "fae79049395fb3174bb5405a2d7e8681edbc1b87c3642bf1ca95a673bbf6e55e"
)
_POSTGRESQL_GATEWAY_TRIGGER_OPERATION_BY_TYPE = {
    7: "insert",  # BEFORE INSERT FOR EACH ROW
    11: "delete",  # BEFORE DELETE FOR EACH ROW
    19: "update",  # BEFORE UPDATE FOR EACH ROW
}
_LEGACY_0072_POSTGRESQL_GATEWAY_TRIGGER_CONTRACTS = {
    **{
        (table_name, f"no_update_{table_name}"): (
            19,  # BEFORE UPDATE FOR EACH ROW
            "reject_mutable_ledger",
            _POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256,
        )
        for table_name in _GATEWAY_IMMUTABLE_TABLES
    },
    **{
        (table_name, f"no_delete_{table_name}"): (
            11,  # BEFORE DELETE FOR EACH ROW
            "reject_mutable_ledger",
            _POSTGRESQL_REJECT_MUTABLE_LEDGER_SOURCE_SHA256,
        )
        for table_name in _GATEWAY_IMMUTABLE_TABLES
    },
}
_POSTGRESQL_0073_GATEWAY_TRIGGER_CONTRACTS = {
    **_LEGACY_0072_POSTGRESQL_GATEWAY_TRIGGER_CONTRACTS,
    (
        "research_run_specs",
        "validate_insert_research_run_specs_input_artifact_refs",
    ): (
        7,  # BEFORE INSERT FOR EACH ROW
        "validate_gateway_artifact_references",
        _POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256,
    ),
    ("role_events", "validate_insert_role_events_artifact_refs"): (
        7,
        "validate_gateway_artifact_references",
        _POSTGRESQL_GATEWAY_ARTIFACT_VALIDATOR_SOURCE_SHA256,
    ),
}
# PostgreSQL text and JSON types reject embedded NUL bytes natively, so 0074
# adds no server trigger there. Keep an explicit stage alias nevertheless:
# bootstrap must authenticate a 0073 database against its historical contract
# before it runs 0074, and a 0074 database against its current contract.
_POSTGRESQL_0074_GATEWAY_TRIGGER_CONTRACTS = _POSTGRESQL_0073_GATEWAY_TRIGGER_CONTRACTS
_POSTGRESQL_GATEWAY_TRIGGER_CONTRACTS = _POSTGRESQL_0074_GATEWAY_TRIGGER_CONTRACTS
_SQLITE_PROFESSIONAL_TEAM_GUARD_CONTRACTS = {
    "team_no_delete_professional_attempts": (
        "professional_attempts",
        "81367eb6374a6d0ed154a422b5b142fba3b4e593dd907d931807cc719e009c61",
    ),
    "team_no_replace_professional_attempts": (
        "professional_attempts",
        "565060f6809d207f558aa5711fe501975a1b5fe42f3e36a04f4cd9edbeb1a411",
    ),
    "team_no_update_professional_attempts": (
        "professional_attempts",
        "81e2410ad3e3f3cddbeee913f00df38dff3ad35700c8f0a16709b7f2a8aa6366",
    ),
    "team_no_delete_professional_dependencies": (
        "professional_dependencies",
        "3abe252cecf7e97d539b2598302a023a6210605646d20e07b977dabe48614f39",
    ),
    "team_no_replace_professional_dependencies": (
        "professional_dependencies",
        "35c98b39d04ed56f03207954b91f8386e97107d259be7d9a3e16e496e9b34d34",
    ),
    "team_no_update_professional_dependencies": (
        "professional_dependencies",
        "720b1934d38bf5f6c38cfb3dbbf5e3a5c89aced0fbfb53646c2bef1abd8bcf3f",
    ),
    "team_no_delete_professional_events": (
        "professional_events",
        "e70f8787bdedaf73ec9f26c082e24f5d132416e00de36a1cf7fa95d4ee3e379d",
    ),
    "team_no_replace_professional_events": (
        "professional_events",
        "06ce381c75c35b4aec713e3288e0fccfeabac588e992a05116d9769f7052dbbb",
    ),
    "team_no_update_professional_events": (
        "professional_events",
        "5ff879e6a3084458c94731b8b85185cd2a9da776ea4e42c480cf5f0f9ed5b7e6",
    ),
    "team_no_delete_professional_outputs": (
        "professional_outputs",
        "48cdf4c569a84061ba1cd31524eb8c4080345cc7ccc511bf4a18e13b62b1eaae",
    ),
    "team_no_replace_professional_outputs": (
        "professional_outputs",
        "5a838f45da252d29e8372668426d3f32872ba8944cf3240031915aa014bb1ee8",
    ),
    "team_no_update_professional_outputs": (
        "professional_outputs",
        "a292fb82f1629dddf1366562993240aa34e08463df299f23916a189644e841e7",
    ),
    "team_valid_refs_professional_outputs": (
        "professional_outputs",
        "4c3648c0105d0d740ef34f6ddc3e982f26247b89f7015e1c67bda7cb653fd24e",
    ),
    "team_no_delete_professional_requests": (
        "professional_requests",
        "1dce20e4445c988e7059b99dfb32317f7b4eadc620864b1473ab94f5c0961832",
    ),
    "team_no_replace_professional_requests": (
        "professional_requests",
        "5a3f8016f587f26eba80fbe6f439725d675743221b579b269c83e28cc2fe10a5",
    ),
    "team_no_update_professional_requests": (
        "professional_requests",
        "d227ef3928e497a480df473ae2b18468781e02bda23d715d5bfe2763b550d95f",
    ),
    "team_no_delete_professional_reviews": (
        "professional_reviews",
        "6cf855e1124e8fbc9d522414c986d173624431aab81cbd59c08de81342b8325a",
    ),
    "team_no_replace_professional_reviews": (
        "professional_reviews",
        "199dcc5cc905af1b9a65394d7b4a0ef0cc8817ee6612e736aad9d289ef172c95",
    ),
    "team_no_update_professional_reviews": (
        "professional_reviews",
        "5198189d7cf33c7e7a9424bc167a080cb71002ae9d98a4a6e3c5846bdc3b8c7e",
    ),
    "team_valid_refs_professional_reviews": (
        "professional_reviews",
        "598a2e99ccf3afbe981255316be6e4cebc4f1568a687f328a51a7ff979518e6a",
    ),
    "team_frozen_professional_tasks": (
        "professional_tasks",
        "db5cb57288388c045db4197402e3d4e8a62c233683b960073bc9d1d23cce81eb",
    ),
    "team_no_delete_professional_tasks": (
        "professional_tasks",
        "7c268af680e3ca8a43e649eba893b5dc17062f886875c8d281c7268e2d4fb1f8",
    ),
    "team_no_replace_professional_tasks": (
        "professional_tasks",
        "00c136a18596efd790fda6d4423b944a66627dd55f5ff320490d7742cd623a71",
    ),
    "team_frozen_research_teams": (
        "research_teams",
        "d3752d60a1dafed64462660464bc37456950a7951643364885102c674a13429b",
    ),
    "team_no_delete_research_teams": (
        "research_teams",
        "04b402744374e1ff2cced87c0caacf6bc5b7819f6663b16ab857f447b5af2c40",
    ),
    "team_no_replace_research_teams": (
        "research_teams",
        "099534f41835fa42d20744ba76095a64dc4dbd96b53dffea7be145492b754228",
    ),
}
_POSTGRESQL_PROFESSIONAL_HISTORY_GUARD_SOURCE_SHA256 = (
    "8185ec4170a992d017f518ef22af420e189afffad9e021fc4d96fdf419ba74ec"
)
_POSTGRESQL_PROFESSIONAL_TEAM_GUARD_CONTRACTS = {
    **{
        (table_name, f"team_no_delete_{table_name}"): (
            11,
            f"team_no_delete_{table_name}",
            _POSTGRESQL_PROFESSIONAL_HISTORY_GUARD_SOURCE_SHA256,
        )
        for table_name in _PROFESSIONAL_TEAM_TABLE_NAMES
    },
    **{
        (table_name, f"team_no_update_{table_name}"): (
            19,
            f"team_no_update_{table_name}",
            _POSTGRESQL_PROFESSIONAL_HISTORY_GUARD_SOURCE_SHA256,
        )
        for table_name in _PROFESSIONAL_TEAM_TABLE_NAMES[2:]
    },
    ("research_teams", "team_frozen_research_teams"): (
        19,
        "team_frozen_research_teams",
        "bf438e30d44a9f548abb2d052bf201fe871d4adede6e4319424f69af7aa33dc2",
    ),
    ("professional_tasks", "team_frozen_professional_tasks"): (
        19,
        "team_frozen_professional_tasks",
        "6773390b0e4701c894efb9f4b1bcdea87746aeafc576833cb9ee9b64978b3f38",
    ),
    ("professional_outputs", "team_valid_refs_professional_outputs"): (
        7,
        "team_valid_refs_professional_outputs",
        "779684b83a911407c8ff75c13c9a00397128bbda109d5b6dbb4abf3a3c78b398",
    ),
    ("professional_reviews", "team_valid_refs_professional_reviews"): (
        7,
        "team_valid_refs_professional_reviews",
        "2f6cf0fa1eb0f213fd8e1bca793f26416ddb3efc9740c9a9ced274ffb33b322e",
    ),
}
_POSTGRESQL_GATEWAY_TRIGGER_STATE_SQL = """
SELECT
    trigger_rel.relname AS table_name,
    trigger_rel.relowner AS table_owner,
    trigger_row.tgname AS trigger_name,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgqual AS trigger_qual,
    trigger_row.tgattr <> ''::int2vector AS is_column_specific,
    pg_get_triggerdef(trigger_row.oid, true) AS trigger_definition,
    function_namespace.nspname AS function_schema,
    function_row.proname AS function_name,
    function_row.proowner AS function_owner,
    function_row.prosrc AS function_source,
    language.lanname AS function_language,
    pg_get_function_result(function_row.oid) AS function_result,
    pg_get_function_arguments(function_row.oid) AS function_arguments,
    encode(trigger_row.tgargs, 'escape') AS trigger_arguments
FROM pg_trigger AS trigger_row
JOIN pg_class AS trigger_rel ON trigger_rel.oid = trigger_row.tgrelid
JOIN pg_namespace AS trigger_namespace
    ON trigger_namespace.oid = trigger_rel.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_namespace
    ON function_namespace.oid = function_row.pronamespace
JOIN pg_language AS language ON language.oid = function_row.prolang
WHERE NOT trigger_row.tgisinternal
  -- Bind to the schema deliberately selected for this migration connection,
  -- not to an unqualified relation which a later search-path entry could
  -- shadow with a same-named decoy.
  AND trigger_namespace.nspname = :schema_name
  AND trigger_row.tgrelid IN (
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'research_conversations')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'gateway_idempotency_requests')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'research_messages')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'research_intents')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'research_run_specs')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'role_runs')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'role_events')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'gateway_commands'))
  )
"""
_POSTGRESQL_PROFESSIONAL_TEAM_TRIGGER_STATE_SQL = """
SELECT
    trigger_rel.relname AS table_name,
    trigger_rel.relowner AS table_owner,
    trigger_row.tgname AS trigger_name,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgqual AS trigger_qual,
    trigger_row.tgattr <> ''::int2vector AS is_column_specific,
    pg_get_triggerdef(trigger_row.oid, true) AS trigger_definition,
    function_namespace.nspname AS function_schema,
    function_row.proname AS function_name,
    function_row.proowner AS function_owner,
    function_row.prosrc AS function_source,
    language.lanname AS function_language,
    pg_get_function_result(function_row.oid) AS function_result,
    pg_get_function_arguments(function_row.oid) AS function_arguments,
    encode(trigger_row.tgargs, 'escape') AS trigger_arguments
FROM pg_trigger AS trigger_row
JOIN pg_class AS trigger_rel ON trigger_rel.oid = trigger_row.tgrelid
JOIN pg_namespace AS trigger_namespace
    ON trigger_namespace.oid = trigger_rel.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_namespace
    ON function_namespace.oid = function_row.pronamespace
JOIN pg_language AS language ON language.oid = function_row.prolang
WHERE NOT trigger_row.tgisinternal
  AND trigger_namespace.nspname = :schema_name
  AND trigger_row.tgrelid IN (
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'research_teams')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_tasks')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_dependencies')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_outputs')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_attempts')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_events')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_requests')),
    to_regclass(format('%I.%I', CAST(:schema_name AS text), 'professional_reviews'))
  )
"""
_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER = "trg_ai_assessments_protocol_scope"
# SHA-256 of the frozen 0051 SQLite trigger DDL after quote-safe ASCII
# normalization.  Bootstrap must not derive this historical contract from
# mutable ORM metadata.
_SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_SHA256 = (
    "af7b328f12148d8aeb80d570af3d5086febe2c4a93e5962b4a57b7868224ff13"
)
_POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL = (
    "SELECT i.indisvalid, i.indisready, am.amname AS access_method, "
    "i.indnkeyatts, "
    "ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY "
    "AS key(attnum, ordinal) JOIN pg_attribute a "
    "ON a.attrelid = table_rel.oid AND a.attnum = key.attnum "
    "WHERE key.ordinal <= i.indnkeyatts ORDER BY key.ordinal) "
    "AS key_columns, "
    "ARRAY(SELECT (i.indoption[ordinal - 1] & 1) <> 0 "
    "FROM generate_series(1, i.indnkeyatts) AS ordinal "
    "ORDER BY ordinal) AS descending, "
    "ARRAY(SELECT (i.indoption[ordinal - 1] & 2) <> 0 "
    "FROM generate_series(1, i.indnkeyatts) AS ordinal "
    "ORDER BY ordinal) AS nulls_first "
    "FROM pg_index i JOIN pg_class index_rel "
    "ON index_rel.oid = i.indexrelid JOIN pg_class table_rel "
    "ON table_rel.oid = i.indrelid JOIN pg_am am "
    "ON am.oid = index_rel.relam WHERE i.indrelid = to_regclass(:table_name) "
    "AND index_rel.relname = :index_name"
)


def _normalize_company_worker_predicate(value: object) -> str:
    """Normalize SQL syntax without changing case-sensitive literals."""
    source = str(value)
    normalized: list[str] = []
    cursor = 0
    while cursor < len(source):
        if source[cursor] == "'":
            end = cursor + 1
            while end < len(source):
                if source[end] != "'":
                    end += 1
                    continue
                if end + 1 < len(source) and source[end + 1] == "'":
                    end += 2
                    continue
                end += 1
                break
            if end > len(source) or source[end - 1] != "'":
                return "invalid-unclosed-literal"
            normalized.append(source[cursor:end])
            cursor = end
            continue
        end = source.find("'", cursor)
        if end < 0:
            end = len(source)
        syntax = (
            source[cursor:end]
            .lower()
            .replace('"', "")
            .replace("::text", "")
        )
        normalized.append(
            "".join(
                character
                for character in syntax
                if not character.isspace() and character not in "()"
            )
        )
        cursor = end
    return "".join(normalized)


def upgrade_database_to_head(database_url: str) -> None:
    """Upgrade ``database_url`` using this repository's Alembic history."""
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    # ``script_location`` in alembic.ini is relative to the caller's CWD.
    # Bootstrap is invoked by scripts and tests from outside ``backend/``, so
    # bind it to this module's repository location instead.
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names())
        if actual_tables and "alembic_version" not in actual_tables:
            pre_gateway_schema = _require_current_metadata(inspector, actual_tables)
            audit_identity_revision = _unmanaged_audit_identity_revision(
                inspector,
                pre_gateway_schema=pre_gateway_schema,
            )
            # Direct ORM-created demo databases predate migration management.
            # Only a structurally complete one is adopted; unknown schemas are
            # rejected instead of being falsely marked current.
            _require_unmanaged_sqlite_integrity(engine)
            _repair_company_event_schema(engine)
            _install_company_worker_index(engine)
            command.stamp(config, audit_identity_revision)
            if audit_identity_revision != "head":
                command.upgrade(config, "head")
        else:
            # A version marker only identifies which migrations ran; it does
            # not authenticate a trigger that may have been replaced later.
            # Existing managed databases must be authenticated before a new
            # migration can issue DDL.  A fresh database has no trigger until
            # its history has been applied, so authenticate that case after.
            managed_database = bool(actual_tables)
            if managed_database and engine.dialect.name == "sqlite":
                if not _managed_sqlite_includes_revision(config, engine, "0051"):
                    # The trigger was introduced by 0051, so an older valid
                    # database cannot be authenticated until that exact
                    # revision has installed it. Do not run later DDL first.
                    command.upgrade(config, "0051")
                _require_sqlite_protocol_trigger(engine)
            if managed_database and _managed_database_includes_revision(
                config,
                engine,
                "0074",
            ):
                # A head marker is not proof that its complete Gateway
                # boundary still exists. Authenticate all 27 SQLite 0074
                # guards (or the equivalent PostgreSQL contract) before an
                # idempotent Alembic upgrade or later repair can issue DDL.
                _require_gateway_immutable_triggers(engine)
            elif managed_database and _managed_database_includes_revision(
                config,
                engine,
                "0073",
            ):
                # A 0073 database still has precisely the historical
                # seventeen-trigger contract. It must authenticate that
                # boundary before 0074 can install its successor guards.
                _require_0073_gateway_immutable_triggers(engine)
            elif managed_database and _managed_database_includes_revision(
                config,
                engine,
                "0072",
            ):
                # 0072 was released before the successor hardening guards.
                # It must authenticate against its exact ten-trigger
                # contract, then 0073 itself repeats that preflight before
                # changing any schema.
                _require_legacy_gateway_immutable_triggers(engine)
            if managed_database and _managed_database_includes_revision(
                config,
                engine,
                "0075",
            ):
                # Head may be invoked idempotently during startup. Authenticate
                # the 0075 professional-team history guards before any repair
                # or no-op migration can change schema state.
                _require_professional_team_guards(engine)
            if managed_database and _managed_database_includes_revision(config, engine, "0076"):
                require_company_study_guards(engine)
            command.upgrade(config, "head")
            if not managed_database:
                _require_sqlite_protocol_trigger(engine)
        if _managed_database_includes_revision(config, engine, "0074"):
            _require_gateway_immutable_triggers(engine)
        elif _managed_database_includes_revision(config, engine, "0073"):
            _require_0073_gateway_immutable_triggers(engine)
        elif _managed_database_includes_revision(config, engine, "0072"):
            _require_legacy_gateway_immutable_triggers(engine)
        if _managed_database_includes_revision(config, engine, "0075"):
            _require_professional_team_guards(engine)
        if _managed_database_includes_revision(config, engine, "0076"):
            require_company_study_guards(engine)
        # Reinstall and behavior-check the canonical append-only boundary even
        # when Alembic was already at head.  This repairs later trigger loss or
        # a trigger with a trusted name but untrusted body.
        _repair_company_event_schema(engine)
        _require_company_worker_index(engine)
    finally:
        engine.dispose()


def _require_current_metadata(inspector, actual_tables: set[str]) -> bool:
    """Authenticate an unmanaged ORM schema and identify a pre-0072 shape.

    A database created by the old 0070/0071 ORM metadata has no version table
    and, necessarily, none of the new Gateway tables.  It can be adopted only
    as that complete historical shape and then migrated.  A partial Gateway
    footprint is neither historical nor current, so it must never be stamped.
    """
    expected = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }
    present_gateway_tables = _GATEWAY_TABLES.intersection(actual_tables)
    if present_gateway_tables:
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and contains Gateway tables; "
            "the 0072 database guards require an explicit Alembic migration"
        )
    present_professional_team_tables = _PROFESSIONAL_TEAM_TABLES.intersection(
        actual_tables
    )
    if present_professional_team_tables:
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and contains professional "
            "team tables; the 0075 database guards require an explicit Alembic "
            "migration"
        )
    if COMPANY_STUDY_TABLES.intersection(actual_tables):
        raise UnmanagedDatabaseSchemaError("unmanaged company study tables require explicit Alembic migration")
    missing_table_names = set(expected) - actual_tables
    missing_gateway_tables = _GATEWAY_TABLES.intersection(missing_table_names)
    missing_professional_team_tables = _PROFESSIONAL_TEAM_TABLES.intersection(
        missing_table_names
    )
    pre_gateway_schema = (
        missing_gateway_tables == _GATEWAY_TABLES
        and missing_professional_team_tables == _PROFESSIONAL_TEAM_TABLES
    )
    if pre_gateway_schema:
        missing_table_names -= _GATEWAY_TABLES
        missing_table_names -= _PROFESSIONAL_TEAM_TABLES
        missing_table_names -= COMPANY_STUDY_TABLES
    missing_tables = sorted(missing_table_names)
    missing_columns = {
        table: sorted(
            columns
            - {column["name"] for column in inspector.get_columns(table)}
        )
        for table, columns in expected.items()
        if table in actual_tables
        and columns - {column["name"] for column in inspector.get_columns(table)}
    }
    if missing_tables or missing_columns:
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and is not compatible with "
            f"the current schema; missing_tables={missing_tables}, "
            f"missing_columns={missing_columns}"
        )
    _require_company_event_relational_contract(inspector)
    return pre_gateway_schema


def _unmanaged_audit_identity_revision(
    inspector,
    *,
    pre_gateway_schema: bool,
) -> str:
    widths: list[int] = []
    column_widths: dict[str, int | None] = {}
    incompatible_columns: list[str] = []
    for table_name, column_name in _AUDIT_IDENTITY_COLUMNS:
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        column = columns[column_name]
        column_type = column["type"]
        width = getattr(column_type, "length", None)
        column_widths[f"{table_name}.{column_name}"] = width
        if (
            getattr(column_type, "__visit_name__", "").upper() != "VARCHAR"
            or width not in {
                _LEGACY_AUDIT_IDENTITY_LENGTH,
                _CURRENT_AUDIT_IDENTITY_LENGTH,
            }
        ):
            incompatible_columns.append(f"{table_name}.{column_name}")
            continue
        widths.append(width)
    if incompatible_columns or len(set(widths)) != 1:
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and has incompatible "
            "audit identity columns; expected every audit identity to be "
            "VARCHAR(128) or VARCHAR(256), found widths="
            f"{column_widths}, incompatible={incompatible_columns}"
        )
    if widths[0] == _LEGACY_AUDIT_IDENTITY_LENGTH:
        if not pre_gateway_schema:
            raise UnmanagedDatabaseSchemaError(
                "existing database has Gateway tables but legacy audit identity widths"
            )
        return "0070"
    if pre_gateway_schema:
        return "0071"
    return "head"


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


def _require_unmanaged_sqlite_integrity(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise UnmanagedDatabaseSchemaError(
                "existing database has no Alembic version and has foreign key "
                "violations"
            )
        _require_sqlite_protocol_trigger_on_connection(connection)


def _managed_database_includes_revision(
    config: Config,
    engine,
    required_revision: str,
) -> bool:
    """Return whether a managed head descends from one revision.

    This uses Alembic's revision graph instead of assuming fixed-width numeric
    revision strings. A malformed/multi-head managed database is rejected
    before any repair or later migration can alter it.
    """
    with engine.connect() as connection:
        current_heads = MigrationContext.configure(connection).get_current_heads()
    if len(current_heads) > 1:
        raise UnmanagedDatabaseSchemaError(
            "managed database has multiple Alembic heads"
        )
    if not current_heads:
        return False
    try:
        revisions = ScriptDirectory.from_config(config).iterate_revisions(
            current_heads[0],
            "base",
        )
        return required_revision in {revision.revision for revision in revisions}
    except ResolutionError as exc:
        raise UnmanagedDatabaseSchemaError(
            "managed database has an unknown Alembic revision"
        ) from exc


def _managed_sqlite_includes_revision(
    config: Config,
    engine,
    required_revision: str,
) -> bool:
    """Return whether a managed SQLite head descends from one revision."""
    if engine.dialect.name != "sqlite":
        return False
    return _managed_database_includes_revision(config, engine, required_revision)


def _require_sqlite_protocol_trigger(engine) -> None:
    """Authenticate the frozen 0051 SQLite assessment protocol boundary."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        _require_sqlite_protocol_trigger_on_connection(connection)


def _require_sqlite_protocol_trigger_on_connection(connection) -> None:
    trigger_sql = connection.scalar(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = :trigger_name"
        ),
        {"trigger_name": _SQLITE_ASSESSMENT_PROTOCOL_TRIGGER},
    )
    if (
        not isinstance(trigger_sql, str)
        or hashlib.sha256(
            _normalized_sqlite_ddl(trigger_sql).encode("utf-8")
        ).hexdigest()
        != _SQLITE_ASSESSMENT_PROTOCOL_TRIGGER_SHA256
    ):
        raise UnmanagedDatabaseSchemaError(
            "database lacks the canonical assessment protocol trigger"
        )


def _gateway_trigger_error() -> UnmanagedDatabaseSchemaError:
    return UnmanagedDatabaseSchemaError(
        "database lacks a canonical Gateway append-only trigger"
    )


def _require_gateway_immutable_triggers(engine) -> None:
    """Fail closed unless every current 0074 Gateway guard is canonical."""
    _require_gateway_trigger_contract(
        engine,
        sqlite_contracts=_SQLITE_GATEWAY_TRIGGER_CONTRACTS,
        postgresql_contracts=_POSTGRESQL_GATEWAY_TRIGGER_CONTRACTS,
    )


def _require_0073_gateway_immutable_triggers(engine) -> None:
    """Fail closed unless every historical 0073 Gateway guard is canonical."""
    _require_gateway_trigger_contract(
        engine,
        sqlite_contracts=_SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS,
        postgresql_contracts=_POSTGRESQL_0073_GATEWAY_TRIGGER_CONTRACTS,
    )


def _require_legacy_gateway_immutable_triggers(engine) -> None:
    """Fail closed unless every released-0072 Gateway guard is canonical."""
    _require_gateway_trigger_contract(
        engine,
        sqlite_contracts=_LEGACY_0072_SQLITE_GATEWAY_TRIGGER_CONTRACTS,
        postgresql_contracts=_LEGACY_0072_POSTGRESQL_GATEWAY_TRIGGER_CONTRACTS,
    )


def _require_gateway_trigger_contract(
    engine,
    *,
    sqlite_contracts: Mapping[str, tuple[str, str]],
    postgresql_contracts: Mapping[tuple[str, str], tuple[int, str, str]],
) -> None:
    """Authenticate the revision-specific Gateway trigger contract."""
    if engine.dialect.name == "sqlite":
        _require_sqlite_gateway_immutable_triggers(engine, contracts=sqlite_contracts)
        return
    if engine.dialect.name == "postgresql":
        _require_postgresql_gateway_immutable_triggers(
            engine,
            contracts=postgresql_contracts,
        )
        return
    raise _gateway_trigger_error()


def _require_sqlite_gateway_immutable_triggers(
    engine,
    *,
    contracts: Mapping[str, tuple[str, str]],
) -> None:
    table_parameters = {
        f"table_{index}": table_name
        for index, table_name in enumerate(_GATEWAY_TABLE_NAMES)
    }
    table_placeholders = ", ".join(f":{name}" for name in table_parameters)
    with engine.connect() as connection:
        trigger_rows = connection.execute(
            text(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name IN ("
                f"{table_placeholders})"
            ),
            table_parameters,
        ).mappings()
        trigger_by_name = {
            row["name"]: (row["tbl_name"], row["sql"])
            for row in trigger_rows
            if isinstance(row["name"], str)
            and isinstance(row["tbl_name"], str)
            and isinstance(row["sql"], str)
        }
    if set(trigger_by_name) != set(contracts):
        raise _gateway_trigger_error()
    for trigger_name, (expected_table, expected_hash) in (
        contracts.items()
    ):
        actual_table, trigger_sql = trigger_by_name[trigger_name]
        if actual_table != expected_table:
            raise _gateway_trigger_error()
        if (
            hashlib.sha256(
                _normalized_sqlite_ddl(trigger_sql).encode("utf-8")
            ).hexdigest()
            != expected_hash
        ):
            raise _gateway_trigger_error()


def _postgresql_gateway_trigger_state_is_canonical(
    state: Mapping[str, object],
    *,
    table_name: str,
    trigger_name: str,
    trigger_type: int,
    function_name: str,
    function_source_sha256: str,
    schema_name: str,
) -> bool:
    function_source = state.get("function_source")
    return bool(
        state.get("table_name") == table_name
        and state.get("trigger_name") == trigger_name
        and state.get("enabled") == "O"
        and state.get("trigger_type") == trigger_type
        # A matching tgtype still permits a ``WHEN`` condition or ``UPDATE
        # OF`` attribute list.  Those variants can make an otherwise
        # identical immutable guard silently skip a mutation.
        and state.get("trigger_qual") is None
        and state.get("is_column_specific") is False
        and _postgresql_gateway_trigger_definition_is_canonical(
            state,
            table_name=table_name,
            trigger_name=trigger_name,
            trigger_type=trigger_type,
            function_name=function_name,
            schema_name=schema_name,
        )
        and state.get("function_schema") == schema_name
        and state.get("function_name") == function_name
        and state.get("function_language") == "plpgsql"
        and state.get("function_result") == "trigger"
        and state.get("function_arguments") == ""
        and state.get("trigger_arguments") == ""
        and _postgresql_gateway_trigger_ownership_is_canonical(state)
        and isinstance(function_source, str)
        and hashlib.sha256(
            _normalized_sqlite_ddl(function_source).encode("utf-8")
        ).hexdigest()
        == function_source_sha256
    )


def _postgresql_gateway_trigger_definition_is_canonical(
    state: Mapping[str, object],
    *,
    table_name: str,
    trigger_name: str,
    trigger_type: int,
    function_name: str,
    schema_name: str,
) -> bool:
    """Authenticate the full PostgreSQL trigger syntax, not only tgtype.

    ``pg_get_triggerdef`` records details (notably ``WHEN`` and ``UPDATE OF``)
    which are absent from ``tgtype``.  The catalog fields checked alongside
    this definition anchor both the relation and function to ``schema_name``;
    the definition therefore permits only PostgreSQL's harmless optional
    qualification of those already-authenticated names.
    """
    definition = state.get("trigger_definition")
    operation = _POSTGRESQL_GATEWAY_TRIGGER_OPERATION_BY_TYPE.get(trigger_type)
    if not isinstance(definition, str) or operation is None:
        return False

    escaped_schema = re.escape(schema_name)
    escaped_table = re.escape(table_name)
    escaped_function = re.escape(function_name)
    expected = (
        rf"create trigger {re.escape(trigger_name)} before {operation} on "
        rf"(?:{escaped_schema}\.)?{escaped_table} for each row execute "
        rf"(?:function|procedure) (?:{escaped_schema}\.)?{escaped_function}\(\)"
    )
    return re.fullmatch(expected, _normalized_sqlite_ddl(definition)) is not None


def _postgresql_gateway_trigger_ownership_is_canonical(
    state: Mapping[str, object],
) -> bool:
    """Require every Gateway trigger function to be owned with its table.

    This closes the interval in which a distinct function owner could replace
    a guard body after bootstrap. The normal migrations create both relation
    and function under the same deployment role; a deliberately mixed-owner
    schema therefore fails closed instead of treating only a name/source as
    authority.
    """
    table_owner = state.get("table_owner")
    function_owner = state.get("function_owner")
    return (
        isinstance(table_owner, int)
        and isinstance(function_owner, int)
        and table_owner == function_owner
    )


def _postgresql_trusted_schema(engine) -> str:
    """Return the schema expressly selected for this migration connection.

    The normal deployment schema is PostgreSQL's dialect default (``public``).
    A non-public migration schema must be selected in the database URL with a
    single ``-csearch_path=<schema>`` option, which is also how the migration
    tests create disposable schemas.  Do not infer a schema from a mutable
    runtime search path: that would let a same-named decoy table authenticate
    the wrong append-only boundary.
    """
    options = engine.url.query.get("options")
    if isinstance(options, tuple):
        option_values = options
    elif isinstance(options, str):
        option_values = (options,)
    elif options is None:
        option_values = ()
    else:
        raise _gateway_trigger_error()

    configured_paths: list[str] = []
    for option_value in option_values:
        match = re.search(
            r"(?:^|\s)-c\s*search_path=([^\s]+)",
            option_value,
        )
        if match is not None:
            configured_paths.append(match.group(1))
    if len(configured_paths) > 1:
        raise _gateway_trigger_error()
    schema_name = (
        configured_paths[0]
        if configured_paths
        else engine.dialect.default_schema_name
    )
    if not isinstance(schema_name, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*",
        schema_name,
    ):
        raise _gateway_trigger_error()
    return schema_name


def _require_postgresql_gateway_immutable_triggers(
    engine,
    *,
    contracts: Mapping[tuple[str, str], tuple[int, str, str]],
) -> None:
    schema_name = _postgresql_trusted_schema(engine)
    with engine.connect() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
        version_relation = connection.scalar(
            text(
                "SELECT to_regclass("
                "format('%I.%I', CAST(:schema_name AS text), 'alembic_version'))"
            ),
            {"schema_name": schema_name},
        )
        if current_schema != schema_name or version_relation is None:
            raise _gateway_trigger_error()
        states = connection.execute(
            text(_POSTGRESQL_GATEWAY_TRIGGER_STATE_SQL),
            {"schema_name": schema_name},
        ).mappings().all()
    states_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for state in states:
        table_name = state.get("table_name")
        trigger_name = state.get("trigger_name")
        if not isinstance(table_name, str) or not isinstance(trigger_name, str):
            raise _gateway_trigger_error()
        key = (table_name, trigger_name)
        if key not in contracts or key in states_by_key:
            raise _gateway_trigger_error()
        states_by_key[key] = state
    if set(states_by_key) != set(contracts):
        raise _gateway_trigger_error()
    for (table_name, trigger_name), (
        trigger_type,
        function_name,
        function_source_sha256,
    ) in contracts.items():
        if not _postgresql_gateway_trigger_state_is_canonical(
            states_by_key[(table_name, trigger_name)],
            table_name=table_name,
            trigger_name=trigger_name,
            trigger_type=trigger_type,
            function_name=function_name,
            function_source_sha256=function_source_sha256,
            schema_name=schema_name,
        ):
            raise _gateway_trigger_error()


def _professional_team_guard_error() -> UnmanagedDatabaseSchemaError:
    return UnmanagedDatabaseSchemaError(
        "database lacks a canonical professional team guard"
    )


def _require_professional_team_guards(engine) -> None:
    """Fail closed unless every current 0075 professional-team guard is canonical."""
    if engine.dialect.name == "sqlite":
        _require_sqlite_professional_team_guards(engine)
        return
    if engine.dialect.name == "postgresql":
        _require_postgresql_professional_team_guards(engine)
        return
    raise _professional_team_guard_error()


def _require_sqlite_professional_team_guards(engine) -> None:
    table_parameters = {
        f"table_{index}": table_name
        for index, table_name in enumerate(_PROFESSIONAL_TEAM_TABLE_NAMES)
    }
    table_placeholders = ", ".join(f":{name}" for name in table_parameters)
    with engine.connect() as connection:
        trigger_rows = connection.execute(
            text(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name IN ("
                f"{table_placeholders})"
            ),
            table_parameters,
        ).mappings()
        trigger_by_name = {
            row["name"]: (row["tbl_name"], row["sql"])
            for row in trigger_rows
            if isinstance(row["name"], str)
            and isinstance(row["tbl_name"], str)
            and isinstance(row["sql"], str)
        }
    if set(trigger_by_name) != set(_SQLITE_PROFESSIONAL_TEAM_GUARD_CONTRACTS):
        raise _professional_team_guard_error()
    for trigger_name, (expected_table, expected_hash) in (
        _SQLITE_PROFESSIONAL_TEAM_GUARD_CONTRACTS.items()
    ):
        actual_table, trigger_sql = trigger_by_name[trigger_name]
        if actual_table != expected_table:
            raise _professional_team_guard_error()
        if (
            hashlib.sha256(
                _normalized_sqlite_ddl(trigger_sql).encode("utf-8")
            ).hexdigest()
            != expected_hash
        ):
            raise _professional_team_guard_error()


def _require_postgresql_professional_team_guards(engine) -> None:
    schema_name = _postgresql_trusted_schema(engine)
    with engine.connect() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
        version_relation = connection.scalar(
            text(
                "SELECT to_regclass("
                "format('%I.%I', CAST(:schema_name AS text), 'alembic_version'))"
            ),
            {"schema_name": schema_name},
        )
        if current_schema != schema_name or version_relation is None:
            raise _professional_team_guard_error()
        states = connection.execute(
            text(_POSTGRESQL_PROFESSIONAL_TEAM_TRIGGER_STATE_SQL),
            {"schema_name": schema_name},
        ).mappings().all()
    states_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for state in states:
        table_name = state.get("table_name")
        trigger_name = state.get("trigger_name")
        if not isinstance(table_name, str) or not isinstance(trigger_name, str):
            raise _professional_team_guard_error()
        key = (table_name, trigger_name)
        if key not in _POSTGRESQL_PROFESSIONAL_TEAM_GUARD_CONTRACTS:
            raise _professional_team_guard_error()
        if key in states_by_key:
            raise _professional_team_guard_error()
        states_by_key[key] = state
    if set(states_by_key) != set(_POSTGRESQL_PROFESSIONAL_TEAM_GUARD_CONTRACTS):
        raise _professional_team_guard_error()
    for (table_name, trigger_name), (
        trigger_type,
        function_name,
        function_source_sha256,
    ) in _POSTGRESQL_PROFESSIONAL_TEAM_GUARD_CONTRACTS.items():
        if not _postgresql_gateway_trigger_state_is_canonical(
            states_by_key[(table_name, trigger_name)],
            table_name=table_name,
            trigger_name=trigger_name,
            trigger_type=trigger_type,
            function_name=function_name,
            function_source_sha256=function_source_sha256,
            schema_name=schema_name,
        ):
            raise _professional_team_guard_error()


def _require_company_event_relational_contract(inspector) -> None:
    company_event_columns = {
        column["name"]: column
        for column in inspector.get_columns(_COMPANY_EVENTS)
    }
    company_event_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(_COMPANY_EVENTS)
    }
    company_event_indexes = {
        index["name"] for index in inspector.get_indexes(_COMPANY_EVENTS)
    }
    company_event_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(_COMPANY_EVENTS)
    }
    company_event_foreign_keys = {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspector.get_foreign_keys(_COMPANY_EVENTS)
    }
    required_checks = {
        "ck_uw_company_research_event_content_hash",
        "ck_uw_company_research_event_sequence",
        "ck_uw_company_research_event_hash_version",
        "ck_uw_company_research_event_predecessor_hash",
        "ck_uw_company_research_event_payload_shape",
    }
    required_indexes = {"ix_uw_company_research_event_preparation"}
    expected_columns = {
        "id",
        "preparation_id",
        "sequence",
        "hash_version",
        "previous_event_hash",
        "event_type",
        "payload",
        "content_hash",
        "created_at",
    }
    incompatible_extra_columns = {
        name
        for name, column in company_event_columns.items()
        if name not in expected_columns
        and (
            column.get("computed") is not None
            or column.get("identity") is not None
            or (
                not column.get("nullable", True)
                and column.get("default") is None
            )
        )
    }
    if (
        set(company_event_columns) < expected_columns
        or incompatible_extra_columns
        or company_event_columns["hash_version"]["nullable"]
        or company_event_columns["hash_version"].get("default") is not None
        or not required_checks.issubset(company_event_checks)
        or not required_indexes.issubset(company_event_indexes)
        or "uq_uw_company_research_event_sequence" not in company_event_uniques
        or (
            ("preparation_id",),
            "uw_company_research_preparations",
            ("id",),
        )
        not in company_event_foreign_keys
    ):
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and is not compatible with "
            "the current company research event schema"
        )


def _repair_company_event_schema(engine) -> None:
    with engine.begin() as connection:
        try:
            repair_company_event_schema(connection)
        except (CompanyResearchEventSchemaError, ValueError, TypeError) as exc:
            raise UnmanagedDatabaseSchemaError(
                "cannot authenticate or repair company research event schema"
            ) from exc


def _install_company_worker_index(engine) -> None:
    """Install the 0070 worker index for ORM-created unmanaged databases."""
    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX IF EXISTS {_COMPANY_WORKER_INDEX}"))
        connection.execute(
            text(
                f"CREATE INDEX {_COMPANY_WORKER_INDEX} ON jobs "
                f"(status, created_at, id) WHERE {_COMPANY_WORKER_INDEX_PREDICATE}"
            )
        )


def _postgresql_company_worker_index_is_canonical(
    state: Mapping[str, object] | None,
) -> bool:
    return bool(
        state is not None
        and state.get("indisvalid") is True
        and state.get("indisready") is True
        and state.get("access_method") == "btree"
        and state.get("indnkeyatts") == len(_COMPANY_WORKER_INDEX_COLUMNS)
        and tuple(state.get("key_columns") or ())
        == _COMPANY_WORKER_INDEX_COLUMNS
        and tuple(state.get("descending") or ()) == (False, False, False)
        and tuple(state.get("nulls_first") or ()) == (False, False, False)
    )


def _company_worker_index_physical_definition_is_canonical(engine) -> bool:
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            index_row = next(
                (
                    row
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list('jobs')"
                    )
                    if row[1] == _COMPANY_WORKER_INDEX
                ),
                None,
            )
            if (
                index_row is None
                or index_row[2] != 0
                or index_row[3] != "c"
                or index_row[4] != 1
            ):
                return False
            key_rows = tuple(
                row
                for row in connection.exec_driver_sql(
                    f"PRAGMA index_xinfo('{_COMPANY_WORKER_INDEX}')"
                )
                if row[5] == 1
            )
            return tuple(
                (row[2], row[3], row[4]) for row in key_rows
            ) == tuple(
                (column, 0, "BINARY")
                for column in _COMPANY_WORKER_INDEX_COLUMNS
            )
        if connection.dialect.name != "postgresql":
            return False
        state = connection.execute(
            text(_POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL),
            {"table_name": "jobs", "index_name": _COMPANY_WORKER_INDEX},
        ).mappings().one_or_none()
        return _postgresql_company_worker_index_is_canonical(state)


def _require_company_worker_index(engine) -> None:
    inspector = inspect(engine)
    indexes = {
        index["name"]: index for index in inspector.get_indexes("jobs")
    }
    index = indexes.get(_COMPANY_WORKER_INDEX)
    if (
        index is None
        or tuple(index.get("column_names") or ())
        != _COMPANY_WORKER_INDEX_COLUMNS
        or index.get("unique", False)
    ):
        raise UnmanagedDatabaseSchemaError(
            "database lacks the company research worker candidate index"
        )
    dialect_options = index.get("dialect_options") or {}
    predicate = dialect_options.get(
        "sqlite_where"
        if engine.dialect.name == "sqlite"
        else "postgresql_where"
    )
    if predicate is None:
        raise UnmanagedDatabaseSchemaError(
            "database lacks the company research worker candidate index"
        )
    if _normalize_company_worker_predicate(
        predicate
    ) != _normalize_company_worker_predicate(
        _COMPANY_WORKER_INDEX_PREDICATE
    ):
        raise UnmanagedDatabaseSchemaError(
            "database has an invalid company research worker candidate index"
        )
    if not _company_worker_index_physical_definition_is_canonical(engine):
        raise UnmanagedDatabaseSchemaError(
            "database has an invalid company research worker candidate index"
        )


def require_company_research_event_schema(database_url: str) -> None:
    """Fail startup closed unless the timestamp-bound event schema is active."""
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if "alembic_version" not in tables or _COMPANY_EVENTS not in tables:
            raise UnmanagedDatabaseSchemaError(
                "database is not at the company research event schema"
            )
        columns = {
            column["name"]: column
            for column in inspector.get_columns(_COMPANY_EVENTS)
        }
        if (
            "hash_version" not in columns
            or columns["hash_version"]["nullable"]
            or columns["hash_version"].get("default") is not None
        ):
            raise UnmanagedDatabaseSchemaError(
                "database is not at the company research event schema"
            )
        _require_company_event_relational_contract(inspector)
        _repair_company_event_schema(engine)
        _require_company_worker_index(engine)
    finally:
        engine.dispose()
