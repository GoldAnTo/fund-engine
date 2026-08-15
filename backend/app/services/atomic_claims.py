"""Candidate admission plus human or governed automatic publication."""

from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.atomic_claims import AtomicClaimDraft
from app.acquisition.policy import B_SCOPE_POLICY
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
    ValidationError,
)
from app.repositories.acquisition import StaleLeaseError
from app.repositories.research import ResearchRepository
from app.services.automatic_admission import (
    ADAPTER_SOURCE_IDENTITY,
    adapter_url_is_authorized,
    automatic_temporal_failures,
    canonical_admission_digest,
    frozen_request_digest,
    lease_write_fence,
    parser_replay_identity,
    trusted_extraction_run,
    worker_replay_identity,
)
from app.services.source_admission import source_contract_is_active
from app.models.source_governance import SourceContract
from app.repositories.research_preparation import ResearchPreparationRepository


_CLAIM_TYPES = frozenset(
    {
        "disclosed_fact",
        "reported_claim",
        "management_attribution",
        "forecast",
        "research_opinion",
    }
)
_AUTHORITY_LEVELS = frozenset(
    {
        "primary_disclosure",
        "licensed_research",
        "secondary_source",
        "user_supplied",
        "unknown",
    }
)
_REVIEW_OUTCOMES = frozenset({"confirmed", "modified", "rejected"})
ATOMIC_CLAIM_NORMALIZER_VERSION = "atomic-claim-normalizer-v1"


