"""Contracts and provenance records for trusted report intake."""
from __future__ import annotations

import importlib.util
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DatabaseError, IntegrityError

from app.models.ledger import (
    CaseDocumentVersion,
    DocumentSupplementLink,
    DocumentSourceRecord,
    DocumentVersion,
    ImmutableLedgerError,
    ReportCaseInitialAdmission,
    SourceContractVersion,
)
from app.repositories.source_contracts import SourceContractRepository


def _append_contract_with(
    repository: SourceContractRepository,
    *,
    tenant_id: str = "tenant-a",
    may_display: bool = True,
    may_search: bool = True,
    may_ai_process: bool = True,
    may_export: bool = False,
    may_api_use: bool = False,
    region: str = "CN",
    effective_until: datetime | None = None,
    retention_until: datetime | None = None,
    downstream_restrictions: str = "internal research only",
) -> SourceContractVersion:
    return repository.append_contract(
        provider_name="fixture-provider",
        tenant_id=tenant_id,
        may_display=may_display,
        may_search=may_search,
        may_ai_process=may_ai_process,
        may_export=may_export,
        may_api_use=may_api_use,
        region=region,
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=effective_until,
        retention_until=retention_until,
        deletion_policy="fixture deletion policy",
        downstream_restrictions=downstream_restrictions,
        approved_by="legal-reviewer",
        reason="fixture contract",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0037_source_contract_report_snapshots.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "source_contract_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _append_contract(repository: SourceContractRepository) -> SourceContractVersion:
    return repository.append_contract(
        provider_name="licensed-research-provider",
        tenant_id="tenant-a",
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=False,
        may_api_use=False,
        region="CN",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_until=None,
        retention_until=None,
        deletion_policy="delete on contract expiry",
        downstream_restrictions="internal research only",
        approved_by="legal-reviewer",
        reason="approved provider agreement",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def _other_document(session) -> DocumentVersion:
    now = datetime.now(timezone.utc)
    version = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://example.test/supplement",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add(version)
    session.flush()
    return version


def _admit_initial_report_case_source(
    repository: SourceContractRepository,
    *,
    case_id: uuid.UUID,
    source_record: DocumentSourceRecord,
) -> ReportCaseInitialAdmission:
    return repository.add_report_case_initial_admission(
        research_case_id=case_id,
        document_source_record_id=source_record.id,
        tenant_id=source_record.tenant_id,
        created_at=source_record.created_at,
    )


def test_contract_source_record_and_supplement_link_persist(session, document) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)
    source_record = repository.add_document_source_record(
        document_version_id=document.id,
        source_contract_version_id=contract.id,
        admission_type="licensed_provider",
        tenant_id="tenant-a",
        source_actor="intake-worker",
        provider_record_id="provider-report-42",
        verification_state="verified",
        acquisition_request={"request_id": "request-42"},
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    supplement = _other_document(session)
    supplement_link = repository.add_supplement_link(
        original_document_version_id=document.id,
        supplement_document_version_id=supplement.id,
        claimed_page_reference="page 12",
        created_by="researcher-a",
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    session.commit()

    assert (
        session.get(SourceContractVersion, contract.id).provider_name
        == "licensed-research-provider"
    )
    assert (
        session.get(DocumentSourceRecord, source_record.id).provider_record_id
        == "provider-report-42"
    )
    assert (
        session.get(DocumentSupplementLink, supplement_link.id).claimed_page_reference
        == "page 12"
    )


def test_supplement_has_one_parent_and_can_point_at_a_distinct_document(session, document) -> None:
    repository = SourceContractRepository(session)
    other_parent = _other_document(session)
    supplement = _other_document(session)
    link = repository.add_supplement_link(
        original_document_version_id=document.id,
        supplement_document_version_id=supplement.id,
        claimed_page_reference=None,
        created_by="researcher-a",
        created_at=datetime.now(timezone.utc),
    )

    assert link.original_document_version_id != link.supplement_document_version_id
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repository.add_supplement_link(
                original_document_version_id=other_parent.id,
                supplement_document_version_id=supplement.id,
                claimed_page_reference=None,
                created_by="researcher-b",
                created_at=datetime.now(timezone.utc),
            )


def test_document_source_record_is_unique_per_document_tenant_and_contract(
    session, document
) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)
    original_record = repository.add_document_source_record(
        document_version_id=document.id,
        source_contract_version_id=contract.id,
        admission_type="pasted_snapshot",
        tenant_id="tenant-a",
        source_actor="researcher-a",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"editor": "researcher-a"},
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repository.add_document_source_record(
                document_version_id=document.id,
                source_contract_version_id=contract.id,
                admission_type="pasted_snapshot",
                tenant_id="tenant-a",
                source_actor="researcher-b",
                provider_record_id=None,
                verification_state="verified",
                acquisition_request={"editor": "researcher-b"},
                created_at=datetime.now(timezone.utc),
            )


def test_document_source_record_rejects_unknown_admission_type(session, document) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            repository.add_document_source_record(
                document_version_id=document.id,
                source_contract_version_id=contract.id,
                admission_type="unverified_web_scrape",
                tenant_id="tenant-a",
                source_actor="collector",
                provider_record_id=None,
                verification_state="pending_review",
                acquisition_request={"url": document.source_url},
                created_at=datetime.now(timezone.utc),
            )


def test_source_contract_ledger_models_reject_update_and_delete(session, document) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)
    source_record = repository.add_document_source_record(
        document_version_id=document.id,
        source_contract_version_id=contract.id,
        admission_type="uploaded_file",
        tenant_id="tenant-a",
        source_actor="researcher-a",
        provider_record_id=None,
        verification_state="pending_review",
        acquisition_request={"filename": "report.pdf"},
        created_at=datetime.now(timezone.utc),
    )
    supplement = _other_document(session)
    supplement_link = repository.add_supplement_link(
        original_document_version_id=document.id,
        supplement_document_version_id=supplement.id,
        claimed_page_reference=None,
        created_by="researcher-a",
        created_at=datetime.now(timezone.utc),
    )

    for model, row_id, values in (
        (SourceContractVersion, contract.id, {"reason": "changed"}),
        (DocumentSourceRecord, source_record.id, {"verification_state": "changed"}),
        (DocumentSupplementLink, supplement_link.id, {"created_by": "changed"}),
    ):
        with pytest.raises(ImmutableLedgerError):
            session.execute(update(model).where(model.id == row_id).values(**values))
        with pytest.raises(ImmutableLedgerError):
            session.execute(delete(model).where(model.id == row_id))


def test_report_case_initial_admission_is_append_only(
    session, document, research_service
) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)
    source_record = repository.add_document_source_record(
        document_version_id=document.id,
        source_contract_version_id=contract.id,
        admission_type="pasted_snapshot",
        tenant_id="tenant-a",
        source_actor="researcher-a",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"title": "fixture"},
        created_at=datetime.now(timezone.utc),
    )
    case = research_service.add_case(
        title="来源归属测试", industry_topic="研报研究", created_by="tester"
    )
    admission = _admit_initial_report_case_source(
        repository, case_id=case.id, source_record=source_record
    )

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(ReportCaseInitialAdmission)
            .where(ReportCaseInitialAdmission.id == admission.id)
            .values(tenant_id="tenant-b")
        )
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            delete(ReportCaseInitialAdmission).where(
                ReportCaseInitialAdmission.id == admission.id
            )
        )


