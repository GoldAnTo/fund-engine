"""Alembic contracts for the FundClaw Gateway persistence slice."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.models.research_gateway import (
    ArtifactReference,
    GatewayCommand,
    GatewayIdempotencyRequest,
    ResearchConversation,
    ResearchIntent,
    ResearchMessage,
    ResearchRunSpec,
    RoleEvent,
    RoleRun,
    canonical_role_event_source_key,
)
from app.repositories.research_gateway import ResearchGatewayRepository
from sqlalchemy.orm import Session

GATEWAY_TABLES = {
    "research_conversations",
    "gateway_idempotency_requests",
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_runs",
    "role_events",
    "gateway_commands",
}
IMMUTABLE_GATEWAY_TABLES = (
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_events",
    "gateway_commands",
)
LEGACY_0072_GATEWAY_TRIGGER_NAMES = frozenset(
    f"no_{operation}_{table_name}"
    for table_name in IMMUTABLE_GATEWAY_TABLES
    for operation in ("update", "delete")
)


def _alembic_config(database_url: str) -> Config:
    backend = Path(__file__).parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _gateway_trigger_names(connection: sa.Connection) -> set[str]:
    """Return every non-internal SQLite trigger on a Gateway table."""
    placeholders = ", ".join(f":table_{index}" for index in range(len(GATEWAY_TABLES)))
    parameters = {
        f"table_{index}": table_name
        for index, table_name in enumerate(sorted(GATEWAY_TABLES))
    }
    return {
        str(row[0])
        for row in connection.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                f"AND tbl_name IN ({placeholders})"
            ),
            parameters,
        )
    }


def _schema_database_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


def _seed_gateway_rows(connection: sa.Connection) -> dict[str, uuid.UUID]:
    now = datetime.now(UTC)
    with Session(bind=connection, future=True) as session:
        native_case = ResearchCase(
            id=uuid.uuid4(),
            title="migration case",
            industry_topic="semiconductor",
            created_by="human:alice",
            created_at=now,
        )
        session.add(native_case)
        session.flush()
        native_run = ResearchRun(
            id=uuid.uuid4(),
            research_case_id=native_case.id,
            created_at=now,
            updated_at=now,
        )
        conversation = ResearchConversation(
            id=uuid.uuid4(),
            tenant_id="team-a",
            owner_subject_id="alice",
            visibility="private",
            event_retention_floor=1,
            next_message_sequence=2,
            next_event_sequence=2,
            created_at=now,
            updated_at=now,
        )
        request = GatewayIdempotencyRequest(
            id=uuid.uuid4(),
            tenant_id="team-a",
            subject_id="alice",
            operation="send_message",
            client_key="migration-key",
            request_fingerprint="a" * 64,
            status="in_progress",
            attempt=1,
            created_at=now,
            updated_at=now,
        )
        command_request = GatewayIdempotencyRequest(
            id=uuid.uuid4(),
            tenant_id="team-a",
            subject_id="alice",
            operation="issue_command",
            client_key="migration-command-key",
            request_fingerprint="b" * 64,
            status="in_progress",
            attempt=1,
            created_at=now,
            updated_at=now,
        )
        message = ResearchMessage(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            tenant_id="team-a",
            subject_id="alice",
            sequence=1,
            message_kind="user",
            content="研究供应链",
            input_sha256=hashlib.sha256("研究供应链".encode()).hexdigest(),
            created_at=now,
        )
        session.add_all((native_run, conversation, request, command_request, message))
        session.flush()
        intent = ResearchIntent(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            message_id=message.id,
            gateway_request_id=request.id,
            tenant_id="team-a",
            subject_id="alice",
            intent_kind="start",
            status="queued",
            input_sha256=message.input_sha256,
            created_at=now,
        )
        session.add(intent)
        session.flush()
        run_spec = ResearchRunSpec(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            intent_id=intent.id,
            gateway_request_id=request.id,
            tenant_id="team-a",
            subject_id="alice",
            native_case_id=native_case.id,
            native_run_id=native_run.id,
            frozen_scope={"question": "供应链"},
            frozen_cutoff={"as_of": "2026-09-04"},
            frozen_source_policy={"allowed_source_types": ["company_disclosure"]},
            role_manifest_version="fundclaw-roles.v1",
            capability_manifest_version="fundclaw-capabilities.v1",
            correlation_id="migration-correlation",
            input_artifact_refs=[],
            created_at=now,
        )
        session.add(run_spec)
        session.flush()
        role_run = RoleRun(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            run_spec_id=run_spec.id,
            tenant_id="team-a",
            subject_id="alice",
            role_key="scope_identity",
            status="queued",
            attempt=0,
            next_event_sequence=1,
            created_at=now,
            updated_at=now,
        )
        session.add(role_run)
        session.flush()
        event = RoleEvent(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            run_spec_id=run_spec.id,
            role_run_id=role_run.id,
            tenant_id="team-a",
            subject_id="alice",
            sequence=1,
            run_sequence=1,
            role_key="scope_identity",
            event_type="role_started",
            status="running",
            artifact_refs=[],
            source_key="migration:event:1",
            created_at=now,
        )
        command_row = GatewayCommand(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            run_spec_id=run_spec.id,
            gateway_request_id=command_request.id,
            tenant_id="team-a",
            subject_id="alice",
            actor_subject_id="alice",
            command_kind="pause",
            target_hash="b" * 64,
            outcome="rejected",
            reason_code="unsupported_in_gateway_p0",
            created_at=now,
        )
        session.add_all((event, command_row))
        ids = {
            "research_conversations": conversation.id,
            "gateway_idempotency_requests": request.id,
            "gateway_command_request": command_request.id,
            "native_case": native_case.id,
            "native_run": native_run.id,
            "research_messages": message.id,
            "research_intents": intent.id,
            "research_run_specs": run_spec.id,
            "role_runs": role_run.id,
            "role_events": event.id,
            "gateway_commands": command_row.id,
        }
        session.commit()
    return ids


def _seed_crashed_gateway_run_spec(connection: sa.Connection) -> dict[str, uuid.UUID]:
    """Commit precisely at the crash point before role initialization."""
    now = datetime.now(UTC)
    with Session(bind=connection, future=True) as session:
        repository = ResearchGatewayRepository(session)
        request, acquired = repository.acquire_request(
            tenant_id="team-a",
            subject_id="alice",
            operation="send_message",
            client_key="crash-recovery-key",
            request_fingerprint="c" * 64,
        )
        assert acquired is True
        conversation = repository.create_conversation(
            tenant_id="team-a", owner_subject_id="alice", title="crash recovery"
        )
        message = repository.append_message(
            conversation_id=conversation.id,
            tenant_id="team-a",
            subject_id="alice",
            text="恢复四角色初始化",
        )
        intent = repository.append_intent(
            conversation_id=conversation.id,
            message_id=message.id,
            tenant_id="team-a",
            subject_id="alice",
            intent_kind="start",
            input_sha256=message.input_sha256,
            gateway_request_id=request.id,
        )
        native_case = ResearchCase(
            id=uuid.uuid4(),
            title="crash recovery case",
            industry_topic="semiconductor",
            created_by="human:alice",
            created_at=now,
        )
        session.add(native_case)
        session.flush()
        native_run = ResearchRun(
            id=uuid.uuid4(),
            research_case_id=native_case.id,
            created_at=now,
            updated_at=now,
        )
        session.add(native_run)
        session.flush()
        run_spec = repository.create_run_spec(
            conversation_id=conversation.id,
            intent_id=intent.id,
            tenant_id="team-a",
            subject_id="alice",
            native_case_id=native_case.id,
            native_run_id=native_run.id,
            frozen_scope={"question": "crash recovery"},
            frozen_cutoff={"as_of": "2026-09-04"},
            frozen_source_policy={"allowed_source_types": ["company_disclosure"]},
            role_manifest_version="fundclaw-roles.v1",
            capability_manifest_version="fundclaw-capabilities.v1",
            correlation_id="crash-recovery-correlation",
            input_artifact_refs=[],
            gateway_request_id=request.id,
        )
        # Deliberately no initialize_role_runs(), initial events, or receipt.
        session.commit()
        return {
            "request": request.id,
            "conversation": conversation.id,
            "run_spec": run_spec.id,
        }


def test_0072_gateway_migration_is_private_constrained_and_append_only(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-p0.db'}"
    config = _alembic_config(database_url)

    command.upgrade(config, "0072")

    engine = sa.create_engine(database_url, future=True)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert GATEWAY_TABLES.issubset(inspector.get_table_names())
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0072"
        )
        conversation_checks = {
            check["name"]: check.get("sqltext", check.get("sql"))
            for check in inspector.get_check_constraints("research_conversations")
        }
        assert "private" in str(
            conversation_checks["ck_research_conversations_private_visibility"]
        )
        role_checks = {
            check["name"]: check.get("sqltext", check.get("sql"))
            for check in inspector.get_check_constraints("role_runs")
        }
        assert "scope_identity" in str(role_checks["ck_role_runs_role_key"])
        assert "compilation_checks" in str(role_checks["ck_role_runs_role_key"])
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("role_events")
        }.issuperset(
            {
                "uq_role_events_conversation_sequence",
                "uq_role_events_run_sequence",
                "uq_role_events_native_source",
            }
        )
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("research_run_specs")
        }.issuperset({"uq_research_run_specs_gateway_request"})
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("gateway_commands")
        }.issuperset({"uq_gateway_commands_gateway_request"})
        assert {
            index["name"] for index in inspector.get_indexes("research_conversations")
        }.issuperset({"ix_research_conversations_owner_recent"})
        message_columns = {
            column["name"] for column in inspector.get_columns("research_messages")
        }
        assert not {"context_pack", "raw_trace"}.intersection(message_columns)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        for table_name in IMMUTABLE_GATEWAY_TABLES:
            row_id = ids[table_name]
            for statement in (
                f"UPDATE {table_name} SET created_at = created_at WHERE id = :id",
                f"DELETE FROM {table_name} WHERE id = :id",
            ):
                savepoint = connection.begin_nested()
                try:
                    with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                        connection.execute(sa.text(statement), {"id": row_id.hex})
                finally:
                    savepoint.rollback()

    command.downgrade(config, "0071")
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0071"
        )
        assert not GATEWAY_TABLES.intersection(sa.inspect(connection).get_table_names())
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


def test_0073_sqlite_upgrades_clean_legacy_0072_to_hardened_gateway_contract(
    tmp_path,
) -> None:
    """A released 0072 database advances without rewriting its history."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-0072-clean.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        assert _gateway_trigger_names(connection) == LEGACY_0072_GATEWAY_TRIGGER_NAMES
        assert "ck_role_events_source_key_digest" not in {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints("role_events")
        }

    command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        assert _gateway_trigger_names(connection) > LEGACY_0072_GATEWAY_TRIGGER_NAMES
        assert "ck_role_events_source_key_digest" in {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints("role_events")
        }
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0073_sqlite_refuses_unsafe_legacy_0072_history_before_hardening(
    tmp_path,
) -> None:
    """Unsafe immutable legacy rows fail closed instead of being rewritten."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-0072-unsafe.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        connection.execute(
            sa.text(
                "INSERT INTO role_events "
                "(id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                "sequence, run_sequence, role_key, event_type, status, reason_code, "
                "display_text, artifact_refs_json, source_key, created_at) "
                "SELECT :id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                "2, 2, role_key, 'role_progress', 'running', NULL, :display_text, "
                "artifact_refs_json, :source_key, created_at "
                "FROM role_events WHERE id = :event_id"
            ),
            {
                "id": uuid.uuid4().hex,
                "event_id": ids["role_events"].hex,
                "display_text": "Authorization: Bearer SUPERSECRET",
                "source_key": "legacy:raw-provider-event",
            },
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="unsafe legacy Gateway history"):
        command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        assert _gateway_trigger_names(connection) == LEGACY_0072_GATEWAY_TRIGGER_NAMES
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM role_events")) == 2
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


@pytest.mark.parametrize(
    ("unsafe_column", "unsafe_value"),
    (
        ("source_key", b"sha256:" + b"a" * 64),
        ("artifact_refs_json", b"[]"),
    ),
)
def test_0073_sqlite_refuses_legacy_blob_event_history_before_rebuild(
    tmp_path,
    unsafe_column: str,
    unsafe_value: bytes,
) -> None:
    """A released SQLite row cannot smuggle BLOB data through 0073 preflight."""
    database_url = f"sqlite:///{tmp_path / f'gateway-legacy-blob-{unsafe_column}.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        values: dict[str, object] = {
            "id": uuid.uuid4().hex,
            "event_id": ids["role_events"].hex,
            "source_key": "sha256:" + "b" * 64,
            "artifact_refs_json": "[]",
        }
        values[unsafe_column] = unsafe_value
        connection.execute(
            sa.text(
                "INSERT INTO role_events "
                "(id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                "sequence, run_sequence, role_key, event_type, status, reason_code, "
                "display_text, artifact_refs_json, source_key, created_at) "
                "SELECT :id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                "2, 2, role_key, 'role_started', 'running', NULL, 'Role started', "
                ":artifact_refs_json, :source_key, created_at "
                "FROM role_events WHERE id = :event_id"
            ),
            values,
        )
        connection.commit()
        assert connection.scalar(
            sa.text(
                f"SELECT typeof({unsafe_column}) FROM role_events WHERE id = :id"
            ),
            {"id": values["id"]},
        ) == "blob"

    with pytest.raises(
        RuntimeError,
        match=rf"unsafe legacy Gateway history in role_events\.{unsafe_column}",
    ):
        command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        assert _gateway_trigger_names(connection) == LEGACY_0072_GATEWAY_TRIGGER_NAMES
    engine.dispose()


def test_0073_sqlite_refuses_legacy_blob_digest_before_rebuild(tmp_path) -> None:
    """A BLOB fingerprint cannot exploit SQLite length/replace coercion."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-blob-fingerprint.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO gateway_idempotency_requests "
                "(id, tenant_id, subject_id, operation, client_key, request_fingerprint, "
                "status, attempt, created_at, updated_at) "
                "VALUES (:id, 'team-a', 'alice', 'send_message', 'blob-fingerprint', "
                ":request_fingerprint, 'in_progress', 1, :now, :now)"
            ),
            {
                "id": uuid.uuid4().hex,
                "request_fingerprint": b"a" * 64,
                "now": datetime.now(UTC),
            },
        )
        assert connection.scalar(
            sa.text(
                "SELECT typeof(request_fingerprint) FROM gateway_idempotency_requests "
                "WHERE client_key = 'blob-fingerprint'"
            )
        ) == "blob"

    with pytest.raises(
        RuntimeError,
        match="unsafe legacy Gateway history in gateway_idempotency_requests.request_fingerprint",
    ):
        command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        assert _gateway_trigger_names(connection) == LEGACY_0072_GATEWAY_TRIGGER_NAMES
    engine.dispose()


