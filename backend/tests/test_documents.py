import uuid

import pytest
from sqlalchemy import update

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