def test_source_contract_repository_reads_records_and_contract(session, document) -> None:
    repository = SourceContractRepository(session)
    contract = _append_contract(repository)
    record = repository.add_document_source_record(
        document_version_id=document.id,
        source_contract_version_id=contract.id,
        admission_type="public_url",
        tenant_id="tenant-a",
        source_actor="collector",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"url": document.source_url},
        created_at=datetime.now(timezone.utc),
    )

    assert repository.contract_for_record(record.id) == contract
    assert (
        repository.source_record_for_document(document.id, tenant_id="tenant-a")
        == record
    )
    assert repository.source_record_for_document(document.id, tenant_id="tenant-b") is None


def test_source_contract_migration_creates_foreign_keys_indexes_and_safe_downgrade() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE document_versions (id VARCHAR(36) PRIMARY KEY)")
        )
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "source_contract_versions",
            "document_source_records",
            "document_supplement_links",
        } <= set(inspector.get_table_names())
        assert {
            fk["referred_table"]
            for fk in inspector.get_foreign_keys("document_source_records")
        } == {"document_versions", "source_contract_versions"}
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("document_source_records")
        } == {"ck_document_source_records_admission_type"}
        assert len(inspector.get_foreign_keys("document_supplement_links")) == 2
        assert {
            index["name"] for index in inspector.get_indexes("document_supplement_links")
        } == {
            "ix_document_supplement_links_original_document_version",
            "ix_document_supplement_links_supplement_document_version",
        }

        migration.downgrade()

        assert not {
            "source_contract_versions",
            "document_source_records",
            "document_supplement_links",
        } & set(sa.inspect(connection).get_table_names())


