# CATL Economic Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a frozen, historically replayable power-battery industry and CATL economic model that explains how demand, effective capacity, pricing, cost, segments, capital intensity, and working capital produce revenue, operating profit, and free cash flow before any price or valuation conclusion is allowed.

**Architecture:** Extend the independent `app.underwriting` bounded context created in Wave 1. Source governance freezes an authorized real-data manifest at a fixed historical cutoff; typed metric definitions and observations feed versioned formal mechanism packs; industry and earnings engines compile only formal mechanisms into append-only research versions. The output is a source-traceable economic-model snapshot and Answerability result, not a recommendation, target price, position, or live-data claim.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL append-only triggers, SQLite unit tests, pytest, JSON fixtures, Decimal arithmetic, existing `underwriting.v1` API/OpenAPI generation.

---

## 1. Scope and frozen baseline

This plan implements Wave 2 of `2026-08-21-dynamic-investment-underwriting-program.md` only.

The baseline is fixed at `2025-05-15T23:59:59+08:00`. It may use only information whose `first_available_at` is at or before that cutoff. It intentionally includes CATL's 2024 annual report and the IEA Global EV Outlook 2025, while excluding CATL's 2025 half-year report and every later disclosure.

Required primary or high-authority baseline sources:

1. CATL 2024 Annual Report, CNINFO PDF, published 2025-03-15: `https://static.cninfo.com.cn/finalpage/2025-03-15/1222806982.PDF`.
2. CATL official 2024 Annual Report publication page, dated 2025-03-14: `https://www.catl.com/en/news/6392.html`.
3. IEA Global EV Outlook 2025, published 2025-05-14 under CC BY 4.0: `https://www.iea.org/reports/global-ev-outlook-2025`.
4. China Automotive Power Battery Industry Innovation Alliance 2024 domestic installation statistic, published 2025-01-13 and preserved through the Changzhou government page: `https://kjj.changzhou.gov.cn/html/kjj/2025/MHFDIKKN_0120/47286.html`.

The fixture must preserve source identity, locator, publication time, first-available time, retrieval time, content hash, authority role, display/export policy, and the exact metric observations derived from each source. It must not commit copyrighted full documents.

Known annual-report anchors, stored in CNY rather than the report's CNY-thousand presentation unit:

```python
CATL_2024_ANCHORS = {
    "company.revenue": Decimal("362012554000"),
    "company.net_profit_parent": Decimal("50744682000"),
    "company.operating_cash_flow": Decimal("96990345000"),
    "company.cash_capex": Decimal("31179943000"),
    "segment.power_battery.revenue": Decimal("253041337000"),
    "segment.power_battery.cost": Decimal("192461282000"),
    "segment.energy_storage.revenue": Decimal("57290460000"),
    "segment.energy_storage.cost": Decimal("41914003000"),
    "segment.materials_recycling.revenue": Decimal("28699935000"),
    "segment.materials_recycling.cost": Decimal("25682916000"),
    "segment.mineral_resources.revenue": Decimal("5493003000"),
    "segment.mineral_resources.cost": Decimal("5024611000"),
    "segment.other.revenue": Decimal("17487818000"),
    "segment.other.cost": Decimal("8436147000"),
    "company.battery_sales_volume_gwh": Decimal("475"),
    "segment.power_battery.volume_gwh": Decimal("381"),
    "segment.energy_storage.volume_gwh": Decimal("93"),
}
```

Revenue reconciliation tolerance is CNY 1,000 because the source table is published in CNY thousands and its displayed segment rows differ from the displayed total by CNY 1,000 after conversion.

Explicitly out of scope:

- current or historical share price ingestion;
- PE, PB, DCF, SOTP, target price, expected return, or position sizing;
- buy, sell, add, trim, or stop-loss recommendations;
- automatic web refresh or silent AI completion of missing observations;
- frontend workbench pages;
- Alphabet reuse validation.

## 2. File map

New domain modules:

- `backend/app/underwriting/domain/metrics.py`: metric definitions, units, periods, observations, and reconciliation contracts.
- `backend/app/underwriting/domain/mechanisms.py`: mechanism lifecycle, financial mapping, alternatives, and falsifiers.
- `backend/app/underwriting/domain/industry.py`: industry inputs, state, scenario, and company exposure values.
- `backend/app/underwriting/domain/earnings.py`: segment economics, four-core views, financial bridge, and balance results.

New persistence and service modules:

- `backend/app/underwriting/persistence/research_models.py`: append-only Wave 2 ORM rows.
- `backend/app/underwriting/persistence/research_repository.py`: successor-chain and effective-at-cutoff queries for Wave 2 rows.
- `backend/app/underwriting/services/source_policy.py`: authority, licensing, retention, and reproducibility gates.
- `backend/app/underwriting/services/source_freeze.py`: manifest validation, canonical hash, and observation freeze.
- `backend/app/underwriting/services/mechanism_compiler.py`: lifecycle transitions and formal-mechanism compilation.
- `backend/app/underwriting/services/industry_state.py`: demand, effective capacity, utilization, price/cost, and scenario compilation.
- `backend/app/underwriting/services/earnings_engine.py`: segment and company financial bridge.
- `backend/app/underwriting/services/catl_baseline.py`: one transaction that imports and publishes the frozen CATL baseline.

New fixture and tests:

- `backend/app/underwriting/fixtures/catl_baseline/manifest.json`
- `backend/app/underwriting/fixtures/catl_baseline/observations.json`
- `backend/app/underwriting/fixtures/catl_baseline/mechanisms.json`
- `backend/app/underwriting/fixtures/catl_baseline/README.md`
- `backend/tests/underwriting/test_metric_contracts.py`
- `backend/tests/underwriting/test_source_freeze.py`
- `backend/tests/underwriting/test_research_persistence.py`
- `backend/tests/underwriting/test_mechanism_compiler.py`
- `backend/tests/underwriting/test_catl_industry_state.py`
- `backend/tests/underwriting/test_catl_earnings_engine.py`
- `backend/tests/underwriting/test_catl_source_traceability.py`
- `backend/tests/underwriting/test_catl_baseline.py`

