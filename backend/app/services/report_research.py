"""Create an auditable research case from one company research report."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.datasources.docling import PARSER_VERSION_PYPDF, PypdfAdapter
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentBlob,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
)
from app.models.report_research import ReportClaim, ReportRelation
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.report_research import CreateReportResearchRequest
from app.services.ingest import DocumentService
from app.services.document_blobs import LocalImmutableBlobStore
from app.services.research import ResearchService


_REPORT_CLAIM_KINDS = frozenset(
    {"report_opinion", "report_forecast", "report_assumption", "report_risk"}
)


@dataclass(frozen=True)
class ExtractedReportRelation:
    """A relation candidate from one report assertion.

    Company IDs are optional and must refer to an existing ledger company.  A
    missing ID is intentionally represented by a name-only graph node.
    """

    subject_name: str | None
    object_name: str | None
    relation_kind: str
    mechanism: str
    subject_company_id: uuid.UUID | None = None
    object_company_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ExtractedReportClaim:
    kind: str
    statement: str
    relations: tuple[ExtractedReportRelation, ...] = ()


class ReportContentExtractor(Protocol):
    def extract(self, *, span: SourceSpan) -> Sequence[ExtractedReportClaim]: ...


class RuleBasedReportContentExtractor:
    """A conservative local fallback until a model-backed parser is configured.

    It creates only explicit report assertions.  Relationship extraction is
    intentionally delegated to the parser seam: guessing a company identity
    from prose would be worse than retaining an unresolved named node.
    """

    _sentences = re.compile(r"(?<=[。！？；;\n])")
    _company_name = r"[\u4e00-\u9fffA-Za-z0-9]{2,64}"
    _role_relation = re.compile(
        rf"(?P<subject>{_company_name})\s*(?:是|为)\s*"
        rf"(?P<object>{_company_name})的(?P<role>供应商|客户|竞争对手)"
    )
    _competitor_relation = re.compile(
        rf"(?P<subject>{_company_name})与(?P<object>{_company_name})(?:存在)?竞争"
    )
    _narrator_prefix = re.compile(
        r"^(?:(?:本)?报告|研报|公司|管理层)(?:认为|指出|表示|判断|提到|称|强调)"
        r"(?:[:：，,、\s]*)"
    )

    def extract(self, *, span: SourceSpan) -> Sequence[ExtractedReportClaim]:
        extracted: list[ExtractedReportClaim] = []
        for fragment in self._sentences.split(span.verbatim_text):
            statement = fragment.strip()
            if not statement:
                continue
            relations = tuple(self._relations_for(statement))
            kind = self._kind_for(statement)
            # An explicit company relationship is itself a report assertion.
            # Do not require an additional narrator/opinion marker to retain
            # it for later verification.
            if kind is None and relations:
                kind = "report_opinion"
            if kind is not None:
                extracted.append(
                    ExtractedReportClaim(
                        kind=kind,
                        statement=statement,
                        relations=relations,
                    )
                )
        return extracted

    def _relations_for(self, statement: str) -> Sequence[ExtractedReportRelation]:
        """Extract only explicit named relationships, retaining names as nodes.

        This rule never resolves or inserts a Company.  It intentionally
        recognizes a narrow, easily reviewable set of Chinese report phrases;
        broader entity resolution belongs behind an injected parser boundary.
        """
        normalized_statement = self._narrator_prefix.sub("", statement).strip()
        role_match = self._role_relation.search(normalized_statement)
        if role_match is not None:
            role = role_match.group("role")
            kind = {
                "供应商": "supplier",
                "客户": "customer",
                "竞争对手": "competitor",
            }[role]
            return (
                ExtractedReportRelation(
                    subject_name=role_match.group("subject"),
                    object_name=role_match.group("object"),
                    relation_kind=kind,
                    mechanism=f"研报明确称为{role}",
                ),
            )
        competitor_match = self._competitor_relation.search(normalized_statement)
        if competitor_match is not None:
            return (
                ExtractedReportRelation(
                    subject_name=competitor_match.group("subject"),
                    object_name=competitor_match.group("object"),
                    relation_kind="competitor",
                    mechanism="研报明确称双方存在竞争关系",
                ),
            )
        return ()

    @staticmethod
    def _kind_for(statement: str) -> str | None:
        if any(marker in statement for marker in ("风险", "不及预期", "下行")):
            return "report_risk"
        if any(marker in statement for marker in ("假设", "前提", "基于")):
            return "report_assumption"
        if any(marker in statement for marker in ("预计", "预测", "目标价", "将")):
            return "report_forecast"
        if any(marker in statement for marker in ("观点", "看好", "判断", "认为", "核心结论")):
            return "report_opinion"
        return None


class ReportClaimExtractor:
    """Append source statements, then append claims and report-only relations."""

    def __init__(
        self, session: Session, extractor: ReportContentExtractor | None = None
    ) -> None:
        self._session = session
        self._extractor = extractor or RuleBasedReportContentExtractor()
        self._research = ResearchService(ResearchRepository(session))

    def extract(self, research_case_id: uuid.UUID) -> list[ReportClaim]:
        claims: list[ReportClaim] = []
        for span in self._case_spans(research_case_id):
            for draft in self._extractor.extract(span=span):
                self._validate_claim(draft)
                # Statement first: claims and relations always lead back to the
                # original span's stable page/paragraph locator.
                source_statement = self._research.add_statement(
                    span.id,
                    draft.statement.strip(),
                    kind=("forecast" if draft.kind == "report_forecast" else "research_opinion"),
                )
                claim = ReportClaim(
                    research_case_id=research_case_id,
                    source_span_id=span.id,
                    source_statement_id=source_statement.id,
                    kind=draft.kind,
                    statement=draft.statement.strip(),
                )
                self._session.add(claim)
                self._session.flush()
                for relation_draft in draft.relations:
                    self._session.add(
                        self._relation_from_draft(
                            claim=claim,
                            research_case_id=research_case_id,
                            span=span,
                            draft=relation_draft,
                        )
                    )
                claims.append(claim)
        self._session.flush()
        return claims

    def _case_spans(self, research_case_id: uuid.UUID) -> list[SourceSpan]:
        return list(
            self._session.scalars(
                select(SourceSpan)
                .join(
                    CaseDocumentVersion,
                    CaseDocumentVersion.document_version_id
                    == SourceSpan.document_version_id,
                )
                .where(CaseDocumentVersion.research_case_id == research_case_id)
                .order_by(SourceSpan.id)
            )
        )

    @staticmethod
    def _validate_claim(draft: ExtractedReportClaim) -> None:
        if draft.kind not in _REPORT_CLAIM_KINDS:
            raise ValueError(f"unsupported report claim kind: {draft.kind}")
        if not draft.statement.strip():
            raise ValueError("report claim statement must not be blank")

    def _relation_from_draft(
        self,
        *,
        claim: ReportClaim,
        research_case_id: uuid.UUID,
        span: SourceSpan,
        draft: ExtractedReportRelation,
    ) -> ReportRelation:
        relation_kind = draft.relation_kind.strip()
        mechanism = draft.mechanism.strip()
        if not relation_kind or not mechanism:
            raise ValueError("report relation kind and mechanism must not be blank")
        subject_company_id = self._existing_company_id(draft.subject_company_id)
        object_company_id = self._existing_company_id(draft.object_company_id)
        subject_name = self._node_name(draft.subject_name)
        object_name = self._node_name(draft.object_name)
        if subject_company_id is None and subject_name is None:
            raise ValueError("report relation subject must be a company or named node")
        if object_company_id is None and object_name is None:
            raise ValueError("report relation object must be a company or named node")
        return ReportRelation(
            claim_id=claim.id,
            research_case_id=research_case_id,
            source_span_id=span.id,
            source_statement_id=claim.source_statement_id,
            subject_company_id=subject_company_id,
            object_company_id=object_company_id,
            subject_name=subject_name,
            object_name=object_name,
            relation_kind=relation_kind,
            mechanism=mechanism,
            status="report_claim",
        )

    def _existing_company_id(self, company_id: uuid.UUID | None) -> uuid.UUID | None:
        if company_id is None:
            return None
        if self._session.get(Company, company_id) is None:
            raise ValueError(f"report relation references unknown company {company_id}")
        return company_id

    @staticmethod
    def _node_name(name: str | None) -> str | None:
        if name is None:
            return None
        normalized = " ".join(name.split())
        return normalized or None


@dataclass(frozen=True)
class CreatedReportResearch:
    case: ResearchCase
    document: DocumentVersion
    input_kind: str
    publisher: str | None
    state: str
    source_statement_ids: list


def _before_document_blob_insert() -> None:
    """Test seam for the concurrent immutable-blob reference race."""


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
        statement_ids.extend(self._extract_claim_statement_ids(case.id))
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
                    "filename": filename,
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
        statement_ids.extend(self._extract_claim_statement_ids(case.id))
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

    def _extract_claim_statement_ids(self, research_case_id: uuid.UUID) -> list:
        """Run the default report parser immediately after source intake.

        The input and every original span are already frozen at this point.
        Extraction appends its own narrow SourceStatements and cannot mutate
        either original text or page/paragraph locators.
        """
        claims = ReportClaimExtractor(self._session).extract(research_case_id)
        return [claim.source_statement_id for claim in claims]

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
        try:
            with self._session.begin_nested():
                _before_document_blob_insert()
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
        except IntegrityError:
            # A concurrent request persisted the same immutable reference.
            # The savepoint leaves the outer case/document transaction usable;
            # re-read and validate the winner before treating it as a replay.
            existing = self._session.query(DocumentBlob).filter_by(
                document_version_id=document.id
            ).one_or_none()
            if existing is None:
                raise
            self._blobs.read(existing)
            return existing

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
