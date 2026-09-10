"""Create an auditable research case from one company research report."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasources.docling import PARSER_VERSION_PYPDF, PypdfAdapter
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentBlob,
    DocumentSourceRecord,
    DocumentVersion,
    RecoverySupplementSnapshot,
    ResearchCase,
    SourceContractVersion,
    SourceSpan,
)
from app.models.report_research import (
    ReportCaseSourceSpan,
    ReportClaim,
    ReportExtractionClaim,
    ReportResearchScopeClaim,
    ReportResearchScopeRelation,
    ReportResearchScopeVersion,
    ReportRelation,
)
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.repositories.source_contracts import SourceContractRepository
from app.repositories.auto_research import AutoResearchRepository
from app.schemas.v1.report_research import CreateReportResearchRequest
from app.services.ingest import DocumentService
from app.services.document_blobs import LocalImmutableBlobStore
from app.services.research import ResearchService
from app.services.event_research_scope_evidence import lock_event_scope_case
from app.services.source_contracts import (
    RecoverySupplementSnapshotService,
    require_ai_processing,
)


_REPORT_CLAIM_KINDS = frozenset(
    {"report_opinion", "report_forecast", "report_assumption", "report_risk"}
)
_REPORT_EXTRACTOR_VERSION = "report-rule-v2"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


@dataclass(frozen=True)
class ReportFactorAssessment:
    """A conservative, explainable gate for a report's proposed factor.

    A broker's assertion stays a ``report_claim`` in the ledger.  The graph
    may promote its *research priority* to ``key`` only after independent
    company, operating, market, peer and confounder checks are all present.
    This is intentionally a projection, not a mutation of the original
    claim, so a later collection run cannot rewrite what the report said.
    """

    classification: str
    components: dict[str, bool]
    explanation: str


@dataclass(frozen=True)
class ReportResearchScopeSelection:
    """One display-safe immutable scope with its persisted path selection."""

    scope: ReportResearchScopeVersion
    selected_claim_ids: list[uuid.UUID]
    selected_relation_ids: list[uuid.UUID]


class ReportFactorClassifier:
    """Classify a report factor without conflating opinion with verified fact."""

    @staticmethod
    def classify(
        *,
        report_source: bool,
        company_relation: bool,
        operating: bool,
        market: bool,
        peer: bool,
        confounder_assessed: bool,
        competing_explanation: bool,
    ) -> ReportFactorAssessment:
        components = {
            "report_source": report_source,
            "company_relation": company_relation,
            "operating": operating,
            "market": market,
            "peer": peer,
            "confounder": confounder_assessed,
        }
        if all(components.values()) and not competing_explanation:
            return ReportFactorAssessment(
                classification="key",
                components=components,
                explanation=(
                    "研报主张、独立公司关系、经营、市场、同业控制和同期混杂"
                    "因素评估均已具备；该因素可作为待审阅的关键因素。"
                ),
            )
        if competing_explanation:
            return ReportFactorAssessment(
                classification="alternative",
                components=components,
                explanation=(
                    "同期存在可审计混杂因素，且市场或同业观察不能单独归因于研报；"
                    "应将其作为替代解释竞争，而非关键因素。"
                ),
            )
        missing = [label for label, present in components.items() if not present]
        return ReportFactorAssessment(
            classification="evidence_gap",
            components=components,
            explanation="证据不足：尚缺 " + "、".join(missing) + "。",
        )


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
        rf"(?P<subject>{_company_name}?)与(?P<object>{_company_name}?)(?:存在)?竞争"
    )
    # Consume the *construction* (speaker + attribution/prediction verb),
    # rather than maintaining a brittle list of every possible narrator.
    # This intentionally favors an unresolved relation over a polluted node.
    _attribution_prefix = re.compile(
        r"^(?:[\u4e00-\u9fffA-Za-z0-9]{1,16}"
        r"(?:认为|预计|预测|判断|指出|表示|提到|称|强调|看好)"
        r"|据悉|传闻|消息称|报道称)(?:[:：，,、\s]*)"
    )
    _unsafe_entity_cue = re.compile(
        r"(?:认为|预计|预测|判断|指出|表示|提到|称|强调|看好|据悉|传闻|消息|报道)"
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
        normalized_statement = self._strip_attribution_prefixes(statement)
        role_match = self._role_relation.search(normalized_statement)
        if role_match is not None:
            role = role_match.group("role")
            kind = {
                "供应商": "supplier",
                "客户": "customer",
                "竞争对手": "competitor",
            }[role]
            return self._safe_relation(
                subject_name=role_match.group("subject"),
                object_name=role_match.group("object"),
                relation_kind=kind,
                mechanism=f"研报明确称为{role}",
            )
        competitor_match = self._competitor_relation.search(normalized_statement)
        if competitor_match is not None:
            return self._safe_relation(
                subject_name=competitor_match.group("subject"),
                object_name=competitor_match.group("object"),
                relation_kind="competitor",
                mechanism="研报明确称双方存在竞争关系",
            )
        return ()

    def _strip_attribution_prefixes(self, statement: str) -> str:
        normalized = statement.strip()
        # Nested attributions such as “据悉券商认为…” are common.  A bounded
        # loop strips only leading constructions, never a relation's interior.
        for _ in range(3):
            stripped = self._attribution_prefix.sub("", normalized, count=1).strip()
            if stripped == normalized:
                break
            normalized = stripped
        return normalized

    def _safe_relation(
        self,
        *,
        subject_name: str,
        object_name: str,
        relation_kind: str,
        mechanism: str,
    ) -> Sequence[ExtractedReportRelation]:
        if self._unsafe_entity_cue.search(subject_name) or self._unsafe_entity_cue.search(
            object_name
        ):
            return ()
        return (
            ExtractedReportRelation(
                subject_name=subject_name,
                object_name=object_name,
                relation_kind=relation_kind,
                mechanism=mechanism,
            ),
        )

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
        self,
        session: Session,
        extractor: ReportContentExtractor | None = None,
        *,
        extractor_version: str | None = None,
    ) -> None:
        self._session = session
        self._extractor = extractor or RuleBasedReportContentExtractor()
        self._extractor_version = extractor_version or _REPORT_EXTRACTOR_VERSION
        self._research = ResearchService(ResearchRepository(session))

    def extract(
        self, research_case_id: uuid.UUID, *, input_fingerprint: str | None = None
    ) -> list[ReportClaim]:
        """Append one complete extraction or no output at all.

        The durable extraction claim is part of the same outer savepoint as
        every SourceStatement, ReportClaim and ReportRelation.  If a later
        span yields an invalid draft, all earlier output and its claim roll
        back, leaving a clean retry path.
        """
        claims: list[ReportClaim] = []
        spans_by_document: dict[DocumentVersion, list[SourceSpan]] = {}
        for span, document in self._case_spans(research_case_id):
            spans_by_document.setdefault(document, []).append(span)
        with self._session.begin_nested():
            for document, spans in spans_by_document.items():
                if not self._claim_document_extraction(
                    research_case_id, document, input_fingerprint=input_fingerprint
                ):
                    continue
                for span in spans:
                    for draft in self._extractor.extract(span=span):
                        self._validate_claim(draft)
                        # Statement first: claims and relations always lead
                        # back to the original span's stable locator.
                        source_statement = self._research.add_statement(
                            span.id,
                            draft.statement.strip(),
                            kind=(
                                "forecast"
                                if draft.kind == "report_forecast"
                                else "research_opinion"
                            ),
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

    def _case_spans(
        self, research_case_id: uuid.UUID
    ) -> list[tuple[SourceSpan, DocumentVersion]]:
        return list(
            self._session.execute(
                select(SourceSpan, DocumentVersion)
                .join(
                    ReportCaseSourceSpan,
                    ReportCaseSourceSpan.source_span_id == SourceSpan.id,
                )
                .join(
                    DocumentVersion,
                    DocumentVersion.id == ReportCaseSourceSpan.document_version_id,
                )
                .where(ReportCaseSourceSpan.research_case_id == research_case_id)
                .order_by(ReportCaseSourceSpan.document_version_id, SourceSpan.id)
            )
        )

    def _claim_document_extraction(
        self,
        research_case_id: uuid.UUID,
        document: DocumentVersion,
        *,
        input_fingerprint: str | None = None,
    ) -> bool:
        """Claim one case/document/version/fingerprint key without poisoning retry."""
        fingerprint = input_fingerprint or document.content_sha256
        existing = self._session.scalar(
            select(ReportExtractionClaim.id).where(
                ReportExtractionClaim.research_case_id == research_case_id,
                ReportExtractionClaim.document_version_id == document.id,
                ReportExtractionClaim.extractor_version == self._extractor_version,
                ReportExtractionClaim.input_fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return False
        try:
            with self._session.begin_nested():
                self._session.add(
                    ReportExtractionClaim(
                        research_case_id=research_case_id,
                        document_version_id=document.id,
                        extractor_version=self._extractor_version,
                        input_fingerprint=fingerprint,
                    )
                )
                self._session.flush()
            return True
        except IntegrityError:
            # A concurrent worker won the unique claim.  Its output is the
            # only valid result for this extractor/input identity.
            return False

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
class ReportIntakeOutcome:
    """A truthful intake handoff before candidate extraction is authorized."""

    state: Literal[
        "needs_supplement",
        "pending_candidate_extraction",
        "artifact_extraction_unavailable",
    ]
    next_action: Literal[
        "supplement_text", "extract_candidates", "view_saved_snapshot"
    ]
    blocking_reason: str | None = None


@dataclass(frozen=True)
class CreatedReportResearch:
    case: ResearchCase
    document: DocumentVersion
    input_kind: str
    publisher: str | None
    outcome: ReportIntakeOutcome
    initial_scope_version: int | None
    source_statement_ids: list
    recovery_snapshot: RecoverySupplementSnapshot | None = None

    @property
    def state(self) -> str:
        """Compatibility accessor for callers not yet migrated to outcome."""
        return self.outcome.state


def _before_document_blob_insert() -> None:
    """Test seam for the concurrent immutable-blob reference race."""


def _before_report_scope_append_lock() -> None:
    """Test seam before report scope writers contend on the case root lock."""


class ReportResearchService:
    """Freeze report bytes/text before any later AI extraction can inspect them.

    A failed PDF parse is a normal, recoverable intake state: its original
    bytes and failure marker remain attached to the
    newly-created case, so a researcher can paste text or select pages later.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentService(DocumentRepository(session))
        self._research = ResearchService(ResearchRepository(session))
        self._contracts = SourceContractRepository(session)
        self._blobs = LocalImmutableBlobStore()

    def _require_active_source_contract(
        self, source_contract_id: uuid.UUID
    ) -> SourceContractVersion:
        contract = self._session.get(SourceContractVersion, source_contract_id)
        if contract is None:
            raise ValueError("source contract not found")
        require_ai_processing(contract)
        return contract

    def _admit_initial_document(
        self,
        *,
        document: DocumentVersion,
        contract: SourceContractVersion,
        admission_type: str,
        source_actor: str,
        acquisition_request: dict,
    ) -> DocumentSourceRecord:
        return self._contracts.ensure_document_source_record(
            document_version_id=document.id,
            source_contract_version_id=contract.id,
            admission_type=admission_type,
            tenant_id=contract.tenant_id,
            source_actor=source_actor,
            provider_record_id=None,
            verification_state="verified",
            acquisition_request=acquisition_request,
            created_at=_utcnow(),
        )

    def _admit_initial_case_source(
        self,
        *,
        case: ResearchCase,
        document: DocumentVersion,
        contract: SourceContractVersion,
        admission_type: str,
        source_actor: str,
        acquisition_request: dict,
    ) -> None:
        record = self._admit_initial_document(
            document=document,
            contract=contract,
            admission_type=admission_type,
            source_actor=source_actor,
            acquisition_request=acquisition_request,
        )
        self._contracts.add_report_case_initial_admission(
            research_case_id=case.id,
            document_source_record_id=record.id,
            tenant_id=contract.tenant_id,
            created_at=_utcnow(),
        )

    def create_text(
        self, request: CreateReportResearchRequest
    ) -> CreatedReportResearch:
        contract = self._require_active_source_contract(request.source_contract_id)
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
        self._admit_initial_case_source(
            case=case,
            document=document,
            contract=contract,
            admission_type={
                "pasted_text": "pasted_snapshot",
                "web_content": "public_url",
            }[request.input_kind],
            source_actor=request.created_by,
            acquisition_request={"source_url": source_url, "input_kind": request.input_kind},
        )
        self._append_text_span(
            research_case_id=case.id,
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
            outcome=ReportIntakeOutcome(
                state="pending_candidate_extraction",
                next_action="extract_candidates",
            ),
            initial_scope_version=None,
            source_statement_ids=[],
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
        source_contract_id: uuid.UUID,
    ) -> CreatedReportResearch:
        if not raw:
            raise ValueError("PDF upload must not be empty")
        contract = self._require_active_source_contract(source_contract_id)
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
                contract=contract,
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
        self._admit_initial_case_source(
            case=case,
            document=document,
            contract=contract,
            admission_type="uploaded_file",
            source_actor=created_by,
            acquisition_request={"filename": filename, "input_kind": "pdf_upload"},
        )
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
            self._select_span_for_case(case.id, document.id, span.id)
        self._session.commit()
        return CreatedReportResearch(
            case=case,
            document=document,
            input_kind="pdf_upload",
            publisher=normalized_publisher,
            outcome=ReportIntakeOutcome(
                state="pending_candidate_extraction",
                next_action="extract_candidates",
            ),
            initial_scope_version=None,
            source_statement_ids=[],
        )

    def supplement_text(
        self,
        *,
        research_case_id: uuid.UUID,
        document_version_id: uuid.UUID,
        content: str,
        page_reference: str | None,
        created_by: str,
        source_contract_id: uuid.UUID,
    ) -> CreatedReportResearch:
        """Continue an unresolved intake without replacing its frozen source.

        The original document bytes/text remain immutable.  A researcher-supplied
        readable fragment is an additional source span attached to that exact
        case/document pair, so its locator preserves both the recovery actor
        and any page reference.
        """
        case = lock_event_scope_case(self._session, research_case_id)
        if case is None:
            raise ValueError("report research case not found")
        document = self._session.get(DocumentVersion, document_version_id)
        attached = self._session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == research_case_id,
                CaseDocumentVersion.document_version_id == document_version_id,
            )
        )
        if document is None or attached is None:
            raise ValueError("report document must belong to its research case")
        source_kind, publisher = self._original_document_metadata(
            research_case_id, document_version_id
        )
        contract = self._require_active_source_contract(source_contract_id)
        snapshot = RecoverySupplementSnapshotService(self._session).admit(
            research_case_id=research_case_id,
            original_document_version_id=document_version_id,
            content=content,
            claimed_page_reference=page_reference,
            created_by=created_by,
            tenant_id=contract.tenant_id,
            supplement_contract_version_id=contract.id,
        )
        if snapshot.document is not None and snapshot.span is not None:
            self._select_span_for_case(case.id, snapshot.document.id, snapshot.span.id)
        return CreatedReportResearch(
            case=case,
            document=snapshot.document or document,
            input_kind="recovery_text" if snapshot.document is not None else source_kind,
            publisher=publisher,
            outcome=(
                ReportIntakeOutcome(
                    state="artifact_extraction_unavailable",
                    next_action="view_saved_snapshot",
                    blocking_reason=(
                        "recovery snapshot is saved case-locally because identical bytes "
                        "belong to another immutable document; it awaits the controlled "
                        "candidate adapter before candidate extraction can run"
                    ),
                )
                if snapshot.artifact is not None
                else ReportIntakeOutcome(
                    state="pending_candidate_extraction",
                    next_action="extract_candidates",
                )
            ),
            initial_scope_version=None,
            source_statement_ids=[],
            recovery_snapshot=snapshot.artifact,
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
        contract: SourceContractVersion,
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
        self._admit_initial_case_source(
            case=case,
            document=document,
            contract=contract,
            admission_type="uploaded_file",
            source_actor=created_by,
            acquisition_request={"filename": filename, "input_kind": "pdf_upload"},
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
        self._select_span_for_case(case.id, document.id, span.id)
        self._session.commit()
        return CreatedReportResearch(
            case=case,
            document=document,
            input_kind="pdf_upload",
            publisher=publisher,
            outcome=ReportIntakeOutcome(
                state="needs_supplement",
                next_action="supplement_text",
                blocking_reason=f"PDF text extraction failed: {type(parse_error).__name__}",
            ),
            initial_scope_version=None,
            source_statement_ids=[],
        )

    def _append_text_span(
        self,
        *,
        research_case_id: uuid.UUID,
        document: DocumentVersion,
        content: str,
        input_kind: str,
        publisher: str | None,
        locator_extra: dict[str, object] | None = None,
    ) -> SourceSpan:
        locator: dict[str, object] = {
            "input_kind": input_kind,
            "publisher": publisher,
            "published_at": self._iso_published_at(document.published_at),
            "paragraph": 1,
            "parser": "user-pasted-report-v1",
        }
        if locator_extra:
            locator.update(locator_extra)
        span = self._documents.add_span(
            document_version_id=document.id,
            locator=locator,
            verbatim_text=content,
        )
        self._select_span_for_case(research_case_id, document.id, span.id)
        return span

    def _original_document_metadata(
        self, research_case_id: uuid.UUID, document_version_id: uuid.UUID
    ) -> tuple[str, str | None]:
        """Recover display metadata from the first frozen source span only."""
        span = self._session.scalar(
            select(SourceSpan)
            .join(
                ReportCaseSourceSpan,
                ReportCaseSourceSpan.source_span_id == SourceSpan.id,
            )
            .where(ReportCaseSourceSpan.research_case_id == research_case_id)
            .where(ReportCaseSourceSpan.document_version_id == document_version_id)
            .order_by(ReportCaseSourceSpan.created_at, SourceSpan.id)
            .limit(1)
        )
        locator = span.locator if span is not None else {}
        input_kind = locator.get("input_kind")
        if input_kind not in {"pdf_upload", "pasted_text", "web_content"}:
            raise ValueError("report document has no frozen intake source")
        publisher = locator.get("publisher")
        return input_kind, publisher if isinstance(publisher, str) else None

    def _select_span_for_case(
        self,
        research_case_id: uuid.UUID,
        document_version_id: uuid.UUID,
        source_span_id: uuid.UUID,
    ) -> None:
        self._session.add(
            ReportCaseSourceSpan(
                research_case_id=research_case_id,
                document_version_id=document_version_id,
                source_span_id=source_span_id,
            )
        )
        self._session.flush()

    def _create_initial_scope(
        self,
        research_case_id: uuid.UUID,
        document_version_id: uuid.UUID,
        changed_by: str,
        research_question: str | None,
        *,
        change_summary: str = "初始研报研究范围",
    ) -> ReportResearchScopeVersion | None:
        """Append the first explicit report-research scope for a new case."""
        selected_claim_ids, selected_relation_ids = self._paths_for_document(
            research_case_id, document_version_id
        )
        if not selected_claim_ids:
            # A report with no extracted claim is a valid intake outcome, but
            # not yet a research scope.  Creating an empty scope would make
            # its selection semantics ambiguous; later extraction must append
            # a first scope with explicit paths instead.
            return None
        selected_claim_ids, selected_relation_ids = self._validate_scope_selection(
            research_case_id,
            document_version_id,
            selected_claim_ids=selected_claim_ids,
            selected_relation_ids=selected_relation_ids,
        )
        created_at = _utcnow()
        scope = ReportResearchScopeVersion(
            research_case_id=research_case_id,
            document_version_id=document_version_id,
            version=1,
            changed_by=changed_by,
            change_summary=change_summary,
            research_question=(research_question or "验证研报观点与市场影响。").strip(),
            factor_selection=[],
            evidence_plan=[],
            # New scopes use their exact creation time as their historical
            # evidence horizon; a later disclosure needs a successor scope.
            visibility_cutoff_at=created_at,
            created_at=created_at,
        )
        self._session.add(scope)
        self._session.flush()
        self._append_scope_selections(
            scope,
            selected_claim_ids=selected_claim_ids,
            selected_relation_ids=selected_relation_ids,
        )
        return scope

    def append_scope(
        self,
        research_case_id: uuid.UUID,
        document_version_id: uuid.UUID,
        *,
        changed_by: str,
        change_summary: str,
        research_question: str | None = None,
        factor_selection: Sequence[str] | None = None,
        evidence_plan: Sequence[str] | None = None,
        selected_claim_ids: Sequence[uuid.UUID],
        selected_relation_ids: Sequence[uuid.UUID] = (),
    ) -> ReportResearchScopeVersion:
        """Select an attached report revision as a new immutable scope.

        This is the single write seam for future UI/API scope switching.  It
        deliberately appends a version rather than changing a document tag,
        so historical Wiki and factor results remain independently readable.
        """
        if not changed_by.strip() or not change_summary.strip():
            raise ValueError("report scope changed_by and change_summary must not be blank")
        # Scope versions use the same stable root lock as event scope changes.
        # It must be acquired before reading the latest version, otherwise two
        # PostgreSQL sessions can both append the same successor number.
        _before_report_scope_append_lock()
        if lock_event_scope_case(self._session, research_case_id) is None:
            raise ValueError("report research case not found")
        latest = self._session.scalar(
            select(ReportResearchScopeVersion)
            .where(ReportResearchScopeVersion.research_case_id == research_case_id)
            .order_by(ReportResearchScopeVersion.version.desc())
            .limit(1)
        )
        selected_question = (
            research_question
            or (latest.research_question if latest is not None else "验证研报观点与市场影响。")
        ).strip()
        if not selected_question:
            raise ValueError("report scope research_question must not be blank")
        selected_claim_ids, selected_relation_ids = self._validate_scope_selection(
            research_case_id,
            document_version_id,
            selected_claim_ids=selected_claim_ids,
            selected_relation_ids=selected_relation_ids,
        )
        created_at = _utcnow()
        scope = ReportResearchScopeVersion(
            research_case_id=research_case_id,
            document_version_id=document_version_id,
            version=1 if latest is None else latest.version + 1,
            changed_by=changed_by.strip(),
            change_summary=change_summary.strip(),
            research_question=selected_question,
            factor_selection=list(
                latest.factor_selection
                if factor_selection is None and latest is not None
                else factor_selection or []
            ),
            evidence_plan=list(
                latest.evidence_plan
                if evidence_plan is None and latest is not None
                else evidence_plan or []
            ),
            visibility_cutoff_at=created_at,
            created_at=created_at,
        )
        self._session.add(scope)
        self._session.flush()
        self._append_scope_selections(
            scope,
            selected_claim_ids=selected_claim_ids,
            selected_relation_ids=selected_relation_ids,
        )
        return scope

    def list_scope_selections(
        self, research_case_id: uuid.UUID
    ) -> list[ReportResearchScopeSelection]:
        """Read all immutable scopes for one report case in version order.

        This command/read seam deliberately does not fall back to document
        history.  A report may have been frozen but not yet yielded a claim,
        in which case callers receive an empty scope list rather than a
        fabricated selection.
        """
        if self._session.get(ResearchCase, research_case_id) is None:
            raise ValueError("report research case not found")
        scopes = list(
            self._session.scalars(
                select(ReportResearchScopeVersion)
                .where(ReportResearchScopeVersion.research_case_id == research_case_id)
                .order_by(ReportResearchScopeVersion.version)
            )
        )
        if not scopes:
            return []
        scope_ids = [scope.id for scope in scopes]
        claims_by_scope: dict[uuid.UUID, list[uuid.UUID]] = {scope_id: [] for scope_id in scope_ids}
        relations_by_scope: dict[uuid.UUID, list[uuid.UUID]] = {scope_id: [] for scope_id in scope_ids}
        for scope_id, claim_id in self._session.execute(
            select(
                ReportResearchScopeClaim.scope_version_id,
                ReportResearchScopeClaim.report_claim_id,
            )
            .where(ReportResearchScopeClaim.scope_version_id.in_(scope_ids))
            .order_by(ReportResearchScopeClaim.created_at, ReportResearchScopeClaim.id)
        ):
            claims_by_scope[scope_id].append(claim_id)
        for scope_id, relation_id in self._session.execute(
            select(
                ReportResearchScopeRelation.scope_version_id,
                ReportResearchScopeRelation.report_relation_id,
            )
            .where(ReportResearchScopeRelation.scope_version_id.in_(scope_ids))
            .order_by(ReportResearchScopeRelation.created_at, ReportResearchScopeRelation.id)
        ):
            relations_by_scope[scope_id].append(relation_id)
        return [
            ReportResearchScopeSelection(
                scope=scope,
                selected_claim_ids=claims_by_scope[scope.id],
                selected_relation_ids=relations_by_scope[scope.id],
            )
            for scope in scopes
        ]

    def _paths_for_document(
        self, research_case_id: uuid.UUID, document_version_id: uuid.UUID
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        """Return every extracted path for the system-created initial scope."""
        claim_ids = list(
            self._session.scalars(
                select(ReportClaim.id)
                .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
                .where(ReportClaim.research_case_id == research_case_id)
                .where(SourceSpan.document_version_id == document_version_id)
                .order_by(ReportClaim.created_at, ReportClaim.id)
            )
        )
        if not claim_ids:
            return [], []
        relation_ids = list(
            self._session.scalars(
                select(ReportRelation.id)
                .where(ReportRelation.research_case_id == research_case_id)
                .where(ReportRelation.claim_id.in_(claim_ids))
                .order_by(ReportRelation.created_at, ReportRelation.id)
            )
        )
        return claim_ids, relation_ids

    def _append_scope_selections(
        self,
        scope: ReportResearchScopeVersion,
        *,
        selected_claim_ids: Sequence[uuid.UUID],
        selected_relation_ids: Sequence[uuid.UUID],
    ) -> None:
        """Persist a selection that was validated before the scope was inserted."""
        self._session.add_all(
            ReportResearchScopeClaim(
                scope_version_id=scope.id,
                report_claim_id=claim_id,
            )
            for claim_id in selected_claim_ids
        )
        self._session.add_all(
            ReportResearchScopeRelation(
                scope_version_id=scope.id,
                report_relation_id=relation_id,
            )
            for relation_id in selected_relation_ids
        )
        self._session.flush()

    def _validate_scope_selection(
        self,
        research_case_id: uuid.UUID,
        document_version_id: uuid.UUID,
        *,
        selected_claim_ids: Sequence[uuid.UUID],
        selected_relation_ids: Sequence[uuid.UUID],
    ) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
        """Validate a complete selection before a new immutable scope exists."""
        claim_ids = list(dict.fromkeys(selected_claim_ids))
        relation_ids = list(dict.fromkeys(selected_relation_ids))
        if not claim_ids:
            raise ValueError("report scope requires at least one selected claim")
        claims = {
            claim.id: claim
            for claim in self._session.scalars(
                select(ReportClaim)
                .join(SourceSpan, SourceSpan.id == ReportClaim.source_span_id)
                .where(ReportClaim.id.in_(claim_ids))
                .where(ReportClaim.research_case_id == research_case_id)
                .where(SourceSpan.document_version_id == document_version_id)
            )
        }
        if set(claim_ids) != set(claims):
            raise ValueError("report scope selected claims must belong to its case and document")
        relations = {
            relation.id: relation
            for relation in self._session.scalars(
                select(ReportRelation)
                .where(ReportRelation.id.in_(relation_ids))
                .where(ReportRelation.research_case_id == research_case_id)
            )
        } if relation_ids else {}
        if set(relation_ids) != set(relations) or any(
            relation.claim_id not in claims for relation in relations.values()
        ):
            raise ValueError(
                "report scope selected relations must belong to its selected claims, case and document"
            )
        claims_with_paths = set(
            self._session.scalars(
                select(ReportRelation.claim_id)
                .where(ReportRelation.research_case_id == research_case_id)
                .where(ReportRelation.claim_id.in_(claim_ids))
            )
        )
        selected_path_claims = {relation.claim_id for relation in relations.values()}
        if claims_with_paths - selected_path_claims:
            raise ValueError(
                "report scope must select at least one relation for every selected claim with paths"
            )
        return claim_ids, relation_ids

    def _extract_claim_statement_ids(
        self, research_case_id: uuid.UUID, *, input_fingerprint: str | None = None
    ) -> list:
        """Run the default report parser immediately after source intake.

        The input and every original span are already frozen at this point.
        Extraction appends its own narrow SourceStatements and cannot mutate
        either original text or page/paragraph locators.
        """
        claims = ReportClaimExtractor(self._session).extract(
            research_case_id, input_fingerprint=input_fingerprint
        )
        return [claim.source_statement_id for claim in claims]

    def _schedule_market_impact(self, research_case_id: uuid.UUID) -> None:
        """Queue report market collection after immutable claim extraction.

        The worker owns execution; intake only writes a durable run/job/task
        handoff.  A claim without mapped China assets still runs and appends a
        visible evidence gap instead of silently disappearing.
        """
        claims = list(
            self._session.scalars(
                select(ReportClaim).where(ReportClaim.research_case_id == research_case_id)
            )
        )
        if not claims:
            return
        repository = AutoResearchRepository(self._session)
        run = repository.create_run(
            research_case_id=research_case_id,
            max_rounds=1,
            budget=max(1, len(claims)),
            scope_thesis_ids=[],
        )
        for claim in claims:
            repository.create_task(
                run_id=run.id,
                research_case_id=research_case_id,
                thesis_id=None,
                task_type="report_market_impact",
                query=f"report_market_impact:{claim.id}",
            )
        repository.enqueue_run_job(run)

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