Modified integration files:

- `backend/app/underwriting/domain/__init__.py`
- `backend/app/underwriting/persistence/__init__.py`
- `backend/app/models/ledger.py`
- `backend/app/underwriting/api/schemas.py`
- `backend/app/underwriting/api/router.py`
- `backend/alembic/versions/0061_underwriting_economic_model.py`
- `backend/tests/test_sqlite_migration_bootstrap.py`
- `frontend/openapi.json`
- `frontend/src/contracts/v1.ts`

### Task 1: Freeze metric vocabulary and reconciliation semantics

**Files:**
- Create: `backend/app/underwriting/domain/metrics.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Test: `backend/tests/underwriting/test_metric_contracts.py`

- [ ] **Step 1: Write failing domain-contract tests**

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.metrics import (
    AggregationRule,
    MetricDefinition,
    MetricObservation,
    PeriodSemantics,
    SourceRole,
    reconcile,
)

T = datetime(2025, 3, 15, tzinfo=UTC)


def test_metric_definition_preserves_unit_period_and_source_role() -> None:
    definition = MetricDefinition(
        key="segment.power_battery.revenue",
        version=1,
        label="Power battery revenue",
        unit="CNY",
        period_semantics=PeriodSemantics.FLOW,
        source_role=SourceRole.REPORTED,
        aggregation=AggregationRule.SUM,
        reconciliation_tolerance=Decimal("1000"),
    )
    assert definition.key == "segment.power_battery.revenue"
    assert definition.unit == "CNY"
    assert definition.period_semantics is PeriodSemantics.FLOW


def test_observation_rejects_future_availability_and_unit_mismatch() -> None:
    definition = MetricDefinition(
        "company.revenue", 1, "Revenue", "CNY", PeriodSemantics.FLOW,
        SourceRole.REPORTED, AggregationRule.SUM, Decimal("1000")
    )
    with pytest.raises(ValidationError, match="available_at must not exceed cutoff"):
        MetricObservation.create(
            definition=definition,
            value=Decimal("1"),
            observed_start=datetime(2024, 1, 1, tzinfo=UTC),
            observed_end=datetime(2024, 12, 31, tzinfo=UTC),
            effective_at=datetime(2024, 12, 31, tzinfo=UTC),
            available_at=datetime(2025, 5, 16, tzinfo=UTC),
            cutoff=datetime(2025, 5, 15, tzinfo=UTC),
            source_id="catl-2024-ar",
            source_locator="p18",
            unit="CNY",
            dimensions={"segment": "company"},
        )


def test_reconciliation_reports_delta_instead_of_hiding_it() -> None:
    result = reconcile(
        total=Decimal("362012554000"),
        parts=(Decimal("253041337000"), Decimal("109971216000")),
        tolerance=Decimal("1000"),
    )
    assert result.balanced is True
    assert result.delta == Decimal("1000")
```

- [ ] **Step 2: Run tests and confirm the import failure**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_metric_contracts.py
```

Expected: collection fails with `ModuleNotFoundError: app.underwriting.domain.metrics`.

- [ ] **Step 3: Implement immutable metric contracts**

Implement these public types and functions:

```python
class PeriodSemantics(StrEnum):
    POINT_IN_TIME = "point_in_time"
    FLOW = "flow"
    PERIOD_AVERAGE = "period_average"


class SourceRole(StrEnum):
    REPORTED = "reported"
    OFFICIAL_INDUSTRY = "official_industry"
    DERIVED = "derived"
    ASSUMPTION = "assumption"


class AggregationRule(StrEnum):
    SUM = "sum"
    WEIGHTED_AVERAGE = "weighted_average"
    LAST = "last"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    version: int
    label: str
    unit: str
    period_semantics: PeriodSemantics
    source_role: SourceRole
    aggregation: AggregationRule
    reconciliation_tolerance: Decimal


@dataclass(frozen=True, slots=True)
class MetricObservation:
    definition_key: str
    definition_version: int
    value: Decimal
    unit: str
    observed_start: datetime
    observed_end: datetime
    effective_at: datetime
    available_at: datetime
    source_id: str
    source_locator: str
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    total: Decimal
    parts_total: Decimal
    delta: Decimal
    tolerance: Decimal
    balanced: bool


def reconcile(total: Decimal, parts: tuple[Decimal, ...], tolerance: Decimal) -> ReconciliationResult:
    parts_total = sum(parts, start=Decimal("0"))
    delta = total - parts_total
    return ReconciliationResult(total, parts_total, delta, tolerance, abs(delta) <= tolerance)
```

`MetricObservation.create()` must normalize timezone-aware timestamps to UTC, reject empty identity/locator fields, reject negative definition versions, reject an observation unit that differs from its definition, reject `observed_start > observed_end`, and reject `available_at > cutoff`.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest -q tests/underwriting/test_metric_contracts.py`

Expected: all tests pass with no skip.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/domain backend/tests/underwriting/test_metric_contracts.py
git commit -m "feat: define underwriting economic metrics"
```

### Task 2: Enforce frozen-source policy and reproducibility

**Files:**
- Create: `backend/app/underwriting/services/source_policy.py`
- Create: `backend/app/underwriting/services/source_freeze.py`
- Test: `backend/tests/underwriting/test_source_freeze.py`

- [ ] **Step 1: Write failing source-policy tests**

```python
def test_freeze_rejects_future_forbidden_or_unreproducible_sources() -> None:
    manifest = manifest_fixture()
    manifest["sources"][0]["first_available_at"] = "2025-05-16T00:00:00+08:00"
    with pytest.raises(ValidationError, match="source is unavailable at cutoff"):
        freeze_manifest(manifest, cutoff=CUTOFF)