def test_source_contract_migration_installs_triggers_and_refuses_data_loss(
    monkeypatch,
) -> None:
    migration = _migration()
    executed: list[str] = []
    bind = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        execute=lambda _statement: SimpleNamespace(scalar_one=lambda: 1),
    )
    fake_op = SimpleNamespace(
        get_bind=lambda: bind,
        create_table=lambda *_args: None,
        create_index=lambda *_args: None,
        execute=executed.append,
    )
    monkeypatch.setattr(migration, "op", fake_op)

    migration.upgrade()

    assert executed == [
        f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        for table in migration._IMMUTABLE_TABLES
        for action in ("update", "delete")
    ]
    with pytest.raises(RuntimeError, match="source_contract_versions contains 1"):
        migration.downgrade()


def test_source_contract_migration_rejects_sqlite_raw_mutation_and_replace() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    ids = {name: uuid.uuid4().hex for name in (
        "original", "other_original", "supplement", "contract", "record", "link"
    )}
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE document_versions (id VARCHAR(36) PRIMARY KEY)")
        )
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text("INSERT INTO document_versions (id) VALUES (:id)"),
            [{"id": ids[name]} for name in ("original", "other_original", "supplement")],
        )
        connection.execute(
            sa.text(
                "INSERT INTO source_contract_versions "
                "(id, provider_name, tenant_id, may_display, may_search, may_ai_process, "
                "may_export, may_api_use, region, effective_from, deletion_policy, "
                "downstream_restrictions, approved_by, reason, created_at) "
                "VALUES (:id, 'provider', 'tenant-a', 1, 1, 1, 0, 0, 'CN', "
                "'2026-01-01T00:00:00+00:00', 'delete', 'internal', 'legal', "
                "'approved', '2026-01-01T00:00:00+00:00')"
            ),
            {"id": ids["contract"]},
        )
        connection.execute(
            sa.text(
                "INSERT INTO document_source_records "
                "(id, document_version_id, source_contract_version_id, admission_type, "
                "tenant_id, source_actor, verification_state, acquisition_request, created_at) "
                "VALUES (:id, :document_id, :contract_id, 'licensed_provider', "
                "'tenant-a', 'original-actor', 'verified', '{}', "
                "'2026-01-01T00:00:00+00:00')"
            ),
            {
                "id": ids["record"],
                "document_id": ids["original"],
                "contract_id": ids["contract"],
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO document_supplement_links "
                "(id, original_document_version_id, supplement_document_version_id, "
                "created_by, created_at) "
                "VALUES (:id, :original_id, :supplement_id, 'original-actor', "
                "'2026-01-01T00:00:00+00:00')"
            ),
            {
                "id": ids["link"],
                "original_id": ids["original"],
                "supplement_id": ids["supplement"],
            },
        )

        attacks = (
            (
                "source_contract_versions",
                "provider_name",
                "changed",
                "INSERT OR REPLACE INTO source_contract_versions "
                "(id, provider_name, tenant_id, may_display, may_search, may_ai_process, "
                "may_export, may_api_use, region, effective_from, deletion_policy, "
                "downstream_restrictions, approved_by, reason, created_at) "
                "VALUES (:id, 'replaced', 'tenant-a', 1, 1, 1, 0, 0, 'CN', "
                "'2026-01-01T00:00:00+00:00', 'delete', 'internal', 'legal', "
                "'approved', '2026-01-01T00:00:00+00:00')",
                {"id": ids["contract"]},
                "provider",
            ),
            (
                "document_source_records",
                "source_actor",
                "changed",
                "INSERT OR REPLACE INTO document_source_records "
                "(id, document_version_id, source_contract_version_id, admission_type, "
                "tenant_id, source_actor, verification_state, acquisition_request, created_at) "
                "VALUES (:new_id, :document_id, :contract_id, 'licensed_provider', "
                "'tenant-a', 'replaced', 'verified', '{}', '2026-01-01T00:00:00+00:00')",
                {
                    "new_id": uuid.uuid4().hex,
                    "document_id": ids["original"],
                    "contract_id": ids["contract"],
                },
                "original-actor",
            ),
            (
                "document_supplement_links",
                "created_by",
                "changed",
                "INSERT OR REPLACE INTO document_supplement_links "
                "(id, original_document_version_id, supplement_document_version_id, "
                "created_by, created_at) VALUES (:new_id, :original_id, :supplement_id, "
                "'replaced', '2026-01-01T00:00:00+00:00')",
                {
                    "new_id": uuid.uuid4().hex,
                    "original_id": ids["other_original"],
                    "supplement_id": ids["supplement"],
                },
                "original-actor",
            ),
        )
        table_ids = {
            "source_contract_versions": ids["contract"],
            "document_source_records": ids["record"],
            "document_supplement_links": ids["link"],
        }
        for table, column, replacement, replace_sql, replace_params, original in attacks:
            row_id = table_ids[table]
            with pytest.raises(DatabaseError):
                connection.execute(
                    sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                    {"value": replacement, "id": row_id},
                )
            with pytest.raises(DatabaseError):
                connection.execute(
                    sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id}
                )
            with pytest.raises(DatabaseError):
                connection.execute(sa.text(replace_sql), replace_params)
            assert connection.scalar(
                sa.text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id}
            ) == original

        for table, column, original, replace_sql, replace_params in (
            (
                "document_source_records",
                "source_actor",
                "original-actor",
                "INSERT OR REPLACE INTO document_source_records "
                "(id, document_version_id, source_contract_version_id, admission_type, "
                "tenant_id, source_actor, verification_state, acquisition_request, created_at) "
                "VALUES (:id, :document_id, :contract_id, 'licensed_provider', "
                "'tenant-a', 'replaced', 'verified', '{}', '2026-01-01T00:00:00+00:00')",
                {
                    "id": ids["record"],
                    "document_id": ids["original"],
                    "contract_id": ids["contract"],
                },
            ),
            (
                "document_supplement_links",
                "created_by",
                "original-actor",
                "INSERT OR REPLACE INTO document_supplement_links "
                "(id, original_document_version_id, supplement_document_version_id, "
                "created_by, created_at) VALUES (:id, :original_id, :supplement_id, "
                "'replaced', '2026-01-01T00:00:00+00:00')",
                {
                    "id": ids["link"],
                    "original_id": ids["original"],
                    "supplement_id": ids["supplement"],
                },
            ),
        ):
            with pytest.raises(DatabaseError):
                connection.execute(sa.text(replace_sql), replace_params)
            assert connection.scalar(
                sa.text(f"SELECT {column} FROM {table} WHERE id = :id"),
                {"id": table_ids[table]},
            ) == original


