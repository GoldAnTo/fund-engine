"""Authenticated single-item decisions for Company Research critical inputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.company_research import (
    DEFAULT_STRATEGY_VERSION,
    CompanyResearchMemoArtifact,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CriticalInputCandidate,
    CriticalInputDecision,
    CriticalInputKind,
    CriticalInputSet,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelBuilder,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchPublicationService,
)
from app.underwriting.services.company_research_run import (
    CompanyResearchRun,
    CompanyResearchRunService,
)


class CompanyResearchCriticalInputConfirmationService:
    """Append one optimistic, immutable critical-input decision."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)

    def _utcnow(self) -> datetime:
        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    def decide(
        self,
        *,
        project_id: UUID,
        critical_input_key: str,
        expected_artifact_id: UUID,
        expected_input_fingerprint: str,
        decision: CriticalInputDecision,
        replacement_value: str | None = None,
        replacement_unit: str | None = None,
        replacement_rationale: str | None = None,
    ) -> CompanyResearchRun:
        if type(project_id) is not UUID or type(expected_artifact_id) is not UUID:
            raise ValidationError("critical input decision identity is invalid")
        if not isinstance(critical_input_key, str) or not critical_input_key:
            raise ValidationError("critical input key is invalid")
        if (
            not isinstance(expected_input_fingerprint, str)
            or len(expected_input_fingerprint) != 64
        ):
            raise ValidationError("critical input fingerprint is invalid")
        if type(decision) is not CriticalInputDecision or decision is (
            CriticalInputDecision.PENDING
        ):
            raise ValidationError("critical input decision is invalid")
        replacement_fields = (
            replacement_value,
            replacement_unit,
            replacement_rationale,
        )
        replacing = decision is (
            CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION
        )
        if replacing:
            if any(
                not isinstance(value, str) or not value.strip()
                for value in replacement_fields
            ):
                raise ValidationError(
                    "critical input replacement requires value, unit, and rationale"
                )
        elif any(value is not None for value in replacement_fields):
            raise ValidationError(
                "critical input replacement fields require a replacement decision"
            )

        when = self._utcnow()
        automatic_confirmation: tuple[int, UUID, str, str] | None = None
        with self._session.begin_nested():
            state = self._repository.lock_publication_state(project_id)
            if state.preparation.strategy_version != DEFAULT_STRATEGY_VERSION:
                raise ValidationError(
                    "critical input decisions require the mainline strategy"
                )
            if (
                state.preparation.status != "awaiting_judgment_review"
                or state.preparation.current_step != "judgment_context"
                or state.preparation.progress != 85
                or state.job.status != "waiting_for_review"
                or state.job.step != "judgment_context"
                or state.job.progress != 85
            ):
                raise ConflictError("critical input decision is frozen or stale")
            head = state.artifact_heads.get("critical_inputs")
            if head is None:
                raise ValidationError("mainline critical inputs are missing")
            if head.id != expected_artifact_id:
                raise ConflictError("critical input decision artifact is stale")
            decoded = CompanyResearchArtifactCodec.decode(
                "critical_inputs", head.payload
            )
            if type(decoded) is not CriticalInputSet:
                raise ValidationError("critical_inputs payload is invalid")
            selected = next(
                (item for item in decoded.inputs if item.key == critical_input_key),
                None,
            )
            if selected is None:
                if replacing and critical_input_key.startswith("calculation:"):
                    raise ValidationError(
                        "derived critical input cannot be directly replaced"
                    )
                raise ValidationError("critical input key is invalid")
            if selected.input_fingerprint != expected_input_fingerprint:
                raise ConflictError("critical input fingerprint is stale")
            if selected.decision is not CriticalInputDecision.PENDING:
                raise ConflictError("critical input decision is contradictory")
            if decision is CriticalInputDecision.ACCEPTED_GAP:
                memo_row = state.artifact_heads.get("memo")
                gaps_row = state.artifact_heads.get("research_gaps")
                if memo_row is None or gaps_row is None:
                    raise ValidationError(
                        "accepted gap requires a specific governed gap"
                    )
                memo_value = CompanyResearchArtifactCodec.decode(
                    "memo",
                    {
                        key: value
                        for key, value in memo_row.payload.items()
                        if key != "_lineage"
                    },
                )
                raw_gaps = gaps_row.payload.get("gaps")
                gap_keys = (
                    {
                        str(gap.get("code") or gap.get("gap_key"))
                        for gap in raw_gaps
                        if isinstance(gap, dict)
                        and (gap.get("code") or gap.get("gap_key"))
                    }
                    if isinstance(raw_gaps, list)
                    else set()
                )
                if type(memo_value) is CompanyResearchMemoArtifact:
                    gap_keys.update(gap.code for gap in memo_value.research_gaps)
                if (
                    selected.kind is not CriticalInputKind.UNKNOWN
                    or selected.gap_key is None
                    or type(memo_value) is not CompanyResearchMemoArtifact
                    or memo_value.assessment_status != "not_answerable"
                    or selected.gap_key not in memo_value.gap_keys
                    or selected.gap_key not in gap_keys
                ):
                    raise ValidationError(
                        "accepted gap requires a specific governed gap"
                    )
            if decision is CriticalInputDecision.MARKED_UNKNOWN and selected.kind in {
                CriticalInputKind.UNKNOWN,
                CriticalInputKind.DERIVED_CALCULATION,
            }:
                raise ValidationError(
                    "marked unknown requires a concrete critical input"
                )

            replacement = None
            if replacing:
                if selected.kind is CriticalInputKind.DERIVED_CALCULATION:
                    raise ValidationError(
                        "derived critical input cannot be directly replaced"
                    )
                assert replacement_value is not None
                assert replacement_unit is not None
                assert replacement_rationale is not None
                typed_value: Decimal | str
                if type(selected.value) is Decimal:
                    try:
                        typed_value = Decimal(replacement_value)
                    except InvalidOperation as exc:
                        raise ValidationError(
                            "critical input replacement value is invalid"
                        ) from exc
                    if not typed_value.is_finite():
                        raise ValidationError(
                            "critical input replacement value is invalid"
                        )
                else:
                    typed_value = replacement_value.strip()
                replacement = CriticalInputCandidate(
                    key=selected.key,
                    kind=CriticalInputKind.USER_ASSUMPTION,
                    value=typed_value,
                    period=selected.period,
                    unit=replacement_unit.strip(),
                    currency=selected.currency,
                    rationale=replacement_rationale.strip(),
                    assumption_key=(
                        (
                            selected.assumption_key.split(":", 1)[0]
                            if selected.assumption_key is not None
                            else "company-research-mainline.v1"
                        )
                        + ":user_assumption_"
                        f"input_{selected.input_fingerprint[:16]}"
                    ),
                )
                CompanyResearchModelBuilder.validate_critical_input_replacement(
                    selected, replacement
                )
                rights_ids = tuple(state.draft_content.security_rights_ids)
                rights = ProductRepository(self._session).security_rights_many(
                    rights_ids
                )
                if len(rights) != len(rights_ids):
                    raise ValidationError(
                        "critical input replacement market boundary is incomplete"
                    )
                CompanyResearchModelBuilder.validate_critical_input_replacement_set(
                    decoded,
                    selected,
                    replacement,
                    minimum_listed_units=sum(
                        (item.economic_units for item in rights),
                        start=Decimal(0),
                    ),
                )

            successor_set = CriticalInputSet(
                inputs=tuple(
                    replace(item, decision=decision, replacement=replacement)
                    if item.key == critical_input_key
                    else item
                    for item in decoded.inputs
                )
            )
            successor = self._repository.append_critical_input_successor(
                project_id=project_id,
                payload=CompanyResearchArtifactCodec.encode(
                    "critical_inputs", successor_set
                ),
                expected_parent_id=head.id,
                created_at=when,
            )
            self._repository.append_event(
                preparation_id=state.preparation.id,
                event_type="critical_input_decided",
                payload={
                    "critical_inputs_artifact_id": str(successor.id),
                    "critical_inputs_content_hash": successor.content_hash,
                    "critical_input_key": selected.key,
                    "input_fingerprint": selected.input_fingerprint,
                    "decision": decision.value,
                },
                created_at=when,
            )
            rebuild_required = replacing or decision is (
                CriticalInputDecision.MARKED_UNKNOWN
            )
            if rebuild_required:
                self._repository.queue_critical_input_model_rebuild(
                    state=state,
                    critical_inputs=successor,
                    updated_at=when,
                )
                self._repository.append_event(
                    preparation_id=state.preparation.id,
                    event_type="model_rebuild_queued",
                    payload={
                        "critical_inputs_artifact_id": str(successor.id),
                        "critical_inputs_content_hash": successor.content_hash,
                    },
                    created_at=when,
                )
            elif all(
                item.decision is not CriticalInputDecision.PENDING
                for item in successor_set.inputs
            ):
                memo = state.artifact_heads.get("memo")
                if memo is None:
                    raise ValidationError(
                        "terminal critical inputs require a current memo"
                    )
                decoded_memo = CompanyResearchArtifactCodec.decode(
                    "memo",
                    {
                        key: value
                        for key, value in memo.payload.items()
                        if key != "_lineage"
                    },
                )
                if (
                    type(decoded_memo) is not CompanyResearchMemoArtifact
                    or decoded_memo.candidate_status != "machine_draft"
                ):
                    raise ValidationError(
                        "terminal critical inputs require a current machine memo"
                    )
                non_confirmed = tuple(
                    item
                    for item in successor_set.inputs
                    if item.decision is not CriticalInputDecision.CONFIRMED
                )
                accepted_gap_keys = {
                    item.gap_key
                    for item in non_confirmed
                    if item.decision is CriticalInputDecision.ACCEPTED_GAP
                    and item.gap_key is not None
                }
                if non_confirmed and (
                    decoded_memo.assessment_status != "not_answerable"
                    or not decoded_memo.gap_keys
                    or not accepted_gap_keys.issubset(set(decoded_memo.gap_keys))
                ):
                    raise ValidationError(
                        "critical input decisions conflict with memo answerability"
                    )
                automatic_confirmation = (
                    state.draft.lock_version,
                    memo.id,
                    memo.content_hash,
                    self._authenticated_narrative_markdown(decoded_memo),
                )

        if automatic_confirmation is not None:
            lock_version, memo_id, memo_hash, markdown = automatic_confirmation
            CompanyResearchPublicationService(
                self._session, now=lambda: when
            ).confirm_judgment(
                project_id=project_id,
                expected_lock_version=lock_version,
                expected_memo_id=memo_id,
                expected_memo_content_hash=memo_hash,
                markdown=markdown,
            )

        return CompanyResearchRunService(self._session, now=self._now).read(project_id)

    @staticmethod
    def _authenticated_narrative_markdown(
        memo: CompanyResearchMemoArtifact,
    ) -> str:
        """Render the authenticated narrative without inventing new research text."""

        narrative = memo.narrative
        if narrative is None:
            if memo.markdown is None:
                raise ValidationError(
                    "terminal critical inputs require an authenticated narrative"
                )
            return memo.markdown

        def claim(text: str, citations: tuple[str, ...]) -> str:
            return f"{text}\n\n_Citations: {', '.join(citations)}_"

        def claim_list(values: tuple[object, ...]) -> str:
            if not values:
                return "- None recorded."
            return "\n".join(
                f"- {value.text} _(Citations: {', '.join(value.citations)})_"
                for value in values
            )

        drivers = "\n".join(
            (
                f"- **{driver.driver_key}**: {driver.text} "
                f"_(Citations: {', '.join(driver.citations)})_"
            )
            for driver in narrative.driver_explanations
        )
        return "\n\n".join(
            (
                "# Company Research Memo",
                "## Assessment\n\n"
                + claim(narrative.summary.text, narrative.summary.citations),
                "## Business Explanation\n\n"
                + claim(
                    narrative.business_explanation.text,
                    narrative.business_explanation.citations,
                ),
                "## Key Drivers\n\n" + drivers,
                "## Counterevidence\n\n"
                + claim_list(narrative.counterevidence),
                "## Research Gaps\n\n" + claim_list(narrative.gaps),
                "## Next Verification\n\n" + claim_list(narrative.next_checks),
            )
        )