def test_freeze_hash_is_stable_under_source_ordering() -> None:
    forward = freeze_manifest(manifest_fixture(), cutoff=CUTOFF)
    reverse_manifest = manifest_fixture()
    reverse_manifest["sources"].reverse()
    reverse = freeze_manifest(reverse_manifest, cutoff=CUTOFF)
    assert forward.manifest_hash == reverse.manifest_hash


def test_unresolved_source_conflict_fails_closed() -> None:
    observations = observation_fixture_with_conflict()
    with pytest.raises(ValidationError, match="unresolved source conflict"):
        freeze_observations(observations, source_manifest=manifest_fixture())
```

- [ ] **Step 2: Confirm the missing-module red state**

Run: `cd backend && pytest -q tests/underwriting/test_source_freeze.py`

Expected: collection fails because `source_policy` and `source_freeze` do not exist.

- [ ] **Step 3: Implement source policy types**

```python
class AuthorizationState(StrEnum):
    AUTHORIZED = "authorized"
    REFERENCE_ONLY = "reference_only"
    FORBIDDEN = "forbidden"


class DisplayPolicy(StrEnum):
    PUBLIC_EXCERPT = "public_excerpt"
    DERIVED_ONLY = "derived_only"
    METADATA_ONLY = "metadata_only"


@dataclass(frozen=True, slots=True)
class SourcePolicyDecision:
    source_id: str
    authorization: AuthorizationState
    display_policy: DisplayPolicy
    reproducible: bool
    reasons: tuple[str, ...]
```

`evaluate_source_policy()` must fail a source when its authorization is `forbidden`, when a provider capability named in the manifest is unavailable, when `content_sha256` is not 64 lowercase hex characters, when locator/publication/first-available timestamps are missing, or when the required retention mode is not supported. `reference_only` sources may support a mechanism but may not be the sole source of a reported company observation.

- [ ] **Step 4: Implement canonical freeze functions**

```python
@dataclass(frozen=True, slots=True)
class FrozenSourceManifest:
    cutoff: datetime
    source_ids: tuple[str, ...]
    manifest_hash: str
    sources: tuple[dict[str, object], ...]


def freeze_manifest(payload: dict[str, object], cutoff: datetime) -> FrozenSourceManifest:
    normalized = tuple(
        sorted(
            (normalize_source_record(raw, cutoff=cutoff) for raw in payload["sources"]),
            key=lambda item: item["source_id"],
        )
    )
    source_ids = tuple(item["source_id"] for item in normalized)
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("source_id must be unique")
    serialized = {
        "schema_version": payload["schema_version"],
        "cutoff": cutoff.astimezone(UTC).isoformat(),
        "sources": normalized,
    }
    return FrozenSourceManifest(
        cutoff=cutoff.astimezone(UTC),
        source_ids=source_ids,
        manifest_hash=canonical_hash(serialized),
        sources=normalized,
    )


def freeze_observations(
    payload: list[dict[str, object]],
    source_manifest: FrozenSourceManifest,
) -> tuple[MetricObservation, ...]:
    known_sources = set(source_manifest.source_ids)
    seen: set[tuple[object, ...]] = set()
    result: list[MetricObservation] = []
    for raw in payload:
        if raw["source_id"] not in known_sources:
            raise ValidationError("observation references unknown source")
        if raw.get("conflict_group") and raw.get("resolution") != "resolved":
            raise ValidationError("unresolved source conflict")
        value = observation_from_record(raw, cutoff=source_manifest.cutoff)
        identity = (
            value.definition_key,
            value.definition_version,
            value.observed_end,
            value.dimensions,
            value.source_id,
        )
        if identity in seen:
            raise ValidationError("observation identity must be unique")
        seen.add(identity)
        result.append(value)
    return tuple(sorted(result, key=observation_sort_key))
```

`normalize_source_record`, `observation_from_record`, and `observation_sort_key` are private helpers in `source_freeze.py`; their behavior is fully specified by the validation rules in Steps 3 and 4 and by the failing tests in Step 1.

- [ ] **Step 5: Run source tests and the existing future-leakage tests**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_source_freeze.py tests/underwriting/test_historical_replay.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/services/source_policy.py backend/app/underwriting/services/source_freeze.py backend/tests/underwriting/test_source_freeze.py
git commit -m "feat: freeze authorized underwriting sources"
```

### Task 3: Add append-only Wave 2 research persistence

**Files:**
- Create: `backend/app/underwriting/persistence/research_models.py`
- Modify: `backend/app/underwriting/persistence/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Create: `backend/alembic/versions/0061_underwriting_economic_model.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py`
- Test: `backend/tests/underwriting/test_research_persistence.py`

- [ ] **Step 1: Write failing metadata and migration tests**

Assert exact table and constraint names:

```python
WAVE2_TABLES = {
    "uw_source_manifest_versions",
    "uw_metric_definition_versions",
    "uw_metric_observations",
    "uw_mechanism_pack_versions",
    "uw_industry_state_versions",
    "uw_industry_scenario_versions",
    "uw_company_exposure_versions",
    "uw_earnings_engine_versions",
    "uw_forecast_input_versions",
    "uw_falsifier_versions",
}


def test_wave2_tables_are_registered_and_append_only() -> None:
    assert WAVE2_TABLES <= set(Base.metadata.tables)
    assert WAVE2_TABLES <= IMMUTABLE_TABLE_NAMES