def test_permissions_intersect_capabilities_and_earliest_retention() -> None:
    from app.services.source_contracts import SourcePermission, intersect_permissions

    original = SourcePermission(
        may_display=True,
        may_search=True,
        may_ai_process=True,
        may_export=True,
        may_api_use=False,
        retention_until=datetime(2026, 12, 31, tzinfo=timezone.utc),
        region="GLOBAL",
        downstream_restrictions="internal research only",
    )
    supplement = SourcePermission(
        may_display=True,
        may_search=False,
        may_ai_process=True,
        may_export=False,
        may_api_use=True,
        retention_until=datetime(2026, 6, 30, tzinfo=timezone.utc),
        region="CN",
        downstream_restrictions="no export",
    )

    effective = intersect_permissions(original, supplement)

    assert effective.may_display is True
    assert effective.may_search is False
    assert effective.may_ai_process is True
    assert effective.may_export is False
    assert effective.may_api_use is False
    assert effective.retention_until == datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert effective.region == "CN"
    assert effective.downstream_restrictions == "internal research only AND no export"


def test_source_permission_is_a_slotted_immutable_value() -> None:
    from app.services.source_contracts import SourcePermission

    permission = SourcePermission(may_ai_process=True)

    assert not hasattr(permission, "__dict__")
    with pytest.raises(AttributeError):
        permission.may_ai_process = False


