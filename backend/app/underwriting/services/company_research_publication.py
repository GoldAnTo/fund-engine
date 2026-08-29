"""Atomic human confirmation of an authenticated Company Research memo."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.company_research import CompanyResearchMemoArtifact
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

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REVIEWER = "human:local-user"
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
class _AuthenticatedPublication:
    state: CompanyResearchPublicationState
    workspace: CompanyResearchWorkspace
    machine_or_confirmed_memo: CompanyResearchArtifactVersion
    decoded_memo: CompanyResearchMemoArtifact
    source_contract: CompanyResearchEvidenceCompilation


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
        if not isinstance(lineage, dict) or "research_gaps" not in machine_memo.payload:
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
            or len(gaps_chain) != 1
            or gaps_chain[0].version != 1
            or gaps_chain[0].supersedes_id is not None
            or state.artifact_heads.get("evidence_index") != evidence_chain[-1]
            or state.artifact_heads.get("research_gaps") != gaps_chain[0]
        ):
            raise CompanyResearchIntegrityError(
                "company research governed evidence closure is invalid"
            )
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
                research_gaps=gaps_chain[0],
            )
            reconcile_company_research_evidence_audit(
                preparation=state.preparation,
                evidence_chain=evidence_chain,
                research_gaps=gaps_chain[0],
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
    ) -> None:
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
        preview = build_alphabet_company_research_preview_at_cutoff(
            self._session,
            company_id=state.company.id,
            cutoff_at=cutoff,
        )
        preview_security_ids = tuple(
            sorted((row.object_id for row in preview.securities), key=str)
        )
        if (
            preview.input_hash != state.preparation.request_hash
            or preview.strategy_version != state.preparation.strategy_version
            or preview.company.object_id != state.company.id
            or preview.company.external_key != state.company.external_key
            or preview_security_ids != tuple(sorted(security_ids, key=str))
        ):
            raise CompanyResearchIntegrityError(
                "company research historical boundary identity is invalid"
            )
        if (
            state.mandate is None
            or state.scope is None
            or state.agenda is None
            or state.draft_content.mandate_id != state.mandate_head_id
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

    def _authenticate_workspace_model(
        self, state: CompanyResearchPublicationState
    ) -> CompanyResearchWorkspace:
        if state.draft.base_revision_id is not None:
            raise CompanyResearchIntegrityError(
                "company research judgment draft already names a frozen revision"
            )
        workspace = CompanyResearchWorkbench(
            self._session, now=self._now
        ).workspace(project_id=state.project.id)
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

    def _authenticate_cross_artifact_chronology(
        self,
        state: CompanyResearchPublicationState,
        workspace: CompanyResearchWorkspace,
    ) -> None:
        by_id = {
            row.id: row
            for chain in state.artifact_chains.values()
            for row in chain
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
        self, state: CompanyResearchPublicationState
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
        self._authenticate_identity_foundation_and_basis(
            state, source_contract=source_contract
        )
        workspace = self._authenticate_workspace_model(state)
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
        )

    def _validate_model_claim_audit(
        self,
        *,
        state: CompanyResearchPublicationState,
        machine_memo: CompanyResearchArtifactVersion,
        claim: CompanyResearchEvent,
    ) -> None:
        if (
            claim.event_type != "model_stage_claimed"
            or claim.payload
            != {"stage": "model_bundle", "attempt": state.job.attempt}
            or self._stored_utc(
                claim.created_at, "company research model claim created_at"
            )
            != self._stored_utc(
                state.job.started_at, "company research model job started_at"
            )
            or self._stored_utc(
                machine_memo.created_at,
                "company research machine memo created_at",
            )
            < self._stored_utc(
                claim.created_at, "company research model claim created_at"
            )
        ):
            raise CompanyResearchIntegrityError(
                "company research model audit boundary is incomplete"
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
            or state.draft.lock_version != expected_lock_version + 1
            or len(memo_chain) < 2
            or memo_chain[-1].id != confirmed_memo.id
            or len(judgment_events) != 1
            or state.events[-1].id != judgment_events[0].id
        ):
            raise ConflictError("company research judgment confirmation is stale")
        machine_memo = memo_chain[-2]
        if (
            machine_memo.id != expected_memo_id
            or machine_memo.content_hash != expected_memo_content_hash
            or confirmed_memo.supersedes_id != machine_memo.id
            or confirmed_memo.parent_content_hash != machine_memo.content_hash
            or confirmed_memo.source_refs != machine_memo.source_refs
        ):
            raise ConflictError("company research judgment confirmation conflicts")
        expected_payload, confirmed = self._memo_payload(
            machine_memo, markdown=markdown
        )
        event = judgment_events[0]
        if event.sequence < 2:
            raise CompanyResearchIntegrityError(
                "company research judgment confirmation audit is inconsistent"
            )
        self._validate_model_claim_audit(
            state=state,
            machine_memo=machine_memo,
            claim=state.events[event.sequence - 2],
        )
        expected_event_payload = {
            "machine_memo_id": str(machine_memo.id),
            "machine_memo_content_hash": machine_memo.content_hash,
            "confirmed_memo_id": str(confirmed_memo.id),
            "confirmed_memo_content_hash": confirmed_memo.content_hash,
            "assessment_status": confirmed.assessment_status,
            "reviewer": _REVIEWER,
        }
        confirmed_at = self._stored_utc(
            confirmed_memo.created_at,
            "company research confirmed memo created_at",
        )
        if (
            authenticated.decoded_memo.candidate_status != "human_confirmed"
            or confirmed_memo.payload != expected_payload
            or confirmed_memo.input_hash != machine_memo.input_hash
            or event.hash_version != 2
            or event.payload != expected_event_payload
            or self._stored_utc(
                event.created_at, "company research judgment event created_at"
            )
            != confirmed_at
            or self._stored_utc(
                state.preparation.updated_at,
                "company research preparation updated_at",
            )
            != confirmed_at
            or self._stored_utc(
                state.draft.updated_at, "company research draft updated_at"
            )
            != confirmed_at
        ):
            raise ConflictError("company research judgment confirmation conflicts")
        return self._confirmation_result(
            state=state,
            machine_memo=machine_memo,
            confirmed_memo=confirmed_memo,
            confirmed=confirmed,
            draft_lock_version=state.draft.lock_version,
            confirmed_at=confirmed_at,
        )

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
        self._repository.reserve_publication_writer()
        try:
            with self._session.begin_nested():
                state = self._repository.lock_publication_state(project_id)
                authenticated = self._authenticate_common_state(state)
                current_memo = authenticated.machine_or_confirmed_memo
                if (
                    state.preparation.status == "ready_to_freeze"
                    or authenticated.decoded_memo.candidate_status
                    == "human_confirmed"
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
                confirmed_memo = (
                    self._repository.append_judgment_confirmation_memo(
                        state=state,
                        payload=payload,
                        input_hash=current_memo.input_hash,
                        created_at=when,
                    )
                )
                updated_draft = (
                    self._repository.compare_and_swap_publication_draft(
                        state=state,
                        expected_lock_version=expected_lock_version,
                        updated_at=when,
                    )
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
