"""High-level, caller-transaction initialization for company research."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.domain.company_research import (
    CompanyResearchCompany,
    CompanyResearchIdentitySet,
    CompanyResearchPreview,
    CompanyResearchSecurity,
    build_company_research_preview,
)
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import InvestmentMandateInput, ResearchObjectKind
from app.underwriting.persistence.company_research_models import (
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.models import (
    UnderwritingMandateVersion,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchScopeVersion,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.product_project import (
    ResearchProjectService,
    ResearchProjectView,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftService,
    WorkspaceDraftView,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.services.company_research_market_inputs import (
    CompanyResearchMarketInputs,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelTemplate,
    FrozenMarketContext,
    StrategyAssumptionSet,
)

_PREPARE_JOB_KIND = "prepare_company_research"
_PREPARE_JOB_TARGET_TYPE = "company_research_preparation"
_AGENDA_TEMPLATE_KEY = "company-research-default"


@dataclass(frozen=True, slots=True)
class CompanyResearchInitialization:
    """The durable foundation returned by a successful initialization."""

    project: ResearchProjectView
    mandate: UnderwritingMandateVersion
    scope: UnderwritingResearchScopeVersion
    agenda: UnderwritingResearchAgendaVersion
    draft: WorkspaceDraftView
    preparation: CompanyResearchPreparation
    job: Job


@dataclass(frozen=True, slots=True)
class CompanyResearchGovernedInputs:
    """Internal typed seam; callers never submit market UUID/hash forms."""

    model_template: CompanyResearchModelTemplate
    strategy_assumptions: StrategyAssumptionSet
    market_context: FrozenMarketContext | None


@dataclass(frozen=True, slots=True)
class CompanyResearchProjectStatus:
    """The user-facing operational state attached to one project."""

    project: ResearchProjectView
    preparation: CompanyResearchPreparation


class CompanyResearchInitializer:
    """Create one complete company-research foundation without exposing internals."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._products = ResearchProjectService(session, now=now)
        self._product_repository = ProductRepository(session)
        self._company_repository = CompanyResearchRepository(session)
        self._drafts = WorkspaceDraftService(session, now=now)
        self._adapters = (AlphabetCompanyResearchAdapter(),)

    @staticmethod
    def _uuid(value: object, field: str) -> UUID:
        if type(value) is not UUID:
            raise ValidationError(f"{field} must be a UUID")
        return value

    @staticmethod
    def _utc(value: object, field: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"{field} must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _text(value: object, field: str) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValidationError(f"{field} must not be empty")
        return normalized

    def _created_at(self) -> datetime:
        return self._utc(self._now(), "clock")

    def _adapter_for(self, company_external_key: str):
        for adapter in self._adapters:
            if adapter.supports(company_external_key):
                return adapter
        raise ValidationError("Company is not supported for company research")

    def _preview(
        self, *, company_id: UUID, cutoff_at: datetime
    ) -> CompanyResearchPreview:
        company_id = self._uuid(company_id, "company_id")
        cutoff = self._utc(cutoff_at, "cutoff_at")
        # A preview is a pure read even when the caller has pending objects.
        with self._session.no_autoflush:
            company = self._product_repository.object(company_id)
            if company is None or company.kind != ResearchObjectKind.COMPANY.value:
                raise ValidationError("company_id must identify a Company")
            company_identity = self._products.effective_identity(company_id, cutoff)
            if company_identity is None:
                raise ValidationError("Company has no effective identity at cutoff_at")
            rows = tuple(
                self._session.execute(
                    self._product_repository._effective_company_children_statement(
                        {company_id}, cutoff
                    ).order_by("parent_id", "id")
                ).tuples()
            )
            if not rows:
                raise ValidationError("Company has no effective related Securities")
            securities = tuple(
                CompanyResearchSecurity(
                    object_id=security.id,
                    company_id=company_id,
                    external_key=security.external_key,
                    canonical_name=identity.canonical_name,
                    symbol=identity.symbol,
                    exchange=identity.exchange,
                    share_class=identity.share_class,
                    trading_currency=identity.trading_currency,
                )
                for _parent_id, security, identity in rows
            )
            identities = CompanyResearchIdentitySet(
                company=CompanyResearchCompany(
                    object_id=company_id,
                    external_key=company.external_key,
                    canonical_name=company_identity.canonical_name,
                ),
                securities=securities,
            )
            return build_company_research_preview(
                adapter=self._adapter_for(company.external_key),
                identities=identities,
                cutoff_at=cutoff,
            )

    def preview(
        self,
        *,
        company_id: UUID,
        cutoff_at: datetime,
    ) -> CompanyResearchPreview:
        return self._preview(company_id=company_id, cutoff_at=cutoff_at)

    def _reserve_initialization(self, company_id: UUID) -> None:
        """Serialize same-company initialization without owning the transaction."""
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            raw = getattr(
                connection.connection, "driver_connection", connection.connection
            )
            if not raw.in_transaction:
                try:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                except OperationalError as exc:
                    raise ConflictError(
                        "company research initialization is concurrent"
                    ) from exc
            return
        # On PostgreSQL this locks the Company row until the caller commits.
        self._session.scalar(
            select(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.id == company_id)
            .with_for_update()
        )

    def _existing_result(
        self, preparation: CompanyResearchPreparation
    ) -> CompanyResearchInitialization:
        project = self._products.project(preparation.project_id)
        if project is None:
            raise ConflictError("company research preparation project is missing")
        draft = self._drafts.read(project.id)
        if draft is None or None in (
            draft.content.mandate_id,
            draft.content.scope_id,
            draft.content.agenda_id,
        ):
            raise ConflictError(
                "company research initialization foundation is incomplete"
            )
        mandate = self._products.product_mandate(project.id, draft.content.mandate_id)
        scope = self._products.scope(project.id, draft.content.scope_id)
        agenda = self._products.agenda(project.id, draft.content.agenda_id)
        job = self._company_repository.prepare_job(preparation.id)
        if mandate is None or scope is None or agenda is None or job is None:
            raise ConflictError(
                "company research initialization foundation is incomplete"
            )
        return CompanyResearchInitialization(
            project=project,
            mandate=mandate,
            scope=scope,
            agenda=agenda,
            draft=draft,
            preparation=preparation,
            job=job,
        )

    @staticmethod
    def _agenda_items(preview: CompanyResearchPreview) -> tuple[str, ...]:
        return tuple(
            module.key
            for module in (*preview.generic_modules, *preview.business_modules)
        )

    def _initialize(
        self,
        *,
        preview_hash: str,
        company_id: UUID,
        cutoff_at: datetime,
        idempotency_key: str,
    ) -> CompanyResearchInitialization:
        idempotency_key = self._text(idempotency_key, "idempotency_key")
        preview = self._preview(company_id=company_id, cutoff_at=cutoff_at)
        if preview_hash != preview.input_hash:
            raise ValidationError("preview_hash does not match the current preview")
        self._reserve_initialization(company_id)

        existing_by_key = self._company_repository.preparation_by_idempotency_key(
            idempotency_key
        )
        if existing_by_key is not None:
            if existing_by_key.request_hash != preview.input_hash:
                raise ConflictError(
                    "idempotency_key is already bound to another request"
                )
            return self._existing_result(existing_by_key)

        existing_by_request = self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.request_hash == preview.input_hash)
            .with_for_update()
            .limit(1)
        )
        if existing_by_request is not None:
            raise ConflictError("company research request is already initialized")

        with self._session.begin_nested():
            return self._create_initialization(
                preview=preview,
                idempotency_key=idempotency_key,
            )

    def _create_initialization(
        self,
        *,
        preview: CompanyResearchPreview,
        idempotency_key: str,
    ) -> CompanyResearchInitialization:
        project = self._products.create_project(
            primary_company_id=preview.company.object_id,
            target_security_ids=tuple(
                security.object_id for security in preview.securities
            ),
        )
        mandate = self._products.append_product_mandate(
            project_id=project.id,
            value=InvestmentMandateInput(
                mandate_key="company-research-default",
                horizon_years=5,
                base_currency="CNY",
                required_return=Decimal("0.12"),
                permanent_loss_limit=Decimal("0.25"),
                comparison_set=("absolute_intrinsic_value",),
            ),
            benchmark_key=None,
            required_excess_return=None,
            effective_at=preview.cutoff_at,
            expires_at=None,
            expected_parent_id=None,
        )
        scope = self._products.append_scope(
            project.id,
            ResearchScopeInput(
                primary_company_id=preview.company.object_id,
                target_security_ids=tuple(
                    security.object_id for security in preview.securities
                ),
                industry_ids=(),
                covered_segments=(),
                user_focus=None,
                exclusions=(),
            ),
            expected_parent_id=None,
        )
        agenda_items = self._agenda_items(preview)
        agenda = self._products.append_agenda(
            project.id,
            ResearchAgendaInput(
                scope_id=scope.id,
                items=agenda_items,
                generator=AgendaGeneratorInput(
                    method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
                    template_key=_AGENDA_TEMPLATE_KEY,
                    template_version=preview.strategy_version,
                    model_name=None,
                    prompt_template_version=None,
                    input_summary_hash=canonical_hash(preview.canonical_payload()),
                    output_hash=agenda_items_hash(agenda_items),
                ),
            ),
            expected_parent_id=None,
        )
        draft = self._drafts.create(
            project.id,
            initial_content=WorkspaceDraftContent(
                mandate_id=mandate.id,
                scope_id=scope.id,
                agenda_id=agenda.id,
            ),
        )
        preparation_id = uuid4()
        job = Job(
            id=uuid4(),
            kind=_PREPARE_JOB_KIND,
            status="queued",
            progress=0,
            attempt=1,
            step="evidence_index",
            target_type=_PREPARE_JOB_TARGET_TYPE,
            target_id=preparation_id,
            research_case_id=None,
            created_at=self._created_at(),
        )
        self._session.add(job)
        self._session.flush([job])
        preparation = self._company_repository.add_preparation(
            project_id=project.id,
            idempotency_key=idempotency_key,
            request_hash=preview.input_hash,
            strategy_version=preview.strategy_version,
            status="queued",
            current_step="evidence_index",
            progress=0,
            attempt=1,
            next_attempt_at=None,
            last_error_code=None,
            job_id=job.id,
            created_at=self._created_at(),
            updated_at=self._created_at(),
        )
        self._company_repository.append_event(
            preparation_id=preparation.id,
            event_type="initialized",
            payload={"request_hash": preview.input_hash},
            created_at=self._created_at(),
        )
        return CompanyResearchInitialization(
            project=project,
            mandate=mandate,
            scope=scope,
            agenda=agenda,
            draft=draft,
            preparation=preparation,
            job=job,
        )

    def initialize(
        self,
        *,
        preview_hash: str,
        company_id: UUID,
        cutoff_at: datetime,
        idempotency_key: str,
    ) -> CompanyResearchInitialization:
        return self._initialize(
            preview_hash=preview_hash,
            company_id=company_id,
            cutoff_at=cutoff_at,
            idempotency_key=idempotency_key,
        )

    def governed_inputs(
        self,
        *,
        project_id: UUID,
        cutoff_at: datetime,
    ) -> CompanyResearchGovernedInputs:
        """Prepare/resolve only authenticated internal inputs for model compilation."""
        project_id = self._uuid(project_id, "project_id")
        cutoff = self._utc(cutoff_at, "cutoff_at")
        project = self._products.project(project_id)
        if project is None:
            raise ValidationError("company research project not found")
        company = self._product_repository.object(project.primary_company_id)
        if company is None:
            raise ValidationError("company research project Company is missing")
        adapter = self._adapter_for(company.external_key)
        fixture = load_alphabet_golden_case_fixture()
        if fixture.cutoff != cutoff:
            raise ValidationError("governed inputs require the exact fixture cutoff")
        strategy = adapter.strategy_assumptions(fixture.strategy_assumptions)
        resolver = CompanyResearchMarketInputs(self._session, now=self._now)
        if fixture.market_inputs is not None:
            market_context = resolver.prepare(
                project_id=project_id,
                cutoff_at=cutoff,
                market_inputs=fixture.market_inputs,
            )
        else:
            try:
                market_context = resolver.resolve(
                    project_id=project_id,
                    cutoff_at=cutoff,
                )
            except ValidationError as exc:
                if "market inputs are incomplete" not in str(exc):
                    raise
                market_context = None
        return CompanyResearchGovernedInputs(
            model_template=adapter.model_template(),
            strategy_assumptions=strategy,
            market_context=market_context,
        )


