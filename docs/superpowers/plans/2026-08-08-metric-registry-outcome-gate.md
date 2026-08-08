# Metric Registry and Outcome Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a new-protocol thesis a versioned, human-approved fixed outcome metric and prevent it from entering formal assessment when the required research definition is incomplete.

**Architecture:** Add immutable metric-definition versions and immutable outcome-binding versions alongside the existing evidence ledger. A pure `ResearchabilityGate` reads the latest approved binding for a thesis and returns machine-readable missing requirements; it does not edit a thesis or infer a conclusion. During this first slice, existing theses remain legacy-compatible and a bound thesis deliberately remains blocked until the subsequent mechanism-template and verification-rule slice supplies its dependencies.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL, pytest.

---

## Scope and safety boundary

This is the first of the three plans named in [the confirmed design](../specs/2026-08-08-metric-and-mechanism-template-design.md). It implements only:

```text
MetricRegistryVersion + OutcomeBindingVersion + ResearchabilityGate (blocking read model)
```

It does **not** implement `MechanismTemplateVersion`, `VerificationRuleVersion`, AI proposal generation, frontend workbench, or an automatic causal assessment. The gate must return explicit missing requirements until the next plan supplies those objects; it must not use a stringly-typed placeholder to pretend a template exists.

The active dirty change in `backend/app/services/event_research_scope.py` is excluded. Execute later in a dedicated worktree; do not branch from or edit the current dirty tree.

## Domain contract

```python
MetricRole = Literal["outcome", "driver", "mediator", "context"]
OutcomeDirection = Literal["increase", "decrease", "stable", "mixed"]
OutcomeBindingState = Literal["draft", "approved", "rejected"]
ResearchabilityStatus = Literal["not_applicable", "blocked", "ready"]

@dataclass(frozen=True, slots=True)
class MetricDefinitionInput:
    metric_id: str                    # e.g. "business_line_revenue"
    display_name: str                 # e.g. "相关业务收入"
    canonical_definition: str         # unambiguous accounting/business definition
    entity_scope: str                 # company | business_line | product_line
    unit: str                         # yuan | units | percent | multiple
    frequency: str                    # quarterly | annual | monthly | event
    period_semantics: str             # period_end | point_in_time | cumulative
    allowed_source_roles: list[str]   # primary_disclosure etc.
    role_eligibility: list[MetricRole]

@dataclass(frozen=True, slots=True)
class OutcomeBindingInput:
    thesis_id: UUID
    metric_definition_id: UUID
    entity_scope: dict[str, str]      # company_id + business/product scope
    direction: OutcomeDirection
    baseline: dict[str, str]          # typed source ref + value + period
    horizon_start: date
    horizon_end: date
    reviewer: str
    reason: str
```

`MetricDefinitionVersion` is never updated. Correcting a definition inserts a successor with the same `metric_id`, a new definition version and `supersedes_id`. `OutcomeBindingVersion` is likewise append-only; the latest approved binding by `created_at` is the effective binding. A revision must point to `supersedes_id`; it never mutates an earlier binding.

The gate reports the following stable reason codes:

```text
missing_outcome_binding
binding_not_approved
metric_not_outcome_eligible
invalid_scope
invalid_baseline
invalid_horizon
missing_mechanism_template
missing_verification_rule
insufficient_primary_metrics
missing_counter_hypothesis
```

Only the first six can be resolved by this plan. The last four are deliberately visible blockers for the later plans.

