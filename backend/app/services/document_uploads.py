"""Freeze original uploaded material before attempting document parsing."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasources.docling import PypdfAdapter
from app.errors import ValidationFailedError
from app.models.ledger import DocumentUploadArtifact, DocumentVersion
from app.models.operational import EventResearchLifecycle
from app.repositories.documents import DocumentRepository
from app.services.ingest import DocumentService
from app.services.source_governance import SourceGovernanceService

_SUPPORTED_MIME_TYPES = frozenset(
    {"application/pdf", "text/plain", "text/markdown", "text/csv"}
)


@dataclass(frozen=True)
class UploadedCaseMaterial:
    document: DocumentVersion
    next_action: str


@dataclass(frozen=True)
class _ParsedUpload:
    parser_version: str
    parse_state: str
    spans: tuple[tuple[dict, str, str | None, str | None, dict | None], ...]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentUploadService:
    """One-purpose command service for a user-supplied original file.

    Parsing happens in memory before the immutable document record is made,
    because an immutable `DocumentVersion` cannot truthfully change from
    `pending` to `failed`.  Every recognised parse failure is converted to a
    failed immutable version and its original artifact is written in the same
    database transaction; no parser text is fabricated.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentService(DocumentRepository(session))

    def freeze_case_material(
        self,
        *,
        case_id: uuid.UUID,
        raw: bytes,
        file_name: str,
        mime_type: str,
        actor: str,
        source_metadata: dict[str, object],
    ) -> UploadedCaseMaterial:
        return self._freeze_case_material(
            case_id=case_id,
            raw=raw,
            file_name=file_name,
            mime_type=mime_type,
            actor=actor,
            source_metadata=source_metadata,
            published_case=False,
        )

    def freeze_published_case_material(
        self,
        *,
        case_id: uuid.UUID,
        raw: bytes,
        file_name: str,
        mime_type: str,
        actor: str,
        source_metadata: dict[str, object],
    ) -> UploadedCaseMaterial:
        """Freeze an original for an explicit published-Case review decision.

        This deliberately performs no lifecycle transition.  The caller must
        subsequently record either ``reopen`` or ``no_change`` in the same
        transaction, so the prior published conclusion is never rewritten by
        material intake alone.
        """
        return self._freeze_case_material(
            case_id=case_id,
            raw=raw,
            file_name=file_name,
            mime_type=mime_type,
            actor=actor,
            source_metadata=source_metadata,
            published_case=True,
        )

    def _freeze_case_material(
        self,
        *,
        case_id: uuid.UUID,
        raw: bytes,
        file_name: str,
        mime_type: str,
        actor: str,
        source_metadata: dict[str, object],
        published_case: bool,
    ) -> UploadedCaseMaterial:
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if lifecycle is None:
            raise ValidationFailedError("event research case not found")
        if lifecycle.status == "published" and not published_case:
            raise ValidationFailedError(
                "published case material must use the published-material decision workflow"
            )
        if lifecycle.status != "published" and published_case:
            raise ValidationFailedError(
                "only a published event research case can accept a material decision"
            )
        if not raw:
            raise ValidationFailedError("uploaded file must not be empty")
        if mime_type not in _SUPPORTED_MIME_TYPES:
            raise ValidationFailedError(
                "unsupported original file type; upload PDF, TXT, Markdown, or CSV"
            )
        safe_name = file_name.strip()
        if not safe_name:
            raise ValidationFailedError("uploaded file must have a file name")
        parsed = self._parse(raw=raw, mime_type=mime_type, file_name=safe_name)
        digest = hashlib.sha256(raw).hexdigest()
        document = self._documents.freeze(
            raw=raw,
            source_url=f"upload://{digest}",
            parser_version=parsed.parser_version,
            title=safe_name,
            byte_size=len(raw),
            parse_state=parsed.parse_state,
            source_authority=source_metadata.get("authority_level", "user_supplied"),
        )
        self._record_artifact(
            document=document,
            raw=raw,
            file_name=safe_name,
            mime_type=mime_type,
            actor=actor,
            retention_policy=str(source_metadata.get("retention_policy") or "case_retained"),
        )
        self._documents.attach_to_case(
            research_case_id=case_id, document_version_id=document.id
        )
        SourceGovernanceService(self._session).record_event_intake(
            document=document,
            source_type="uploaded_file",
            source_metadata=source_metadata,
            declared_by=actor,
        )
        for locator, verbatim_text, text_sha256, context_hash, locator_v1 in parsed.spans:
            self._documents.add_span(
                document_version_id=document.id,
                locator=locator,
                verbatim_text=verbatim_text,
                text_sha256=text_sha256,
                context_hash=context_hash,
                locator_v1=locator_v1,
            )
        return UploadedCaseMaterial(
            document=document,
            next_action=(
                "supplement_original"
                if parsed.parse_state == "failed"
                else "review_original"
            ),
        )

    def _record_artifact(
        self,
        *,
        document: DocumentVersion,
        raw: bytes,
        file_name: str,
        mime_type: str,
        actor: str,
        retention_policy: str,
    ) -> None:
        existing = self._session.scalar(
            select(DocumentUploadArtifact).where(
                DocumentUploadArtifact.document_version_id == document.id
            )
        )
        if existing is not None:
            return
        digest = hashlib.sha256(raw).hexdigest()
        self._session.add(
            DocumentUploadArtifact(
                document_version_id=document.id,
                content_sha256=digest,
                object_version=f"sha256:{digest}",
                storage_kind="database_blob",
                file_name=file_name,
                mime_type=mime_type,
                byte_size=len(raw),
                raw_bytes=raw,
                uploaded_by=actor,
                retention_policy=retention_policy,
                created_at=_utcnow(),
            )
        )
        self._session.flush()

    @staticmethod
    def _parse(*, raw: bytes, mime_type: str, file_name: str) -> _ParsedUpload:
        if mime_type == "application/pdf":
            digest = hashlib.sha256(raw).hexdigest()
            try:
                parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)
            except Exception:  # pypdf has several parse-error subclasses.
                return _ParsedUpload(
                    parser_version=PypdfAdapter.parser_version,
                    parse_state="failed",
                    spans=(),
                )
            if not parsed:
                return _ParsedUpload(
                    parser_version=PypdfAdapter.parser_version,
                    parse_state="failed",
                    spans=(),
                )
            return _ParsedUpload(
                parser_version=PypdfAdapter.parser_version,
                parse_state="success",
                spans=tuple(
                    (
                        span.legacy_locator_dict(),
                        span.verbatim_text,
                        span.text_sha256,
                        span.context_hash,
                        span.locator.to_storage_dict(),
                    )
                    for span in parsed
                ),
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _ParsedUpload(
                parser_version="uploaded-text-v1",
                parse_state="failed",
                spans=(),
            )
        if not text.strip():
            return _ParsedUpload(
                parser_version="uploaded-text-v1",
                parse_state="failed",
                spans=(),
            )
        return _ParsedUpload(
            parser_version="uploaded-text-v1",
            parse_state="partial",
            spans=(
                (
                    {"kind": "uploaded_file", "file_name": file_name, "mime_type": mime_type, "line_start": 1, "line_end": text.count("\n") + 1},
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    None,
                    None,
                ),
            ),
        )