def test_0073_direct_upgrade_refuses_tampered_legacy_guard_before_any_ddl(
    tmp_path,
) -> None:
    """Operators invoking Alembic directly get the same legacy fail-closed gate."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-0072-direct-tamper.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER no_update_role_events")
        before_tables = tuple(sorted(sa.inspect(connection).get_table_names()))
        before_constraints = tuple(
            sorted(
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "role_events"
                )
            )
        )

    with pytest.raises(RuntimeError, match="legacy Gateway 0072 append-only trigger"):
        command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        assert tuple(sorted(sa.inspect(connection).get_table_names())) == before_tables
        assert tuple(
            sorted(
                constraint["name"]
                for constraint in sa.inspect(connection).get_check_constraints(
                    "role_events"
                )
            )
        ) == before_constraints
        assert connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'no_update_role_events'"
            )
        ) is None
    engine.dispose()


def test_0073_sqlite_serializes_legacy_preflight_before_hardening(
    tmp_path, monkeypatch
) -> None:
    """A legacy unsafe writer cannot slip between preflight and 0073 guards."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-preflight-race.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)

    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("0073").module
    original_preflight = migration._require_safe_legacy_gateway_history
    writer_errors: list[sa.exc.DBAPIError] = []
    writer_finished = threading.Event()

    def inject_writer_after_preflight(bind: sa.Connection) -> None:
        original_preflight(bind)

        def write_unsafe_legacy_event() -> None:
            writer_engine = sa.create_engine(
                database_url,
                future=True,
                connect_args={"timeout": 0.1},
            )
            try:
                with writer_engine.begin() as writer:
                    writer.execute(
                        sa.text(
                            "INSERT INTO role_events "
                            "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                            "subject_id, sequence, run_sequence, role_key, event_type, "
                            "status, reason_code, display_text, artifact_refs_json, source_key, "
                            "created_at) "
                            "SELECT :id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                            "subject_id, 2, 2, role_key, 'role_progress', 'running', NULL, "
                            "'Role progress recorded', :artifact_refs_json, :source_key, created_at "
                            "FROM role_events WHERE id = :event_id"
                        ),
                        {
                            "id": uuid.uuid4().hex,
                            "event_id": ids["role_events"].hex,
                            "artifact_refs_json": (
                                '[{"kind":"evidence_link","id":"'
                                + str(uuid.uuid4())
                                + '","raw_tool_arguments":"Authorization: Bearer SUPERSECRET"}]'
                            ),
                            "source_key": "sha256:" + "e" * 64,
                        },
                    )
            except sa.exc.DBAPIError as error:  # expected SQLite lock result
                writer_errors.append(error)
            finally:
                writer_engine.dispose()
                writer_finished.set()

        writer_thread = threading.Thread(target=write_unsafe_legacy_event)
        writer_thread.start()
        writer_thread.join(timeout=2)
        assert writer_finished.is_set()
        assert len(writer_errors) == 1
        assert isinstance(writer_errors[0], sa.exc.OperationalError)

    monkeypatch.setattr(
        migration, "_require_safe_legacy_gateway_history", inject_writer_after_preflight
    )
    # Reuse the script directory whose revision module carries the test hook.
    class _PatchedScriptDirectory:
        @staticmethod
        def from_config(_config: Config) -> ScriptDirectory:
            return script

    monkeypatch.setattr(command, "ScriptDirectory", _PatchedScriptDirectory)
    command.upgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM role_events")) == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0073_sqlite_fk_enabled_hardening_stamps_before_releasing_writer_lock(
    tmp_path,
) -> None:
    """The real FK-enabled Alembic path commits hardened DDL with its stamp."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-fk-lock.db'}"
    config = _alembic_config(database_url)

    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    sa.event.listen(sa.engine.Engine, "connect", enable_foreign_keys)
    try:
        command.upgrade(config, "0072")
        command.upgrade(config, "0073")
        engine = sa.create_engine(database_url, future=True)
        try:
            with engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert (
                    connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                    == "0073"
                )
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        finally:
            engine.dispose()
    finally:
        sa.event.remove(sa.engine.Engine, "connect", enable_foreign_keys)


@pytest.mark.parametrize("start_revision", ("fresh", "0071"))
def test_0073_sqlite_rejects_replace_for_every_gateway_immutable_table(
    tmp_path,
    start_revision: str,
) -> None:
    """SQLite's default recursive-trigger setting cannot bypass append-only rows."""
    database_url = f"sqlite:///{tmp_path / f'gateway-replace-{start_revision}.db'}"
    config = _alembic_config(database_url)
    if start_revision != "fresh":
        command.upgrade(config, start_revision)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        ids = _seed_gateway_rows(connection)
        message_id = ids["research_messages"].hex
        replace_statements = (
            (
                "research_messages",
                sa.text(
                    "INSERT OR REPLACE INTO research_messages "
                    "(id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, content, input_sha256, created_at) "
                    "SELECT id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, 'MUTATED', input_sha256, created_at "
                    "FROM research_messages WHERE id = :id"
                ),
                message_id,
            ),
            (
                "research_intents",
                sa.text(
                    "INSERT OR REPLACE INTO research_intents "
                    "SELECT * FROM research_intents WHERE id = :id"
                ),
                ids["research_intents"].hex,
            ),
            (
                "research_run_specs",
                sa.text(
                    "INSERT OR REPLACE INTO research_run_specs "
                    "SELECT * FROM research_run_specs WHERE id = :id"
                ),
                ids["research_run_specs"].hex,
            ),
            (
                "role_events",
                sa.text(
                    "INSERT OR REPLACE INTO role_events "
                    "SELECT * FROM role_events WHERE id = :id"
                ),
                ids["role_events"].hex,
            ),
            (
                "gateway_commands",
                sa.text(
                    "INSERT OR REPLACE INTO gateway_commands "
                    "SELECT * FROM gateway_commands WHERE id = :id"
                ),
                ids["gateway_commands"].hex,
            ),
        )
        for table_name, statement, row_id in replace_statements:
            with pytest.raises(
                sa.exc.IntegrityError, match="append-only"
            ), connection.begin_nested():
                connection.execute(statement, {"id": row_id})
            assert connection.scalar(
                sa.text(f"SELECT COUNT(*) FROM {table_name}")
            ) == 1
        assert connection.scalar(
            sa.text("SELECT content FROM research_messages WHERE id = :id"),
            {"id": message_id},
        ) != "MUTATED"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0073_sqlite_replace_guards_cover_every_non_primary_unique_target(
    tmp_path,
) -> None:
    """A different inserted ID cannot REPLACE a row through a secondary key."""
    database_url = f"sqlite:///{tmp_path / 'gateway-replace-secondary-keys.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        with Session(bind=connection, future=True, expire_on_commit=False) as session:
            message_for_sequence = ResearchMessage(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                tenant_id="team-a",
                subject_id="alice",
                sequence=2,
                message_kind="user",
                content="secondary sequence target",
                input_sha256=hashlib.sha256(b"secondary sequence target").hexdigest(),
                created_at=now,
            )
            message_for_intent = ResearchMessage(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                tenant_id="team-a",
                subject_id="alice",
                sequence=3,
                message_kind="user",
                content="secondary intent target",
                input_sha256=hashlib.sha256(b"secondary intent target").hexdigest(),
                created_at=now,
            )
            intent_request = GatewayIdempotencyRequest(
                id=uuid.uuid4(),
                tenant_id="team-a",
                subject_id="alice",
                operation="send_message",
                client_key="replace-intent-request",
                request_fingerprint="c" * 64,
                status="in_progress",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            intent_for_request = ResearchIntent(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                message_id=message_for_intent.id,
                gateway_request_id=intent_request.id,
                tenant_id="team-a",
                subject_id="alice",
                intent_kind="start",
                status="queued",
                input_sha256=message_for_intent.input_sha256,
                created_at=now,
            )
            request_native_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            native_target_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            request_target_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            alternate_native_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            run_spec_request = GatewayIdempotencyRequest(
                id=uuid.uuid4(),
                tenant_id="team-a",
                subject_id="alice",
                operation="send_message",
                client_key="replace-run-spec-request",
                request_fingerprint="d" * 64,
                status="in_progress",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            run_spec_for_native_target = ResearchRunSpec(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                intent_id=intent_for_request.id,
                tenant_id="team-a",
                subject_id="alice",
                native_case_id=ids["native_case"],
                native_run_id=native_target_run.id,
                frozen_scope={"question": "replace native target"},
                frozen_cutoff={"as_of": "2026-09-04"},
                frozen_source_policy={"allowed_source_types": []},
                role_manifest_version="fundclaw-roles.v1",
                capability_manifest_version="fundclaw-capabilities.v1",
                correlation_id="replace-guard-native-target",
                input_artifact_refs=[],
                created_at=now,
            )
            run_spec_for_request_target = ResearchRunSpec(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                intent_id=intent_for_request.id,
                gateway_request_id=run_spec_request.id,
                tenant_id="team-a",
                subject_id="alice",
                native_case_id=ids["native_case"],
                native_run_id=request_target_run.id,
                frozen_scope={"question": "replace request target"},
                frozen_cutoff={"as_of": "2026-09-04"},
                frozen_source_policy={"allowed_source_types": []},
                role_manifest_version="fundclaw-roles.v1",
                capability_manifest_version="fundclaw-capabilities.v1",
                correlation_id="replace-guard-request-target",
                input_artifact_refs=[],
                created_at=now,
            )
            alternate_run_spec = ResearchRunSpec(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                intent_id=ids["research_intents"],
                tenant_id="team-a",
                subject_id="alice",
                native_case_id=ids["native_case"],
                native_run_id=alternate_native_run.id,
                frozen_scope={"question": "replace guard"},
                frozen_cutoff={"as_of": "2026-09-04"},
                frozen_source_policy={"allowed_source_types": []},
                role_manifest_version="fundclaw-roles.v1",
                capability_manifest_version="fundclaw-capabilities.v1",
                correlation_id="replace-guard-alternate-run",
                input_artifact_refs=[],
                created_at=now,
            )
            alternate_role_run = RoleRun(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                run_spec_id=alternate_run_spec.id,
                tenant_id="team-a",
                subject_id="alice",
                role_key="scope_identity",
                status="queued",
                attempt=0,
                next_event_sequence=1,
                created_at=now,
                updated_at=now,
            )
            session.add_all(
                (
                    message_for_sequence,
                    message_for_intent,
                    intent_request,
                    request_native_run,
                    native_target_run,
                    request_target_run,
                    alternate_native_run,
                    run_spec_request,
                )
            )
            session.flush()
            session.add_all(
                (
                    intent_for_request,
                    run_spec_for_native_target,
                    run_spec_for_request_target,
                    alternate_run_spec,
                    alternate_role_run,
                )
            )
            session.flush()
            session.commit()

        def new_id() -> str:
            return uuid.uuid4().hex

        replacement_statements = (
            (
                "research_messages conversation sequence",
                sa.text(
                    "INSERT OR REPLACE INTO research_messages "
                    "(id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, content, input_sha256, created_at) "
                    "SELECT :new_id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, 'MUTATED', input_sha256, created_at "
                    "FROM research_messages WHERE id = :old_id"
                ),
                {"new_id": new_id(), "old_id": message_for_sequence.id.hex},
            ),
            (
                "research intents gateway request",
                sa.text(
                    "INSERT OR REPLACE INTO research_intents "
                    "(id, conversation_id, message_id, parent_intent_id, "
                    "gateway_request_id, tenant_id, subject_id, intent_kind, status, "
                    "input_sha256, reason_code, created_at) "
                    "SELECT :new_id, conversation_id, message_id, parent_intent_id, "
                    "gateway_request_id, tenant_id, subject_id, intent_kind, status, "
                    "input_sha256, reason_code, created_at "
                    "FROM research_intents WHERE id = :old_id"
                ),
                {"new_id": new_id(), "old_id": intent_for_request.id.hex},
            ),
            (
                "research run specs native run",
                sa.text(
                    "INSERT OR REPLACE INTO research_run_specs "
                    "(id, conversation_id, intent_id, gateway_request_id, tenant_id, "
                    "subject_id, native_case_id, native_run_id, frozen_scope_json, "
                    "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                    "capability_manifest_version, correlation_id, "
                    "input_artifact_refs_json, created_at) "
                    "SELECT :new_id, conversation_id, intent_id, NULL, tenant_id, "
                    "subject_id, native_case_id, native_run_id, frozen_scope_json, "
                    "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                    "capability_manifest_version, correlation_id, "
                    "input_artifact_refs_json, created_at "
                    "FROM research_run_specs WHERE id = :old_id"
                ),
                {
                    "new_id": new_id(),
                    "old_id": run_spec_for_native_target.id.hex,
                },
            ),
            (
                "research run specs gateway request",
                sa.text(
                    "INSERT OR REPLACE INTO research_run_specs "
                    "(id, conversation_id, intent_id, gateway_request_id, tenant_id, "
                    "subject_id, native_case_id, native_run_id, frozen_scope_json, "
                    "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                    "capability_manifest_version, correlation_id, "
                    "input_artifact_refs_json, created_at) "
                    "SELECT :new_id, conversation_id, intent_id, gateway_request_id, "
                    "tenant_id, subject_id, native_case_id, :native_run_id, "
                    "frozen_scope_json, frozen_cutoff_json, frozen_source_policy_json, "
                    "role_manifest_version, capability_manifest_version, correlation_id, "
                    "input_artifact_refs_json, created_at "
                    "FROM research_run_specs WHERE id = :old_id"
                ),
                {
                    "new_id": new_id(),
                    "old_id": run_spec_for_request_target.id.hex,
                    "native_run_id": request_native_run.id.hex,
                },
            ),
            (
                "role events conversation sequence",
                sa.text(
                    "INSERT OR REPLACE INTO role_events "
                    "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                    "subject_id, sequence, run_sequence, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, source_key, created_at) "
                    "SELECT :new_id, conversation_id, :run_spec_id, :role_run_id, "
                    "tenant_id, subject_id, 1, 1, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, :source_key, created_at "
                    "FROM role_events WHERE id = :old_id"
                ),
                {
                    "new_id": new_id(),
                    "old_id": ids["role_events"].hex,
                    "run_spec_id": alternate_run_spec.id.hex,
                    "role_run_id": alternate_role_run.id.hex,
                    "source_key": "sha256:" + "d" * 64,
                },
            ),
            (
                "role events run sequence",
                sa.text(
                    "INSERT OR REPLACE INTO role_events "
                    "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                    "subject_id, sequence, run_sequence, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, source_key, created_at) "
                    "SELECT :new_id, conversation_id, run_spec_id, role_run_id, "
                    "tenant_id, subject_id, 2, 1, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, :source_key, created_at "
                    "FROM role_events WHERE id = :old_id"
                ),
                {
                    "new_id": new_id(),
                    "old_id": ids["role_events"].hex,
                    "source_key": "sha256:" + "e" * 64,
                },
            ),
            (
                "role events native source",
                sa.text(
                    "INSERT OR REPLACE INTO role_events "
                    "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                    "subject_id, sequence, run_sequence, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, source_key, created_at) "
                    "SELECT :new_id, conversation_id, run_spec_id, role_run_id, "
                    "tenant_id, subject_id, 2, 2, role_key, event_type, status, "
                    "reason_code, display_text, artifact_refs_json, source_key, created_at "
                    "FROM role_events WHERE id = :old_id"
                ),
                {"new_id": new_id(), "old_id": ids["role_events"].hex},
            ),
            (
                "gateway commands gateway request",
                sa.text(
                    "INSERT OR REPLACE INTO gateway_commands "
                    "(id, conversation_id, run_spec_id, gateway_request_id, tenant_id, "
                    "subject_id, actor_subject_id, command_kind, target_hash, "
                    "target_version, outcome, reason_code, created_at) "
                    "SELECT :new_id, conversation_id, run_spec_id, gateway_request_id, "
                    "tenant_id, subject_id, actor_subject_id, command_kind, target_hash, "
                    "target_version, outcome, reason_code, created_at "
                    "FROM gateway_commands WHERE id = :old_id"
                ),
                {"new_id": new_id(), "old_id": ids["gateway_commands"].hex},
            ),
        )
        for target, statement, parameters in replacement_statements:
            with pytest.raises(sa.exc.IntegrityError, match="append-only"), connection.begin_nested():
                connection.execute(statement, parameters)
            assert target
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0073_gateway_migration_binds_request_links_to_request_scope(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-request-scope.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        inspector = sa.inspect(connection)
        for table_name in (
            "research_intents",
            "research_run_specs",
            "gateway_commands",
        ):
            assert any(
                tuple(foreign_key["constrained_columns"])
                == ("gateway_request_id", "tenant_id", "subject_id")
                and foreign_key["referred_table"] == "gateway_idempotency_requests"
                and tuple(foreign_key["referred_columns"])
                == ("id", "tenant_id", "subject_id")
                for foreign_key in inspector.get_foreign_keys(table_name)
            )

        now = datetime.now(UTC)
        foreign_request = GatewayIdempotencyRequest(
            id=uuid.uuid4(),
            tenant_id="team-b",
            subject_id="bob",
            operation="send_message",
            client_key="foreign-scope",
            request_fingerprint="c" * 64,
            status="in_progress",
            attempt=1,
            created_at=now,
            updated_at=now,
        )
        with Session(bind=connection, future=True) as session:
            session.add(foreign_request)
            session.flush()
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        ResearchIntent(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            message_id=ids["research_messages"],
                            gateway_request_id=foreign_request.id,
                            tenant_id="team-a",
                            subject_id="alice",
                            intent_kind="start",
                            status="queued",
                            input_sha256=hashlib.sha256(b"foreign request").hexdigest(),
                            created_at=now,
                        )
                    )
                    session.flush()

    engine.dispose()


