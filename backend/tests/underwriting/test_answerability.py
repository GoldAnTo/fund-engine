import pytest
from datetime import UTC, datetime
import uuid
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain import (
    AnswerabilityInput,
    AnswerabilityResult,
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    ENTRY_ACTIONS,
    HistoricalBasisInput,
    ResearchObjectKind,
    enforce_action_boundary,
    evaluate_answerability,
)
from app.underwriting.services import UnderwritingKernelService


NOW = datetime(2026, 8, 21, tzinfo=UTC)


@pytest.fixture
def kernel(session: Session) -> UnderwritingKernelService:
    return UnderwritingKernelService(session, now=lambda: NOW)


@pytest.fixture
def answerability_subject(kernel: UnderwritingKernelService):
    research_object = kernel.add_object(
        ResearchObjectKind.COMPANY, "company:answerability", "Answerability Company"
    )
    basis = kernel.add_basis(HistoricalBasisInput(NOW, NOW, "a" * 64))
    return research_object, basis


def test_no_blocker_or_research_debt_is_answerable():
    result = evaluate_answerability(
        AnswerabilityInput((), (), resolvable_within_mandate=True)
    )

    assert result == AnswerabilityResult(
        state=AnswerabilityState.ANSWERABLE,
        blockers=(),
        resolvable_within_mandate=True,
    )


def test_research_debt_without_hard_blocker_is_partially_answerable():
    result = evaluate_answerability(
        AnswerabilityInput((), ("baseline", "source_quality"), False)
    )

    assert result.state is AnswerabilityState.PARTIALLY_ANSWERABLE
    assert result.blockers == ()
    assert not result.resolvable_within_mandate


def test_hard_blocker_is_not_answerable_and_blockers_are_deduplicated_in_order():
    blockers = (
        BlockerCode.MISSING_KEY_BASELINE,
        BlockerCode.SOURCE_UNAVAILABLE,
        BlockerCode.MISSING_KEY_BASELINE,
    )
    result = evaluate_answerability(AnswerabilityInput(blockers, (), True))

    assert result.state is AnswerabilityState.NOT_ANSWERABLE
    assert result.blockers == (
        BlockerCode.MISSING_KEY_BASELINE,
        BlockerCode.SOURCE_UNAVAILABLE,
    )
    assert result.resolvable_within_mandate


@pytest.mark.parametrize(
    ("resolvable", "expected"),
    [
        (True, EligibleAction.WAIT_FOR_VALIDATION),
        (False, EligibleAction.DO_NOT_ENTER),
    ],
)
def test_not_answerable_maps_to_fail_closed_boundary(resolvable, expected):
    result = evaluate_answerability(
        AnswerabilityInput((BlockerCode.MECHANISM_UNIDENTIFIED,), (), resolvable)
    )

    assert enforce_action_boundary(result, EligibleAction.OBSERVE) is expected


@pytest.mark.parametrize("requested", tuple(ENTRY_ACTIONS))
def test_partial_answerability_blocks_entry_actions(requested):
    result = evaluate_answerability(AnswerabilityInput((), ("debt",), True))

    assert enforce_action_boundary(result, requested) is EligibleAction.WAIT_FOR_VALIDATION


@pytest.mark.parametrize(
    "requested",
    (
        EligibleAction.OBSERVE,
        EligibleAction.WAIT_FOR_VALIDATION,
        EligibleAction.DO_NOT_ENTER,
    ),
)
def test_partial_answerability_preserves_non_entry_actions(requested):
    result = evaluate_answerability(AnswerabilityInput((), ("debt",), True))

    assert enforce_action_boundary(result, requested) is requested


def test_answerable_preserves_requested_action_and_entry_actions_are_exact():
    assert ENTRY_ACTIONS == frozenset(
        {
            EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY,
            EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY,
        }
    )
    result = evaluate_answerability(AnswerabilityInput((), (), False))

    assert enforce_action_boundary(result, EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY) is (
        EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY
    )


@pytest.mark.parametrize("requested", tuple(EligibleAction))
def test_not_answerable_never_produces_entry_eligible_action(requested):
    result = evaluate_answerability(
        AnswerabilityInput((BlockerCode.FINANCIAL_MODEL_NOT_CLOSED,), (), False)
    )

    action = enforce_action_boundary(result, requested)
    assert action not in ENTRY_ACTIONS


def test_record_answerability_persists_resolvable_hard_gate(
    kernel: UnderwritingKernelService, answerability_subject
) -> None:
    research_object, basis = answerability_subject

    row = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(BlockerCode.MISSING_KEY_BASELINE,),
        research_debt_keys=("unit-economics",),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY,
        resolution_requirements=("reconcile baseline",),
        expected_parent_id=None,
    )

    assert row.state == AnswerabilityState.NOT_ANSWERABLE.value
    assert row.allowed_action == EligibleAction.WAIT_FOR_VALIDATION.value
    assert row.blockers == [BlockerCode.MISSING_KEY_BASELINE.value]
    assert row.research_debt_keys == ["unit-economics"]
    assert row.resolution_requirements == ["reconcile baseline"]


