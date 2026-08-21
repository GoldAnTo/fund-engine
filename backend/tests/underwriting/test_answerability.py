import pytest

from app.underwriting.domain import (
    AnswerabilityInput,
    AnswerabilityResult,
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    ENTRY_ACTIONS,
    enforce_action_boundary,
    evaluate_answerability,
)


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