class CompanyResearchPreparationService:
    """Read and recover the preparation created by the high-level initializer."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._products = ResearchProjectService(session, now=now)
        self._company_repository = CompanyResearchRepository(session)

    def _now_utc(self) -> datetime:
        return CompanyResearchInitializer._utc(self._now(), "clock")

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        """SQLite returns timezone columns as naive values; they are stored UTC."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def status(self, *, project_id: UUID) -> CompanyResearchProjectStatus:
        project_id = CompanyResearchInitializer._uuid(project_id, "project_id")
        project = self._products.project(project_id)
        if project is None:
            raise ValidationError("company research project not found")
        preparation = self._company_repository.preparation_for_project(project_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        return CompanyResearchProjectStatus(project=project, preparation=preparation)

    def retry(self, *, project_id: UUID) -> CompanyResearchProjectStatus:
        current = self.status(project_id=project_id)
        if (
            current.preparation.next_attempt_at is not None
            and self._stored_utc(current.preparation.next_attempt_at) > self._now_utc()
        ):
            raise ValidationError("company research preparation is not ready to retry")
        preparation = self._company_repository.requeue_recoverable_preparation(
            current.preparation.id,
            updated_at=self._now_utc(),
        )
        self._company_repository.append_event(
            preparation_id=preparation.id,
            event_type="retry_queued",
            payload={"attempt": preparation.attempt},
            created_at=self._now_utc(),
        )
        return CompanyResearchProjectStatus(
            project=current.project, preparation=preparation
        )