def test_0073_sqlite_upgrade_makes_a_request_link_to_one_intent(tmp_path) -> None:
    """A real 0072→0073 upgrade rejects a second intent for one request."""
    database_url = f"sqlite:///{tmp_path / 'gateway-request-intent-unique.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0071")
    engine = sa.create_engine(database_url, future=True)
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0071"
        )

    command.upgrade(config, "0073")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert (
            connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0073"
        )
        assert {
            constraint["name"]
            for constraint in sa.inspect(connection).get_unique_constraints(
                "research_intents"
            )
        }.issuperset({"uq_research_intents_gateway_request"})

        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            second_message = ResearchMessage(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                tenant_id="team-a",
                subject_id="alice",
                sequence=2,
                message_kind="user",
                content="second idempotency input",
                input_sha256=hashlib.sha256(b"second idempotency input").hexdigest(),
                created_at=now,
            )
            session.add(second_message)
            session.flush()
            # The explicit request uniqueness is present above. SQLite's
            # immutable REPLACE guard fires first for this conflicting insert,
            # which is equally fail-closed and prevents the second intent.
            with pytest.raises(
                sa.exc.IntegrityError, match="UNIQUE|append-only"
            ), session.begin_nested():
                session.add(
                    ResearchIntent(
                        id=uuid.uuid4(),
                        conversation_id=ids["research_conversations"],
                        message_id=second_message.id,
                        gateway_request_id=ids["gateway_idempotency_requests"],
                        tenant_id="team-a",
                        subject_id="alice",
                        intent_kind="start",
                        status="queued",
                        input_sha256=second_message.input_sha256,
                        created_at=now,
                    )
                )
                session.flush()

            nullable_messages = [
                ResearchMessage(
                    id=uuid.uuid4(),
                    conversation_id=ids["research_conversations"],
                    tenant_id="team-a",
                    subject_id="alice",
                    sequence=sequence,
                    message_kind="user",
                    content=f"nullable request input {sequence}",
                    input_sha256=hashlib.sha256(
                        f"nullable request input {sequence}".encode()
                    ).hexdigest(),
                    created_at=now,
                )
                for sequence in (3, 4)
            ]
            session.add_all(nullable_messages)
            session.flush()
            session.add_all(
                [
                    ResearchIntent(
                        id=uuid.uuid4(),
                        conversation_id=ids["research_conversations"],
                        message_id=message.id,
                        gateway_request_id=None,
                        tenant_id="team-a",
                        subject_id="alice",
                        intent_kind="grounded_question",
                        status="queued",
                        input_sha256=message.input_sha256,
                        created_at=now,
                    )
                    for message in nullable_messages
                ]
            )
            session.flush()

    engine.dispose()


def test_0073_sqlite_crash_recovery_backfills_fixed_roles_and_safe_events(
    tmp_path,
) -> None:
    """Recovery repairs a RunSpec committed before its four-role bootstrap."""
    database_url = f"sqlite:///{tmp_path / 'gateway-crash-recovery.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_crashed_gateway_run_spec(connection)
        # The connection itself owns the real SQLite transaction boundary;
        # preserve the committed crash point before opening a fresh process
        # session below.
        connection.commit()
        with Session(bind=connection, future=True) as crashed_session:
            assert crashed_session.get(ResearchRunSpec, ids["run_spec"]) is not None
            assert crashed_session.scalar(
                sa.select(sa.func.count(RoleRun.id)).where(
                    RoleRun.run_spec_id == ids["run_spec"]
                )
            ) == 0
            assert crashed_session.scalar(
                sa.select(sa.func.count(RoleEvent.id)).where(
                    RoleEvent.run_spec_id == ids["run_spec"]
                )
            ) == 0

    with Session(bind=engine, future=True) as recovery_session:
        repository = ResearchGatewayRepository(recovery_session)
        request = recovery_session.get(
            GatewayIdempotencyRequest, ids["request"]
        )
        assert request is not None
        receipt = repository.recover_or_return_receipt(request)
        assert receipt is not None
        assert receipt.run_spec_id == ids["run_spec"]
        assert receipt.status == "queued"
        assert receipt.latest_sequence == 4
        recovery_session.commit()

    with Session(bind=engine, future=True) as verify_session:
        role_runs = list(
            verify_session.scalars(
                sa.select(RoleRun)
                .where(RoleRun.run_spec_id == ids["run_spec"])
                .order_by(RoleRun.role_key)
            )
        )
        events = list(
            verify_session.scalars(
                sa.select(RoleEvent)
                .where(RoleEvent.run_spec_id == ids["run_spec"])
                .order_by(RoleEvent.run_sequence)
            )
        )
        assert [row.role_key for row in role_runs] == sorted(
            ("scope_identity", "sources_evidence", "analysis_counter_evidence", "compilation_checks")
        )
        assert [event.role_key for event in events] == [
            "scope_identity",
            "sources_evidence",
            "analysis_counter_evidence",
            "compilation_checks",
        ]
        assert [event.event_type for event in events] == ["role_queued"] * 4
        assert [event.status for event in events] == ["queued"] * 4
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert [event.run_sequence for event in events] == [1, 2, 3, 4]
        assert [event.source_key for event in events] == [
            canonical_role_event_source_key(
                f"gateway:run-spec:{ids['run_spec']}:role:{role_key}:queued"
            )
            for role_key in (
                "scope_identity",
                "sources_evidence",
                "analysis_counter_evidence",
                "compilation_checks",
            )
        ]
        request = verify_session.get(GatewayIdempotencyRequest, ids["request"])
        assert request is not None
        assert request.status == "completed"

    with Session(bind=engine, future=True) as replay_session:
        repository = ResearchGatewayRepository(replay_session)
        request = replay_session.get(GatewayIdempotencyRequest, ids["request"])
        assert request is not None
        replayed = repository.recover_or_return_receipt(request)
        assert replayed is not None
        assert replayed.latest_sequence == 4
        replay_session.commit()

    with Session(bind=engine, future=True) as final_session:
        assert final_session.scalar(
            sa.select(sa.func.count(RoleRun.id)).where(
                RoleRun.run_spec_id == ids["run_spec"]
            )
        ) == 4
        assert final_session.scalar(
            sa.select(sa.func.count(RoleEvent.id)).where(
                RoleEvent.run_spec_id == ids["run_spec"]
            )
        ) == 4

    engine.dispose()


def test_0073_sqlite_recovery_backfills_history_without_regressing_live_roles(
    tmp_path,
) -> None:
    """A real hardened database retains later mutable role state on recovery."""
    database_url = f"sqlite:///{tmp_path / 'gateway-partial-role-history.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_crashed_gateway_run_spec(connection)
        connection.commit()

    with Session(bind=engine, future=True) as partial_session:
        repository = ResearchGatewayRepository(partial_session)
        repository.initialize_role_runs(run_spec_id=ids["run_spec"])
        for role_key, event_type in (
            ("scope_identity", "role_started"),
            ("sources_evidence", "role_blocked"),
            ("analysis_counter_evidence", "role_completed"),
        ):
            repository.append_role_event(
                run_spec_id=ids["run_spec"],
                role_key=role_key,
                event_type=event_type,
                source_key=f"partial-db-history:{role_key}:{event_type}",
            )
        role_runs = {
            row.role_key: row
            for row in partial_session.scalars(
                sa.select(RoleRun).where(RoleRun.run_spec_id == ids["run_spec"])
            )
        }
        role_runs["scope_identity"].claim_token = "live-running-claim"
        role_runs["sources_evidence"].attempt = 2
        partial_session.commit()

    with Session(bind=engine, future=True) as recovery_session:
        repository = ResearchGatewayRepository(recovery_session)
        request = recovery_session.get(GatewayIdempotencyRequest, ids["request"])
        assert request is not None
        receipt = repository.recover_or_return_receipt(request)
        assert receipt is not None
        assert receipt.latest_sequence == 7
        recovery_session.commit()

    with Session(bind=engine, future=True) as verify_session:
        role_runs = {
            row.role_key: row
            for row in verify_session.scalars(
                sa.select(RoleRun).where(RoleRun.run_spec_id == ids["run_spec"])
            )
        }
        assert role_runs["scope_identity"].status == "running"
        assert role_runs["scope_identity"].claim_token == "live-running-claim"
        assert role_runs["sources_evidence"].status == "blocked"
        assert role_runs["sources_evidence"].attempt == 2
        assert role_runs["analysis_counter_evidence"].status == "completed"
        queued_events = list(
            verify_session.scalars(
                sa.select(RoleEvent)
                .where(RoleEvent.run_spec_id == ids["run_spec"])
                .where(RoleEvent.event_type == "role_queued")
                .order_by(RoleEvent.sequence)
            )
        )
        assert [event.role_key for event in queued_events] == [
            "scope_identity",
            "sources_evidence",
            "analysis_counter_evidence",
            "compilation_checks",
        ]
        assert [event.sequence for event in queued_events] == [4, 5, 6, 7]
        assert [event.run_sequence for event in queued_events] == [4, 5, 6, 7]
        assert verify_session.scalar(
            sa.select(ResearchConversation.next_event_sequence).where(
                ResearchConversation.id == ids["conversation"]
            )
        ) == 8
        assert verify_session.execute(sa.text("PRAGMA foreign_key_check")).all() == []

    engine.dispose()