## File map

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0019_metric_registry_outcome_binding.py` | Schema extension, immutable tables and PostgreSQL triggers. |
| `backend/app/models/ledger.py` | ORM records, immutable-table registration and thesis protocol opt-in field. |
| `backend/app/domain/research_protocol.py` | Pure enum, baseline, scope, horizon and gate validation. |
| `backend/app/repositories/research_protocol.py` | Append-only metric/binding writes and effective-version queries. |
| `backend/app/services/research_protocol.py` | Metric admission, binding review and `ResearchabilityGate`. |
| `backend/app/schemas/v1/research_protocol.py` | Strict request/response DTOs. |
| `backend/app/api/v1/commands/research_protocol.py` | Metric and outcome-binding command routes. |
| `backend/app/api/v1/research_protocol.py` | Read-only metric lookup, effective binding and gate routes. |
| `backend/app/api/v1/router.py` | Route registration. |
| `backend/app/api/v1/commands/cases.py` | Enables protocol-required new thesis creation without changing legacy defaults. |
| `backend/tests/test_research_protocol.py` | Pure domain, repository and immutable-ledger tests. |
| `backend/tests/test_research_protocol_api.py` | API/error-envelope/idempotency tests. |
| `backend/tests/test_ai_engine.py` | Ensures opted-in blocked theses cannot be formally assessed. |

### Task 1: Add append-only registry and outcome-binding storage

**Files:**
- Create: `backend/alembic/versions/0019_metric_registry_outcome_binding.py`
- Modify: `backend/app/models/ledger.py:217-268`
- Test: `backend/tests/test_research_protocol.py`

- [ ] **Step 1: Write failing ledger tests**

```python
def test_metric_versions_and_outcome_bindings_are_append_only(session, thesis):
    metric = MetricDefinitionVersion(
        metric_id="business_line_revenue", version=1, display_name="相关业务收入",
        canonical_definition="目标公司指定业务线按季度确认的营业收入", entity_scope="business_line",
        unit="yuan", frequency="quarterly", period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"], role_eligibility=["outcome"],
        approved_by="alice", reason="initial metric", created_at=now,
    )
    session.add(metric); session.flush()
    binding = OutcomeBindingVersion(
        thesis_id=thesis.id, metric_definition_id=metric.id,
        entity_scope={"company_id": "company-a", "business_line": "800G optics"},
        direction="increase", baseline={"source_ref": "doc:baseline-1", "value": "10", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
        horizon_start=date(2026, 4, 1), horizon_end=date(2026, 12, 31), state="draft",
        reviewer="alice", reason="initial binding", created_at=now,
    )
    session.add(binding); session.flush()
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(OutcomeBindingVersion).where(OutcomeBindingVersion.id == binding.id).values(direction="decrease"))

def test_legacy_thesis_does_not_require_protocol_by_default(thesis):
    assert thesis.research_protocol_required is False
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `cd backend && pytest tests/test_research_protocol.py -q`

Expected: collection fails because the ORM records and `research_protocol_required` field do not exist.

- [ ] **Step 3: Add the migration and ORM models**

Create immutable `metric_definition_versions` and `outcome_binding_versions` tables. Add non-null `research_protocol_required` to `theses` with server default `false`, preserving existing behavior. Give metric definitions `(metric_id, version)` uniqueness and bindings a nullable `supersedes_id`. Add new tables to `IMMUTABLE_TABLES` and use the established PostgreSQL update/delete trigger pattern.

```python
class MetricDefinitionVersion(Base):
    __tablename__ = "metric_definition_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_definition: Mapped[str] = mapped_column(Text, nullable=False)
    entity_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    period_semantics: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_source_roles: Mapped[list] = mapped_column(JSON, nullable=False)
    role_eligibility: Mapped[list] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("metric_definition_versions.id"))
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

```python
class OutcomeBindingVersion(Base):
    __tablename__ = "outcome_binding_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    thesis_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("theses.id"), nullable=False)
    metric_definition_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("metric_definition_versions.id"), nullable=False)
    entity_scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False)
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("outcome_binding_versions.id"))
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Run focused model tests**

Run: `cd backend && pytest tests/test_research_protocol.py tests/test_documents.py -q`

Expected: PASS; both new tables reject updates/deletes and legacy thesis rows remain valid.

- [ ] **Step 5: Commit the storage slice**

```bash
git add backend/alembic/versions/0019_metric_registry_outcome_binding.py backend/app/models/ledger.py backend/tests/test_research_protocol.py
git commit -m "feat: add metric registry and outcome bindings"
```

### Task 2: Implement pure validation and effective-version reads

**Files:**
- Create: `backend/app/domain/research_protocol.py`
- Create: `backend/app/repositories/research_protocol.py`
- Test: `backend/tests/test_research_protocol.py`

- [ ] **Step 1: Write failing validation tests**

