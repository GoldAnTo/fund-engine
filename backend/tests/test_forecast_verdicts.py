"""Forecast verdict records must remain separate from ordinary claim review."""
from __future__ import annotations


def test_forecast_verdict_records_are_immutable_ledger_entries() -> None:
    from app.models.ledger import Base, IMMUTABLE_TABLES
    from app.models.research_expression import (
        ActualMetricObservation,
        ForecastEvaluationCandidate,
        ForecastTargetVersion,
        ForecastVerdict,
    )

    records = (
        ForecastTargetVersion,
        ActualMetricObservation,
        ForecastEvaluationCandidate,
        ForecastVerdict,
    )
    assert {record.__tablename__ for record in records} <= set(Base.metadata.tables)
    assert {record.__tablename__ for record in records} <= IMMUTABLE_TABLES
