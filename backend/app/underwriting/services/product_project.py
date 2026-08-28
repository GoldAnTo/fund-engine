"""Product-level identity, project, scope, agenda, mandate, and basis use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    PRODUCT_HISTORICAL_BASIS_SCHEMA,
    ProductHistoricalBasisInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    product_historical_basis_content_hash,
)
from app.underwriting.domain.search_terms import normalize_search_term
from app.underwriting.domain.types import InvestmentMandateInput, ResearchObjectKind
from app.underwriting.persistence.product_models import (
    UnderwritingObjectIdentityVersion,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash


_SUPPORTED_PRODUCT_CURRENCIES = frozenset({"CNY", "USD"})
_INDUSTRY_COMPANY_RELATION = "industry_exposes_company"
_COMPANY_SECURITY_RELATION = "company_has_security"
# Product returns are fractions. A required excess return in [0, 1) permits
# ordinary hurdle rates while rejecting percentages accidentally supplied as 3.
_MAX_REQUIRED_EXCESS_RETURN = Decimal("1")
_MANDATE_NUMERIC_QUANTUM = Decimal("0.00000001")
_MANDATE_NUMERIC_ZERO = Decimal("0.00000000")


@dataclass(frozen=True, slots=True)
class ObjectSearchResult:
    object_id: UUID
    identity_version_id: UUID
    kind: ResearchObjectKind
    external_key: str
    canonical_name: str
    symbol: str | None
    exchange: str | None
    share_class: str | None
    trading_currency: str | None


@dataclass(frozen=True, slots=True)
class ProjectCompanyIdentityView:
    object_id: UUID
    identity_version_id: UUID
    canonical_name: str


@dataclass(frozen=True, slots=True)
class ProjectSecurityIdentityView(ProjectCompanyIdentityView):
    symbol: str
    exchange: str
    share_class: str
    trading_currency: str


@dataclass(frozen=True, slots=True)
class ResearchProjectView:
    id: UUID
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...]
    company_identity: ProjectCompanyIdentityView | None
    security_identities: tuple[ProjectSecurityIdentityView, ...]
    content_hash: str
    created_at: datetime


class ResearchProjectService:
    """Validate and append the long-lived product project foundation."""

    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._repository = ProductRepository(session)
        self._now = now

    @staticmethod
    def _uuid(value: UUID, field: str) -> UUID:
        if type(value) is not UUID:
            raise ValidationError(f"{field} must be a UUID")
        return value

    @staticmethod
    def _text(value: object, field: str) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValidationError(f"{field} must not be empty")
        return normalized

    @staticmethod
    def _utc(value: datetime, field: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"{field} must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _finite_decimal(value: object, field: str) -> Decimal:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValidationError(f"{field} must be a finite Decimal")
        return value

    @classmethod
    def _mandate_decimal(cls, value: object, field: str) -> Decimal:
        decimal_value = cls._finite_decimal(value, field)
        try:
            normalized = decimal_value.quantize(_MANDATE_NUMERIC_QUANTUM)
        except InvalidOperation as exc:
            raise ValidationError(f"{field} must fit 8 decimal places") from exc
        if normalized != decimal_value:
            raise ValidationError(f"{field} must fit 8 decimal places without rounding")
        if normalized.is_zero():
            return _MANDATE_NUMERIC_ZERO
        return normalized

    def _created_at(self) -> datetime:
        return self._utc(self._now(), "clock")

    @classmethod
    def _normalized_string_set(
        cls, values: tuple[str, ...], field: str
    ) -> tuple[str, ...]:
        normalized = tuple(sorted(cls._text(value, field) for value in values))
        if len(normalized) != len(set(normalized)):
            raise ValidationError(f"{field} must not contain duplicates")
        return normalized

    @staticmethod
    def _as_conflict(operation: Callable[[], object]):
        try:
            return operation()
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc

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
        expected_parent_id: UUID | None,
    ) -> UnderwritingObjectIdentityVersion:
        object_id = self._uuid(object_id, "object_id")
        research_object = self._repository.object(object_id)
        if research_object is None:
            raise ValidationError("research object does not exist")
        canonical_name = self._text(canonical_name, "canonical_name")
        normalized_from = self._utc(effective_from, "effective_from")
        normalized_to = (
            self._utc(effective_to, "effective_to")
            if effective_to is not None
            else None
        )
        if normalized_to is not None and normalized_to <= normalized_from:
            raise ValidationError("effective_to must be later than effective_from")

        if research_object.kind == ResearchObjectKind.SECURITY.value:
            symbol = self._text(symbol, "symbol")
            exchange = self._text(exchange, "exchange")
            share_class = self._text(share_class, "share_class")
            trading_currency = self._text(trading_currency, "trading_currency").upper()
            if trading_currency not in _SUPPORTED_PRODUCT_CURRENCIES:
                raise ValidationError(
                    "trading_currency must be a supported CNY/USD currency"
                )
        elif research_object.kind in (
            ResearchObjectKind.COMPANY.value,
            ResearchObjectKind.INDUSTRY.value,
        ):
            if any(
                value is not None
                for value in (symbol, exchange, share_class, trading_currency)
            ):
                raise ValidationError(
                    "non-Security identity must not contain security fields"
                )
            symbol = exchange = share_class = trading_currency = None
        else:  # Defensive against rows created outside the constrained schema.
            raise ValidationError("research object kind is invalid")

        head = self._repository.identity_head(object_id)
        if head is not None and head.id == expected_parent_id:
            parent_from = self._stored_utc(head.effective_from)
            if normalized_from <= parent_from:
                raise ValidationError("identity successor effective_from must advance")
            if head.effective_to is not None and normalized_from < self._stored_utc(
                head.effective_to
            ):
                raise ValidationError(
                    "identity successor effective interval overlaps or regresses"
                )

        payload = {
            "schema_version": "product.object-identity.v1",
            "object_id": str(object_id),
            "kind": research_object.kind,
            "canonical_name": canonical_name,
            "symbol": symbol,
            "exchange": exchange,
            "share_class": share_class,
            "trading_currency": trading_currency,
            "effective_from": normalized_from.isoformat(),
            "effective_to": normalized_to.isoformat() if normalized_to else None,
        }
        return self._as_conflict(
            lambda: self._repository.append_identity_version(
                object_id=object_id,
                canonical_name=canonical_name,
                symbol=symbol,
                exchange=exchange,
                share_class=share_class,
                trading_currency=trading_currency,
                effective_from=normalized_from,
                effective_to=normalized_to,
                content_hash=canonical_hash(payload),
                expected_parent_id=expected_parent_id,
                created_at=self._created_at(),
            )
        )

    def effective_identity(
        self, object_id: UUID, as_of: datetime
    ) -> UnderwritingObjectIdentityVersion | None:
        object_id = self._uuid(object_id, "object_id")
        normalized_as_of = self._utc(as_of, "as_of")
        if self._repository.object(object_id) is None:
            return None
        return self._repository.effective_identity(object_id, normalized_as_of)

    def search_objects(
        self,
        query: str,
        as_of: datetime,
        limit: int = 20,
    ) -> tuple[ObjectSearchResult, ...]:
        raw_query = self._text(query, "query")
        normalized_query = normalize_search_term(raw_query)
        normalized_as_of = self._utc(as_of, "as_of")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValidationError("limit must be between 1 and 100")
        outcome = self._repository.search_objects(
            raw_query,
            normalized_as_of,
            limit,
        )
        if (
            not outcome.rows
            and not outcome.had_raw_match
            and normalized_query.endswith("公司")
            and len(normalized_query) > len("公司")
        ):
            outcome = self._repository.search_objects(
                normalized_query[: -len("公司")].rstrip(),
                normalized_as_of,
                limit,
            )
        return tuple(
            ObjectSearchResult(
                object_id=research_object.id,
                identity_version_id=identity.id,
                kind=ResearchObjectKind(research_object.kind),
                external_key=research_object.external_key,
                canonical_name=identity.canonical_name,
                symbol=identity.symbol,
                exchange=identity.exchange,
                share_class=identity.share_class,
                trading_currency=identity.trading_currency,
            )
            for research_object, identity in outcome.rows
        )

    def industry_companies(
        self,
        industry_id: UUID,
        as_of: datetime,
        limit: int = 20,
    ) -> tuple[ObjectSearchResult, ...] | None:
        industry_id = self._uuid(industry_id, "industry_id")
        normalized_as_of = self._utc(as_of, "as_of")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValidationError("limit must be between 1 and 100")
        rows = self._repository.industry_company_groups(
            industry_id, normalized_as_of, limit
        )
        if rows is None:
            return None
        return tuple(
            ObjectSearchResult(
                object_id=research_object.id,
                identity_version_id=identity.id,
                kind=ResearchObjectKind(research_object.kind),
                external_key=research_object.external_key,
                canonical_name=identity.canonical_name,
                symbol=identity.symbol,
                exchange=identity.exchange,
                share_class=identity.share_class,
                trading_currency=identity.trading_currency,
            )
            for research_object, identity in rows
        )

    def _project_view(self, record) -> ResearchProjectView:
        project, security_ids = record
        created_at = self._stored_utc(project.created_at)
        company_identity = self._repository.effective_identity(
            project.primary_company_id, created_at
        )
        security_identities = tuple(
            self._repository.effective_identity(security_id, created_at)
            for security_id in sorted(security_ids, key=str)
        )
        if any(
            not value.symbol
            or not value.exchange
            or not value.share_class
            or not value.trading_currency
            for value in security_identities
            if value is not None
        ):
            raise ValidationError("project Security identity snapshot is incomplete")
        return ResearchProjectView(
            id=project.id,
            primary_company_id=project.primary_company_id,
            target_security_ids=tuple(sorted(security_ids, key=str)),
            company_identity=(
                ProjectCompanyIdentityView(
                    object_id=company_identity.object_id,
                    identity_version_id=company_identity.id,
                    canonical_name=company_identity.canonical_name,
                )
                if company_identity is not None
                else None
            ),
            security_identities=tuple(
                ProjectSecurityIdentityView(
                    object_id=value.object_id,
                    identity_version_id=value.id,
                    canonical_name=value.canonical_name,
                    symbol=value.symbol,
                    exchange=value.exchange,
                    share_class=value.share_class,
                    trading_currency=value.trading_currency,
                )
                for value in security_identities
                if value is not None
            ),
            content_hash=project.content_hash,
            created_at=created_at,
        )

    def create_project(
        self,
        primary_company_id: UUID,
        target_security_ids: tuple[UUID, ...],
    ) -> ResearchProjectView:
        primary_company_id = self._uuid(primary_company_id, "primary_company_id")
        if not isinstance(target_security_ids, tuple) or not target_security_ids:
            raise ValidationError("target_security_ids must be a nonempty tuple")
        if not all(type(security_id) is UUID for security_id in target_security_ids):
            raise ValidationError("target_security_ids must contain UUIDs")
        if len(target_security_ids) != len(set(target_security_ids)):
            raise ValidationError("target_security_ids must not contain duplicates")

        company = self._repository.object(primary_company_id)
        if company is None:
            raise ValidationError("primary company does not exist")
        if company.kind != ResearchObjectKind.COMPANY.value:
            raise ValidationError("primary_company_id must identify a Company")

        normalized_security_ids = tuple(sorted(target_security_ids, key=str))
        for security_id in normalized_security_ids:
            security = self._repository.object(security_id)
            if security is None:
                raise ValidationError("target Security does not exist")
            if security.kind != ResearchObjectKind.SECURITY.value:
                raise ValidationError("target_security_ids must identify Securities")
            if not self._repository.relation_exists(
                parent_id=primary_company_id,
                child_id=security_id,
                relation_type=_COMPANY_SECURITY_RELATION,
            ):
                raise ValidationError(
                    "target Security must have a company_has_security relation "
                    "from the primary Company"
                )

        project_id = uuid4()
        content_hash = canonical_hash(
            {
                "schema_version": "product.research-project.v1",
                "primary_company_id": str(primary_company_id),
                "target_security_ids": [
                    str(value) for value in normalized_security_ids
                ],
            }
        )
        membership_hashes = {
            security_id: canonical_hash(
                {
                    "schema_version": "product.research-project-security.v1",
                    "project_id": str(project_id),
                    "security_id": str(security_id),
                }
            )
            for security_id in normalized_security_ids
        }
        created_at = self._created_at()
        project = self._repository.create_project(
            project_id=project_id,
            primary_company_id=primary_company_id,
            target_security_ids=normalized_security_ids,
            content_hash=content_hash,
            membership_hashes=membership_hashes,
            created_at=created_at,
        )
        return self._project_view((project, normalized_security_ids))

    def project(self, project_id: UUID) -> ResearchProjectView | None:
        project_id = self._uuid(project_id, "project_id")
        record = self._repository.project(project_id)
        return self._project_view(record) if record is not None else None

    def list_projects(self, limit: int = 20) -> tuple[ResearchProjectView, ...]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValidationError("limit must be between 1 and 100")
        return tuple(
            self._project_view(record)
            for record in self._repository.list_projects(limit)
        )

    def product_mandate(self, project_id: UUID, mandate_id: UUID):
        project_id = self._uuid(project_id, "project_id")
        mandate_id = self._uuid(mandate_id, "mandate_id")
        return self._repository.product_mandate(project_id, mandate_id)

    def append_product_mandate(
        self,
        *,
        project_id: UUID,
        value: InvestmentMandateInput,
        benchmark_key: str | None,
        required_excess_return: Decimal | None,
        effective_at: datetime,
        expires_at: datetime | None,
        expected_parent_id: UUID | None,
    ):
        project_id = self._uuid(project_id, "project_id")
        if self._repository.project(project_id) is None:
            raise ValidationError("project does not exist")
        if type(value) is not InvestmentMandateInput:
            raise ValidationError("value must be an InvestmentMandateInput")
        if value.horizon_years not in (3, 4, 5):
            raise ValidationError("horizon_years must be one of 3, 4, 5")
        base_currency = self._text(value.base_currency, "base_currency").upper()
        if base_currency not in _SUPPORTED_PRODUCT_CURRENCIES:
            raise ValidationError("base_currency must be a supported CNY/USD currency")
        required_return = self._mandate_decimal(
            value.required_return, "required_return"
        )
        if not Decimal("0") <= required_return < Decimal("1"):
            raise ValidationError("required_return must be in [0, 1)")
        permanent_loss_limit = self._mandate_decimal(
            value.permanent_loss_limit, "permanent_loss_limit"
        )
        if not Decimal("0") <= permanent_loss_limit <= Decimal("1"):
            raise ValidationError("permanent_loss_limit must be in [0, 1]")
        if not isinstance(value.comparison_set, tuple) or not value.comparison_set:
            raise ValidationError("comparison_set must be a nonempty tuple")
        comparison_set = self._normalized_string_set(
            value.comparison_set, "comparison_set item"
        )

        if (benchmark_key is None) != (required_excess_return is None):
            raise ValidationError(
                "benchmark_key and required_excess_return must both be present or absent"
            )
        normalized_benchmark = (
            self._text(benchmark_key, "benchmark_key")
            if benchmark_key is not None
            else None
        )
        normalized_excess = None
        if required_excess_return is not None:
            normalized_excess = self._mandate_decimal(
                required_excess_return, "required_excess_return"
            )
            if not Decimal("0") <= normalized_excess < _MAX_REQUIRED_EXCESS_RETURN:
                raise ValidationError("required_excess_return must be in [0, 1)")

        normalized_effective = self._utc(effective_at, "effective_at")
        normalized_expires = (
            self._utc(expires_at, "expires_at") if expires_at is not None else None
        )
        if (
            normalized_expires is not None
            and normalized_expires <= normalized_effective
        ):
            raise ValidationError("expires_at must be later than effective_at")
        head = self._repository.mandate_head(project_id)
        if head is not None and head.id == expected_parent_id:
            if normalized_effective <= self._stored_utc(head.effective_at):
                raise ValidationError("mandate effective_at must advance")

        mandate_key = f"product.project:{project_id}"
        payload = {
            "schema_version": "product.investment-mandate.v1",
            "project_id": str(project_id),
            "mandate_key": mandate_key,
            "horizon_years": value.horizon_years,
            "base_currency": base_currency,
            "required_return": format(required_return, ".8f"),
            "permanent_loss_limit": format(permanent_loss_limit, ".8f"),
            "comparison_set": list(comparison_set),
            "benchmark_key": normalized_benchmark,
            "required_excess_return": (
                format(normalized_excess, ".8f")
                if normalized_excess is not None
                else None
            ),
            "effective_at": normalized_effective.isoformat(),
            "expires_at": (
                normalized_expires.isoformat()
                if normalized_expires is not None
                else None
            ),
        }
        return self._as_conflict(
            lambda: self._repository.append_product_mandate(
                project_id=project_id,
                mandate_key=mandate_key,
                horizon_years=value.horizon_years,
                base_currency=base_currency,
                required_return=required_return,
                permanent_loss_limit=permanent_loss_limit,
                comparison_set=comparison_set,
                benchmark_key=normalized_benchmark,
                required_excess_return=normalized_excess,
                effective_at=normalized_effective,
                expires_at=normalized_expires,
                content_hash=canonical_hash(payload),
                expected_parent_id=expected_parent_id,
                created_at=self._created_at(),
            )
        )

    def append_scope(
        self,
        project_id: UUID,
        value: ResearchScopeInput,
        expected_parent_id: UUID | None,
    ):
        project_id = self._uuid(project_id, "project_id")
        if type(value) is not ResearchScopeInput:
            raise ValidationError("value must be a ResearchScopeInput")
        record = self._repository.project(project_id)
        if record is None:
            raise ValidationError("project does not exist")
        project, project_security_ids = record
        if value.primary_company_id != project.primary_company_id:
            raise ValidationError("scope primary Company must equal project Company")
        targets = tuple(sorted(value.target_security_ids, key=str))
        if not targets or not set(targets).issubset(project_security_ids):
            raise ValidationError(
                "scope target Securities must be a nonempty subset of project Securities"
            )

        industries = tuple(sorted(value.industry_ids, key=str))
        for industry_id in industries:
            industry = self._repository.object(industry_id)
            if industry is None or industry.kind != ResearchObjectKind.INDUSTRY.value:
                raise ValidationError("scope industry_ids must identify Industries")
            if not self._repository.relation_exists(
                parent_id=industry_id,
                child_id=project.primary_company_id,
                relation_type=_INDUSTRY_COMPANY_RELATION,
            ):
                raise ValidationError(
                    "scope Industry must relate to the Company through "
                    "industry_exposes_company"
                )

        covered_segments = self._normalized_string_set(
            value.covered_segments, "covered_segments item"
        )
        exclusions = self._normalized_string_set(value.exclusions, "exclusions item")
        user_focus = value.user_focus.strip() if value.user_focus is not None else None
        payload: dict[str, object] = {
            "primary_company_id": str(project.primary_company_id),
            "target_security_ids": [str(target) for target in targets],
            "industry_ids": [str(industry) for industry in industries],
            "covered_segments": list(covered_segments),
            "user_focus": user_focus,
            "exclusions": list(exclusions),
        }
        return self._as_conflict(
            lambda: self._repository.append_scope(
                project_id=project_id,
                payload=payload,
                content_hash=canonical_hash(
                    {
                        "schema_version": "product.research-scope.v1",
                        "project_id": str(project_id),
                        "scope": payload,
                    }
                ),
                expected_parent_id=expected_parent_id,
                created_at=self._created_at(),
            )
        )

    def scope(self, project_id: UUID, scope_id: UUID):
        project_id = self._uuid(project_id, "project_id")
        scope_id = self._uuid(scope_id, "scope_id")
        return self._repository.scope(project_id, scope_id)

    def append_agenda(
        self,
        project_id: UUID,
        value: ResearchAgendaInput,
        expected_parent_id: UUID | None,
    ):
        project_id = self._uuid(project_id, "project_id")
        if type(value) is not ResearchAgendaInput:
            raise ValidationError("value must be a ResearchAgendaInput")
        if self._repository.project(project_id) is None:
            raise ValidationError("project does not exist")
        scope = self._repository.scope_by_id(value.scope_id)
        if scope is None:
            raise ValidationError("agenda scope does not exist")
        if scope.project_id != project_id:
            raise ValidationError("agenda scope must belong to the same project")

        generator = value.generator
        provenance: dict[str, object] = {
            "method": generator.method.value,
            "template_key": generator.template_key,
            "template_version": generator.template_version,
            "model_name": generator.model_name,
            "prompt_template_version": generator.prompt_template_version,
            "input_summary_hash": generator.input_summary_hash,
            "output_hash": generator.output_hash,
        }
        items = list(value.items)
        payload: dict[str, object] = {"items": items}
        return self._as_conflict(
            lambda: self._repository.append_agenda(
                project_id=project_id,
                scope_id=scope.id,
                payload=payload,
                generator_provenance=provenance,
                content_hash=canonical_hash(
                    {
                        "schema_version": "product.research-agenda.v1",
                        "project_id": str(project_id),
                        "scope_id": str(scope.id),
                        "items": items,
                        "generator": provenance,
                    }
                ),
                expected_parent_id=expected_parent_id,
                created_at=self._created_at(),
            )
        )

    def agenda(self, project_id: UUID, agenda_id: UUID):
        project_id = self._uuid(project_id, "project_id")
        agenda_id = self._uuid(agenda_id, "agenda_id")
        return self._repository.agenda(project_id, agenda_id)

    def create_historical_basis(self, value: ProductHistoricalBasisInput):
        if type(value) is not ProductHistoricalBasisInput:
            raise ValidationError("value must be a ProductHistoricalBasisInput")
        cutoff = self._utc(value.cutoff_at, "cutoff_at")
        content_hash = product_historical_basis_content_hash(value)
        return self._repository.create_product_basis(
            cutoff=cutoff,
            source_manifest_hash=value.source_manifest_hash,
            definition_bundle_hash=value.definition_bundle_hash,
            parser_bundle_hash=value.parser_bundle_hash,
            boundary_schema_version=PRODUCT_HISTORICAL_BASIS_SCHEMA,
            content_hash=content_hash,
            created_at=self._created_at(),
        )

    def historical_basis(self, basis_id: UUID):
        basis_id = self._uuid(basis_id, "basis_id")
        return self._repository.product_basis(basis_id)
