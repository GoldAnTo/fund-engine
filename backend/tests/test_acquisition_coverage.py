from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.acquisition_coverage import (
    COVERAGE_POLICY,
    CoverageEvidence,
    CoverageGoal,
    CoverageSearchOperation,
    build_boundary_decision_context,
    decide_coverage,
)


NOW = datetime(2026, 8, 15, tzinfo=UTC)


def _goal(
    *,
    objective: str = "support",
    goal_id: str = "goal:support",
    round_status: str = "succeeded",
    search_outcomes: tuple[str, ...] = ("succeeded",),
    metric_periods: tuple[str, ...] = ("2026-Q2",),
    metric_units: tuple[str, ...] = ("亿元",),
    require_explicit_metric_semantics: bool = True,
) -> CoverageGoal:
    job_id = uuid.uuid4()
    return CoverageGoal(
        goal_id=goal_id,
        thesis_id=uuid.uuid4(),
        objective=objective,
        job_id=job_id,
        job_status=round_status,
        cutoff=NOW,
        entity_names=("示例公司",),
        security_codes=("600001",),
        metric_terms=("营业收入",),
        metric_periods=metric_periods,
        metric_units=metric_units,
        period_start="2026-01-01",
        period_end="2026-12-31",
        search_operations=tuple(
            CoverageSearchOperation(
                job_id=job_id,
                query_index=index,
                adapter_key=f"source-{index}",
                query=f"query-{index}",
                outcome=outcome,
                error_codes=("search_failed",) if outcome == "failed" else (),
            )
            for index, outcome in enumerate(search_outcomes)
        ),
        require_explicit_metric_semantics=require_explicit_metric_semantics,
    )


def _evidence(
    *,
    goal: CoverageGoal,
    authority: str = "primary_disclosure",
    source_identity: str = "sse:issuer-a",
    publication_key: str = "publication-a",
    canonical_url: str = "https://www.sse.com.cn/a.pdf",
    content_sha256: str = "a" * 64,
    available_at: datetime = NOW,
    subject: str = "示例公司",
    security_code: str = "600001",
    metric: str = "营业收入",
    observed_period: str = "2026-Q2",
    unit: str = "亿元",
    review_state: str = "automatically_admitted",
    role: str | None = None,
) -> CoverageEvidence:
    resolved_role = role or {
        "support": "supports",
        "contradict": "contradicts",
        "alternative_explanation": "contextualizes",
    }[goal.objective]
    return CoverageEvidence(
        evidence_link_id=uuid.uuid4(),
        automatic_admission_decision_id=uuid.uuid4(),
        source_statement_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        job_id=goal.job_id,
        goal_id=goal.goal_id,
        review_state=review_state,
        role=resolved_role,
        authority=authority,
        source_identity=source_identity,
        publication_key=publication_key,
        canonical_url=canonical_url,
        content_sha256=content_sha256,
        available_at=available_at,
        subject=subject,
        security_code=security_code,
        metric=metric,
        observed_period=observed_period,
        unit=unit,
    )


def test_event_level_alternative_contextual_evidence_qualifies_without_metric_semantics(
) -> None:
    goal = _goal(
        objective="alternative_explanation",
        goal_id="goal:alternative",
        metric_periods=(),
        metric_units=(),
    )
    evidence = _evidence(
        goal=goal,
        metric=None,
        observed_period=None,
        unit=None,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (evidence,))

    assert result.status == "ready"
    assert result.search_completed is True
    assert result.qualifying_evidence_ids == (evidence.evidence_link_id,)
    assert result.observed_authority_count == 1
    assert result.observed_independent_source_count == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"available_at": NOW + timedelta(seconds=1)},
        {"subject": "无关公司"},
        {"review_state": "reviewed"},
        {"role": "supports"},
        {"source_identity": ""},
    ],
)
def test_event_level_alternative_rejects_invalid_contextual_evidence_without_inventing_observation(
    overrides: dict,
) -> None:
    goal = _goal(
        objective="alternative_explanation",
        goal_id="goal:alternative",
        metric_periods=(),
        metric_units=(),
    )
    evidence = _evidence(
        goal=goal,
        metric=None,
        observed_period=None,
        unit=None,
        **overrides,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (evidence,))

    assert result.status == "ready"
    assert result.search_completed is True
    assert result.qualifying_evidence_ids == ()
    assert result.observed_authority_count == 0
    assert result.observed_independent_source_count == 0