```

Also assert foreign keys back to `uw_research_objects`, `uw_historical_bases`, and source/definition/mechanism parents; unique successor identities; JSON columns; content hashes; cutoff-linked basis IDs; and indexes for effective lookup.

- [ ] **Step 2: Verify tests fail before the models exist**

Run: `cd backend && pytest -q tests/underwriting/test_research_persistence.py`

Expected: import or table-name assertions fail.

- [ ] **Step 3: Implement ORM rows and immutable registration**

Every version table must have `id`, domain identity, `version`, `basis_id`, `content_hash`, nullable `supersedes_id`, and `created_at`. `uw_metric_observations` additionally stores both observed period and available time. `uw_source_manifest_versions` stores the normalized manifest JSON and manifest hash. JSON annotations must be precise (`Mapped[list[str]]`, `Mapped[dict[str, object]]`) rather than `Any`.

Use these exact unique constraints:

```text
uq_uw_source_manifest_version         (manifest_key, version)
uq_uw_metric_definition_version       (metric_key, version)
uq_uw_metric_observation_identity     (basis_id, metric_key, definition_version, observed_end, dimension_hash, source_id)
uq_uw_mechanism_pack_version          (mechanism_key, version)
uq_uw_industry_state_version          (object_id, basis_id, version)
uq_uw_industry_scenario_version       (industry_state_id, scenario_key, version)
uq_uw_company_exposure_version        (company_id, industry_state_id, exposure_key, version)
uq_uw_earnings_engine_version         (company_id, basis_id, version)
uq_uw_forecast_input_version          (company_id, basis_id, input_key, version)
uq_uw_falsifier_version               (mechanism_id, falsifier_key, version)
```

- [ ] **Step 4: Implement Alembic revision 0061**

Set `down_revision = "0060"`. Create all ten tables with the same named constraints and indexes as the ORM. On PostgreSQL, install `reject_mutable_ledger` triggers for all ten tables. On downgrade, drop the ten triggers and tables in reverse foreign-key order without dropping the shared trigger function.

- [ ] **Step 5: Run migration tests on a fresh SQLite database**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_research_persistence.py tests/test_sqlite_migration_bootstrap.py
```

Expected: all tests pass; fresh head is `0061`; downgrade to `0060` removes only the ten Wave 2 tables.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/persistence backend/app/models/ledger.py backend/alembic/versions/0061_underwriting_economic_model.py backend/tests/underwriting/test_research_persistence.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: persist underwriting economic research"
```

### Task 4: Enforce Wave 2 successor chains and cutoff queries

**Files:**
- Create: `backend/app/underwriting/persistence/research_repository.py`
- Test: `backend/tests/underwriting/test_research_repository.py`

- [ ] **Step 1: Write failing repository tests**

Cover first version, valid successor, missing expected parent, stale parent, cross-family parent, duplicate observation identity, effective version at a basis cutoff, and stable sorting. Use the existing `StaleParentError` and the Wave 1 repository pattern.

```python
def test_mechanism_successor_requires_current_parent(session) -> None:
    repository = UnderwritingResearchRepository(session)
    first = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company_id,
        basis_id=basis_id,
        status="candidate",
        payload=mechanism_payload("demand_to_shipments"),
        content_hash="a" * 64,
        expected_parent_id=None,
        created_at=NOW,
    )
    second = repository.append_mechanism(
        mechanism_key="demand_to_shipments",
        object_id=company_id,
        basis_id=basis_id,
        status="adapted",
        payload=mechanism_payload("demand_to_shipments"),
        content_hash="b" * 64,
        expected_parent_id=first.id,
        created_at=NOW,
    )
    with pytest.raises(StaleParentError):
        repository.append_mechanism(
            mechanism_key="demand_to_shipments",
            object_id=company_id,
            basis_id=basis_id,
            status="adapted",
            payload=mechanism_payload("demand_to_shipments"),
            content_hash="c" * 64,
            expected_parent_id=first.id,
            created_at=NOW,
        )
    assert second.version == 2
```

- [ ] **Step 2: Confirm repository import failure**

Run: `cd backend && pytest -q tests/underwriting/test_research_repository.py`

Expected: collection fails because the repository module does not exist.

- [ ] **Step 3: Implement add/append/effective query methods**

The repository must expose the write methods `add_source_manifest`, `append_metric_definition`, `add_metric_observation`, `append_mechanism`, `append_industry_state`, `append_industry_scenario`, `append_company_exposure`, `append_earnings_engine`, `append_forecast_input`, and `append_falsifier`. It must expose the read methods `effective_metric_definitions_at(basis_id)`, `effective_observations_at(object_id, basis_id)`, `formal_mechanisms_at(object_id, basis_id)`, `latest_industry_state(object_id, basis_id)`, and `latest_earnings_engine(company_id, basis_id)`.

All writes use `session.add()` plus `flush()` only. No repository method may commit, update, delete, or mutate a returned JSON payload.

- [ ] **Step 4: Run repository and immutability tests**

Run: `cd backend && pytest -q tests/underwriting/test_research_repository.py tests/underwriting/test_kernel_persistence.py`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/persistence/research_repository.py backend/tests/underwriting/test_research_repository.py
git commit -m "feat: version underwriting research artifacts"
```

### Task 5: Build the governed MechanismPack compiler

**Files:**
- Create: `backend/app/underwriting/domain/mechanisms.py`
- Create: `backend/app/underwriting/services/mechanism_compiler.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Test: `backend/tests/underwriting/test_mechanism_compiler.py`

- [ ] **Step 1: Write failing lifecycle and formalization tests**

```python
VALID_TRANSITIONS = (
    ("candidate", "adapted"),
    ("adapted", "calibrated"),
    ("calibrated", "human_confirmed"),
    ("human_confirmed", "formal"),
    ("formal", "challenged"),
    ("challenged", "retired_or_replaced"),
)


def test_only_formal_mechanisms_can_compile() -> None:
    with pytest.raises(ValidationError, match="mechanism must be formal"):
        compile_mechanisms((candidate_mechanism(),))


def test_formal_mechanism_requires_mapping_alternative_and_falsifier() -> None:
    value = formal_mechanism(financial_mappings=(), alternatives=(), falsifiers=())
    with pytest.raises(ValidationError, match="formal mechanism is incomplete"):
        validate_formal_mechanism(value)
