from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.models.ledger import ImmutableLedgerError


def test_uploaded_original_artifact_preserves_exact_bytes_and_is_append_only(
    session, document
) -> None:
    from app.models.ledger import DocumentUploadArtifact

    raw = b"original uploaded document bytes"
    digest = hashlib.sha256(raw).hexdigest()
    artifact = DocumentUploadArtifact(
        document_version_id=document.id,
        content_sha256=digest,
        object_version=f"sha256:{digest}",
        storage_kind="database_blob",
        file_name="research.pdf",
        mime_type="application/pdf",
        byte_size=len(raw),
        raw_bytes=raw,
        uploaded_by="human:lin",
        retention_policy="case_retained",
        created_at=datetime.now(timezone.utc),
    )
    session.add(artifact)
    session.commit()

    assert session.get(DocumentUploadArtifact, artifact.id).raw_bytes == raw
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(DocumentUploadArtifact)
            .where(DocumentUploadArtifact.id == artifact.id)
            .values(file_name="rewritten.pdf")
        )
