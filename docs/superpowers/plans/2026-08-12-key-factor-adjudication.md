# Key Factor Extraction and Adjudication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract complete measurable factor candidates and replace evidence-count conclusions with an explicit, reviewable four-gate key-factor assessment.

**Architecture:** Keep source parsing separate from factor adjudication. Parser v2 produces source-bound structured candidates; `FactorAdjudicationService` evaluates event association, company transmission, data verification, and exclusive explanation independently. A factor is eligible only when every gate passes, and it becomes formal only after human review.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, FastAPI, Pydantic v2, pytest, existing immutable ledger and review patterns.

---

## File map

- Modify `backend/app/services/key_factor_candidates.py`: multi-match parser v2 and structured forecast fields.
- Modify `backend/app/models/research_expression.py`: persist new candidate fields and adjudication records.
- Modify `backend/app/services/market_expression.py`, `backend/app/queries/market_expression.py`, and `backend/app/schemas/v1/market_expression.py`: write/read the structured fields.
- Create `backend/app/services/factor_adjudication.py`: four-gate policy and persistence.
- Create `backend/app/schemas/v1/factor_adjudication.py` and `backend/app/api/v1/factor_adjudication.py`: generate and review candidates.
- Modify `backend/app/services/event_conclusion.py`: consume reviewed eligible assessments instead of evidence counts.
- Create `backend/alembic/versions/0051_structured_factor_candidates.py`: additive parser-field migration.
- Create `backend/alembic/versions/0052_factor_adjudication.py`: additive assessment and review tables.

### Task 1: Upgrade numeric forecast parsing to v2

**Files:**
- Modify: `backend/app/services/key_factor_candidates.py`
- Test: `backend/tests/test_key_factor_candidate_parser.py`

- [ ] **Step 1: Write failing multi-forecast and field tests**

```python
def test_parser_extracts_every_numeric_forecast_and_target_fields():
    result = KeyFactorCandidateParser().parse(
        "预计工业富联2026年归母净利润达到300亿元；预测2027年服务器收入不低于500亿元。"
    )
    assert result.parser_version == "key-factor-rules-v2"
    assert len(result.candidates) == 2
    assert [item.metric_name for item in result.candidates] == ["归母净利润", "服务器收入"]
    assert [item.target_value for item in result.candidates] == [Decimal("300"), Decimal("500")]
    assert [item.comparator for item in result.candidates] == ["gte", "gte"]
    assert [item.unit for item in result.candidates] == ["亿元", "亿元"]
    assert [item.entity_name for item in result.candidates] == ["工业富联", None]
    assert [item.forecast_period_label for item in result.candidates] == ["2026年", "2027年"]
```

Add tests for `低于`/`不超过` mapping to `lte`, `%` units, duplicate excerpts, and vague text producing no candidate.

- [ ] **Step 2: Run and verify current single-match failure**

Run: `cd backend && uv run pytest tests/test_key_factor_candidate_parser.py -k "every_numeric or target_fields" -v`

Expected: FAIL because `.search()` returns one candidate and the fields do not exist.

- [ ] **Step 3: Extend the candidate value object**

Add:

```python
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class KeyFactorCandidate:
    name: str
    metric_name: str
    expected_direction: str
    target_value: Decimal | None
    comparator: str | None
    unit: str | None
    entity_name: str | None
    business_scope: str | None
    forecast_period_label: str | None
    verification_window_start: date
    verification_window_end: date
    support_condition: str
    refutation_condition: str
    next_verification_event: str
    evidence_excerpt: str
    rule_id: str
```

Set `PARSER_VERSION = "key-factor-rules-v2"`. Replace `_NUMERIC_FORECAST.search` with `finditer`, normalize comma-separated numbers before `Decimal`, split the numeric unit from the value, and deduplicate by `(excerpt, metric_name, year, target_value, unit)` while preserving source order.

- [ ] **Step 4: Run parser tests**

Run: `cd backend && uv run pytest tests/test_key_factor_candidate_parser.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/key_factor_candidates.py backend/tests/test_key_factor_candidate_parser.py
git commit -m "feat: extract structured key factor forecasts"
```

### Task 2: Persist and expose structured candidate fields