```

- [ ] **Step 2: Confirm tests fail before implementation**

Run: `cd backend && pytest -q tests/underwriting/test_mechanism_compiler.py`

Expected: missing-module failure.

- [ ] **Step 3: Implement lifecycle and formal mechanism contracts**

```python
class MechanismStatus(StrEnum):
    CANDIDATE = "candidate"
    ADAPTED = "adapted"
    CALIBRATED = "calibrated"
    HUMAN_CONFIRMED = "human_confirmed"
    FORMAL = "formal"
    CHALLENGED = "challenged"
    RETIRED_OR_REPLACED = "retired_or_replaced"


@dataclass(frozen=True, slots=True)
class FinancialMapping:
    driver_key: str
    target_metric_key: str
    direction: Literal["positive", "negative", "nonlinear"]
    lag_periods: int
    magnitude_low: Decimal
    magnitude_high: Decimal


@dataclass(frozen=True, slots=True)
class Falsifier:
    key: str
    metric_key: str
    operator: Literal["lt", "lte", "gt", "gte", "outside"]
    threshold_low: Decimal | None
    threshold_high: Decimal | None
    evaluation_periods: int
    consequence: str


@dataclass(frozen=True, slots=True)
class MechanismPack:
    key: str
    version: int
    status: MechanismStatus
    scope_object_id: UUID
    driver_keys: tuple[str, ...]
    formula: str
    applicability: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    financial_mappings: tuple[FinancialMapping, ...]
    alternative_explanations: tuple[str, ...]
    falsifiers: tuple[Falsifier, ...]
    source_ids: tuple[str, ...]
```

The compiler must accept only consecutive lifecycle transitions, require a human confirmation identity before `formal`, require all referenced metric definitions and source IDs to exist at the basis cutoff, and return a deterministic content hash plus dependency IDs.

- [ ] **Step 4: Add CATL mechanism-shape tests**

Require five mechanisms plus one counter-model:

```text
ev_storage_demand_to_shipments
effective_capacity_to_utilization_and_price
material_cost_pass_through_to_unit_margin
certification_overseas_footprint_to_obtainable_share
capex_working_capital_to_free_cash_flow
counter_model_customer_bargaining_and_oversupply
```

Each formal mechanism must have direction, lag, magnitude range, applicability, invalidation conditions, at least one financial mapping, at least one alternative explanation, at least one falsifier, and at least one frozen source.

- [ ] **Step 5: Run mechanism tests**

Run: `cd backend && pytest -q tests/underwriting/test_mechanism_compiler.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/domain/mechanisms.py backend/app/underwriting/services/mechanism_compiler.py backend/app/underwriting/domain/__init__.py backend/tests/underwriting/test_mechanism_compiler.py
git commit -m "feat: compile governed economic mechanisms"
```

### Task 6: Build the power-battery IndustryState engine

**Files:**
- Create: `backend/app/underwriting/domain/industry.py`
- Create: `backend/app/underwriting/services/industry_state.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Test: `backend/tests/underwriting/test_catl_industry_state.py`

- [ ] **Step 1: Write failing industry-mechanics tests**

```python
def test_nominal_capacity_is_never_used_as_effective_capacity() -> None:
    result = compile_industry_state(
        inputs=IndustryInputs(
            ev_sales=Decimal("30"),
            average_battery_kwh=Decimal("55"),
            storage_demand_gwh=Decimal("300"),
            nominal_capacity_gwh=Decimal("3000"),
            commissioned_share=Decimal("0.80"),
            certified_share=Decimal("0.75"),
            yield_rate=Decimal("0.95"),
            shipments_gwh=Decimal("1500"),
            production_gwh=Decimal("1600"),
        ),
        mechanisms=formal_industry_mechanisms(),
    )
    assert result.effective_capacity_gwh == Decimal("1710.0000")
    assert result.utilization == Decimal("1500") / Decimal("1710")
    assert result.effective_capacity_gwh != Decimal("3000")


def test_missing_certification_baseline_fails_closed() -> None:
    with pytest.raises(AnswerabilityBlocked, match="missing_key_baseline"):
        compile_industry_state(inputs=inputs_without_certification(), mechanisms=formal_industry_mechanisms())
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest -q tests/underwriting/test_catl_industry_state.py`

Expected: missing-module failure.

- [ ] **Step 3: Implement industry state values and formulas**

```python
battery_demand_gwh = (
    ev_sales_millions * average_battery_kwh
    + storage_demand_gwh
)
effective_capacity_gwh = (
    nominal_capacity_gwh
    * commissioned_share
    * certified_share
    * yield_rate
)
utilization = shipments_gwh / effective_capacity_gwh
inventory_change_gwh = production_gwh - shipments_gwh
unit_margin_cny_per_kwh = cell_asp_cny_per_kwh - unit_cash_cost_cny_per_kwh
```

Quantize all ratios and monetary unit economics explicitly. Reject negative physical values, any share outside `[0, 1]`, zero effective capacity, and any missing input referenced by a formal mechanism. Store nominal capacity and effective capacity as separate fields and metric keys.

- [ ] **Step 4: Implement base, upside, and downside scenarios**

Each scenario must override only declared driver keys and retain the industry-state parent ID. Scenario outputs include demand, effective capacity, utilization, inventory change, price range, unit-cost range, industry profit-pool range, and the falsifiers that would retire the scenario. Do not attach probabilities in Wave 2.

- [ ] **Step 5: Run industry tests**

Run: `cd backend && pytest -q tests/underwriting/test_catl_industry_state.py tests/underwriting/test_answerability.py`

Expected: all tests pass and missing baselines map to `missing_key_baseline` or `mechanism_unidentified`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/domain/industry.py backend/app/underwriting/services/industry_state.py backend/app/underwriting/domain/__init__.py backend/tests/underwriting/test_catl_industry_state.py
git commit -m "feat: model power battery industry state"
```

### Task 7: Build CATL's segment EarningsEngine

**Files:**
- Create: `backend/app/underwriting/domain/earnings.py`
- Create: `backend/app/underwriting/services/earnings_engine.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Test: `backend/tests/underwriting/test_catl_earnings_engine.py`

