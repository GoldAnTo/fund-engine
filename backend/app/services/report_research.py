"""Create an auditable research case from one company research report."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.datasources.docling import PARSER_VERSION_PYPDF, PypdfAdapter
from app.models.ledger import DocumentBlob, DocumentVersion, ResearchCase
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.report_research import CreateReportResearchRequest
from app.services.ingest import DocumentService
from app.services.document_blobs import LocalImmutableBlobStore
from app.services.research import ResearchService


@dataclass(frozen=True)
class CreatedReportResearch:
    case: ResearchCase
    document: DocumentVersion
    input_kind: str
    publisher: str | None
    state: str
    source_statement_ids: list


class ReportResearchService:
    """Freeze report bytes/text before any later AI extraction can inspect them.

    A failed PDF parse is a normal, recoverable intake state: its original
    bytes, failure marker and a provenance statement remain attached to the
    newly-created case, so a researcher can paste text or select pages later.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentService(DocumentRepository(session))
        self._research = ResearchService(ResearchRepository(session))
        self._blobs = LocalImmutableBlobStore()

    def create_text(
        self, request: CreateReportResearchRequest
    ) -> CreatedReportResearch:
        raw = request.content.encode("utf-8")
        source_url = request.source_url or self._generated_source_url(
            request.input_kind,
            title=request.title,
            publisher=request.publisher,
            published_at=request.published_at,
        )
        document = self._documents.freeze(
            raw=raw,
            source_url=source_url,
            published_at=request.published_at,
            parser_version="user-pasted-report-v1",
            title=request.title,
            natural_key=self._content_natural_key(raw),
            language="zh",
            parse_state="partial",
        )
        case = self._create_case(request.title, request.publisher, request.created_by)
        self._documents.attach_to_case(
            research_case_id=case.id, document_version_id=document.id
        )
        statement_ids = self._append_text_statement(
            document=document,
            content=request.content,
            input_kind=request.input_kind,
            publisher=request.publisher,
        )
        self._session.commit()
        return CreatedReportResearch(
            case=case,
            document=document,
            input_kind=request.input_kind,
            publisher=request.publisher,
            state="ready_to_extract",
            source_statement_ids=statement_ids,
        )

    def create_pdf(
        self,
        *,
        raw: bytes,
        title: str,
        publisher: str | None,
        published_at: datetime | None,
        filename: str | None,
        created_by: str,
    ) -> CreatedReportResearch:
        if not raw:
            raise ValueError("PDF upload must not be empty")
        normalized_title = title.strip()
        normalized_publisher = publisher.strip() if publisher else None
        digest = hashlib.sha256(raw).hexdigest()
        try:
            parsed_spans = PypdfAdapter().extract_spans(raw, document_sha256=digest)
        except Exception as exc:  # parser failures must retain the original file
            return self._create_failed_pdf(
                raw=raw,
                title=normalized_title,
                publisher=normalized_publisher,
                published_at=published_at,
                filename=filename,
                created_by=created_by,
                parse_error=exc,
            )

        document = self._documents.freeze(
            raw=raw,
            source_url=self._generated_source_url(
                "pdf_upload",
                title=normalized_title,
                publisher=normalized_publisher,
                published_at=published_at,
            ),
            published_at=published_at,
            parser_version=PARSER_VERSION_PYPDF,
            title=normalized_title,
            natural_key=self._content_natural_key(raw),
            language="zh",
            parse_state="success",
        )
        self._persist_uploaded_blob(document, raw)
        case = self._create_case(normalized_title, normalized_publisher, created_by)
        self._documents.attach_to_case(
            research_case_id=case.id, document_version_id=document.id
        )
        statement_ids: list = []
        for parsed in parsed_spans:
            locator = parsed.legacy_locator_dict()
            locator.update(
                {
                    "input_kind": "pdf_upload",
                    "publisher": normalized_publisher,
                    "published_at": self._iso_published_at(document.published_at),
                }
            )
            span = self._documents.add_span(
                document_version_id=document.id,
                locator=locator,
                verbatim_text=parsed.verbatim_text,
                text_sha256=parsed.text_sha256,
                context_hash=parsed.context_hash,
                locator_v1=parsed.locator.to_storage_dict(),
            )
            statement = self._research.add_statement(
                span.id, parsed.verbatim_text, kind="research_opinion"
            )
            statement_ids.append(statement.id)
        self._session.commit()
        return CreatedReportResearch(
            case=case,
            document=document,
            input_kind="pdf_upload",
            publisher=normalized_publisher,
            state="ready_to_extract",
            source_statement_ids=statement_ids,
        )

    def _create_failed_pdf(
        self,
        *,
        raw: bytes,
        title: str,
        publisher: str | None,
        published_at: datetime | None,
        filename: str | None,
        created_by: str,
        parse_error: Exception,
    ) -> CreatedReportResearch:
        document = self._documents.freeze(
            raw=raw,
            source_url=self._generated_source_url(
                "pdf_upload",
                title=title,
                publisher=publisher,
                published_at=published_at,
            ),
            published_at=published_at,
            parser_version=PARSER_VERSION_PYPDF,
            title=title,
            natural_key=self._content_natural_key(raw),
            language=None,
            parse_state="failed",
        )
        self._persist_uploaded_blob(document, raw)
        case = self._create_case(title, publisher, created_by)
        self._documents.attach_to_case(
            research_case_id=case.id, document_version_id=document.id
        )
        span = self._documents.add_span(
            document_version_id=document.id,
            locator={
                "input_kind": "pdf_upload",
                "publisher": publisher,
                "filename": filename,
                "published_at": self._iso_published_at(document.published_at),
                "parse_state": "failed",
                "parse_error": type(parse_error).__name__,
            },
            verbatim_text="PDF uploaded but no extractable text is available.",
        )
        statement = self._research.add_statement(
            span.id,
            "PDF uploaded but no extractable text is available.",
            kind="research_opinion",
        )
        self._session.commit()
        return CreatedReportResearch(
            case=case,
            document=document,
            input_kind="pdf_upload",
            publisher=publisher,
            state="needs_text_or_pages",
            source_statement_ids=[statement.id],
        )

    def _append_text_statement(
        self,
        *,
        document: DocumentVersion,
        content: str,
        input_kind: str,
        publisher: str | None,
    ) -> list:
        span = self._documents.add_span(
            document_version_id=document.id,
            locator={
                "input_kind": input_kind,
                "publisher": publisher,
                "published_at": self._iso_published_at(document.published_at),
                "paragraph": 1,
                "parser": "user-pasted-report-v1",
            },
            verbatim_text=content,
        )
        statement = self._research.add_statement(
            span.id, content, kind="research_opinion"
        )
        return [statement.id]

    def _create_case(
        self, title: str, publisher: str | None, created_by: str
    ) -> ResearchCase:
        return self._research.add_case(
            title=title,
            industry_topic="研报研究",
            created_by=created_by,
            research_object=publisher or title,
            core_question=f"验证《{title}》中的核心观点及其市场影响。",
        )

    def _persist_uploaded_blob(self, document: DocumentVersion, raw: bytes) -> DocumentBlob:
        existing = self._session.query(DocumentBlob).filter_by(
            document_version_id=document.id
        ).one_or_none()
        if existing is not None:
            self._blobs.read(existing)
            return existing
        storage_key, digest = self._blobs.persist(raw)
        blob = DocumentBlob(
            document_version_id=document.id,
            storage_key=storage_key,
            content_sha256=digest,
            byte_size=len(raw),
            media_type="application/pdf",
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(blob)
        self._session.flush()
        return blob

    @staticmethod
    def _iso_published_at(published_at: datetime | None) -> str | None:
        if published_at is None:
            return None
        if published_at.tzinfo is None:
            return published_at.replace(tzinfo=timezone.utc).isoformat()
        return published_at.isoformat()

    @staticmethod
    def _content_natural_key(raw: bytes) -> str:
        """Keep report versions distinct when title/date metadata is reused."""
        return hashlib.sha256(b"report-content-v1\0" + raw).hexdigest()[:32]

    @staticmethod
    def _generated_source_url(
        input_kind: str,
        *,
        title: str,
        publisher: str | None,
        published_at: datetime | None,
    ) -> str:
        # The generated source identity is stable across revisions of one
        # pasted/uploaded report. Content itself lives in ``natural_key`` so a
        # revised file becomes a successor rather than being collapsed by the
        # generic (source, title, date) normalizer.
        identity = "\0".join(
            [
                input_kind,
                title.strip().casefold(),
                (publisher or "").strip().casefold(),
                published_at.isoformat() if published_at else "",
            ]
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"report://{input_kind}/{digest}"