def test_event_level_alternative_can_complete_with_zero_observed_evidence() -> None:
    goal = _goal(
        objective="alternative_explanation",
        goal_id="goal:alternative",
        metric_periods=(),
        metric_units=(),
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, ())

    assert result.status == "ready"
    assert result.search_completed is True
    assert result.qualifying_evidence_ids == ()
    assert result.observed_authority_count == 0
    assert result.observed_independent_source_count == 0


def test_support_goal_requires_authority_and_independent_source() -> None:
    goal = _goal()
    weak = _evidence(goal=goal, authority="licensed_research")

    unmet = COVERAGE_POLICY.evaluate_goal(goal, (weak,))
    ready = COVERAGE_POLICY.evaluate_goal(goal, (_evidence(goal=goal),))

    assert unmet.status == "unmet"
    assert "authority_requirement_unmet" in unmet.blocking_reasons
    assert ready.status == "ready"
    assert ready.required_authority_levels == ("primary_disclosure",)
    assert ready.observed_authority_levels == ("primary_disclosure",)
    assert ready.observed_authority_count == 1
    assert ready.observed_independent_source_count == 1


def test_duplicate_publication_and_canonical_document_count_once() -> None:
    goal = _goal()
    first = _evidence(goal=goal)
    duplicate = _evidence(
        goal=goal,
        source_identity="sse:issuer-b",
        publication_key=first.publication_key,
        canonical_url=first.canonical_url,
        content_sha256=first.content_sha256,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (first, duplicate))

    assert result.observed_authority_count == 1
    assert result.observed_independent_source_count == 1
    assert len(result.qualifying_evidence_ids) == 1


def test_conflicting_publication_variants_are_not_merged_or_counted() -> None:
    goal = _goal()
    first = _evidence(goal=goal)
    conflict = _evidence(
        goal=goal,
        publication_key=first.publication_key,
        content_sha256="b" * 64,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (first, conflict))

    assert result.status == "unmet"
    assert "conflicting_document_variants" in result.blocking_reasons
    assert result.qualifying_evidence_ids == ()
    assert result.conflict_details


def test_conflicting_canonical_url_variants_are_not_silently_deduplicated() -> None:
    goal = _goal()
    first = _evidence(goal=goal)
    conflict = _evidence(
        goal=goal,
        publication_key="different-publication-key",
        canonical_url=first.canonical_url,
        content_sha256="d" * 64,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (first, conflict))

    assert result.status == "unmet"
    assert result.observed_independent_source_count == 0
    assert result.conflict_details == (
        {"canonical_url": first.canonical_url, "variant_count": 2},
    )


@pytest.mark.parametrize("outcome", ["missing", "failed"])
def test_contradiction_goal_requires_every_frozen_search_operation(outcome: str) -> None:
    goal = _goal(
        objective="contradict",
        goal_id="goal:contradict",
        round_status="succeeded",
        search_outcomes=("succeeded", outcome),
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, ())

    assert result.status == "unmet"
    assert result.contrary_search_completed is False


@pytest.mark.parametrize("status", ["succeeded", "partial"])
def test_contradiction_goal_can_be_ready_with_zero_evidence_after_all_searches(
    status: str,
) -> None:
    goal = _goal(
        objective="contradict",
        goal_id="goal:contradict",
        round_status=status,
        search_outcomes=("succeeded", "succeeded"),
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, ())

    assert result.status == "ready"
    assert result.contrary_search_completed is True