def test_0073_sqlite_role_event_safe_payload_guards_reject_raw_leaks(tmp_path) -> None:
    """The real 0073 schema and repository cannot append raw event payloads."""
    database_url = f"sqlite:///{tmp_path / 'gateway-safe-role-event.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        with Session(bind=connection, future=True) as session:
            repository = ResearchGatewayRepository(session)
            safe = repository.append_role_event(
                run_spec_id=ids["research_run_specs"],
                role_key="scope_identity",
                event_type="role_started",
                source_key="native:research_run_event:safe-summary",
            )
            assert safe.display_text == "Role started"

            with pytest.raises(ValueError, match="server-owned safe summary"):
                repository.append_role_event(
                    run_spec_id=ids["research_run_specs"],
                    role_key="scope_identity",
                    event_type="role_progress",
                    source_key="native:research_run_event:unsafe-summary",
                    display_text=(
                        "Authorization: Bearer SUPERSECRET\\n"
                        "Traceback: provider tool arguments"
                    ),
                )
            assert session.scalar(
                sa.select(sa.func.count(RoleEvent.id)).where(
                    RoleEvent.run_spec_id == ids["research_run_specs"]
                )
            ) == 2
            session.commit()

        check_names = {
            constraint["name"]
            for constraint in sa.inspect(connection).get_check_constraints("role_events")
        }
        assert {
            "ck_role_events_display_text_bounded",
            "ck_role_events_display_text_safe",
            "ck_role_events_reason_code_safe",
            "ck_role_events_source_key_digest",
        }.issubset(check_names)

        now = datetime.now(UTC)
        raw_event = {
            "id": uuid.uuid4(),
            "conversation_id": ids["research_conversations"],
            "run_spec_id": ids["research_run_specs"],
            "role_run_id": ids["role_runs"],
            "tenant_id": "team-a",
            "subject_id": "alice",
            "sequence": 3,
            "run_sequence": 3,
            "role_key": "scope_identity",
            "event_type": "role_started",
            "status": "running",
            "artifact_refs_json": [],
            "source_key": "sha256:" + "a" * 64,
            "created_at": now,
        }
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_role_events_display_text_safe"
        ), connection.begin_nested():
            connection.execute(
                RoleEvent.__table__.insert().values(
                    **raw_event,
                    display_text="provider error: Authorization: Bearer SUPERSECRET",
                )
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_role_events_reason_code_safe"
        ), connection.begin_nested():
            connection.execute(
                RoleEvent.__table__.insert().values(
                    **{
                        **raw_event,
                        "id": uuid.uuid4(),
                        "sequence": 4,
                        "run_sequence": 4,
                        "source_key": "sha256:" + "b" * 64,
                        "display_text": "Role started",
                        "reason_code": "Traceback: raw provider response",
                    },
                )
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_role_events_source_key_digest"
        ), connection.begin_nested():
            connection.execute(
                RoleEvent.__table__.insert().values(
                    **{
                        **raw_event,
                        "id": uuid.uuid4(),
                        "sequence": 5,
                        "run_sequence": 5,
                        "source_key": "Authorization: Bearer SUPERSECRET",
                        "display_text": "Role started",
                    },
                )
            )
        leaking_prefixed_source_key = "sha256:" + (
            "Authorization:BearerSUPERSECRET" + "x" * 64
        )[:64]
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_role_events_source_key_digest"
        ), connection.begin_nested():
            connection.execute(
                RoleEvent.__table__.insert().values(
                    **{
                        **raw_event,
                        "id": uuid.uuid4(),
                        "sequence": 6,
                        "run_sequence": 6,
                        "source_key": leaking_prefixed_source_key,
                        "display_text": "Role started",
                    },
                )
            )

    engine.dispose()


def test_0073_sqlite_gateway_reason_and_target_version_guards_reject_raw_payloads(
    tmp_path,
) -> None:
    """Real 0073 checks keep immutable reason/version fields server-owned."""
    database_url = f"sqlite:///{tmp_path / 'gateway-safe-command-fields.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC).isoformat()
        message_id = uuid.uuid4().hex
        connection.execute(
            sa.text(
                "INSERT INTO research_messages "
                "(id, conversation_id, tenant_id, subject_id, sequence, message_kind, "
                "role_key, content, input_sha256, created_at) "
                "VALUES (:id, :conversation_id, 'team-a', 'alice', 2, 'user', NULL, "
                "'safe command field probe', :input_sha256, :created_at)"
            ),
            {
                "id": message_id,
                "conversation_id": ids["research_conversations"].hex,
                "input_sha256": "c" * 64,
                "created_at": now,
            },
        )
        unsafe_reason = "Authorization: Bearer SUPERSECRET raw provider traceback"
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_research_intents_reason_code_safe"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_intents "
                    "(id, conversation_id, message_id, parent_intent_id, gateway_request_id, "
                    "tenant_id, subject_id, intent_kind, status, input_sha256, reason_code, "
                    "created_at) VALUES (:id, :conversation_id, :message_id, NULL, NULL, "
                    "'team-a', 'alice', 'unsupported', 'rejected', :input_sha256, "
                    ":reason_code, :created_at)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "conversation_id": ids["research_conversations"].hex,
                    "message_id": message_id,
                    "input_sha256": "c" * 64,
                    "reason_code": unsafe_reason,
                    "created_at": now,
                },
            )

        def insert_command_request(request_id: uuid.UUID, client_key: str) -> None:
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_idempotency_requests "
                    "(id, tenant_id, subject_id, operation, client_key, "
                    "request_fingerprint, status, attempt, lease_expires_at, receipt_json, "
                    "created_at, updated_at) VALUES (:id, 'team-a', 'alice', "
                    "'issue_command', :client_key, :request_fingerprint, 'in_progress', "
                    "1, NULL, NULL, :created_at, :updated_at)"
                ),
                {
                    "id": request_id.hex,
                    "client_key": client_key,
                    "request_fingerprint": "d" * 64,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        raw_reason_request = uuid.uuid4()
        insert_command_request(raw_reason_request, "raw-command-reason")
        command_values = {
            "id": uuid.uuid4().hex,
            "conversation_id": ids["research_conversations"].hex,
            "run_spec_id": ids["research_run_specs"].hex,
            "tenant_id": "team-a",
            "subject_id": "alice",
            "actor_subject_id": "alice",
            "command_kind": "pause",
            "target_hash": "e" * 64,
            "outcome": "rejected",
            "created_at": now,
        }
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_gateway_commands_reason_code_safe"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_commands "
                    "(id, conversation_id, run_spec_id, gateway_request_id, tenant_id, "
                    "subject_id, actor_subject_id, command_kind, target_hash, target_version, "
                    "outcome, reason_code, created_at) VALUES (:id, :conversation_id, "
                    ":run_spec_id, :gateway_request_id, :tenant_id, :subject_id, "
                    ":actor_subject_id, :command_kind, :target_hash, NULL, :outcome, "
                    ":reason_code, :created_at)"
                ),
                {
                    **command_values,
                    "gateway_request_id": raw_reason_request.hex,
                    "reason_code": unsafe_reason,
                },
            )

        raw_version_request = uuid.uuid4()
        insert_command_request(raw_version_request, "raw-command-version")
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_gateway_commands_target_version_absent"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_commands "
                    "(id, conversation_id, run_spec_id, gateway_request_id, tenant_id, "
                    "subject_id, actor_subject_id, command_kind, target_hash, target_version, "
                    "outcome, reason_code, created_at) VALUES (:id, :conversation_id, "
                    ":run_spec_id, :gateway_request_id, :tenant_id, :subject_id, "
                    ":actor_subject_id, :command_kind, :target_hash, :target_version, "
                    ":outcome, 'unsupported_in_gateway_p0', :created_at)"
                ),
                {
                    **command_values,
                    "id": uuid.uuid4().hex,
                    "gateway_request_id": raw_version_request.hex,
                    "target_version": unsafe_reason,
                },
            )

        assert connection.scalar(sa.select(sa.func.count(ResearchIntent.id))) == 1
        assert connection.scalar(sa.select(sa.func.count(GatewayCommand.id))) == 1
        check_names = {
            constraint["name"]
            for table_name in ("research_intents", "gateway_commands")
            for constraint in sa.inspect(connection).get_check_constraints(table_name)
        }
        assert {
            "ck_research_intents_reason_code_safe",
            "ck_gateway_commands_reason_code_safe",
            "ck_gateway_commands_target_version_absent",
        }.issubset(check_names)

    engine.dispose()


def test_0073_sqlite_recovery_rebuilds_tampered_run_receipt_json(tmp_path) -> None:
    """A completed request ignores mutable receipt JSON and rebuilds safe facts."""
    database_url = f"sqlite:///{tmp_path / 'gateway-tampered-receipt.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        tampered = {
            "conversation_id": str(ids["research_conversations"]),
            "intent_id": str(ids["research_intents"]),
            "run_spec_id": str(ids["research_run_specs"]),
            "native_case_id": str(ids["native_case"]),
            "native_run_id": str(ids["native_run"]),
            "status": "Bearer SUPERSECRET raw provider traceback",
            "latest_sequence": 987654321,
            "raw_provider_error": "Authorization: Bearer SUPERSECRET",
        }
        connection.execute(
            sa.text(
                "UPDATE gateway_idempotency_requests SET status = 'completed', "
                "receipt_json = :receipt_json WHERE id = :request_id"
            ),
            {
                "receipt_json": json.dumps(tampered),
                "request_id": ids["gateway_idempotency_requests"].hex,
            },
        )
        connection.commit()

    with Session(bind=engine, future=True) as session:
        request = session.get(
            GatewayIdempotencyRequest, ids["gateway_idempotency_requests"]
        )
        assert request is not None
        receipt = ResearchGatewayRepository(session).recover_or_return_receipt(request)
        assert receipt is not None
        assert receipt.run_spec_id == ids["research_run_specs"]
        assert receipt.status == "queued"
        assert receipt.latest_sequence == session.scalar(
            sa.select(sa.func.max(RoleEvent.sequence)).where(
                RoleEvent.conversation_id == ids["research_conversations"]
            )
        )
        assert "SUPERSECRET" not in str(receipt.to_payload())
        assert request.receipt_json == receipt.to_payload()
        session.commit()

    engine.dispose()


@pytest.mark.parametrize(
    "unsafe_receipt",
    (
        '{"raw_provider_error":"Authorization: Bearer SUPERSECRET"}\x00hidden',
        b'{"raw_provider_error":"Authorization: Bearer SUPERSECRET"}',
        "not JSON",
        "[]",
    ),
)
def test_gateway_head_sqlite_rejects_unsafe_raw_receipt_cache(
    tmp_path,
    unsafe_receipt: str | bytes,
) -> None:
    """A mutable cache must be NUL-free text containing one JSON object."""
    database_url = f"sqlite:///{tmp_path / 'gateway-nul-receipt-cache.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        with pytest.raises(
            sa.exc.IntegrityError, match="Gateway receipt cache"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "UPDATE gateway_idempotency_requests SET receipt_json = :receipt "
                    "WHERE id = :request_id"
                ),
                {
                    "request_id": ids["gateway_idempotency_requests"].hex,
                    "receipt": unsafe_receipt,
                },
            )

    engine.dispose()


def test_gateway_head_sqlite_orm_clears_receipt_cache_as_sql_null(tmp_path) -> None:
    """The nullable cache retains ordinary ORM ``None`` semantics at 0074."""
    database_url = f"sqlite:///{tmp_path / 'gateway-orm-null-receipt-cache.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        ids = _seed_gateway_rows(connection)

    with Session(bind=engine, future=True) as session:
        request = session.get(
            GatewayIdempotencyRequest,
            ids["gateway_idempotency_requests"],
        )
        assert request is not None
        request.receipt_json = {"status": "safe-cache"}
        session.commit()
        request.receipt_json = None
        session.commit()

    with engine.connect() as connection:
        assert connection.scalar(
            sa.text(
                "SELECT receipt_json IS NULL FROM gateway_idempotency_requests "
                "WHERE id = :request_id"
            ),
            {"request_id": ids["gateway_idempotency_requests"].hex},
        ) == 1
    engine.dispose()


@pytest.mark.parametrize(
    "legacy_receipt",
    (
        '{"raw_provider_error":"SUPERSECRET"}\x00hidden',
        b'{"raw_provider_error":"SUPERSECRET"}',
        "not JSON",
        "[]",
    ),
)
def test_gateway_head_clears_legacy_unsafe_receipt_cache_before_orm_loads_it(
    tmp_path,
    legacy_receipt: str | bytes,
) -> None:
    """0074 discards a non-authoritative malformed 0073 cache before ORM load."""
    database_url = f"sqlite:///{tmp_path / 'gateway-legacy-unsafe-receipt-cache.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        connection.execute(
            sa.text(
                "UPDATE gateway_idempotency_requests SET receipt_json = :receipt "
                "WHERE id = :request_id"
            ),
            {
                "request_id": ids["gateway_idempotency_requests"].hex,
                "receipt": legacy_receipt,
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.scalar(
            sa.text(
                "SELECT receipt_json IS NULL FROM gateway_idempotency_requests "
                "WHERE id = :request_id"
            ),
            {"request_id": ids["gateway_idempotency_requests"].hex},
        ) == 1

    with Session(bind=engine, future=True) as session:
        request = session.get(
            GatewayIdempotencyRequest, ids["gateway_idempotency_requests"]
        )
        assert request is not None
        receipt = ResearchGatewayRepository(session).recover_or_return_receipt(request)
        assert receipt is not None
        assert request.receipt_json == receipt.to_payload()
        session.commit()

    engine.dispose()


def test_0073_sqlite_artifact_reference_guards_reject_raw_leaks(tmp_path) -> None:
    """Both immutable JSON columns reject raw provider payloads in 0073."""
    database_url = f"sqlite:///{tmp_path / 'gateway-artifact-reference-guards.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        leaking_reference = [
            {
                "kind": "evidence_link",
                "id": str(uuid.uuid4()),
                "source_body": "Authorization: Bearer SUPERSECRET",
            }
        ]
        raw_event = {
            "id": uuid.uuid4(),
            "conversation_id": ids["research_conversations"],
            "run_spec_id": ids["research_run_specs"],
            "role_run_id": ids["role_runs"],
            "tenant_id": "team-a",
            "subject_id": "alice",
            "sequence": 2,
            "run_sequence": 2,
            "role_key": "scope_identity",
            "event_type": "role_started",
            "status": "running",
            "display_text": "Role started",
            "artifact_refs_json": leaking_reference,
            "source_key": "sha256:" + "b" * 64,
            "created_at": now,
        }
        with pytest.raises(
            sa.exc.IntegrityError, match="artifact reference"
        ), connection.begin_nested():
            connection.execute(RoleEvent.__table__.insert().values(**raw_event))
        assert connection.scalar(
            sa.select(sa.func.count(RoleEvent.id)).where(
                RoleEvent.run_spec_id == ids["research_run_specs"]
            )
        ) == 1

        with Session(bind=connection, future=True) as session:
            native_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            session.add(native_run)
            session.flush()
            raw_run_spec = {
                "id": uuid.uuid4(),
                "conversation_id": ids["research_conversations"],
                "intent_id": ids["research_intents"],
                "tenant_id": "team-a",
                "subject_id": "alice",
                "native_case_id": ids["native_case"],
                "native_run_id": native_run.id,
                "frozen_scope_json": {"question": "raw artifact reference"},
                "frozen_cutoff_json": {"as_of": "2026-09-04"},
                "frozen_source_policy_json": {"allowed_source_types": []},
                "role_manifest_version": "fundclaw-roles.v1",
                "capability_manifest_version": "fundclaw-capabilities.v1",
                "correlation_id": "raw-artifact-reference",
                "input_artifact_refs_json": leaking_reference,
                "created_at": now,
            }
            with pytest.raises(
                sa.exc.IntegrityError, match="artifact reference"
            ), session.begin_nested():
                session.execute(ResearchRunSpec.__table__.insert().values(**raw_run_spec))
            assert session.scalar(
                sa.select(sa.func.count(ResearchRunSpec.id)).where(
                    ResearchRunSpec.native_run_id == native_run.id
                )
            ) == 0

    engine.dispose()


