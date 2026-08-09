"""Deterministic, license-safe release scoring for atomic-claim extraction."""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_FIELD_NAMES = ("numeric_value", "unit", "observed_period", "subject")


@dataclass(frozen=True, slots=True)
class GoldScoreReport:
    quote_exact_rate: float
    offset_exact_rate: float
    quote_hash_exact_rate: float
    atomic_precision: float
    atomic_recall: float
    atomic_f1: float
    high_impact_recall: float
    disclosed_fact_precision: float
    duplicate_count: int
    primary_authority_violations: int
    claim_type_confusion: dict[str, int]
    field_accuracy: dict[str, float]
    review_approval_rate: float
    passes: bool


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _candidate_key(item: Mapping[str, Any]) -> str:
    return "|".join((
        str(item.get("quote_sha256") or hashlib.sha256(str(item.get("quote", "")).encode("utf-8")).hexdigest()),
        str(item.get("claim_type", "")),
        str(item.get("normalized_text", "")),
    ))


def _is_exact(gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    quote = str(candidate.get("quote", ""))
    expected_hash = hashlib.sha256(str(gold.get("quote", "")).encode("utf-8")).hexdigest()
    return (
        quote == gold.get("quote")
        and candidate.get("quote_start") == gold.get("quote_start")
        and candidate.get("quote_end") == gold.get("quote_end")
        and candidate.get("quote_sha256") == expected_hash
        and candidate.get("claim_type") == gold.get("claim_type")
        and candidate.get("normalized_text") == gold.get("normalized_text")
    )


def score_gold_items(
    gold_items: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> GoldScoreReport:
    """Score a frozen labeled corpus without loading any provider material.

    Inputs are plain mappings so the same scorer can replay private licensed
    corpora outside the repository. Candidate ``gold_id`` links a prediction
    to an adjudicated item; an unknown or missing link counts as a false
    positive. The scorer never normalizes quotes before checking offsets or
    hashes, deliberately making even whitespace drift a release failure.
    """
    gold_by_id = {str(item["id"]): item for item in gold_items}
    linked: list[tuple[Mapping[str, Any] | None, Mapping[str, Any]]] = [
        (gold_by_id.get(str(candidate.get("gold_id", candidate.get("id", "")))), candidate)
        for candidate in candidates
    ]
    exact_pairs = [(gold, candidate) for gold, candidate in linked if gold is not None and _is_exact(gold, candidate)]
    matched_gold_ids = {str(gold["id"]) for gold, _ in exact_pairs}
    aligned = [(gold, candidate) for gold, candidate in linked if gold is not None]
    quote_exact = sum(candidate.get("quote") == gold.get("quote") for gold, candidate in aligned)
    offset_exact = sum(candidate.get("quote_start") == gold.get("quote_start") and candidate.get("quote_end") == gold.get("quote_end") for gold, candidate in aligned)
    hash_exact = sum(candidate.get("quote_sha256") == hashlib.sha256(str(gold.get("quote", "")).encode("utf-8")).hexdigest() for gold, candidate in aligned)
    duplicate_count = sum(count - 1 for count in Counter(_candidate_key(candidate) for candidate in candidates).values() if count > 1)
    confusion = Counter(
        f"{gold.get('claim_type')}->{candidate.get('claim_type')}"
        for gold, candidate in aligned
        if gold.get("claim_type") != candidate.get("claim_type")
    )
    primary_authority_violations = sum(
        candidate.get("claim_type") == "disclosed_fact" and candidate.get("authority_level") != "primary_disclosure"
        for candidate in candidates
    )
    field_accuracy: dict[str, float] = {}
    for field in _FIELD_NAMES:
        comparable = [(gold, candidate) for gold, candidate in aligned if field in gold]
        field_accuracy[field] = _rate(sum(candidate.get(field) == gold.get(field) for gold, candidate in comparable), len(comparable))
    approved = [item for item in gold_items if item.get("review_approved")]
    review_approval_rate = _rate(
        sum(
            any(candidate.get("review_outcome") in {"confirmed", "modified"} for matched, candidate in linked if matched is gold)
            for gold in approved
        ),
        len(approved),
    )
    high_impact = [item for item in gold_items if item.get("high_impact")]
    high_impact_recall = _rate(sum(str(item["id"]) in matched_gold_ids for item in high_impact), len(high_impact))
    disclosed_predictions = [(gold, candidate) for gold, candidate in linked if candidate.get("claim_type") == "disclosed_fact"]
    disclosed_fact_precision = _rate(sum(gold is not None and _is_exact(gold, candidate) and gold.get("claim_type") == "disclosed_fact" for gold, candidate in disclosed_predictions), len(disclosed_predictions))
    atomic_precision = _rate(len(exact_pairs), len(candidates))
    atomic_recall = _rate(len(matched_gold_ids), len(gold_items))
    atomic_f1 = _rate(2 * atomic_precision * atomic_recall, atomic_precision + atomic_recall) if atomic_precision + atomic_recall else 0.0
    passes = (
        _rate(quote_exact, len(aligned)) == 1.0
        and _rate(offset_exact, len(aligned)) == 1.0
        and _rate(hash_exact, len(aligned)) == 1.0
        and duplicate_count == 0
        and disclosed_fact_precision >= 0.98
        and atomic_precision >= 0.95
        and high_impact_recall >= 0.90
        and primary_authority_violations == 0
    )
    return GoldScoreReport(
        quote_exact_rate=_rate(quote_exact, len(aligned)),
        offset_exact_rate=_rate(offset_exact, len(aligned)),
        quote_hash_exact_rate=_rate(hash_exact, len(aligned)),
        atomic_precision=atomic_precision,
        atomic_recall=atomic_recall,
        atomic_f1=atomic_f1,
        high_impact_recall=high_impact_recall,
        disclosed_fact_precision=disclosed_fact_precision,
        duplicate_count=duplicate_count,
        primary_authority_violations=primary_authority_violations,
        claim_type_confusion=dict(confusion),
        field_accuracy=field_accuracy,
        review_approval_rate=review_approval_rate,
        passes=passes,
    )
