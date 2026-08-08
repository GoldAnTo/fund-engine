import hashlib
import uuid
from datetime import datetime, timezone
from threading import Barrier, Thread

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.models.ledger import ImmutableLedgerError, SourceSpan


def test_same_content_hash_reuses_document_version(document_service):
    first = document_service.freeze(raw=b"page one", source_url="https://example.test/a")
    second = document_service.freeze(raw=b"page one", source_url="https://example.test/a")
    assert second.id == first.id


def test_changed_bytes_append_new_document_version(document_service):
    first = document_service.freeze(raw=b"v1", source_url="https://example.test/a")
    second = document_service.freeze(raw=b"v2", source_url="https://example.test/a")
    assert second.id != first.id
    assert second.supersedes_id == first.id


def test_freeze_with_different_source_url_does_not_supersede(document_service):
    a = document_service.freeze(raw=b"v1", source_url="https://example.test/a")
    b = document_service.freeze(raw=b"v1", source_url="https://example.test/b")
    assert b.id == a.id
    assert b.supersedes_id is None


def test_add_span_persists_locator_and_verbatim_text(document_service):
    version = document_service.freeze(raw=b"doc", source_url="https://example.test/d")
    span = document_service.add_span(
        document_version_id=version.id,
        locator={"page": 3, "table": 1, "row": 4},
        verbatim_text="资本开支同比增长 120%",
    )
    assert span.id is not None
    assert span.locator == {"page": 3, "table": 1, "row": 4}
    assert span.verbatim_text == "资本开支同比增长 120%"
    assert span.document_version_id == version.id


def test_source_span_is_not_mutable(session, span):
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(SourceSpan)
            .where(SourceSpan.id == span.id)
            .values(verbatim_text="changed")
        )


def test_document_version_is_not_deletable(session, document_service):
    version = document_service.freeze(raw=b"to delete", source_url="https://example.test/x")
    from sqlalchemy import delete
    from app.models.ledger import DocumentVersion

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            delete(DocumentVersion).where(DocumentVersion.id == version.id)
        )


@pytest.mark.pg_only
def test_postgres_document_freeze_serializes_same_report_source_revisions(
    engine, monkeypatch
) -> None:
    from app.models.ledger import DocumentVersion
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    SessionLocal = sessionmaker(bind=engine, future=True)
    source_url = "report://pasted_text/concurrent-report"
    published_at = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    start = Barrier(2)
    before_source_lock = Barrier(2)
    errors: list[BaseException] = []

    monkeypatch.setattr(
        "app.services.ingest._before_document_source_lock",
        lambda: before_source_lock.wait(timeout=5),
    )

    def _freeze(raw: bytes) -> None:
        db = SessionLocal()
        try:
            start.wait(timeout=5)
            DocumentService(DocumentRepository(db)).freeze(
                raw=raw,
                source_url=source_url,
                title="同一研报",
                published_at=published_at,
                natural_key=hashlib.sha256(raw).hexdigest()[:32],
            )
            db.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first = Thread(target=_freeze, args=(b"revision one",))
    second = Thread(target=_freeze, args=(b"revision two",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    verify = SessionLocal()
    try:
        versions = list(
            verify.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.source_url == source_url)
                .order_by(DocumentVersion.acquired_at, DocumentVersion.id)
            )
        )
        assert len(versions) == 2
        assert sum(version.supersedes_id is None for version in versions) == 1
        assert any(
            version.supersedes_id in {other.id for other in versions if other.id != version.id}
            for version in versions
        )
    finally:
        verify.close()


@pytest.mark.pg_only
def test_postgres_concurrent_identical_document_freeze_rereads_unique_conflict(
    engine, monkeypatch
) -> None:
    from app.models.ledger import DocumentVersion
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    SessionLocal = sessionmaker(bind=engine, future=True)
    start = Barrier(2)
    before_insert = Barrier(2)
    errors: list[BaseException] = []
    raw = b"identical concurrent document"

    monkeypatch.setattr(
        "app.services.ingest._before_document_version_insert",
        lambda: before_insert.wait(timeout=5),
    )

    def _freeze(source_url: str) -> None:
        db = SessionLocal()
        try:
            start.wait(timeout=5)
            DocumentService(DocumentRepository(db)).freeze(
                raw=raw,
                source_url=source_url,
                title="同一份正文",
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            db.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
            db.rollback()
        finally:
            db.close()

    first = Thread(target=_freeze, args=("https://one.example.com/report",))
    second = Thread(target=_freeze, args=("https://two.example.com/report",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    verify = SessionLocal()
    try:
        assert verify.scalar(
            select(DocumentVersion).where(
                DocumentVersion.content_sha256 == hashlib.sha256(raw).hexdigest()
            )
        ) is not None
        assert len(
            list(
                verify.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.content_sha256
                        == hashlib.sha256(raw).hexdigest()
                    )
                )
            )
        ) == 1
    finally:
        verify.close()