```python
def test_outcome_metric_must_be_outcome_eligible(valid_metric, thesis):
    driver_only = replace(valid_metric, role_eligibility=["driver"])
    with pytest.raises(ValidationError, match="outcome eligible"):
        service.create_outcome_binding(
            thesis.id, driver_only.id, entity_scope={"company_id": "company-a", "business_line": "800G optics"},
            direction="increase", baseline={"source_ref": "doc:baseline-1", "value": "10", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
            horizon_start=date(2026, 4, 1), horizon_end=date(2026, 12, 31), reviewer="alice", reason="test",
        )

def test_binding_rejects_inverted_horizon_and_untraceable_baseline(metric, thesis):
    with pytest.raises(ValidationError, match="horizon_start"):
        service.create_outcome_binding(
            thesis.id, metric.id, entity_scope={"company_id": "company-a", "business_line": "800G optics"}, direction="increase",
            baseline={"source_ref": "doc:baseline-1", "value": "10", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
            horizon_start=date(2026, 6, 30), horizon_end=date(2026, 3, 31), reviewer="alice", reason="test",
        )
    with pytest.raises(ValidationError, match="baseline.source_ref"):
        service.create_outcome_binding(
            thesis.id, metric.id, entity_scope={"company_id": "company-a", "business_line": "800G optics"}, direction="increase",
            baseline={"value": "10"}, horizon_start=date(2026, 4, 1), horizon_end=date(2026, 12, 31), reviewer="alice", reason="test",
        )

def test_effective_metric_is_latest_version_not_mutated(session):
    v1 = repo.add_metric(MetricDefinitionInput("business_line_revenue", "相关业务收入", "目标业务线收入", "business_line", "yuan", "quarterly", "period_end", ["primary_disclosure"], ["outcome"]), approved_by="alice", reason="v1")
    v2 = repo.add_metric(MetricDefinitionInput("business_line_revenue", "相关业务收入", "目标业务线收入，排除非目标产品", "business_line", "yuan", "quarterly", "period_end", ["primary_disclosure"], ["outcome"]), approved_by="alice", reason="scope clarification", supersedes_id=v1.id)
    assert repo.effective_metric("business_line_revenue").id == v2.id
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `cd backend && pytest tests/test_research_protocol.py -q`

Expected: FAIL because the protocol domain and repository do not exist.

- [ ] **Step 3: Implement domain rules and repository methods**

Implement `validate_metric_definition()`, `validate_outcome_binding()`, `effective_metric(metric_id)`, `effective_binding(thesis_id)`, `add_metric_version()` and `add_outcome_binding_version()`. A baseline must contain non-empty `source_ref`, `value`, `unit`, `observed_period`, and `available_at`; its unit must equal the bound metric unit. Validate that entity-scope keys match the metric's declared scope, that the horizon is non-empty and ordered, and that a revision references a binding for the same thesis.

```python
def validate_outcome_binding(metric: MetricDefinitionVersion, binding: OutcomeBindingInput) -> None:
    if "outcome" not in metric.role_eligibility:
        raise ValidationError("metric is not outcome eligible")
    if binding.horizon_start > binding.horizon_end:
        raise ValidationError("horizon_start must not be after horizon_end")
    required = {"source_ref", "value", "unit", "observed_period", "available_at"}
    if set(binding.baseline) != required or binding.baseline["unit"] != metric.unit:
        raise ValidationError("baseline.source_ref/value/unit/observed_period/available_at must match metric")
```

- [ ] **Step 4: Run domain/repository tests**

Run: `cd backend && pytest tests/test_research_protocol.py -q`

Expected: PASS; invalid scope, baseline, role and successor paths are rejected before writes.

- [ ] **Step 5: Commit the domain slice**

```bash
git add backend/app/domain/research_protocol.py backend/app/repositories/research_protocol.py backend/tests/test_research_protocol.py
git commit -m "feat: validate versioned research outcomes"
```

### Task 3: Create approval-only outcome bindings and the blocking gate

**Files:**
- Create: `backend/app/services/research_protocol.py`
- Modify: `backend/app/services/research.py:78-118`
- Test: `backend/tests/test_research_protocol.py`

- [ ] **Step 1: Write failing service tests**

```python
def test_gate_is_not_applicable_for_legacy_thesis(legacy_thesis):
    assert service.check(legacy_thesis.id).status == "not_applicable"

