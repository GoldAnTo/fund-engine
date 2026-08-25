"""Fail-closed answerability policy for underwriting decisions."""

from dataclasses import dataclass

from .types import AnswerabilityState, BlockerCode, EligibleAction


@dataclass(frozen=True, slots=True)
class AnswerabilityInput:
    hard_blockers: tuple[BlockerCode, ...]
    research_debt_keys: tuple[str, ...]
    resolvable_within_mandate: bool


@dataclass(frozen=True, slots=True)
class AnswerabilityResult:
    state: AnswerabilityState
    blockers: tuple[BlockerCode, ...]
    resolvable_within_mandate: bool


ENTRY_ACTIONS = frozenset(
    {
        EligibleAction.ELIGIBLE_FOR_PROBE_ENTRY,
        EligibleAction.ELIGIBLE_FOR_STAGED_ENTRY,
    }
)


def evaluate_answerability(value: AnswerabilityInput) -> AnswerabilityResult:
    """Classify answerability, preserving unique hard blockers in input order."""
    blockers = tuple(dict.fromkeys(value.hard_blockers))
    if blockers:
        state = AnswerabilityState.NOT_ANSWERABLE
    elif value.research_debt_keys:
        state = AnswerabilityState.PARTIALLY_ANSWERABLE
    else:
        state = AnswerabilityState.ANSWERABLE

    return AnswerabilityResult(
        state=state,
        blockers=blockers,
        resolvable_within_mandate=value.resolvable_within_mandate,
    )


def enforce_action_boundary(
    result: AnswerabilityResult, requested: EligibleAction
) -> EligibleAction:
    """Prevent entry actions until answerability has cleared its gate."""
    if result.state is AnswerabilityState.NOT_ANSWERABLE:
        return (
            EligibleAction.WAIT_FOR_VALIDATION
            if result.resolvable_within_mandate
            else EligibleAction.DO_NOT_ENTER
        )
    if (
        result.state is AnswerabilityState.PARTIALLY_ANSWERABLE
        and requested in ENTRY_ACTIONS
    ):
        return EligibleAction.WAIT_FOR_VALIDATION
    return requested