def test_0073_sqlite_artifact_reference_guards_accept_typed_references(tmp_path) -> None:
    """The real 0073 validators permit the closed typed-reference shape."""
    database_url = f"sqlite:///{tmp_path / 'gateway-artifact-reference-valid.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        reference = ArtifactReference(
            kind="evidence_link",
            id=uuid.uuid4(),
            case_id=ids["native_case"],
            locator_available=True,
        ).to_json()
        with Session(bind=connection, future=True) as session:
            native_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            message = ResearchMessage(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                tenant_id="team-a",
                subject_id="alice",
                sequence=2,
                message_kind="user",
                content="typed artifact input",
                input_sha256=hashlib.sha256(b"typed artifact input").hexdigest(),
                created_at=now,
            )
            session.add_all((native_run, message))
            session.flush()
            intent = ResearchIntent(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                message_id=message.id,
                tenant_id="team-a",
                subject_id="alice",
                intent_kind="grounded_question",
                status="queued",
                input_sha256=message.input_sha256,
                created_at=now,
            )
            session.add(intent)
            session.flush()
            run_spec = ResearchRunSpec(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                intent_id=intent.id,
                tenant_id="team-a",
                subject_id="alice",
                native_case_id=ids["native_case"],
                native_run_id=native_run.id,
                frozen_scope={"question": "typed artifact reference"},
                frozen_cutoff={"as_of": "2026-09-04"},
                frozen_source_policy={"allowed_source_types": []},
                role_manifest_version="fundclaw-roles.v1",
                capability_manifest_version="fundclaw-capabilities.v1",
                correlation_id="typed-artifact-reference",
                input_artifact_refs=[reference],
                created_at=now,
            )
            session.add(run_spec)
            session.flush()
            role_run = RoleRun(
                id=uuid.uuid4(),
                conversation_id=ids["research_conversations"],
                run_spec_id=run_spec.id,
                tenant_id="team-a",
                subject_id="alice",
                role_key="scope_identity",
                status="queued",
                attempt=0,
                next_event_sequence=2,
                created_at=now,
                updated_at=now,
            )
            session.add(role_run)
            session.flush()
            session.add(
                RoleEvent(
                    id=uuid.uuid4(),
                    conversation_id=ids["research_conversations"],
                    run_spec_id=run_spec.id,
                    role_run_id=role_run.id,
                    tenant_id="team-a",
                    subject_id="alice",
                    sequence=2,
                    run_sequence=1,
                    role_key="scope_identity",
                    event_type="evidence_available",
                    status="running",
                    display_text="Approved evidence available",
                    artifact_refs=[reference],
                    source_key="sha256:" + "f" * 64,
                    created_at=now,
                )
            )
            session.flush()

    engine.dispose()


def test_0073_sqlite_artifact_reference_guards_reject_every_unsafe_json_shape(
    tmp_path,
) -> None:
    """Both JSON columns reject malformed, duplicate, and payload-bearing JSON."""
    database_url = f"sqlite:///{tmp_path / 'gateway-artifact-reference-shapes.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)
    reference_id = str(uuid.uuid4())
    valid_reference = {"kind": "evidence_link", "id": reference_id}
    invalid_documents = (
        "null",
        '"scalar"',
        json.dumps(valid_reference),
        "[null]",
        "[42]",
        json.dumps([{**valid_reference, "raw_tool_arguments": "SUPERSECRET"}]),
        (
            '[{"kind":"evidence_link","id":"'
            + reference_id
            + '","id":"Authorization: Bearer SUPERSECRET"}]'
        ),
        json.dumps([{**valid_reference, "kind": "raw_tool_arguments"}]),
        json.dumps([{**valid_reference, "id": reference_id.upper()}]),
        json.dumps([{**valid_reference, "case_id": "Authorization: Bearer SUPERSECRET"}]),
        json.dumps([{**valid_reference, "locator_available": "true"}]),
        json.dumps([{**valid_reference, "locator_available": 0}]),
        json.dumps([valid_reference] * 33),
        json.dumps([{"kind": "evidence_link", "id": "a" * 36}]),
        "[" + (" " * 4097) + "]",
    )

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        with Session(bind=connection, future=True, expire_on_commit=False) as session:
            native_runs = [
                ResearchRun(
                    id=uuid.uuid4(),
                    research_case_id=ids["native_case"],
                    created_at=now,
                    updated_at=now,
                )
                for _ in invalid_documents
            ]
            session.add_all(native_runs)
            session.commit()

        for index, (document, native_run) in enumerate(
            zip(invalid_documents, native_runs, strict=True), start=2
        ):
            with pytest.raises(
                sa.exc.IntegrityError, match="Gateway artifact"
            ), connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO role_events "
                        "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                        "subject_id, sequence, run_sequence, role_key, event_type, "
                        "status, reason_code, display_text, artifact_refs_json, source_key, "
                        "created_at) "
                        "SELECT :id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                        "subject_id, :sequence, :run_sequence, role_key, event_type, status, "
                        "reason_code, display_text, :document, :source_key, created_at "
                        "FROM role_events WHERE id = :event_id"
                    ),
                    {
                        "id": uuid.uuid4().hex,
                        "event_id": ids["role_events"].hex,
                        "sequence": index,
                        "run_sequence": index,
                        "document": document,
                        "source_key": "sha256:" + f"{index:064x}",
                    },
                )
            with pytest.raises(
                sa.exc.IntegrityError, match="Gateway artifact"
            ), connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO research_run_specs "
                        "(id, conversation_id, intent_id, gateway_request_id, tenant_id, "
                        "subject_id, native_case_id, native_run_id, frozen_scope_json, "
                        "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                        "capability_manifest_version, correlation_id, "
                        "input_artifact_refs_json, created_at) "
                        "SELECT :id, conversation_id, intent_id, NULL, tenant_id, subject_id, "
                        "native_case_id, :native_run_id, frozen_scope_json, frozen_cutoff_json, "
                        "frozen_source_policy_json, role_manifest_version, "
                        "capability_manifest_version, :correlation_id, :document, created_at "
                        "FROM research_run_specs WHERE id = :run_spec_id"
                    ),
                    {
                        "id": uuid.uuid4().hex,
                        "run_spec_id": ids["research_run_specs"].hex,
                        "native_run_id": native_run.id.hex,
                        "correlation_id": f"unsafe-artifact-{index}",
                        "document": document,
                    },
                )

        assert connection.scalar(sa.select(sa.func.count(RoleEvent.id))) == 1
        assert connection.scalar(sa.select(sa.func.count(ResearchRunSpec.id))) == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


@pytest.mark.parametrize(
    "bad_digest",
    (
        "Authorization: Bearer SUPERSECRET".ljust(64, "x"),
        "A" * 64,
        "g" * 64,
        b"a" * 64,
        b"a" * 63 + b"\x00",
    ),
)
def test_0073_sqlite_sha256_columns_reject_noncanonical_raw_values(
    tmp_path, bad_digest: str | bytes
) -> None:
    """Raw 0073 inserts cannot turn a digest slot into payload or BLOB storage."""
    database_url = f"sqlite:///{tmp_path / 'gateway-canonical-digest.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        with pytest.raises(
            sa.exc.IntegrityError, match="request_fingerprint"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_idempotency_requests "
                    "(id, tenant_id, subject_id, operation, client_key, "
                    "request_fingerprint, status, attempt, created_at, updated_at) "
                    "VALUES (:id, 'team-a', 'alice', 'send_message', "
                    "'raw-bad-fingerprint', :digest, 'in_progress', 1, :now, :now)"
                ),
                {"id": uuid.uuid4().hex, "digest": bad_digest, "now": now},
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="input_sha256"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_messages "
                    "(id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, content, input_sha256, created_at) "
                    "SELECT :id, conversation_id, tenant_id, subject_id, 2, "
                    "message_kind, role_key, content, :digest, created_at "
                    "FROM research_messages WHERE id = :message_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "message_id": ids["research_messages"].hex,
                    "digest": bad_digest,
                },
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="input_sha256"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_intents "
                    "(id, conversation_id, message_id, parent_intent_id, "
                    "gateway_request_id, tenant_id, subject_id, intent_kind, "
                    "status, input_sha256, reason_code, created_at) "
                    "SELECT :id, conversation_id, message_id, NULL, NULL, "
                    "tenant_id, subject_id, intent_kind, status, :digest, "
                    "reason_code, created_at FROM research_intents WHERE id = :intent_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "intent_id": ids["research_intents"].hex,
                    "digest": bad_digest,
                },
            )
        command_request_id = uuid.uuid4()
        with Session(bind=connection, future=True) as session:
            session.add(
                GatewayIdempotencyRequest(
                    id=command_request_id,
                    tenant_id="team-a",
                    subject_id="alice",
                    operation="issue_command",
                    client_key="raw-bad-command-target",
                    request_fingerprint="a" * 64,
                    status="in_progress",
                    attempt=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        with pytest.raises(
            sa.exc.IntegrityError, match="target_hash"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_commands "
                    "(id, conversation_id, run_spec_id, gateway_request_id, "
                    "tenant_id, subject_id, actor_subject_id, command_kind, "
                    "target_hash, target_version, outcome, reason_code, created_at) "
                    "SELECT :id, conversation_id, run_spec_id, :request_id, "
                    "tenant_id, subject_id, actor_subject_id, command_kind, "
                    ":digest, target_version, outcome, reason_code, created_at "
                    "FROM gateway_commands WHERE id = :command_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "request_id": command_request_id.hex,
                    "command_id": ids["gateway_commands"].hex,
                    "digest": bad_digest,
                },
            )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0074_sqlite_nul_text_guards_reject_raw_secret_suffixes(tmp_path) -> None:
    """Head rejects values SQLite 0073 checks treated as NUL-terminated text."""
    database_url = f"sqlite:///{tmp_path / 'gateway-nul-text-guards.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, future=True)
    nul_digest = "a" * 64 + "\x00Authorization: Bearer SUPERSECRET"
    nul_source_key = "sha256:" + "c" * 64 + "\x00Authorization: Bearer SUPERSECRET"
    nul_artifact_refs = (
        json.dumps(
            [{"kind": "evidence_link", "id": str(uuid.uuid4())}],
        )
        + "\x00Authorization: Bearer SUPERSECRET"
    )

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        now = datetime.now(UTC)
        with pytest.raises(
            sa.exc.IntegrityError,
            match="ck_gateway_idempotency_requests_request_fingerprint",
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_idempotency_requests "
                    "(id, tenant_id, subject_id, operation, client_key, "
                    "request_fingerprint, status, attempt, created_at, updated_at) "
                    "VALUES (:id, 'team-a', 'alice', 'send_message', "
                    "'nul-fingerprint', :digest, 'in_progress', 1, :now, :now)"
                ),
                {"id": uuid.uuid4().hex, "digest": nul_digest, "now": now},
            )
        with pytest.raises(
            sa.exc.IntegrityError,
            match="ck_gateway_idempotency_requests_request_fingerprint",
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "UPDATE gateway_idempotency_requests SET request_fingerprint = :digest "
                    "WHERE id = :request_id"
                ),
                {
                    "digest": nul_digest,
                    "request_id": ids["gateway_idempotency_requests"].hex,
                },
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_research_messages_input_sha256"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_messages "
                    "(id, conversation_id, tenant_id, subject_id, sequence, message_kind, "
                    "role_key, content, input_sha256, created_at) "
                    "SELECT :id, conversation_id, tenant_id, subject_id, 2, message_kind, "
                    "role_key, content, :digest, created_at FROM research_messages "
                    "WHERE id = :message_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "message_id": ids["research_messages"].hex,
                    "digest": nul_digest,
                },
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_research_intents_input_sha256"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_intents "
                    "(id, conversation_id, message_id, parent_intent_id, gateway_request_id, "
                    "tenant_id, subject_id, intent_kind, status, input_sha256, reason_code, "
                    "created_at) SELECT :id, conversation_id, message_id, NULL, NULL, "
                    "tenant_id, subject_id, intent_kind, status, :digest, reason_code, created_at "
                    "FROM research_intents WHERE id = :intent_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "intent_id": ids["research_intents"].hex,
                    "digest": nul_digest,
                },
            )
        command_request_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO gateway_idempotency_requests "
                "(id, tenant_id, subject_id, operation, client_key, request_fingerprint, "
                "status, attempt, created_at, updated_at) VALUES "
                "(:id, 'team-a', 'alice', 'issue_command', 'nul-command', :fingerprint, "
                "'in_progress', 1, :now, :now)"
            ),
            {
                "id": command_request_id.hex,
                "fingerprint": "d" * 64,
                "now": now,
            },
        )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_gateway_commands_target_hash"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_commands "
                    "(id, conversation_id, run_spec_id, gateway_request_id, tenant_id, "
                    "subject_id, actor_subject_id, command_kind, target_hash, target_version, "
                    "outcome, reason_code, created_at) SELECT :id, conversation_id, "
                    "run_spec_id, :request_id, tenant_id, subject_id, actor_subject_id, "
                    "command_kind, :digest, target_version, outcome, reason_code, created_at "
                    "FROM gateway_commands WHERE id = :command_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "request_id": command_request_id.hex,
                    "command_id": ids["gateway_commands"].hex,
                    "digest": nul_digest,
                },
            )
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_role_events_source_key_digest"
        ), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO role_events "
                    "(id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                    "sequence, run_sequence, role_key, event_type, status, reason_code, "
                    "display_text, artifact_refs_json, source_key, created_at) SELECT :id, "
                    "conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, 2, 2, "
                    "role_key, event_type, status, reason_code, 'Role started', "
                    "artifact_refs_json, :source_key, created_at FROM role_events "
                    "WHERE id = :event_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "event_id": ids["role_events"].hex,
                    "source_key": nul_source_key,
                },
            )
        with pytest.raises(sa.exc.IntegrityError, match="Gateway artifact"), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO role_events "
                    "(id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                    "sequence, run_sequence, role_key, event_type, status, reason_code, "
                    "display_text, artifact_refs_json, source_key, created_at) SELECT :id, "
                    "conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, 3, 3, "
                    "role_key, event_type, status, reason_code, 'Role started', :artifact_refs, "
                    ":source_key, created_at FROM role_events WHERE id = :event_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "event_id": ids["role_events"].hex,
                    "artifact_refs": nul_artifact_refs,
                    "source_key": "sha256:" + "e" * 64,
                },
            )
        native_run = ResearchRun(
            id=uuid.uuid4(),
            research_case_id=ids["native_case"],
            created_at=now,
            updated_at=now,
        )
        native_run_id = native_run.id
        with Session(bind=connection, future=True) as session:
            session.add(native_run)
            session.commit()
        with pytest.raises(sa.exc.IntegrityError, match="Gateway artifact"), connection.begin_nested():
            connection.execute(
                sa.text(
                    "INSERT INTO research_run_specs "
                    "(id, conversation_id, intent_id, gateway_request_id, tenant_id, "
                    "subject_id, native_case_id, native_run_id, frozen_scope_json, "
                    "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                    "capability_manifest_version, correlation_id, input_artifact_refs_json, "
                    "created_at) SELECT :id, conversation_id, intent_id, NULL, tenant_id, "
                    "subject_id, native_case_id, :native_run_id, frozen_scope_json, "
                    "frozen_cutoff_json, frozen_source_policy_json, role_manifest_version, "
                    "capability_manifest_version, :correlation_id, :artifact_refs, created_at "
                    "FROM research_run_specs WHERE id = :run_spec_id"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "run_spec_id": ids["research_run_specs"].hex,
                    "native_run_id": native_run_id.hex,
                    "correlation_id": "nul-artifact-references",
                    "artifact_refs": nul_artifact_refs,
                },
            )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0074_sqlite_installs_exact_successor_trigger_contract(tmp_path) -> None:
    """The successor is reversible to exactly 0073 and reauthenticates at head."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-trigger-contract.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)
    from app.db_migrations import (
        _SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS,
        _SQLITE_GATEWAY_TRIGGER_CONTRACTS,
        _require_0073_gateway_immutable_triggers,
        _require_gateway_immutable_triggers,
    )

    with engine.connect() as connection:
        assert _gateway_trigger_names(connection) == set(
            _SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS
        )
    _require_0073_gateway_immutable_triggers(engine)

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0076"
        assert _gateway_trigger_names(connection) == set(_SQLITE_GATEWAY_TRIGGER_CONTRACTS)
    _require_gateway_immutable_triggers(engine)

    command.downgrade(config, "0073")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        assert _gateway_trigger_names(connection) == set(
            _SQLITE_0073_GATEWAY_TRIGGER_CONTRACTS
        )
    _require_0073_gateway_immutable_triggers(engine)

    command.upgrade(config, "head")
    _require_gateway_immutable_triggers(engine)
    engine.dispose()


def test_0074_sqlite_downgrade_rolls_back_partial_guard_drop(tmp_path, monkeypatch) -> None:
    """A failed downgrade leaves the 0074 contract and version fully intact."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-downgrade-rollback.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0074")
    engine = sa.create_engine(database_url, future=True)
    from app.db_migrations import _SQLITE_GATEWAY_TRIGGER_CONTRACTS

    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("0074").module

    def fail_after_first_guard_drop() -> None:
        migration.op.execute(
            "DROP TRIGGER validate_insert_gateway_commands_target_hash_nul"
        )
        raise RuntimeError("simulated 0074 downgrade failure")

    monkeypatch.setattr(migration, "_drop_sqlite_nul_guards", fail_after_first_guard_drop)

    class _PatchedScriptDirectory:
        @staticmethod
        def from_config(_config: Config) -> ScriptDirectory:
            return script

    monkeypatch.setattr(command, "ScriptDirectory", _PatchedScriptDirectory)
    with pytest.raises(RuntimeError, match="simulated 0074 downgrade failure"):
        command.downgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0074"
        assert _gateway_trigger_names(connection) == set(_SQLITE_GATEWAY_TRIGGER_CONTRACTS)
    engine.dispose()


