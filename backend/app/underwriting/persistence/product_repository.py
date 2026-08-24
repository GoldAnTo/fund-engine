"""Focused persistence operations for investment-research product projects."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingObjectIdentityVersion,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
)
from app.underwriting.persistence.repository import StaleParentError


_STALE_MESSAGE = "expected parent is not the version family head"


class ProductRepository:
    """Flush-only persistence primitives for product project orchestration."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _require_expected_parent(
        actual_parent_id: UUID | None,
        expected_parent_id: UUID | None,
    ) -> None:
        if actual_parent_id != expected_parent_id:
            raise StaleParentError(_STALE_MESSAGE)

    def _flush_version(self, row: Any) -> Any:
        """Flush a CAS append and translate the DB uniqueness race defense."""
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise StaleParentError(_STALE_MESSAGE) from exc
        return row

    def object(self, object_id: UUID) -> UnderwritingResearchObject | None:
        return self._session.get(UnderwritingResearchObject, object_id)

    def relation_exists(
        self,
        *,
        parent_id: UUID,
        child_id: UUID,
        relation_type: str,
    ) -> bool:
        return (
            self._session.scalar(
                select(UnderwritingObjectRelation.id)
                .where(
                    UnderwritingObjectRelation.parent_id == parent_id,
                    UnderwritingObjectRelation.child_id == child_id,
                    UnderwritingObjectRelation.relation_type == relation_type,
                )
                .limit(1)
            )
            is not None
        )

    def identity_head(
        self, object_id: UUID
    ) -> UnderwritingObjectIdentityVersion | None:
        return self._session.scalar(
            select(UnderwritingObjectIdentityVersion)
            .where(UnderwritingObjectIdentityVersion.object_id == object_id)
            .order_by(
                UnderwritingObjectIdentityVersion.version.desc(),
                UnderwritingObjectIdentityVersion.id.desc(),
            )
            .limit(1)
        )

    def append_identity_version(
        self,
        *,
        object_id: UUID,
        canonical_name: str,
        symbol: str | None,
        exchange: str | None,
        share_class: str | None,
        trading_currency: str | None,
        effective_from: datetime,
        effective_to: datetime | None,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingObjectIdentityVersion:
        head = self._session.scalar(
            select(UnderwritingObjectIdentityVersion)
            .where(UnderwritingObjectIdentityVersion.object_id == object_id)
            .order_by(
                UnderwritingObjectIdentityVersion.version.desc(),
                UnderwritingObjectIdentityVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        return self._flush_version(
            UnderwritingObjectIdentityVersion(
                object_id=object_id,
                version=1 if head is None else head.version + 1,
                canonical_name=canonical_name,
                symbol=symbol,
                exchange=exchange,
                share_class=share_class,
                trading_currency=trading_currency,
                effective_from=effective_from,
                effective_to=effective_to,
                supersedes_id=actual_parent_id,
                content_hash=content_hash,
                created_at=created_at,
            )
        )

    def effective_identity(
        self,
        object_id: UUID,
        as_of: datetime,
    ) -> UnderwritingObjectIdentityVersion | None:
        candidate = aliased(UnderwritingObjectIdentityVersion)
        latest_started_id = (
            select(candidate.id)
            .where(
                candidate.object_id == object_id,
                candidate.effective_from <= as_of,
            )
            .order_by(candidate.version.desc(), candidate.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        return self._session.scalar(
            select(UnderwritingObjectIdentityVersion)
            .where(
                UnderwritingObjectIdentityVersion.id == latest_started_id,
                or_(
                    UnderwritingObjectIdentityVersion.effective_to.is_(None),
                    UnderwritingObjectIdentityVersion.effective_to > as_of,
                ),
            )
            .limit(1)
        )

    def effective_object(
        self,
        object_id: UUID,
        as_of: datetime,
    ) -> tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion] | None:
        identity = self.effective_identity(object_id, as_of)
        research_object = self.object(object_id)
        if research_object is None or identity is None:
            return None
        return research_object, identity

    def search_objects(
        self,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> list[tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]]:
        pattern = query.casefold()
        candidate = aliased(UnderwritingObjectIdentityVersion)
        effective_identity_id = (
            select(candidate.id)
            .where(
                candidate.object_id == UnderwritingResearchObject.id,
                candidate.effective_from <= as_of,
            )
            .order_by(candidate.version.desc(), candidate.id.desc())
            .limit(1)
            .correlate(UnderwritingResearchObject)
            .scalar_subquery()
        )
        statement = (
            select(UnderwritingResearchObject, UnderwritingObjectIdentityVersion)
            .join(
                UnderwritingObjectIdentityVersion,
                UnderwritingObjectIdentityVersion.id == effective_identity_id,
            )
            .where(
                UnderwritingResearchObject.kind.in_(
                    ("industry", "company", "security")
                ),
                UnderwritingObjectIdentityVersion.effective_from <= as_of,
                or_(
                    UnderwritingObjectIdentityVersion.effective_to.is_(None),
                    UnderwritingObjectIdentityVersion.effective_to > as_of,
                ),
                or_(
                    func.lower(
                        UnderwritingObjectIdentityVersion.canonical_name
                    ).contains(pattern, autoescape=True),
                    func.lower(UnderwritingObjectIdentityVersion.symbol).contains(
                        pattern, autoescape=True
                    ),
                    func.lower(UnderwritingResearchObject.external_key).contains(
                        pattern, autoescape=True
                    ),
                ),
            )
            .order_by(
                UnderwritingResearchObject.kind,
                func.lower(UnderwritingObjectIdentityVersion.canonical_name),
                func.lower(UnderwritingResearchObject.external_key),
                UnderwritingResearchObject.id,
                UnderwritingObjectIdentityVersion.version.desc(),
            )
            .limit(limit)
        )
        seen: set[UUID] = set()
        results: list[
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]
        ] = []
        for research_object, identity in self._session.execute(statement):
            if research_object.id not in seen:
                seen.add(research_object.id)
                results.append((research_object, identity))
                if len(results) == limit:
                    break
        return results

    def create_project(
        self,
        *,
        project_id: UUID,
        primary_company_id: UUID,
        target_security_ids: tuple[UUID, ...],
        content_hash: str,
        membership_hashes: dict[UUID, str],
        created_at: datetime,
    ) -> UnderwritingResearchProject:
        project = UnderwritingResearchProject(
            id=project_id,
            primary_company_id=primary_company_id,
            content_hash=content_hash,
            created_at=created_at,
        )
        self._session.add(project)
        self._session.flush()
        self._session.add_all(
            UnderwritingResearchProjectSecurity(
                project_id=project_id,
                security_id=security_id,
                content_hash=membership_hashes[security_id],
                created_at=created_at,
            )
            for security_id in target_security_ids
        )
        self._session.flush()
        return project

    def project(
        self, project_id: UUID
    ) -> tuple[UnderwritingResearchProject, tuple[UUID, ...]] | None:
        project = self._session.get(UnderwritingResearchProject, project_id)
        if project is None:
            return None
        security_ids = tuple(
            self._session.scalars(
                select(UnderwritingResearchProjectSecurity.security_id)
                .where(UnderwritingResearchProjectSecurity.project_id == project_id)
                .order_by(UnderwritingResearchProjectSecurity.security_id)
            )
        )
        return project, security_ids

    def list_projects(
        self, limit: int
    ) -> list[tuple[UnderwritingResearchProject, tuple[UUID, ...]]]:
        projects = list(
            self._session.scalars(
                select(UnderwritingResearchProject)
                .order_by(
                    UnderwritingResearchProject.created_at,
                    UnderwritingResearchProject.id,
                )
                .limit(limit)
            )
        )
        return [
            (project, self.project(project.id)[1])  # type: ignore[index]
            for project in projects
        ]

    def mandate_head(self, project_id: UUID) -> UnderwritingMandateVersion | None:
        return self._session.scalar(
            select(UnderwritingMandateVersion)
            .where(UnderwritingMandateVersion.project_id == project_id)
            .order_by(
                UnderwritingMandateVersion.version.desc(),
                UnderwritingMandateVersion.id.desc(),
            )
            .limit(1)
        )

    def product_mandate(
        self, project_id: UUID, mandate_id: UUID
    ) -> UnderwritingMandateVersion | None:
        return self._session.scalar(
            select(UnderwritingMandateVersion)
            .where(
                UnderwritingMandateVersion.id == mandate_id,
                UnderwritingMandateVersion.project_id == project_id,
            )
            .limit(1)
        )

    def append_product_mandate(
        self,
        *,
        project_id: UUID,
        mandate_key: str,
        horizon_years: int,
        base_currency: str,
        required_return: Decimal,
        permanent_loss_limit: Decimal,
        comparison_set: tuple[str, ...],
        benchmark_key: str | None,
        required_excess_return: Decimal | None,
        effective_at: datetime,
        expires_at: datetime | None,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingMandateVersion:
        head = self._session.scalar(
            select(UnderwritingMandateVersion)
            .where(UnderwritingMandateVersion.project_id == project_id)
            .order_by(
                UnderwritingMandateVersion.version.desc(),
                UnderwritingMandateVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        return self._flush_version(
            UnderwritingMandateVersion(
                mandate_key=mandate_key,
                version=1 if head is None else head.version + 1,
                horizon_years=horizon_years,
                base_currency=base_currency,
                required_return=required_return,
                permanent_loss_limit=permanent_loss_limit,
                comparison_set=list(comparison_set),
                supersedes_id=actual_parent_id,
                project_id=project_id,
                benchmark_key=benchmark_key,
                required_excess_return=required_excess_return,
                effective_at=effective_at,
                expires_at=expires_at,
                content_hash=content_hash,
                created_at=created_at,
            )
        )

    def scope(
        self, project_id: UUID, scope_id: UUID
    ) -> UnderwritingResearchScopeVersion | None:
        return self._session.scalar(
            select(UnderwritingResearchScopeVersion)
            .where(
                UnderwritingResearchScopeVersion.id == scope_id,
                UnderwritingResearchScopeVersion.project_id == project_id,
            )
            .limit(1)
        )

    def scope_by_id(self, scope_id: UUID) -> UnderwritingResearchScopeVersion | None:
        return self._session.get(UnderwritingResearchScopeVersion, scope_id)

    def append_scope(
        self,
        *,
        project_id: UUID,
        payload: dict[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingResearchScopeVersion:
        head = self._session.scalar(
            select(UnderwritingResearchScopeVersion)
            .where(UnderwritingResearchScopeVersion.project_id == project_id)
            .order_by(
                UnderwritingResearchScopeVersion.version.desc(),
                UnderwritingResearchScopeVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        return self._flush_version(
            UnderwritingResearchScopeVersion(
                project_id=project_id,
                version=1 if head is None else head.version + 1,
                payload=deepcopy(payload),
                supersedes_id=actual_parent_id,
                content_hash=content_hash,
                created_at=created_at,
            )
        )

    def append_agenda(
        self,
        *,
        project_id: UUID,
        scope_id: UUID,
        payload: dict[str, object],
        generator_provenance: dict[str, object],
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingResearchAgendaVersion:
        head = self._session.scalar(
            select(UnderwritingResearchAgendaVersion)
            .where(UnderwritingResearchAgendaVersion.project_id == project_id)
            .order_by(
                UnderwritingResearchAgendaVersion.version.desc(),
                UnderwritingResearchAgendaVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        return self._flush_version(
            UnderwritingResearchAgendaVersion(
                project_id=project_id,
                version=1 if head is None else head.version + 1,
                scope_id=scope_id,
                payload=deepcopy(payload),
                generator_provenance=deepcopy(generator_provenance),
                supersedes_id=actual_parent_id,
                content_hash=content_hash,
                created_at=created_at,
            )
        )

    def agenda(
        self, project_id: UUID, agenda_id: UUID
    ) -> UnderwritingResearchAgendaVersion | None:
        return self._session.scalar(
            select(UnderwritingResearchAgendaVersion)
            .where(
                UnderwritingResearchAgendaVersion.id == agenda_id,
                UnderwritingResearchAgendaVersion.project_id == project_id,
            )
            .limit(1)
        )

    def create_product_basis(
        self,
        *,
        cutoff: datetime,
        source_manifest_hash: str,
        definition_bundle_hash: str,
        parser_bundle_hash: str,
        boundary_schema_version: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingHistoricalBasis:
        row = UnderwritingHistoricalBasis(
            cutoff=cutoff,
            price_as_of=None,
            source_manifest_hash=source_manifest_hash,
            definition_bundle_hash=definition_bundle_hash,
            parser_bundle_hash=parser_bundle_hash,
            boundary_schema_version=boundary_schema_version,
            content_hash=content_hash,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def product_basis(self, basis_id: UUID) -> UnderwritingHistoricalBasis | None:
        return self._session.scalar(
            select(UnderwritingHistoricalBasis)
            .where(
                UnderwritingHistoricalBasis.id == basis_id,
                UnderwritingHistoricalBasis.boundary_schema_version
                == "product.historical-basis.v1",
                UnderwritingHistoricalBasis.price_as_of.is_(None),
            )
            .limit(1)
        )
