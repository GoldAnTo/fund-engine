"""One product progress projection shared by workspace and compact status reads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError

from app.underwriting.domain.company_research import company_research_product_status
from app.underwriting.services.company_research_foundation import (
    build_alphabet_company_research_preview_at_cutoff,
    company_research_scope_focus,
)
from app.underwriting.services.company_research_boundary import resolve_alphabet_company_research_boundary
from app.underwriting.services.product_project import research_scope_content_hash
from app.underwriting.persistence.company_research_repository import CompanyResearchRepository
from app.underwriting.persistence.company_research_models import CompanyResearchPreparation
from app.underwriting.persistence.models import UnderwritingHistoricalBasis, UnderwritingMandateVersion
from app.underwriting.persistence.product_models import UnderwritingResearchScopeVersion


@dataclass(frozen=True, slots=True)
class CompanyResearchProductProgress:
    run_id: UUID
    project_id: UUID
    company_id: UUID
    status: str
    current_step: str | None
    progress_percent: int
    user_focus: str | None
    cutoff_at: datetime
    retryable: bool
    error_code: str | None


def company_research_product_progress(
    *,
    session: Session,
    preparation: CompanyResearchPreparation,
    company_id: UUID,
    scope: UnderwritingResearchScopeVersion | None,
    basis: UnderwritingHistoricalBasis | None,
) -> CompanyResearchProductProgress | None:
    # Old incomplete foundations remain readable through their existing error
    # projection. An unknown cutoff is never replaced by a fixture or wall clock.
    if scope is None or basis is None:
        return None
    focus = company_research_scope_focus(scope)
    cutoff = basis.cutoff.replace(tzinfo=UTC) if basis.cutoff.tzinfo is None else basis.cutoff.astimezone(UTC)
    if scope.project_id != preparation.project_id or scope.content_hash != research_scope_content_hash(
        project_id=preparation.project_id, payload=scope.payload,
    ):
        raise ValidationError("company research run scope is invalid")
    boundary = resolve_alphabet_company_research_boundary(cutoff)
    CompanyResearchRepository(session).authenticate_governed_historical_basis(
        basis, expected_input=boundary.basis_input, expected_content_hash=boundary.basis_content_hash,
    )
    preview = build_alphabet_company_research_preview_at_cutoff(
        session, company_id=company_id, cutoff_at=cutoff, user_focus=focus,
    )
    if preview.input_hash != preparation.request_hash:
        # Older initializations hashed their original requested cutoff. Use the
        # persisted first mandate time only to authenticate that legacy request.
        mandate = session.scalar(select(UnderwritingMandateVersion).where(
            UnderwritingMandateVersion.mandate_key == f"product.project:{preparation.project_id}",
            UnderwritingMandateVersion.version == 1,
        ).limit(1))
        if mandate is not None:
            legacy_cutoff = (mandate.effective_at.replace(tzinfo=UTC)
                             if mandate.effective_at.tzinfo is None else mandate.effective_at.astimezone(UTC))
            preview = build_alphabet_company_research_preview_at_cutoff(
                session, company_id=company_id, cutoff_at=legacy_cutoff, user_focus=focus,
            )
        if preview.input_hash != preparation.request_hash:
            raise ValidationError("company research run context does not match initialized request")
    return CompanyResearchProductProgress(
        run_id=preparation.id,
        project_id=preparation.project_id,
        company_id=company_id,
        status=company_research_product_status(preparation.status, preparation.current_step),
        current_step=preparation.current_step,
        progress_percent=preparation.progress,
        user_focus=focus,
        cutoff_at=cutoff,
        retryable=preparation.status == "recoverable_failure",
        error_code=preparation.last_error_code,
    )
