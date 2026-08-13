"""Schema contract for durable governed-acquisition records."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
import sqlalchemy as sa

import app.models  # noqa: F401 - register every model with Base.metadata
from app.models.ledger import (
    Base,
    IMMUTABLE_TABLES,
    ImmutableLedgerError,
    ReviewState,
)
from app.schemas.v1.overview import KeyChangeDTO


ACQUISITION_TABLES = {
    "acquisition_jobs",
    "acquisition_job_events",
    "acquisition_attempts",
    "source_references",
    "retrieval_artifacts",
    "retrieval_artifact_documents",
    "automatic_admission_decisions",
    "acquisition_exceptions",
}
APPEND_ONLY_TABLES = ACQUISITION_TABLES - {"acquisition_jobs"}

EXPECTED_COLUMNS = {
    "acquisition_jobs": {
        "id", "tenant_id", "research_case_id", "thesis_id", "research_run_id",
        "idempotency_key", "request_snapshot", "policy_snapshot", "status", "stage",
        "attempt", "reference_count", "fetched_count", "frozen_count",
        "admitted_count", "exception_count", "lease_owner", "lease_token",
        "lease_expires_at", "retry_at", "error_code", "error_detail", "created_at",
        "started_at", "finished_at", "updated_at",
    },
    "acquisition_job_events": {
        "id", "job_id", "seq", "status", "stage", "message", "payload_json",
        "created_at",
    },
    "acquisition_attempts": {
        "id", "job_id", "adapter_key", "operation", "attempt_no", "started_at",
        "finished_at", "outcome", "error_code", "retryable", "safe_metadata",
    },
    "source_references": {
        "id", "job_id", "adapter_key", "external_record_id", "external_version",
        "canonical_url", "title", "published_at", "source_role", "metadata_json",
        "created_at",
    },
    "retrieval_artifacts": {
        "id", "source_reference_id", "attempt_id", "content_sha256", "raw_bytes",
        "mime_type", "byte_size", "final_url", "etag", "last_modified",
        "provider_request_id", "retrieved_at",
    },
    "retrieval_artifact_documents": {
        "id", "retrieval_artifact_id", "document_version_id", "relation",
        "publication_key", "created_at",
    },
    "automatic_admission_decisions": {
        "id", "job_id", "candidate_id", "retrieval_artifact_id", "outcome",
        "gate_version", "policy_version", "gate_results", "created_at",
    },
    "acquisition_exceptions": {
        "id", "job_id", "source_reference_id", "retrieval_artifact_id",
        "candidate_id", "reason_code", "detail_json", "created_at",
    },
}

EXPECTED_UNIQUES = {
    "acquisition_jobs": {
        "uq_acquisition_jobs_tenant_key": ("tenant_id", "idempotency_key")
    },
    "acquisition_job_events": {
        "uq_acquisition_job_events_job_seq": ("job_id", "seq")
    },
    "acquisition_attempts": {
        "uq_acquisition_attempts_operation_attempt": (
            "job_id", "adapter_key", "operation", "attempt_no",
        )
    },
    "source_references": {
        "uq_source_references_provider_identity": (
            "job_id", "adapter_key", "external_record_id", "external_version",
        )
    },
    "retrieval_artifacts": {
        "uq_retrieval_artifacts_reference_attempt": (
            "source_reference_id", "attempt_id",
        )
    },
    "retrieval_artifact_documents": {
        "uq_retrieval_artifact_documents_artifact": ("retrieval_artifact_id",)
    },
    "automatic_admission_decisions": {
        "uq_automatic_admission_decisions_gate_policy": (
            "job_id", "candidate_id", "gate_version", "policy_version",
        )
    },
    "acquisition_exceptions": {},
}

EXPECTED_FOREIGN_KEYS = {
    "acquisition_jobs": {
        "fk_acquisition_jobs_research_case_id": (
            ("research_case_id",), "research_cases", ("id",),
        ),
        "fk_acquisition_jobs_thesis_id": (("thesis_id",), "theses", ("id",)),
        "fk_acquisition_jobs_research_run_id": (
            ("research_run_id",), "research_runs", ("id",),
        ),
    },
    "acquisition_job_events": {
        "fk_acquisition_job_events_job_id": (
            ("job_id",), "acquisition_jobs", ("id",),
        )
    },
    "acquisition_attempts": {
        "fk_acquisition_attempts_job_id": (
            ("job_id",), "acquisition_jobs", ("id",),
        )
    },
    "source_references": {
        "fk_source_references_job_id": (
            ("job_id",), "acquisition_jobs", ("id",),
        )
    },
    "retrieval_artifacts": {
        "fk_retrieval_artifacts_source_reference_id": (
            ("source_reference_id",), "source_references", ("id",),
        ),
        "fk_retrieval_artifacts_attempt_id": (
            ("attempt_id",), "acquisition_attempts", ("id",),
        ),
    },
    "retrieval_artifact_documents": {
        "fk_retrieval_artifact_documents_artifact_id": (
            ("retrieval_artifact_id",), "retrieval_artifacts", ("id",),
        ),
        "fk_retrieval_artifact_documents_document_version_id": (
            ("document_version_id",), "document_versions", ("id",),
        ),
    },
    "automatic_admission_decisions": {
        "fk_automatic_admission_decisions_job_id": (
            ("job_id",), "acquisition_jobs", ("id",),
        ),
        "fk_automatic_admission_decisions_candidate_id": (
            ("candidate_id",), "atomic_claim_candidates", ("id",),
        ),
        "fk_automatic_admission_decisions_artifact_id": (
            ("retrieval_artifact_id",), "retrieval_artifacts", ("id",),
        ),
    },
    "acquisition_exceptions": {
        "fk_acquisition_exceptions_job_id": (
            ("job_id",), "acquisition_jobs", ("id",),
        ),
        "fk_acquisition_exceptions_source_reference_id": (
            ("source_reference_id",), "source_references", ("id",),
        ),
        "fk_acquisition_exceptions_retrieval_artifact_id": (
            ("retrieval_artifact_id",), "retrieval_artifacts", ("id",),
        ),
        "fk_acquisition_exceptions_candidate_id": (
            ("candidate_id",), "atomic_claim_candidates", ("id",),
        ),
    },
}

EXPECTED_PROVENANCE_UNIQUES = {
    "source_statements": {
        "uq_source_statements_automatic_admission_decision": (
            "automatic_admission_decision_id",
        )
    }
}

EXPECTED_PROVENANCE_FOREIGN_KEYS = {
    "source_statements": {
        "fk_source_statements_automatic_admission_decision_id": (
            ("automatic_admission_decision_id",),
            "automatic_admission_decisions",
            ("id",),
        )
    },
    "evidence_links": {
        "fk_evidence_links_automatic_admission_decision_id": (
            ("automatic_admission_decision_id",),
            "automatic_admission_decisions",
            ("id",),
        )
    },
}

EXPECTED_CHECKS = {
    "acquisition_jobs": {
        "ck_acquisition_jobs_status": (
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'partial', 'failed', 'cancelled')"
        ),
        "ck_acquisition_jobs_stage": (
            "stage IN ('queued', 'searching', 'fetching', 'freezing', "
            "'extracting', 'admitting', 'succeeded', 'partial', 'failed', "
            "'cancelled')"
        ),
        "ck_acquisition_jobs_attempt_non_negative": "attempt >= 0",
        "ck_acquisition_jobs_counters_non_negative": (
            "reference_count >= 0 AND fetched_count >= 0 AND frozen_count >= 0 "
            "AND admitted_count >= 0 AND exception_count >= 0"
        ),
        "ck_acquisition_jobs_lease_complete": (
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)"
        ),
    },
    "acquisition_job_events": {
        "ck_acquisition_job_events_seq_non_negative": "seq >= 0"
    },
    "acquisition_attempts": {
        "ck_acquisition_attempts_attempt_positive": "attempt_no >= 1"
    },
    "source_references": {},
    "retrieval_artifacts": {
        "ck_retrieval_artifacts_byte_size_positive": "byte_size > 0",
        "ck_retrieval_artifacts_sha256_length": "length(content_sha256) = 64",
        "ck_retrieval_artifacts_byte_size_matches_raw": (
            "byte_size = length(raw_bytes)"
        ),
    },
    "retrieval_artifact_documents": {},
    "automatic_admission_decisions": {
        "ck_automatic_admission_decisions_outcome": (
            "outcome IN ('admitted', 'quarantined')"
        )
    },
    "acquisition_exceptions": {},
}

EXPECTED_PROVENANCE_CHECKS = {
    "evidence_links": {
        "ck_evidence_links_automatic_admission_provenance": (
            "(review_state = 'automatically_admitted' AND "
            "automatic_admission_decision_id IS NOT NULL) OR "
            "(review_state <> 'automatically_admitted' AND "
            "automatic_admission_decision_id IS NULL)"
        )
    }
}

EXPECTED_INDEXES = {
    "acquisition_jobs": {
        "ix_acquisition_jobs_case": ("research_case_id",),
        "ix_acquisition_jobs_thesis": ("thesis_id",),
        "ix_acquisition_jobs_research_run": ("research_run_id",),
        "ix_acquisition_jobs_claim": ("status", "retry_at", "created_at"),
        "ix_acquisition_jobs_lease_expiry": ("lease_expires_at",),
    },
    "acquisition_job_events": {},
    "acquisition_attempts": {},
    "source_references": {},
    "retrieval_artifacts": {
        "ix_retrieval_artifacts_attempt": ("attempt_id",)
    },
    "retrieval_artifact_documents": {
        "ix_retrieval_artifact_documents_document": ("document_version_id",)
    },
    "automatic_admission_decisions": {
        "ix_automatic_admission_decisions_candidate": ("candidate_id",),
        "ix_automatic_admission_decisions_artifact": ("retrieval_artifact_id",),
    },
    "acquisition_exceptions": {
        "ix_acquisition_exceptions_job": ("job_id",),
        "ix_acquisition_exceptions_source_reference": ("source_reference_id",),
        "ix_acquisition_exceptions_artifact": ("retrieval_artifact_id",),
        "ix_acquisition_exceptions_candidate": ("candidate_id",),
        "uq_acquisition_exceptions_automatic_quarantine": (
            "job_id",
            "retrieval_artifact_id",
            "candidate_id",
            "reason_code",
        ),
    },
    "evidence_links": {
        "uq_evidence_links_automatic_admission_decision": (
            "automatic_admission_decision_id",
        )
    },
}


def _unique_constraints(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name or "": tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _foreign_key_constraints(
    table: sa.Table,
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        constraint.name or "": (
            tuple(element.parent.name for element in constraint.elements),
            constraint.elements[0].column.table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    }


def _checks(table: sa.Table) -> dict[str, str]:
    return {
        constraint.name or "": str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _indexes(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        index.name or "": tuple(column.name for column in index.columns)
        for index in table.indexes
    }


def _inspected_unique_constraints(
    inspector: sa.Inspector, table_name: str
) -> dict[str, tuple[str, ...]]:
    return {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table_name)
    }


def _inspected_foreign_keys(
    inspector: sa.Inspector, table_name: str
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        constraint["name"]: (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspector.get_foreign_keys(table_name)
    }


def _inspected_check_names(
    inspector: sa.Inspector, table_name: str
) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(table_name)
    }


def _inspected_indexes(
    inspector: sa.Inspector, table_name: str
) -> dict[str, tuple[str, ...]]:
    return {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes(table_name)
        if index["name"].startswith(("ix_", "uq_"))
        # PostgreSQL exposes a UNIQUE constraint's backing index in the
        # index inspector as well.  It is already asserted above as a
        # constraint and must not be counted as a separately declared index.
        and not index.get("duplicates_constraint")
    }


def assert_migrated_acquisition_schema(
    inspector: sa.Inspector, *, automatic_uniqueness: bool = True
) -> None:
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert _inspected_unique_constraints(inspector, table_name) == expected
    for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
        assert _inspected_foreign_keys(inspector, table_name) == expected
    for table_name, expected in EXPECTED_CHECKS.items():
        assert _inspected_check_names(inspector, table_name) == set(expected)
    for table_name, expected in EXPECTED_INDEXES.items():
        migrated_expected = dict(expected)
        if not automatic_uniqueness and table_name == "acquisition_exceptions":
            migrated_expected.pop(
                "uq_acquisition_exceptions_automatic_quarantine", None
            )
        if not automatic_uniqueness and table_name == "evidence_links":
            migrated_expected = {
                "ix_evidence_links_automatic_admission_decision": (
                    "automatic_admission_decision_id",
                )
            }
        assert _inspected_indexes(inspector, table_name) == migrated_expected

    for table_name, expected in EXPECTED_PROVENANCE_UNIQUES.items():
        relevant = {
            name: columns
            for name, columns in _inspected_unique_constraints(
                inspector, table_name
            ).items()
            if "automatic_admission" in name
        }
        assert relevant == expected
    for table_name, expected in EXPECTED_PROVENANCE_FOREIGN_KEYS.items():
        relevant = {
            name: target
            for name, target in _inspected_foreign_keys(
                inspector, table_name
            ).items()
            if name and "automatic_admission" in name
        }
        assert relevant == expected
    for table_name, expected in EXPECTED_PROVENANCE_CHECKS.items():
        relevant = {
            name
            for name in _inspected_check_names(inspector, table_name)
            if name and "automatic_admission" in name
        }
        assert relevant == set(expected)


def test_metadata_registers_acquisition_tables_and_exact_columns() -> None:
    assert ACQUISITION_TABLES <= set(Base.metadata.tables)
    for table_name, expected in EXPECTED_COLUMNS.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == expected


def test_metadata_declares_exact_constraints_and_important_indexes() -> None:
    for table_name, expected in EXPECTED_UNIQUES.items():
        assert _unique_constraints(Base.metadata.tables[table_name]) == expected
    for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
        assert _foreign_key_constraints(Base.metadata.tables[table_name]) == expected
    for table_name, expected in EXPECTED_CHECKS.items():
        assert _checks(Base.metadata.tables[table_name]) == expected
    for table_name, expected in EXPECTED_INDEXES.items():
        assert _indexes(Base.metadata.tables[table_name]) == expected

    for table_name, expected in EXPECTED_PROVENANCE_UNIQUES.items():
        relevant = {
            name: columns
            for name, columns in _unique_constraints(
                Base.metadata.tables[table_name]
            ).items()
            if "automatic_admission" in name
        }
        assert relevant == expected
    for table_name, expected in EXPECTED_PROVENANCE_FOREIGN_KEYS.items():
        relevant = {
            name: target
            for name, target in _foreign_key_constraints(
                Base.metadata.tables[table_name]
            ).items()
            if "automatic_admission" in name
        }
        assert relevant == expected
    for table_name, expected in EXPECTED_PROVENANCE_CHECKS.items():
        relevant = {
            name: sql
            for name, sql in _checks(Base.metadata.tables[table_name]).items()
            if "automatic_admission" in name
        }
        assert relevant == expected

    external_version = Base.metadata.tables["source_references"].c.external_version
    assert external_version.nullable is False
    assert external_version.default is not None
    assert external_version.server_default is not None

    assert "automatically_admitted" in get_args(ReviewState)

    evidence_index = next(
        index
        for index in Base.metadata.tables["evidence_links"].indexes
        if index.name == "uq_evidence_links_automatic_admission_decision"
    )
    quarantine_index = next(
        index
        for index in Base.metadata.tables["acquisition_exceptions"].indexes
        if index.name == "uq_acquisition_exceptions_automatic_quarantine"
    )
    assert evidence_index.unique is True
    assert quarantine_index.unique is True
    assert evidence_index.dialect_options["sqlite"]["where"] is not None
    assert quarantine_index.dialect_options["sqlite"]["where"] is not None


def test_overview_key_change_accepts_automatically_admitted_review_state() -> None:
    change = KeyChangeDTO(
        id="evidence-link-1",
        tag="新增",
        text="Automatically admitted evidence",
        occurred_at="2026-08-12T00:00:00Z",
        source_label="SSE disclosure",
        review_state="automatically_admitted",
    )

    assert change.review_state == "automatically_admitted"


def test_metadata_declares_exact_job_and_integrity_check_vocabulary() -> None:
    job_checks = _checks(Base.metadata.tables["acquisition_jobs"])
    assert all(
        status in job_checks["ck_acquisition_jobs_status"]
        for status in (
            "queued", "running", "retry_wait", "succeeded", "partial", "failed",
            "cancelled",
        )
    )
    assert all(
        stage in job_checks["ck_acquisition_jobs_stage"]
        for stage in (
            "queued", "searching", "fetching", "freezing", "extracting",
            "admitting", "succeeded", "partial", "failed", "cancelled",
        )
    )
    assert set(_checks(Base.metadata.tables["retrieval_artifacts"])) == {
        "ck_retrieval_artifacts_byte_size_positive",
        "ck_retrieval_artifacts_sha256_length",
        "ck_retrieval_artifacts_byte_size_matches_raw",
    }
    assert _checks(Base.metadata.tables["automatic_admission_decisions"])[
        "ck_automatic_admission_decisions_outcome"
    ] == "outcome IN ('admitted', 'quarantined')"


def _new_sqlite_schema() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return engine


@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    [
        ({"raw_bytes": b"", "byte_size": 0}, "byte_size_positive"),
        ({"content_sha256": "a" * 63}, "sha256_length"),
        ({"raw_bytes": b"a", "byte_size": 2}, "byte_size_matches_raw"),
    ],
)
def test_sqlite_rejects_invalid_retrieval_artifact_integrity(
    overrides: dict[str, object], constraint_name: str
) -> None:
    engine = _new_sqlite_schema()
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "source_reference_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "content_sha256": "a" * 64,
        "raw_bytes": b"a",
        "mime_type": "text/plain",
        "byte_size": 1,
        "final_url": "https://example.test/artifact",
        "retrieved_at": now,
    }
    values.update(overrides)

    with pytest.raises(sa.exc.IntegrityError, match=constraint_name):
        with engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["retrieval_artifacts"].insert().values(**values)
            )


def test_sqlite_rejects_unknown_automatic_admission_outcome() -> None:
    engine = _new_sqlite_schema()
    with pytest.raises(
        sa.exc.IntegrityError, match="automatic_admission_decisions_outcome"
    ):
        with engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["automatic_admission_decisions"].insert(),
                {
                    "id": uuid.uuid4(),
                    "job_id": uuid.uuid4(),
                    "candidate_id": uuid.uuid4(),
                    "retrieval_artifact_id": uuid.uuid4(),
                    "outcome": "reviewed",
                    "gate_version": "gate-v1",
                    "policy_version": "policy-v1",
                    "gate_results": {},
                    "created_at": datetime.now(UTC),
                },
            )


def _evidence_link_values(
    *, review_state: str, decision_id: uuid.UUID | None
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4(),
        "thesis_id": uuid.uuid4(),
        "source_statement_id": uuid.uuid4(),
        "role": "supports",
        "reason": "test",
        "scope": {},
        "available_at": now,
        "creator_type": "ai",
        "review_state": review_state,
        "automatic_admission_decision_id": decision_id,
        "created_at": now,
    }


@pytest.mark.parametrize(
    ("review_state", "decision_id"),
    [
        ("automatically_admitted", None),
        ("machine_generated", uuid.uuid4()),
    ],
)
def test_sqlite_rejects_inconsistent_automatic_admission_provenance(
    review_state: str, decision_id: uuid.UUID | None
) -> None:
    engine = _new_sqlite_schema()
    with pytest.raises(
        sa.exc.IntegrityError, match="evidence_links_automatic_admission_provenance"
    ):
        with engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["evidence_links"].insert(),
                _evidence_link_values(
                    review_state=review_state, decision_id=decision_id
                ),
            )


def test_sqlite_accepts_existing_and_automatic_provenance_pairs() -> None:
    engine = _new_sqlite_schema()
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["evidence_links"].insert(),
            [
                _evidence_link_values(
                    review_state="machine_generated", decision_id=None
                ),
                _evidence_link_values(
                    review_state="automatically_admitted",
                    decision_id=uuid.uuid4(),
                ),
            ],
        )


def test_sqlite_allows_many_legacy_links_but_one_link_per_automatic_decision() -> None:
    engine = _new_sqlite_schema()
    decision_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["evidence_links"].insert(),
            [
                _evidence_link_values(
                    review_state="machine_generated", decision_id=None
                ),
                _evidence_link_values(
                    review_state="machine_generated", decision_id=None
                ),
                _evidence_link_values(
                    review_state="automatically_admitted", decision_id=decision_id
                ),
            ],
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                Base.metadata.tables["evidence_links"].insert(),
                _evidence_link_values(
                    review_state="automatically_admitted", decision_id=decision_id
                ),
            )


def test_sqlite_allows_other_exceptions_but_one_logical_automatic_quarantine() -> None:
    engine = _new_sqlite_schema()
    now = datetime.now(UTC)
    logical = {
        "job_id": uuid.uuid4(),
        "source_reference_id": uuid.uuid4(),
        "retrieval_artifact_id": uuid.uuid4(),
        "candidate_id": uuid.uuid4(),
        "reason_code": "automatic_admission_quarantined",
        "detail_json": {},
        "created_at": now,
    }
    table = Base.metadata.tables["acquisition_exceptions"]
    with engine.begin() as connection:
        connection.execute(table.insert(), {"id": uuid.uuid4(), **logical})
        connection.execute(
            table.insert(),
            {
                "id": uuid.uuid4(),
                **logical,
                "reason_code": "ordinary_fetch_failure",
            },
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(table.insert(), {"id": uuid.uuid4(), **logical})


def test_append_only_guard_covers_acquisition_history_but_not_jobs() -> None:
    assert APPEND_ONLY_TABLES <= IMMUTABLE_TABLES
    engine = sa.create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table_name in sorted(APPEND_ONLY_TABLES):
            table = Base.metadata.tables[table_name]
            with pytest.raises(ImmutableLedgerError, match="append-only"):
                connection.execute(table.update().values(id=table.c.id))
            with pytest.raises(ImmutableLedgerError, match="append-only"):
                connection.execute(table.delete())

        jobs = Base.metadata.tables["acquisition_jobs"]
        connection.execute(jobs.update().values(status="running"))
        connection.execute(jobs.delete())


def test_sqlite_upgrade_downgrade_and_reupgrade_0052(tmp_path: Path) -> None:
    database_path = tmp_path / "acquisition-0051.db"
    backend = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database_path}",
    }
    previous = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert previous.returncode == 0, previous.stderr

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0052"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0052"
        assert ACQUISITION_TABLES <= set(inspector.get_table_names())
        assert_migrated_acquisition_schema(inspector, automatic_uniqueness=False)
        assert "automatic_admission_decision_id" in {
            column["name"] for column in inspector.get_columns("source_statements")
        }
        assert "automatic_admission_decision_id" in {
            column["name"] for column in inspector.get_columns("evidence_links")
        }
    engine.dispose()

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0051"
        assert ACQUISITION_TABLES.isdisjoint(inspector.get_table_names())
        assert "automatic_admission_decision_id" not in {
            column["name"] for column in inspector.get_columns("source_statements")
        }
        assert "automatic_admission_decision_id" not in {
            column["name"] for column in inspector.get_columns("evidence_links")
        }
    engine.dispose()

    reupgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0052"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reupgraded.returncode == 0, reupgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0052"
        assert ACQUISITION_TABLES <= set(inspector.get_table_names())
        assert_migrated_acquisition_schema(inspector, automatic_uniqueness=False)


def test_sqlite_upgrade_downgrade_and_reupgrade_0053(tmp_path: Path) -> None:
    database_path = tmp_path / "automatic-admission-0053.db"
    backend = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database_path}",
    }
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0053"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0053"
        assert_migrated_acquisition_schema(inspector)
        assert "uq_evidence_links_automatic_admission_decision" in {
            index["name"] for index in inspector.get_indexes("evidence_links")
        }
        assert "uq_acquisition_exceptions_automatic_quarantine" in {
            index["name"]
            for index in inspector.get_indexes("acquisition_exceptions")
        }
    engine.dispose()

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0052"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        names = {
            index["name"]
            for table in ("evidence_links", "acquisition_exceptions")
            for index in sa.inspect(connection).get_indexes(table)
        }
        assert "uq_evidence_links_automatic_admission_decision" not in names
        assert "uq_acquisition_exceptions_automatic_quarantine" not in names
        assert "ix_evidence_links_automatic_admission_decision" in names
    engine.dispose()

    reupgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0053"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert reupgraded.returncode == 0, reupgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"], future=True)
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0053"
        assert_migrated_acquisition_schema(sa.inspect(connection))
    engine.dispose()
