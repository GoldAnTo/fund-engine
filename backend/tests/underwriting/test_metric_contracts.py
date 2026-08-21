from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain import (
    AggregationRule,
    MetricDefinition,
    MetricObservation,
    PeriodSemantics,
    ReconciliationResult,
    SourceRole,
    reconcile,
)


UTC_NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def definition(**overrides):
    values = {
        "key": "revenue",
        "version": 1,
        "label": "Revenue",
        "unit": "CNY",
        "period_semantics": PeriodSemantics.FLOW,
        "source_role": SourceRole.REPORTED,
        "aggregation": AggregationRule.SUM,
        "reconciliation_tolerance": Decimal("1000"),
    }
    values.update(overrides)
    return MetricDefinition(**values)


def test_metric_definition_preserves_metric_vocabulary():
    metric = definition()
    assert metric.unit == "CNY"
    assert metric.period_semantics is PeriodSemantics.FLOW
    assert metric.source_role is SourceRole.REPORTED
    assert metric.aggregation is AggregationRule.SUM


def test_observation_normalizes_aware_times_to_utc():
    metric = definition()
    observed = MetricObservation.create(
        metric,
        Decimal("12.5"),
        UTC_NOW.replace(tzinfo=UTC),
        UTC_NOW + timedelta(days=1),
        UTC_NOW,
        UTC_NOW,
        UTC_NOW,
        "source-1",
        "https://example.test/source",
        "CNY",
        {"segment": "total", "region": "cn"},
    )
    assert observed.observed_start.tzinfo is UTC
    assert observed.dimensions == (("region", "cn"), ("segment", "total"))


def test_observation_rejects_future_availability():
    with pytest.raises(ValidationError, match="available_at must not exceed cutoff"):
        MetricObservation.create(
            definition(), Decimal("1"), UTC_NOW, UTC_NOW, UTC_NOW, UTC_NOW + timedelta(seconds=1),
            UTC_NOW, "source", "locator", "CNY", (),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"unit": "USD", "match": "unit"},
        {"observed_start": datetime(2026, 1, 2, tzinfo=UTC), "match": "observed"},
        {"observed_start": datetime(2026, 1, 1), "match": "aware"},
    ],
)
def test_observation_rejects_invalid_contract_fields(kwargs):
    values = dict(
        definition=definition(), value=Decimal("1"), observed_start=UTC_NOW,
        observed_end=UTC_NOW, effective_at=UTC_NOW, available_at=UTC_NOW,
        cutoff=UTC_NOW, source_id="source", source_locator="locator", unit="CNY", dimensions=(),
    )
    values.update({key: value for key, value in kwargs.items() if key != "match"})
    with pytest.raises(ValidationError, match=kwargs["match"]):
        MetricObservation.create(**values)


def test_observation_canonicalizes_dimensions_and_rejects_duplicates():
    with pytest.raises(ValidationError):
        MetricObservation.create(
            definition(), Decimal("1"), UTC_NOW, UTC_NOW, UTC_NOW, UTC_NOW, UTC_NOW,
            "source", "locator", "CNY", (("a", "1"), ("a", "2")),
        )


def test_reconcile_reports_delta_and_balanced_status():
    result = reconcile(
        Decimal("362012554000"),
        (
            Decimal("253041337000"),
            Decimal("57290460000"),
            Decimal("28699935000"),
            Decimal("5493003000"),
            Decimal("17487818000"),
        ),
        Decimal("1000"),
    )
    assert result == ReconciliationResult(
        Decimal("362012554000"), Decimal("362012553000"), Decimal("1000"), Decimal("1000"), True
    )
    assert not reconcile(Decimal("362012554000"), (Decimal("1"),), Decimal("1000")).balanced


def test_reconcile_preserves_negative_delta_when_parts_exceed_total():
    result = reconcile(Decimal("100"), (Decimal("101"),), Decimal("1"))
    assert result.delta == Decimal("-1")
    assert result.balanced
    assert not reconcile(Decimal("100"), (Decimal("102"),), Decimal("1")).balanced
