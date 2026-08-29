"""Persistence primitives for append-only company-research workbench data."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    CompanyResearchMemoArtifact,
    JudgmentContextArtifact,
    ResearchGap,
    SourceLineageReference,
)
from app.underwriting.domain.company_research_provenance import (
    canonical_source_refs,
    evidence_payload_source_refs,
    source_record,
)
from app.underwriting.domain.product_contracts import (
    ProductHistoricalBasisInput,
    product_historical_basis_content_hash,
)
from app.underwriting.persistence.company_research_models import (
    COMPANY_RESEARCH_ARTIFACT_KINDS,
    COMPANY_RESEARCH_PREPARATION_STATUSES,
    COMPANY_RESEARCH_PREPARATION_STEPS,
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.models import (
    UnderwritingHistoricalBasis,
    UnderwritingMandateVersion,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingMarketCaptureEnvelope,
    UnderwritingPriceSnapshot,
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchProjectSecurity,
    UnderwritingResearchScopeVersion,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.hashing import canonical_hash
from app.underwriting.domain.company_research_market_contracts import (
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
    FrozenRawComponentReference,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
    MODEL_ARTIFACT_KINDS,
)
from app.underwriting.services.market_snapshots import (
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    market_capture_envelope_hash,
    price_snapshot_hash,
    security_rights_hash,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftContent,
    WorkspaceDraftService,
)

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PREPARE_JOB_KIND = "prepare_company_research"
_PREPARE_JOB_TARGET_TYPE = "company_research_preparation"


def validate_company_research_derived_gap_semantics(
    *,
    business_map: BusinessMapArtifact,
    memo: CompanyResearchMemoArtifact,
    judgment: JudgmentContextArtifact,
    gaps: tuple[ResearchGap, ...],
    has_valuation: bool,
    require_embedded_memo_gaps: bool,
) -> None:
    """Close every published/read projection of model-derived gaps."""
    expected_refs_by_module: dict[str, list[str]] = {}
    for gap in gaps:
        expected_refs_by_module.setdefault(gap.module_key, []).append(gap.code)
    expected = {
        module_key: tuple(sorted(codes))
        for module_key, codes in expected_refs_by_module.items()
    }
    actual = {
        module.module_key: module.gap_refs
        for module in business_map.modules
        if module.gap_refs
    }
    if (
        (require_embedded_memo_gaps and memo.research_gaps != gaps)
        or tuple(gap.code for gap in gaps) != memo.gap_keys
        or tuple(gap.message for gap in gaps) != judgment.next_verification_events
        or memo.next_verification_events != judgment.next_verification_events
        or memo.strongest_counterevidence != judgment.strongest_counterevidence
        or actual != expected
        or (
            any(gap.severity.value == "critical" for gap in gaps)
            and (memo.assessment_status != "not_answerable" or has_valuation)
        )
    ):
        raise ValidationError("company research derived gaps are inconsistent")


class CompanyResearchIntegrityError(ValidationError):
    """A persisted immutable company-research record cannot be trusted."""


class CompanyResearchGovernedBasisMismatch(CompanyResearchIntegrityError):
    """An authentic basis belongs to a different governed model boundary."""


@dataclass(frozen=True, slots=True)
class CompanyResearchAuthenticatedHistoricalBasis:
    """One freshly loaded product basis whose immutable fields authenticate."""

    id: UUID
    cutoff_at: datetime
    source_manifest_hash: str
    definition_bundle_hash: str
    parser_bundle_hash: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class CompanyResearchValidatedWorkspaceBoundary:
    """One draft/basis snapshot accepted for company-model validation."""

    draft_id: UUID
    draft_lock_version: int
    cutoff_at: datetime
    historical_basis_id: UUID
    historical_basis_content_hash: str


@dataclass(frozen=True, slots=True)
class CompanyResearchBasisRecoveryState:
    """All mutable and immutable inputs locked for one legacy basis recovery."""

    preparation: CompanyResearchPreparation
    job: Job
    draft: UnderwritingWorkspaceDraft
    draft_content: WorkspaceDraftContent
    project: UnderwritingResearchProject | None
    company: UnderwritingResearchObject | None
    securities: tuple[UnderwritingResearchObject, ...]
    memberships: tuple[UnderwritingResearchProjectSecurity, ...]
    mandate: UnderwritingMandateVersion | None
    scope: UnderwritingResearchScopeVersion | None
    agenda: UnderwritingResearchAgendaVersion | None
    mandate_head_id: UUID | None
    scope_head_id: UUID | None
    agenda_head_id: UUID | None
    evidence: CompanyResearchArtifactVersion
    research_gaps: CompanyResearchArtifactVersion
    evidence_chain: tuple[CompanyResearchArtifactVersion, ...]
    research_gaps_chain: tuple[CompanyResearchArtifactVersion, ...]
    events: tuple[CompanyResearchEvent, ...]


@dataclass(frozen=True, slots=True)
class CompanyResearchPublicationState:
    """One fresh, project-first locked judgment-publication boundary."""

    project: UnderwritingResearchProject
    preparation: CompanyResearchPreparation
    job: Job
    draft: UnderwritingWorkspaceDraft
    draft_content: WorkspaceDraftContent
    company: UnderwritingResearchObject
    securities: tuple[UnderwritingResearchObject, ...]
    memberships: tuple[UnderwritingResearchProjectSecurity, ...]
    historical_basis: UnderwritingHistoricalBasis
    authenticated_basis: CompanyResearchAuthenticatedHistoricalBasis
    mandate: UnderwritingMandateVersion | None
    scope: UnderwritingResearchScopeVersion | None
    agenda: UnderwritingResearchAgendaVersion | None
    mandate_head_id: UUID | None
    scope_head_id: UUID | None
    agenda_head_id: UUID | None
    artifact_heads: Mapping[str, CompanyResearchArtifactVersion]
    artifact_chains: Mapping[str, tuple[CompanyResearchArtifactVersion, ...]]
    events: tuple[CompanyResearchEvent, ...]


@dataclass(frozen=True, slots=True)
class CompanyResearchRetryState:
    """Fresh project-first ownership boundary used to select a retry path."""

    project: UnderwritingResearchProject
    preparation: CompanyResearchPreparation
    job: Job


def reconcile_company_research_evidence_audit(
    *,
    preparation: CompanyResearchPreparation,
    evidence_chain: tuple[CompanyResearchArtifactVersion, ...],
    research_gaps: CompanyResearchArtifactVersion,
    events: tuple[CompanyResearchEvent, ...],
) -> None:
    """Bind the reviewed evidence version chain to its append-only audit events."""
    invalid = ValidationError("company research evidence audit history is invalid")
    initialized_events = tuple(
        event for event in events if event.event_type == "initialized"
    )
    if (
        not evidence_chain
        or evidence_chain[0].project_id != preparation.project_id
        or research_gaps.project_id != preparation.project_id
        or not events
        or len(initialized_events) != 1
        or events[0].event_type != "initialized"
        or events[0].sequence != 1
        or events[0].payload != {"request_hash": preparation.request_hash}
    ):
        raise invalid
    prepared = tuple(
        event for event in events if event.event_type == "evidence_index_prepared"
    )
    if len(prepared) != 1:
        raise invalid
    prepared_event = prepared[0]
    prepared_at = CompanyResearchRepository._persisted_utc(prepared_event.created_at)
    if (
        prepared_event.payload
        != {
            "evidence_index_id": str(evidence_chain[0].id),
            "research_gaps_id": str(research_gaps.id),
        }
        or prepared_at
        != CompanyResearchRepository._persisted_utc(evidence_chain[0].created_at)
        or prepared_at
        != CompanyResearchRepository._persisted_utc(research_gaps.created_at)
    ):
        raise invalid
    predecessor_index = prepared_event.sequence - 2
    if predecessor_index < 0 or predecessor_index >= len(events):
        raise invalid
    source_claim = events[predecessor_index]
    if (
        not isinstance(source_claim.payload, Mapping)
        or source_claim.event_type != "source_stage_claimed"
        or set(source_claim.payload) != {"stage", "attempt"}
        or source_claim.payload.get("stage") != "evidence_index"
        or type(source_claim.payload.get("attempt")) is not int
        or source_claim.payload["attempt"] < 1
    ):
        raise invalid
    review_events = tuple(
        event for event in events if event.event_type == "evidence_reviewed"
    )
    if len(review_events) != len(evidence_chain) - 1:
        raise invalid
    for offset, (parent, successor, event) in enumerate(
        zip(evidence_chain[:-1], evidence_chain[1:], review_events, strict=True),
        start=1,
    ):
        before = parent.payload.get("facts")
        after = successor.payload.get("facts")
        if (
            not isinstance(before, list)
            or not isinstance(after, list)
            or len(before) != len(after)
        ):
            raise invalid
        changes = [
            candidate
            for previous, candidate in zip(before, after, strict=True)
            if previous != candidate
        ]
        if len(changes) != 1 or not isinstance(changes[0], Mapping):
            raise invalid
        fact_key = changes[0].get("fact_key")
        decision = changes[0].get("review_decision")
        if (
            event.sequence != prepared_event.sequence + offset
            or event.payload
            != {
                "evidence_artifact_id": str(successor.id),
                "fact_key": fact_key,
                "decision": decision,
            }
            or CompanyResearchRepository._persisted_utc(event.created_at)
            != CompanyResearchRepository._persisted_utc(successor.created_at)
        ):
            raise invalid


@dataclass(frozen=True, slots=True)
class CompanyResearchPersistedBundle:
    """Complete JSON-ready model candidate passed to one atomic publication."""

    evidence_artifact_id: UUID
    evidence_content_hash: str
    research_gaps_artifact_id: UUID
    research_gaps_content_hash: str
    workspace_draft_id: UUID
    workspace_draft_lock_version: int
    historical_basis_id: UUID
    historical_basis_content_hash: str
    business_map: Mapping[str, object]
    driver_map: Mapping[str, object]
    financial_bridge: Mapping[str, object]
    scenario_set: Mapping[str, object]
    valuation_set: Mapping[str, object] | None
    judgment_context: Mapping[str, object]
    research_gaps: Mapping[str, object]
    memo: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]
    market_snapshot_bindings: tuple[FrozenMarketSnapshotBinding, ...]

    def __post_init__(self) -> None:
        required = (
            self.business_map,
            self.driver_map,
            self.financial_bridge,
            self.scenario_set,
            self.judgment_context,
            self.research_gaps,
            self.memo,
        )
        if any(not isinstance(payload, Mapping) for payload in required):
            raise ValidationError("company research model bundle payload is invalid")
        if self.valuation_set is not None and not isinstance(
            self.valuation_set, Mapping
        ):
            raise ValidationError("company research model bundle valuation is invalid")
        if (
            type(self.evidence_artifact_id) is not UUID
            or type(self.research_gaps_artifact_id) is not UUID
            or type(self.workspace_draft_id) is not UUID
            or type(self.historical_basis_id) is not UUID
            or type(self.workspace_draft_lock_version) is not int
            or self.workspace_draft_lock_version < 1
        ):
            raise ValidationError("company research model bundle boundary is invalid")
        for value in (
            self.evidence_content_hash,
            self.research_gaps_content_hash,
            self.historical_basis_content_hash,
        ):
            if not isinstance(value, str) or _HASH.fullmatch(value) is None:
                raise ValidationError(
                    "company research model bundle head hashes are invalid"
                )
        if self.valuation_set is not None and not self.market_snapshot_bindings:
            raise ValidationError(
                "company research model bundle valuation market refs are invalid"
            )
        if any(
            "_lineage" in payload for payload in (*required, self.valuation_set or {})
        ):
            raise ValidationError("company research model bundle lineage is reserved")
        if not isinstance(self.source_refs, tuple):
            raise ValidationError(
                "company research model bundle source refs are invalid"
            )
        object.__setattr__(
            self,
            "source_refs",
            canonical_source_refs(
                self.source_refs,
                field_name="company research model bundle source refs",
            ),
        )
        if (
            not isinstance(self.market_snapshot_bindings, tuple)
            or not all(
                type(item) is FrozenMarketSnapshotBinding
                for item in self.market_snapshot_bindings
            )
            or len({item.snapshot_id for item in self.market_snapshot_bindings})
            != len(self.market_snapshot_bindings)
            or tuple(
                sorted(
                    self.market_snapshot_bindings,
                    key=lambda item: str(item.snapshot_id),
                )
            )
            != self.market_snapshot_bindings
        ):
            raise ValidationError(
                "company research model bundle market snapshot bindings must be canonical"
            )
        canonical_payloads = {
            kind: CompanyResearchArtifactCodec.validate_payload(kind, payload)
            for kind, payload in (
                ("business_map", self.business_map),
                ("driver_map", self.driver_map),
                ("financial_bridge", self.financial_bridge),
                ("scenario_set", self.scenario_set),
                ("judgment_context", self.judgment_context),
                ("research_gaps", self.research_gaps),
                ("memo", self.memo),
            )
        }
        for kind, payload in canonical_payloads.items():
            object.__setattr__(self, kind, payload)
        if self.valuation_set is not None:
            object.__setattr__(
                self,
                "valuation_set",
                CompanyResearchArtifactCodec.validate_payload(
                    "valuation_set", self.valuation_set
                ),
            )
        memo = CompanyResearchArtifactCodec.decode("memo", self.memo)
        judgment = CompanyResearchArtifactCodec.decode(
            "judgment_context", self.judgment_context
        )
        business_map = CompanyResearchArtifactCodec.decode(
            "business_map", self.business_map
        )
        gaps = CompanyResearchArtifactCodec.decode("research_gaps", self.research_gaps)
        assert type(memo) is CompanyResearchMemoArtifact
        assert type(judgment) is JudgmentContextArtifact
        assert type(business_map) is BusinessMapArtifact
        assert isinstance(gaps, tuple) and all(type(gap) is ResearchGap for gap in gaps)
        if "research_gaps" not in self.memo:
            raise ValidationError("company research derived gaps are inconsistent")
        validate_company_research_derived_gap_semantics(
            business_map=business_map,
            memo=memo,
            judgment=judgment,
            gaps=gaps,
            has_valuation=self.valuation_set is not None,
            require_embedded_memo_gaps=True,
        )
        if (
            bool(self.market_snapshot_bindings)
            != judgment.market_security_bridge_available
        ):
            raise ValidationError(
                "company research bundle market availability is inconsistent"
            )
        memo_refs = {
            "business_map": memo.business_map_ref,
            "driver_map": memo.driver_map_ref,
            "financial_bridge": memo.financial_bridge_ref,
            "scenario_set": memo.scenario_set_ref,
        }
        if any(
            reference.content_hash != canonical_hash(canonical_payloads[kind])
            for kind, reference in memo_refs.items()
        ):
            raise ValidationError(
                "company research memo artifact references are inconsistent"
            )
        if self.valuation_set is not None and (
            memo.valuation_set_ref is None
            or memo.valuation_set_ref.content_hash != canonical_hash(self.valuation_set)
        ):
            raise ValidationError(
                "company research memo artifact references are inconsistent"
            )
        if self.valuation_set is None:
            if (
                memo.assessment_status != "not_answerable"
                or memo.valuation_set_ref is not None
            ):
                raise ValidationError(
                    "company research bundle without valuation must be not_answerable"
                )
        elif (
            memo.valuation_set_ref is None
            or not judgment.market_security_bridge_available
        ):
            raise ValidationError(
                "company research bundle valuation state is inconsistent"
            )

    @property
    def market_snapshot_ids(self) -> tuple[UUID, ...]:
        return tuple(item.snapshot_id for item in self.market_snapshot_bindings)


class CompanyResearchRepository:
    """Flush-only operations which retain ownership of the caller transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _reserve_sqlite_writer_before_ownership_read(
        self, project_id: UUID | None = None
    ) -> None:
        """Serialize SQLite ownership validation without taking over caller work."""
        connection = self._session.connection()
        if connection.dialect.name != "sqlite":
            return
        dbapi_connection = getattr(
            connection.connection, "driver_connection", connection.connection
        )
        # SQLAlchemy can expose a logical transaction before sqlite3 has opened
        # a physical one.  In that state we can still reserve the sole writer
        # before the Job/preparation read.  Once the caller owns a physical
        # transaction, do not begin, commit, or replace it here.
        if not dbapi_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif project_id is not None:
            self._session.execute(
                update(UnderwritingWorkspaceDraft)
                .where(UnderwritingWorkspaceDraft.project_id == project_id)
                .values(lock_version=UnderwritingWorkspaceDraft.lock_version)
                .execution_options(synchronize_session=False)
            )

    def reserve_retry_writer(self) -> None:
        """Reserve SQLite retry ownership before an outer atomic savepoint.

        PostgreSQL ownership remains row-lock based.  SQLite must acquire its
        sole writer before ``Session.begin_nested()`` opens a deferred physical
        transaction, otherwise two retries can both read and then deadlock on
        their first write.
        """
        self._reserve_sqlite_writer_before_ownership_read()

    def _reserve_sqlite_writer_before_artifact_head_read(self) -> None:
        """Reserve SQLite's writer before calculating an artifact successor."""
        try:
            self._reserve_sqlite_writer_before_ownership_read()
        except OperationalError as exc:
            raise StaleParentError(
                "expected parent is not the company artifact head"
            ) from exc

    def _reserve_sqlite_writer_before_event_append(self) -> None:
        """Serialize an event sequence without committing caller-owned work."""
        try:
            self._reserve_sqlite_writer_before_ownership_read()
        except OperationalError as exc:
            raise ConflictError("company research event append is concurrent") from exc

    def _job_for_update(
        self, job_id: UUID, *, populate_existing: bool = False
    ) -> Job | None:
        """Read a Job under the caller transaction's row lock when supported."""
        statement = select(Job).where(Job.id == job_id).with_for_update()
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def _preparation_for_update(
        self, preparation_id: UUID, *, populate_existing: bool = False
    ) -> CompanyResearchPreparation | None:
        """Serialize competing attachments to the mutable preparation row."""
        statement = (
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.id == preparation_id)
            .with_for_update()
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def _workspace_draft_for_update(
        self, project_id: UUID
    ) -> UnderwritingWorkspaceDraft | None:
        return self._session.scalar(
            select(UnderwritingWorkspaceDraft)
            .where(UnderwritingWorkspaceDraft.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _project_for_update(
        self, project_id: UUID, *, skip_locked: bool = False
    ) -> UnderwritingResearchProject | None:
        return self._session.scalar(
            select(UnderwritingResearchProject)
            .where(UnderwritingResearchProject.id == project_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )

    def authenticate_historical_basis(
        self,
        basis: UnderwritingHistoricalBasis,
    ) -> CompanyResearchAuthenticatedHistoricalBasis:
        """Authenticate immutable basis fields from a freshly loaded row."""
        try:
            cutoff = self._persisted_utc(basis.cutoff)
            source_manifest_hash = self._require_hash(
                basis.source_manifest_hash,
                "historical basis source manifest hash",
            )
            definition_bundle_hash = self._require_hash(
                basis.definition_bundle_hash,
                "historical basis definition bundle hash",
            )
            parser_bundle_hash = self._require_hash(
                basis.parser_bundle_hash,
                "historical basis parser bundle hash",
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research historical basis is invalid"
            ) from exc
        content_hash = product_historical_basis_content_hash(
            ProductHistoricalBasisInput(
                cutoff_at=cutoff,
                source_manifest_hash=source_manifest_hash,
                definition_bundle_hash=definition_bundle_hash,
                parser_bundle_hash=parser_bundle_hash,
            )
        )
        if basis.content_hash != content_hash:
            raise CompanyResearchIntegrityError(
                "company research historical basis is invalid"
            )
        return CompanyResearchAuthenticatedHistoricalBasis(
            id=basis.id,
            cutoff_at=cutoff,
            source_manifest_hash=source_manifest_hash,
            definition_bundle_hash=definition_bundle_hash,
            parser_bundle_hash=parser_bundle_hash,
            content_hash=content_hash,
        )

    def authenticate_governed_historical_basis(
        self,
        basis: UnderwritingHistoricalBasis,
        *,
        expected_input: ProductHistoricalBasisInput,
        expected_content_hash: str,
    ) -> CompanyResearchAuthenticatedHistoricalBasis:
        """Authenticate one basis against an exact governed boundary contract."""
        if (
            type(expected_input) is not ProductHistoricalBasisInput
            or product_historical_basis_content_hash(expected_input)
            != expected_content_hash
        ):
            raise ValidationError("governed historical basis contract is invalid")
        authenticated = self.authenticate_historical_basis(basis)
        expected_cutoff = self._stored_datetime(
            expected_input.cutoff_at, "expected historical basis cutoff"
        )
        if (
            authenticated.cutoff_at != expected_cutoff
            or authenticated.source_manifest_hash != expected_input.source_manifest_hash
            or authenticated.definition_bundle_hash
            != expected_input.definition_bundle_hash
            or authenticated.parser_bundle_hash != expected_input.parser_bundle_hash
            or authenticated.content_hash != expected_content_hash
        ):
            raise CompanyResearchGovernedBasisMismatch(
                "company research historical basis does not match governed contract"
            )
        return authenticated

    def validate_workspace_market_boundary(
        self,
        *,
        project_id: UUID,
        bindings: Sequence[FrozenMarketSnapshotBinding],
        expected_cutoff_at: datetime,
        expected_source_manifest_hash: str,
        expected_historical_basis_id: UUID | None = None,
        expected_historical_basis_content_hash: str | None = None,
        expected_draft_id: UUID | None = None,
        expected_lock_version: int | None = None,
        lock: bool = False,
    ) -> CompanyResearchValidatedWorkspaceBoundary:
        draft = (
            self._workspace_draft_for_update(project_id)
            if lock
            else ProductRepository(self._session).workspace_draft(project_id)
        )
        if (
            draft is None
            or (expected_draft_id is not None and draft.id != expected_draft_id)
            or (
                expected_lock_version is not None
                and draft.lock_version != expected_lock_version
            )
        ):
            raise ValidationError("company research workspace draft is stale")
        content = WorkspaceDraftService.decode_content(draft.content)
        if content.historical_basis_id is None:
            raise ValidationError("company research historical basis is missing")
        basis = ProductRepository(self._session).product_basis(
            content.historical_basis_id
        )
        if basis is None:
            raise ValidationError("company research historical basis is invalid")
        authenticated_basis = self.authenticate_historical_basis(basis)
        if (
            expected_historical_basis_id is not None
            and authenticated_basis.id != expected_historical_basis_id
        ):
            raise ValidationError("company research historical basis is stale")
        expected_cutoff = self._stored_datetime(
            expected_cutoff_at, "expected_cutoff_at"
        )
        if authenticated_basis.cutoff_at != expected_cutoff:
            raise ValidationError(
                "company research historical basis cutoff does not match reviewed evidence"
            )
        expected_source_manifest_hash = self._require_hash(
            expected_source_manifest_hash,
            "expected_source_manifest_hash",
        )
        if authenticated_basis.source_manifest_hash != expected_source_manifest_hash:
            raise ValidationError(
                "company research historical basis source does not match reviewed evidence"
            )
        if (
            expected_historical_basis_content_hash is not None
            and authenticated_basis.content_hash
            != expected_historical_basis_content_hash
        ):
            raise ValidationError("company research historical basis is stale")
        bindings_by_role = {
            role: tuple(
                sorted(
                    (
                        binding.snapshot_id
                        for binding in bindings
                        if binding.role is role
                    ),
                    key=str,
                )
            )
            for role in FrozenMarketSnapshotRole
        }
        if (
            bindings_by_role[FrozenMarketSnapshotRole.PRICE]
            != content.price_snapshot_ids
            or bindings_by_role[FrozenMarketSnapshotRole.FX] != content.fx_snapshot_ids
            or bindings_by_role[FrozenMarketSnapshotRole.SECURITY_RIGHTS]
            != content.security_rights_ids
            or bindings_by_role[FrozenMarketSnapshotRole.CAPITAL_STRUCTURE]
            != (
                (content.capital_structure_snapshot_id,)
                if content.capital_structure_snapshot_id is not None
                else ()
            )
        ):
            raise ValidationError(
                "company research market bindings do not match workspace draft"
            )
        return CompanyResearchValidatedWorkspaceBoundary(
            draft_id=draft.id,
            draft_lock_version=draft.lock_version,
            cutoff_at=authenticated_basis.cutoff_at,
            historical_basis_id=authenticated_basis.id,
            historical_basis_content_hash=authenticated_basis.content_hash,
        )

    @classmethod
    def evidence_cutoff(cls, artifact: CompanyResearchArtifactVersion) -> datetime:
        value = (
            artifact.payload.get("cutoff")
            if isinstance(artifact.payload, dict)
            else None
        )
        if not isinstance(value, str):
            raise ValidationError("reviewed evidence cutoff is invalid")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError("reviewed evidence cutoff is invalid") from exc
        return cls._stored_datetime(parsed, "reviewed evidence cutoff")

    @classmethod
    def evidence_source_manifest_hash(
        cls, artifact: CompanyResearchArtifactVersion
    ) -> str:
        value = (
            artifact.payload.get("fixture_content_hash")
            if isinstance(artifact.payload, dict)
            else None
        )
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            raise ValidationError("reviewed evidence source manifest is invalid")
        return value

    def _locked_prepare_job(
        self,
        preparation: CompanyResearchPreparation,
        *,
        populate_existing: bool = False,
    ) -> Job:
        """Lock the Job owned by an already-locked preparation exactly."""
        if preparation.job_id is None:
            raise CompanyResearchIntegrityError(
                "company research preparation job is missing"
            )
        job = self._job_for_update(
            preparation.job_id, populate_existing=populate_existing
        )
        if not self.is_exact_prepare_job_owner(job, preparation.id):
            raise CompanyResearchIntegrityError(
                "company research preparation job ownership is invalid"
            )
        return job

    def _locked_project_preparation_job(
        self,
        preparation_id: UUID,
        *,
        populate_existing: bool,
    ) -> tuple[
        UnderwritingResearchProject,
        CompanyResearchPreparation,
        Job,
    ]:
        """Lock a preparation owner in canonical project → preparation → Job order."""
        project_id = self._session.scalar(
            select(CompanyResearchPreparation.project_id).where(
                CompanyResearchPreparation.id == preparation_id
            )
        )
        if project_id is None:
            raise ValidationError("company research preparation not found")
        self._reserve_sqlite_writer_before_ownership_read(project_id)
        project = self._project_for_update(project_id)
        if project is None:
            raise CompanyResearchIntegrityError(
                "company research preparation project is missing"
            )
        preparation = self._preparation_for_update(
            preparation_id, populate_existing=populate_existing
        )
        if preparation is None:
            raise ValidationError("company research preparation not found")
        if preparation.project_id != project.id:
            raise CompanyResearchIntegrityError(
                "company research preparation project ownership is invalid"
            )
        job = self._locked_prepare_job(preparation, populate_existing=populate_existing)
        return project, preparation, job

    @staticmethod
    def _require_preparation_step(value: str | None, field: str) -> str:
        if value not in COMPANY_RESEARCH_PREPARATION_STEPS:
            raise ValidationError(f"{field} is not a company research preparation step")
        return value

    @staticmethod
    def _validate_prepare_job_step(
        *,
        preparation_step: str | None,
        preparation_status: str,
        job: Job,
        persisted: bool,
    ) -> None:
        error_type = CompanyResearchIntegrityError if persisted else ValidationError
        if preparation_step not in COMPANY_RESEARCH_PREPARATION_STEPS:
            raise error_type("company research preparation step is invalid")
        if job.step != preparation_step:
            raise error_type("company research preparation job step is invalid")
        expected_job_statuses = {
            "queued": frozenset({"queued"}),
            # A legacy/manual retry records a failed Job, while the worker's
            # automatic retry keeps its owned Job queued behind backoff.
            "recoverable_failure": frozenset({"failed", "queued"}),
        }.get(preparation_status)
        if (
            expected_job_statuses is not None
            and job.status not in expected_job_statuses
        ):
            raise error_type("company research preparation job status is invalid")

    def _flush_in_savepoint(self, row: Any) -> Any:
        """Flush without committing or poisoning an outer SQLite transaction."""
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            dbapi_connection = getattr(
                connection.connection, "driver_connection", connection.connection
            )
            if not dbapi_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        with self._session.begin_nested():
            self._session.add(row)
            self._session.flush([row])
        return row

    @staticmethod
    def _require_hash(value: str, field: str) -> str:
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            raise ValidationError(f"{field} must be a lowercase SHA-256 hash")
        return value

    @staticmethod
    def _stored_datetime(value: datetime, field: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _persisted_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _validate_typed_artifact_payload(
        *,
        kind: str,
        payload: Mapping[str, object],
        supersedes_id: UUID | None,
    ) -> None:
        if kind not in MODEL_ARTIFACT_KINDS:
            return
        has_lineage = "_lineage" in payload
        modules = payload.get("modules")
        legacy_business_map = (
            kind == "business_map"
            and supersedes_id is None
            and not has_lineage
            and set(payload)
            == {"evidence_index_id", "evidence_content_hash", "modules"}
            and isinstance(payload.get("evidence_index_id"), str)
            and isinstance(payload.get("evidence_content_hash"), str)
            and _HASH.fullmatch(payload["evidence_content_hash"]) is not None
            and isinstance(modules, list)
            and bool(modules)
            and all(
                isinstance(module, dict)
                and set(module) == {"key", "fact_keys"}
                and isinstance(module["key"], str)
                and bool(module["key"])
                and isinstance(module["fact_keys"], list)
                and bool(module["fact_keys"])
                and all(
                    isinstance(fact_key, str) and bool(fact_key)
                    for fact_key in module["fact_keys"]
                )
                for module in modules
            )
        )
        gaps = payload.get("gaps")
        source_gap_contract = (
            kind == "research_gaps"
            and supersedes_id is None
            and not has_lineage
            and set(payload) == {"fixture_content_hash", "company_external_key", "gaps"}
            and isinstance(payload.get("fixture_content_hash"), str)
            and _HASH.fullmatch(payload["fixture_content_hash"]) is not None
            and isinstance(payload.get("company_external_key"), str)
            and bool(payload["company_external_key"])
            and isinstance(gaps, list)
            and all(
                isinstance(gap, dict)
                and set(gap) == {"gap_key", "business_module", "reason"}
                and all(
                    isinstance(value, str) and bool(value) for value in gap.values()
                )
                for gap in gaps
            )
        )
        if legacy_business_map or source_gap_contract:
            return
        CompanyResearchArtifactCodec.validate_payload(
            kind,
            {key: value for key, value in payload.items() if key != "_lineage"},
        )

    @staticmethod
    def _require_nonempty_text(value: str, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not (cleaned := value.strip()):
            raise ValidationError(f"{field} must not be empty")
        if len(cleaned) > maximum:
            raise ValidationError(f"{field} is too long")
        return cleaned

    @staticmethod
    def _artifact_payload(
        *,
        project_id: UUID,
        kind: str,
        version: int,
        supersedes_id: UUID | None,
        parent_content_hash: str | None,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        return {
            "schema_version": "company-research-artifact.v1",
            "project_id": str(project_id),
            "kind": kind,
            "version": version,
            "supersedes_id": str(supersedes_id) if supersedes_id is not None else None,
            "parent_content_hash": parent_content_hash,
            "input_hash": input_hash,
            "payload": payload,
            "source_refs": source_refs,
        }

    @classmethod
    def artifact_content_hash(
        cls,
        *,
        project_id: UUID,
        kind: str,
        version: int,
        supersedes_id: UUID | None,
        parent_content_hash: str | None = None,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
    ) -> str:
        return canonical_hash(
            cls._artifact_payload(
                project_id=project_id,
                kind=kind,
                version=version,
                supersedes_id=supersedes_id,
                parent_content_hash=parent_content_hash,
                input_hash=input_hash,
                payload=payload,
                source_refs=source_refs,
            )
        )

    @staticmethod
    def _event_payload(
        *,
        preparation_id: UUID,
        sequence: int,
        previous_event_hash: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "company-research-event.v1",
            "preparation_id": str(preparation_id),
            "sequence": sequence,
            "previous_event_hash": previous_event_hash,
            "event_type": event_type,
            "payload": payload,
        }

    @classmethod
    def event_content_hash(
        cls,
        *,
        preparation_id: UUID,
        sequence: int,
        previous_event_hash: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> str:
        return canonical_hash(
            cls._event_payload(
                preparation_id=preparation_id,
                sequence=sequence,
                previous_event_hash=previous_event_hash,
                event_type=event_type,
                payload=payload,
            )
        )

    @classmethod
    def event_content_hash_v2(
        cls,
        *,
        preparation_id: UUID,
        sequence: int,
        previous_event_hash: str | None,
        event_type: str,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> str:
        if not isinstance(created_at, datetime):
            raise ValidationError("event created_at must be a datetime")
        return canonical_hash(
            {
                "schema_version": "company-research-event.v2",
                "preparation_id": str(preparation_id),
                "sequence": sequence,
                "previous_event_hash": previous_event_hash,
                "event_type": event_type,
                "payload": payload,
                "created_at": cls._persisted_utc(created_at).isoformat(),
            }
        )

    def add_preparation(
        self,
        *,
        project_id: UUID,
        idempotency_key: str,
        request_hash: str,
        strategy_version: str,
        status: str,
        current_step: str | None,
        progress: int,
        attempt: int,
        next_attempt_at: datetime | None,
        last_error_code: str | None,
        job_id: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> CompanyResearchPreparation:
        if status not in COMPANY_RESEARCH_PREPARATION_STATUSES:
            raise ValidationError("company research preparation status is invalid")
        if type(progress) is not int or not 0 <= progress <= 100:
            raise ValidationError("progress must be between 0 and 100")
        if type(attempt) is not int or attempt < 1:
            raise ValidationError("attempt must be at least 1")
        if current_step is not None:
            current_step = self._require_nonempty_text(current_step, "current_step", 64)
            self._require_preparation_step(current_step, "current_step")
        if last_error_code is not None:
            last_error_code = self._require_nonempty_text(
                last_error_code, "last_error_code", 96
            )
        job: Job | None = None
        preparation_id: UUID | None = None
        if job_id is not None:
            self._reserve_sqlite_writer_before_ownership_read()
            job = self._job_for_update(job_id)
            if (
                job is None
                or job.kind != _PREPARE_JOB_KIND
                or job.target_type != _PREPARE_JOB_TARGET_TYPE
                or job.target_id is None
                or job.research_case_id is not None
            ):
                raise ValidationError(
                    "company research preparation job ownership is invalid"
                )
            # The job is created first with the UUID it owns; persist the new
            # preparation at that exact UUID so no later attachment can turn a
            # legacy or unrelated job into an owner.
            preparation_id = job.target_id
            self._validate_prepare_job_step(
                preparation_step=current_step,
                preparation_status=status,
                job=job,
                persisted=False,
            )
        row = CompanyResearchPreparation(
            id=preparation_id,
            project_id=project_id,
            idempotency_key=self._require_nonempty_text(
                idempotency_key, "idempotency_key", 255
            ),
            request_hash=self._require_hash(request_hash, "request_hash"),
            strategy_version=self._require_nonempty_text(
                strategy_version, "strategy_version", 96
            ),
            status=status,
            current_step=current_step,
            progress=progress,
            attempt=attempt,
            next_attempt_at=(
                self._stored_datetime(next_attempt_at, "next_attempt_at")
                if next_attempt_at is not None
                else None
            ),
            last_error_code=last_error_code,
            job_id=job_id,
            created_at=self._stored_datetime(created_at, "created_at"),
            updated_at=self._stored_datetime(updated_at, "updated_at"),
        )
        try:
            return self._flush_in_savepoint(row)
        except IntegrityError as exc:
            raise ConflictError("company research preparation already exists") from exc

    def preparation(self, preparation_id: UUID) -> CompanyResearchPreparation | None:
        return self._session.get(CompanyResearchPreparation, preparation_id)

    def preparation_by_idempotency_key(
        self, idempotency_key: str
    ) -> CompanyResearchPreparation | None:
        return self._session.scalar(
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.idempotency_key == idempotency_key)
            .limit(1)
        )

    def preparation_for_project(
        self, project_id: UUID, *, fresh: bool = False, lock: bool = False
    ) -> CompanyResearchPreparation | None:
        """Return the single preparation owned by a high-level project."""
        statement = (
            select(CompanyResearchPreparation)
            .where(CompanyResearchPreparation.project_id == project_id)
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        if fresh:
            statement = statement.execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def reserve_publication_writer(
        self, project_id: UUID
    ) -> UnderwritingResearchProject:
        """Lock the project and reserve SQLite's writer before authentication."""
        try:
            self._reserve_sqlite_writer_before_ownership_read(project_id)
        except OperationalError as exc:
            message = str(getattr(exc, "orig", exc)).lower()
            if "locked" in message or "busy" in message:
                raise ConflictError(
                    "company research judgment confirmation is concurrent"
                ) from exc
            raise
        project = self._project_for_update(project_id)
        if project is None:
            raise ValidationError("company research project not found")
        return project

    def lock_publication_state(
        self, project_id: UUID
    ) -> CompanyResearchPublicationState:
        """Capture every mutable owner and immutable authentication input freshly."""
        return self._publication_state(project_id, lock=True)

    def publication_state(self, project_id: UUID) -> CompanyResearchPublicationState:
        """Read the complete publication boundary freshly without reserving a writer."""
        return self._publication_state(project_id, lock=False)

    def _publication_state(
        self, project_id: UUID, *, lock: bool
    ) -> CompanyResearchPublicationState:
        if type(project_id) is not UUID:
            raise ValidationError("project_id must be a UUID")
        if lock:
            project = self.reserve_publication_writer(project_id)
        else:
            project = self._session.scalar(
                select(UnderwritingResearchProject)
                .where(UnderwritingResearchProject.id == project_id)
                .execution_options(populate_existing=True)
            )
            if project is None:
                raise ValidationError("company research project not found")
        preparation_id = self._session.scalar(
            select(CompanyResearchPreparation.id).where(
                CompanyResearchPreparation.project_id == project_id
            )
        )
        if preparation_id is None:
            raise ValidationError("company research preparation not found")
        preparation = (
            self._preparation_for_update(preparation_id, populate_existing=True)
            if lock
            else self.preparation_for_project(project_id, fresh=True)
        )
        if preparation is None or preparation.project_id != project_id:
            raise ValidationError("company research preparation not found")
        if lock:
            job = self._locked_prepare_job(preparation, populate_existing=True)
            draft = self._workspace_draft_for_update(project_id)
        else:
            job = self._session.scalar(
                select(Job)
                .where(Job.id == preparation.job_id)
                .execution_options(populate_existing=True)
            )
            draft = self._session.scalar(
                select(UnderwritingWorkspaceDraft)
                .where(UnderwritingWorkspaceDraft.project_id == project_id)
                .execution_options(populate_existing=True)
            )
        if job is None:
            raise ValidationError("company research preparation job not found")
        if draft is None:
            raise ValidationError("company research workspace draft is missing")
        try:
            draft_content = WorkspaceDraftService.decode_content(draft.content)
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research workspace draft content is invalid"
            ) from exc

        membership_statement = (
            select(UnderwritingResearchProjectSecurity)
            .where(UnderwritingResearchProjectSecurity.project_id == project_id)
            .order_by(UnderwritingResearchProjectSecurity.security_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            membership_statement = membership_statement.with_for_update()
        memberships = tuple(self._session.scalars(membership_statement))
        authority_ids = tuple(
            sorted(
                {project.primary_company_id, *(row.security_id for row in memberships)},
                key=str,
            )
        )
        authority_statement = (
            select(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.id.in_(authority_ids))
            .order_by(UnderwritingResearchObject.id)
            .execution_options(populate_existing=True)
        )
        if lock:
            authority_statement = authority_statement.with_for_update()
        authorities = tuple(self._session.scalars(authority_statement))
        authority_by_id = {row.id: row for row in authorities}
        company = authority_by_id.get(project.primary_company_id)
        securities = tuple(
            authority_by_id[row.security_id]
            for row in memberships
            if row.security_id in authority_by_id
        )
        if company is None or len(securities) != len(memberships):
            raise CompanyResearchIntegrityError(
                "company research project identity is incomplete"
            )

        historical_basis_id = draft_content.historical_basis_id
        if historical_basis_id is None:
            raise CompanyResearchIntegrityError(
                "company research historical basis is missing"
            )
        historical_basis_statement = (
            select(UnderwritingHistoricalBasis)
            .where(UnderwritingHistoricalBasis.id == historical_basis_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            historical_basis_statement = historical_basis_statement.with_for_update()
        historical_basis = self._session.scalar(historical_basis_statement)
        if historical_basis is None:
            raise CompanyResearchIntegrityError(
                "company research historical basis is invalid"
            )
        authenticated_basis = self.authenticate_historical_basis(historical_basis)

        foundation_heads: list[object | None] = []
        for model in (
            UnderwritingMandateVersion,
            UnderwritingResearchScopeVersion,
            UnderwritingResearchAgendaVersion,
        ):
            foundation_statement = (
                select(model)
                .where(model.project_id == project_id)
                .order_by(model.version.desc(), model.id.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if lock:
                foundation_statement = foundation_statement.with_for_update()
            foundation_heads.append(self._session.scalar(foundation_statement))

        artifact_statement = (
            select(CompanyResearchArtifactVersion)
            .where(CompanyResearchArtifactVersion.project_id == project_id)
            .order_by(
                CompanyResearchArtifactVersion.kind,
                CompanyResearchArtifactVersion.version,
                CompanyResearchArtifactVersion.id,
            )
            .limit(2049)
            .execution_options(populate_existing=True)
        )
        if lock:
            artifact_statement = artifact_statement.with_for_update()
        all_artifacts = tuple(self._session.scalars(artifact_statement))
        if len(all_artifacts) > 2048:
            raise CompanyResearchIntegrityError(
                "company research artifact history limit exceeded"
            )
        rows_by_kind: dict[str, list[CompanyResearchArtifactVersion]] = {}
        for row in all_artifacts:
            if row.kind not in COMPANY_RESEARCH_ARTIFACT_KINDS:
                raise CompanyResearchIntegrityError(
                    "company research artifact kind is invalid"
                )
            self._validate_artifact_row(row)
            rows_by_kind.setdefault(row.kind, []).append(row)
        artifact_heads: dict[str, CompanyResearchArtifactVersion] = {}
        artifact_chains: dict[str, tuple[CompanyResearchArtifactVersion, ...]] = {}
        for kind in sorted(rows_by_kind):
            try:
                head = self.current_artifact(project_id, kind, lock=lock)
            except ConflictError as exc:
                raise CompanyResearchIntegrityError(
                    "company research artifact history has multiple current heads"
                ) from exc
            if head is None:
                raise CompanyResearchIntegrityError(
                    "company research artifact history is incomplete"
                )
            chain = self.artifact_chain(head.id, lock=lock)
            if len(chain) != len(rows_by_kind[kind]):
                raise CompanyResearchIntegrityError(
                    "company research artifact parent closure is invalid"
                )
            artifact_heads[kind] = head
            artifact_chains[kind] = chain
        events = self.events(preparation.id, lock=lock, fresh=not lock)
        mandate = foundation_heads[0]
        scope = foundation_heads[1]
        agenda = foundation_heads[2]
        return CompanyResearchPublicationState(
            project=project,
            preparation=preparation,
            job=job,
            draft=draft,
            draft_content=draft_content,
            company=company,
            securities=securities,
            memberships=memberships,
            historical_basis=historical_basis,
            authenticated_basis=authenticated_basis,
            mandate=mandate,
            scope=scope,
            agenda=agenda,
            mandate_head_id=mandate.id if mandate is not None else None,
            scope_head_id=scope.id if scope is not None else None,
            agenda_head_id=agenda.id if agenda is not None else None,
            artifact_heads=artifact_heads,
            artifact_chains=artifact_chains,
            events=events,
        )

    def lock_basis_recovery_state(
        self, preparation_id: UUID
    ) -> CompanyResearchBasisRecoveryState:
        """Lock the complete legacy-recovery snapshot in one transaction."""
        project_id = self._session.scalar(
            select(CompanyResearchPreparation.project_id).where(
                CompanyResearchPreparation.id == preparation_id
            )
        )
        if project_id is None:
            raise ValidationError("company research preparation not found")
        self._reserve_sqlite_writer_before_ownership_read()
        project = self._project_for_update(project_id)
        preparation = self._preparation_for_update(
            preparation_id, populate_existing=True
        )
        if preparation is None or preparation.project_id != project_id:
            raise ValidationError("company research preparation not found")
        if preparation.job_id is None:
            raise ValidationError(
                "company research historical basis recovery inputs are incomplete"
            )
        job = self._job_for_update(preparation.job_id, populate_existing=True)
        draft = self._workspace_draft_for_update(project_id)
        if draft is None:
            raise ValidationError(
                "company research historical basis recovery inputs are incomplete"
            )
        draft_content = WorkspaceDraftService.decode_content(draft.content)
        memberships = tuple(
            self._session.scalars(
                select(UnderwritingResearchProjectSecurity)
                .where(
                    UnderwritingResearchProjectSecurity.project_id
                    == preparation.project_id
                )
                .order_by(UnderwritingResearchProjectSecurity.security_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        authority_ids = tuple(
            sorted(
                {
                    *(row.security_id for row in memberships),
                    *((project.primary_company_id,) if project is not None else ()),
                },
                key=str,
            )
        )
        authorities = tuple(
            self._session.scalars(
                select(UnderwritingResearchObject)
                .where(UnderwritingResearchObject.id.in_(authority_ids))
                .order_by(UnderwritingResearchObject.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        authority_by_id = {row.id: row for row in authorities}
        company = (
            authority_by_id.get(project.primary_company_id)
            if project is not None
            else None
        )
        securities = tuple(
            authority_by_id[row.security_id]
            for row in memberships
            if row.security_id in authority_by_id
        )
        foundation_heads = []
        for model in (
            UnderwritingMandateVersion,
            UnderwritingResearchScopeVersion,
            UnderwritingResearchAgendaVersion,
        ):
            foundation_heads.append(
                self._session.scalar(
                    select(model)
                    .where(model.project_id == preparation.project_id)
                    .order_by(model.version.desc(), model.id.desc())
                    .limit(1)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        for model, identifiers in (
            (UnderwritingMandateVersion, (draft_content.mandate_id,)),
            (UnderwritingResearchScopeVersion, (draft_content.scope_id,)),
            (UnderwritingResearchAgendaVersion, (draft_content.agenda_id,)),
            (UnderwritingPriceSnapshot, draft_content.price_snapshot_ids),
            (UnderwritingFXSnapshot, draft_content.fx_snapshot_ids),
            (
                UnderwritingCapitalStructureSnapshot,
                (draft_content.capital_structure_snapshot_id,),
            ),
            (UnderwritingSecurityRightsVersion, draft_content.security_rights_ids),
        ):
            ids = tuple(value for value in identifiers if value is not None)
            if ids:
                tuple(
                    self._session.scalars(
                        select(model)
                        .where(model.id.in_(ids))
                        .order_by(model.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
        evidence = self.current_artifact(
            preparation.project_id, "evidence_index", lock=True
        )
        research_gaps = self.current_artifact(
            preparation.project_id, "research_gaps", lock=True
        )
        if job is None or evidence is None or research_gaps is None:
            raise ValidationError(
                "company research historical basis recovery inputs are incomplete"
            )
        if not self.is_exact_prepare_job_owner(job, preparation.id):
            raise ValidationError(
                "company research historical basis recovery inputs are incomplete"
            )
        evidence_chain = self.artifact_chain(evidence.id, lock=True)
        research_gaps_chain = self.artifact_chain(research_gaps.id, lock=True)
        events = self.events(preparation.id, lock=True)
        return CompanyResearchBasisRecoveryState(
            preparation=preparation,
            job=job,
            draft=draft,
            draft_content=draft_content,
            project=project,
            company=company,
            securities=securities,
            memberships=memberships,
            mandate=foundation_heads[0],
            scope=foundation_heads[1],
            agenda=foundation_heads[2],
            mandate_head_id=(
                foundation_heads[0].id if foundation_heads[0] is not None else None
            ),
            scope_head_id=(
                foundation_heads[1].id if foundation_heads[1] is not None else None
            ),
            agenda_head_id=(
                foundation_heads[2].id if foundation_heads[2] is not None else None
            ),
            evidence=evidence,
            research_gaps=research_gaps,
            evidence_chain=evidence_chain,
            research_gaps_chain=research_gaps_chain,
            events=events,
        )

    def lock_worker_claim_state(
        self,
        *,
        preparation_id: UUID,
        job_id: UUID,
        skip_locked: bool = False,
    ) -> tuple[CompanyResearchPreparation, Job] | None:
        """Lock one worker ownership boundary in global project-first order."""
        project_id = self._session.scalar(
            select(CompanyResearchPreparation.project_id).where(
                CompanyResearchPreparation.id == preparation_id
            )
        )
        if project_id is None:
            return None
        self._reserve_sqlite_writer_before_ownership_read()
        if self._project_for_update(project_id, skip_locked=skip_locked) is None:
            return None
        preparation = self._preparation_for_update(
            preparation_id, populate_existing=True
        )
        if preparation is None or preparation.project_id != project_id:
            return None
        job = self._job_for_update(job_id, populate_existing=True)
        if job is None or preparation.job_id != job.id:
            return None
        return preparation, job

    def requeue_recoverable_preparation(
        self,
        preparation_id: UUID,
        *,
        updated_at: datetime,
        expected_recovered_basis_id: UUID | None = None,
        locked_state: CompanyResearchRetryState | None = None,
    ) -> CompanyResearchPreparation:
        """Return one recoverable preparation to its initial queued step."""
        when = self._stored_datetime(updated_at, "updated_at")
        if locked_state is None:
            project_id = self._session.scalar(
                select(CompanyResearchPreparation.project_id).where(
                    CompanyResearchPreparation.id == preparation_id
                )
            )
            if project_id is None:
                raise ValidationError("company research preparation not found")
            locked_state = self.lock_retry_state(project_id=project_id, retry_at=when)
        preparation = locked_state.preparation
        job = locked_state.job
        if preparation.id != preparation_id:
            raise ValidationError("company research preparation not found")
        if (
            preparation.next_attempt_at is not None
            and self._persisted_utc(preparation.next_attempt_at) > when
        ):
            raise ValidationError("company research preparation is not ready to retry")
        if (
            expected_recovered_basis_id is None
            and preparation.status != "recoverable_failure"
        ):
            raise ValidationError("company research preparation is not recoverable")
        if expected_recovered_basis_id is not None and (
            preparation.status != "blocked"
            or preparation.current_step != "model_bundle"
            or preparation.progress != 30
            or preparation.last_error_code != "validation_failed"
        ):
            raise ValidationError(
                "company research preparation is not eligible for basis recovery"
            )
        if expected_recovered_basis_id is not None:
            draft = self._workspace_draft_for_update(preparation.project_id)
            if (
                job.status != "failed"
                or job.step != "model_bundle"
                or draft is None
                or WorkspaceDraftService.decode_content(
                    draft.content
                ).historical_basis_id
                != expected_recovered_basis_id
            ):
                raise ValidationError(
                    "company research preparation is not eligible for basis recovery"
                )
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        # Worker backoff has already reserved its next execution attempt by
        # leaving the Job queued.  The synchronous source path leaves a failed
        # Job at the attempt it just consumed, so a manual retry reserves the
        # next attempt here.
        reserve_next_attempt = job.status == "failed"
        preparation.status = (
            "building_model" if preparation.current_step == "model_bundle" else "queued"
        )
        preparation.progress = 25 if preparation.current_step == "model_bundle" else 0
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = when
        if reserve_next_attempt:
            preparation.attempt += 1
            job.attempt += 1
        job.status = "queued"
        job.progress = preparation.progress
        job.error = None
        job.started_at = None
        job.finished_at = None
        try:
            with self._session.begin_nested():
                self._session.flush([preparation, job])
        except IntegrityError as exc:
            raise ConflictError("company research preparation retry conflicts") from exc
        return preparation

    def lock_retry_state(
        self, *, project_id: UUID, retry_at: datetime
    ) -> CompanyResearchRetryState:
        """Lock and freshly revalidate the branch-neutral retry owner."""
        when = self._stored_datetime(retry_at, "retry_at")
        self._reserve_sqlite_writer_before_ownership_read()
        project = self._project_for_update(project_id)
        if project is None:
            raise ValidationError("company research project not found")
        preparation_id = self._session.scalar(
            select(CompanyResearchPreparation.id).where(
                CompanyResearchPreparation.project_id == project_id
            )
        )
        if preparation_id is None:
            raise ValidationError("company research preparation not found")
        preparation = self._preparation_for_update(
            preparation_id, populate_existing=True
        )
        if preparation is None or preparation.project_id != project_id:
            raise ValidationError("company research preparation not found")
        job = self._locked_prepare_job(preparation, populate_existing=True)
        if (
            preparation.next_attempt_at is not None
            and self._persisted_utc(preparation.next_attempt_at) > when
        ):
            raise ValidationError("company research preparation is not ready to retry")
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        return CompanyResearchRetryState(
            project=project, preparation=preparation, job=job
        )

    def complete_evidence_preparation(
        self,
        preparation_id: UUID,
        *,
        input_hash: str,
        evidence_index_payload: Mapping[str, object],
        research_gaps_payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
        created_at: datetime,
        expected_claim_token: str | None = None,
        expected_request_hash: str | None = None,
        expected_strategy_version: str | None = None,
    ) -> tuple[
        CompanyResearchPreparation,
        CompanyResearchArtifactVersion,
        CompanyResearchArtifactVersion,
        Job,
        CompanyResearchEvent,
    ]:
        """Append the two source artifacts and atomically hand off to review."""
        claimed = expected_claim_token is not None
        _project, preparation, job = self._locked_project_preparation_job(
            preparation_id, populate_existing=claimed
        )
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        if (
            expected_request_hash is not None
            and preparation.request_hash != expected_request_hash
        ):
            raise ValidationError("company research preparation input changed")
        if (
            expected_strategy_version is not None
            and preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation strategy changed")
        if claimed:
            if (
                preparation.status != "preparing_sources"
                or preparation.current_step != "evidence_index"
                or job.status != "running"
                or job.claim_token != expected_claim_token
                or job.cancel_requested
            ):
                raise ValidationError("company research preparation claim is stale")
        elif (
            preparation.status != "queued"
            or preparation.current_step != "evidence_index"
        ):
            raise ValidationError(
                "company research preparation is not queued for evidence indexing"
            )
        when = self._stored_datetime(created_at, "created_at")
        with self._session.begin_nested():
            evidence_index = self.append_artifact(
                project_id=preparation.project_id,
                kind="evidence_index",
                input_hash=input_hash,
                payload=evidence_index_payload,
                source_refs=source_refs,
                expected_parent_id=None,
                created_at=when,
            )
            research_gaps = self.append_artifact(
                project_id=preparation.project_id,
                kind="research_gaps",
                input_hash=input_hash,
                payload=research_gaps_payload,
                source_refs=source_refs,
                expected_parent_id=None,
                created_at=when,
            )
            preparation.status = "awaiting_evidence_review"
            preparation.current_step = "research_gaps"
            preparation.progress = 25
            preparation.next_attempt_at = None
            preparation.last_error_code = None
            preparation.updated_at = when
            job.status = "waiting_for_review"
            job.progress = 25
            job.step = "research_gaps"
            job.error = None
            job.finished_at = None
            job.claim_token = None
            event = self.append_event(
                preparation_id=preparation_id,
                event_type="evidence_index_prepared",
                payload={
                    "evidence_index_id": str(evidence_index.id),
                    "research_gaps_id": str(research_gaps.id),
                },
                created_at=when,
            )
            self._session.flush([preparation, job])
        return preparation, evidence_index, research_gaps, job, event

    def complete_business_map_preparation(
        self,
        preparation_id: UUID,
        *,
        created_at: datetime,
        expected_claim_token: str,
        expected_request_hash: str,
        expected_strategy_version: str,
    ) -> tuple[
        CompanyResearchPreparation,
        CompanyResearchArtifactVersion,
        Job,
        CompanyResearchEvent,
    ]:
        """Build the first model artifact from an explicitly reviewed index.

        The source and model jobs deliberately share a single owned Job.  This
        transition therefore validates the exact step and lease before it reads
        the evidence head; a stale source claim can never be reinterpreted as a
        model claim.
        """
        _project, preparation, job = self._locked_project_preparation_job(
            preparation_id, populate_existing=True
        )
        if (
            preparation.status != "building_model"
            or preparation.current_step != "business_map"
            or job.status != "running"
            or job.step != "business_map"
            or job.claim_token != expected_claim_token
            or job.cancel_requested
            or preparation.request_hash != expected_request_hash
            or preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation claim is stale")
        evidence = self.current_artifact(
            preparation.project_id, "evidence_index", lock=True
        )
        if evidence is None or not isinstance(evidence.payload, Mapping):
            raise ValidationError("reviewed evidence index is missing")
        facts = evidence.payload.get("facts")
        if not isinstance(facts, list) or not facts:
            raise ValidationError("reviewed evidence index facts are invalid")
        if any(
            not isinstance(item, Mapping)
            or item.get("review_decision") not in {"confirmed", "rejected"}
            for item in facts
        ):
            raise ValidationError("reviewed evidence index is incomplete")
        modules: dict[str, list[str]] = {}
        for fact in facts:
            module, fact_key = fact.get("business_module"), fact.get("fact_key")
            if (
                not isinstance(module, str)
                or not module
                or not isinstance(fact_key, str)
                or not fact_key
            ):
                raise ValidationError("reviewed evidence index facts are invalid")
            modules.setdefault(module, []).append(fact_key)
        payload = {
            "evidence_index_id": str(evidence.id),
            "evidence_content_hash": evidence.content_hash,
            "modules": [
                {"key": key, "fact_keys": sorted(values)}
                for key, values in sorted(modules.items())
            ],
        }
        when = self._stored_datetime(created_at, "created_at")
        with self._session.begin_nested():
            business_map = self.append_artifact(
                project_id=preparation.project_id,
                kind="business_map",
                input_hash=canonical_hash(
                    {"evidence_content_hash": evidence.content_hash}
                ),
                payload=payload,
                source_refs=evidence.source_refs,
                expected_parent_id=None,
                created_at=when,
            )
            # There is no executable driver compiler yet.  Stop at a named,
            # review-gated boundary rather than silently recomputing evidence
            # or queuing an unsupported stage.
            preparation.status = "awaiting_judgment_review"
            preparation.current_step = "judgment_context"
            preparation.progress = 40
            preparation.next_attempt_at = None
            preparation.last_error_code = None
            preparation.updated_at = when
            job.status = "waiting_for_review"
            job.step = "judgment_context"
            job.progress = 40
            job.error = None
            job.finished_at = None
            job.claim_token = None
            event = self.append_event(
                preparation_id=preparation.id,
                event_type="business_map_prepared",
                payload={
                    "business_map_id": str(business_map.id),
                    "evidence_index_id": str(evidence.id),
                },
                created_at=when,
            )
            self._session.flush([preparation, job])
        return preparation, business_map, job, event

    @staticmethod
    def _artifact_reference(
        row: CompanyResearchArtifactVersion,
    ) -> dict[str, str]:
        return {
            "artifact_id": str(row.id),
            "artifact_kind": row.kind,
            "content_hash": row.content_hash,
        }

    @staticmethod
    def _model_artifact_payload(
        payload: Mapping[str, object],
        *,
        artifact_refs: Sequence[Mapping[str, str]],
        market_snapshot_bindings: Sequence[FrozenMarketSnapshotBinding] = (),
    ) -> dict[str, object]:
        copied = deepcopy(dict(payload))
        if "_lineage" in copied:
            raise ValidationError("company research model bundle lineage is reserved")
        copied["_lineage"] = {
            "artifact_refs": deepcopy(list(artifact_refs)),
            "market_snapshot_ids": [
                str(value.snapshot_id) for value in market_snapshot_bindings
            ],
            "market_snapshot_bindings": [
                CompanyResearchRepository._market_binding_payload(value)
                for value in market_snapshot_bindings
            ],
        }
        return copied

    @staticmethod
    def _market_binding_payload(
        value: FrozenMarketSnapshotBinding,
    ) -> dict[str, object]:
        return {
            "snapshot_id": str(value.snapshot_id),
            "snapshot_kind": value.role.value,
            "snapshot_content_hash": value.snapshot_content_hash,
            "security_external_key": value.security_external_key,
            "source_ref": value.source_ref.canonical_payload(),
            "capture_envelope_id": str(value.capture_envelope_id),
            "capture_content_hash": value.capture_content_hash,
            "provenance_role": value.provenance_role,
            "provider_policy_version": value.provider_policy_version,
            "raw_components": [
                {
                    "raw_file": item.raw_file,
                    "raw_hash": item.raw_hash,
                    "source_url": item.source_url,
                    "source_locator": item.source_locator,
                }
                for item in value.raw_components
            ],
        }

    @staticmethod
    def market_binding_from_payload(
        value: object,
    ) -> FrozenMarketSnapshotBinding:
        if not isinstance(value, Mapping) or set(value) != {
            "snapshot_id",
            "snapshot_kind",
            "snapshot_content_hash",
            "security_external_key",
            "source_ref",
            "capture_envelope_id",
            "capture_content_hash",
            "provenance_role",
            "provider_policy_version",
            "raw_components",
        }:
            raise ValidationError("company research market snapshot binding is invalid")
        source_ref = value["source_ref"]
        components = value["raw_components"]
        if not isinstance(source_ref, Mapping) or not isinstance(components, list):
            raise ValidationError("company research market snapshot binding is invalid")
        try:
            return FrozenMarketSnapshotBinding(
                snapshot_id=UUID(str(value["snapshot_id"])),
                role=FrozenMarketSnapshotRole(str(value["snapshot_kind"])),
                security_external_key=value["security_external_key"],
                source_ref=SourceLineageReference(**dict(source_ref)),
                snapshot_content_hash=value["snapshot_content_hash"],
                capture_envelope_id=UUID(str(value["capture_envelope_id"])),
                capture_content_hash=value["capture_content_hash"],
                provenance_role=value["provenance_role"],
                provider_policy_version=value["provider_policy_version"],
                raw_components=tuple(
                    FrozenRawComponentReference(**dict(item)) for item in components
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "company research market snapshot binding is invalid"
            ) from exc

    @staticmethod
    def _model_artifact_input_hash(
        *,
        request_hash: str,
        artifact_refs: Sequence[Mapping[str, str]],
        historical_basis_id: UUID,
        historical_basis_content_hash: str,
        market_snapshot_bindings: Sequence[FrozenMarketSnapshotBinding] = (),
    ) -> str:
        if type(historical_basis_id) is not UUID:
            raise ValidationError("company research model historical basis is invalid")
        historical_basis_content_hash = CompanyResearchRepository._require_hash(
            historical_basis_content_hash,
            "historical_basis_content_hash",
        )
        binding_payloads = [
            CompanyResearchRepository._market_binding_payload(value)
            for value in market_snapshot_bindings
        ]
        return canonical_hash(
            {
                "request_hash": request_hash,
                "artifact_refs": list(artifact_refs),
                "historical_basis_id": str(historical_basis_id),
                "historical_basis_content_hash": historical_basis_content_hash,
                "market_snapshot_ids": [
                    str(value.snapshot_id) for value in market_snapshot_bindings
                ],
                "market_snapshot_bindings": binding_payloads,
            }
        )

    def validate_market_snapshot_bindings(
        self,
        *,
        project_id: UUID,
        bindings: Sequence[FrozenMarketSnapshotBinding],
        cutoff_at: datetime | None = None,
        fresh: bool = False,
        lock: bool = False,
    ) -> None:
        if not bindings:
            return
        cutoff = (
            self._stored_datetime(cutoff_at, "cutoff_at")
            if cutoff_at is not None
            else None
        )
        project_record = ProductRepository(self._session).project(project_id)
        if project_record is None:
            raise ValidationError("company research market snapshot binding is invalid")
        project, security_ids = project_record
        securities = {
            row.external_key: row.id
            for row in (
                self._session.get(UnderwritingResearchObject, value)
                for value in security_ids
            )
            if row is not None and row.kind == "security"
        }
        expected_roles = (
            {(FrozenMarketSnapshotRole.PRICE, key) for key in securities}
            | {(FrozenMarketSnapshotRole.SECURITY_RIGHTS, key) for key in securities}
            | {
                (FrozenMarketSnapshotRole.FX, None),
                (FrozenMarketSnapshotRole.CAPITAL_STRUCTURE, None),
            }
        )
        actual_roles = {
            (binding.role, binding.security_external_key) for binding in bindings
        }
        if actual_roles != expected_roles or len(bindings) != len(expected_roles):
            raise ValidationError("company research market snapshot binding is invalid")
        expected_fact_keys = {
            (FrozenMarketSnapshotRole.FX, None): "usd_cny_fx",
            (
                FrozenMarketSnapshotRole.CAPITAL_STRUCTURE,
                None,
            ): "capital_structure_usd",
            **{
                (FrozenMarketSnapshotRole.PRICE, key): (
                    "market_price_usd_" + key.lower().replace(":", "_")
                )
                for key in securities
            },
            **{
                (FrozenMarketSnapshotRole.SECURITY_RIGHTS, key): (
                    "security_rights_" + key.lower().replace(":", "_")
                )
                for key in securities
            },
        }
        role_contracts = {
            FrozenMarketSnapshotRole.PRICE: (
                UnderwritingPriceSnapshot,
                price_snapshot_hash,
            ),
            FrozenMarketSnapshotRole.FX: (
                UnderwritingFXSnapshot,
                fx_snapshot_hash,
            ),
            FrozenMarketSnapshotRole.CAPITAL_STRUCTURE: (
                UnderwritingCapitalStructureSnapshot,
                capital_structure_snapshot_hash,
            ),
            FrozenMarketSnapshotRole.SECURITY_RIGHTS: (
                UnderwritingSecurityRightsVersion,
                security_rights_hash,
            ),
        }

        def persisted_row(model, row_id):
            statement = select(model).where(model.id == row_id).limit(1)
            if lock:
                statement = statement.with_for_update()
            if fresh:
                statement = statement.execution_options(populate_existing=True)
            return self._session.scalar(statement)

        for binding in bindings:
            if (
                binding.source_ref.source_role != "frozen_market_snapshot"
                or binding.source_ref.fact_key
                != expected_fact_keys[(binding.role, binding.security_external_key)]
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            model, hasher = role_contracts[binding.role]
            row = persisted_row(model, binding.snapshot_id)
            if (
                row is None
                or row.content_hash != binding.snapshot_content_hash
                or row.content_hash != hasher(row)
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            if cutoff is not None:
                for field in ("market_at", "available_at"):
                    value = getattr(row, field, None)
                    if value is not None and self._persisted_utc(value) > cutoff:
                        raise ValidationError(
                            "company research market snapshot exceeds preparation cutoff"
                        )
                effective_from = getattr(row, "effective_from", None)
                if (
                    effective_from is not None
                    and self._persisted_utc(effective_from) > cutoff
                ):
                    raise ValidationError(
                        "company research market snapshot exceeds preparation cutoff"
                    )
            if binding.role is FrozenMarketSnapshotRole.PRICE and (
                row.security_identity_id
                != securities.get(binding.security_external_key)
                or row.currency != "USD"
                or row.price_type != "official_close"
                or row.adjustment_basis != "unadjusted"
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            if binding.role is FrozenMarketSnapshotRole.SECURITY_RIGHTS and (
                row.security_identity_id
                != securities.get(binding.security_external_key)
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            if binding.role is FrozenMarketSnapshotRole.FX and (
                row.base_currency != "USD"
                or row.quote_currency != "CNY"
                or row.quote_direction != "quote_per_base"
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            if binding.role is FrozenMarketSnapshotRole.CAPITAL_STRUCTURE and (
                row.company_id != project.primary_company_id or row.currency != "USD"
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            capture = persisted_row(
                UnderwritingMarketCaptureEnvelope, binding.capture_envelope_id
            )
            expected_components = [
                {
                    "raw_file": item.raw_file,
                    "raw_hash": item.raw_hash,
                    "source_url": item.source_url,
                    "source_locator": item.source_locator,
                }
                for item in binding.raw_components
            ]
            if (
                capture is None
                or capture.snapshot_kind != binding.role.value
                or capture.snapshot_id != binding.snapshot_id
                or capture.provenance_role != binding.provenance_role
                or capture.content_hash != binding.capture_content_hash
                or capture.content_hash != market_capture_envelope_hash(capture)
                or capture.provider_policy_version != binding.provider_policy_version
                or capture.raw_components != expected_components
                or capture.source_url != binding.source_ref.source_url
                or capture.source_locator != binding.source_ref.source_locator
                or capture.raw_hash != binding.source_ref.raw_hash
            ):
                raise ValidationError(
                    "company research market snapshot binding is invalid"
                )
            if (
                cutoff is not None
                and self._persisted_utc(capture.authenticated_available_at) > cutoff
            ):
                raise ValidationError(
                    "company research market snapshot exceeds preparation cutoff"
                )

    @staticmethod
    def expected_model_source_refs(
        *,
        evidence: CompanyResearchArtifactVersion,
        predecessor_gaps: CompanyResearchArtifactVersion,
        market_snapshot_bindings: Sequence[FrozenMarketSnapshotBinding],
    ) -> tuple[dict[str, str], ...]:
        """Derive the only source-record set a model publication may carry."""
        evidence_refs = evidence_payload_source_refs(evidence.payload)
        persisted_evidence_refs = canonical_source_refs(
            evidence.source_refs,
            field_name="evidence index source refs",
        )
        if persisted_evidence_refs != evidence_refs:
            raise CompanyResearchIntegrityError(
                "evidence index source refs do not match governed facts"
            )
        gap_refs = canonical_source_refs(
            predecessor_gaps.source_refs,
            field_name="predecessor research gaps source refs",
        )
        market_refs = tuple(
            source_record(binding.source_ref) for binding in market_snapshot_bindings
        )
        return canonical_source_refs(
            (*evidence_refs, *gap_refs, *market_refs),
            field_name="company research model source refs",
        )

    def complete_model_bundle(
        self,
        preparation_id: UUID,
        *,
        bundle: CompanyResearchPersistedBundle,
        expected_claim_token: str,
        expected_request_hash: str,
        expected_strategy_version: str,
        created_at: datetime,
    ) -> tuple[
        CompanyResearchPreparation,
        tuple[CompanyResearchArtifactVersion, ...],
    ]:
        """Append one closed model bundle or leave every artifact family unchanged."""
        if type(bundle) is not CompanyResearchPersistedBundle:
            raise ValidationError("company research model bundle is invalid")
        _project, preparation, job = self._locked_project_preparation_job(
            preparation_id, populate_existing=True
        )
        if (
            preparation.status != "building_model"
            or preparation.current_step != "model_bundle"
            or job.status != "running"
            or job.step != "model_bundle"
            or job.claim_token != expected_claim_token
            or job.cancel_requested
            or preparation.request_hash != expected_request_hash
            or preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation claim is stale")
        evidence = self.current_artifact(
            preparation.project_id, "evidence_index", lock=True
        )
        current_gaps = self.current_artifact(
            preparation.project_id, "research_gaps", lock=True
        )
        if evidence is None or current_gaps is None:
            raise ValidationError("reviewed evidence and research gaps are required")
        if (
            evidence.id != bundle.evidence_artifact_id
            or evidence.content_hash != bundle.evidence_content_hash
            or current_gaps.id != bundle.research_gaps_artifact_id
            or current_gaps.content_hash != bundle.research_gaps_content_hash
        ):
            raise ValidationError("company research model inputs are stale")
        evidence_cutoff = self.evidence_cutoff(evidence)
        evidence_source_manifest_hash = self.evidence_source_manifest_hash(evidence)
        workspace_boundary = self.validate_workspace_market_boundary(
            project_id=preparation.project_id,
            bindings=bundle.market_snapshot_bindings,
            expected_cutoff_at=evidence_cutoff,
            expected_source_manifest_hash=evidence_source_manifest_hash,
            expected_historical_basis_id=bundle.historical_basis_id,
            expected_historical_basis_content_hash=bundle.historical_basis_content_hash,
            expected_draft_id=bundle.workspace_draft_id,
            expected_lock_version=bundle.workspace_draft_lock_version,
            lock=True,
        )
        facts = (
            evidence.payload.get("facts")
            if isinstance(evidence.payload, dict)
            else None
        )
        if (
            not isinstance(facts, list)
            or not facts
            or any(
                not isinstance(item, Mapping)
                or item.get("review_decision") not in {"confirmed", "rejected"}
                for item in facts
            )
        ):
            raise ValidationError("reviewed evidence index is incomplete")
        when = self._stored_datetime(created_at, "created_at")
        request_hash = self._require_hash(
            expected_request_hash, "expected_request_hash"
        )
        market_snapshot_bindings = bundle.market_snapshot_bindings
        self.validate_market_snapshot_bindings(
            project_id=preparation.project_id,
            bindings=market_snapshot_bindings,
            cutoff_at=workspace_boundary.cutoff_at,
        )
        model_source_refs = self.expected_model_source_refs(
            evidence=evidence,
            predecessor_gaps=current_gaps,
            market_snapshot_bindings=market_snapshot_bindings,
        )
        if bundle.source_refs != model_source_refs:
            raise ValidationError(
                "company research model source refs do not match governed inputs"
            )
        artifacts: list[CompanyResearchArtifactVersion] = []

        with self._session.begin_nested():

            def append(
                kind: str,
                payload: Mapping[str, object],
                parents: Sequence[CompanyResearchArtifactVersion],
                *,
                artifact_id: UUID | None = None,
            ) -> CompanyResearchArtifactVersion:
                refs = tuple(self._artifact_reference(parent) for parent in parents)
                current = self.current_artifact(preparation.project_id, kind, lock=True)
                row = self.append_artifact(
                    project_id=preparation.project_id,
                    kind=kind,
                    input_hash=self._model_artifact_input_hash(
                        request_hash=request_hash,
                        artifact_refs=refs,
                        historical_basis_id=workspace_boundary.historical_basis_id,
                        historical_basis_content_hash=(
                            workspace_boundary.historical_basis_content_hash
                        ),
                        market_snapshot_bindings=market_snapshot_bindings,
                    ),
                    payload=self._model_artifact_payload(
                        payload,
                        artifact_refs=refs,
                        market_snapshot_bindings=market_snapshot_bindings,
                    ),
                    source_refs=model_source_refs,
                    expected_parent_id=current.id if current is not None else None,
                    created_at=when,
                    artifact_id=artifact_id,
                )
                artifacts.append(row)
                return row

            business_map = append("business_map", bundle.business_map, (evidence,))
            driver_map = append("driver_map", bundle.driver_map, (business_map,))
            financial_bridge = append(
                "financial_bridge", bundle.financial_bridge, (driver_map,)
            )
            scenario_set = append("scenario_set", bundle.scenario_set, (driver_map,))
            valuation_set = (
                append(
                    "valuation_set",
                    bundle.valuation_set,
                    (scenario_set, financial_bridge),
                )
                if bundle.valuation_set is not None
                else None
            )

            model_rows = (
                business_map,
                driver_map,
                financial_bridge,
                scenario_set,
                *((valuation_set,) if valuation_set is not None else ()),
            )
            judgment_context = append(
                "judgment_context",
                bundle.judgment_context,
                (evidence, *model_rows, current_gaps),
            )
            append("memo", bundle.memo, (judgment_context,))

            preparation.status = "awaiting_judgment_review"
            preparation.current_step = "judgment_context"
            preparation.progress = 85
            preparation.next_attempt_at = None
            preparation.last_error_code = None
            preparation.updated_at = when
            job.status = "waiting_for_review"
            job.step = "judgment_context"
            job.progress = 85
            job.error = None
            job.finished_at = None
            job.claim_token = None
            self._session.flush([preparation, job])
        return preparation, tuple(artifacts)

    def fail_evidence_preparation(
        self,
        preparation_id: UUID,
        *,
        error_code: str,
        created_at: datetime,
        expected_claim_token: str | None = None,
        expected_request_hash: str | None = None,
        expected_strategy_version: str | None = None,
    ) -> tuple[CompanyResearchPreparation, Job, CompanyResearchEvent]:
        """Record a recoverable source failure before any source artifact exists."""
        self._reserve_sqlite_writer_before_ownership_read()
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        job = self._locked_prepare_job(preparation)
        self._validate_prepare_job_step(
            preparation_step=preparation.current_step,
            preparation_status=preparation.status,
            job=job,
            persisted=True,
        )
        claimed = expected_claim_token is not None
        if (
            expected_request_hash is not None
            and preparation.request_hash != expected_request_hash
        ):
            raise ValidationError("company research preparation input changed")
        if (
            expected_strategy_version is not None
            and preparation.strategy_version != expected_strategy_version
        ):
            raise ValidationError("company research preparation strategy changed")
        if claimed:
            if (
                preparation.status != "preparing_sources"
                or preparation.current_step != "evidence_index"
                or job.status != "running"
                or job.claim_token != expected_claim_token
                or job.cancel_requested
            ):
                raise ValidationError("company research preparation claim is stale")
        elif (
            preparation.status != "queued"
            or preparation.current_step != "evidence_index"
        ):
            raise ValidationError(
                "company research preparation is not queued for evidence indexing"
            )
        when = self._stored_datetime(created_at, "created_at")
        error_code = self._require_nonempty_text(error_code, "error_code", 96)
        with self._session.begin_nested():
            preparation.status = "recoverable_failure"
            preparation.progress = 0
            preparation.next_attempt_at = None
            preparation.last_error_code = error_code
            preparation.updated_at = when
            job.status = "failed"
            job.progress = 0
            job.error = error_code
            job.finished_at = when
            job.claim_token = None
            event = self.append_event(
                preparation_id=preparation_id,
                event_type="source_preparation_failed",
                payload={"code": error_code, "recoverable": True},
                created_at=when,
            )
            self._session.flush([preparation, job])
        return preparation, job, event

    @staticmethod
    def _validate_artifact_row(row: CompanyResearchArtifactVersion) -> None:
        expected_hash = CompanyResearchRepository.artifact_content_hash(
            project_id=row.project_id,
            kind=row.kind,
            version=row.version,
            supersedes_id=row.supersedes_id,
            parent_content_hash=row.parent_content_hash,
            input_hash=row.input_hash,
            payload=row.payload,
            source_refs=row.source_refs,
        )
        if row.content_hash != expected_hash:
            raise CompanyResearchIntegrityError(
                "company research artifact content hash mismatch"
            )
        if not isinstance(row.payload, dict):
            raise CompanyResearchIntegrityError(
                "company research artifact payload is invalid"
            )
        try:
            CompanyResearchRepository._validate_typed_artifact_payload(
                kind=row.kind,
                payload=row.payload,
                supersedes_id=row.supersedes_id,
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(str(exc)) from exc

    @staticmethod
    def _validate_event_row(row: CompanyResearchEvent) -> None:
        if not isinstance(row.event_type, str) or not isinstance(row.payload, dict):
            raise CompanyResearchIntegrityError(
                "company research event payload is invalid"
            )
        if row.hash_version == 1:
            expected_hash = CompanyResearchRepository.event_content_hash(
                preparation_id=row.preparation_id,
                sequence=row.sequence,
                previous_event_hash=row.previous_event_hash,
                event_type=row.event_type,
                payload=row.payload,
            )
        elif row.hash_version == 2:
            expected_hash = CompanyResearchRepository.event_content_hash_v2(
                preparation_id=row.preparation_id,
                sequence=row.sequence,
                previous_event_hash=row.previous_event_hash,
                event_type=row.event_type,
                payload=row.payload,
                created_at=row.created_at,
            )
        else:
            raise CompanyResearchIntegrityError(
                "company research event hash version is invalid"
            )
        if row.content_hash != expected_hash:
            raise CompanyResearchIntegrityError(
                "company research event content hash mismatch"
            )

    def artifact(
        self, artifact_id: UUID, *, lock: bool = False, fresh: bool = False
    ) -> CompanyResearchArtifactVersion | None:
        statement = select(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.id == artifact_id
        )
        if fresh:
            statement = statement.execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        row = self._session.scalar(statement)
        if row is not None:
            self._validate_artifact_row(row)
        return row

    def append_judgment_confirmation_memo(
        self,
        *,
        state: CompanyResearchPublicationState,
        payload: Mapping[str, object],
        input_hash: str,
        created_at: datetime,
    ) -> CompanyResearchArtifactVersion:
        """Append only the authenticated machine memo's human successor."""
        machine_memo = state.artifact_heads.get("memo")
        if machine_memo is None:
            raise CompanyResearchIntegrityError(
                "company research machine memo is missing"
            )
        return self.append_artifact(
            project_id=state.project.id,
            kind="memo",
            input_hash=input_hash,
            payload=payload,
            source_refs=machine_memo.source_refs,
            expected_parent_id=machine_memo.id,
            created_at=created_at,
        )

    def compare_and_swap_publication_draft(
        self,
        *,
        state: CompanyResearchPublicationState,
        expected_lock_version: int,
        updated_at: datetime,
    ) -> UnderwritingWorkspaceDraft:
        """Increment only the optimistic publication token on the locked draft."""
        if (
            state.draft.project_id != state.project.id
            or state.draft.lock_version != expected_lock_version
        ):
            raise ConflictError("company research workspace draft is stale")
        original_content = deepcopy(state.draft.content)
        original_base_revision_id = state.draft.base_revision_id
        updated = ProductRepository(self._session).compare_and_swap_workspace_draft(
            project_id=state.project.id,
            expected_lock_version=expected_lock_version,
            content=original_content,
            updated_at=updated_at,
        )
        if (
            updated.id != state.draft.id
            or updated.lock_version != expected_lock_version + 1
            or updated.content != original_content
            or updated.base_revision_id != original_base_revision_id
        ):
            raise CompanyResearchIntegrityError(
                "company research publication draft CAS is invalid"
            )
        return updated

    def advance_judgment_confirmation(
        self,
        *,
        state: CompanyResearchPublicationState,
        updated_at: datetime,
    ) -> CompanyResearchPreparation:
        """Advance only the lifecycle projection; the terminal Job is immutable here."""
        preparation = state.preparation
        job = state.job
        if (
            preparation.status != "awaiting_judgment_review"
            or preparation.current_step != "judgment_context"
            or preparation.progress != 85
            or preparation.next_attempt_at is not None
            or preparation.last_error_code is not None
            or job.status != "waiting_for_review"
            or job.step != "judgment_context"
            or job.progress != 85
        ):
            raise ConflictError("company research judgment review is stale")
        when = self._stored_datetime(updated_at, "updated_at")
        if when < self._persisted_utc(preparation.updated_at):
            raise ValidationError(
                "company research judgment confirmation precedes preparation"
            )
        preparation.status = "ready_to_freeze"
        preparation.current_step = "memo"
        preparation.progress = 95
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = when
        self._session.flush([preparation])
        return preparation

    def append_judgment_confirmation_event(
        self,
        *,
        state: CompanyResearchPublicationState,
        machine_memo: CompanyResearchArtifactVersion,
        confirmed_memo: CompanyResearchArtifactVersion,
        assessment_status: str,
        reviewer: str,
        created_at: datetime,
    ) -> CompanyResearchEvent:
        """Append the exact v2 audit binding after the memo successor exists."""
        return self.append_event(
            preparation_id=state.preparation.id,
            event_type="judgment_confirmed",
            payload={
                "machine_memo_id": str(machine_memo.id),
                "machine_memo_content_hash": machine_memo.content_hash,
                "confirmed_memo_id": str(confirmed_memo.id),
                "confirmed_memo_content_hash": confirmed_memo.content_hash,
                "assessment_status": assessment_status,
                "reviewer": reviewer,
            },
            created_at=created_at,
        )

    def append_artifact(
        self,
        *,
        project_id: UUID,
        kind: str,
        input_hash: str,
        payload: Mapping[str, object],
        source_refs: Sequence[Mapping[str, object]],
        expected_parent_id: UUID | None,
        created_at: datetime,
        artifact_id: UUID | None = None,
    ) -> CompanyResearchArtifactVersion:
        if kind not in COMPANY_RESEARCH_ARTIFACT_KINDS:
            raise ValidationError("company research artifact kind is invalid")
        if not isinstance(payload, Mapping):
            raise ValidationError("artifact payload must be an object")
        if isinstance(source_refs, (str, bytes)) or not isinstance(
            source_refs, Sequence
        ):
            raise ValidationError("artifact source_refs must be an array")
        if not all(isinstance(value, Mapping) for value in source_refs):
            raise ValidationError("artifact source_refs must contain objects")
        self._reserve_sqlite_writer_before_artifact_head_read()
        parent = self.current_artifact(project_id, kind, lock=True)
        actual_parent_id = parent.id if parent is not None else None
        if actual_parent_id != expected_parent_id:
            raise StaleParentError("expected parent is not the company artifact head")
        when = self._stored_datetime(created_at, "created_at")
        if parent is not None and when < self._persisted_utc(parent.created_at):
            raise ValidationError(
                "company research artifact created_at precedes its parent"
            )
        self._validate_typed_artifact_payload(
            kind=kind,
            payload=payload,
            supersedes_id=actual_parent_id,
        )
        version = 1 if parent is None else parent.version + 1
        copied_payload = deepcopy(dict(payload))
        copied_refs = deepcopy(list(source_refs))
        input_hash = self._require_hash(input_hash, "input_hash")
        row = CompanyResearchArtifactVersion(
            id=artifact_id,
            project_id=project_id,
            kind=kind,
            version=version,
            supersedes_id=actual_parent_id,
            parent_content_hash=(parent.content_hash if parent is not None else None),
            input_hash=input_hash,
            payload=copied_payload,
            source_refs=copied_refs,
            content_hash=self.artifact_content_hash(
                project_id=project_id,
                kind=kind,
                version=version,
                supersedes_id=actual_parent_id,
                parent_content_hash=(
                    parent.content_hash if parent is not None else None
                ),
                input_hash=input_hash,
                payload=copied_payload,
                source_refs=copied_refs,
            ),
            created_at=when,
        )
        try:
            return self._flush_in_savepoint(row)
        except IntegrityError as exc:
            raise StaleParentError(
                "expected parent is not the company artifact head"
            ) from exc

    def artifact_chain(
        self, artifact_id: UUID, *, lock: bool = False
    ) -> tuple[CompanyResearchArtifactVersion, ...]:
        """Return root-to-leaf chain after iteratively validating every link."""
        seen: set[UUID] = set()
        chain: list[CompanyResearchArtifactVersion] = []
        current_id: UUID | None = artifact_id
        expected_project_id: UUID | None = None
        expected_kind: str | None = None
        expected_version: int | None = None
        while current_id is not None:
            if current_id in seen:
                raise CompanyResearchIntegrityError(
                    "company research artifact chain contains a cycle"
                )
            seen.add(current_id)
            row = self.artifact(current_id, lock=lock)
            if row is None:
                raise CompanyResearchIntegrityError(
                    "company research artifact parent is missing"
                )
            if expected_project_id is not None and (
                row.project_id != expected_project_id
                or row.kind != expected_kind
                or row.version != expected_version
            ):
                raise CompanyResearchIntegrityError(
                    "company research artifact parent chain is invalid"
                )
            if row.supersedes_id is None:
                if row.parent_content_hash is not None:
                    raise CompanyResearchIntegrityError(
                        "company research artifact root has a parent content hash"
                    )
            else:
                parent = self.artifact(row.supersedes_id, lock=lock)
                if parent is None:
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent is missing"
                    )
                if row.parent_content_hash != parent.content_hash:
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent content hash mismatch"
                    )
                if self._persisted_utc(row.created_at) < self._persisted_utc(
                    parent.created_at
                ):
                    raise CompanyResearchIntegrityError(
                        "company research artifact timestamps are not monotonic"
                    )
            chain.append(row)
            expected_project_id = row.project_id
            expected_kind = row.kind
            expected_version = row.version - 1
            current_id = row.supersedes_id
        if not chain:
            raise CompanyResearchIntegrityError(
                "company research artifact chain is empty"
            )
        if chain[-1].version != 1:
            raise CompanyResearchIntegrityError(
                "company research artifact chain has no version-one root"
            )
        return tuple(reversed(chain))

    def current_artifact(
        self, project_id: UUID, kind: str, *, lock: bool = False
    ) -> CompanyResearchArtifactVersion | None:
        if kind not in COMPANY_RESEARCH_ARTIFACT_KINDS:
            raise ValidationError("company research artifact kind is invalid")
        statement = (
            select(CompanyResearchArtifactVersion)
            .where(
                CompanyResearchArtifactVersion.project_id == project_id,
                CompanyResearchArtifactVersion.kind == kind,
            )
            .order_by(
                CompanyResearchArtifactVersion.version.desc(),
                CompanyResearchArtifactVersion.id,
            )
        )
        if lock:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        rows = tuple(self._session.scalars(statement))
        if not rows:
            return None
        rows_by_id = {row.id: row for row in rows}
        for row in rows:
            self._validate_artifact_row(row)

        successor_statement = select(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.supersedes_id.in_(rows_by_id)
        )
        if lock:
            successor_statement = (
                successor_statement.with_for_update().execution_options(
                    populate_existing=True
                )
            )
        successors = tuple(self._session.scalars(successor_statement))
        parent_ids_with_successors: set[UUID] = set()
        for successor in successors:
            self._validate_artifact_row(successor)
            parent = rows_by_id[successor.supersedes_id]
            if (
                successor.project_id != parent.project_id
                or successor.kind != parent.kind
            ):
                raise CompanyResearchIntegrityError(
                    "company research artifact successor crosses project or kind"
                )
            parent_ids_with_successors.add(parent.id)

        for row in rows:
            seen: set[UUID] = set()
            current = row
            while current.supersedes_id is not None:
                if current.id in seen:
                    raise CompanyResearchIntegrityError(
                        "company research artifact chain contains a cycle"
                    )
                seen.add(current.id)
                parent = rows_by_id.get(current.supersedes_id)
                if parent is None:
                    parent = self.artifact(current.supersedes_id)
                    if parent is None:
                        raise CompanyResearchIntegrityError(
                            "company research artifact parent is missing"
                        )
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent crosses project or kind"
                    )
                current = parent

        heads = tuple(row for row in rows if row.id not in parent_ids_with_successors)
        if not heads:
            raise CompanyResearchIntegrityError(
                "company research artifact rows have no current leaf"
            )
        if len(heads) > 1:
            raise ConflictError("multiple current artifact heads")
        self.artifact_chain(heads[0].id)
        return heads[0]

    def append_event(
        self,
        *,
        preparation_id: UUID,
        event_type: str,
        payload: Mapping[str, object],
        created_at: datetime,
    ) -> CompanyResearchEvent:
        self._reserve_sqlite_writer_before_event_append()
        if self._preparation_for_update(preparation_id) is None:
            raise ValidationError("company research preparation not found")
        if not isinstance(payload, Mapping):
            raise ValidationError("company research event payload must be an object")
        event_type = self._require_nonempty_text(event_type, "event_type", 64)
        copied_payload = deepcopy(dict(payload))
        existing_events = self.events(preparation_id)
        predecessor = existing_events[-1] if existing_events else None
        when = self._stored_datetime(created_at, "created_at")
        if predecessor is not None and when < self._persisted_utc(
            predecessor.created_at
        ):
            raise ValidationError(
                "company research event created_at precedes its predecessor"
            )
        sequence = 1 if predecessor is None else predecessor.sequence + 1
        previous_event_hash = (
            predecessor.content_hash if predecessor is not None else None
        )
        return self._flush_in_savepoint(
            CompanyResearchEvent(
                preparation_id=preparation_id,
                sequence=sequence,
                hash_version=2,
                previous_event_hash=previous_event_hash,
                event_type=event_type,
                payload=copied_payload,
                content_hash=self.event_content_hash_v2(
                    preparation_id=preparation_id,
                    sequence=sequence,
                    previous_event_hash=previous_event_hash,
                    event_type=event_type,
                    payload=copied_payload,
                    created_at=when,
                ),
                created_at=when,
            )
        )

    def events(
        self, preparation_id: UUID, *, lock: bool = False, fresh: bool = False
    ) -> tuple[CompanyResearchEvent, ...]:
        statement = (
            select(CompanyResearchEvent)
            .where(CompanyResearchEvent.preparation_id == preparation_id)
            .order_by(CompanyResearchEvent.sequence)
        )
        if fresh:
            statement = statement.execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        rows = tuple(self._session.scalars(statement))
        previous_hash: str | None = None
        previous_created_at: datetime | None = None
        v2_started = False
        for expected_sequence, row in enumerate(rows, start=1):
            self._validate_event_row(row)
            if row.hash_version == 1 and v2_started:
                raise CompanyResearchIntegrityError(
                    "company research event hash version regressed"
                )
            v2_started = v2_started or row.hash_version == 2
            if row.sequence != expected_sequence:
                raise CompanyResearchIntegrityError(
                    "company research event sequence is invalid"
                )
            if row.previous_event_hash != previous_hash:
                raise CompanyResearchIntegrityError(
                    "company research event predecessor hash mismatch"
                )
            created_at = self._persisted_utc(row.created_at)
            if previous_created_at is not None and created_at < previous_created_at:
                raise CompanyResearchIntegrityError(
                    "company research event timestamps are not monotonic"
                )
            previous_hash = row.content_hash
            previous_created_at = created_at
        return rows

    def prepare_job(self, preparation_id: UUID) -> Job | None:
        """Return the preparation's bound job after verifying its discriminator."""
        preparation = self.preparation(preparation_id)
        if preparation is None or preparation.job_id is None:
            return None
        job = self._session.get(Job, preparation.job_id)
        if not self.is_exact_prepare_job_owner(job, preparation.id):
            raise CompanyResearchIntegrityError(
                "company research preparation job ownership is invalid"
            )
        return job

    def attach_prepare_job(
        self, preparation_id: UUID, job_id: UUID
    ) -> CompanyResearchPreparation:
        """Attach only a generic Job that is scoped to this preparation exactly."""
        self._reserve_sqlite_writer_before_ownership_read()
        preparation = self._preparation_for_update(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        if preparation.job_id is not None:
            if preparation.job_id != job_id:
                raise ConflictError("company research preparation job is already bound")
            job = self._job_for_update(job_id)
            if not self.is_exact_prepare_job_owner(job, preparation.id):
                raise CompanyResearchIntegrityError(
                    "company research preparation job ownership is invalid"
                )
            return preparation
        job = self._job_for_update(job_id)
        if not self.is_exact_prepare_job_owner(job, preparation.id):
            raise ValidationError(
                "company research preparation job ownership is invalid"
            )
        preparation.job_id = job.id
        try:
            with self._session.begin_nested():
                self._session.flush([preparation])
        except IntegrityError as exc:
            raise ConflictError(
                "company research preparation job is already bound"
            ) from exc
        return preparation

    @staticmethod
    def is_exact_prepare_job_owner(job: Job | None, preparation_id: UUID) -> bool:
        return bool(
            job is not None
            and job.kind == _PREPARE_JOB_KIND
            and job.target_type == _PREPARE_JOB_TARGET_TYPE
            and job.target_id == preparation_id
            and job.research_case_id is None
        )
