"""Focused persistence operations for investment-research product projects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import RevisionBoundaryInput
from app.underwriting.domain.search_terms import (
    SearchTermIntegrityError,
    digest_search_term,
    normalize_search_term,
    require_valid_search_normalization,
    require_valid_search_term,
)
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
    UnderwritingResearchVersion,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingObjectIdentityVersion,
    UnderwritingResearchObjectAlias,
    UnderwritingResearchObjectSearchTerm,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchAssessmentVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingRevisionBoundary,
    UnderwritingRevisionManifest,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash


_STALE_MESSAGE = "expected parent is not the version family head"
# This bounds rows materialized and expanded in Python. Without a deployed FTS or
# trigram extension, a leading-wildcard substring predicate may still scan in DB.
_OBJECT_SEARCH_CANDIDATE_CAP = 256


@dataclass(frozen=True, slots=True)
class ObjectSearchOutcome:
    rows: tuple[
        tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
    ]
    had_raw_match: bool
    candidate_count: int


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
    "uw_price_snapshots": "uq_uw_price_snapshot_business_time",
    "uw_fx_snapshots": "uq_uw_fx_snapshot_business_time",
    "uw_capital_structure_snapshots": "uq_uw_capital_structure_business_time",
}
_SQLITE_SNAPSHOT_IDENTITY_COLUMNS = {
    "uw_price_snapshots": frozenset(
        {
            "security_identity_id",
            "price_type",
            "adjustment_basis",
            "market_at",
        }
    ),
    "uw_fx_snapshots": frozenset(
        {
            "base_currency",
            "quote_currency",
            "quote_direction",
            "market_at",
        }
    ),
    "uw_capital_structure_snapshots": frozenset(
        {
            "company_id",
            "report_period_start",
            "report_period_end",
            "market_at",
        }
    ),
}
_WORKSPACE_DRAFT_PROJECT_CONSTRAINT = "uq_uw_workspace_draft_project"
_PRODUCT_MANIFEST_SCHEMA = "underwriting.research-revision-manifest.v1"
_PRODUCT_REVISION_KIND = "independent_research"


class _UnchangedBaseRevision:
    """Typed sentinel that keeps the ordinary draft-save path base-blind."""

    __slots__ = ()


_UNCHANGED_BASE_REVISION = _UnchangedBaseRevision()


def _sqlite_unique_columns(
    exc: IntegrityError, expected_table: str
) -> frozenset[str] | None:
    """Return exact SQLite UNIQUE columns only for ``expected_table``."""
    detail = str(getattr(exc, "orig", exc)).casefold().replace('"', "")
    marker = "unique constraint failed:"
    if marker not in detail:
        return None
    raw_columns = detail.split(marker, 1)[1].splitlines()[0]
    columns: set[str] = set()
    for raw_column in raw_columns.split(","):
        qualified = raw_column.strip().removeprefix("main.")
        parts = qualified.split(".")
        if len(parts) < 2 or parts[-2] != expected_table:
            return None
        columns.add(parts[-1])
    return frozenset(columns)


class ProductRepository:
    """Flush-only persistence primitives for product project orchestration."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _flush_in_savepoint(self, row: Any) -> None:
        """Flush one new row without letting SQLite release the outer unit of work."""
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            dbapi_connection = getattr(
                connection.connection,
                "driver_connection",
                connection.connection,
            )
            if not dbapi_connection.in_transaction:
                # In sqlite3 legacy transaction mode, a top-level SAVEPOINT is
                # committed when released. Anchor it in the caller transaction.
                connection.exec_driver_sql("BEGIN")
        # begin_nested flushes pending state before opening the savepoint, so
        # this row must not be attached until the savepoint exists.
        with self._session.begin_nested():
            self._session.add(row)
            self._session.flush([row])

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

        columns = _sqlite_unique_columns(exc, table_name)
        return columns in _SQLITE_CAS_COLUMNS[table_name]

    def _flush_version(self, row: Any) -> Any:
        """Flush one CAS append inside a savepoint and classify only CAS races."""
        try:
            self._flush_in_savepoint(row)
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
        columns = _sqlite_unique_columns(exc, table_name)
        return columns == _SQLITE_SNAPSHOT_IDENTITY_COLUMNS[table_name]

    def _flush_snapshot(self, row: Any, existing_statement: Any) -> Any:
        """Insert an immutable snapshot, recovering exact natural-key retries."""
        try:
            self._flush_in_savepoint(row)
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

    def ensure_identity_search_terms(
        self,
        research_object: UnderwritingResearchObject,
        identity: UnderwritingObjectIdentityVersion,
        *,
        repair_missing: bool,
    ) -> tuple[UnderwritingResearchObjectSearchTerm, ...]:
        """Validate one identity's exact derived projection and fill only gaps."""
        expected = {
            (None, "external_key"): research_object.external_key,
            (identity.id, "canonical_name"): identity.canonical_name,
        }
        if identity.symbol is not None:
            expected[(identity.id, "symbol")] = identity.symbol
        existing = tuple(
            self._session.scalars(
                select(UnderwritingResearchObjectSearchTerm)
                .where(
                    UnderwritingResearchObjectSearchTerm.object_id
                    == research_object.id,
                    or_(
                        UnderwritingResearchObjectSearchTerm.identity_version_id
                        == identity.id,
                        and_(
                            UnderwritingResearchObjectSearchTerm.identity_version_id.is_(
                                None
                            ),
                            UnderwritingResearchObjectSearchTerm.term_kind
                            == "external_key",
                        ),
                    ),
                )
                .order_by(
                    UnderwritingResearchObjectSearchTerm.term_kind,
                    UnderwritingResearchObjectSearchTerm.id,
                )
            )
        )
        actual: dict[tuple[UUID | None, str], UnderwritingResearchObjectSearchTerm] = {}
        for row in existing:
            key = (row.identity_version_id, row.term_kind)
            raw_value = expected.get(key)
            if raw_value is None or row.raw_value != raw_value:
                raise SearchTermIntegrityError(
                    "persisted research search term source conflict"
                )
            require_valid_search_term(
                row.raw_value,
                row.normalized_value,
                row.normalized_digest,
            )
            if key in actual:
                raise SearchTermIntegrityError(
                    "persisted research search term duplicate conflict"
                )
            actual[key] = row
        missing = expected.keys() - actual.keys()
        if missing and not repair_missing:
            raise SearchTermIntegrityError(
                "persisted research search term projection is incomplete"
            )
        for key in sorted(missing, key=lambda item: (str(item[0]), item[1])):
            identity_version_id, term_kind = key
            raw_value = expected[key]
            normalized_value = normalize_search_term(raw_value)
            row = UnderwritingResearchObjectSearchTerm(
                object_id=research_object.id,
                identity_version_id=identity_version_id,
                term_kind=term_kind,
                raw_value=raw_value,
                normalized_value=normalized_value,
                normalized_digest=digest_search_term(normalized_value),
                created_at=(
                    research_object.created_at
                    if identity_version_id is None
                    else identity.created_at
                ),
            )
            self._session.add(row)
            actual[key] = row
        if missing:
            self._session.flush()
        return tuple(
            actual[key] for key in sorted(actual, key=lambda x: (str(x[0]), x[1]))
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
        identity = self._flush_version(
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
        research_object = self.object(object_id)
        if research_object is None:  # Defensive against a disabled foreign key.
            raise SearchTermIntegrityError("research search term object is missing")
        self.ensure_identity_search_terms(
            research_object,
            identity,
            repair_missing=True,
        )
        return identity

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

    @staticmethod
    def _row_key(row):
        research_object, identity = row
        return (
            research_object.kind,
            normalize_search_term(identity.canonical_name),
            normalize_search_term(research_object.external_key),
            str(research_object.id),
            -identity.version,
        )

    @staticmethod
    def _effective_object_statement(as_of: datetime):
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
        return (
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
            )
        )

    @staticmethod
    def _candidate_match_predicate(
        pattern: str,
        pattern_digest: str,
        *,
        exact: bool,
    ):
        term_value = UnderwritingResearchObjectSearchTerm.normalized_value
        alias_value = UnderwritingResearchObjectAlias.normalized_alias
        term_matches = (
            and_(
                UnderwritingResearchObjectSearchTerm.normalized_digest
                == pattern_digest,
                term_value == pattern,
            )
            if exact
            else term_value.contains(pattern, autoescape=True)
        )
        alias_matches = (
            alias_value == pattern
            if exact
            else alias_value.contains(pattern, autoescape=True)
        )
        projected_match = (
            select(UnderwritingResearchObjectSearchTerm.id)
            .where(
                UnderwritingResearchObjectSearchTerm.object_id
                == UnderwritingResearchObject.id,
                or_(
                    and_(
                        UnderwritingResearchObjectSearchTerm.term_kind
                        == "external_key",
                        UnderwritingResearchObjectSearchTerm.identity_version_id.is_(
                            None
                        ),
                    ),
                    UnderwritingResearchObjectSearchTerm.identity_version_id
                    == UnderwritingObjectIdentityVersion.id,
                ),
                term_matches,
            )
            .correlate(UnderwritingResearchObject, UnderwritingObjectIdentityVersion)
            .exists()
        )
        alias_match = (
            select(UnderwritingResearchObjectAlias.id)
            .where(
                UnderwritingResearchObjectAlias.object_id
                == UnderwritingResearchObject.id,
                alias_matches,
            )
            .correlate(UnderwritingResearchObject)
            .exists()
        )
        return or_(projected_match, alias_match)

    def _search_candidate_rows(
        self,
        pattern: str,
        pattern_digest: str,
        as_of: datetime,
    ) -> tuple[
        tuple[
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
        ],
        set[UUID],
    ]:
        canonical_sort = (
            select(UnderwritingResearchObjectSearchTerm.normalized_value)
            .where(
                UnderwritingResearchObjectSearchTerm.identity_version_id
                == UnderwritingObjectIdentityVersion.id,
                UnderwritingResearchObjectSearchTerm.term_kind == "canonical_name",
            )
            .correlate(UnderwritingObjectIdentityVersion)
            .limit(1)
            .scalar_subquery()
        )
        external_sort = (
            select(UnderwritingResearchObjectSearchTerm.normalized_value)
            .where(
                UnderwritingResearchObjectSearchTerm.object_id
                == UnderwritingResearchObject.id,
                UnderwritingResearchObjectSearchTerm.term_kind == "external_key",
                UnderwritingResearchObjectSearchTerm.identity_version_id.is_(None),
            )
            .correlate(UnderwritingResearchObject)
            .limit(1)
            .scalar_subquery()
        )
        stable_order = (
            UnderwritingResearchObject.kind,
            canonical_sort,
            external_sort,
            UnderwritingResearchObject.id,
        )
        exact_statement = (
            self._effective_object_statement(as_of)
            .where(
                self._candidate_match_predicate(
                    pattern,
                    pattern_digest,
                    exact=True,
                )
            )
            .order_by(*stable_order)
            .limit(_OBJECT_SEARCH_CANDIDATE_CAP)
            .execution_options(uw_search_candidate_stage="exact")
        )
        exact_rows = tuple(self._session.execute(exact_statement).tuples())
        exact_ids = {row[0].id for row in exact_rows}
        remaining = _OBJECT_SEARCH_CANDIDATE_CAP - len(exact_rows)
        substring_statement = self._effective_object_statement(as_of).where(
            self._candidate_match_predicate(
                pattern,
                pattern_digest,
                exact=False,
            )
        )
        if exact_ids:
            substring_statement = substring_statement.where(
                UnderwritingResearchObject.id.not_in(exact_ids)
            )
        substring_statement = (
            substring_statement.order_by(*stable_order)
            .limit(remaining)
            .execution_options(uw_search_candidate_stage="substring")
        )
        substring_rows = tuple(self._session.execute(substring_statement).tuples())
        return exact_rows + substring_rows, exact_ids

    def _validate_selected_search_terms(
        self,
        rows: tuple[
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
        ],
        pattern: str,
    ) -> None:
        """Fail closed if any selected admin-bypass projection is non-canonical."""
        expected: dict[tuple[UUID, UUID | None, str], str] = {}
        object_ids: set[UUID] = set()
        identity_ids: set[UUID] = set()
        for research_object, identity in rows:
            object_ids.add(research_object.id)
            identity_ids.add(identity.id)
            expected[(research_object.id, None, "external_key")] = (
                research_object.external_key
            )
            expected[(research_object.id, identity.id, "canonical_name")] = (
                identity.canonical_name
            )
            if identity.symbol is not None:
                expected[(research_object.id, identity.id, "symbol")] = identity.symbol
        projected_rows = tuple(
            self._session.scalars(
                select(UnderwritingResearchObjectSearchTerm)
                .where(
                    UnderwritingResearchObjectSearchTerm.object_id.in_(object_ids),
                    or_(
                        UnderwritingResearchObjectSearchTerm.identity_version_id.in_(
                            identity_ids
                        ),
                        and_(
                            UnderwritingResearchObjectSearchTerm.identity_version_id.is_(
                                None
                            ),
                            UnderwritingResearchObjectSearchTerm.term_kind
                            == "external_key",
                        ),
                    ),
                )
                .order_by(UnderwritingResearchObjectSearchTerm.id)
            )
        )
        actual: set[tuple[UUID, UUID | None, str]] = set()
        for term in projected_rows:
            key = (term.object_id, term.identity_version_id, term.term_kind)
            if key not in expected or term.raw_value != expected[key]:
                raise SearchTermIntegrityError(
                    "persisted research search term source conflict"
                )
            require_valid_search_term(
                term.raw_value,
                term.normalized_value,
                term.normalized_digest,
            )
            actual.add(key)
        if actual != expected.keys():
            raise SearchTermIntegrityError(
                "persisted research search term projection is incomplete"
            )
        aliases = tuple(
            self._session.scalars(
                select(UnderwritingResearchObjectAlias)
                .where(
                    UnderwritingResearchObjectAlias.object_id.in_(object_ids),
                    UnderwritingResearchObjectAlias.normalized_alias.contains(
                        pattern, autoescape=True
                    ),
                )
                .order_by(UnderwritingResearchObjectAlias.id)
                .limit(_OBJECT_SEARCH_CANDIDATE_CAP + 1)
            )
        )
        if len(aliases) > _OBJECT_SEARCH_CANDIDATE_CAP:
            raise SearchTermIntegrityError(
                "research alias match set exceeds the validation bound"
            )
        for alias in aliases:
            require_valid_search_normalization(alias.alias, alias.normalized_alias)

    def _search_anchor_rows(
        self,
        matched: dict[
            UUID,
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
        ],
        exact_object_ids: set[UUID],
        as_of: datetime,
    ) -> tuple[
        dict[
            UUID,
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
        ],
        dict[UUID, bool],
    ]:
        matched_security_ids = {
            object_id
            for object_id, (research_object, _) in matched.items()
            if research_object.kind == "security"
        }
        parent_ids_by_security: dict[UUID, set[UUID]] = {
            object_id: set() for object_id in matched_security_ids
        }
        if matched_security_ids:
            relation_rows = self._session.execute(
                select(
                    UnderwritingObjectRelation.parent_id,
                    UnderwritingObjectRelation.child_id,
                )
                .where(
                    UnderwritingObjectRelation.relation_type == "company_has_security",
                    UnderwritingObjectRelation.child_id.in_(matched_security_ids),
                )
                .order_by(
                    UnderwritingObjectRelation.parent_id,
                    UnderwritingObjectRelation.child_id,
                )
                .limit(_OBJECT_SEARCH_CANDIDATE_CAP)
            )
            for parent_id, child_id in relation_rows:
                parent_ids_by_security[child_id].add(parent_id)
        possible_parent_ids = {
            parent_id
            for parent_ids in parent_ids_by_security.values()
            for parent_id in parent_ids
        }
        effective_parents = (
            {
                research_object.id: (research_object, identity)
                for research_object, identity in self._session.execute(
                    self._effective_object_statement(as_of).where(
                        UnderwritingResearchObject.id.in_(possible_parent_ids)
                    )
                )
                if research_object.kind == "company"
            }
            if possible_parent_ids
            else {}
        )
        anchor_rows = {
            object_id: row
            for object_id, row in matched.items()
            if row[0].kind in {"company", "industry"}
        }
        anchor_exact = {
            object_id: object_id in exact_object_ids for object_id in anchor_rows
        }
        anchor_rows.update(effective_parents)
        for security_id in matched_security_ids:
            valid_parent_ids = parent_ids_by_security[security_id] & set(
                effective_parents
            )
            for parent_id in valid_parent_ids:
                anchor_exact[parent_id] = (
                    anchor_exact.get(parent_id, False)
                    or security_id in exact_object_ids
                )
            if not valid_parent_ids:
                anchor_rows[security_id] = matched[security_id]
                anchor_exact[security_id] = security_id in exact_object_ids
        ordered = sorted(
            anchor_rows.items(),
            key=lambda item: (
                not anchor_exact.get(item[0], False),
                self._row_key(item[1]),
            ),
        )[:_OBJECT_SEARCH_CANDIDATE_CAP]
        bounded_rows = dict(ordered)
        return bounded_rows, {
            object_id: anchor_exact.get(object_id, False) for object_id in bounded_rows
        }

    @staticmethod
    def _effective_company_children_statement(
        company_ids: set[UUID],
        as_of: datetime,
        *,
        require_relation_as_of: bool = False,
    ):
        child = aliased(UnderwritingResearchObject)
        identity = aliased(UnderwritingObjectIdentityVersion)
        candidate = aliased(UnderwritingObjectIdentityVersion)
        effective_identity_id = (
            select(candidate.id)
            .where(
                candidate.object_id == child.id,
                candidate.effective_from <= as_of,
            )
            .order_by(candidate.version.desc(), candidate.id.desc())
            .limit(1)
            .correlate(child)
            .scalar_subquery()
        )
        statement = (
            select(UnderwritingObjectRelation.parent_id, child, identity)
            .join(child, child.id == UnderwritingObjectRelation.child_id)
            .join(identity, identity.id == effective_identity_id)
            .where(
                UnderwritingObjectRelation.relation_type == "company_has_security",
                UnderwritingObjectRelation.parent_id.in_(company_ids),
                child.kind == "security",
                identity.effective_from <= as_of,
                or_(identity.effective_to.is_(None), identity.effective_to > as_of),
            )
        )
        if require_relation_as_of:
            statement = statement.where(UnderwritingObjectRelation.created_at <= as_of)
        return statement

    def _pack_search_groups(
        self,
        anchor_rows: dict[
            UUID,
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
        ],
        anchor_exact: dict[UUID, bool],
        as_of: datetime,
        limit: int,
    ) -> tuple[
        tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
    ]:
        company_ids = {
            object_id
            for object_id, row in anchor_rows.items()
            if row[0].kind == "company"
        }
        child_counts: dict[UUID, int] = {}
        if company_ids:
            child_rows = self._effective_company_children_statement(
                company_ids, as_of
            ).subquery()
            child_counts = dict(
                self._session.execute(
                    select(child_rows.c.parent_id, func.count())
                    .group_by(child_rows.c.parent_id)
                    .order_by(child_rows.c.parent_id)
                ).all()
            )
        selected: list[
            tuple[
                UUID,
                tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
            ]
        ] = []
        reserved = 0
        ordered_anchors = sorted(
            anchor_rows.items(),
            key=lambda item: (
                not anchor_exact.get(item[0], False),
                self._row_key(item[1]),
            ),
        )

        def reserve_if_complete(anchor_id, anchor_row) -> None:
            nonlocal reserved
            group_size = 1 + (
                child_counts.get(anchor_id, 0) if anchor_row[0].kind == "company" else 0
            )
            if reserved + group_size > limit:
                return
            selected.append((anchor_id, anchor_row))
            reserved += group_size

        exact_anchors = [
            item for item in ordered_anchors if anchor_exact.get(item[0], False)
        ]
        for anchor_id, anchor_row in exact_anchors:
            reserve_if_complete(anchor_id, anchor_row)
        if exact_anchors and not selected:
            return ()
        for anchor_id, anchor_row in ordered_anchors:
            if anchor_exact.get(anchor_id, False):
                continue
            reserve_if_complete(anchor_id, anchor_row)
        selected_company_ids = {
            anchor_id for anchor_id, row in selected if row[0].kind == "company"
        }
        children_by_company: dict[
            UUID,
            list[tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]],
        ] = {company_id: [] for company_id in selected_company_ids}
        if selected_company_ids:
            child_statement = (
                self._effective_company_children_statement(selected_company_ids, as_of)
                .order_by(
                    UnderwritingObjectRelation.parent_id,
                    UnderwritingObjectRelation.child_id,
                )
                .limit(limit)
            )
            for parent_id, child, identity in self._session.execute(child_statement):
                children_by_company[parent_id].append((child, identity))
        results: list[
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]
        ] = []
        seen: set[UUID] = set()
        for anchor_id, anchor_row in selected:
            group = [anchor_row, *children_by_company.get(anchor_id, ())]
            group = sorted(group, key=self._row_key)
            group_ids = {row[0].id for row in group}
            if group_ids & seen or len(results) + len(group) > limit:
                continue
            results.extend(group)
            seen.update(group_ids)
        return tuple(results)

    def _pack_industry_company_groups(
        self,
        anchor_rows: dict[
            UUID,
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
        ],
        as_of: datetime,
        limit: int,
    ) -> tuple[
        tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
    ]:
        """Pack Industry browse groups without search's exact-match priority rule."""
        child_counts: dict[UUID, int] = {}
        if anchor_rows:
            child_rows = self._effective_company_children_statement(
                set(anchor_rows), as_of, require_relation_as_of=True
            ).subquery()
            child_counts = dict(
                self._session.execute(
                    select(child_rows.c.parent_id, func.count())
                    .group_by(child_rows.c.parent_id)
                    .order_by(child_rows.c.parent_id)
                ).all()
            )
        selected: list[
            tuple[
                UUID,
                tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion],
            ]
        ] = []
        reserved = 0
        for anchor_id, anchor_row in sorted(
            anchor_rows.items(), key=lambda item: self._row_key(item[1])
        ):
            group_size = 1 + child_counts.get(anchor_id, 0)
            if reserved + group_size > limit:
                continue
            selected.append((anchor_id, anchor_row))
            reserved += group_size

        selected_company_ids = {anchor_id for anchor_id, _ in selected}
        children_by_company: dict[
            UUID,
            list[tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]],
        ] = {company_id: [] for company_id in selected_company_ids}
        if selected_company_ids:
            child_statement = (
                self._effective_company_children_statement(
                    selected_company_ids, as_of, require_relation_as_of=True
                )
                .order_by(
                    UnderwritingObjectRelation.parent_id,
                    UnderwritingObjectRelation.child_id,
                )
                .limit(limit)
            )
            for parent_id, child, identity in self._session.execute(child_statement):
                children_by_company[parent_id].append((child, identity))
        results: list[
            tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion]
        ] = []
        seen: set[UUID] = set()
        for anchor_id, anchor_row in selected:
            group = [anchor_row, *children_by_company.get(anchor_id, ())]
            group = sorted(group, key=self._row_key)
            group_ids = {row[0].id for row in group}
            if group_ids & seen or len(results) + len(group) > limit:
                continue
            results.extend(group)
            seen.update(group_ids)
        return tuple(results)

    def search_objects(
        self,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> ObjectSearchOutcome:
        pattern = normalize_search_term(query)
        pattern_digest = digest_search_term(pattern)
        candidate_rows, exact_object_ids = self._search_candidate_rows(
            pattern,
            pattern_digest,
            as_of,
        )
        if not candidate_rows:
            return ObjectSearchOutcome(rows=(), had_raw_match=False, candidate_count=0)
        self._validate_selected_search_terms(candidate_rows, pattern)
        matched = {row[0].id: row for row in candidate_rows}
        anchor_rows, anchor_exact = self._search_anchor_rows(
            matched, exact_object_ids, as_of
        )
        return ObjectSearchOutcome(
            rows=self._pack_search_groups(anchor_rows, anchor_exact, as_of, limit),
            had_raw_match=True,
            candidate_count=len(candidate_rows),
        )

    def industry_company_groups(
        self,
        industry_id: UUID,
        as_of: datetime,
        limit: int,
    ) -> tuple[
        tuple[UnderwritingResearchObject, UnderwritingObjectIdentityVersion], ...
    ] | None:
        """Return effective, complete Company groups directly exposed by an Industry.

        The direct relation is the auditable browse boundary.  We inspect no
        transitive graph edges and bound materialized relation rows before
        expanding a Company to its effective Securities.
        """
        industry = self.object(industry_id)
        if industry is None:
            return None
        if industry.kind != "industry":
            raise ValidationError("industry_id must identify an Industry")
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        if self.effective_identity(industry_id, as_of) is None:
            raise ValidationError("Industry has no effective identity at as_of")

        direct_children = tuple(
            self._session.scalars(
                select(UnderwritingResearchObject)
                .join(
                    UnderwritingObjectRelation,
                    UnderwritingObjectRelation.child_id
                    == UnderwritingResearchObject.id,
                )
                .where(
                    UnderwritingObjectRelation.parent_id == industry_id,
                    UnderwritingObjectRelation.relation_type
                    == "industry_exposes_company",
                    UnderwritingObjectRelation.created_at <= as_of,
                )
                .order_by(UnderwritingObjectRelation.child_id)
                .limit(_OBJECT_SEARCH_CANDIDATE_CAP + 1)
            )
        )
        if len(direct_children) > _OBJECT_SEARCH_CANDIDATE_CAP:
            raise ValidationError("industry exposes too many Company relations")
        if any(child.kind != "company" for child in direct_children):
            raise ValidationError(
                "industry_exposes_company must directly reference a Company"
            )

        child_ids = {child.id for child in direct_children}
        effective_rows = (
            tuple(
                self._session.execute(
                    self._effective_object_statement(as_of).where(
                        UnderwritingResearchObject.id.in_(child_ids)
                    )
                )
            )
            if child_ids
            else ()
        )
        anchors = {
            research_object.id: (research_object, identity)
            for research_object, identity in effective_rows
        }
        if anchors:
            security_rows = self._effective_company_children_statement(
                set(anchors), as_of, require_relation_as_of=True
            ).subquery()
            overlapping_security_id = self._session.scalar(
                select(security_rows.c.id)
                .group_by(security_rows.c.id)
                .having(func.count(func.distinct(security_rows.c.parent_id)) > 1)
                .limit(1)
            )
            if overlapping_security_id is not None:
                raise ValidationError(
                    "Security belongs to multiple selected Industry Company groups"
                )
        return self._pack_industry_company_groups(anchors, as_of, limit)

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

        columns = _sqlite_unique_columns(exc, "uw_workspace_drafts")
        return columns == frozenset({"project_id"})

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
            self._flush_in_savepoint(row)
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
        base_revision_id: UUID | None | _UnchangedBaseRevision = (
            _UNCHANGED_BASE_REVISION
        ),
    ) -> UnderwritingWorkspaceDraft:
        """Atomically update a draft; publication may also reset its base."""
        if (
            not isinstance(updated_at, datetime)
            or updated_at.tzinfo is None
            or updated_at.utcoffset() is None
        ):
            raise ValidationError("updated_at must be a timezone-aware datetime")
        normalized_updated_at = updated_at.astimezone(UTC)
        if base_revision_id is not _UNCHANGED_BASE_REVISION:
            if base_revision_id is not None and type(base_revision_id) is not UUID:
                raise ValidationError(
                    "base_revision_id must be an exact UUID, None, or omitted"
                )
            if base_revision_id is not None:
                revision = self._session.get(
                    UnderwritingResearchVersion, base_revision_id
                )
                if revision is None or revision.project_id != project_id:
                    raise ValidationError(
                        "base_revision_id must reference a revision owned by the draft project"
                    )
        values: dict[str, object] = {
            "content": deepcopy(content),
            "lock_version": expected_lock_version + 1,
            "updated_at": normalized_updated_at,
        }
        if base_revision_id is not _UNCHANGED_BASE_REVISION:
            values["base_revision_id"] = base_revision_id
        result = self._session.execute(
            update(UnderwritingWorkspaceDraft)
            .where(
                UnderwritingWorkspaceDraft.project_id == project_id,
                UnderwritingWorkspaceDraft.lock_version == expected_lock_version,
                UnderwritingWorkspaceDraft.created_at <= normalized_updated_at,
                UnderwritingWorkspaceDraft.updated_at <= normalized_updated_at,
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
            row.security_id for row in self.project_memberships(project_id)
        )
        return project, security_ids

    def project_memberships(
        self, project_id: UUID
    ) -> tuple[UnderwritingResearchProjectSecurity, ...]:
        return tuple(
            self._session.scalars(
                select(UnderwritingResearchProjectSecurity)
                .where(UnderwritingResearchProjectSecurity.project_id == project_id)
                .order_by(UnderwritingResearchProjectSecurity.security_id)
            )
        )

    def list_projects(
        self, limit: int
    ) -> list[tuple[UnderwritingResearchProject, tuple[UUID, ...]]]:
        projects = list(
            self._session.scalars(
                select(UnderwritingResearchProject)
                .order_by(
                    UnderwritingResearchProject.created_at.desc(),
                    UnderwritingResearchProject.id.desc(),
                )
                .limit(limit)
            )
        )
        if not projects:
            return []
        memberships_by_project: dict[UUID, list[UUID]] = {
            project.id: [] for project in projects
        }
        statement = (
            select(
                UnderwritingResearchProjectSecurity.project_id,
                UnderwritingResearchProjectSecurity.security_id,
            )
            .where(
                UnderwritingResearchProjectSecurity.project_id.in_(
                    tuple(memberships_by_project)
                )
            )
            .order_by(
                UnderwritingResearchProjectSecurity.project_id,
                UnderwritingResearchProjectSecurity.security_id,
            )
        )
        for project_id, security_id in self._session.execute(statement):
            memberships_by_project[project_id].append(security_id)
        return [
            (project, tuple(memberships_by_project[project.id])) for project in projects
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
            UnderwritingPriceSnapshot.legacy_business_conflict.is_(False),
            UnderwritingPriceSnapshot.security_identity_id == security_identity_id,
            UnderwritingPriceSnapshot.price_type == price_type,
            UnderwritingPriceSnapshot.adjustment_basis == adjustment_basis,
            UnderwritingPriceSnapshot.market_at == market_at,
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
            UnderwritingFXSnapshot.legacy_business_conflict.is_(False),
            UnderwritingFXSnapshot.base_currency == base_currency,
            UnderwritingFXSnapshot.quote_currency == quote_currency,
            UnderwritingFXSnapshot.quote_direction == quote_direction,
            UnderwritingFXSnapshot.market_at == market_at,
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
            UnderwritingCapitalStructureSnapshot.legacy_business_conflict.is_(False),
            UnderwritingCapitalStructureSnapshot.company_id == company_id,
            UnderwritingCapitalStructureSnapshot.report_period_start
            == report_period_start,
            UnderwritingCapitalStructureSnapshot.report_period_end == report_period_end,
            UnderwritingCapitalStructureSnapshot.market_at == market_at,
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

    def lock_project(self, project_id: UUID) -> UnderwritingResearchProject:
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            driver_connection = connection.connection.driver_connection
            try:
                if not driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    self._session.execute(
                        update(UnderwritingWorkspaceDraft)
                        .where(UnderwritingWorkspaceDraft.project_id == project_id)
                        .values(lock_version=UnderwritingWorkspaceDraft.lock_version)
                        .execution_options(synchronize_session=False)
                    )
            except OperationalError as exc:
                message = str(getattr(exc, "orig", exc)).lower()
                if "locked" in message or "busy" in message:
                    raise ConflictError(
                        "research project publication lock is busy; retry"
                    ) from exc
                raise
        row = self._session.scalar(
            select(UnderwritingResearchProject)
            .where(UnderwritingResearchProject.id == project_id)
            .with_for_update()
        )
        if row is None:
            raise ValidationError("research project does not exist")
        return row

    def product_revision_head(
        self, project_id: UUID, *, lock: bool = False
    ) -> UnderwritingResearchVersion | None:
        statement = (
            select(UnderwritingResearchVersion)
            .where(
                UnderwritingResearchVersion.project_id == project_id,
                UnderwritingResearchVersion.version_kind == "independent_research",
            )
            .order_by(
                UnderwritingResearchVersion.sequence.desc(),
                UnderwritingResearchVersion.id.desc(),
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def manifest(self, manifest_id: UUID | None) -> UnderwritingRevisionManifest | None:
        if manifest_id is None:
            return None
        return self._session.get(UnderwritingRevisionManifest, manifest_id)

    def boundary(self, boundary_id: UUID | None) -> UnderwritingRevisionBoundary | None:
        if boundary_id is None:
            return None
        return self._session.get(UnderwritingRevisionBoundary, boundary_id)

    def assessment(
        self, assessment_id: UUID
    ) -> UnderwritingResearchAssessmentVersion | None:
        return self._session.get(UnderwritingResearchAssessmentVersion, assessment_id)

    def revision_for_idempotency(
        self, project_id: UUID, idempotency_key: str
    ) -> UnderwritingResearchVersion | None:
        return self._session.scalar(
            select(UnderwritingResearchVersion)
            .join(
                UnderwritingRevisionManifest,
                UnderwritingRevisionManifest.id
                == UnderwritingResearchVersion.manifest_id,
            )
            .where(
                UnderwritingRevisionManifest.project_id == project_id,
                UnderwritingRevisionManifest.idempotency_key == idempotency_key,
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def append_assessment(
        self,
        *,
        project_id: UUID,
        answerability: str,
        direction: str | None,
        confidence: str | None,
        publication_status: str,
        blockers: list[str],
        resolution_requirements: list[str],
        next_review_at: datetime | None,
        content_hash: str,
        expected_parent_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingResearchAssessmentVersion:
        head = self._session.scalar(
            select(UnderwritingResearchAssessmentVersion)
            .where(UnderwritingResearchAssessmentVersion.project_id == project_id)
            .order_by(
                UnderwritingResearchAssessmentVersion.version.desc(),
                UnderwritingResearchAssessmentVersion.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        actual_parent_id = head.id if head is not None else None
        self._require_expected_parent(actual_parent_id, expected_parent_id)
        row = UnderwritingResearchAssessmentVersion(
            project_id=project_id,
            version=1 if head is None else head.version + 1,
            supersedes_id=actual_parent_id,
            answerability=answerability,
            direction=direction,
            confidence=confidence,
            publication_status=publication_status,
            blockers=deepcopy(blockers),
            resolution_requirements=deepcopy(resolution_requirements),
            next_review_at=next_review_at,
            content_hash=content_hash,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush([row])
        return row

    def append_boundary(
        self,
        *,
        project_id: UUID,
        value: RevisionBoundaryInput,
        schema_version: str,
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingRevisionBoundary:
        row = UnderwritingRevisionBoundary(
            project_id=project_id,
            historical_basis_id=value.historical_basis_id,
            mandate_id=value.mandate_id,
            scope_id=value.scope_id,
            agenda_id=value.agenda_id,
            price_snapshot_ids=[str(item) for item in value.price_snapshot_ids],
            fx_snapshot_ids=[str(item) for item in value.fx_snapshot_ids],
            capital_structure_snapshot_id=value.capital_structure_snapshot_id,
            security_rights_ids=[str(item) for item in value.security_rights_ids],
            parent_revision_id=value.parent_revision_id,
            schema_version=schema_version,
            content_hash=content_hash,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush([row])
        return row

    def append_manifest(
        self,
        *,
        project_id: UUID,
        boundary_id: UUID,
        idempotency_key: str,
        manifest: dict[str, object],
        content_hash: str,
        created_at: datetime,
    ) -> UnderwritingRevisionManifest:
        row = UnderwritingRevisionManifest(
            project_id=project_id,
            boundary_id=boundary_id,
            idempotency_key=idempotency_key,
            manifest=deepcopy(manifest),
            content_hash=content_hash,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush([row])
        return row

    def append_product_revision(
        self,
        *,
        project_id: UUID,
        object_id: UUID | str,
        basis_id: UUID,
        boundary_id: UUID,
        manifest_id: UUID,
        manifest_hash: str,
        assessment_id: UUID,
        parent_revision_id: UUID | None,
        created_at: datetime,
    ) -> UnderwritingResearchVersion:
        if isinstance(object_id, str):
            try:
                object_id = UUID(object_id)
            except ValueError as exc:
                raise ValidationError("primary object reference is malformed") from exc
        head = self.product_revision_head(project_id, lock=True)
        actual_parent_id = head.id if head is not None else None
        if actual_parent_id != parent_revision_id:
            raise StaleParentError(_STALE_MESSAGE)
        sequence = 1 if head is None else head.sequence + 1
        content_hash = canonical_hash(
            {
                "schema_version": "product.research-revision.v1",
                "project_id": str(project_id),
                "object_id": str(object_id),
                "basis_id": str(basis_id),
                "version_kind": _PRODUCT_REVISION_KIND,
                "sequence": sequence,
                "boundary_id": str(boundary_id),
                "manifest_id": str(manifest_id),
                "manifest_hash": manifest_hash,
                "assessment_id": str(assessment_id),
                "parent_revision_id": (
                    str(parent_revision_id) if parent_revision_id is not None else None
                ),
                "publication_status": "user_frozen",
            }
        )
        row = UnderwritingResearchVersion(
            object_id=object_id,
            basis_id=basis_id,
            version_kind=_PRODUCT_REVISION_KIND,
            sequence=sequence,
            content_hash=content_hash,
            parent_ids=(
                [str(parent_revision_id)] if parent_revision_id is not None else []
            ),
            supersedes_id=parent_revision_id,
            project_id=project_id,
            boundary_id=boundary_id,
            manifest_id=manifest_id,
            manifest_schema=_PRODUCT_MANIFEST_SCHEMA,
            publication_status="user_frozen",
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush([row])
        return row

    def reset_draft_after_publish(
        self,
        *,
        project_id: UUID,
        expected_lock_version: int,
        base_revision_id: UUID,
        updated_at: datetime,
    ) -> UnderwritingWorkspaceDraft:
        current = self.workspace_draft(project_id)
        if current is None:
            raise ValidationError("workspace draft does not exist for project")
        return self.compare_and_swap_workspace_draft(
            project_id=project_id,
            expected_lock_version=expected_lock_version,
            content=deepcopy(current.content),
            updated_at=updated_at,
            base_revision_id=base_revision_id,
        )