def test_job_status_does_not_override_durable_search_provenance() -> None:
    partial_after_search = _goal(
        objective="contradict",
        goal_id="goal:partial-after-search",
        round_status="partial",
        search_outcomes=("succeeded",),
    )
    succeeded_with_failed_search = _goal(
        objective="contradict",
        goal_id="goal:succeeded-with-failed-search",
        round_status="succeeded",
        search_outcomes=("failed",),
    )

    assert COVERAGE_POLICY.evaluate_goal(partial_after_search, ()).status == "ready"
    failed = COVERAGE_POLICY.evaluate_goal(succeeded_with_failed_search, ())
    assert failed.status == "unmet"
    assert failed.contrary_search_completed is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"available_at": NOW + timedelta(seconds=1)}, "cutoff_violation"),
        ({"subject": "无关公司"}, "entity_mismatch"),
        ({"security_code": "000001"}, "security_mismatch"),
        ({"metric": "净利润"}, "metric_mismatch"),
        ({"observed_period": "2025-Q4"}, "period_mismatch"),
        ({"unit": ""}, "unit_unknown"),
        ({"unit": "万元"}, "unit_mismatch"),
    ],
)
def test_frozen_semantic_boundaries_reject_evidence(overrides: dict, reason: str) -> None:
    goal = _goal()

    result = COVERAGE_POLICY.evaluate_goal(goal, (_evidence(goal=goal, **overrides),))

    assert result.status == "unmet"
    assert reason in result.blocking_reasons


def test_missing_frozen_unit_is_typed_ambiguity_not_wildcard() -> None:
    goal = _goal(metric_units=())

    result = COVERAGE_POLICY.evaluate_goal(goal, (_evidence(goal=goal),))

    assert result.status == "unmet"
    assert "expected_unit_unspecified" in result.blocking_reasons


def test_optional_protocol_scope_can_use_grounded_evidence_without_metric_binding() -> None:
    goal = _goal(
        metric_periods=(),
        metric_units=(),
        require_explicit_metric_semantics=False,
    )

    result = COVERAGE_POLICY.evaluate_goal(
        goal,
        (_evidence(goal=goal, observed_period="2026-06-30", unit=None),),
    )

    assert result.status == "ready"
    assert result.qualifying_evidence_ids


def test_optional_protocol_scope_completes_with_explicit_unknowns_after_search() -> None:
    unresolved = COVERAGE_POLICY.evaluate_goal(
        _goal(require_explicit_metric_semantics=False),
        (),
    )
    ready_goal = _goal(
        goal_id="goal:ready",
        require_explicit_metric_semantics=False,
    )
    ready = COVERAGE_POLICY.evaluate_goal(
        ready_goal,
        (_evidence(goal=ready_goal),),
    )

    outcome = decide_coverage(
        (ready, unresolved),
        current_round=1,
        max_rounds=3,
        zero_new_rounds=0,
        allow_unknown_completion=True,
    )

    assert outcome.kind == "ready"
    assert outcome.unknown_goal_ids == (unresolved.goal_id,)


@pytest.mark.parametrize("observed", ["2026-06-30", "2026-04-01/2026-06-30"])
def test_period_semantics_require_exact_frozen_shape(observed: str) -> None:
    goal = _goal(metric_periods=("2026-Q2",))

    result = COVERAGE_POLICY.evaluate_goal(
        goal, (_evidence(goal=goal, observed_period=observed),)
    )

    assert result.status == "unmet"
    assert "period_mismatch" in result.blocking_reasons


@pytest.mark.parametrize(
    ("frozen_period", "observed_period"),
    [
        ("period_end:2026-04-01/2026-06-30", "2026-06-30"),
        ("flow:2026-04-01/2026-06-30", "2026-Q2"),
        ("fiscal_year:2026-01-01/2026-12-31", "2026"),
    ],
)
def test_server_frozen_period_semantics_match_exact_observed_period_shape(
    frozen_period: str, observed_period: str
) -> None:
    goal = _goal(metric_periods=(frozen_period,))

    result = COVERAGE_POLICY.evaluate_goal(
        goal, (_evidence(goal=goal, observed_period=observed_period),)
    )

    assert result.status == "ready"
    assert result.qualifying_evidence_ids


@pytest.mark.parametrize(
    ("frozen_period", "observed_period"),
    [
        ("period_end:2026-04-01/2026-06-30", "2026-05-31"),
        ("flow:2026-04-01/2026-06-30", "2026-05"),
        ("fiscal_year:2026-01-01/2026-12-31", "2025"),
    ],
)
def test_server_frozen_period_semantics_reject_nearby_but_different_periods(
    frozen_period: str, observed_period: str
) -> None:
    goal = _goal(metric_periods=(frozen_period,))

    result = COVERAGE_POLICY.evaluate_goal(
        goal, (_evidence(goal=goal, observed_period=observed_period),)
    )

    assert result.status == "unmet"
    assert "period_mismatch" in result.blocking_reasons