**Files:**
- Modify: `backend/app/models/research_expression.py`
- Modify: `backend/app/services/market_expression.py`
- Modify: `backend/app/queries/market_expression.py`
- Modify: `backend/app/schemas/v1/market_expression.py`
- Create: `backend/alembic/versions/0051_structured_factor_candidates.py`
- Test: `backend/tests/test_market_expression_api.py`

- [ ] **Step 1: Write a failing API persistence test**

Start a candidate run and assert the response retains:

```python
candidate = response.json()["candidates"][0]
assert candidate["target_value"] == "300.000000"
assert candidate["comparator"] == "gte"
assert candidate["unit"] == "亿元"
assert candidate["entity_name"] == "工业富联"
assert candidate["business_scope"] is None
assert candidate["forecast_period_label"] == "2026年"
```

- [ ] **Step 2: Verify schema failure**

Run: `cd backend && uv run pytest tests/test_market_expression_api.py -k structured_candidate -v`

Expected: FAIL because the columns and DTO fields do not exist.

- [ ] **Step 3: Add nullable columns additively**

Add these mapped columns to `KeyFactorCandidate`:

```python
target_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
comparator: Mapped[str | None] = mapped_column(String(16), nullable=True)
unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
entity_name: Mapped[str | None] = mapped_column(Text, nullable=True)
business_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
forecast_period_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

In migration `0051` (`revision = "0051"`, `down_revision = "0050"`), use `op.add_column` for the same nullable columns. Do not backfill v1 candidates with invented values; `parser_version` already identifies their semantics.

- [ ] **Step 4: Wire service and DTO fields**

Copy every parsed field in `MarketExpressionService.parse_key_factor_candidates`, add matching optional Pydantic fields, and serialize Decimal values without converting them to binary floats.

- [ ] **Step 5: Run migration and API tests**

Run: `cd backend && uv run alembic upgrade head && uv run pytest tests/test_key_factor_candidate_parser.py tests/test_market_expression_api.py -v`

Expected: migration succeeds and tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/research_expression.py backend/app/services/market_expression.py backend/app/queries/market_expression.py backend/app/schemas/v1/market_expression.py backend/alembic/versions/0051_structured_factor_candidates.py backend/tests/test_market_expression_api.py
git commit -m "feat: persist structured factor candidates"
```

### Task 3: Add append-only four-gate assessment records

**Files:**
- Modify: `backend/app/models/research_expression.py`
- Create: `backend/alembic/versions/0052_factor_adjudication.py`
- Create: `backend/app/services/factor_adjudication.py`
- Create: `backend/tests/test_factor_adjudication.py`

- [ ] **Step 1: Write pure policy tests**

```python
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("passed", "passed", "passed", "passed"), "eligible_key_factor"),
        (("failed", "passed", "passed", "passed"), "rejected"),
        (("passed", "insufficient", "passed", "passed"), "needs_evidence"),
    ],
)
def test_overall_classification_has_no_hidden_score(statuses, expected):
    gates = dict(zip(FACTOR_GATE_NAMES, statuses, strict=True))
    assert classify_factor(gates) == expected
```

Add a persistence test proving each gate retains its rationale and evidence-link ids. Add a separate review test proving a human decision appends `FactorAssessmentReview` without updating the machine candidate.

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && uv run pytest tests/test_factor_adjudication.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Add the assessment model**

Add the immutable candidate and review records:

```python
class FactorAssessment(Base):
    __tablename__ = "factor_assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    thesis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("theses.id"), nullable=False, index=True)
    scope_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event_research_scope_versions.id"), nullable=False)
    gates: Mapped[dict] = mapped_column(JSON, nullable=False)
    overall_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FactorAssessmentReview(Base):
    __tablename__ = "factor_assessment_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    factor_assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("factor_assessments.id"), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    review_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Migration `0052` must use `down_revision = "0051"`, add checks for the three classifications and `confirmed/rejected` review outcomes, and make `factor_assessment_id` unique so a candidate receives one final human decision.

- [ ] **Step 4: Implement gate validation and classification**

```python
FACTOR_GATE_NAMES = (
    "event_association",
    "company_transmission",
    "data_verification",
    "exclusive_explanation",
)
GATE_STATUSES = frozenset({"passed", "failed", "insufficient"})


