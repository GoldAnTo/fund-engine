"""Review-queue read model (unified Proposal queue, design §8.5).

Pending proposals of kind ``evidence_link`` are the primary review workload:
each carries the AI-suggested role/reason/scope plus the source statement and
its document locator so the reviewer sees exactly what the AI based the link
on.  A proposal enters the queue when created (status=pending) and leaves it
once a ``ProposalReviewDecision`` marks it decided.

The legacy ``EvidenceReview``-based queue (machine_generated EvidenceLinks
without going through Proposals) is retained under ``include_legacy=True`` so
the prototype review workspace keeps working during the transition; new AI
output flows through Proposals only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.proposals import Proposal
from app.models.source_governance import SourceContract
from app.services.source_admission import (
    SourceAdmission,
    SourceStatus,
    classify_document_source,
)
from app.schemas.v1.commands import ReviewQueueItemDTO, ReviewQueueResponse


@dataclass(frozen=True)
class ProposalEvidenceContext:
    """Resolved evidence provenance plus its source-admission decision."""

    proposal: Proposal
    thesis: Thesis | None
    statement: SourceStatement | None
    span: SourceSpan | None
    document: DocumentVersion | None
    admission: SourceAdmission


def proposal_evidence_context(
    session: Session,
    proposal: Proposal,
    *,
    evidence_payload: dict | None = None,
) -> ProposalEvidenceContext:
    """Resolve a proposal's frozen source and classify it without network I/O."""
    payload = (
        evidence_payload
        if isinstance(evidence_payload, dict)
        else (proposal.payload if isinstance(proposal.payload, dict) else {})
    )
    target_context = (
        proposal.target_context if isinstance(proposal.target_context, dict) else {}
    )
    statement = _get_by_uuid(
        session, SourceStatement, payload.get("source_statement_id")
    )
    thesis = _get_by_uuid(session, Thesis, target_context.get("thesis_id"))
    if (
        proposal.research_case_id is not None
        and (thesis is None or thesis.research_case_id != proposal.research_case_id)
    ):
        return _invalid_evidence_context(
            proposal,
            "证据目标命题未绑定到当前研究事件，不能作为有效证据来源。",
        )
    span = session.get(SourceSpan, statement.source_span_id) if statement else None
    document = (
        session.get(DocumentVersion, span.document_version_id) if span else None
    )
    if (
        document is not None
        and proposal.research_case_id is not None
        and session.scalar(
            select(CaseDocumentVersion.id)
            .where(CaseDocumentVersion.research_case_id == proposal.research_case_id)
            .where(CaseDocumentVersion.document_version_id == document.id)
            .limit(1)
        )
        is None
    ):
        return _invalid_evidence_context(
            proposal,
            "来源文档未绑定到当前研究事件，不能作为有效证据来源。",
        )
    source_contract = (
        session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == document.id
            )
        )
        if document is not None
        else None
    )
    admission = classify_document_source(
        source_url=document.source_url if document else None,
        parser_version=document.parser_version if document else "",
        content_verified=bool(document and document.parse_state in {"success", "parsed"}),
        contract=source_contract,
    )
    return ProposalEvidenceContext(
        proposal=proposal,
        thesis=thesis,
        statement=statement,
        span=span,
        document=document,
        admission=admission,
    )


def _invalid_evidence_context(
    proposal: Proposal, reason: str
) -> ProposalEvidenceContext:
    return ProposalEvidenceContext(
        proposal=proposal,
        thesis=None,
        statement=None,
        span=None,
        document=None,
        admission=SourceAdmission(SourceStatus.INVALID, reason, False),
    )


def _get_by_uuid(session: Session, model, raw_id: object):
    try:
        return session.get(model, uuid.UUID(str(raw_id)))
    except (TypeError, ValueError, AttributeError):
        return None


class ReviewQueueQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_items(
        self,
        *,
        case_id: uuid.UUID | None = None,
        kind: str | None = None,
        limit: int = 50,
        include_legacy: bool = True,
    ) -> ReviewQueueResponse:
        items: list[ReviewQueueItemDTO] = []

        # --- Unified proposal queue (primary) ---
        proposals = self._pending_proposals(case_id=case_id, kind=kind, limit=limit)
        for proposal in proposals:
            dto = self._proposal_item(proposal)
            if dto is not None:
                items.append(dto)
            if len(items) >= limit:
                return ReviewQueueResponse(items=items[:limit])

        # --- Legacy machine_generated link queue (transition) ---
        if include_legacy and len(items) < limit:
            remaining = limit - len(items)
            for link, thesis, statement, span, version in self._legacy_links(
                case_id=case_id, limit=remaining
            ):
                items.append(
                    ReviewQueueItemDTO(
                        link_id=str(link.id),
                        thesis_id=str(thesis.id),
                        case_id=str(thesis.research_case_id),
                        thesis_statement=thesis.statement,
                        ai_role=link.role,
                        ai_reason=link.reason,
                        ai_scope=link.scope,
                        statement_id=str(statement.id),
                        statement_text=statement.normalized_text,
                        statement_kind=statement.kind,
                        span_id=str(span.id),
                        verbatim_text=span.verbatim_text,
                        locator=span.locator,
                        document_version_id=str(version.id),
                        document_source_url=version.source_url,
                        document_published_at=(
                            version.published_at.isoformat()
                            if version.published_at
                            else None
                        ),
                        available_at=link.available_at.isoformat(),
                    )
                )
        return ReviewQueueResponse(items=items)

    def _pending_proposals(
        self, *, case_id: uuid.UUID | None, kind: str | None, limit: int
    ) -> list[Proposal]:
        query = select(Proposal).where(Proposal.status == "pending")
        if case_id is not None:
            query = query.where(Proposal.research_case_id == case_id)
        if kind is not None:
            query = query.where(Proposal.kind == kind)
        query = query.order_by(Proposal.proposed_at).limit(limit)
        return list(self._db.scalars(query))

    def _proposal_item(self, proposal: Proposal) -> ReviewQueueItemDTO | None:
        context = proposal_evidence_context(self._db, proposal)
        statement, thesis, span, version = (
            context.statement,
            context.thesis,
            context.span,
            context.document,
        )
        if statement is None or thesis is None:
            return None
        payload = proposal.payload
        return ReviewQueueItemDTO(
            link_id=str(proposal.id),
            thesis_id=str(thesis.id),
            case_id=str(thesis.research_case_id),
            thesis_statement=thesis.statement,
            ai_role=payload.get("role", ""),
            ai_reason=payload.get("reason", ""),
            ai_scope=payload.get("scope", {}),
            statement_id=str(statement.id),
            statement_text=statement.normalized_text,
            statement_kind=statement.kind,
            span_id=str(span.id) if span else "",
            verbatim_text=span.verbatim_text if span else "",
            locator=span.locator if span else {},
            document_version_id=str(version.id) if version else "",
            document_source_url=version.source_url if version else "",
            document_published_at=(
                version.published_at.isoformat() if version and version.published_at else None
            ),
            available_at=(
                proposal.basis_cutoff.isoformat() if proposal.basis_cutoff else ""
            ),
        )

    def _legacy_links(self, *, case_id: uuid.UUID | None, limit: int):
        reviewed = select(EvidenceReview.evidence_link_id)
        query = (
            select(EvidenceLink, Thesis, SourceStatement, SourceSpan, DocumentVersion)
            .join(Thesis, EvidenceLink.thesis_id == Thesis.id)
            .join(
                SourceStatement,
                EvidenceLink.source_statement_id == SourceStatement.id,
            )
            .join(SourceSpan, SourceStatement.source_span_id == SourceSpan.id)
            .join(
                DocumentVersion,
                SourceSpan.document_version_id == DocumentVersion.id,
            )
            .where(EvidenceLink.creator_type == "ai")
            .where(EvidenceLink.review_state == "machine_generated")
            .where(EvidenceLink.id.not_in(reviewed))
            .order_by(EvidenceLink.created_at)
            .limit(limit)
        )
        if case_id is not None:
            query = query.where(Thesis.research_case_id == case_id)
        return self._db.execute(query)