def test_semantically_equivalent_period_representations_do_not_conflict() -> None:
    goal = _goal(
        metric_periods=("period_end:2026-04-01/2026-06-30",),
    )
    quarter = _evidence(goal=goal, observed_period="2026-Q2")
    period_end = _evidence(
        goal=goal,
        observed_period="2026-06-30",
        publication_key="publication-b",
        canonical_url="https://www.sse.com.cn/b.pdf",
        content_sha256="b" * 64,
        source_identity="sse:issuer-b",
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (quarter, period_end))

    assert result.status == "ready"
    assert "conflicting_evidence_periods" not in result.blocking_reasons
    assert set(result.qualifying_evidence_ids) == {
        quarter.evidence_link_id,
        period_end.evidence_link_id,
    }


def test_semantically_different_period_intervals_still_conflict() -> None:
    goal = _goal(
        metric_periods=(
            "period_end:2026-01-01/2026-03-31",
            "period_end:2026-04-01/2026-06-30",
        ),
    )
    q1 = _evidence(goal=goal, observed_period="2026-Q1")
    q2 = _evidence(
        goal=goal,
        observed_period="2026-Q2",
        publication_key="publication-b",
        canonical_url="https://www.sse.com.cn/b.pdf",
        content_sha256="b" * 64,
        source_identity="sse:issuer-b",
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (q1, q2))

    assert result.status == "unmet"
    assert "conflicting_evidence_periods" in result.blocking_reasons
    assert result.qualifying_evidence_ids == ()


@pytest.mark.parametrize(
    ("field", "other", "reason"),
    [
        ("unit", "万元", "conflicting_evidence_units"),
        ("observed_period", "2026-Q1", "conflicting_evidence_periods"),
    ],
)
def test_qualifying_items_with_conflicting_semantics_do_not_qualify_together(
    field: str, other: str, reason: str
) -> None:
    goal = _goal(
        metric_units=("亿元", "万元"),
        metric_periods=("2026-Q1", "2026-Q2"),
    )
    first = _evidence(goal=goal)
    second = _evidence(
        goal=goal,
        publication_key="publication-b",
        canonical_url="https://www.sse.com.cn/b.pdf",
        content_sha256="b" * 64,
        source_identity="sse:issuer-b",
        **{field: other},
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (first, second))

    assert result.status == "unmet"
    assert reason in result.blocking_reasons
    assert result.qualifying_evidence_ids == ()


def test_total_document_count_cannot_make_goal_ready() -> None:
    goal = _goal()
    unrelated = tuple(
        _evidence(goal=goal, metric="净利润", publication_key=f"p-{index}")
        for index in range(10)
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, unrelated)

    assert result.status == "unmet"
    assert result.observed_independent_source_count == 0


def test_nonqualifying_extra_document_does_not_override_qualifying_evidence() -> None:
    goal = _goal()
    valid = _evidence(goal=goal)
    unrelated = _evidence(
        goal=goal,
        metric="净利润",
        publication_key="unrelated-publication",
        canonical_url="https://www.sse.com.cn/unrelated.pdf",
        content_sha256="c" * 64,
    )

    result = COVERAGE_POLICY.evaluate_goal(goal, (valid, unrelated))

    assert result.status == "ready"
    assert result.qualifying_evidence_ids == (valid.evidence_link_id,)


def test_cross_job_or_goal_evidence_is_excluded() -> None:
    goal = _goal()
    wrong_job = _evidence(goal=goal)
    wrong_job = CoverageEvidence(**{**wrong_job.as_dict(), "job_id": uuid.uuid4()})
    wrong_goal = _evidence(goal=goal)
    wrong_goal = CoverageEvidence(**{**wrong_goal.as_dict(), "goal_id": "other"})

    result = COVERAGE_POLICY.evaluate_goal(goal, (wrong_job, wrong_goal))

    assert result.status == "unmet"
    assert result.qualifying_evidence_ids == ()