def classify_factor(gates: dict[str, str]) -> str:
    statuses = [gates[name] for name in FACTOR_GATE_NAMES]
    if all(status == "passed" for status in statuses):
        return "eligible_key_factor"
    if any(status == "failed" for status in statuses):
        return "rejected"
    return "needs_evidence"
```

Require every stored gate to contain exactly `status`, `rationale`, and `evidence_link_ids`; validate that referenced evidence belongs to the Case, is reviewed, and is mapped to the current scope factor.

- [ ] **Step 5: Run model and service tests**

Run: `cd backend && uv run pytest tests/test_factor_adjudication.py tests/test_event_research_scope.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/research_expression.py backend/app/services/factor_adjudication.py backend/alembic/versions/0052_factor_adjudication.py backend/tests/test_factor_adjudication.py
git commit -m "feat: record four-gate factor assessments"
```

### Task 4: Generate gate candidates from formal research records

**Files:**
- Modify: `backend/app/services/factor_adjudication.py`
- Test: `backend/tests/test_factor_adjudication.py`

- [ ] **Step 1: Write one test per gate input**

Assert:

- event association is `passed` only with reviewed evidence mapped to that current factor;
- company transmission is `passed` only with reviewed `MarketInstrumentBinding` and reviewed `FundamentalImpact` for the Case/factor;
- data verification is `passed` only with a reviewed supporting `ClaimVerification` or reviewed forecast verdict, `failed` with a reviewed contradiction, otherwise `insufficient`;
- exclusive explanation is `passed` only when the current protocol has a contradiction predicate and at least one reviewed alternative/counter-evidence result, otherwise `insufficient`.

- [ ] **Step 2: Verify all gates are currently absent**

Run: `cd backend && uv run pytest tests/test_factor_adjudication.py -k "event_gate or transmission_gate or verification_gate or explanation_gate" -v`

Expected: FAIL.

- [ ] **Step 3: Implement `generate_candidate` without evidence counts**

```python
def generate_candidate(
    self,
    case_id: uuid.UUID,
    thesis_id: uuid.UUID,
) -> FactorAssessment:
    scope = self._current_scope(case_id)
    gates = {
        "event_association": self._event_association_gate(scope, thesis_id),
        "company_transmission": self._company_transmission_gate(case_id, thesis_id),
        "data_verification": self._data_verification_gate(case_id, thesis_id),
        "exclusive_explanation": self._exclusive_explanation_gate(case_id, thesis_id),
    }
    record = FactorAssessment(
        research_case_id=case_id,
        thesis_id=thesis_id,
        scope_version_id=scope.id,
        gates=gates,
        overall_classification=classify_factor(
            {name: value["status"] for name, value in gates.items()}
        ),
        created_at=_utcnow(),
    )
    self._session.add(record)
    self._session.flush()
    return record
```

Every helper returns explicit insufficiency reasons; no helper returns a numeric score.

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && uv run pytest tests/test_factor_adjudication.py -v`

Expected: PASS.

```bash
git add backend/app/services/factor_adjudication.py backend/tests/test_factor_adjudication.py
git commit -m "feat: generate explicit factor gate candidates"
```

### Task 5: Add review API and human publication boundary

**Files:**
- Create: `backend/app/schemas/v1/factor_adjudication.py`
- Create: `backend/app/api/v1/factor_adjudication.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/factor_adjudication.py`
- Create: `backend/tests/test_factor_adjudication_api.py`

- [ ] **Step 1: Write API tests**

Cover candidate generation, stale-scope rejection, human review, and the publication gate:

```python
reviewed = client.post(
    f"/api/v1/factor-assessments/{assessment_id}/reviews",
    json={
        "outcome": "confirmed",
        "reviewed_by": "human:alice",
        "review_reason": "四项门槛和来源均已复核",
    },
)
assert reviewed.status_code == 201, reviewed.text
assert reviewed.json()["outcome"] == "confirmed"
```

Assert `confirmed` is rejected when overall classification is not `eligible_key_factor`, while `rejected` remains allowed.

- [ ] **Step 2: Implement schemas and endpoints**

Expose:

```text
POST /research-cases/{case_id}/theses/{thesis_id}/factor-assessments
POST /factor-assessments/{assessment_id}/reviews
GET  /research-cases/{case_id}/factor-assessments
```

Use the same idempotency and conflict patterns as proposal review. Persist `FactorAssessmentReview`; never update the machine `FactorAssessment` row.

- [ ] **Step 3: Run API and OpenAPI tests**

Run: `cd backend && uv run pytest tests/test_factor_adjudication_api.py tests/test_api_v1_common.py -v`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/v1/factor_adjudication.py backend/app/api/v1/factor_adjudication.py backend/app/api/v1/router.py backend/app/services/factor_adjudication.py backend/tests/test_factor_adjudication_api.py
git commit -m "feat: review factor gate assessments"
```

### Task 6: Replace evidence-count conclusion selection

**Files:**
- Modify: `backend/app/services/event_conclusion.py`
- Modify: `backend/app/services/event_research_scope_evidence.py`
- Test: `backend/tests/test_event_research_lifecycle.py`
- Test: `backend/tests/test_event_research_scope.py`

- [ ] **Step 1: Write failing conclusion tests**

Cover these cases:

```python
# Ten support links but no reviewed four-gate assessment.
with pytest.raises(ValidationFailedError, match="reviewed factor assessment"):
    EventConclusionService(session).create_draft(case.id)

# Exactly one reviewed eligible factor.
draft = EventConclusionService(session).create_draft(case.id)
assert draft.primary_factor == eligible_thesis.statement

# Two reviewed eligible factors.
draft = EventConclusionService(session).create_draft(case.id)
assert draft.primary_factor is None
assert "多个因素" in draft.text
```

- [ ] **Step 2: Verify old count behavior fails the tests**

Run: `cd backend && uv run pytest tests/test_event_research_lifecycle.py tests/test_event_research_scope.py -k factor_assessment -v`

Expected: FAIL because support-minus-contradiction counts select the factor.

- [ ] **Step 3: Select from reviewed current-scope assessments**

Remove `scores`. Query `FactorAssessment` rows joined to a `FactorAssessmentReview(outcome="confirmed")` for the current scope and `overall_classification == "eligible_key_factor"`. Set `primary_factor` only when exactly one eligible factor exists. If none exist, block draft creation; if multiple exist, create a bounded draft that names the ambiguity and requires human selection.

Evidence coverage remains necessary but is no longer sufficient for a conclusion.

- [ ] **Step 4: Run event-research suites**

Run: `cd backend && uv run pytest tests/test_event_research_lifecycle.py tests/test_event_research_scope.py tests/test_factor_adjudication.py tests/test_factor_adjudication_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/event_conclusion.py backend/app/services/event_research_scope_evidence.py backend/tests/test_event_research_lifecycle.py backend/tests/test_event_research_scope.py
git commit -m "fix: base event conclusions on reviewed factor gates"
```

### Task 7: Phase verification and evaluation fixtures

**Files:**
- Create: `backend/tests/fixtures/factor_adjudication_cases.json`
- Create: `backend/tests/test_factor_adjudication_evaluation.py`

- [ ] **Step 1: Add a small gold set**

Include at least 20 source-bound cases covering multiple numeric forecasts, negative forecasts, missing units, entity ambiguity, alternative explanations, contradictory actuals, and insufficient company transmission. Each fixture must name expected parsed fields and four gate statuses.

- [ ] **Step 2: Add deterministic evaluation assertions**

Assert parser exact-match accuracy, zero invented target values, and exact gate/classification outcomes. Tests must print failing fixture ids.

- [ ] **Step 3: Run the complete factor slice**

Run: `cd backend && uv run pytest tests/test_key_factor_candidate_parser.py tests/test_market_expression_api.py tests/test_factor_adjudication.py tests/test_factor_adjudication_api.py tests/test_factor_adjudication_evaluation.py tests/test_event_research_lifecycle.py tests/test_event_research_scope.py -v`

Expected: PASS.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && uv run pytest`

Expected: PASS with only documented skips.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/factor_adjudication_cases.json backend/tests/test_factor_adjudication_evaluation.py
git commit -m "test: add factor adjudication gold set"
```
