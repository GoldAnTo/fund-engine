"""Atomic human confirmation of an authenticated Company Research memo."""

from __future__ import annotations

import re
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from html import escape as html_escape
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchCompany,
    CompanyResearchMemoArtifact,
    CompanyResearchSecurity,
)
from app.underwriting.domain.product_contracts import RevisionBoundaryInput
from app.underwriting.domain.company_research_market_contracts import (
    FrozenMarketSnapshotRole,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchIntegrityError,
    CompanyResearchPublicationState,
    CompanyResearchRepository,
    reconcile_company_research_evidence_audit,
)
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_boundary import (
    resolve_alphabet_company_research_boundary,
)
from app.underwriting.services.company_research_foundation import (
    alphabet_company_research_foundation_contract,
    authenticate_company_research_foundation,
    build_alphabet_company_research_preview_at_cutoff,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchEvidenceCompilation,
    CompanyResearchProviderInput,
    authenticate_governed_reviewed_evidence,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkspace,
    CompanyResearchWorkbench,
)
from app.underwriting.services.product_project import (
    research_project_security_content_hash,
)
from app.underwriting.services.kernel import canonical_hash

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REVIEWER = "human:local-user"
_MANIFEST_SCHEMA = "company-research.revision-manifest.v1"
_BOUNDARY_SCHEMA = "company-research.boundary.v1"
_ASSESSMENT_SCHEMA = "company-research.research-assessment.v1"
_MODEL_VERSION = "company-research-model.v1"
_PUBLICATION_ONLY_MANIFEST_FIELDS = frozenset(
    {
        "boundary_id",
        "assessment_id",
        "preview_manifest_hash",
        "preparation_id",
        "idempotency_key",
        "published_at",
    }
)
_FROZEN_ARTIFACT_KINDS = (
    "evidence_index",
    "research_gaps",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "judgment_context",
    "memo",
)
_REQUIRED_MODEL_KINDS = frozenset(
    {
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "judgment_context",
        "memo",
    }
)


@dataclass(frozen=True, slots=True)
class CompanyResearchJudgmentConfirmation:
    """The exact optimistic boundary produced by one human confirmation."""

    project_id: UUID
    preparation_id: UUID
    draft_id: UUID
    draft_lock_version: int
    machine_memo_id: UUID
    machine_memo_content_hash: str
    confirmed_memo_id: UUID
    confirmed_memo_content_hash: str
    assessment_status: str
    reviewer: Literal["human:local-user"]
    markdown: str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class CompanyResearchFrozenAssessment:
    """Closed investment assessment exposed by preview and frozen replay."""

    answerability: str
    direction: str | None
    confidence: str | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class CompanyResearchFrozenArtifactReference:
    """Exact Company Research artifact identity frozen by a revision manifest."""

    kind: str
    id: UUID
    version: int
    input_hash: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class CompanyResearchPublicationPreview:
    """Canonical, zero-write candidate for one immutable publication."""

    project_id: UUID
    expected_lock_version: int
    company: CompanyResearchCompany
    securities: tuple[CompanyResearchSecurity, ...]
    cutoff_at: datetime
    historical_basis_id: UUID
    historical_basis_content_hash: str
    strategy_version: str
    model_version: str
    assessment: CompanyResearchFrozenAssessment
    value_range: dict[str, str] | None
    return_range: dict[str, str] | None
    blockers: tuple[str, ...]
    strongest_counterevidence: tuple[dict[str, str], ...]
    next_verification_events: tuple[str, ...]
    memo_markdown: str
    artifacts: tuple[CompanyResearchFrozenArtifactReference, ...]
    boundary: RevisionBoundaryInput
    boundary_hash: str
    manifest: dict[str, object]
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class CompanyResearchFrozenRevision:
    """Fail-closed replay of one immutable Company Research revision."""

    id: UUID
    project_id: UUID
    sequence: int
    published_at: datetime
    boundary_id: UUID
    manifest_id: UUID
    manifest_hash: str
    company: CompanyResearchCompany
    securities: tuple[CompanyResearchSecurity, ...]
    cutoff_at: datetime
    historical_basis_id: UUID
    historical_basis_content_hash: str
    strategy_version: str
    model_version: str
    assessment: CompanyResearchFrozenAssessment
    value_range: dict[str, str] | None
    return_range: dict[str, str] | None
    blockers: tuple[str, ...]
    strongest_counterevidence: tuple[dict[str, str], ...]
    next_verification_events: tuple[str, ...]
    memo_markdown: str
    artifacts: tuple[CompanyResearchFrozenArtifactReference, ...]
    preparation_status: Literal["completed"]
    current_step: None
    progress: Literal[100]


@dataclass(frozen=True, slots=True)
class CompanyResearchMarkdownExport:
    """Deterministic UTF-8 Markdown export envelope."""

    filename: str
    media_type: Literal["text/markdown"]
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class _AuthenticatedPublication:
    state: CompanyResearchPublicationState
    workspace: CompanyResearchWorkspace
    machine_or_confirmed_memo: CompanyResearchArtifactVersion
    decoded_memo: CompanyResearchMemoArtifact
    source_contract: CompanyResearchEvidenceCompilation
    legacy_request_cutoff_at: datetime | None