- [ ] **Step 1: Write failing segment and company bridge tests**

```python
def test_segment_volume_price_cost_bridge() -> None:
    result = build_segment(
        SegmentInputs(
            key="power_battery",
            volume_gwh=Decimal("381"),
            asp_cny_per_kwh=Decimal("664.1504908136483"),
            unit_cash_cost_cny_per_kwh=Decimal("505.1477217847769"),
            operating_expense=Decimal("0"),
            depreciation=Decimal("0"),
            cash_capex=Decimal("0"),
            working_capital_change=Decimal("0"),
            cash_tax=Decimal("0"),
        )
    )
    assert result.revenue.quantize(Decimal("1")) == Decimal("253041337000")
    assert result.gross_profit.quantize(Decimal("1")) == Decimal("60580055000")


def test_company_bridge_refuses_unbalanced_revenue() -> None:
    with pytest.raises(ValidationError, match="revenue does not reconcile"):
        build_company_engine(company_total=Decimal("362012554000"), segments=broken_segments())
```

- [ ] **Step 2: Verify tests fail before the engine exists**

Run: `cd backend && pytest -q tests/underwriting/test_catl_earnings_engine.py`

Expected: missing-module failure.

- [ ] **Step 3: Implement segment financial formulas**

Use Decimal throughout:

```python
revenue = volume_gwh * Decimal("1000000") * asp_cny_per_kwh
gross_profit = volume_gwh * Decimal("1000000") * (
    asp_cny_per_kwh - unit_cash_cost_cny_per_kwh
)
operating_profit = gross_profit - operating_expense
nopat = operating_profit - cash_tax
free_cash_flow = (
    nopat
    + depreciation
    - cash_capex
    - working_capital_change
)
```

Reported segment rows may use directly reported revenue and cost. Derived ASP or unit cost must be marked `derived`, keep numerator/denominator metric IDs, and never be relabeled as reported.

- [ ] **Step 4: Implement the four independent core views**

```python
@dataclass(frozen=True, slots=True)
class CoreContribution:
    segment_key: str
    amount: Decimal
    share: Decimal | None
    basis: str


@dataclass(frozen=True, slots=True)
class FourCoreView:
    revenue_core: tuple[CoreContribution, ...]
    profit_core: tuple[CoreContribution, ...]
    cash_core: tuple[CoreContribution, ...]
    value_core: tuple[CoreContribution, ...]
```

`value_core` in Wave 2 means normalized long-run cash-earning-power contribution under an explicit industry scenario. It is not a fair-value amount and must not contain price, multiple, discount rate, or target-price fields. Tests must prove that the largest revenue segment is not automatically selected as the largest cash or value core.

- [ ] **Step 5: Add company reconciliation tests**

The engine must expose and enforce:

```text
sum(segment revenue) -> company revenue within CNY 1,000
sum(segment cost) -> company cost within CNY 1,000
reported OCF - cash capex -> reported FCF proxy
operating profit -> NOPAT -> modeled FCF bridge
volume x ASP -> segment revenue when both inputs are available
volume x unit cost -> segment cost when both inputs are available
```

If operating expenses, depreciation, working capital, or tax cannot be allocated to a segment from authorized evidence, retain an explicit unallocated-company row. Do not spread them proportionally without a formal allocation mechanism.

- [ ] **Step 6: Run earnings tests**

Run: `cd backend && pytest -q tests/underwriting/test_catl_earnings_engine.py`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/underwriting/domain/earnings.py backend/app/underwriting/services/earnings_engine.py backend/app/underwriting/domain/__init__.py backend/tests/underwriting/test_catl_earnings_engine.py
git commit -m "feat: build CATL segment earnings engine"
```

### Task 8: Create the real frozen CATL baseline fixture

**Files:**
- Create: `backend/app/underwriting/fixtures/catl_baseline/manifest.json`
- Create: `backend/app/underwriting/fixtures/catl_baseline/observations.json`
- Create: `backend/app/underwriting/fixtures/catl_baseline/mechanisms.json`
- Create: `backend/app/underwriting/fixtures/catl_baseline/README.md`
- Test: `backend/tests/underwriting/test_catl_source_traceability.py`

- [ ] **Step 1: Write failing fixture integrity tests**

```python
def test_catl_fixture_uses_fixed_cutoff_and_real_source_anchors() -> None:
    frozen = load_catl_fixture()
    assert frozen.cutoff.isoformat() == "2025-05-15T15:59:59+00:00"
    assert frozen.observation("company.revenue").value == Decimal("362012554000")
    assert frozen.observation("company.operating_cash_flow").value == Decimal("96990345000")
    assert frozen.observation("segment.power_battery.volume_gwh").value == Decimal("381")
    assert frozen.observation("segment.energy_storage.volume_gwh").value == Decimal("93")


def test_every_observation_traces_to_source_and_locator() -> None:
    frozen = load_catl_fixture()
    for observation in frozen.observations:
        assert frozen.source(observation.source_id) is not None
        assert observation.source_locator
        assert observation.available_at <= frozen.cutoff