def test_require_ai_processing_rejects_missing_expired_and_disallowed_contracts() -> None:
    from app.services.source_contracts import SourcePermission, require_ai_processing

    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="missing"):
        require_ai_processing(None, now=now)
    with pytest.raises(ValueError, match="expired"):
        require_ai_processing(
            SourcePermission(may_ai_process=True, effective_until=datetime(2026, 8, 7, tzinfo=timezone.utc)),
            now=now,
        )
    with pytest.raises(ValueError, match="does not permit AI"):
        require_ai_processing(SourcePermission(may_ai_process=False), now=now)


def test_recovery_snapshot_creates_distinct_case_attached_source_chain(session, document_service, research_service) -> None:
    from app.models.ledger import CaseDocumentVersion, SourceSpan
    from app.services.source_contracts import RecoverySupplementSnapshotService

    case = research_service.add_case(
        title="失败 PDF 恢复", industry_topic="研报研究", created_by="tester"
    )
    original_raw = b"%PDF-original-failed"
    original = document_service.freeze(
        raw=original_raw,
        source_url="report://fixture/original-pdf",
        title="失败 PDF 恢复",
        parse_state="failed",
    )
    document_service.attach_to_case(
        research_case_id=case.id, document_version_id=original.id
    )
    repository = SourceContractRepository(session)
    original_contract = _append_contract_with(repository, retention_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
    original_record = repository.add_document_source_record(
        document_version_id=original.id,
        source_contract_version_id=original_contract.id,
        admission_type="uploaded_file",
        tenant_id="tenant-a",
        source_actor="uploader",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={"filename": "failed.pdf"},
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _admit_initial_report_case_source(
        repository, case_id=case.id, source_record=original_record
    )
    supplement_contract = _append_contract_with(
        repository,
        may_export=True,
        retention_until=datetime(2026, 9, 1, tzinfo=timezone.utc),
        downstream_restrictions="no redistribution",
    )

    result = RecoverySupplementSnapshotService(session).admit(
        research_case_id=case.id,
        original_document_version_id=original.id,
        content="研报第 3 页的可读正文。",
        claimed_page_reference="第 3 页",
        created_by="analyst-a",
        tenant_id="tenant-a",
        supplement_contract_version_id=supplement_contract.id,
    )

    assert result.document.id != original.id
    assert result.document.content_sha256 == hashlib.sha256("研报第 3 页的可读正文。".encode()).hexdigest()
    assert result.document.source_url == f"pasted://report-supplement/{result.document.content_sha256}"
    assert session.scalar(select(CaseDocumentVersion).where(
        CaseDocumentVersion.research_case_id == case.id,
        CaseDocumentVersion.document_version_id == result.document.id,
    )) is not None
    record = session.get(DocumentSourceRecord, result.source_record.id)
    assert record is not None and record.admission_type == "pasted_snapshot"
    assert session.get(DocumentSupplementLink, result.link.id).original_document_version_id == original.id
    assert result.span.document_version_id == result.document.id
    assert result.span.locator == {
        "input_kind": "recovery_text",
        "claimed_page_reference": "第 3 页",
        "parser": "user-pasted-report-v1",
    }
    assert result.span.text_sha256 == hashlib.sha256("研报第 3 页的可读正文。".encode()).hexdigest()
    effective_contract = session.get(SourceContractVersion, record.source_contract_version_id)
    assert effective_contract is not None
    assert effective_contract.may_export is False
    assert effective_contract.retention_until.replace(tzinfo=timezone.utc) == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    )
    assert effective_contract.downstream_restrictions == "internal research only AND no redistribution"
    assert original.content_sha256 == hashlib.sha256(original_raw).hexdigest()
    assert session.scalar(select(SourceSpan).where(SourceSpan.document_version_id == original.id)) is None


def test_recovery_snapshot_rejects_tenant_mismatch_and_cross_case_document(session, document_service, research_service) -> None:
    from app.services.source_contracts import RecoverySupplementSnapshotService

    first_case = research_service.add_case(title="甲", industry_topic="研报研究", created_by="tester")
    second_case = research_service.add_case(title="乙", industry_topic="研报研究", created_by="tester")
    original = document_service.freeze(raw=b"original", source_url="report://fixture/original")
    document_service.attach_to_case(research_case_id=first_case.id, document_version_id=original.id)
    repository = SourceContractRepository(session)
    original_contract = _append_contract_with(repository, tenant_id="tenant-a")
    original_record = repository.add_document_source_record(
        document_version_id=original.id,
        source_contract_version_id=original_contract.id,
        admission_type="uploaded_file",
        tenant_id="tenant-a",
        source_actor="uploader",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={},
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _admit_initial_report_case_source(
        repository, case_id=first_case.id, source_record=original_record
    )
    other_tenant_contract = _append_contract_with(repository, tenant_id="tenant-b")
    service = RecoverySupplementSnapshotService(session)

    with pytest.raises(ValueError, match="tenant"):
        service.admit(
            research_case_id=first_case.id,
            original_document_version_id=original.id,
            content="恢复正文",
            claimed_page_reference=None,
            created_by="analyst-a",
            tenant_id="tenant-a",
            supplement_contract_version_id=other_tenant_contract.id,
        )
    tenant_a_contract = _append_contract_with(repository, tenant_id="tenant-a")
    with pytest.raises(ValueError, match="belong"):
        service.admit(
            research_case_id=second_case.id,
            original_document_version_id=original.id,
            content="恢复正文",
            claimed_page_reference=None,
            created_by="analyst-a",
            tenant_id="tenant-a",
            supplement_contract_version_id=tenant_a_contract.id,
        )
    contract_count = session.query(SourceContractVersion).count()
    with pytest.raises(ValueError, match="self-link"):
        service.admit(
            research_case_id=first_case.id,
            original_document_version_id=original.id,
            content="original",
            claimed_page_reference=None,
            created_by="analyst-a",
            tenant_id="tenant-a",
            supplement_contract_version_id=tenant_a_contract.id,
        )
    assert session.query(SourceContractVersion).count() == contract_count


def test_recovery_intersects_every_original_source_record_before_allowing_ai(
    session, document_service, research_service
) -> None:
    from app.services.source_contracts import RecoverySupplementSnapshotService

    case = research_service.add_case(title="多来源限制", industry_topic="研报研究", created_by="tester")
    original = document_service.freeze(raw=b"original", source_url="report://fixture/multi")
    document_service.attach_to_case(research_case_id=case.id, document_version_id=original.id)
    repository = SourceContractRepository(session)
    restrictive = _append_contract_with(repository, may_ai_process=False)
    permissive = _append_contract_with(repository, may_ai_process=True)
    for contract, actor in ((restrictive, "earlier-restrictive"), (permissive, "later-permissive")):
        record = repository.add_document_source_record(
            document_version_id=original.id,
            source_contract_version_id=contract.id,
            admission_type="uploaded_file",
            tenant_id="tenant-a",
            source_actor=actor,
            provider_record_id=None,
            verification_state="verified",
            acquisition_request={},
            created_at=datetime(2026, 8, 1 if contract is restrictive else 2, tzinfo=timezone.utc),
        )
        if contract is restrictive:
            _admit_initial_report_case_source(
                repository, case_id=case.id, source_record=record
            )
    supplement_contract = _append_contract_with(repository)

    with pytest.raises(ValueError, match="does not permit AI"):
        RecoverySupplementSnapshotService(session).admit(
            research_case_id=case.id,
            original_document_version_id=original.id,
            content="恢复正文",
            claimed_page_reference=None,
            created_by="analyst-a",
            tenant_id="tenant-a",
            supplement_contract_version_id=supplement_contract.id,
        )


def test_recovery_collision_is_recorded_as_case_local_snapshot_artifact(
    session, document_service, research_service
) -> None:
    from app.services.source_contracts import RecoverySupplementSnapshotService

    content = "与其他案例相同的恢复正文"
    other_case = research_service.add_case(title="既有内容", industry_topic="研报研究", created_by="tester")
    preexisting = document_service.freeze(
        raw=content.encode(), source_url="https://example.test/already-frozen", title="既有内容"
    )
    document_service.attach_to_case(research_case_id=other_case.id, document_version_id=preexisting.id)
    case = research_service.add_case(title="恢复案例", industry_topic="研报研究", created_by="tester")
    original = document_service.freeze(raw=b"failed PDF", source_url="report://fixture/collision")
    document_service.attach_to_case(research_case_id=case.id, document_version_id=original.id)
    repository = SourceContractRepository(session)
    original_contract = _append_contract_with(repository)
    original_record = repository.add_document_source_record(
        document_version_id=original.id,
        source_contract_version_id=original_contract.id,
        admission_type="uploaded_file",
        tenant_id="tenant-a",
        source_actor="uploader",
        provider_record_id=None,
        verification_state="verified",
        acquisition_request={},
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _admit_initial_report_case_source(
        repository, case_id=case.id, source_record=original_record
    )
    supplement_contract = _append_contract_with(repository)

    result = RecoverySupplementSnapshotService(session).admit(
        research_case_id=case.id,
        original_document_version_id=original.id,
        content=content,
        claimed_page_reference="第 9 页",
        created_by="analyst-a",
        tenant_id="tenant-a",
        supplement_contract_version_id=supplement_contract.id,
    )

    assert result.document is None
    assert result.artifact is not None
    assert result.artifact.research_case_id == case.id
    assert result.artifact.original_document_version_id == original.id
    assert result.artifact.content_sha256 == preexisting.content_sha256
    assert result.artifact.source_identity == f"pasted://report-supplement/{preexisting.content_sha256}"
    assert result.artifact.claimed_page_reference == "第 9 页"
    assert session.scalar(
        select(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case.id,
            CaseDocumentVersion.document_version_id == preexisting.id,
        )
    ) is None
