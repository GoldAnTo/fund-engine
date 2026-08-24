"""Focused persistence operations for investment-research product projects."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.models.ledger import ConflictError
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingObjectIdentityVersion,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.repository import StaleParentError


_STALE_MESSAGE = "expected parent is not the version family head"
_CAS_CONSTRAINTS = {
    "uw_object_identity_versions": frozenset(
        {
            "uq_uw_object_identity_version",
            "uq_uw_object_identity_successor",
        }
    ),
    "uw_research_scope_versions": frozenset(
        {
            "uq_uw_research_scope_version",
            "uq_uw_research_scope_successor",
        }
    ),
    "uw_research_agenda_versions": frozenset(
        {
            "uq_uw_research_agenda_version",
            "uq_uw_research_agenda_successor",
        }
    ),
    "uw_mandate_versions": frozenset(
        {
            "uq_uw_mandate_version",
            "uq_uw_product_mandate_project_version",
            "uq_uw_product_mandate_successor",
            "uq_uw_mandate_product_project_version",
            "uq_uw_mandate_product_successor",
        }
    ),
    "uw_security_rights_versions": frozenset(
        {
            "uq_uw_security_rights_version",
            "uq_uw_security_rights_successor",
        }
    ),
}
_SQLITE_CAS_COLUMNS = {
    "uw_object_identity_versions": frozenset(
        {
            frozenset({"object_id", "version"}),
            frozenset({"supersedes_id"}),
        }
    ),
    "uw_research_scope_versions": frozenset(
        {
            frozenset({"project_id", "version"}),
            frozenset({"supersedes_id"}),
        }
    ),
    "uw_research_agenda_versions": frozenset(
        {
            frozenset({"project_id", "version"}),
            frozenset({"supersedes_id"}),
        }
    ),
    "uw_mandate_versions": frozenset(
        {
            frozenset({"mandate_key", "version"}),
            frozenset({"project_id", "version"}),
            frozenset({"supersedes_id"}),
        }
    ),
    "uw_security_rights_versions": frozenset(
        {
            frozenset({"security_identity_id", "version"}),
            frozenset({"supersedes_id"}),
        }
    ),
}

_SNAPSHOT_IDENTITY_CONSTRAINTS = {
    "uw_price_snapshots": "uq_uw_price_snapshot_identity",
    "uw_fx_snapshots": "uq_uw_fx_snapshot_identity",
    "uw_capital_structure_snapshots": "uq_uw_capital_structure_identity",
}
_SQLITE_SNAPSHOT_IDENTITY_COLUMNS = {
    "uw_price_snapshots": frozenset(
        {
            "security_identity_id",
            "price_type",
            "adjustment_basis",
            "market_at",
            "source_id",
            "raw_hash",
        }
    ),
    "uw_fx_snapshots": frozenset(
        {
            "base_currency",
            "quote_currency",
            "quote_direction",
            "market_at",
            "source_id",
            "raw_hash",
        }
    ),
    "uw_capital_structure_snapshots": frozenset(
        {
            "company_id",
            "report_period_start",
            "report_period_end",
            "market_at",
            "source_id",
            "raw_hash",
        }
    ),
}
_WORKSPACE_DRAFT_PROJECT_CONSTRAINT = "uq_uw_workspace_draft_project"
_UNSET_BASE_REVISION = object()


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

    @staticmethod
    def _is_cas_integrity_error(row: Any, exc: IntegrityError) -> bool:
        table_name = getattr(row, "__tablename__", "")
        if table_name not in _CAS_CONSTRAINTS:
            return False
        if table_name == "uw_mandate_versions" and row.project_id is None:
            return False

        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if isinstance(constraint_name, str):
            if constraint_name in _CAS_CONSTRAINTS[table_name]:
                return True
            normalized_name = constraint_name.casefold()
            return (
                table_name == "uw_mandate_versions"
                and "mandate" in normalized_name
                and (
                    "successor" in normalized_name
                    or ("project" in normalized_name and "version" in normalized_name)
                )
            )

        detail = str(getattr(exc, "orig", exc)).casefold().replace('"', "")
        marker = "unique constraint failed:"
        if marker not in detail:
            return False
        raw_columns = detail.split(marker, 1)[1].splitlines()[0]
        columns = frozenset(
            column.strip().removeprefix("main.").split(".")[-1]
            for column in raw_columns.split(",")
        )
        return columns in _SQLITE_CAS_COLUMNS[table_name]

    def _flush_version(self, row: Any) -> Any:
        """Flush one CAS append inside a savepoint and classify only CAS races."""
        try:
            connection = self._session.connection()
            if connection.dialect.name == "sqlite":
                dbapi_connection = getattr(
                    connection.connection,
                    "driver_connection",
                    connection.connection,
                )
                if not dbapi_connection.in_transaction:
                    # In sqlite3 legacy transaction mode, a top-level SAVEPOINT
                    # is committed when released. Start the caller transaction
                    # explicitly so releasing our savepoint cannot commit it.
                    connection.exec_driver_sql("BEGIN")
            # ``begin_nested`` flushes existing pending state before opening the
            # savepoint, so this row must not be added until the savepoint exists.
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush([row])
        except IntegrityError as exc:
            if self._is_cas_integrity_error(row, exc):
                raise StaleParentError(_STALE_MESSAGE) from exc
            raise
        return row

    @staticmethod
    def _is_snapshot_identity_error(row: Any, exc: IntegrityError) -> bool:
        table_name = getattr(row, "__tablename__", "")
        constraint = _SNAPSHOT_IDENTITY_CONSTRAINTS.get(table_name)
        if constraint is None:
            return False
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name == constraint
        detail = str(getattr(exc, "orig", exc)).casefold().replace('"', "")
        marker = "unique constraint failed:"
        if marker not in detail:
            return False
        raw_columns = detail.split(marker, 1)[1].splitlines()[0]
        columns = frozenset(
            column.strip().removeprefix("main.").split(".")[-1]
            for column in raw_columns.split(",")
        )
        return columns == _SQLITE_SNAPSHOT_IDENTITY_COLUMNS[table_name]

    def _flush_snapshot(self, row: Any, existing_statement: Any) -> Any:
        """Insert an immutable snapshot, recovering exact natural-key retries."""
        try:
            connection = self._session.connection()
            if connection.dialect.name == "sqlite":
                dbapi_connection = getattr(
                    connection.connection,
                    "driver_connection",
                    connection.connection,
                )
                if not dbapi_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush([row])
            return row
        except IntegrityError as exc:
            if not self._is_snapshot_identity_error(row, exc):
                raise
        existing = self._session.scalar(existing_statement.limit(1))
        if existing is None:
            raise ConflictError("snapshot natural identity conflicted concurrently")
        if existing.content_hash != row.content_hash:
            raise ConflictError("snapshot natural identity has conflicting content")
        return existing

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

    @staticmethod
    def _is_workspace_draft_project_error(exc: IntegrityError) -> bool:
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name == _WORKSPACE_DRAFT_PROJECT_CONSTRAINT

        detail = str(getattr(exc, "orig", exc)).casefold().replace('"', "")
        marker = "unique constraint failed:"
        if marker not in detail:
            return False
        raw_columns = detail.split(marker, 1)[1].splitlines()[0]
        columns = frozenset(
            column.strip().removeprefix("main.") for column in raw_columns.split(",")
        )
        return columns == frozenset({"uw_workspace_drafts.project_id"})

    def workspace_draft(self, project_id: UUID) -> UnderwritingWorkspaceDraft | None:
        return self._session.scalar(
            select(UnderwritingWorkspaceDraft)
            .where(UnderwritingWorkspaceDraft.project_id == project_id)
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def create_workspace_draft(
        self,
        *,
        draft_id: UUID,
        project_id: UUID,
        content: dict[str, object],
        created_at: datetime,
    ) -> UnderwritingWorkspaceDraft:
        """Create one draft, recovering only the per-project uniqueness race."""
        row = UnderwritingWorkspaceDraft(
            id=draft_id,
            project_id=project_id,
            base_revision_id=None,
            lock_version=1,
            content=deepcopy(content),
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            connection = self._session.connection()
            if connection.dialect.name == "sqlite":
                dbapi_connection = getattr(
                    connection.connection,
                    "driver_connection",
                    connection.connection,
                )
                if not dbapi_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush([row])
        except IntegrityError as exc:
            if not self._is_workspace_draft_project_error(exc):
                raise
            existing = self.workspace_draft(project_id)
            if existing is None:
                raise ConflictError(
                    "workspace draft conflicted concurrently; retry creation"
                ) from exc
            return existing
        return row

    def compare_and_swap_workspace_draft(
        self,
        *,
        project_id: UUID,
        expected_lock_version: int,
        content: dict[str, object],
        updated_at: datetime,
        base_revision_id: UUID | None | object = _UNSET_BASE_REVISION,
    ) -> UnderwritingWorkspaceDraft:
        """Atomically update a draft; publication may also reset its base."""
        values: dict[str, object] = {
            "content": deepcopy(content),
            "lock_version": expected_lock_version + 1,
            "updated_at": updated_at,
        }
        if base_revision_id is not _UNSET_BASE_REVISION:
            values["base_revision_id"] = base_revision_id
        result = self._session.execute(
            update(UnderwritingWorkspaceDraft)
            .where(
                UnderwritingWorkspaceDraft.project_id == project_id,
                UnderwritingWorkspaceDraft.lock_version == expected_lock_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ConflictError("workspace draft changed; reload before saving")
        refreshed = self.workspace_draft(project_id)
        if refreshed is None:  # Defensive: drafts are delete-protected.
            raise ConflictError("workspace draft disappeared during saving")
        return refreshed

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

    def freeze_price(
        self,
        *,
        security_identity_id: UUID,
        price: Decimal,
        currency: str,
        price_type: str,
        adjustment_basis: str,
        market_at: datetime,
        available_at: datetime,
        source_id: str,
        raw_hash: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingPriceSnapshot:
        row = UnderwritingPriceSnapshot(
            security_identity_id=security_identity_id,
            price=price,
            currency=currency,
            price_type=price_type,
            adjustment_basis=adjustment_basis,
            market_at=market_at,
            available_at=available_at,
            source_id=source_id,
            raw_hash=raw_hash,
            content_hash=content_hash,
            created_at=created_at,
        )
        existing = select(UnderwritingPriceSnapshot).where(
            UnderwritingPriceSnapshot.security_identity_id == security_identity_id,
            UnderwritingPriceSnapshot.price_type == price_type,
            UnderwritingPriceSnapshot.adjustment_basis == adjustment_basis,
            UnderwritingPriceSnapshot.market_at == market_at,
            UnderwritingPriceSnapshot.source_id == source_id,
            UnderwritingPriceSnapshot.raw_hash == raw_hash,
        )
        return self._flush_snapshot(row, existing)

    def price(self, snapshot_id: UUID) -> UnderwritingPriceSnapshot | None:
        return self._session.get(UnderwritingPriceSnapshot, snapshot_id)

    def prices(self, snapshot_ids: tuple[UUID, ...]) -> list[UnderwritingPriceSnapshot]:
        if not snapshot_ids:
            return []
        return list(
            self._session.scalars(
                select(UnderwritingPriceSnapshot).where(
                    UnderwritingPriceSnapshot.id.in_(snapshot_ids)
                )
            )
        )

    def freeze_fx(
        self,
        *,
        base_currency: str,
        quote_currency: str,
        rate: Decimal,
        quote_direction: str,
        market_at: datetime,
        available_at: datetime,
        source_id: str,
        raw_hash: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingFXSnapshot:
        row = UnderwritingFXSnapshot(
            base_currency=base_currency,
            quote_currency=quote_currency,
            rate=rate,
            quote_direction=quote_direction,
            market_at=market_at,
            available_at=available_at,
            source_id=source_id,
            raw_hash=raw_hash,
            content_hash=content_hash,
            created_at=created_at,
        )
        existing = select(UnderwritingFXSnapshot).where(
            UnderwritingFXSnapshot.base_currency == base_currency,
            UnderwritingFXSnapshot.quote_currency == quote_currency,
            UnderwritingFXSnapshot.quote_direction == quote_direction,
            UnderwritingFXSnapshot.market_at == market_at,
            UnderwritingFXSnapshot.source_id == source_id,
            UnderwritingFXSnapshot.raw_hash == raw_hash,
        )
        return self._flush_snapshot(row, existing)

    def fx(self, snapshot_id: UUID) -> UnderwritingFXSnapshot | None:
        return self._session.get(UnderwritingFXSnapshot, snapshot_id)

    def fxs(self, snapshot_ids: tuple[UUID, ...]) -> list[UnderwritingFXSnapshot]:
        if not snapshot_ids:
            return []
        return list(
            self._session.scalars(
                select(UnderwritingFXSnapshot).where(
                    UnderwritingFXSnapshot.id.in_(snapshot_ids)
                )
            )
        )

    def fx_for_pair(
        self, base_currency: str, quote_currency: str, market_at: datetime
    ) -> UnderwritingFXSnapshot | None:
        candidates = list(
            self._session.scalars(
                select(UnderwritingFXSnapshot)
                .where(
                    UnderwritingFXSnapshot.base_currency == base_currency,
                    UnderwritingFXSnapshot.quote_currency == quote_currency,
                    UnderwritingFXSnapshot.quote_direction == "quote_per_base",
                    UnderwritingFXSnapshot.market_at == market_at,
                )
                .order_by(UnderwritingFXSnapshot.id)
                .limit(2)
            )
        )
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ConflictError(
                "multiple FX sources match; select an exact snapshot ID or source"
            )
        return candidates[0]

    def freeze_capital_structure(
        self,
        *,
        company_id: UUID,
        currency: str,
        cash: Decimal,
        debt: Decimal,
        minority_interest: Decimal,
        investments: Decimal,
        pension_liabilities: Decimal,
        other_adjustments: Decimal,
        basic_shares: Decimal,
        diluted_shares: Decimal,
        potential_dilution_descriptors: list[str],
        report_period_start: datetime,
        report_period_end: datetime,
        market_at: datetime,
        available_at: datetime,
        source_id: str,
        raw_hash: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingCapitalStructureSnapshot:
        row = UnderwritingCapitalStructureSnapshot(
            company_id=company_id,
            currency=currency,
            cash=cash,
            debt=debt,
            minority_interest=minority_interest,
            investments=investments,
            pension_liabilities=pension_liabilities,
            other_adjustments=other_adjustments,
            basic_shares=basic_shares,
            diluted_shares=diluted_shares,
            potential_dilution_descriptors=deepcopy(potential_dilution_descriptors),
            report_period_start=report_period_start,
            report_period_end=report_period_end,
            market_at=market_at,
            available_at=available_at,
            source_id=source_id,
            raw_hash=raw_hash,
            content_hash=content_hash,
            created_at=created_at,
        )
        existing = select(UnderwritingCapitalStructureSnapshot).where(
            UnderwritingCapitalStructureSnapshot.company_id == company_id,
            UnderwritingCapitalStructureSnapshot.report_period_start
            == report_period_start,
            UnderwritingCapitalStructureSnapshot.report_period_end == report_period_end,
            UnderwritingCapitalStructureSnapshot.market_at == market_at,
            UnderwritingCapitalStructureSnapshot.source_id == source_id,
            UnderwritingCapitalStructureSnapshot.raw_hash == raw_hash,
        )
        return self._flush_snapshot(row, existing)

    def capital_structure(
        self, snapshot_id: UUID
    ) -> UnderwritingCapitalStructureSnapshot | None:
        return self._session.get(UnderwritingCapitalStructureSnapshot, snapshot_id)

    def rights_head(
        self, security_identity_id: UUID
    ) -> UnderwritingSecurityRightsVersion | None:
        return self._session.scalar(
            select(UnderwritingSecurityRightsVersion)
            .where(
                UnderwritingSecurityRightsVersion.security_identity_id
                == security_identity_id
            )
            .order_by(
                UnderwritingSecurityRightsVersion.version.desc(),
                UnderwritingSecurityRightsVersion.id.desc(),
            )
            .limit(1)
        )

    def append_security_rights(
        self,
        *,
        security_identity_id: UUID,
        economic_units: Decimal,
        votes_per_unit: Decimal,
        conversion_ratio: Decimal,
        adr_ratio: Decimal,
        dividend_rights_per_unit: Decimal,
        effective_from: datetime,
        effective_to: datetime | None,
        source_id: str,
        raw_hash: str,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingSecurityRightsVersion:
        head = self._session.scalar(
            select(UnderwritingSecurityRightsVersion)
            .where(
                UnderwritingSecurityRightsVersion.security_identity_id
                == security_identity_id
            )
            .order_by(
                UnderwritingSecurityRightsVersion.version.desc(),
                UnderwritingSecurityRightsVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        if (
            head is not None
            and head.supersedes_id == expected_parent_id
            and head.content_hash == content_hash
        ):
            return head
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        return self._flush_version(
            UnderwritingSecurityRightsVersion(
                security_identity_id=security_identity_id,
                version=1 if head is None else head.version + 1,
                economic_units=economic_units,
                votes_per_unit=votes_per_unit,
                conversion_ratio=conversion_ratio,
                adr_ratio=adr_ratio,
                dividend_rights_per_unit=dividend_rights_per_unit,
                effective_from=effective_from,
                effective_to=effective_to,
                source_id=source_id,
                raw_hash=raw_hash,
                supersedes_id=actual_parent_id,
                content_hash=content_hash,
                created_at=created_at,
            )
        )

    def security_rights(
        self, rights_id: UUID
    ) -> UnderwritingSecurityRightsVersion | None:
        return self._session.get(UnderwritingSecurityRightsVersion, rights_id)

    def security_rights_many(
        self, rights_ids: tuple[UUID, ...]
    ) -> list[UnderwritingSecurityRightsVersion]:
        if not rights_ids:
            return []
        return list(
            self._session.scalars(
                select(UnderwritingSecurityRightsVersion).where(
                    UnderwritingSecurityRightsVersion.id.in_(rights_ids)
                )
            )
        )

    def effective_security_rights(
        self, security_identity_id: UUID, as_of: datetime
    ) -> UnderwritingSecurityRightsVersion | None:
        candidate = aliased(UnderwritingSecurityRightsVersion)
        latest_started_id = (
            select(candidate.id)
            .where(
                candidate.security_identity_id == security_identity_id,
                candidate.effective_from <= as_of,
            )
            .order_by(
                candidate.effective_from.desc(),
                candidate.version.desc(),
                candidate.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        return self._session.scalar(
            select(UnderwritingSecurityRightsVersion)
            .where(
                UnderwritingSecurityRightsVersion.id == latest_started_id,
                or_(
                    UnderwritingSecurityRightsVersion.effective_to.is_(None),
                    UnderwritingSecurityRightsVersion.effective_to > as_of,
                ),
            )
            .limit(1)
        )