```

- [ ] **Step 2: Confirm fixture tests fail**

Run: `cd backend && pytest -q tests/underwriting/test_catl_source_traceability.py`

Expected: fixture files are missing.

- [ ] **Step 3: Create `manifest.json`**

Use schema version `underwriting.source-manifest.v1`. Include the four required sources from section 1. Each entry must contain:

```json
{
  "source_id": "catl-2024-annual-report-cninfo",
  "title": "宁德时代新能源科技股份有限公司2024年年度报告",
  "publisher": "宁德时代新能源科技股份有限公司",
  "locator": "https://static.cninfo.com.cn/finalpage/2025-03-15/1222806982.PDF",
  "published_at": "2025-03-15T00:00:00+08:00",
  "first_available_at": "2025-03-15T00:00:00+08:00",
  "retrieved_at": "2026-08-21T00:00:00+08:00",
  "content_sha256": "b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad",
  "authority": "issuer_filing",
  "authorization": "authorized",
  "display_policy": "derived_only",
  "provider_capability": "public_http",
  "retention": "hash_locator_and_derived_observations"
}
```

The implementation worker must download that exact PDF once and verify that its SHA-256 is `b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad`. Tests reject empty hashes and hashes that differ from the retrieved bytes. Full source documents remain outside git.

- [ ] **Step 4: Create normalized `observations.json`**

Use schema version `underwriting.observations.v1`. Store all annual-report anchors from section 1, the published 2024 EV/battery/storage demand observations, nominal and effective-capacity components when available, and explicit Unknown observations when a required baseline is not disclosed. Each entry includes metric definition version, value, unit, observed period, effective time, available time, source ID, page/section locator, dimensions, and derivation parents.

- [ ] **Step 5: Create `mechanisms.json`**

Use schema version `underwriting.mechanisms.v1`. Include the six exact mechanism keys from Task 5. Initial statuses may be `candidate`, but the fixture must contain the full mapping, alternative, falsifier, source references, and the exact review record that permits sequential promotion to `formal` during the import test.

- [ ] **Step 6: Document fixture provenance**

`README.md` must state the fixed cutoff, excluded later disclosures, source rights, reported-versus-derived distinctions, rounding tolerance, unresolved gaps, and that the fixture is a historical research basis rather than current CATL research.

- [ ] **Step 7: Run traceability and future-leakage tests**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_catl_source_traceability.py tests/underwriting/test_historical_replay.py
```

Expected: all tests pass; no fixture observation has `available_at` after the cutoff.

- [ ] **Step 8: Commit**

```bash
git add backend/app/underwriting/fixtures/catl_baseline backend/tests/underwriting/test_catl_source_traceability.py
git commit -m "data: freeze CATL 2024 economic baseline"
```

### Task 9: Orchestrate and publish the CATL baseline atomically

**Files:**
- Create: `backend/app/underwriting/services/catl_baseline.py`
- Modify: `backend/app/underwriting/services/__init__.py`
- Test: `backend/tests/underwriting/test_catl_baseline.py`

- [ ] **Step 1: Write failing import and replay tests**

```python
def test_import_catl_baseline_publishes_one_replayable_version(session) -> None:
    result = CatlBaselineService(session, now=lambda: NOW).import_fixture(FIXTURE)
    assert result.industry.external_key == "POWER_BATTERY:GLOBAL"
    assert result.company.external_key == "CN:300750:COMPANY"
    assert result.security.external_key == "SZSE:300750"
    assert result.industry_state.version == 1
    assert result.earnings_engine.version == 1
    assert len(result.formal_mechanisms) == 6
    assert result.research_version.content_hash == result.preview_hash


def test_catl_baseline_import_rolls_back_on_unbalanced_engine(session) -> None:
    fixture = fixture_with_wrong_company_revenue()
    with pytest.raises(ValidationError, match="revenue does not reconcile"):
        CatlBaselineService(session, now=lambda: NOW).import_fixture(fixture)
    assert count_wave2_rows(session) == 0
```

- [ ] **Step 2: Verify tests fail before orchestration exists**

Run: `cd backend && pytest -q tests/underwriting/test_catl_baseline.py`

Expected: missing-service failure.

- [ ] **Step 3: Implement one-transaction orchestration**

`CatlBaselineService.import_fixture()` must:

1. freeze the source manifest and observations;
2. create or resolve the Industry, Company, and Security identities and relations;
3. create the fixed HistoricalBasis from the manifest hash;
4. persist metric definition versions and observations;
5. transition and persist the six MechanismPacks;
6. compile and persist one IndustryState plus three scenarios;
7. compile and persist CATL CompanyExposure and EarningsEngine versions;
8. append Reality entries for reported/derived observations and Belief entries for formal mechanisms;
9. run the Wave 1 AnswerabilityGate;
10. publish one `catl_economic_model` research version whose parents are the manifest, state, exposure, earnings, mechanism, and Answerability version IDs.

The service must not commit. The caller owns one commit, and any failure rolls back all Wave 2 rows.

- [ ] **Step 4: Enforce answerability at publication**

Map failures exactly:

```text
missing required observation -> missing_key_baseline
unresolved conflict -> unresolved_source_conflict
no formal mechanism -> mechanism_unidentified
failed financial reconciliation -> financial_model_not_closed
forbidden/unavailable source -> source_unavailable
future available_at -> future_information_leakage
```

Wave 2 may end `answerable` or `partially_answerable`, but it must request only `observe`. It cannot request either entry action because price, expectation surface, and valuation are out of scope.

- [ ] **Step 5: Run orchestration tests twice**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_catl_baseline.py
pytest -q tests/underwriting/test_catl_baseline.py
```

Expected: both runs pass; the second independent database run produces the same preview content hash.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/services/catl_baseline.py backend/app/underwriting/services/__init__.py backend/tests/underwriting/test_catl_baseline.py
git commit -m "feat: publish CATL economic baseline"
```

### Task 10: Expose a read-only economic-model API contract

**Files:**
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Test: `backend/tests/underwriting/test_catl_economic_api.py`
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write failing API contract tests**

```python
def test_catl_economic_snapshot_returns_traceable_model(api_client, imported_catl) -> None:
    response = api_client.get(
        f"/api/underwriting/v1/objects/{imported_catl.company.id}/economic-models/{imported_catl.basis.id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "underwriting.v1"
    assert body["valuation"] is None
    assert body["eligible_action"] == "observe"
    assert body["earnings_engine"]["reconciliations"]["revenue"]["balanced"] is True
    assert all(item["source_ids"] for item in body["formal_mechanisms"])


def test_economic_snapshot_is_not_found_when_model_was_not_published(api_client, company, basis) -> None:
    response = api_client.get(
        f"/api/underwriting/v1/objects/{company.id}/economic-models/{basis.id}"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
```