def test_0074_direct_sqlite_downgrade_refuses_tampered_head_contract_before_ddl(
    tmp_path,
) -> None:
    """A direct downgrade cannot stamp 0073 after a head guard was removed."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-tampered-downgrade.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0074")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.execute(
            sa.text("DROP TRIGGER validate_insert_gateway_commands_target_hash_nul")
        )

    with pytest.raises(RuntimeError, match="Gateway 0074 trigger contract"):
        command.downgrade(config, "0073")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0074"
        trigger_names = _gateway_trigger_names(connection)
        assert "validate_insert_gateway_commands_target_hash_nul" not in trigger_names
        assert "validate_insert_gateway_idempotency_receipt_cache" in trigger_names
    engine.dispose()


def test_0074_direct_sqlite_upgrade_refuses_tampered_0073_contract_before_ddl(
    tmp_path,
) -> None:
    """Direct Alembic callers cannot skip the predecessor trigger boundary."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-tampered-0073.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER no_update_role_events"))

    with pytest.raises(RuntimeError, match="Gateway 0073 trigger contract"):
        command.upgrade(config, "0074")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        trigger_names = _gateway_trigger_names(connection)
        assert "validate_insert_role_events_source_key_nul" not in trigger_names
        assert "validate_update_gateway_idempotency_receipt_cache" not in trigger_names
        assert connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")
        ).all() == []
    engine.dispose()


def test_0074_sqlite_refuses_nul_immutable_history_before_cache_cleanup_or_ddl(
    tmp_path,
) -> None:
    """An unsafe 0073 immutable record never becomes grandfathered at 0074."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-unsafe-history.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        connection.execute(
            sa.text(
                "INSERT INTO role_events "
                "(id, conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, "
                "sequence, run_sequence, role_key, event_type, status, reason_code, "
                "display_text, artifact_refs_json, source_key, created_at) SELECT :id, "
                "conversation_id, run_spec_id, role_run_id, tenant_id, subject_id, 2, 2, "
                "role_key, event_type, status, reason_code, 'Role started', "
                "artifact_refs_json, :source_key, created_at FROM role_events "
                "WHERE id = :event_id"
            ),
            {
                "id": uuid.uuid4().hex,
                "event_id": ids["role_events"].hex,
                "source_key": "sha256:" + "a" * 64 + "\x00SUPERSECRET",
            },
        )
        connection.execute(
            sa.text(
                "UPDATE gateway_idempotency_requests SET receipt_json = :receipt "
                "WHERE id = :request_id"
            ),
            {
                "request_id": ids["gateway_idempotency_requests"].hex,
                "receipt": '{"raw_provider_error":"SUPERSECRET"}\x00hidden',
            },
        )

    with pytest.raises(
        RuntimeError,
        match="unsafe Gateway history in role_events.source_key",
    ):
        command.upgrade(config, "0074")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        assert connection.scalar(
            sa.text(
                "SELECT receipt_json IS NULL FROM gateway_idempotency_requests "
                "WHERE id = :request_id"
            ),
            {"request_id": ids["gateway_idempotency_requests"].hex},
        ) == 0
        assert "validate_insert_role_events_source_key_nul" not in _gateway_trigger_names(
            connection
        )
        assert connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")
        ).all() == []
    engine.dispose()


def test_0074_sqlite_rolls_back_receipt_cache_cleanup_if_guard_installation_fails(
    tmp_path,
    monkeypatch,
) -> None:
    """Cache clearing is transactional and cannot outlive a failed 0074 DDL step."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-cache-rollback.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)

    with engine.begin() as connection:
        ids = _seed_gateway_rows(connection)
        connection.execute(
            sa.text(
                "UPDATE gateway_idempotency_requests SET receipt_json = :receipt "
                "WHERE id = :request_id"
            ),
            {
                "request_id": ids["gateway_idempotency_requests"].hex,
                "receipt": '{"raw_provider_error":"SUPERSECRET"}\x00hidden',
            },
        )

    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("0074").module

    def fail_after_cache_cleanup() -> None:
        raise RuntimeError("simulated 0074 guard installation failure")

    monkeypatch.setattr(migration, "_install_sqlite_nul_guards", fail_after_cache_cleanup)

    class _PatchedScriptDirectory:
        @staticmethod
        def from_config(_config: Config) -> ScriptDirectory:
            return script

    monkeypatch.setattr(command, "ScriptDirectory", _PatchedScriptDirectory)
    with pytest.raises(RuntimeError, match="simulated 0074 guard installation failure"):
        command.upgrade(config, "0074")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        assert connection.scalar(
            sa.text(
                "SELECT receipt_json IS NULL FROM gateway_idempotency_requests "
                "WHERE id = :request_id"
            ),
            {"request_id": ids["gateway_idempotency_requests"].hex},
        ) == 0
        assert "validate_update_gateway_idempotency_receipt_cache" not in _gateway_trigger_names(
            connection
        )
    engine.dispose()


def test_0074_sqlite_serializes_preflight_before_nul_guards(tmp_path, monkeypatch) -> None:
    """A writer cannot slip an unsafe row between 0074 preflight and its stamp."""
    database_url = f"sqlite:///{tmp_path / 'gateway-0074-preflight-race.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0073")
    engine = sa.create_engine(database_url, future=True)
    writer_engine = sa.create_engine(
        database_url,
        future=True,
        connect_args={"timeout": 5},
    )
    writer_thread: threading.Thread | None = None

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            ids = _seed_gateway_rows(connection)

        script = ScriptDirectory.from_config(config)
        migration = script.get_revision("0074").module
        original_preflight = migration._require_safe_0073_gateway_history
        writer_started = threading.Event()
        writer_finished = threading.Event()
        writer_errors: list[sa.exc.DBAPIError] = []

        def write_nul_event() -> None:
            writer_started.set()
            try:
                with writer_engine.begin() as writer:
                    writer.execute(
                        sa.text(
                            "INSERT INTO role_events "
                            "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                            "subject_id, sequence, run_sequence, role_key, event_type, status, "
                            "reason_code, display_text, artifact_refs_json, source_key, created_at) "
                            "SELECT :id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                            "subject_id, 2, 2, role_key, event_type, status, reason_code, "
                            "'Role started', artifact_refs_json, :source_key, created_at "
                            "FROM role_events WHERE id = :event_id"
                        ),
                        {
                            "id": uuid.uuid4().hex,
                            "event_id": ids["role_events"].hex,
                            "source_key": "sha256:" + "f" * 64 + "\x00SUPERSECRET",
                        },
                    )
            except sa.exc.DBAPIError as error:
                writer_errors.append(error)
            finally:
                writer_finished.set()

        def start_writer_after_preflight(bind: sa.Connection) -> None:
            original_preflight(bind)
            nonlocal writer_thread
            writer_thread = threading.Thread(target=write_nul_event)
            writer_thread.start()
            assert writer_started.wait(timeout=2)
            time.sleep(0.1)
            assert not writer_finished.is_set()

        monkeypatch.setattr(
            migration,
            "_require_safe_0073_gateway_history",
            start_writer_after_preflight,
        )

        class _PatchedScriptDirectory:
            @staticmethod
            def from_config(_config: Config) -> ScriptDirectory:
                return script

        monkeypatch.setattr(command, "ScriptDirectory", _PatchedScriptDirectory)
        command.upgrade(config, "0074")

        assert writer_finished.wait(timeout=5)
        assert len(writer_errors) == 1
        assert isinstance(writer_errors[0], sa.exc.IntegrityError)
        assert "ck_role_events_source_key_digest" in str(writer_errors[0])
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0074"
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM role_events")) == 1
    finally:
        if writer_thread is not None:
            writer_thread.join(timeout=5)
        writer_engine.dispose()
        engine.dispose()


