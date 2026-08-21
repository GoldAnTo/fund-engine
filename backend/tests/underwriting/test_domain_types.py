from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.underwriting.domain import (
    AnswerabilityState,
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    InvestmentMandateInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (
            ResearchObjectKind,
            {"industry", "company", "security"},
        ),
        (
            LedgerKind,
            {"reality", "belief", "decision", "calibration"},
        ),
        (
            AnswerabilityState,
            {"answerable", "partially_answerable", "not_answerable"},
        ),
        (
            EligibleAction,
            {
                "observe",
                "wait_for_validation",
                "eligible_for_probe_entry",
                "eligible_for_staged_entry",
                "do_not_enter",
            },
        ),
    ],
)
def test_domain_enums_have_exact_value_sets(enum_type, expected_values):
    assert {member.value for member in enum_type} == expected_values


def test_blocker_codes_have_exactly_seven_values():
    assert {member.value for member in BlockerCode} == {
        "missing_key_baseline",
        "unresolved_source_conflict",
        "mechanism_unidentified",
        "financial_model_not_closed",
        "expectation_surface_unidentifiable",
        "source_unavailable",
        "future_information_leakage",
    }
    assert len(BlockerCode) == 7


def test_investment_mandate_input_is_frozen():
    mandate = InvestmentMandateInput(
        mandate_key="long_term",
        horizon_years=5,
        base_currency="CNY",
        required_return=Decimal("0.08"),
        permanent_loss_limit=Decimal("0.2"),
        comparison_set=("CSI300", "SSE50"),
    )

    with pytest.raises(FrozenInstanceError):
        mandate.horizon_years = 10


def test_domain_input_dataclasses_preserve_declared_fields():
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    assert HistoricalBasisInput(cutoff, None, "manifest-hash").source_manifest_hash == (
        "manifest-hash"
    )
    entry = LedgerEntryInput(
        ledger_kind=LedgerKind.REALITY,
        family_key="revenue",
        entry_type="reported",
        payload={"value": 1},
        effective_at=cutoff,
        available_at=cutoff,
        source_boundary="public",
    )
    assert entry.payload == {"value": 1}
