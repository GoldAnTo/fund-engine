from __future__ import annotations

import json
from pathlib import Path

from app.services.extraction_evaluation import score_gold_items


FIXTURE = Path(__file__).parent / "fixtures" / "extraction_gold_v1.json"


def _fixture_items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]


def test_gold_v1_accepts_the_exact_license_safe_candidate_set() -> None:
    gold = _fixture_items()
    candidates = [dict(item["expected_candidate"]) for item in gold]

    report = score_gold_items(gold, candidates)

    assert report.quote_exact_rate == 1.0
    assert report.atomic_precision == 1.0
    assert report.atomic_recall == 1.0
    assert report.duplicate_count == 0
    assert report.passes is True


def test_gold_v1_rejects_quote_drift_and_fact_forecast_confusion() -> None:
    gold = _fixture_items()
    candidates = [dict(item["expected_candidate"]) for item in gold]
    candidates[0]["quote"] = "订单同比增长 20%"  # adds a space absent from the frozen span
    candidates[0]["claim_type"] = "forecast"

    report = score_gold_items(gold, candidates)

    assert report.quote_exact_rate < 1.0
    assert report.claim_type_confusion["disclosed_fact->forecast"] == 1
    assert report.passes is False


def test_gold_v1_rejects_duplicate_and_non_primary_disclosed_fact() -> None:
    gold = _fixture_items()
    candidates = [dict(item["expected_candidate"]) for item in gold]
    duplicate = dict(candidates[0])
    duplicate["candidate_id"] = "duplicate-candidate"
    duplicate["authority_level"] = "secondary_source"
    candidates.append(duplicate)

    report = score_gold_items(gold, candidates)

    assert report.duplicate_count == 1
    assert report.primary_authority_violations == 1
    assert report.passes is False