class AtomicClaimService:
    def __init__(
        self, session: Session, *, clock=lambda: datetime.now(timezone.utc)
    ) -> None:
        self._session = session
        self._clock = clock

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("atomic-claim clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def admit(
        self, draft: AtomicClaimDraft, *, authority_level: str, run_ref: str
    ) -> AtomicClaimCandidate:
        span = self._session.get(SourceSpan, draft.source_span_id)
        if span is None:
            raise ValidationError("source span not found")
        if draft.claim_type not in _CLAIM_TYPES:
            raise ValidationError("atomic claim type is invalid")
        if authority_level not in _AUTHORITY_LEVELS:
            raise ValidationError("atomic claim authority level is invalid")
        if (
            draft.claim_type == "disclosed_fact"
            and authority_level != "primary_disclosure"
        ):
            raise ValidationError("disclosed facts require primary authority")
        if not run_ref.strip():
            raise ValidationError("atomic claim run_ref must not be empty")
        if (
            draft.quote_start < 0
            or draft.quote_end <= draft.quote_start
            or span.verbatim_text[draft.quote_start : draft.quote_end] != draft.quote
        ):
            raise ValidationError(
                "atomic claim quote must be a continuous source span slice"
            )

        quote_sha256 = hashlib.sha256(draft.quote.encode("utf-8")).hexdigest()
        canonical_payload = {
            "span": str(draft.source_span_id),
            "quote_sha256": quote_sha256,
            "claim_type": draft.claim_type,
            "normalized_text": draft.normalized_text,
            "scope": draft.scope,
        }
        canonical_key = hashlib.sha256(
            json.dumps(
                canonical_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = self._session.scalar(
            select(AtomicClaimCandidate).where(
                AtomicClaimCandidate.canonical_key == canonical_key
            )
        )
        if existing is not None:
            return existing
        candidate = AtomicClaimCandidate(
            source_span_id=draft.source_span_id,
            canonical_key=canonical_key,
            quote=draft.quote,
            quote_start=draft.quote_start,
            quote_end=draft.quote_end,
            quote_sha256=quote_sha256,
            normalized_text=draft.normalized_text,
            claim_type=draft.claim_type,
            assertion_actor=draft.assertion_actor,
            authority_level=authority_level,
            structured_fields={
                "subject": draft.subject,
                "predicate": draft.predicate,
                "object_text": draft.object_text,
                "numeric_value": draft.numeric_value,
                "unit": draft.unit,
                "observed_period": draft.observed_period.isoformat()
                if draft.observed_period
                else None,
                "scope": draft.scope,
                "run_ref": run_ref,
            },
            validation_result={
                "quote_continuous": True,
                "quote_sha256": quote_sha256,
                "normalizer_version": ATOMIC_CLAIM_NORMALIZER_VERSION,
            },
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(candidate)
        self._session.flush()
        return candidate

    def review(
        self,
        candidate_id,
        *,
        outcome: str,
        reviewer: str,
        reason: str,
        idempotency_key: str,
        normalized_text: str | None = None,
        observed_period: date | None = None,
        preparation_locking: Literal["direct", "already_locked"] = "direct",
    ) -> AtomicClaimReview:
        repository = ResearchPreparationRepository(self._session)
        # Candidate rows are always locked before any Case/preparation lock.
        # ``confirm_claims`` already owns its Case → preparation lock after
        # taking this candidate lock, so it must not traverse shared mappings.
        repository.lock_candidate_rows({candidate_id})
        if preparation_locking == "direct":
            repository.lock_preparation_for_candidate_review(candidate_id)
        elif preparation_locking != "already_locked":
            raise ValueError("atomic claim preparation locking mode is invalid")
        if self._session.get(AtomicClaimCandidate, candidate_id) is None:
            raise ValidationError("atomic claim candidate not found")
        if (
            outcome not in _REVIEW_OUTCOMES
            or not reviewer.strip()
            or not reason.strip()
            or not idempotency_key.strip()
        ):
            raise ValidationError("atomic claim review is incomplete or invalid")
        if outcome == "modified" and not (normalized_text or "").strip():
            raise ValidationError(
                "a modified atomic claim requires reviewed normalized_text"
            )
        if outcome != "modified" and (
            normalized_text is not None or observed_period is not None
        ):
            raise ValidationError(
                "only a modified atomic claim may change published fields"
            )
        existing = self._session.scalar(
            select(AtomicClaimReview).where(
                AtomicClaimReview.atomic_claim_candidate_id == candidate_id,
                AtomicClaimReview.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        candidate = self._session.get(AtomicClaimCandidate, candidate_id)
        assert candidate is not None
        statement = None
        if outcome in {"confirmed", "modified"}:
            candidate_period = candidate.structured_fields.get("observed_period")
            statement = SourceStatement(
                source_span_id=candidate.source_span_id,
                atomic_claim_candidate_id=candidate.id,
                kind=candidate.claim_type,
                normalized_text=(normalized_text or candidate.normalized_text).strip(),
                observed_period=observed_period
                or (date.fromisoformat(candidate_period) if candidate_period else None),
                created_at=datetime.now(timezone.utc),
            )
            self._session.add(statement)
            self._session.flush()
        review = AtomicClaimReview(
            atomic_claim_candidate_id=candidate_id,
            outcome=outcome,
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            idempotency_key=idempotency_key.strip(),
            published_source_statement_id=statement.id if statement else None,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(review)
        self._session.flush()
        return review

    def publish_automatically(
        self,
        candidate_id,
        decision_id,
        *,
        lease_token: str,
    ) -> tuple[SourceStatement, EvidenceLink]:
        """Publish an admitted decision without fabricating a human review."""
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise StaleLeaseError("acquisition lease is stale")
        lease_token = lease_token.strip()

        def require_lease(job_id) -> tuple[AcquisitionJob, datetime]:
            checked_at = self._now()
            job_value = self._session.scalar(
                select(AcquisitionJob)
                .where(
                    AcquisitionJob.id == job_id,
                    AcquisitionJob.status == "running",
                    AcquisitionJob.stage == "admitting",
                    AcquisitionJob.lease_token == lease_token,
                    AcquisitionJob.lease_expires_at.is_not(None),
                    AcquisitionJob.lease_expires_at > checked_at,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                job_value is None
                or job_value.lease_expires_at is None
                or self._as_utc(job_value.lease_expires_at) <= checked_at
            ):
                raise StaleLeaseError("acquisition lease is stale")
            return job_value, checked_at

        decision = self._session.scalar(
            select(AutomaticAdmissionDecision)
            .where(AutomaticAdmissionDecision.id == decision_id)
            .with_for_update()
        )
        if decision is None:
            raise ValidationError("automatic-admission decision not found")
        if decision.outcome != "admitted":
            raise ValidationError("automatic-admission decision is quarantined")
        if decision.candidate_id != candidate_id:
            raise ValidationError("automatic-admission candidate mismatch")
        if set(decision.gate_results) != {
            "source",
            "temporal",
            "locator",
            "semantic",
        } or not all(
            isinstance(result, dict) and result.get("passed") is True
            for result in decision.gate_results.values()
        ):
            raise ValidationError("automatic-admission decision audit is invalid")
        job, publication_at = require_lease(decision.job_id)

        candidate = self._session.get(AtomicClaimCandidate, candidate_id)
        artifact = self._session.get(RetrievalArtifact, decision.retrieval_artifact_id)
        if candidate is None or artifact is None:
            raise ValidationError("automatic publication lineage is incomplete")
        reference = self._session.get(SourceReference, artifact.source_reference_id)
        attempt = self._session.get(AcquisitionAttempt, artifact.attempt_id)
        binding = self._session.scalar(
            select(RetrievalArtifactDocument).where(
                RetrievalArtifactDocument.retrieval_artifact_id == artifact.id
            )
        )
        span = self._session.get(SourceSpan, candidate.source_span_id)
        document = (
            self._session.get(DocumentVersion, binding.document_version_id)
            if binding is not None
            else None
        )
        snapshot = (
            job.request_snapshot if isinstance(job.request_snapshot, dict) else {}
        )
        if (
            reference is None
            or attempt is None
            or binding is None
            or span is None
            or document is None
            or reference.job_id != job.id
            or attempt.job_id != job.id
            or attempt.adapter_key != reference.adapter_key
            or attempt.operation != "fetch"
            or attempt.outcome != "succeeded"
            or attempt.finished_at is None
            or not isinstance(attempt.safe_metadata, dict)
            or attempt.safe_metadata.get("source_reference_id") != str(reference.id)
            or span.document_version_id != document.id
            or document.source_url != reference.canonical_url
            or snapshot.get("thesis_id") != str(job.thesis_id)
            or snapshot.get("case_id") != str(job.research_case_id)
        ):
            raise ValidationError("automatic publication lineage mismatch")

        case_attached = self._session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == job.research_case_id,
                CaseDocumentVersion.document_version_id == document.id,
            )
        )
        if case_attached is None:
            raise ValidationError("automatic publication lineage mismatch")

        role = snapshot.get("target_link_role")
        objective = snapshot.get("objective")
        if (
            not isinstance(role, str)
            or not role.strip()
            or not isinstance(objective, str)
        ):
            raise ValidationError("automatic publication request snapshot is invalid")
        semantic_audit = decision.gate_results["semantic"].get("facts", {})
        source_audit = decision.gate_results["source"].get("facts", {})
        if (
            semantic_audit.get("objective") != objective
            or semantic_audit.get("target_link_role") != role
            or source_audit.get("request_policy_version")
            != snapshot.get("source_policy_version")
            or source_audit.get("policy_snapshot_version")
            != (
                job.policy_snapshot.get("version")
                if isinstance(job.policy_snapshot, dict)
                else None
            )
            or decision.policy_version != snapshot.get("source_policy_version")
        ):
            raise ValidationError("automatic publication decision audit mismatch")
        cutoff_raw = snapshot.get("cutoff")
        try:
            cutoff = datetime.fromisoformat(str(cutoff_raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                "automatic publication request cutoff is invalid"
            ) from exc
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValidationError("automatic publication request cutoff is invalid")
        cutoff = self._as_utc(cutoff)

        def validate_authorization(
            current_job: AcquisitionJob, *, checked_at: datetime
        ) -> None:
            for immutable in (
                candidate,
                reference,
                attempt,
                artifact,
                binding,
                document,
                span,
            ):
                self._session.refresh(immutable)
            current_snapshot = (
                current_job.request_snapshot
                if isinstance(current_job.request_snapshot, dict)
                else {}
            )
            current_policy = (
                current_job.policy_snapshot
                if isinstance(current_job.policy_snapshot, dict)
                else {}
            )
            current_digest = frozen_request_digest(current_job)
            recorded_digests = {
                result.get("facts", {}).get("frozen_request_digest")
                for result in decision.gate_results.values()
                if isinstance(result, dict)
            }
            if recorded_digests != {current_digest}:
                raise ValidationError("automatic publication request digest mismatch")
            current_contract = self._session.scalar(
                select(SourceContract)
                .where(SourceContract.document_version_id == document.id)
                .execution_options(populate_existing=True)
            )
            ai_run = trusted_extraction_run(
                self._session, candidate, document, span
            )
            admission_digest = canonical_admission_digest(
                job=current_job,
                candidate=candidate,
                reference=reference,
                attempt=attempt,
                artifact=artifact,
                document=document,
                span=span,
                binding=binding,
                contract=current_contract,
                ai_run=ai_run,
            )
            recorded_admission_digests = {
                result.get("facts", {}).get("admission_digest")
                for result in decision.gate_results.values()
                if isinstance(result, dict)
            }
            normalizer_version = candidate.validation_result.get(
                "normalizer_version"
            )
            replay_identity = {
                "extraction": {
                    "airun_id": str(ai_run.id),
                    "run_ref": candidate.structured_fields.get("run_ref"),
                    "model_version": ai_run.model_version,
                    "prompt_version": ai_run.prompt_version,
                },
                "parser": parser_replay_identity(document.parser_version),
                "normalizer_version": normalizer_version,
                "worker": worker_replay_identity(current_job),
            }
            recorded_identities = {
                json.dumps(
                    result.get("facts", {}).get("replay_identity"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for result in decision.gate_results.values()
                if isinstance(result, dict)
            }
            if (
                recorded_admission_digests != {admission_digest}
                or recorded_identities
                != {
                    json.dumps(
                        replay_identity,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                }
            ):
                raise ValidationError("automatic publication admission digest mismatch")
            enabled_adapters = current_policy.get("enabled_adapter_keys") or ()
            mapped_identity = ADAPTER_SOURCE_IDENTITY.get(reference.adapter_key)
            provider_identity = (
                reference.metadata_json.get("provider_identity")
                if isinstance(reference.metadata_json, dict)
                else None
            )
            if (
                reference.adapter_key not in B_SCOPE_POLICY.enabled_adapter_keys
                or reference.adapter_key not in enabled_adapters
                or mapped_identity is None
                or reference.source_role != mapped_identity[0]
                or provider_identity != mapped_identity[1]
                or document.source_authority != mapped_identity[2]
                or candidate.authority_level != mapped_identity[2]
                or document.source_url != reference.canonical_url
                or not adapter_url_is_authorized(
                    reference.adapter_key, reference.canonical_url, final=False
                )
                or not adapter_url_is_authorized(
                    reference.adapter_key, artifact.final_url, final=True
                )
            ):
                raise ValidationError(
                    "automatic publication source authorization failed"
                )
            if (
                attempt.job_id != current_job.id
                or attempt.adapter_key != reference.adapter_key
                or attempt.operation != "fetch"
                or attempt.outcome != "succeeded"
                or attempt.finished_at is None
                or not isinstance(attempt.safe_metadata, dict)
                or attempt.safe_metadata.get("source_reference_id") != str(reference.id)
            ):
                raise ValidationError("automatic publication attempt lineage mismatch")
            if (
                current_contract is None
                or not source_contract_is_active(current_contract, at=checked_at)
                or not current_contract.allow_ai_processing
                or not current_contract.allow_display
                or mapped_identity is None
                or current_contract.source_type != mapped_identity[0]
                or current_contract.research_source_type != mapped_identity[0]
            ):
                raise ValidationError(
                    "automatic publication source contract is invalid"
                )
            if automatic_temporal_failures(
                reference,
                attempt,
                artifact,
                document,
                cutoff=cutoff,
                evaluation_at=checked_at,
            ):
                raise ValidationError(
                    "automatic publication exceeds the request cutoff"
                )
            if current_snapshot.get("thesis_id") != str(
                current_job.thesis_id
            ) or current_snapshot.get("case_id") != str(current_job.research_case_id):
                raise ValidationError("automatic publication request lineage mismatch")

        validate_authorization(job, checked_at=publication_at)

        def fence_write() -> None:
            lease_write_fence(
                self._session,
                job_id=decision.job_id,
                lease_token=lease_token,
                now=self._now(),
            )
            fenced_job = self._session.scalar(
                select(AcquisitionJob)
                .where(AcquisitionJob.id == decision.job_id)
                .execution_options(populate_existing=True)
            )
            if fenced_job is None:
                raise StaleLeaseError("acquisition lease is stale")
            validate_authorization(fenced_job, checked_at=self._now())

        fence_write()
        assert document.available_at is not None
        assert document.acquired_at is not None
        assert artifact.retrieved_at is not None
        available_at = self._as_utc(document.available_at)

        period = candidate.structured_fields.get("observed_period")
        try:
            observed_period = (
                date(int(period), 12, 31)
                if isinstance(period, str) and len(period) == 4 and period.isdigit()
                else date(
                    int(period[:4]),
                    int(period[5:7]),
                    monthrange(int(period[:4]), int(period[5:7]))[1],
                )
                if isinstance(period, str)
                and len(period) == 7
                and period[4] == "-"
                else date.fromisoformat(period)
                if period
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError("automatic publication period is invalid") from exc
        semantic_facts = semantic_audit
        scope = {
            "automatic_admission": {
                "decision_id": str(decision.id),
                "gate_version": decision.gate_version,
                "policy_version": decision.policy_version,
                "objective": objective,
                "semantic": semantic_facts,
            }
        }
        reason = (
            f"Automatically admitted by {decision.gate_version} "
            f"under {decision.policy_version}."
        )
        repository = ResearchRepository(self._session)

        def load_statement() -> SourceStatement | None:
            return self._session.scalar(
                select(SourceStatement).where(
                    SourceStatement.automatic_admission_decision_id == decision.id
                )
            )

        def validate_statement(value: SourceStatement) -> None:
            if (
                value.atomic_claim_candidate_id != candidate.id
                or value.automatic_admission_decision_id != decision.id
                or value.source_span_id != candidate.source_span_id
                or value.kind != candidate.claim_type
                or value.normalized_text != candidate.normalized_text.strip()
                or value.observed_period != observed_period
            ):
                raise ValidationError("automatic publication statement mismatch")

        def validate_link(
            value: EvidenceLink, value_statement: SourceStatement
        ) -> None:
            if (
                value.thesis_id != job.thesis_id
                or value.source_statement_id != value_statement.id
                or value.role != role
                or value.reason != reason
                or value.scope != scope
                or self._as_utc(value.available_at) != available_at
                or value.creator_type != "ai"
                or value.review_state != "automatically_admitted"
                or value.automatic_admission_decision_id != decision.id
            ):
                raise ValidationError("automatic publication evidence link mismatch")

        statement = load_statement()
        if statement is not None:
            validate_statement(statement)
        link = repository.get_automatic_evidence_link(decision.id)
        if link is not None:
            if statement is None:
                raise ValidationError("automatic publication statement is missing")
            validate_link(link, statement)
        if statement is not None and link is not None:
            return statement, link

        try:
            with self._session.begin_nested():
                if statement is None:
                    statement = SourceStatement(
                        source_span_id=candidate.source_span_id,
                        atomic_claim_candidate_id=candidate.id,
                        automatic_admission_decision_id=decision.id,
                        kind=candidate.claim_type,
                        normalized_text=candidate.normalized_text.strip(),
                        observed_period=observed_period,
                        created_at=publication_at,
                    )
                    self._session.add(statement)
                    fence_write()
                    self._session.flush()
                if link is None:
                    link = repository.link_evidence(
                        thesis_id=job.thesis_id,
                        source_statement_id=statement.id,
                        role=role,
                        reason=reason,
                        scope=scope,
                        available_at=available_at,
                        creator_type="ai",
                        review_state="automatically_admitted",
                        automatic_admission_decision_id=decision.id,
                        before_flush=fence_write,
                    )
        except IntegrityError:
            statement = load_statement()
            link = repository.get_automatic_evidence_link(decision.id)
            if statement is None or link is None:
                raise
        assert statement is not None and link is not None
        validate_statement(statement)
        validate_link(link, statement)
        return statement, link