def test_record_answerability_explicit_created_at_respects_historical_cutoff(
    kernel: UnderwritingKernelService, answerability_subject,
) -> None:
    research_object, basis = answerability_subject

    row = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(),
        research_debt_keys=(),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=(),
        expected_parent_id=None,
        created_at=NOW,
    )

    assert row.created_at == NOW
    with pytest.raises(ValidationError, match="created_at must not exceed"):
        kernel.record_answerability(
            research_object.id,
            basis.id,
            hard_blockers=(),
            research_debt_keys=(),
            resolvable_within_mandate=True,
            requested_action=EligibleAction.OBSERVE,
            resolution_requirements=(),
            expected_parent_id=row.id,
            created_at=NOW.replace(year=NOW.year + 1),
        )


def test_record_answerability_persists_unresolvable_as_do_not_enter_without_price_claims(
    kernel: UnderwritingKernelService, answerability_subject
) -> None:
    research_object, basis = answerability_subject

    row = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(BlockerCode.SOURCE_UNAVAILABLE,),
        research_debt_keys=(),
        resolvable_within_mandate=False,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=("obtain source",),
        expected_parent_id=None,
    )

    assert row.state == AnswerabilityState.NOT_ANSWERABLE.value
    assert row.allowed_action == EligibleAction.DO_NOT_ENTER.value
    assert row.allowed_action not in {action.value for action in ENTRY_ACTIONS}
    assert not hasattr(row, "price_state")
    assert not hasattr(row, "bearish_claim")


def test_record_answerability_canonicalizes_duplicate_blockers(
    kernel: UnderwritingKernelService, answerability_subject
) -> None:
    research_object, basis = answerability_subject

    row = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(
            BlockerCode.MISSING_KEY_BASELINE,
            BlockerCode.SOURCE_UNAVAILABLE,
            BlockerCode.MISSING_KEY_BASELINE,
        ),
        research_debt_keys=(),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=("complete baseline",),
        expected_parent_id=None,
    )

    assert row.blockers == [
        BlockerCode.MISSING_KEY_BASELINE.value,
        BlockerCode.SOURCE_UNAVAILABLE.value,
    ]


def test_record_answerability_requires_resolution_requirements_for_hard_gate(
    kernel: UnderwritingKernelService, answerability_subject
) -> None:
    research_object, basis = answerability_subject

    with pytest.raises(ValidationError, match="resolution_requirements"):
        kernel.record_answerability(
            research_object.id,
            basis.id,
            hard_blockers=(BlockerCode.MISSING_KEY_BASELINE,),
            research_debt_keys=(),
            resolvable_within_mandate=True,
            requested_action=EligibleAction.OBSERVE,
            resolution_requirements=(),
            expected_parent_id=None,
        )


@pytest.mark.parametrize(
    ("requested_action", "expected_action"),
    [
        (EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY, EligibleAction.WAIT_FOR_VALIDATION),
        (EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY, EligibleAction.WAIT_FOR_VALIDATION),
        (EligibleAction.OBSERVE, EligibleAction.OBSERVE),
        (EligibleAction.DO_NOT_ENTER, EligibleAction.DO_NOT_ENTER),
    ],
)
def test_record_answerability_persists_partial_action_boundary(
    kernel: UnderwritingKernelService,
    answerability_subject,
    requested_action: EligibleAction,
    expected_action: EligibleAction,
) -> None:
    research_object, basis = answerability_subject

    row = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(),
        research_debt_keys=("debt",),
        resolvable_within_mandate=True,
        requested_action=requested_action,
        resolution_requirements=(),
        expected_parent_id=None,
    )

    assert row.state == AnswerabilityState.PARTIALLY_ANSWERABLE.value
    assert row.allowed_action == expected_action.value


def test_record_answerability_versions_and_rejects_stale_parent(
    kernel: UnderwritingKernelService, answerability_subject
) -> None:
    research_object, basis = answerability_subject
    first = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(),
        research_debt_keys=(),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=(),
        expected_parent_id=None,
    )
    second = kernel.record_answerability(
        research_object.id,
        basis.id,
        hard_blockers=(),
        research_debt_keys=("refresh",),
        resolvable_within_mandate=True,
        requested_action=EligibleAction.OBSERVE,
        resolution_requirements=(),
        expected_parent_id=first.id,
    )

    assert (first.version, second.version, second.supersedes_id) == (1, 2, first.id)
    with pytest.raises(ConflictError, match="expected parent"):
        kernel.record_answerability(
            research_object.id,
            basis.id,
            hard_blockers=(),
            research_debt_keys=(),
            resolvable_within_mandate=True,
            requested_action=EligibleAction.OBSERVE,
            resolution_requirements=(),
            expected_parent_id=first.id,
        )


@pytest.mark.parametrize("missing", ("object", "basis"))
def test_record_answerability_rejects_unknown_object_or_basis(
    kernel: UnderwritingKernelService, answerability_subject, missing: str
) -> None:
    research_object, basis = answerability_subject
    object_id = uuid.uuid4() if missing == "object" else research_object.id
    basis_id = uuid.uuid4() if missing == "basis" else basis.id
    expected_message = (
        "research object not found" if missing == "object" else "historical basis not found"
    )

    with pytest.raises(ValidationError, match=f"^{expected_message}$"):
        kernel.record_answerability(
            object_id,
            basis_id,
            hard_blockers=(),
            research_debt_keys=(),
            resolvable_within_mandate=True,
            requested_action=EligibleAction.OBSERVE,
            resolution_requirements=(),
            expected_parent_id=None,
        )