- [ ] **Step 2: Verify the new route returns 404**

Run: `cd backend && pytest -q tests/underwriting/test_catl_economic_api.py`

Expected: route is not registered.

- [ ] **Step 3: Add explicit response DTOs and the GET route**

Add strict `extra="forbid"` Pydantic DTOs for source summary, metric observation, mechanism, industry state, scenario, exposure, segment economics, four-core views, reconciliation, answerability, and the top-level economic model. The top-level response must include:

```python
schema_version: Literal["underwriting.v1"]
object_id: UUID
basis_id: UUID
cutoff: datetime
research_version_id: UUID
snapshot_hash: str
sources: list[SourceSummary]
industry_state: IndustryStateResponse
scenarios: list[IndustryScenarioResponse]
company_exposure: CompanyExposureResponse
earnings_engine: EarningsEngineResponse
formal_mechanisms: list[MechanismResponse]
answerability: AnswerabilityResponse
eligible_action: Literal["observe"]
valuation: None
```

Declare `404` and `422` responses with `UnderwritingErrorEnvelope`. Construct every response explicitly; never return ORM objects directly.

- [ ] **Step 4: Regenerate OpenAPI and TypeScript contracts**

Run:

```bash
cd backend
python scripts/dump_openapi.py
cd ../frontend
npm run gen:contract
npm run typecheck
```

Expected: all commands exit zero. A second `dump_openapi.py` plus `gen:contract` produces no diff.

- [ ] **Step 5: Run API and legacy compatibility tests**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_catl_economic_api.py tests/underwriting/test_kernel_api.py
```

Expected: all tests pass; existing `underwriting.v1` and legacy `/api/v1` error envelopes remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/api backend/tests/underwriting/test_catl_economic_api.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: expose CATL economic model snapshot"
```

### Task 11: Complete the Wave 2 release gate

**Files:**
- Modify: `prototype/investment-research-prototype/README.md`
- Modify: `docs/superpowers/plans/2026-08-21-dynamic-investment-underwriting-program.md`
- Modify: `backend/tests/underwriting/test_kernel_postgres.py`

- [ ] **Step 1: Extend PostgreSQL immutability coverage**

Add all ten Wave 2 table names to the PostgreSQL trigger assertion. Parametrize an UPDATE and DELETE attempt against one source manifest, metric observation, formal mechanism, industry state, and earnings engine row. Every mutation must raise a DBAPI error matching `append-only|immutable`.

- [ ] **Step 2: Add the executable-boundary documentation**

The prototype README must state:

```text
Wave 2 adds a frozen CATL 2024 economic-model snapshot behind
/api/underwriting/v1. It explains source-governed industry state,
formal mechanisms, segment revenue/profit/cash transmission, and
answerability. It does not contain current price, fair value, expected
return, recommendation, position, or live-data claims.
```

Mark the Wave 1 gate complete and add a Wave 2 completion record to the master program without checking Wave 3 items.

- [ ] **Step 3: Run the complete Wave 2 focused gate**

Run:

```bash
cd backend
pytest -q \
  tests/underwriting/test_metric_contracts.py \
  tests/underwriting/test_source_freeze.py \
  tests/underwriting/test_research_persistence.py \
  tests/underwriting/test_research_repository.py \
  tests/underwriting/test_mechanism_compiler.py \
  tests/underwriting/test_catl_industry_state.py \
  tests/underwriting/test_catl_earnings_engine.py \
  tests/underwriting/test_catl_source_traceability.py \
  tests/underwriting/test_catl_baseline.py \
  tests/underwriting/test_catl_economic_api.py
```

Expected: all tests pass with no skip.

- [ ] **Step 4: Run PostgreSQL and full regression gates**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_kernel_postgres.py
pytest -q
cd ../frontend
npm run gen:contract
npm run typecheck
```

Expected: PostgreSQL trigger tests pass when `TEST_DATABASE_URL` is configured; without it, the module skips only under the existing `pg_only` contract and the handoff records the outstanding environment proof. All remaining backend tests pass, contract generation is clean, and typecheck exits zero.

- [ ] **Step 5: Run research-integrity demonstrations**

Record these outputs in the implementation handoff:

1. one annual-report observation tracing to source ID, page locator, definition, period, and available time;
2. the nominal-capacity versus effective-capacity calculation;
3. the 2024 segment-revenue reconciliation and its CNY 1,000 rounding delta;
4. the OCF-to-cash-capex FCF proxy bridge;
5. one counter-model and its falsifier;
6. one missing-baseline case that returns `not_answerable` or `partially_answerable` and `observe`;
7. one replay hash proving that a later source cannot enter the fixed baseline.

- [ ] **Step 6: Run final hygiene checks**

Run:

```bash
git diff --check
git status --short
rg -n "TBD|TODO|NotImplementedError|^[[:space:]]*pass$" backend/app/underwriting backend/tests/underwriting
```

Expected: diff check and placeholder scan emit nothing; the implementation worktree is clean after commit.

- [ ] **Step 7: Commit the Wave 2 release gate**

```bash
git add backend frontend/openapi.json frontend/src/contracts/v1.ts prototype/investment-research-prototype/README.md docs/superpowers/plans/2026-08-21-dynamic-investment-underwriting-program.md
git commit -m "test: gate CATL economic model"
```

## 3. Wave 2 completion record

Before starting valuation work, the implementation handoff must report:

- migration revision `0061` and final commit SHA;
- frozen cutoff and source-manifest hash;
- exact source authorization/reproducibility result;
- focused, PostgreSQL, full-backend, OpenAPI, and TypeScript test results;
- IndustryState, EarningsEngine, and research-version IDs and hashes;
- all unresolved source conflicts and Unknown observations;
- the Answerability state and blockers;
- confirmation that no price, valuation, recommendation, position, or live-data field was added;
- next scope limited to Wave 3 ExpectationSurface, ValueDistribution, price/value/thesis/uncertainty differential, and no-holding action policy.