def test_0072_gateway_migration_binds_every_private_child_to_its_scope(
    tmp_path,
) -> None:
    """No Gateway child can claim another private conversation's provenance."""
    database_url = f"sqlite:///{tmp_path / 'gateway-private-scope.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    expected_scope_foreign_keys = {
        "research_messages": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
        },
        "research_intents": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
            (
                ("message_id", "conversation_id", "tenant_id", "subject_id"),
                "research_messages",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
            (
                ("parent_intent_id", "conversation_id", "tenant_id", "subject_id"),
                "research_intents",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
        },
        "research_run_specs": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
            (
                ("intent_id", "conversation_id", "tenant_id", "subject_id"),
                "research_intents",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
        },
        "role_runs": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
            (
                ("run_spec_id", "conversation_id", "tenant_id", "subject_id"),
                "research_run_specs",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
        },
        "role_events": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
            (
                ("run_spec_id", "conversation_id", "tenant_id", "subject_id"),
                "research_run_specs",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
            (
                (
                    "role_run_id",
                    "run_spec_id",
                    "conversation_id",
                    "tenant_id",
                    "subject_id",
                    "role_key",
                ),
                "role_runs",
                (
                    "id",
                    "run_spec_id",
                    "conversation_id",
                    "tenant_id",
                    "subject_id",
                    "role_key",
                ),
            ),
        },
        "gateway_commands": {
            (
                ("conversation_id", "tenant_id", "subject_id"),
                "research_conversations",
                ("id", "tenant_id", "owner_subject_id"),
            ),
            (
                ("run_spec_id", "conversation_id", "tenant_id", "subject_id"),
                "research_run_specs",
                ("id", "conversation_id", "tenant_id", "subject_id"),
            ),
        },
    }

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        inspector = sa.inspect(connection)
        for table_name, expected in expected_scope_foreign_keys.items():
            actual = {
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
            }
            assert expected.issubset(actual)

        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            other_case = ResearchCase(
                id=uuid.uuid4(),
                title="private scope case",
                industry_topic="semiconductor",
                created_by="human:alice",
                created_at=now,
            )
            other_run = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=other_case.id,
                created_at=now,
                updated_at=now,
            )
            session.add_all((other_case, other_run))
            session.flush()

            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        ResearchMessage(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            tenant_id="team-b",
                            subject_id="bob",
                            sequence=2,
                            message_kind="user",
                            content="foreign private message",
                            input_sha256="f" * 64,
                            created_at=now,
                        )
                    )
                    session.flush()

            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        ResearchIntent(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            message_id=ids["research_messages"],
                            tenant_id="team-b",
                            subject_id="bob",
                            intent_kind="start",
                            status="queued",
                            input_sha256="e" * 64,
                            created_at=now,
                        )
                    )
                    session.flush()

            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        ResearchRunSpec(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            intent_id=ids["research_intents"],
                            tenant_id="team-b",
                            subject_id="bob",
                            native_case_id=other_case.id,
                            native_run_id=other_run.id,
                            frozen_scope={"question": "foreign"},
                            frozen_cutoff={"as_of": "2026-09-04"},
                            frozen_source_policy={"allowed_source_types": []},
                            role_manifest_version="fundclaw-roles.v1",
                            capability_manifest_version="fundclaw-capabilities.v1",
                            correlation_id="foreign-private-run",
                            input_artifact_refs=[],
                            created_at=now,
                        )
                    )
                    session.flush()

            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        RoleRun(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            run_spec_id=ids["research_run_specs"],
                            tenant_id="team-b",
                            subject_id="bob",
                            role_key="sources_evidence",
                            status="queued",
                            attempt=0,
                            next_event_sequence=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.flush()

            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        RoleEvent(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            run_spec_id=ids["research_run_specs"],
                            role_run_id=ids["role_runs"],
                            tenant_id="team-b",
                            subject_id="bob",
                            sequence=2,
                            run_sequence=2,
                            role_key="scope_identity",
                            event_type="role_progress",
                            status="running",
                            artifact_refs=[],
                            source_key="foreign-private-event",
                            created_at=now,
                        )
                    )
                    session.flush()

        with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE research_conversations SET owner_subject_id = 'bob' "
                        "WHERE id = :id"
                    ),
                    {"id": ids["research_conversations"].hex},
                )

        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0072_gateway_migration_requires_command_run_spec(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-command-run-spec.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        command_columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("gateway_commands")
        }
        assert command_columns["run_spec_id"]["nullable"] is False

        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            request = GatewayIdempotencyRequest(
                id=uuid.uuid4(),
                tenant_id="team-a",
                subject_id="alice",
                operation="issue_command",
                client_key="missing-run-spec",
                request_fingerprint="d" * 64,
                status="in_progress",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            session.add(request)
            session.flush()
            with pytest.raises(sa.exc.IntegrityError, match="NOT NULL"):
                with session.begin_nested():
                    session.add(
                        GatewayCommand(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            run_spec_id=None,
                            gateway_request_id=request.id,
                            tenant_id="team-a",
                            subject_id="alice",
                            actor_subject_id="alice",
                            command_kind="pause",
                            target_hash="d" * 64,
                            outcome="rejected",
                            created_at=now,
                        )
                    )
                    session.flush()

    engine.dispose()


def test_0072_gateway_migration_binds_native_run_to_its_case(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-native-pair.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        inspector = sa.inspect(connection)
        assert any(
            tuple(foreign_key["constrained_columns"])
            == ("native_case_id", "native_run_id")
            and foreign_key["referred_table"] == "research_runs"
            and tuple(foreign_key["referred_columns"]) == ("research_case_id", "id")
            for foreign_key in inspector.get_foreign_keys("research_run_specs")
        )

        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            other_case = ResearchCase(
                id=uuid.uuid4(),
                title="other migration case",
                industry_topic="semiconductor",
                created_by="human:alice",
                created_at=now,
            )
            run_from_original_case = ResearchRun(
                id=uuid.uuid4(),
                research_case_id=ids["native_case"],
                created_at=now,
                updated_at=now,
            )
            session.add_all((other_case, run_from_original_case))
            session.flush()
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        ResearchRunSpec(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            intent_id=ids["research_intents"],
                            tenant_id="team-a",
                            subject_id="alice",
                            native_case_id=other_case.id,
                            native_run_id=run_from_original_case.id,
                            frozen_scope={"question": "mismatched"},
                            frozen_cutoff={"as_of": "2026-09-04"},
                            frozen_source_policy={"allowed_source_types": []},
                            role_manifest_version="fundclaw-roles.v1",
                            capability_manifest_version="fundclaw-capabilities.v1",
                            correlation_id="native-pair-probe",
                            input_artifact_refs=[],
                            created_at=now,
                        )
                    )
                    session.flush()
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    engine.dispose()


def test_0072_gateway_migration_binds_command_actor_to_private_subject(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-command-actor.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        command_checks = {
            check["name"]: check.get("sqltext", check.get("sql"))
            for check in sa.inspect(connection).get_check_constraints(
                "gateway_commands"
            )
        }
        assert "actor_subject_id = subject_id" in str(
            command_checks["ck_gateway_commands_actor_subject"]
        )
        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            command_request = GatewayIdempotencyRequest(
                id=uuid.uuid4(),
                tenant_id="team-a",
                subject_id="alice",
                operation="issue_command",
                client_key="actor-subject-check",
                request_fingerprint="d" * 64,
                status="in_progress",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            session.add(command_request)
            session.flush()
            with pytest.raises(sa.exc.IntegrityError, match="CHECK"):
                with session.begin_nested():
                    session.add(
                        GatewayCommand(
                            id=uuid.uuid4(),
                            conversation_id=ids["research_conversations"],
                            run_spec_id=ids["research_run_specs"],
                            gateway_request_id=command_request.id,
                            tenant_id="team-a",
                            subject_id="alice",
                            actor_subject_id="mallory",
                            command_kind="pause",
                            target_hash="d" * 64,
                            outcome="rejected",
                            created_at=now,
                        )
                    )
                    session.flush()

    engine.dispose()


def test_0072_gateway_migration_binds_command_to_private_conversation_scope(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-command-conversation.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "0072")
    engine = sa.create_engine(database_url, future=True)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        ids = _seed_gateway_rows(connection)
        assert any(
            tuple(foreign_key["constrained_columns"])
            == ("conversation_id", "tenant_id", "subject_id")
            and foreign_key["referred_table"] == "research_conversations"
            and tuple(foreign_key["referred_columns"])
            == ("id", "tenant_id", "owner_subject_id")
            for foreign_key in sa.inspect(connection).get_foreign_keys(
                "gateway_commands"
            )
        )

        now = datetime.now(UTC)
        with Session(bind=connection, future=True) as session:
            foreign_conversation = ResearchConversation(
                id=uuid.uuid4(),
                tenant_id="team-b",
                owner_subject_id="bob",
                visibility="private",
                event_retention_floor=1,
                next_message_sequence=1,
                next_event_sequence=1,
                created_at=now,
                updated_at=now,
            )
            command_request = GatewayIdempotencyRequest(
                id=uuid.uuid4(),
                tenant_id="team-a",
                subject_id="alice",
                operation="issue_command",
                client_key="foreign-conversation-command",
                request_fingerprint="f" * 64,
                status="in_progress",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            session.add_all((foreign_conversation, command_request))
            session.flush()
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                with session.begin_nested():
                    session.add(
                        GatewayCommand(
                            id=uuid.uuid4(),
                            conversation_id=foreign_conversation.id,
                            run_spec_id=ids["research_run_specs"],
                            gateway_request_id=command_request.id,
                            tenant_id="team-a",
                            subject_id="alice",
                            actor_subject_id="alice",
                            command_kind="pause",
                            target_hash="e" * 64,
                            outcome="rejected",
                            created_at=now,
                        )
                    )
                    session.flush()

    engine.dispose()


def test_0072_upgrade_from_0071_keeps_foreign_keys_valid(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'gateway-0071-upgrade.db'}"
    config = _alembic_config(database_url)

    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    sa.event.listen(sa.engine.Engine, "connect", enable_foreign_keys)
    try:
        command.upgrade(config, "0071")
        command.upgrade(config, "0072")
        engine = sa.create_engine(database_url, future=True)
        try:
            with engine.connect() as connection:
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                )
                assert (
                    connection.scalar(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                    == "0072"
                )
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    == []
                )
                unique_indexes = {
                    index["name"]: index
                    for index in sa.inspect(connection).get_indexes("research_runs")
                }
                assert bool(unique_indexes["uq_research_runs_case_id"]["unique"])
        finally:
            engine.dispose()
        command.downgrade(config, "0071")
        verify_engine = sa.create_engine(database_url, future=True)
        try:
            with verify_engine.connect() as connection:
                assert (
                    connection.scalar(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                    == "0071"
                )
                assert (
                    connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    == []
                )
        finally:
            verify_engine.dispose()
    finally:
        sa.event.remove(sa.engine.Engine, "connect", enable_foreign_keys)


@pytest.mark.pg_only
def test_0073_postgres_migration_installs_append_only_guards_and_downgrades() -> None:
    """Exercise released-0072 authentication and 0073 hardening in PostgreSQL."""
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"gateway_0073_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        alembic_environment = {**os.environ, "DATABASE_URL": migration_url}
        legacy_upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0072"],
            cwd=backend,
            env=alembic_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert legacy_upgraded.returncode == 0, legacy_upgraded.stderr

        # Direct Alembic invocation must authenticate the released 0072
        # contract itself. The process cannot rely on application bootstrap
        # to notice a missing immutable guard before it starts 0073 DDL.
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text("DROP TRIGGER no_update_role_events ON role_events")
            )
        tampered_upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0073"],
            cwd=backend,
            env=alembic_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert tampered_upgrade.returncode != 0
        assert "legacy Gateway 0072 append-only trigger" in (
            tampered_upgrade.stdout + tampered_upgrade.stderr
        )
        with schema_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0072"
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE TRIGGER no_update_role_events BEFORE UPDATE ON "
                    "role_events FOR EACH ROW EXECUTE FUNCTION "
                    "reject_mutable_ledger()"
                )
            )

        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0073"],
            cwd=backend,
            env=alembic_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr

        # The runtime catalog authentication—not merely migration DDL—must
        # accept its own canonical schema before rows are ever projected.
        from app.db_migrations import (
            UnmanagedDatabaseSchemaError,
            upgrade_database_to_head,
        )

        upgrade_database_to_head(migration_url)

        # PostgreSQL closes an implicit transaction from ``connect()`` with a
        # rollback.  The following probes reuse these seeded IDs, so commit
        # their fixture transaction before leaving this scope.
        with schema_engine.begin() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0076"
            ids = _seed_gateway_rows(connection)
            immutable_table_literals = ", ".join(
                f"'{table_name}'" for table_name in IMMUTABLE_GATEWAY_TABLES
            )
            trigger_rows = connection.execute(
                sa.text(
                    "SELECT trigger_name, event_manipulation, event_object_table "
                    "FROM information_schema.triggers "
                    "WHERE trigger_schema = :schema "
                    f"AND event_object_table IN ({immutable_table_literals})"
                ),
                {"schema": schema},
            ).mappings()
            trigger_names = {
                (
                    row["event_object_table"],
                    row["event_manipulation"],
                    row["trigger_name"],
                )
                for row in trigger_rows
            }
            for table_name in IMMUTABLE_GATEWAY_TABLES:
                assert (
                    table_name,
                    "UPDATE",
                    f"no_update_{table_name}",
                ) in trigger_names
                assert (
                    table_name,
                    "DELETE",
                    f"no_delete_{table_name}",
                ) in trigger_names
            assert (
                "research_run_specs",
                "INSERT",
                "validate_insert_research_run_specs_input_artifact_refs",
            ) in trigger_names
            assert (
                "role_events",
                "INSERT",
                "validate_insert_role_events_artifact_refs",
            ) in trigger_names
            owner_pairs = connection.execute(
                sa.text(
                    "SELECT trigger_row.tgname, relation.relowner AS table_owner, "
                    "function_row.proowner AS function_owner "
                    "FROM pg_trigger AS trigger_row "
                    "JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid "
                    "WHERE NOT trigger_row.tgisinternal "
                    "AND namespace.nspname = :schema "
                    "AND relation.relname IN ("
                    "'research_messages', 'research_intents', 'research_run_specs', "
                    "'role_events', 'gateway_commands')"
                ),
                {"schema": schema},
            ).mappings()
            assert owner_pairs
            assert all(
                row["table_owner"] == row["function_owner"]
                for row in owner_pairs
            )

        # The portable DDL must enforce the newly closed immutable payload
        # fields on the PostgreSQL runtime too, not only on SQLite metadata.
        now = datetime.now(UTC)
        with schema_engine.begin() as connection:
            check_names = set(
                connection.scalars(
                    sa.text(
                        "SELECT constraint_row.conname "
                        "FROM pg_constraint AS constraint_row "
                        "JOIN pg_class AS relation "
                        "ON relation.oid = constraint_row.conrelid "
                        "JOIN pg_namespace AS namespace "
                        "ON namespace.oid = relation.relnamespace "
                        "WHERE namespace.nspname = :schema "
                        "AND relation.relname IN ('research_intents', "
                        "'gateway_commands')"
                    ),
                    {"schema": schema},
                )
            )
            assert {
                "ck_research_intents_reason_code_safe",
                "ck_gateway_commands_reason_code_safe",
                "ck_gateway_commands_target_version_absent",
            }.issubset(check_names)
            connection.execute(
                sa.text(
                    "INSERT INTO research_messages "
                    "(id, conversation_id, tenant_id, subject_id, sequence, "
                    "message_kind, role_key, content, input_sha256, created_at) "
                    "VALUES (:id, :conversation_id, 'team-a', 'alice', 2, "
                    "'user', NULL, 'postgres safe field probe', :input_sha256, "
                    ":created_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "conversation_id": ids["research_conversations"],
                    "input_sha256": "c" * 64,
                    "created_at": now,
                },
            )

        unsafe_reason = "Authorization: Bearer SUPERSECRET raw provider traceback"
        with pytest.raises(
            sa.exc.DBAPIError, match="ck_research_intents_reason_code_safe"
        ), schema_engine.begin() as connection:
            message_id = connection.scalar(
                sa.text(
                    "SELECT id FROM research_messages "
                    "WHERE conversation_id = :conversation_id AND sequence = 2"
                ),
                {"conversation_id": ids["research_conversations"]},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_intents "
                    "(id, conversation_id, message_id, parent_intent_id, "
                    "gateway_request_id, tenant_id, subject_id, intent_kind, "
                    "status, input_sha256, reason_code, created_at) "
                    "VALUES (:id, :conversation_id, :message_id, NULL, NULL, "
                    "'team-a', 'alice', 'unsupported', 'rejected', "
                    ":input_sha256, :reason_code, :created_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "conversation_id": ids["research_conversations"],
                    "message_id": message_id,
                    "input_sha256": "c" * 64,
                    "reason_code": unsafe_reason,
                    "created_at": now,
                },
            )

        def insert_raw_command_request(
            connection: sa.Connection, client_key: str
        ) -> uuid.UUID:
            request_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO gateway_idempotency_requests "
                    "(id, tenant_id, subject_id, operation, client_key, "
                    "request_fingerprint, status, attempt, lease_expires_at, "
                    "receipt_json, created_at, updated_at) "
                    "VALUES (:id, 'team-a', 'alice', 'issue_command', "
                    ":client_key, :request_fingerprint, 'in_progress', 1, NULL, "
                    "NULL, :created_at, :updated_at)"
                ),
                {
                    "id": request_id,
                    "client_key": client_key,
                    "request_fingerprint": "d" * 64,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return request_id

        command_sql = sa.text(
            "INSERT INTO gateway_commands "
            "(id, conversation_id, run_spec_id, gateway_request_id, tenant_id, "
            "subject_id, actor_subject_id, command_kind, target_hash, "
            "target_version, outcome, reason_code, created_at) "
            "VALUES (:id, :conversation_id, :run_spec_id, :gateway_request_id, "
            "'team-a', 'alice', 'alice', 'pause', :target_hash, "
            ":target_version, 'rejected', :reason_code, :created_at)"
        )
        with pytest.raises(
            sa.exc.DBAPIError, match="ck_gateway_commands_reason_code_safe"
        ), schema_engine.begin() as connection:
            request_id = insert_raw_command_request(
                connection, "postgres-raw-command-reason"
            )
            connection.execute(
                command_sql,
                {
                    "id": uuid.uuid4(),
                    "conversation_id": ids["research_conversations"],
                    "run_spec_id": ids["research_run_specs"],
                    "gateway_request_id": request_id,
                    "target_hash": "e" * 64,
                    "target_version": None,
                    "reason_code": unsafe_reason,
                    "created_at": now,
                },
            )
        with pytest.raises(
            sa.exc.DBAPIError, match="ck_gateway_commands_target_version_absent"
        ), schema_engine.begin() as connection:
            request_id = insert_raw_command_request(
                connection, "postgres-raw-command-version"
            )
            connection.execute(
                command_sql,
                {
                    "id": uuid.uuid4(),
                    "conversation_id": ids["research_conversations"],
                    "run_spec_id": ids["research_run_specs"],
                    "gateway_request_id": request_id,
                    "target_hash": "e" * 64,
                    "target_version": unsafe_reason,
                    "reason_code": "unsupported_in_gateway_p0",
                    "created_at": now,
                },
            )

        for table_name in IMMUTABLE_GATEWAY_TABLES:
            row_id = ids[table_name]
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            f"UPDATE {table_name} SET created_at = created_at "
                            "WHERE id = :id"
                        ),
                        {"id": row_id},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f"DELETE FROM {table_name} WHERE id = :id"),
                        {"id": row_id},
                    )

        def assert_gateway_auth_rejects_tamper() -> None:
            with pytest.raises(
                UnmanagedDatabaseSchemaError,
                match="canonical Gateway append-only trigger",
            ):
                upgrade_database_to_head(migration_url)

        # Catalog authentication must reject a missing expected guard, an
        # unexpected trigger, and a same-name shared function whose body has
        # been replaced with a no-op.  Each repair is explicit so this test
        # also proves the original canonical schema remains bootable.
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text("DROP TRIGGER no_delete_research_messages ON research_messages")
            )
        assert_gateway_auth_rejects_tamper()
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE TRIGGER no_delete_research_messages BEFORE DELETE "
                    "ON research_messages FOR EACH ROW EXECUTE FUNCTION "
                    "reject_mutable_ledger()"
                )
            )
        upgrade_database_to_head(migration_url)

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE FUNCTION unexpected_gateway_trigger() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
                )
            )
            connection.execute(
                sa.text(
                    "CREATE TRIGGER unexpected_gateway_trigger BEFORE INSERT ON "
                    "research_messages FOR EACH ROW EXECUTE FUNCTION "
                    "unexpected_gateway_trigger()"
                )
            )
        assert_gateway_auth_rejects_tamper()
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DROP TRIGGER unexpected_gateway_trigger ON research_messages"
                )
            )
            connection.execute(sa.text("DROP FUNCTION unexpected_gateway_trigger()"))
        upgrade_database_to_head(migration_url)

        with schema_engine.begin() as connection:
            original_reject_function = connection.scalar(
                sa.text(
                    "SELECT pg_get_functiondef("
                    "'reject_mutable_ledger()'::regprocedure)"
                )
            )
            assert isinstance(original_reject_function, str)
            connection.execute(
                sa.text(
                    "CREATE OR REPLACE FUNCTION reject_mutable_ledger() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RETURN NEW; END; $$"
                )
            )
        assert_gateway_auth_rejects_tamper()
        with schema_engine.begin() as connection:
            connection.execute(sa.text(original_reject_function))
        upgrade_database_to_head(migration_url)

        # A same-name/function guard is still unsafe when its trigger syntax
        # contains a conditional predicate or an ``UPDATE OF`` attribute list.
        # Runtime authentication must reject both before a no-op head upgrade
        # can repair or otherwise touch managed schema state.
        canonical_update_guard = (
            "CREATE TRIGGER no_update_role_events BEFORE UPDATE ON role_events "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
        )
        for trigger_name, weakened_guard, canonical_guard in (
            (
                "no_update_role_events",
                (
                    "CREATE TRIGGER no_update_role_events BEFORE UPDATE ON "
                    "role_events FOR EACH ROW WHEN (false) EXECUTE FUNCTION "
                    "reject_mutable_ledger()"
                ),
                canonical_update_guard,
            ),
            (
                "validate_insert_role_events_artifact_refs",
                (
                    "CREATE TRIGGER validate_insert_role_events_artifact_refs "
                    "BEFORE INSERT ON role_events FOR EACH ROW WHEN (false) "
                    "EXECUTE FUNCTION validate_gateway_artifact_references()"
                ),
                (
                    "CREATE TRIGGER validate_insert_role_events_artifact_refs "
                    "BEFORE INSERT ON role_events FOR EACH ROW EXECUTE FUNCTION "
                    "validate_gateway_artifact_references()"
                ),
            ),
            (
                "no_update_role_events",
                (
                    "CREATE TRIGGER no_update_role_events BEFORE UPDATE OF "
                    "created_at ON role_events FOR EACH ROW EXECUTE FUNCTION "
                    "reject_mutable_ledger()"
                ),
                canonical_update_guard,
            ),
        ):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text(f"DROP TRIGGER {trigger_name} ON role_events")
                )
                connection.execute(sa.text(weakened_guard))
            assert_gateway_auth_rejects_tamper()
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text(f"DROP TRIGGER {trigger_name} ON role_events")
                )
                connection.execute(sa.text(canonical_guard))
            upgrade_database_to_head(migration_url)

        downgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0071"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert downgraded.returncode == 0, downgraded.stderr
        with schema_engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "0071"
            )
            assert (
                connection.scalar(
                    sa.text("SELECT to_regclass('research_conversations')")
                )
                is None
            )
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0074_postgres_direct_upgrade_authenticates_0073_before_stamping() -> None:
    """0074 itself authenticates a 0073 schema before it can advance its marker."""
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"gateway_0074_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    config = _alembic_config(migration_url)

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, "0073")
        with schema_engine.begin() as connection:
            _seed_gateway_rows(connection)
            connection.execute(
                sa.text("DROP TRIGGER no_update_role_events ON role_events")
            )

        with pytest.raises(RuntimeError, match="Gateway 0073 trigger contract"):
            command.upgrade(config, "0074")

        with schema_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE TRIGGER no_update_role_events BEFORE UPDATE ON role_events "
                    "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
                )
            )
        command.upgrade(config, "0074")

        from app.db_migrations import upgrade_database_to_head

        upgrade_database_to_head(migration_url)
        with schema_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0076"

        command.downgrade(config, "0073")
        with schema_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
        command.upgrade(config, "0074")
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0073_postgres_serializes_legacy_preflight_before_hardening(
    monkeypatch,
) -> None:
    """A concurrent legacy writer waits for 0073 and then meets its guards."""
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"gateway_0073_preflight_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    writer_engine = sa.create_engine(
        migration_url, future=True, pool_size=1, max_overflow=0
    )
    writer_thread: threading.Thread | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        config = _alembic_config(migration_url)
        command.upgrade(config, "0072")
        with schema_engine.begin() as connection:
            ids = _seed_gateway_rows(connection)

        script = ScriptDirectory.from_config(config)
        migration = script.get_revision("0073").module
        original_preflight = migration._require_safe_legacy_gateway_history
        with writer_engine.connect() as writer:
            writer_pid = int(writer.scalar(sa.text("SELECT pg_backend_pid()")))
            writer.rollback()
        writer_worker_ready = threading.Event()
        writer_go = threading.Event()
        writer_insert_started = threading.Event()
        writer_finished = threading.Event()
        writer_errors: list[sa.exc.DBAPIError] = []

        def write_unsafe_legacy_event() -> None:
            writer_worker_ready.set()
            if not writer_go.wait(timeout=10):
                writer_finished.set()
                return
            try:
                with writer_engine.begin() as writer:
                    assert int(writer.scalar(sa.text("SELECT pg_backend_pid()"))) == writer_pid
                    writer.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
                    writer_insert_started.set()
                    writer.execute(
                        sa.text(
                            "INSERT INTO role_events "
                            "(id, conversation_id, run_spec_id, role_run_id, tenant_id, "
                            "subject_id, sequence, run_sequence, role_key, event_type, "
                            "status, reason_code, display_text, artifact_refs_json, source_key, "
                            "created_at) "
                            "SELECT CAST(:id AS uuid), conversation_id, run_spec_id, "
                            "role_run_id, tenant_id, subject_id, 2, 2, role_key, "
                            "'role_progress', 'running', NULL, 'Role progress recorded', "
                            "CAST(:artifact_refs_json AS json), :source_key, created_at "
                            "FROM role_events WHERE id = CAST(:event_id AS uuid)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "event_id": str(ids["role_events"]),
                            "artifact_refs_json": (
                                '[{"kind":"evidence_link","id":"'
                                + str(uuid.uuid4())
                                + '","raw_tool_arguments":"Authorization: Bearer SUPERSECRET"}]'
                            ),
                            "source_key": "sha256:" + "e" * 64,
                        },
                    )
            except sa.exc.DBAPIError as error:  # preserve DB guard rejection
                writer_errors.append(error)
            finally:
                writer_finished.set()

        writer_thread = threading.Thread(target=write_unsafe_legacy_event)
        writer_thread.start()
        assert writer_worker_ready.wait(timeout=2)

        def inject_writer_after_preflight(bind: sa.Connection) -> None:
            original_preflight(bind)
            writer_go.set()
            assert writer_insert_started.wait(timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                wait_event_type = bind.scalar(
                    sa.text(
                        "SELECT wait_event_type FROM pg_stat_activity "
                        "WHERE pid = :pid"
                    ),
                    {"pid": writer_pid},
                )
                if wait_event_type == "Lock":
                    break
                time.sleep(0.02)
            assert wait_event_type == "Lock"
            assert not writer_finished.is_set()

        monkeypatch.setattr(
            migration, "_require_safe_legacy_gateway_history", inject_writer_after_preflight
        )

        class _PatchedScriptDirectory:
            @staticmethod
            def from_config(_config: Config) -> ScriptDirectory:
                return script

        monkeypatch.setattr(command, "ScriptDirectory", _PatchedScriptDirectory)
        command.upgrade(config, "0073")

        assert writer_finished.wait(timeout=5)
        assert len(writer_errors) == 1
        assert isinstance(writer_errors[0], sa.exc.DBAPIError)
        assert "Gateway artifact" in str(writer_errors[0])
        with schema_engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0073"
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM role_events")) == 1
    finally:
        if writer_thread is not None:
            writer_thread.join(timeout=5)
        writer_engine.dispose()
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
