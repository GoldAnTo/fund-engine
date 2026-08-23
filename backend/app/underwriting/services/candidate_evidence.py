"""Publication boundary for independently reviewed evidence candidates.

Candidates are useful research records, but they are never model inputs.  This
service is the only writer that turns a fully reviewed dossier into a research
revision, and that revision is deliberately answerability-only.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
)
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.research_models import (
    UnderwritingEvidenceCandidateDossierVersion,
    UnderwritingEvidenceCandidateReviewVersion,
)
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.kernel import UnderwritingKernelService


INDUSTRY_EVIDENCE_CANDIDATE_KIND = "industry_evidence_candidate"
_CANDIDATE_BLOCKER = BlockerCode.MECHANISM_UNIDENTIFIED


@dataclass(frozen=True, slots=True)
class CandidateEvidencePublication:
    """The deliberately non-promotable result of publishing a dossier."""

    research_version: UnderwritingResearchVersion
    dossier: UnderwritingEvidenceCandidateDossierVersion
    reviews: tuple[UnderwritingEvidenceCandidateReviewVersion, ...]
    answerability: UnderwritingAnswerabilityEvaluation
    formal_mechanism_ids: tuple[UUID, ...] = ()
    industry_state_id: UUID | None = None
    earnings_engine_id: UUID | None = None


class CandidateEvidenceService:
    """Append one candidate-only revision using the caller's transaction."""

    def __init__(self, session: Session, now: Callable[[], datetime] | None = None) -> None:
        self._session = session
        self._now = now
        self._repository = UnderwritingResearchRepository(session)

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _current_dossier(
        self, dossier_id: UUID,
    ) -> UnderwritingEvidenceCandidateDossierVersion:
        dossier = self._session.get(UnderwritingEvidenceCandidateDossierVersion, dossier_id)
        if dossier is None:
            raise ValidationError("candidate dossier does not exist")
        current = self._session.scalars(
            select(UnderwritingEvidenceCandidateDossierVersion)
            .where(
                UnderwritingEvidenceCandidateDossierVersion.object_id == dossier.object_id,
                UnderwritingEvidenceCandidateDossierVersion.basis_id == dossier.basis_id,
                UnderwritingEvidenceCandidateDossierVersion.dossier_key == dossier.dossier_key,
            )
            .order_by(
                UnderwritingEvidenceCandidateDossierVersion.version.desc(),
                UnderwritingEvidenceCandidateDossierVersion.id.desc(),
            )
            .limit(1)
        ).first()
        if current is None or current.id != dossier.id:
            raise ValidationError("candidate dossier is no longer current")
        return dossier

    def _approved_reviews(
        self, dossier: UnderwritingEvidenceCandidateDossierVersion,
    ) -> tuple[UnderwritingEvidenceCandidateReviewVersion, ...]:
        reviews = tuple(self._repository.effective_candidate_reviews(dossier.id))
        expected_roles = {"provenance", "methodology"}
        if (
            len(reviews) != 2
            or {review.reviewer_role for review in reviews} != expected_roles
            or any(review.decision != "approve" for review in reviews)
            or any(review.dossier_content_hash != dossier.content_hash for review in reviews)
            or len({review.reviewer_identity for review in reviews}) != 2
        ):
            raise ValidationError("candidate publication requires provenance and methodology approvals")
        return tuple(sorted(reviews, key=lambda review: review.reviewer_role))

    @staticmethod
    def _debt_key(dossier: UnderwritingEvidenceCandidateDossierVersion) -> str:
        return f"candidate_evidence:{dossier.id}"

    @staticmethod
    def _requirement(dossier: UnderwritingEvidenceCandidateDossierVersion) -> str:
        return f"Formalize reviewed candidate dossier {dossier.id} before model use"

    def _latest_answerability(
        self, dossier: UnderwritingEvidenceCandidateDossierVersion,
    ) -> UnderwritingAnswerabilityEvaluation | None:
        return self._session.scalars(
            select(UnderwritingAnswerabilityEvaluation)
            .where(
                UnderwritingAnswerabilityEvaluation.object_id == dossier.object_id,
                UnderwritingAnswerabilityEvaluation.basis_id == dossier.basis_id,
            )
            .order_by(
                UnderwritingAnswerabilityEvaluation.version.desc(),
                UnderwritingAnswerabilityEvaluation.id.desc(),
            )
            .limit(1)
        ).first()

    def _reusable_answerability(
        self, dossier: UnderwritingEvidenceCandidateDossierVersion,
    ) -> UnderwritingAnswerabilityEvaluation | None:
        current = self._latest_answerability(dossier)
        if current is None:
            return None
        if (
            current.state == AnswerabilityState.NOT_ANSWERABLE.value
            and current.blockers == [_CANDIDATE_BLOCKER.value]
            and current.research_debt_keys == [self._debt_key(dossier)]
            and current.resolvable_within_mandate is True
            and current.allowed_action == EligibleAction.WAIT_FOR_VALIDATION.value
            and current.resolution_requirements == [self._requirement(dossier)]
        ):
            return current
        return None

    def _record_not_answerable(
        self, dossier: UnderwritingEvidenceCandidateDossierVersion,
    ) -> UnderwritingAnswerabilityEvaluation:
        if reusable := self._reusable_answerability(dossier):
            return reusable
        timestamp = self._stored_utc(self._now()) if self._now is not None else self._stored_utc(dossier.created_at)
        latest = self._latest_answerability(dossier)
        kernel = UnderwritingKernelService(self._session, now=lambda: timestamp)
        return kernel.record_answerability(
            dossier.object_id,
            dossier.basis_id,
            (_CANDIDATE_BLOCKER,),
            (self._debt_key(dossier),),
            True,
            EligibleAction.WAIT_FOR_VALIDATION,
            (self._requirement(dossier),),
            latest.id if latest is not None else None,
            created_at=timestamp,
        )

    def _candidate_revision_parent(
        self, dossier: UnderwritingEvidenceCandidateDossierVersion,
    ) -> UUID | None:
        current = self._session.scalars(
            select(UnderwritingResearchVersion)
            .where(
                UnderwritingResearchVersion.object_id == dossier.object_id,
                UnderwritingResearchVersion.version_kind == INDUSTRY_EVIDENCE_CANDIDATE_KIND,
            )
            .order_by(UnderwritingResearchVersion.sequence.desc(), UnderwritingResearchVersion.id.desc())
            .limit(1)
        ).first()
        return current.id if current is not None else None

    def publish(self, dossier_id: UUID) -> CandidateEvidencePublication:
        """Publish only a sealed, non-answerable candidate revision; never commit."""
        with self._session.no_autoflush:
            dossier = self._current_dossier(dossier_id)
            reviews = self._approved_reviews(dossier)
        answerability = self._record_not_answerable(dossier)
        parents = [
            str(dossier.id),
            *(str(review.id) for review in reviews),
            str(dossier.source_manifest_id),
            str(answerability.id),
        ]
        revision = UnderwritingKernelService(
            self._session,
            now=lambda: self._stored_utc(dossier.created_at),
        ).publish_research_version(
            dossier.object_id,
            dossier.basis_id,
            INDUSTRY_EVIDENCE_CANDIDATE_KIND,
            parents,
            self._candidate_revision_parent(dossier),
        )
        return CandidateEvidencePublication(revision, dossier, reviews, answerability)
