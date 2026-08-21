"""Domain layer for the underwriting bounded context."""

from .answerability import (
    ENTRY_ACTIONS,
    AnswerabilityInput,
    AnswerabilityResult,
    enforce_action_boundary,
    evaluate_answerability,
)

from .types import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from .metrics import (
    AggregationRule,
    MetricDefinition,
    MetricObservation,
    PeriodSemantics,
    ReconciliationResult,
    SourceRole,
    reconcile,
)
from .mechanisms import (
    Falsifier,
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from .industry import (
    AnswerabilityBlocked,
    IndustryInputs,
    IndustryRange,
    IndustryScenario,
    IndustryState,
    ScenarioDriverOverride,
    ScenarioKind,
    ScenarioSpec,
)

__all__ = [
    "ENTRY_ACTIONS",
    "AnswerabilityState",
    "AnswerabilityInput",
    "AnswerabilityResult",
    "BlockerCode",
    "EligibleAction",
    "HistoricalBasisInput",
    "InvestmentMandateInput",
    "LedgerEntryInput",
    "LedgerKind",
    "ResearchObjectKind",
    "enforce_action_boundary",
    "evaluate_answerability",
    "AggregationRule",
    "MetricDefinition",
    "MetricObservation",
    "PeriodSemantics",
    "ReconciliationResult",
    "SourceRole",
    "reconcile",
    "Falsifier",
    "FinancialMapping",
    "MechanismPack",
    "MechanismStatus",
    "IndustryInputs",
    "IndustryRange",
    "IndustryScenario",
    "IndustryState",
    "ScenarioDriverOverride",
    "ScenarioKind",
    "ScenarioSpec",
    "AnswerabilityBlocked",
]