def test_decision_continue_only_unresolved_goals_with_explicit_expansion() -> None:
    ready_goal = _goal(goal_id="ready")
    unmet_goal = _goal(goal_id="unmet")
    ready = COVERAGE_POLICY.evaluate_goal(ready_goal, (_evidence(goal=ready_goal),))
    unmet = COVERAGE_POLICY.evaluate_goal(unmet_goal, ())

    decision = decide_coverage(
        (ready, unmet), current_round=1, max_rounds=3, zero_new_rounds=0
    )

    assert decision.kind == "continue"
    assert decision.unresolved_goal_ids == ("unmet",)
    assert decision.expansion_trigger == "coverage_unmet"
    assert decision.expansion_reason.strip()


def test_maximum_round_and_zero_new_sources_exhaust_with_unknowns() -> None:
    goal = _goal()
    unmet = COVERAGE_POLICY.evaluate_goal(goal, ())

    maximum = decide_coverage(
        (unmet,), current_round=3, max_rounds=3, zero_new_rounds=0
    )
    stagnant = decide_coverage(
        (unmet,), current_round=2, max_rounds=3, zero_new_rounds=2
    )

    assert maximum.kind == stagnant.kind == "exhausted"
    assert maximum.unknown_goal_ids == stagnant.unknown_goal_ids == (goal.goal_id,)


def test_ordinary_no_results_continue_without_injected_boundary_trust() -> None:
    goal = _goal()
    unmet = COVERAGE_POLICY.evaluate_goal(goal, ())

    inside = decide_coverage(
        (unmet,), current_round=1, max_rounds=3, zero_new_rounds=0
    )

    assert inside.kind == "continue"


@pytest.mark.parametrize(
    ("evidence_overrides", "expected_boundary"),
    [
        ({"authority": "licensed_research"}, "source_policy"),
        ({"available_at": NOW + timedelta(days=1)}, "time_window"),
        ({"subject": "无关公司"}, "entity_scope"),
        ({"metric": "净利润"}, "metric_scope"),
        ({"unit": "万元"}, "unit_semantics"),
    ],
)
def test_needs_decision_boundary_is_derived_from_persisted_observations(
    evidence_overrides: dict, expected_boundary: str
) -> None:
    goal = _goal()
    unmet = COVERAGE_POLICY.evaluate_goal(
        goal, (_evidence(goal=goal, **evidence_overrides),)
    )

    decision = decide_coverage(
        (unmet,), current_round=1, max_rounds=3, zero_new_rounds=0
    )

    assert decision.kind == "needs_decision"
    assert decision.boundary_changes == (expected_boundary,)
    assert decision.decision_action == "revise_frozen_research_boundary"


def test_boundary_decision_context_is_complete_typed_and_deterministic() -> None:
    goal = _goal()
    result = COVERAGE_POLICY.evaluate_goal(
        goal,
        (_evidence(goal=goal, observed_period="2025-Q4"),),
    )
    outcome = decide_coverage(
        (result,), current_round=1, max_rounds=3, zero_new_rounds=0
    )

    payload = build_boundary_decision_context(
        (result,), outcome=outcome, attempted_rounds=1
    )

    assert payload == {
        "action": "revise_frozen_research_boundary",
        "attempted_rounds": 1,
        "boundary_codes": ["metric_scope"],
        "affected_goals": [
            {"goal_id": goal.goal_id, "reason_codes": ["period_mismatch"]}
        ],
        "missing": [
            {
                "goal_id": goal.goal_id,
                "reason_codes": list(result.blocking_reasons),
            }
        ],
        "attempted": [
            {"goal_id": goal.goal_id, "search_completed": True}
        ],
        "cannot_continue_reason": "继续补证需要改变已冻结的研究边界。",
        "recommendation": {
            "kind": "revise_scope",
            "label": "调整研究边界",
            "impact": "创建新的冻结范围版本后，系统才能按新边界继续补证；现有运行与证据记录保持不变。",
        },
        "alternatives": [
            {
                "kind": "keep_scope",
                "label": "保持当前研究范围",
                "impact": "不改变已冻结边界，未解决目标继续明确保留为未知。",
            },
            {
                "kind": "stop",
                "label": "停止本次研究",
                "impact": "停止后续自动补证，并保留当前证据、缺口与运行记录。",
            },
        ],
    }
