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
from datetime import UTC, datetime

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
from app.schemas.v1.commands import ReviewQueueItemDTO, ReviewQueueResponse
from app.services.source_admission import (
    SourceAdmission,
    SourceStatus,
    classify_document_source,
    document_source_can_display,
)


@dataclass(frozen=True)
class ProposalEvidenceContext:
    """Resolved evidence provenance plus its source-admission decision."""

    proposal: Proposal
    thesis: Thesis | None
    statement: SourceStatement | None
    span: SourceSpan | None
    document: DocumentVersion | None
    admission: SourceAdmission
    can_display: bool


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
    evaluated_at = datetime.now(UTC)
    source_url = document.source_url if document else None
    parser_version = document.parser_version if document else None
    content_verified = bool(document and document.parse_state in {"success", "parsed"})
    admission = classify_document_source(
        source_url=source_url,
        parser_version=parser_version,
        content_verified=content_verified,
        contract=source_contract,
        at=evaluated_at,
    )
    can_display = document_source_can_display(
        source_url=source_url,
        parser_version=parser_version,
        content_verified=content_verified,
        contract=source_contract,
        at=evaluated_at,
    )
    return ProposalEvidenceContext(
        proposal=proposal,
        thesis=thesis,
        statement=statement,
        span=span,
        document=document,
        admission=admission,
        can_display=can_display,
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
        can_display=False,
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
            legacy_rows = list(
                self._legacy_links(case_id=case_id, limit=remaining)
            )
            document_ids = {version.id for *_, version in legacy_rows}
            contracts_by_document_id = (
                {
                    contract.document_version_id: contract
                    for contract in self._db.scalars(
                        select(SourceContract).where(
                            SourceContract.document_version_id.in_(document_ids)
                        )
                    )
                }
                if document_ids
                else {}
            )
            evaluated_at = datetime.now(UTC)
            for link, thesis, statement, span, version in legacy_rows:
                can_display = document_source_can_display(
                    source_url=version.source_url,
                    parser_version=version.parser_version,
                    content_verified=version.parse_state in {"success", "parsed"},
                    contract=contracts_by_document_id.get(version.id),
                    at=evaluated_at,
                )
                items.append(
                    ReviewQueueItemDTO(
                        link_id=str(link.id),
                        thesis_id=str(thesis.id),
                        case_id=str(thesis.research_case_id),
                        thesis_statement=thesis.statement if can_display else "",
                        ai_role=link.role,
                        ai_reason=link.reason if can_display else "",
                        ai_scope=link.scope if can_display else {},
                        statement_id=str(statement.id),
                        statement_text=(
                            statement.normalized_text if can_display else ""
                        ),
                        statement_kind=statement.kind,
                        span_id=str(span.id),
                        verbatim_text=span.verbatim_text if can_display else "",
                        locator=span.locator if can_display else {},
                        document_version_id=str(version.id),
                        document_source_url=(
                            version.source_url if can_display else ""
                        ),
                        document_published_at=(
                            version.published_at.isoformat()
                            if version.published_at and can_display
                            else None
                        ),
                        available_at=(
                            link.available_at.isoformat() if can_display else ""
                        ),
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
            ai_reason=payload.get("reason", "") if context.can_display else "",
            ai_scope=payload.get("scope", {}) if context.can_display else {},
            statement_id=str(statement.id),
            statement_text=(statement.normalized_text if context.can_display else ""),
            statement_kind=statement.kind,
            span_id=str(span.id) if span else "",
            verbatim_text=(
                span.verbatim_text if span and context.can_display else ""
            ),
            locator=span.locator if span and context.can_display else {},
            document_version_id=str(version.id) if version else "",
            document_source_url=(
                version.source_url if version and context.can_display else ""
            ),
            document_published_at=(
                version.published_at.isoformat()
                if version and version.published_at and context.can_display
                else None
            ),
            available_at=(
                proposal.basis_cutoff.isoformat()
                if proposal.basis_cutoff and context.can_display
                else ""
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