def test_protocol_thesis_without_binding_is_blocked(protocol_thesis):
    result = service.check(protocol_thesis.id)
    assert result.status == "blocked"
    assert result.reason_codes == ["missing_outcome_binding"]

def test_approved_binding_exposes_later_template_blockers(protocol_thesis, approved_binding):
    result = service.check(protocol_thesis.id)
    assert result.status == "blocked"
    assert result.reason_codes == [
        "missing_mechanism_template", "missing_verification_rule",
        "insufficient_primary_metrics", "missing_counter_hypothesis",
    ]
```

- [ ] **Step 2: Run the service tests and verify failure**

Run: `cd backend && pytest tests/test_research_protocol.py -q`

Expected: FAIL because `ResearchabilityGate` and outcome approval service do not exist.

- [ ] **Step 3: Implement state transitions as append-only decisions**

`ResearchProtocolService.create_outcome_binding()` creates a `draft` binding only. `approve_outcome_binding()` writes a successor approved binding with reviewer/reason; it never updates the draft. Extend `ResearchService.add_thesis()` to accept `research_protocol_required=False` and pass the flag through to `ResearchRepository.add_thesis()`.

`ResearchabilityGate.check(thesis_id)` returns `not_applicable` for legacy theses. For protocol-required theses, it returns canonical ordered reason codes and no inferred conclusion. The gate may return `ready` only when later template/rule repositories are present and all conditions are met; in this slice every protocol thesis with an approved binding remains safely `blocked` by the four forward dependency codes.

- [ ] **Step 4: Run service and existing thesis tests**

Run: `cd backend && pytest tests/test_research_protocol.py tests/test_event_research_lifecycle.py tests/test_recall.py -q`

Expected: PASS; existing theses retain current behavior and new protocol theses expose an auditable block rather than a fake readiness state.

- [ ] **Step 5: Commit the gate slice**

```bash
git add backend/app/services/research_protocol.py backend/app/services/research.py backend/tests/test_research_protocol.py
git commit -m "feat: block incomplete research protocol cases"
```

### Task 4: Expose command and read APIs without a UI dependency

**Files:**
- Create: `backend/app/schemas/v1/research_protocol.py`
- Create: `backend/app/api/v1/commands/research_protocol.py`
- Create: `backend/app/api/v1/research_protocol.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/api/v1/commands/cases.py`
- Test: `backend/tests/test_research_protocol_api.py`

- [ ] **Step 1: Write failing API contract tests**

```python
def test_metric_registry_and_outcome_binding_api_flow(api_client, protocol_thesis):
    metric = api_client.post("/api/v1/metric-definitions", json=METRIC_BODY)
    assert metric.status_code == 201
    draft = api_client.post(f"/api/v1/theses/{protocol_thesis.id}/outcome-bindings", json=BINDING_BODY(metric.json()["id"]))
    assert draft.status_code == 201 and draft.json()["state"] == "draft"
    approved = api_client.post(f"/api/v1/outcome-bindings/{draft.json()['id']}/approve", json=APPROVE_BODY)
    assert approved.status_code == 201 and approved.json()["state"] == "approved"
    gate = api_client.get(f"/api/v1/theses/{protocol_thesis.id}/researchability")
    assert gate.json()["status"] == "blocked"
    assert "missing_mechanism_template" in gate.json()["reason_codes"]

def test_outcome_binding_rejects_missing_baseline_source(api_client, protocol_thesis):
    response = api_client.post(f"/api/v1/theses/{protocol_thesis.id}/outcome-bindings", json={"metric_definition_id": str(metric_id), "baseline": {"value": "10"}})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
