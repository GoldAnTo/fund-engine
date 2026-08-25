"""Append-only repository operations for the underwriting kernel."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.underwriting.domain.types import HistoricalBasisInput
from app.underwriting.persistence.models import (
    UnderwritingAnswerabilityEvaluation,
    UnderwritingHistoricalBasis,
    UnderwritingLedgerEntry,
    UnderwritingMandateVersion,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)


class StaleParentError(ValueError):
    """Raised when a requested successor does not name the effective parent."""


class UnderwritingRepository:
    """Persist immutable underwriting records and append their successors."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_object(
        self,
        kind: str,
        external_key: str,
        canonical_name: str,
        created_at: datetime,
    ) -> UnderwritingResearchObject:
        row = UnderwritingResearchObject(
            kind=kind,
            external_key=external_key,
            canonical_name=canonical_name,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def object(self, object_id: uuid.UUID) -> UnderwritingResearchObject | None:
        return self._session.scalars(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id == object_id
            )
        ).first()

    def add_relation(
        self,
        parent_id: uuid.UUID,
        child_id: uuid.UUID,
        relation_type: str,
        created_at: datetime,
    ) -> UnderwritingObjectRelation:
        row = UnderwritingObjectRelation(
            parent_id=parent_id,
            child_id=child_id,
            relation_type=relation_type,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_basis(
        self,
        value: HistoricalBasisInput,
        created_at: datetime,
    ) -> UnderwritingHistoricalBasis:
        row = UnderwritingHistoricalBasis(
            cutoff=value.cutoff,
            price_as_of=value.price_as_of,
            source_manifest_hash=value.source_manifest_hash,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def basis(self, basis_id: uuid.UUID) -> UnderwritingHistoricalBasis | None:
        return self._session.scalars(
            select(UnderwritingHistoricalBasis).where(
                UnderwritingHistoricalBasis.id == basis_id
            )
        ).first()

    def effective_ledger_entry(
        self,
        object_id: uuid.UUID,
        ledger_kind: str,
        family_key: str,
    ) -> UnderwritingLedgerEntry | None:
        return self._session.scalars(
            select(UnderwritingLedgerEntry)
            .where(
                UnderwritingLedgerEntry.object_id == object_id,
                UnderwritingLedgerEntry.ledger_kind == ledger_kind,
                UnderwritingLedgerEntry.family_key == family_key,
            )
            .order_by(
                UnderwritingLedgerEntry.version.desc(),
                UnderwritingLedgerEntry.id.desc(),
            )
            .limit(1)
        ).first()

    @staticmethod
    def _require_expected_parent(
        current_id: uuid.UUID | None,
        expected_id: uuid.UUID | None,
    ) -> None:
        if current_id != expected_id:
            raise StaleParentError(
                "expected parent is not the effective family version"
            )

    def append_ledger_entry(
        self,
        *,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        ledger_kind: str,
        family_key: str,
        entry_type: str,
        payload: dict[str, Any],
        effective_at: datetime,
        available_at: datetime,
        source_boundary: str,
        content_hash: str,
        expected_parent_id: uuid.UUID | None,
        created_at: datetime,
    ) -> UnderwritingLedgerEntry:
        current = self.effective_ledger_entry(object_id, ledger_kind, family_key)
        self._require_expected_parent(
            current.id if current is not None else None,
            expected_parent_id,
        )
        row = UnderwritingLedgerEntry(
            object_id=object_id,
            basis_id=basis_id,
            ledger_kind=ledger_kind,
            family_key=family_key,
            entry_type=entry_type,
            version=current.version + 1 if current is not None else 1,
            payload=payload,
            effective_at=effective_at,
            available_at=available_at,
            source_boundary=source_boundary,
            content_hash=content_hash,
            supersedes_id=current.id if current is not None else None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def append_mandate_version(
        self,
        *,
        mandate_key: str,
        horizon_years: int,
        base_currency: str,
        required_return: Decimal,
        permanent_loss_limit: Decimal,
        comparison_set: list[str],
        expected_parent_id: uuid.UUID | None,
        created_at: datetime,
    ) -> UnderwritingMandateVersion:
        current = self._session.scalars(
            select(UnderwritingMandateVersion)
            .where(UnderwritingMandateVersion.mandate_key == mandate_key)
            .order_by(
                UnderwritingMandateVersion.version.desc(),
                UnderwritingMandateVersion.id.desc(),
            )
            .limit(1)
        ).first()
        self._require_expected_parent(
            current.id if current is not None else None,
            expected_parent_id,
        )
        row = UnderwritingMandateVersion(
            mandate_key=mandate_key,
            version=current.version + 1 if current is not None else 1,
            horizon_years=horizon_years,
            base_currency=base_currency,
            required_return=required_return,
            permanent_loss_limit=permanent_loss_limit,
            comparison_set=list(comparison_set),
            supersedes_id=current.id if current is not None else None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def append_research_version(
        self,
        *,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        version_kind: str,
        content_hash: str,
        parent_ids: list[str],
        expected_parent_id: uuid.UUID | None,
        created_at: datetime,
    ) -> UnderwritingResearchVersion:
        current = self._session.scalars(
            select(UnderwritingResearchVersion)
            .where(
                UnderwritingResearchVersion.object_id == object_id,
                UnderwritingResearchVersion.version_kind == version_kind,
            )
            .order_by(
                UnderwritingResearchVersion.sequence.desc(),
                UnderwritingResearchVersion.id.desc(),
            )
            .limit(1)
        ).first()
        self._require_expected_parent(
            current.id if current is not None else None,
            expected_parent_id,
        )
        row = UnderwritingResearchVersion(
            object_id=object_id,
            basis_id=basis_id,
            version_kind=version_kind,
            sequence=current.sequence + 1 if current is not None else 1,
            content_hash=content_hash,
            parent_ids=list(parent_ids),
            supersedes_id=current.id if current is not None else None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def append_answerability_evaluation(
        self,
        *,
        object_id: uuid.UUID,
        basis_id: uuid.UUID,
        state: str,
        blockers: list[str],
        research_debt_keys: list[str],
        resolvable_within_mandate: bool,
        allowed_action: str,
        resolution_requirements: list[str],
        expected_parent_id: uuid.UUID | None,
        created_at: datetime,
    ) -> UnderwritingAnswerabilityEvaluation:
        current = self._session.scalars(
            select(UnderwritingAnswerabilityEvaluation)
            .where(
                UnderwritingAnswerabilityEvaluation.object_id == object_id,
                UnderwritingAnswerabilityEvaluation.basis_id == basis_id,
            )
            .order_by(
                UnderwritingAnswerabilityEvaluation.version.desc(),
                UnderwritingAnswerabilityEvaluation.id.desc(),
            )
            .limit(1)
        ).first()
        self._require_expected_parent(
            current.id if current is not None else None,
            expected_parent_id,
        )
        row = UnderwritingAnswerabilityEvaluation(
            object_id=object_id,
            basis_id=basis_id,
            version=current.version + 1 if current is not None else 1,
            state=state,
            blockers=list(blockers),
            research_debt_keys=list(research_debt_keys),
            resolvable_within_mandate=resolvable_within_mandate,
            allowed_action=allowed_action,
            resolution_requirements=list(resolution_requirements),
            supersedes_id=current.id if current is not None else None,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def effective_entries_at(
        self,
        object_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[UnderwritingLedgerEntry]:
        rows = self._session.scalars(
            select(UnderwritingLedgerEntry)
            .where(
                UnderwritingLedgerEntry.object_id == object_id,
                UnderwritingLedgerEntry.available_at <= cutoff,
            )
            .order_by(
                UnderwritingLedgerEntry.ledger_kind,
                UnderwritingLedgerEntry.family_key,
                UnderwritingLedgerEntry.version,
                UnderwritingLedgerEntry.id,
            )
        )
        effective: dict[tuple[str, str], UnderwritingLedgerEntry] = {}
        for row in rows:
            effective[(row.ledger_kind, row.family_key)] = row
        return sorted(
            effective.values(),
            key=lambda row: (row.ledger_kind, row.family_key),
        )