class CompanyResearchPublicationService:
    """Confirm one locked machine judgment without taking over caller commit."""

    def __init__(self, session: Session, *, now) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)

    @staticmethod
    def _uuid(value: object, field: str) -> UUID:
        if type(value) is not UUID:
            raise ValidationError(f"{field} must be a UUID")
        return value

    @staticmethod
    def _expected_lock(value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValidationError("expected_lock_version must be a positive integer")
        return value

    @staticmethod
    def _hash(value: object, field: str) -> str:
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            raise ValidationError(f"{field} must be a lowercase SHA-256 hash")
        return value

    @staticmethod
    def _normalize_markdown(value: object) -> str:
        if not isinstance(value, str):
            raise ValidationError("markdown must be text")
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValidationError("markdown must not be empty")
        if len(normalized) > 100000:
            raise ValidationError("markdown is too long")
        return normalized

    @staticmethod
    def _stored_utc(value: object, field: str) -> datetime:
        if not isinstance(value, datetime):
            raise CompanyResearchIntegrityError(f"{field} is invalid")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        if value.utcoffset() is None:
            raise CompanyResearchIntegrityError(f"{field} is invalid")
        return value.astimezone(UTC)

    def _utcnow(self) -> datetime:
        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _memo_payload(
        machine_memo: CompanyResearchArtifactVersion,
        *,
        markdown: str,
    ) -> tuple[dict[str, object], CompanyResearchMemoArtifact]:
        if not isinstance(machine_memo.payload, dict):
            raise CompanyResearchIntegrityError(
                "company research machine memo payload is invalid"
            )
        lineage = machine_memo.payload.get("_lineage")
        if not isinstance(lineage, dict):
            raise CompanyResearchIntegrityError(
                "company research machine memo payload is incomplete"
            )
        decoded = CompanyResearchArtifactCodec.decode(
            "memo",
            {
                key: value
                for key, value in machine_memo.payload.items()
                if key != "_lineage"
            },
        )
        if type(decoded) is not CompanyResearchMemoArtifact:
            raise CompanyResearchIntegrityError(
                "company research machine memo payload is invalid"
            )
        if decoded.candidate_status != "machine_draft":
            raise ConflictError("company research memo is already confirmed")
        confirmed = replace(
            decoded,
            candidate_status="human_confirmed",
            reviewer=_REVIEWER,
            markdown=markdown,
        )
        payload = CompanyResearchArtifactCodec.encode("memo", confirmed)
        if "research_gaps" not in machine_memo.payload:
            payload.pop("research_gaps", None)
        payload["_lineage"] = deepcopy(lineage)
        expected = {
            **deepcopy(machine_memo.payload),
            "candidate_status": "human_confirmed",
            "reviewer": _REVIEWER,
            "markdown": markdown,
        }
        if payload != expected:
            raise CompanyResearchIntegrityError(
                "company research memo confirmation changed typed content"
            )
        return payload, confirmed

    def _authenticate_evidence(
        self, state: CompanyResearchPublicationState
    ) -> CompanyResearchEvidenceCompilation:
        evidence_chain = state.artifact_chains.get("evidence_index")
        gaps_chain = state.artifact_chains.get("research_gaps")
        if (
            evidence_chain is None
            or gaps_chain is None
            or len(gaps_chain) not in {1, 2}
            or gaps_chain[0].version != 1
            or gaps_chain[0].supersedes_id is not None
            or state.artifact_heads.get("evidence_index") != evidence_chain[-1]
            or state.artifact_heads.get("research_gaps") != gaps_chain[-1]
        ):
            raise CompanyResearchIntegrityError(
                "company research governed evidence closure is invalid"
            )
        source_gaps = gaps_chain[0]
        try:
            source_contract = authenticate_governed_reviewed_evidence(
                provider_input=CompanyResearchProviderInput(
                    preparation_id=state.preparation.id,
                    project_id=state.project.id,
                    company_external_key=state.company.external_key,
                    request_hash=state.preparation.request_hash,
                    strategy_version=state.preparation.strategy_version,
                ),
                evidence_chain=evidence_chain,
                research_gaps=source_gaps,
            )
            reconcile_company_research_evidence_audit(
                preparation=state.preparation,
                evidence_chain=evidence_chain,
                research_gaps=source_gaps,
                events=state.events,
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research governed evidence closure is invalid"
            ) from exc
        facts = evidence_chain[-1].payload.get("facts")
        if (
            not isinstance(facts, list)
            or not facts
            or any(
                not isinstance(fact, dict)
                or fact.get("review_decision") not in {"confirmed", "rejected"}
                for fact in facts
            )
        ):
            raise CompanyResearchIntegrityError(
                "company research reviewed evidence is incomplete"
            )
        return source_contract

    def _authenticate_identity_foundation_and_basis(
        self,
        state: CompanyResearchPublicationState,
        *,
        source_contract: CompanyResearchEvidenceCompilation,
    ) -> datetime | None:
        evidence = state.artifact_heads["evidence_index"]
        cutoff = self._repository.evidence_cutoff(evidence)
        project_created_at = self._stored_utc(
            state.project.created_at, "company research project created_at"
        )
        preparation_created_at = self._stored_utc(
            state.preparation.created_at,
            "company research preparation created_at",
        )
        security_ids = tuple(row.id for row in state.securities)
        security_keys = tuple(sorted(row.external_key for row in state.securities))
        expected_security_keys = tuple(
            source_contract.evidence_index_payload.get("security_external_keys", ())
        )
        if state.mandate is None or state.scope is None or state.agenda is None:
            raise CompanyResearchIntegrityError(
                "company research foundation boundary is incomplete"
            )
        if (
            state.company.kind != "company"
            or not state.securities
            or any(row.kind != "security" for row in state.securities)
            or len(state.memberships) != len(state.securities)
            or len({row.security_id for row in state.memberships})
            != len(state.memberships)
            or {row.security_id for row in state.memberships} != set(security_ids)
            or any(row.project_id != state.project.id for row in state.memberships)
            or any(
                row.content_hash
                != research_project_security_content_hash(
                    project_id=state.project.id,
                    security_id=row.security_id,
                )
                for row in state.memberships
            )
            or any(
                not project_created_at
                <= self._stored_utc(
                    row.created_at,
                    "company research project membership created_at",
                )
                <= preparation_created_at
                for row in state.memberships
            )
            or security_keys != expected_security_keys
            or state.company.external_key
            != source_contract.evidence_index_payload.get("company_external_key")
        ):
            raise CompanyResearchIntegrityError(
                "company research historical boundary identity is invalid"
            )
        mandate_effective_at = self._stored_utc(
            state.mandate.effective_at,
            "company research mandate effective_at",
        )
        preview = build_alphabet_company_research_preview_at_cutoff(
            self._session,
            company_id=state.company.id,
            cutoff_at=cutoff,
        )
        expected_security_ids = tuple(sorted(security_ids, key=str))

        def preview_matches_request() -> bool:
            return (
                preview.input_hash == state.preparation.request_hash
                and preview.strategy_version == state.preparation.strategy_version
                and preview.company.object_id == state.company.id
                and preview.company.external_key == state.company.external_key
                and tuple(
                    sorted((row.object_id for row in preview.securities), key=str)
                )
                == expected_security_ids
            )

        authenticated_legacy_request_cutoff = None
        if not preview_matches_request():
            preview = build_alphabet_company_research_preview_at_cutoff(
                self._session,
                company_id=state.company.id,
                cutoff_at=mandate_effective_at,
            )
            authenticated_legacy_request_cutoff = mandate_effective_at
        if not preview_matches_request():
            raise CompanyResearchIntegrityError(
                "company research historical boundary identity is invalid"
            )
        if (
            state.draft_content.mandate_id != state.mandate_head_id
            or state.draft_content.scope_id != state.scope_head_id
            or state.draft_content.agenda_id != state.agenda_head_id
            or state.draft_content.historical_basis_id != state.historical_basis.id
            or state.authenticated_basis.id != state.historical_basis.id
        ):
            raise CompanyResearchIntegrityError(
                "company research foundation boundary is incomplete"
            )
        contract = alphabet_company_research_foundation_contract(
            company_external_key=state.company.external_key,
            company_id=state.company.id,
            security_ids=security_ids,
            request_hash=state.preparation.request_hash,
            strategy_version=state.preparation.strategy_version,
        )
        try:
            authenticate_company_research_foundation(
                project=state.project,
                preparation_created_at=state.preparation.created_at,
                contract=contract,
                mandate=state.mandate,
                scope=state.scope,
                agenda=state.agenda,
                authenticated_legacy_request_cutoff_at=(
                    authenticated_legacy_request_cutoff
                ),
            )
            boundary = resolve_alphabet_company_research_boundary(
                cutoff,
                source_manifest_hash=source_contract.input_hash,
            )
            authenticated_basis = (
                self._repository.authenticate_governed_historical_basis(
                    state.historical_basis,
                    expected_input=boundary.basis_input,
                    expected_content_hash=boundary.basis_content_hash,
                )
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research historical foundation boundary is invalid"
            ) from exc
        if authenticated_basis != state.authenticated_basis:
            raise CompanyResearchIntegrityError(
                "company research historical foundation boundary is invalid"
            )
        return authenticated_legacy_request_cutoff

    def _authenticate_workspace_model(
        self, state: CompanyResearchPublicationState, *, lock: bool
    ) -> CompanyResearchWorkspace:
        try:
            workspace = CompanyResearchWorkbench(
                self._session, now=self._now
            ).workspace(
                project_id=state.project.id,
                lock=lock,
                allow_current_heads_after_revision=True,
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research publication model closure is invalid"
            ) from exc
        if (
            workspace.project_id != state.project.id
            or workspace.preparation.id != state.preparation.id
            or workspace.draft.id != state.draft.id
            or workspace.draft.lock_version != state.draft.lock_version
            or not _REQUIRED_MODEL_KINDS
            <= {artifact.kind for artifact in workspace.artifacts}
        ):
            raise CompanyResearchIntegrityError(
                "company research publication workspace is inconsistent"
            )
        for artifact in workspace.artifacts:
            locked = state.artifact_heads.get(artifact.kind)
            if (
                locked is None
                or locked.id != artifact.id
                or locked.content_hash != artifact.content_hash
            ):
                raise CompanyResearchIntegrityError(
                    "company research publication workspace changed during authentication"
                )
        return workspace

    def _authenticate_fresh_market_inputs(
        self, state: CompanyResearchPublicationState, *, lock: bool
    ) -> None:
        judgment = state.artifact_heads.get("judgment_context")
        evidence = state.artifact_heads.get("evidence_index")
        lineage = judgment.payload.get("_lineage") if judgment is not None else None
        binding_payloads = (
            lineage.get("market_snapshot_bindings")
            if isinstance(lineage, dict)
            else None
        )
        if evidence is None or not isinstance(binding_payloads, list):
            raise CompanyResearchIntegrityError(
                "company research publication market boundary is incomplete"
            )
        try:
            bindings = tuple(
                self._repository.market_binding_from_payload(value)
                for value in binding_payloads
            )
            self._repository.validate_market_snapshot_bindings(
                project_id=state.project.id,
                bindings=bindings,
                cutoff_at=self._repository.evidence_cutoff(evidence),
                fresh=True,
                lock=lock,
            )
        except ValidationError as exc:
            raise CompanyResearchIntegrityError(
                "company research publication market boundary is invalid"
            ) from exc

    def _authenticate_cross_artifact_chronology(
        self,
        state: CompanyResearchPublicationState,
        workspace: CompanyResearchWorkspace,
    ) -> None:
        by_id = {
            row.id: row for chain in state.artifact_chains.values() for row in chain
        }
        for artifact in workspace.artifacts:
            if artifact.kind not in _REQUIRED_MODEL_KINDS | {"valuation_set"}:
                continue
            row = by_id[artifact.id]
            lineage = row.payload.get("_lineage")
            refs = lineage.get("artifact_refs") if isinstance(lineage, dict) else None
            if not isinstance(refs, list):
                raise CompanyResearchIntegrityError(
                    "company research artifact parent chronology is invalid"
                )
            child_time = self._stored_utc(
                row.created_at, "company research artifact created_at"
            )
            for ref in refs:
                try:
                    parent_id = UUID(ref["artifact_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent chronology is invalid"
                    ) from exc
                parent = by_id.get(parent_id)
                if (
                    parent is None
                    or ref.get("artifact_kind") != parent.kind
                    or ref.get("content_hash") != parent.content_hash
                    or child_time
                    < self._stored_utc(
                        parent.created_at,
                        "company research parent artifact created_at",
                    )
                ):
                    raise CompanyResearchIntegrityError(
                        "company research artifact parent chronology is invalid"
                    )

    def _authenticate_lifecycle_chronology(
        self, state: CompanyResearchPublicationState
    ) -> None:
        project_created_at = self._stored_utc(
            state.project.created_at, "company research project created_at"
        )
        preparation_created_at = self._stored_utc(
            state.preparation.created_at,
            "company research preparation created_at",
        )
        preparation_updated_at = self._stored_utc(
            state.preparation.updated_at,
            "company research preparation updated_at",
        )
        draft_created_at = self._stored_utc(
            state.draft.created_at, "company research draft created_at"
        )
        draft_updated_at = self._stored_utc(
            state.draft.updated_at, "company research draft updated_at"
        )
        job_created_at = self._stored_utc(
            state.job.created_at, "company research job created_at"
        )
        job_started_at = self._stored_utc(
            state.job.started_at, "company research job started_at"
        )
        if not (
            project_created_at <= preparation_created_at <= preparation_updated_at
            and project_created_at <= draft_created_at <= draft_updated_at
            and project_created_at <= job_created_at <= job_started_at
            and preparation_created_at <= job_started_at <= preparation_updated_at
        ):
            raise CompanyResearchIntegrityError(
                "company research lifecycle chronology is invalid"
            )

    def _authenticate_common_state(
        self, state: CompanyResearchPublicationState, *, lock: bool
    ) -> _AuthenticatedPublication:
        self._authenticate_lifecycle_chronology(state)
        if (
            state.preparation.project_id != state.project.id
            or state.draft.project_id != state.project.id
            or not self._repository.is_exact_prepare_job_owner(
                state.job, state.preparation.id
            )
            or state.job.status != "waiting_for_review"
            or state.job.step != "judgment_context"
            or state.job.progress != 85
            or state.job.error is not None
            or state.job.finished_at is not None
            or state.job.claim_token is not None
            or state.job.cancel_requested
            or state.job.started_at is None
            or state.job.attempt != state.preparation.attempt
        ):
            raise CompanyResearchIntegrityError(
                "company research judgment review job is invalid"
            )
        source_contract = self._authenticate_evidence(state)
        legacy_request_cutoff_at = self._authenticate_identity_foundation_and_basis(
            state, source_contract=source_contract
        )
        workspace = self._authenticate_workspace_model(state, lock=lock)
        self._authenticate_fresh_market_inputs(state, lock=lock)
        self._authenticate_cross_artifact_chronology(state, workspace)
        memo = state.artifact_heads.get("memo")
        if memo is None:
            raise CompanyResearchIntegrityError("company research memo is missing")
        decoded = CompanyResearchArtifactCodec.decode(
            "memo",
            {key: value for key, value in memo.payload.items() if key != "_lineage"},
        )
        if type(decoded) is not CompanyResearchMemoArtifact:
            raise CompanyResearchIntegrityError("company research memo is invalid")
        return _AuthenticatedPublication(
            state=state,
            workspace=workspace,
            machine_or_confirmed_memo=memo,
            decoded_memo=decoded,
            source_contract=source_contract,
            legacy_request_cutoff_at=legacy_request_cutoff_at,
        )

    def _validate_model_claim_audit(
        self,
        *,
        state: CompanyResearchPublicationState,
        machine_memo: CompanyResearchArtifactVersion,
        claim: CompanyResearchEvent,
        allow_legacy_completion_delay: bool,
    ) -> None:
        claim_time = self._stored_utc(
            claim.created_at, "company research model claim created_at"
        )
        model_time = self._stored_utc(
            machine_memo.created_at,
            "company research machine memo created_at",
        )
        if (
            claim.event_type != "model_stage_claimed"
            or claim.payload != {"stage": "model_bundle", "attempt": state.job.attempt}
            or claim_time
            != self._stored_utc(
                state.job.started_at, "company research model job started_at"
            )
            or (
                model_time != claim_time
                and not (allow_legacy_completion_delay and claim_time < model_time)
            )
        ):
            raise CompanyResearchIntegrityError(
                "company research model audit boundary is incomplete"
            )

    def _validate_model_bundle_chronology(
        self,
        *,
        state: CompanyResearchPublicationState,
        machine_memo: CompanyResearchArtifactVersion,
        require_preparation_time: bool,
    ) -> None:
        model_time = self._stored_utc(
            machine_memo.created_at, "company research machine memo created_at"
        )
        bundle_kinds = [
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "judgment_context",
        ]
        if "valuation_set" in state.artifact_heads:
            bundle_kinds.append("valuation_set")
        gaps_chain = state.artifact_chains.get("research_gaps", ())
        if len(gaps_chain) == 2:
            bundle_kinds.append("research_gaps")
        if any(
            self._stored_utc(
                state.artifact_heads[kind].created_at,
                f"company research {kind} created_at",
            )
            != model_time
            for kind in bundle_kinds
        ) or (
            require_preparation_time
            and self._stored_utc(
                state.preparation.updated_at,
                "company research preparation updated_at",
            )
            != model_time
        ):
            raise CompanyResearchIntegrityError(
                "company research model bundle chronology is invalid"
            )

    def _validate_initial_audit(
        self,
        authenticated: _AuthenticatedPublication,
    ) -> None:
        state = authenticated.state
        if (
            any(event.event_type == "judgment_confirmed" for event in state.events)
            or not state.events
        ):
            raise CompanyResearchIntegrityError(
                "company research judgment confirmation audit is inconsistent"
            )
        self._validate_model_claim_audit(
            state=state,
            machine_memo=authenticated.machine_or_confirmed_memo,
            claim=state.events[-1],
            allow_legacy_completion_delay=(
                authenticated.legacy_request_cutoff_at is not None
            ),
        )
        self._validate_model_bundle_chronology(
            state=state,
            machine_memo=authenticated.machine_or_confirmed_memo,
            require_preparation_time=True,
        )

    def _confirmation_result(
        self,
        *,
        state: CompanyResearchPublicationState,
        machine_memo: CompanyResearchArtifactVersion,
        confirmed_memo: CompanyResearchArtifactVersion,
        confirmed: CompanyResearchMemoArtifact,
        draft_lock_version: int,
        confirmed_at: datetime,
    ) -> CompanyResearchJudgmentConfirmation:
        assert confirmed.reviewer == _REVIEWER and confirmed.markdown is not None
        return CompanyResearchJudgmentConfirmation(
            project_id=state.project.id,
            preparation_id=state.preparation.id,
            draft_id=state.draft.id,
            draft_lock_version=draft_lock_version,
            machine_memo_id=machine_memo.id,
            machine_memo_content_hash=machine_memo.content_hash,
            confirmed_memo_id=confirmed_memo.id,
            confirmed_memo_content_hash=confirmed_memo.content_hash,
            assessment_status=confirmed.assessment_status,
            reviewer=_REVIEWER,
            markdown=confirmed.markdown,
            confirmed_at=confirmed_at,
        )

    def _idempotent_replay(
        self,
        authenticated: _AuthenticatedPublication,
        *,
        expected_lock_version: int,
        expected_memo_id: UUID,
        expected_memo_content_hash: str,
        markdown: str,
        allow_prior_publication: bool = False,
    ) -> CompanyResearchJudgmentConfirmation:
        state = authenticated.state
        confirmed_memo = authenticated.machine_or_confirmed_memo
        memo_chain = state.artifact_chains.get("memo", ())
        judgment_events = tuple(
            event for event in state.events if event.event_type == "judgment_confirmed"
        )
        if (
            state.preparation.status != "ready_to_freeze"
            or state.preparation.current_step != "memo"
            or state.preparation.progress != 95
            or state.preparation.next_attempt_at is not None
            or state.preparation.last_error_code is not None
            or len(memo_chain) != 2
            or memo_chain[-1].id != confirmed_memo.id
            or len(judgment_events) != 1
            or (
                state.events[-1].id != judgment_events[0].id
                and not allow_prior_publication
            )
            or any(
                event.event_type != "company_research_published"
                for event in state.events[judgment_events[0].sequence :]
            )
        ):
            raise CompanyResearchIntegrityError(
                "company research judgment confirmation projection is inconsistent"
            )
        if state.draft.lock_version != expected_lock_version + 1:
            raise ConflictError("company research judgment confirmation is stale")
        machine_memo = memo_chain[-2]
        if (
            machine_memo.id != expected_memo_id
            or machine_memo.content_hash != expected_memo_content_hash
        ):
            raise ConflictError("company research judgment confirmation conflicts")
        durable_confirmed = authenticated.decoded_memo
        if durable_confirmed.markdown is None:
            raise CompanyResearchIntegrityError(
                "company research confirmed memo is incomplete"
            )
        expected_payload, expected_confirmed = self._memo_payload(
            machine_memo, markdown=durable_confirmed.markdown
        )
        if (
            confirmed_memo.supersedes_id != machine_memo.id
            or confirmed_memo.parent_content_hash != machine_memo.content_hash
            or confirmed_memo.source_refs != machine_memo.source_refs
            or durable_confirmed.candidate_status != "human_confirmed"
            or confirmed_memo.payload != expected_payload
            or confirmed_memo.input_hash != machine_memo.input_hash
            or expected_confirmed != durable_confirmed
        ):
            raise CompanyResearchIntegrityError(
                "company research confirmed memo closure is inconsistent"
            )
        if durable_confirmed.markdown != markdown:
            raise ConflictError("company research judgment confirmation conflicts")
        event = judgment_events[0]
        if event.sequence < 2:
            raise CompanyResearchIntegrityError(
                "company research judgment confirmation audit is inconsistent"
            )
        self._validate_model_claim_audit(
            state=state,
            machine_memo=machine_memo,
            claim=state.events[event.sequence - 2],
            allow_legacy_completion_delay=(
                authenticated.legacy_request_cutoff_at is not None
            ),
        )
        self._validate_model_bundle_chronology(
            state=state,
            machine_memo=machine_memo,
            require_preparation_time=False,
        )
        expected_event_payload = {
            "machine_memo_id": str(machine_memo.id),
            "machine_memo_content_hash": machine_memo.content_hash,
            "confirmed_memo_id": str(confirmed_memo.id),
            "confirmed_memo_content_hash": confirmed_memo.content_hash,
            "assessment_status": durable_confirmed.assessment_status,
            "reviewer": _REVIEWER,
        }
        confirmed_at = self._stored_utc(
            confirmed_memo.created_at,
            "company research confirmed memo created_at",
        )
        if (
            event.hash_version != 2
            or event.payload != expected_event_payload
            or self._stored_utc(
                event.created_at, "company research judgment event created_at"
            )
            != confirmed_at
            or (
                self._stored_utc(
                    state.preparation.updated_at,
                    "company research preparation updated_at",
                )
                < confirmed_at
                if allow_prior_publication
                else self._stored_utc(
                    state.preparation.updated_at,
                    "company research preparation updated_at",
                )
                != confirmed_at
            )
            or (
                self._stored_utc(
                    state.draft.updated_at, "company research draft updated_at"
                )
                < confirmed_at
                if allow_prior_publication
                else self._stored_utc(
                    state.draft.updated_at, "company research draft updated_at"
                )
                != confirmed_at
            )
        ):
            raise CompanyResearchIntegrityError(
                "company research judgment confirmation audit is inconsistent"
            )
        return self._confirmation_result(
            state=state,
            machine_memo=machine_memo,
            confirmed_memo=confirmed_memo,
            confirmed=durable_confirmed,
            draft_lock_version=state.draft.lock_version,
            confirmed_at=confirmed_at,
        )

    @staticmethod
    def _artifact_reference(
        row: CompanyResearchArtifactVersion,
    ) -> CompanyResearchFrozenArtifactReference:
        return CompanyResearchFrozenArtifactReference(
            kind=row.kind,
            id=row.id,
            version=row.version,
            input_hash=row.input_hash,
            content_hash=row.content_hash,
        )

    @staticmethod
    def _artifact_payload(
        value: CompanyResearchFrozenArtifactReference,
    ) -> dict[str, object]:
        return {
            "kind": value.kind,
            "id": str(value.id),
            "version": value.version,
            "input_hash": value.input_hash,
            "content_hash": value.content_hash,
        }

    @staticmethod
    def _boundary_payload(
        project_id: UUID, boundary: RevisionBoundaryInput
    ) -> dict[str, object]:
        return {
            "schema_version": _BOUNDARY_SCHEMA,
            "project_id": str(project_id),
            "historical_basis_id": str(boundary.historical_basis_id),
            "mandate_id": str(boundary.mandate_id),
            "scope_id": str(boundary.scope_id),
            "agenda_id": str(boundary.agenda_id),
            "price_snapshot_ids": [str(value) for value in boundary.price_snapshot_ids],
            "fx_snapshot_ids": [str(value) for value in boundary.fx_snapshot_ids],
            "capital_structure_snapshot_id": str(
                boundary.capital_structure_snapshot_id
            ),
            "security_rights_ids": [
                str(value) for value in boundary.security_rights_ids
            ],
            "parent_revision_id": (
                str(boundary.parent_revision_id)
                if boundary.parent_revision_id is not None
                else None
            ),
        }

    def _preview_from_authenticated(
        self,
        authenticated: _AuthenticatedPublication,
        *,
        expected_lock_version: int,
    ) -> CompanyResearchPublicationPreview:
        state = authenticated.state
        memo = authenticated.decoded_memo
        if (
            state.draft.lock_version != expected_lock_version
            or memo.candidate_status != "human_confirmed"
            or memo.reviewer != _REVIEWER
            or memo.markdown is None
        ):
            raise ConflictError("company research publication preview is stale")
        memo_chain = state.artifact_chains.get("memo", ())
        if len(memo_chain) != 2:
            raise CompanyResearchIntegrityError(
                "company research confirmed memo closure is inconsistent"
            )
        self._idempotent_replay(
            authenticated,
            expected_lock_version=expected_lock_version - 1,
            expected_memo_id=memo_chain[-2].id,
            expected_memo_content_hash=memo_chain[-2].content_hash,
            markdown=memo.markdown,
            allow_prior_publication=state.draft.base_revision_id is not None,
        )
        cutoff = self._repository.evidence_cutoff(
            state.artifact_heads["evidence_index"]
        )
        product = ProductRepository(self._session)
        company_identity = product.effective_identity(state.company.id, cutoff)
        security_identities = tuple(
            product.effective_identity(row.id, cutoff) for row in state.securities
        )
        if company_identity is None or any(
            identity is None for identity in security_identities
        ):
            raise CompanyResearchIntegrityError(
                "company research frozen identity boundary is incomplete"
            )
        company = CompanyResearchCompany(
            object_id=state.company.id,
            external_key=state.company.external_key,
            canonical_name=company_identity.canonical_name,
        )
        securities = tuple(
            CompanyResearchSecurity(
                object_id=row.id,
                company_id=state.company.id,
                external_key=row.external_key,
                canonical_name=identity.canonical_name,
                symbol=identity.symbol,
                exchange=identity.exchange,
                share_class=identity.share_class,
                trading_currency=identity.trading_currency,
            )
            for row, identity in zip(state.securities, security_identities, strict=True)
        )
        content = state.draft_content
        if (
            state.mandate is None
            or state.scope is None
            or state.agenda is None
            or content.capital_structure_snapshot_id is None
        ):
            raise CompanyResearchIntegrityError(
                "company research publication boundary is incomplete"
            )
        parent = product.company_research_revision_head(state.project.id)
        parent_id = parent.id if parent is not None else None
        if state.draft.base_revision_id != parent_id:
            raise ConflictError(
                "company research draft base revision is not the chain head"
            )
        parent_assessment_id: UUID | None = None
        if parent is not None:
            self.revision(state.project.id, parent.id)
            parent_manifest = product.manifest(parent.manifest_id)
            if parent_manifest is None or not isinstance(
                parent_manifest.manifest, dict
            ):
                raise CompanyResearchIntegrityError(
                    "company research parent assessment is invalid"
                )
            parent_assessment_id = self._manifest_uuid(
                parent_manifest.manifest.get("assessment_id"),
                "company research parent assessment reference",
            )
        try:
            boundary = RevisionBoundaryInput(
                historical_basis_id=state.historical_basis.id,
                mandate_id=state.mandate.id,
                scope_id=state.scope.id,
                agenda_id=state.agenda.id,
                price_snapshot_ids=tuple(sorted(content.price_snapshot_ids, key=str)),
                fx_snapshot_ids=tuple(sorted(content.fx_snapshot_ids, key=str)),
                capital_structure_snapshot_id=content.capital_structure_snapshot_id,
                security_rights_ids=tuple(sorted(content.security_rights_ids, key=str)),
                parent_revision_id=parent_id,
            )
        except ValueError as exc:
            raise CompanyResearchIntegrityError(
                "company research publication boundary is invalid"
            ) from exc
        artifacts = tuple(
            self._artifact_reference(state.artifact_heads[kind])
            for kind in _FROZEN_ARTIFACT_KINDS
            if kind in state.artifact_heads
        )
        required_kinds = set(_FROZEN_ARTIFACT_KINDS) - {"valuation_set"}
        if {item.kind for item in artifacts} != required_kinds | (
            {"valuation_set"} if "valuation_set" in state.artifact_heads else set()
        ):
            raise CompanyResearchIntegrityError(
                "company research frozen artifact boundary is incomplete"
            )
        assessment_payload = {
            "schema_version": _ASSESSMENT_SCHEMA,
            "project_id": str(state.project.id),
            "parent_assessment_id": (
                str(parent_assessment_id) if parent_assessment_id is not None else None
            ),
            "answerability": memo.assessment_status,
            "direction": None,
            "confidence": None,
            "publication_status": "user_frozen",
            "blockers": list(memo.gap_keys),
            "resolution_requirements": list(memo.next_verification_events),
            "next_review_at": None,
        }
        assessment = CompanyResearchFrozenAssessment(
            answerability=memo.assessment_status,
            direction=None,
            confidence=None,
            content_hash=canonical_hash(assessment_payload),
        )
        counterevidence = tuple(
            item.canonical_payload() for item in memo.strongest_counterevidence
        )
        boundary_hash = canonical_hash(
            self._boundary_payload(state.project.id, boundary)
        )
        manifest: dict[str, object] = {
            "schema_version": _MANIFEST_SCHEMA,
            "project_id": str(state.project.id),
            "company": company.canonical_payload(),
            "securities": [item.canonical_payload() for item in securities],
            "cutoff_at": cutoff.isoformat(),
            "historical_basis": {
                "id": str(state.historical_basis.id),
                "content_hash": state.historical_basis.content_hash,
            },
            "draft_lock_version": expected_lock_version,
            "strategy_version": state.preparation.strategy_version,
            "model_version": _MODEL_VERSION,
            "boundary_hash": boundary_hash,
            "assessment": assessment_payload,
            "value_range": None,
            "return_range": None,
            "blockers": list(memo.gap_keys),
            "strongest_counterevidence": list(counterevidence),
            "next_verification_events": list(memo.next_verification_events),
            "memo_markdown": memo.markdown,
            "artifacts": [self._artifact_payload(item) for item in artifacts],
        }
        return CompanyResearchPublicationPreview(
            project_id=state.project.id,
            expected_lock_version=expected_lock_version,
            company=company,
            securities=securities,
            cutoff_at=cutoff,
            historical_basis_id=state.historical_basis.id,
            historical_basis_content_hash=state.historical_basis.content_hash,
            strategy_version=state.preparation.strategy_version,
            model_version=_MODEL_VERSION,
            assessment=assessment,
            value_range=None,
            return_range=None,
            blockers=memo.gap_keys,
            strongest_counterevidence=counterevidence,
            next_verification_events=memo.next_verification_events,
            memo_markdown=memo.markdown,
            artifacts=artifacts,
            boundary=boundary,
            boundary_hash=boundary_hash,
            manifest=manifest,
            manifest_hash=canonical_hash(manifest),
        )

    def preview(
        self, *, project_id: UUID, expected_lock_version: int
    ) -> CompanyResearchPublicationPreview:
        """Authenticate and render a publication candidate without writing."""
        project_id = self._uuid(project_id, "project_id")
        expected_lock_version = self._expected_lock(expected_lock_version)
        with self._session.no_autoflush:
            state = self._repository.publication_state(project_id)
            authenticated = self._authenticate_common_state(state, lock=False)
            return self._preview_from_authenticated(
                authenticated, expected_lock_version=expected_lock_version
            )

    @staticmethod
    def _idempotency_key(value: object) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise ValidationError("idempotency_key must not be empty")
        if len(normalized) > 120:
            raise ValidationError("idempotency_key must be at most 120 characters")
        return normalized

    @staticmethod
    def _manifest_dict(value: object, field: str) -> dict[str, object]:
        if not isinstance(value, dict):
            raise CompanyResearchIntegrityError(f"{field} is invalid")
        return value

    @staticmethod
    def _manifest_uuid(value: object, field: str) -> UUID:
        try:
            return UUID(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, AttributeError) as exc:
            raise CompanyResearchIntegrityError(f"{field} is invalid") from exc

    def _revision_content_hash(
        self,
        *,
        project_id: UUID,
        object_id: UUID,
        basis_id: UUID,
        sequence: int,
        boundary_id: UUID,
        manifest_id: UUID,
        manifest_hash: str,
        assessment_id: UUID,
        parent_revision_id: UUID | None,
    ) -> str:
        return canonical_hash(
            {
                "schema_version": "company-research.research-revision.v1",
                "project_id": str(project_id),
                "object_id": str(object_id),
                "basis_id": str(basis_id),
                "version_kind": "company_research",
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

    def revision(
        self, project_id: UUID, revision_id: UUID
    ) -> CompanyResearchFrozenRevision:
        """Replay one revision exclusively from its authenticated frozen links."""
        project_id = self._uuid(project_id, "project_id")
        revision_id = self._uuid(revision_id, "revision_id")
        with self._session.no_autoflush:
            return self._authenticate_revision_node(
                project_id=project_id,
                revision_id=revision_id,
                seen=frozenset(),
            )

    def _authenticate_revision_node(
        self,
        *,
        project_id: UUID,
        revision_id: UUID,
        seen: frozenset[UUID],
    ) -> CompanyResearchFrozenRevision:
        """Authenticate one complete frozen node after its parent node."""
        if revision_id in seen:
            raise CompanyResearchIntegrityError(
                "company research revision lineage is invalid"
            )
        product = ProductRepository(self._session)
        with self._session.no_autoflush:
            row = product.company_research_revision(revision_id)
            if row is None or row.project_id != project_id:
                raise ValidationError("company research revision does not exist")
            if (
                row.version_kind != "company_research"
                or row.manifest_schema != _MANIFEST_SCHEMA
                or row.publication_status != "user_frozen"
                or row.boundary_id is None
                or row.manifest_id is None
            ):
                raise CompanyResearchIntegrityError(
                    "company research revision identity is invalid"
                )
            parent_node = (
                self._authenticate_revision_node(
                    project_id=project_id,
                    revision_id=row.supersedes_id,
                    seen=seen | {revision_id},
                )
                if row.supersedes_id is not None
                else None
            )
            if (
                row.parent_ids
                != ([str(parent_node.id)] if parent_node is not None else [])
                or row.sequence
                != (parent_node.sequence + 1 if parent_node is not None else 1)
                or parent_node is not None
                and parent_node.published_at
                > self._stored_utc(
                    row.created_at, "company research child revision created_at"
                )
            ):
                raise CompanyResearchIntegrityError(
                    "company research revision lineage is invalid"
                )
            manifest_row = product.manifest(row.manifest_id)
            boundary_row = product.boundary(row.boundary_id)
            if (
                manifest_row is None
                or boundary_row is None
                or manifest_row.project_id != project_id
                or boundary_row.project_id != project_id
                or manifest_row.boundary_id != boundary_row.id
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen publication links are invalid"
                )
            manifest = self._manifest_dict(
                manifest_row.manifest, "company research revision manifest"
            )
            expected_manifest_keys = {
                "schema_version",
                "project_id",
                "company",
                "securities",
                "cutoff_at",
                "historical_basis",
                "draft_lock_version",
                "strategy_version",
                "model_version",
                "boundary_hash",
                "assessment",
                "value_range",
                "return_range",
                "blockers",
                "strongest_counterevidence",
                "next_verification_events",
                "memo_markdown",
                "artifacts",
                "boundary_id",
                "assessment_id",
                "preview_manifest_hash",
                "preparation_id",
                "idempotency_key",
                "published_at",
            }
            if (
                set(manifest) != expected_manifest_keys
                or manifest.get("schema_version") != _MANIFEST_SCHEMA
                or manifest.get("project_id") != str(project_id)
                or manifest.get("boundary_id") != str(boundary_row.id)
                or manifest_row.content_hash != canonical_hash(manifest)
            ):
                raise CompanyResearchIntegrityError(
                    "company research revision manifest hash is invalid"
                )
            preparation_id = self._manifest_uuid(
                manifest.get("preparation_id"),
                "company research frozen preparation reference",
            )
            idempotency_key = manifest.get("idempotency_key")
            preview_manifest_hash = manifest.get("preview_manifest_hash")
            preview_projection = {
                key: deepcopy(value)
                for key, value in manifest.items()
                if key not in _PUBLICATION_ONLY_MANIFEST_FIELDS
            }
            if (
                not isinstance(preview_manifest_hash, str)
                or _HASH.fullmatch(preview_manifest_hash) is None
                or canonical_hash(preview_projection) != preview_manifest_hash
            ):
                raise CompanyResearchIntegrityError(
                    "company research preview manifest hash is invalid"
                )
            try:
                canonical_idempotency_key = self._idempotency_key(idempotency_key)
            except ValidationError as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen publication identity is invalid"
                ) from exc
            frozen_preparation = self._repository.frozen_publication_preparation(
                preparation_id
            )
            if (
                canonical_idempotency_key != idempotency_key
                or frozen_preparation is None
                or frozen_preparation.project_id != project_id
                or manifest.get("strategy_version")
                != frozen_preparation.strategy_version
                or manifest.get("model_version") != _MODEL_VERSION
            ):
                message = (
                    "company research frozen preparation owner is invalid"
                    if canonical_idempotency_key == idempotency_key
                    and (
                        frozen_preparation is None
                        or frozen_preparation.project_id != project_id
                    )
                    else "company research frozen publication identity is invalid"
                )
                raise CompanyResearchIntegrityError(message)
            raw_published_at = manifest.get("published_at")
            try:
                published_at = self._stored_utc(
                    datetime.fromisoformat(raw_published_at),
                    "company research frozen publication time",
                )
            except (TypeError, ValueError) as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen publication identity is invalid"
                ) from exc
            publication_events = tuple(
                event
                for event in self._repository.events(preparation_id, fresh=True)
                if event.event_type == "company_research_published"
                and event.payload.get("revision_id") == str(row.id)
            )
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key
                or manifest_row.idempotency_key != idempotency_key
                or raw_published_at != published_at.isoformat()
                or len(publication_events) != 1
                or publication_events[0].payload
                != {
                    "revision_id": str(row.id),
                    "manifest_hash": manifest_row.content_hash,
                    "idempotency_key": idempotency_key,
                }
                or any(
                    self._stored_utc(value, field) != published_at
                    for value, field in (
                        (row.created_at, "company research revision created_at"),
                        (
                            manifest_row.created_at,
                            "company research manifest created_at",
                        ),
                        (
                            boundary_row.created_at,
                            "company research boundary created_at",
                        ),
                        (
                            publication_events[0].created_at,
                            "company research publication event created_at",
                        ),
                    )
                )
                or self._stored_utc(
                    frozen_preparation.created_at,
                    "company research preparation created_at",
                )
                > published_at
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen publication identity is invalid"
                )
            assessment_id = self._manifest_uuid(
                manifest.get("assessment_id"),
                "company research assessment reference",
            )
            assessment_row = product.assessment(assessment_id)
            assessment_payload = self._manifest_dict(
                manifest.get("assessment"), "company research frozen assessment"
            )
            raw_parent_assessment_id = assessment_payload.get("parent_assessment_id")
            parent_assessment_id = (
                None
                if raw_parent_assessment_id is None
                else self._manifest_uuid(
                    raw_parent_assessment_id,
                    "company research parent assessment reference",
                )
            )
            expected_parent_assessment_id: UUID | None = None
            expected_assessment_version = 1
            if parent_node is not None:
                parent_manifest = product.manifest(parent_node.manifest_id)
                if parent_manifest is None or not isinstance(
                    parent_manifest.manifest, dict
                ):
                    raise CompanyResearchIntegrityError(
                        "company research frozen assessment lineage is invalid"
                    )
                expected_parent_assessment_id = self._manifest_uuid(
                    parent_manifest.manifest.get("assessment_id"),
                    "company research parent assessment reference",
                )
                parent_assessment = product.assessment(expected_parent_assessment_id)
                if parent_assessment is None:
                    raise CompanyResearchIntegrityError(
                        "company research frozen assessment lineage is invalid"
                    )
                expected_assessment_version = parent_assessment.version + 1
            if (
                assessment_row is None
                or assessment_row.project_id != project_id
                or assessment_row.version != expected_assessment_version
                or assessment_row.supersedes_id != expected_parent_assessment_id
                or parent_assessment_id != expected_parent_assessment_id
                or set(assessment_payload)
                != {
                    "schema_version",
                    "project_id",
                    "parent_assessment_id",
                    "answerability",
                    "direction",
                    "confidence",
                    "publication_status",
                    "blockers",
                    "resolution_requirements",
                    "next_review_at",
                }
                or assessment_payload.get("schema_version") != _ASSESSMENT_SCHEMA
                or assessment_payload.get("project_id") != str(project_id)
                or assessment_row.supersedes_id != parent_assessment_id
                or assessment_payload.get("answerability")
                != assessment_row.answerability
                or assessment_payload.get("direction") != assessment_row.direction
                or assessment_payload.get("confidence") != assessment_row.confidence
                or assessment_payload.get("publication_status")
                != assessment_row.publication_status
                or assessment_payload.get("blockers") != assessment_row.blockers
                or assessment_payload.get("resolution_requirements")
                != assessment_row.resolution_requirements
                or assessment_payload.get("next_review_at") is not None
                or assessment_row.next_review_at is not None
                or assessment_row.content_hash != canonical_hash(assessment_payload)
                or self._stored_utc(
                    assessment_row.created_at,
                    "company research assessment created_at",
                )
                != published_at
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen assessment is invalid"
                )
            try:
                boundary = RevisionBoundaryInput(
                    historical_basis_id=boundary_row.historical_basis_id,
                    mandate_id=boundary_row.mandate_id,
                    scope_id=boundary_row.scope_id,
                    agenda_id=boundary_row.agenda_id,
                    price_snapshot_ids=tuple(
                        UUID(value) for value in boundary_row.price_snapshot_ids
                    ),
                    fx_snapshot_ids=tuple(
                        UUID(value) for value in boundary_row.fx_snapshot_ids
                    ),
                    capital_structure_snapshot_id=(
                        boundary_row.capital_structure_snapshot_id
                    ),
                    security_rights_ids=tuple(
                        UUID(value) for value in boundary_row.security_rights_ids
                    ),
                    parent_revision_id=boundary_row.parent_revision_id,
                )
            except (TypeError, ValueError) as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen boundary is invalid"
                ) from exc
            boundary_hash = canonical_hash(self._boundary_payload(project_id, boundary))
            if (
                boundary_row.schema_version != _BOUNDARY_SCHEMA
                or boundary_row.content_hash != boundary_hash
                or boundary.parent_revision_id
                != (parent_node.id if parent_node is not None else None)
                or manifest.get("boundary_hash") != boundary_hash
                or row.boundary_id != boundary_row.id
                or row.basis_id != boundary.historical_basis_id
                or row.object_id
                != self._manifest_uuid(
                    self._manifest_dict(
                        manifest.get("company"), "company research frozen company"
                    ).get("object_id"),
                    "company research frozen company object_id",
                )
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen boundary hash is invalid"
                )
            raw_artifacts = manifest.get("artifacts")
            if not isinstance(raw_artifacts, list) or not raw_artifacts:
                raise CompanyResearchIntegrityError(
                    "company research frozen artifact references are invalid"
                )
            artifacts: list[CompanyResearchFrozenArtifactReference] = []
            artifact_rows: dict[str, CompanyResearchArtifactVersion] = {}
            durable_memo: CompanyResearchMemoArtifact | None = None
            for raw in raw_artifacts:
                descriptor = self._manifest_dict(
                    raw, "company research frozen artifact reference"
                )
                if set(descriptor) != {
                    "kind",
                    "id",
                    "version",
                    "input_hash",
                    "content_hash",
                }:
                    raise CompanyResearchIntegrityError(
                        "company research frozen artifact reference is invalid"
                    )
                artifact_id = self._manifest_uuid(
                    descriptor.get("id"), "company research frozen artifact id"
                )
                artifact = self._repository.artifact(artifact_id, fresh=True)
                if (
                    artifact is None
                    or artifact.project_id != project_id
                    or artifact.kind != descriptor.get("kind")
                    or artifact.version != descriptor.get("version")
                    or artifact.input_hash != descriptor.get("input_hash")
                    or artifact.content_hash != descriptor.get("content_hash")
                    or self._stored_utc(
                        artifact.created_at,
                        "company research frozen artifact created_at",
                    )
                    > published_at
                ):
                    raise CompanyResearchIntegrityError(
                        "company research frozen artifact identity is invalid"
                    )
                reference = self._artifact_reference(artifact)
                artifacts.append(reference)
                artifact_rows[artifact.kind] = artifact
                if artifact.kind == "memo":
                    decoded = CompanyResearchArtifactCodec.decode(
                        "memo",
                        {
                            key: value
                            for key, value in artifact.payload.items()
                            if key != "_lineage"
                        },
                    )
                    if type(decoded) is not CompanyResearchMemoArtifact:
                        raise CompanyResearchIntegrityError(
                            "company research frozen memo is invalid"
                        )
                    durable_memo = decoded
            artifact_tuple = tuple(artifacts)
            expected_kinds = tuple(
                kind
                for kind in _FROZEN_ARTIFACT_KINDS
                if kind != "valuation_set"
                or any(item.kind == "valuation_set" for item in artifact_tuple)
            )
            if (
                tuple(item.kind for item in artifact_tuple) != expected_kinds
                or len({item.id for item in artifact_tuple}) != len(artifact_tuple)
                or durable_memo is None
                or durable_memo.candidate_status != "human_confirmed"
                or durable_memo.markdown != manifest.get("memo_markdown")
                or durable_memo.assessment_status != assessment_row.answerability
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen artifact closure is invalid"
                )
            judgment = artifact_rows["judgment_context"]
            judgment_lineage = judgment.payload.get("_lineage")
            binding_payloads = (
                judgment_lineage.get("market_snapshot_bindings")
                if isinstance(judgment_lineage, dict)
                else None
            )
            if not isinstance(binding_payloads, list):
                raise CompanyResearchIntegrityError(
                    "company research frozen market boundary is invalid"
                )
            parent_id = row.supersedes_id
            if row.parent_ids != (
                [str(parent_id)] if parent_id is not None else []
            ) or row.content_hash != self._revision_content_hash(
                project_id=project_id,
                object_id=row.object_id,
                basis_id=row.basis_id,
                sequence=row.sequence,
                boundary_id=boundary_row.id,
                manifest_id=manifest_row.id,
                manifest_hash=manifest_row.content_hash,
                assessment_id=assessment_id,
                parent_revision_id=parent_id,
            ):
                raise CompanyResearchIntegrityError(
                    "company research revision content hash is invalid"
                )
            company_payload = self._manifest_dict(
                manifest.get("company"), "company research frozen company"
            )
            raw_securities = manifest.get("securities")
            security_keys = {
                "object_id",
                "company_id",
                "external_key",
                "canonical_name",
                "symbol",
                "exchange",
                "share_class",
                "trading_currency",
            }
            if (
                set(company_payload) != {"object_id", "external_key", "canonical_name"}
                or not isinstance(raw_securities, list)
                or any(
                    not isinstance(value, dict) or set(value) != security_keys
                    for value in raw_securities
                )
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen securities are invalid"
                )
            try:
                company = CompanyResearchCompany(
                    object_id=UUID(company_payload["object_id"]),
                    external_key=company_payload["external_key"],
                    canonical_name=company_payload["canonical_name"],
                )
                securities = tuple(
                    CompanyResearchSecurity(
                        object_id=UUID(value["object_id"]),
                        company_id=UUID(value["company_id"]),
                        external_key=value["external_key"],
                        canonical_name=value["canonical_name"],
                        symbol=value["symbol"],
                        exchange=value["exchange"],
                        share_class=value["share_class"],
                        trading_currency=value["trading_currency"],
                    )
                    for value in raw_securities
                    if isinstance(value, dict)
                )
                cutoff_at = datetime.fromisoformat(manifest["cutoff_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen identity is invalid"
                ) from exc
            try:
                bindings = tuple(
                    self._repository.market_binding_from_payload(value)
                    for value in binding_payloads
                )
                price_snapshot_ids = tuple(
                    sorted(
                        (
                            value.snapshot_id
                            for value in bindings
                            if value.role is FrozenMarketSnapshotRole.PRICE
                        ),
                        key=str,
                    )
                )
                fx_snapshot_ids = tuple(
                    sorted(
                        (
                            value.snapshot_id
                            for value in bindings
                            if value.role is FrozenMarketSnapshotRole.FX
                        ),
                        key=str,
                    )
                )
                capital_structure_snapshot_ids = tuple(
                    value.snapshot_id
                    for value in bindings
                    if value.role is FrozenMarketSnapshotRole.CAPITAL_STRUCTURE
                )
                security_rights_ids = tuple(
                    sorted(
                        (
                            value.snapshot_id
                            for value in bindings
                            if value.role is FrozenMarketSnapshotRole.SECURITY_RIGHTS
                        ),
                        key=str,
                    )
                )
                if (
                    boundary.price_snapshot_ids != price_snapshot_ids
                    or boundary.fx_snapshot_ids != fx_snapshot_ids
                    or capital_structure_snapshot_ids
                    != (boundary.capital_structure_snapshot_id,)
                    or boundary.security_rights_ids != security_rights_ids
                ):
                    raise ValidationError(
                        "company research frozen boundary snapshot roles are invalid"
                    )
                self._repository.validate_market_snapshot_bindings(
                    project_id=project_id,
                    bindings=bindings,
                    cutoff_at=cutoff_at,
                    fresh=True,
                    lock=False,
                    frozen_company_id=company.object_id,
                    frozen_security_ids={
                        value.external_key: value.object_id for value in securities
                    },
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen market boundary is invalid"
                ) from exc
            historical_basis = self._manifest_dict(
                manifest.get("historical_basis"),
                "company research frozen historical basis",
            )
            basis_row = product.product_basis(boundary.historical_basis_id)
            if basis_row is None:
                raise CompanyResearchIntegrityError(
                    "company research frozen historical basis is missing"
                )
            try:
                authenticated_basis = self._repository.authenticate_historical_basis(
                    basis_row
                )
            except ValidationError as exc:
                raise CompanyResearchIntegrityError(
                    "company research frozen historical basis is invalid"
                ) from exc
            if (
                len(securities) != len(raw_securities)
                or set(historical_basis) != {"id", "content_hash"}
                or historical_basis.get("id") != str(boundary.historical_basis_id)
                or not isinstance(historical_basis.get("content_hash"), str)
                or _HASH.fullmatch(historical_basis["content_hash"]) is None
                or authenticated_basis.id != boundary.historical_basis_id
                or authenticated_basis.content_hash != historical_basis["content_hash"]
                or self._stored_utc(
                    authenticated_basis.cutoff_at,
                    "company research frozen historical cutoff",
                )
                != self._stored_utc(cutoff_at, "company research frozen cutoff")
                or assessment_row.answerability == "not_answerable"
                and (
                    assessment_row.direction is not None
                    or assessment_row.confidence is not None
                    or manifest.get("value_range") is not None
                    or manifest.get("return_range") is not None
                )
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen assessment projection is invalid"
                )
            counterevidence = manifest.get("strongest_counterevidence")
            blockers = manifest.get("blockers")
            next_events = manifest.get("next_verification_events")
            expected_counterevidence = [
                value.canonical_payload()
                for value in durable_memo.strongest_counterevidence
            ]
            if (
                not isinstance(counterevidence, list)
                or not all(isinstance(value, dict) for value in counterevidence)
                or counterevidence != expected_counterevidence
                or blockers != list(durable_memo.gap_keys)
                or next_events != list(durable_memo.next_verification_events)
                or blockers != assessment_row.blockers
                or not isinstance(next_events, list)
                or next_events != assessment_row.resolution_requirements
            ):
                raise CompanyResearchIntegrityError(
                    "company research frozen judgment projection is invalid"
                )
            return CompanyResearchFrozenRevision(
                id=row.id,
                project_id=project_id,
                sequence=row.sequence,
                published_at=published_at,
                boundary_id=boundary_row.id,
                manifest_id=manifest_row.id,
                manifest_hash=manifest_row.content_hash,
                company=company,
                securities=securities,
                cutoff_at=self._stored_utc(cutoff_at, "company research frozen cutoff"),
                historical_basis_id=boundary.historical_basis_id,
                historical_basis_content_hash=historical_basis["content_hash"],
                strategy_version=manifest["strategy_version"],
                model_version=manifest["model_version"],
                assessment=CompanyResearchFrozenAssessment(
                    answerability=assessment_row.answerability,
                    direction=assessment_row.direction,
                    confidence=assessment_row.confidence,
                    content_hash=assessment_row.content_hash,
                ),
                value_range=manifest["value_range"],
                return_range=manifest["return_range"],
                blockers=tuple(blockers),
                strongest_counterevidence=tuple(deepcopy(counterevidence)),
                next_verification_events=tuple(next_events),
                memo_markdown=durable_memo.markdown,
                artifacts=artifact_tuple,
                preparation_status="completed",
                current_step=None,
                progress=100,
            )

    @staticmethod
    def _markdown_json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _markdown_text(cls, value: object) -> str:
        """Render untrusted data as one inert Markdown text fragment."""
        text = (
            cls._markdown_json(value)
            if isinstance(value, (dict, list, tuple))
            else str(value)
        )
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
        text = html_escape(text, quote=False)
        return re.sub(r"([\\`*[\]{}()#+.!|>~-])", r"\\\1", text)

    @classmethod
    def _markdown_code(cls, value: object) -> str:
        """Render one untrusted scalar inside a self-sizing inline-code fence."""
        text = re.sub(
            r"[\x00-\x1f\x7f]+", " ", html_escape(str(value), quote=False)
        ).strip()
        longest = max((len(match) for match in re.findall(r"`+", text)), default=0)
        fence = "`" * max(1, longest + 1)
        padding = " " if text.startswith("`") or text.endswith("`") else ""
        return f"{fence}{padding}{text}{padding}{fence}"

    @staticmethod
    def _markdown_json_fence(payload: str) -> str:
        longest = max((len(match) for match in re.findall(r"`+", payload)), default=0)
        return "`" * max(3, longest + 1)

    def export(
        self, project_id: UUID, revision_id: UUID
    ) -> CompanyResearchMarkdownExport:
        """Render deterministic Markdown from one authenticated frozen revision."""
        frozen = self.revision(project_id, revision_id)
        artifacts = {
            reference.kind: self._repository.artifact(reference.id, fresh=True)
            for reference in frozen.artifacts
        }
        if any(value is None for value in artifacts.values()):
            raise CompanyResearchIntegrityError(
                "company research frozen export artifact is missing"
            )
        evidence_payload = artifacts["evidence_index"].payload
        gaps_payload = artifacts["research_gaps"].payload
        scenario_payload = artifacts["scenario_set"].payload
        facts = evidence_payload.get("facts")
        gaps = gaps_payload.get("gaps")
        if not isinstance(facts, list) or not isinstance(gaps, list):
            raise CompanyResearchIntegrityError(
                "company research frozen export content is invalid"
            )
        text_value = self._markdown_text
        code_value = self._markdown_code
        scenario_json = self._markdown_json(
            {key: value for key, value in scenario_payload.items() if key != "_lineage"}
        )
        json_fence = self._markdown_json_fence(scenario_json)
        lines = [
            f"# {text_value(frozen.company.canonical_name)} Company Research",
            "",
            "## Revision",
            "",
            f"- Revision ID: {code_value(frozen.id)}",
            f"- Sequence: {frozen.sequence}",
            f"- Published at: {frozen.published_at.isoformat()}",
            f"- Manifest hash: {code_value(frozen.manifest_hash)}",
            "",
            "## Company and Securities",
            "",
            (
                f"- Company: {text_value(frozen.company.canonical_name)} "
                f"({code_value(frozen.company.external_key)}, "
                f"{code_value(frozen.company.object_id)})"
            ),
        ]
        lines.extend(
            (
                f"- Security: {text_value(security.canonical_name)} / "
                f"{text_value(security.symbol)} "
                f"({text_value(security.exchange)}, "
                f"{text_value(security.share_class)}, "
                f"{text_value(security.trading_currency)}; "
                f"{code_value(security.external_key)})"
            )
            for security in frozen.securities
        )
        lines.extend(
            [
                "",
                "## Historical Basis",
                "",
                f"- Cutoff: {frozen.cutoff_at.isoformat()}",
                f"- Historical basis ID: {code_value(frozen.historical_basis_id)}",
                (
                    "- Historical basis hash: "
                    f"{code_value(frozen.historical_basis_content_hash)}"
                ),
                "",
                "## Strategy and Model",
                "",
                f"- Strategy version: {code_value(frozen.strategy_version)}",
                f"- Model version: {code_value(frozen.model_version)}",
                "",
                "## Assessment",
                "",
                f"- Answerability: {code_value(frozen.assessment.answerability)}",
                f"- Direction: {text_value(frozen.assessment.direction or 'null')}",
                f"- Confidence: {text_value(frozen.assessment.confidence or 'null')}",
                "- Value range: null",
                "- Return range: null",
            ]
        )
        if "valuation_set" not in artifacts:
            lines.append("- No authenticated market price is bundled.")
        lines.extend(
            [
                "",
                "## Memo",
                "",
                frozen.memo_markdown,
                "",
                "## Evidence",
                "",
            ]
        )
        for fact in facts:
            if not isinstance(fact, dict):
                raise CompanyResearchIntegrityError(
                    "company research frozen evidence fact is invalid"
                )
            lines.append(f"### {text_value(fact.get('fact_key'))}")
            lines.append("")
            lines.append(
                f"- Review decision: {text_value(fact.get('review_decision'))}"
            )
            for key in (
                "business_module",
                "metric_key",
                "value",
                "value_kind",
                "currency",
                "unit",
                "period_start",
                "period_end",
                "source_role",
                "source_url",
                "source_locator",
                "raw_hash",
            ):
                if key in fact:
                    lines.append(f"- {key}: {text_value(fact[key])}")
            lines.append("")
        lines.extend(["## Research Gaps", ""])
        for gap in gaps:
            if not isinstance(gap, dict):
                raise CompanyResearchIntegrityError(
                    "company research frozen research gap is invalid"
                )
            lines.append(
                f"- {code_value(gap.get('code'))} "
                f"[{text_value(gap.get('severity'))}] "
                f"{text_value(gap.get('message'))} "
                f"(module: {code_value(gap.get('module_key'))})"
            )
        lines.extend(
            [
                "",
                "## Assumptions",
                "",
                f"{json_fence}json",
                scenario_json,
                json_fence,
                "",
                "## Strongest Counterevidence",
                "",
            ]
        )
        lines.extend(
            f"- {text_value(value['fact_key'])} — "
            f"{text_value(value['source_locator'])} "
            f"({text_value(value['source_url'])}; {code_value(value['raw_hash'])})"
            for value in frozen.strongest_counterevidence
        )
        lines.extend(["", "## Next Verification Events", ""])
        lines.extend(
            f"- {text_value(value)}" for value in frozen.next_verification_events
        )
        content = "\n".join(lines).rstrip() + "\n"
        key_parts = frozen.company.external_key.split(":")
        slug_source = (key_parts[1] if len(key_parts) > 1 else key_parts[0]).lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug_source).strip("-") or "company"
        return CompanyResearchMarkdownExport(
            filename=f"{slug}-company-research-{frozen.id}.md",
            media_type="text/markdown",
            content=content,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
        )

    def _verified_existing_revision(
        self,
        *,
        project_id: UUID,
        idempotency_key: str,
        expected_manifest_hash: str,
    ) -> CompanyResearchFrozenRevision | None:
        existing = ProductRepository(self._session).revision_for_idempotency(
            project_id, idempotency_key
        )
        if existing is None:
            return None
        replayed = self.revision(project_id, existing.id)
        manifest = ProductRepository(self._session).manifest(existing.manifest_id)
        if (
            manifest is None
            or manifest.manifest.get("preview_manifest_hash") != expected_manifest_hash
        ):
            raise ConflictError("company research idempotency key was reused")
        return replayed

    def _complete_publication(
        self, authenticated: _AuthenticatedPublication, *, created_at: datetime
    ) -> None:
        preparation = authenticated.state.preparation
        if (
            preparation.status != "ready_to_freeze"
            or preparation.current_step != "memo"
            or preparation.progress != 95
        ):
            raise ConflictError("company research publication is stale")
        preparation.status = "completed"
        preparation.current_step = None
        preparation.progress = 100
        preparation.next_attempt_at = None
        preparation.last_error_code = None
        preparation.updated_at = created_at
        self._session.flush([preparation])

    def publish(
        self,
        *,
        project_id: UUID,
        expected_lock_version: int,
        expected_manifest_hash: str,
        idempotency_key: str,
    ) -> CompanyResearchFrozenRevision:
        """Atomically freeze an authenticated Company Research revision."""
        project_id = self._uuid(project_id, "project_id")
        expected_lock_version = self._expected_lock(expected_lock_version)
        expected_manifest_hash = self._hash(
            expected_manifest_hash, "expected_manifest_hash"
        )
        key = self._idempotency_key(idempotency_key)
        self._repository.reserve_publication_writer(project_id)
        if existing := self._verified_existing_revision(
            project_id=project_id,
            idempotency_key=key,
            expected_manifest_hash=expected_manifest_hash,
        ):
            return existing
        product = ProductRepository(self._session)
        try:
            with self._session.begin_nested():
                state = self._repository.lock_publication_state(project_id)
                if (
                    state.preparation.status != "ready_to_freeze"
                    or state.draft.lock_version != expected_lock_version
                ):
                    raise ConflictError("company research publication is stale")
                authenticated = self._authenticate_common_state(state, lock=True)
                preview = self._preview_from_authenticated(
                    authenticated, expected_lock_version=expected_lock_version
                )
                if preview.manifest_hash != expected_manifest_hash:
                    raise ValidationError(
                        "company research publication manifest hash changed"
                    )
                created_at = self._utcnow()
                if created_at < max(
                    self._stored_utc(
                        state.preparation.updated_at,
                        "company research preparation updated_at",
                    ),
                    self._stored_utc(
                        state.draft.updated_at, "company research draft updated_at"
                    ),
                ):
                    raise ValidationError(
                        "company research publication clock regressed"
                    )
                assessment = product.append_assessment(
                    project_id=project_id,
                    answerability=preview.assessment.answerability,
                    direction=preview.assessment.direction,
                    confidence=preview.assessment.confidence,
                    publication_status="user_frozen",
                    blockers=list(preview.blockers),
                    resolution_requirements=list(preview.next_verification_events),
                    next_review_at=None,
                    content_hash=preview.assessment.content_hash,
                    expected_parent_id=(
                        self._manifest_uuid(
                            preview.manifest["assessment"]["parent_assessment_id"],
                            "company research parent assessment reference",
                        )
                        if preview.manifest["assessment"]["parent_assessment_id"]
                        is not None
                        else None
                    ),
                    created_at=created_at,
                )
                boundary = product.append_boundary(
                    project_id=project_id,
                    value=preview.boundary,
                    schema_version=_BOUNDARY_SCHEMA,
                    content_hash=preview.boundary_hash,
                    created_at=created_at,
                )
                manifest_payload = deepcopy(preview.manifest)
                manifest_payload.update(
                    {
                        "boundary_id": str(boundary.id),
                        "assessment_id": str(assessment.id),
                        "preview_manifest_hash": preview.manifest_hash,
                        "preparation_id": str(state.preparation.id),
                        "idempotency_key": key,
                        "published_at": created_at.isoformat(),
                    }
                )
                manifest_hash = canonical_hash(manifest_payload)
                manifest = product.append_manifest(
                    project_id=project_id,
                    boundary_id=boundary.id,
                    idempotency_key=key,
                    manifest=manifest_payload,
                    content_hash=manifest_hash,
                    created_at=created_at,
                )
                revision = product.append_company_research_revision(
                    project_id=project_id,
                    object_id=state.company.id,
                    basis_id=state.historical_basis.id,
                    boundary_id=boundary.id,
                    manifest_id=manifest.id,
                    manifest_hash=manifest_hash,
                    assessment_id=assessment.id,
                    parent_revision_id=preview.boundary.parent_revision_id,
                    created_at=created_at,
                )
                updated_draft = product.reset_draft_after_publish(
                    project_id=project_id,
                    expected_lock_version=expected_lock_version,
                    base_revision_id=revision.id,
                    updated_at=created_at,
                )
                if (
                    updated_draft.base_revision_id != revision.id
                    or updated_draft.lock_version != expected_lock_version + 1
                ):
                    raise CompanyResearchIntegrityError(
                        "company research published draft reset is invalid"
                    )
                self._repository.append_event(
                    preparation_id=state.preparation.id,
                    event_type="company_research_published",
                    payload={
                        "revision_id": str(revision.id),
                        "manifest_hash": manifest_hash,
                        "idempotency_key": key,
                    },
                    created_at=created_at,
                )
                self._complete_publication(authenticated, created_at=created_at)
                return self.revision(project_id, revision.id)
        except IntegrityError as exc:
            if existing := self._verified_existing_revision(
                project_id=project_id,
                idempotency_key=key,
                expected_manifest_hash=expected_manifest_hash,
            ):
                return existing
            raise ConflictError("company research publication conflicted") from exc
        except StaleParentError as exc:
            raise ConflictError("company research publication is concurrent") from exc
        except OperationalError as exc:
            message = str(getattr(exc, "orig", exc)).lower()
            if "locked" in message or "busy" in message:
                raise ConflictError(
                    "company research publication is concurrent"
                ) from exc
            raise

    def confirm_judgment(
        self,
        *,
        project_id: UUID,
        expected_lock_version: int,
        expected_memo_id: UUID,
        expected_memo_content_hash: str,
        markdown: str,
    ) -> CompanyResearchJudgmentConfirmation:
        """Confirm the current machine memo or replay its exact durable result."""
        project_id = self._uuid(project_id, "project_id")
        expected_lock_version = self._expected_lock(expected_lock_version)
        expected_memo_id = self._uuid(expected_memo_id, "expected_memo_id")
        expected_memo_content_hash = self._hash(
            expected_memo_content_hash, "expected_memo_content_hash"
        )
        normalized_markdown = self._normalize_markdown(markdown)
        self._repository.reserve_publication_writer(project_id)
        try:
            with self._session.begin_nested():
                state = self._repository.lock_publication_state(project_id)
                authenticated = self._authenticate_common_state(state, lock=True)
                current_memo = authenticated.machine_or_confirmed_memo
                if (
                    state.preparation.status == "ready_to_freeze"
                    or authenticated.decoded_memo.candidate_status == "human_confirmed"
                ):
                    return self._idempotent_replay(
                        authenticated,
                        expected_lock_version=expected_lock_version,
                        expected_memo_id=expected_memo_id,
                        expected_memo_content_hash=expected_memo_content_hash,
                        markdown=normalized_markdown,
                    )
                if (
                    state.preparation.status != "awaiting_judgment_review"
                    or state.preparation.current_step != "judgment_context"
                    or state.preparation.progress != 85
                    or state.preparation.next_attempt_at is not None
                    or state.preparation.last_error_code is not None
                    or state.draft.lock_version != expected_lock_version
                    or current_memo.id != expected_memo_id
                    or current_memo.content_hash != expected_memo_content_hash
                    or authenticated.decoded_memo.candidate_status != "machine_draft"
                ):
                    raise ConflictError(
                        "company research judgment confirmation is stale"
                    )
                self._validate_initial_audit(authenticated)
                payload, confirmed = self._memo_payload(
                    current_memo, markdown=normalized_markdown
                )
                when = self._utcnow()
                latest_persisted_time = max(
                    self._stored_utc(
                        state.preparation.updated_at,
                        "company research preparation updated_at",
                    ),
                    self._stored_utc(
                        state.draft.updated_at, "company research draft updated_at"
                    ),
                    self._stored_utc(
                        current_memo.created_at,
                        "company research machine memo created_at",
                    ),
                    self._stored_utc(
                        state.events[-1].created_at,
                        "company research event created_at",
                    ),
                )
                if when < latest_persisted_time:
                    raise ValidationError(
                        "company research judgment confirmation clock regressed"
                    )
                confirmed_memo = self._repository.append_judgment_confirmation_memo(
                    state=state,
                    payload=payload,
                    input_hash=current_memo.input_hash,
                    created_at=when,
                )
                updated_draft = self._repository.compare_and_swap_publication_draft(
                    state=state,
                    expected_lock_version=expected_lock_version,
                    updated_at=when,
                )
                self._repository.advance_judgment_confirmation(
                    state=state, updated_at=when
                )
                event = self._repository.append_judgment_confirmation_event(
                    state=state,
                    machine_memo=current_memo,
                    confirmed_memo=confirmed_memo,
                    assessment_status=confirmed.assessment_status,
                    reviewer=_REVIEWER,
                    created_at=when,
                )
                if self._stored_utc(
                    event.created_at, "company research judgment event created_at"
                ) != self._stored_utc(
                    confirmed_memo.created_at,
                    "company research confirmed memo created_at",
                ):
                    raise CompanyResearchIntegrityError(
                        "company research judgment confirmation chronology is invalid"
                    )
                return self._confirmation_result(
                    state=state,
                    machine_memo=current_memo,
                    confirmed_memo=confirmed_memo,
                    confirmed=confirmed,
                    draft_lock_version=updated_draft.lock_version,
                    confirmed_at=when,
                )
        except StaleParentError as exc:
            raise ConflictError(
                "company research judgment confirmation is concurrent"
            ) from exc
        except OperationalError as exc:
            message = str(getattr(exc, "orig", exc)).lower()
            if "locked" in message or "busy" in message:
                raise ConflictError(
                    "company research judgment confirmation is concurrent"
                ) from exc
            raise