```

- [ ] **Step 2: Run the API tests and verify failure**

Run: `cd backend && pytest tests/test_research_protocol_api.py -q`

Expected: FAIL with 404 because schemas and routes do not exist.

- [ ] **Step 3: Implement strict routes and DTOs**

Implement:

```text
POST /api/v1/metric-definitions
GET  /api/v1/metric-definitions?role=outcome&entity_scope=business_line
POST /api/v1/theses/{thesis_id}/outcome-bindings
POST /api/v1/outcome-bindings/{binding_id}/approve
GET  /api/v1/theses/{thesis_id}/outcome-binding
GET  /api/v1/theses/{thesis_id}/researchability
```

`CreateThesisRequest` gains optional `research_protocol_required: bool = False`; legacy clients remain unchanged. All command responses include version id, state, reviewer and created time. Gate response exposes only `status`, ordered `reason_codes`, effective binding summary and `next_action`; it never fabricates a conclusion or an unstored mechanism template.

- [ ] **Step 4: Run API and command compatibility tests**

Run: `cd backend && pytest tests/test_research_protocol_api.py tests/test_event_research_api.py tests/test_research_flow_e2e.py -q`

Expected: PASS; protocol API is strict and unrelated case creation continues to work.

- [ ] **Step 5: Commit the API slice**

```bash
git add backend/app/schemas/v1/research_protocol.py backend/app/api/v1/research_protocol.py backend/app/api/v1/commands/research_protocol.py backend/app/api/v1/router.py backend/app/api/v1/commands/cases.py backend/tests/test_research_protocol_api.py
git commit -m "feat: expose metric outcome research protocol"
```

### Task 5: Protect formal assessment for opted-in research protocols

**Files:**
- Modify: `backend/app/ai/assessment_gen.py`
- Modify: `backend/app/api/v1/commands/engine.py`
- Modify: `backend/tests/test_ai_engine.py`
- Modify: `backend/tests/test_research_flow_e2e.py`

- [ ] **Step 1: Write the failing protection tests**

```python
def test_assessment_refuses_protocol_thesis_that_fails_researchability(session, protocol_thesis):
    with pytest.raises(ValidationError, match="researchability gate blocked"):
        AssessmentGenerator(client).generate(protocol_thesis.id, cutoff, session)

def test_legacy_thesis_assessment_still_runs(session, legacy_thesis):
    assessment = AssessmentGenerator(client).generate(legacy_thesis.id, cutoff, session)
    assert assessment.displayed_as_provisional is True
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `cd backend && pytest tests/test_ai_engine.py -q`

Expected: FAIL because assessment currently ignores the protocol gate.

- [ ] **Step 3: Implement the narrow enforcement point**

Before snapshot/assessment generation, call `ResearchabilityGate.check(thesis_id)`. If the thesis is protocol-required and status is `blocked`, raise `ValidationError(f"researchability gate blocked: {', '.join(result.reason_codes)}")`; command routing maps it to the established `422 validation_failed` envelope. Leave legacy thesis assessment unchanged. Do not add a bypass flag.

- [ ] **Step 4: Run focused end-to-end verification**

Run: `cd backend && pytest tests/test_ai_engine.py tests/test_research_flow_e2e.py tests/test_research_protocol.py tests/test_research_protocol_api.py -q`

Expected: PASS; a new protocol thesis cannot receive a formal assessment until future template/rule work completes, while existing research remains compatible.

- [ ] **Step 5: Commit the enforcement slice**

```bash
git add backend/app/ai/assessment_gen.py backend/app/api/v1/commands/engine.py backend/tests/test_ai_engine.py backend/tests/test_research_flow_e2e.py
git commit -m "feat: enforce researchability before assessment"
```

## Acceptance checklist

- Metric definitions and outcome bindings are immutable versioned records, not editable labels.
- A binding always identifies one outcome-eligible metric, scope, direction, traceable baseline and ordered horizon.
- Existing theses are not silently reclassified; new protocol theses opt in explicitly.
- A protocol thesis cannot become formally assessed when its definition is incomplete.
- The UI/API can state exactly what is missing, including the future mechanism/rule requirements, instead of saying “AI is thinking” or fabricating readiness.
- No endpoint produces a causal conclusion, investment instruction or auto-approved outcome binding.

## Deferred plans

1. `MechanismTemplate + VerificationRule`: supplies the template, required-node, alternative-explanation and counter-hypothesis objects that make a protocol thesis eligible to become `ready`.
2. `Mechanism review workbench + private gold replay`: provides the human template/outcome UI and licensed Chinese report backtesting.
