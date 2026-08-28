# Alphabet Research Pipeline Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the existing Alphabet preparation pipeline from reviewed evidence to a strict nine-module company-research snapshot, with business drivers, a closed five-year financial bridge, mechanism-distinct scenarios, DCF/reverse-DCF when governed market inputs exist, explicit gaps when they do not, and a durable `awaiting_judgment_review` boundary.

**Architecture:** Keep the current source worker, immutable artifact ledger, pure `CompanyResearchEngine`, and high-level workbench API. Add one deep model-builder module that converts only reviewed evidence, versioned strategy assumptions, and frozen market snapshots into typed engine input; append the resulting artifact bundle atomically; then advance the existing owned job from `business_map` to `model_bundle`. This plan intentionally stops at the judgment-review boundary. The user-facing nine-module UI and freeze/replay/export are separate follow-on plans so each increment remains independently testable.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL/SQLite, pytest, existing immutable underwriting ledgers and product market snapshots.

---

## Verified starting point

- Branch/worktree: `codex/alphabet-company-research` at `ea40ac6`.
- Focused backend gate: `170 passed, 1 skipped` across policy, persistence, initializer, API, source fixture, engine, worker, and workbench tests.
- Focused frontend gate: `77 passed`; TypeScript typecheck passes.
- The durable worker currently stops after `business_map` at 40%. The repository explicitly states that no executable driver compiler is connected.
- `CompanyResearchEngine` already validates typed lineage, five-year financial closure, distinct Base/Bull/Bear mechanisms, DCF, reverse DCF, GOOGL/GOOG separation, and required-return comparison.
- The bundled source set is genuine but incomplete: it has authenticated company facts and explicit gaps, while price, FX, and forward-model inputs are absent.

## Scope boundary

This plan completes the research **pipeline** and aggregated read model. It does not:

- redesign the approved prototype;
- add standalone Industry Research;
- publish a `ResearchRevision`;
- export Markdown/PDF;
- claim an investment direction when governed market/model inputs are missing.

Those boundaries prevent a visually complete workbench from hiding an incomplete research engine.

### Task 1: Close the model, strategy-assumption, and market-input contracts

**Files:**
- Modify: `backend/app/underwriting/domain/company_research.py`
- Create: `backend/app/underwriting/services/company_research_model_builder.py`
- Create: `backend/tests/underwriting/test_company_research_model_builder.py`
- Modify: `backend/tests/underwriting/test_company_research_engine.py`

- [ ] **Step 1: Write failing tests for reviewed facts, explicit assumptions, and frozen market refs**

Add tests with this shape:

```python
def test_builder_rejects_unreviewed_evidence(builder_input) -> None:
    builder_input.evidence_payload["facts"][0].pop("review_decision")
    with pytest.raises(ValidationError, match="evidence must be reviewed"):
        CompanyResearchModelBuilder().build(builder_input)


def test_builder_keeps_missing_market_data_as_blocking_gaps(builder_input) -> None:
    result = CompanyResearchModelBuilder().build(
        replace(builder_input, market_context=None)
    )
    assert result.assessment.status == "not_answerable"
    assert result.valuation_set is None
    assert {gap.gap_key for gap in result.gaps} >= {
        "market_price_missing",
        "usd_cny_fx_missing",
    }


def test_builder_uses_exact_frozen_market_refs(builder_input) -> None:
    result = CompanyResearchModelBuilder().build(builder_input)
    assert {
        value.security_external_key for value in result.valuation_set.security_value_ranges
    } == {"NASDAQ:GOOG", "NASDAQ:GOOGL"}
    assert result.market_snapshot_ids == tuple(
        sorted(builder_input.market_context.snapshot_ids, key=str)
    )


def test_builder_rejects_a_missing_or_unversioned_strategy_assumption_set(
    builder_input,
) -> None:
    with pytest.raises(ValidationError, match="strategy assumptions"):
        CompanyResearchModelBuilder().build(
            replace(builder_input, strategy_assumptions=None)
        )
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
cd /Users/xiongjiali/code/fund-engine/.worktrees/alphabet-company-research/backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_model_builder.py \
  tests/underwriting/test_company_research_engine.py -q
```

Expected: collection fails because `company_research_model_builder` and its closed input/result types do not exist.

- [ ] **Step 3: Add explicit typed inputs; do not pass loose artifact dictionaries into the engine**

Define these frozen types in `company_research_model_builder.py`:

```python
@dataclass(frozen=True, slots=True)
class FrozenMarketContext:
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    market_at: datetime
    snapshot_bindings: tuple[FrozenMarketSnapshotBinding, ...]
    reverse_dcf_request: ReverseDcfRequest


@dataclass(frozen=True, slots=True)
class StrategyAssumptionSet:
    strategy_version: str
    content_hash: str
    first_fiscal_year: int
    driver_paths: tuple[DriverInput, ...]
    scenario_overrides: tuple[ScenarioAssumption, ...]
    terminal_growth: Decimal


@dataclass(frozen=True, slots=True)
class CompanyResearchBuildInput:
    project_id: UUID
    identity_set: CompanyResearchIdentitySet
    cutoff_at: datetime
    required_return: Decimal
    evidence_artifact_id: UUID
    evidence_content_hash: str
    evidence_payload: Mapping[str, object]
    gap_payload: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]
    model_template: CompanyResearchModelTemplate
    strategy_assumptions: StrategyAssumptionSet
    market_context: FrozenMarketContext | None


@dataclass(frozen=True, slots=True)
class CompanyResearchBuildResult:
    business_map: BusinessMapArtifact
    driver_map: DriverMapArtifact
    financial_bridge: FinancialBridgeArtifact
    scenario_set: ScenarioSetArtifact
    valuation_set: ValuationSetArtifact | None
    judgment_context: JudgmentContextArtifact
    memo: CompanyResearchMemoArtifact
    assessment: CompanyResearchAssessment
    gaps: tuple[ResearchGap, ...]
    market_snapshot_ids: tuple[UUID, ...]
```

All constructors must reject naive datetimes, duplicate refs, noncanonical UUID order, unknown evidence fields, unreviewed facts, facts marked `rejected` when used as model inputs, and missing or unversioned strategy assumptions. Rejected facts remain valid reviewed evidence but are filtered out of model inputs; if a required input is then absent, the builder records a gap instead of rejecting the whole reviewed artifact. The evidence company/security keys, gap identity, model template, and market bridge must exactly match the required `CompanyResearchIdentitySet`; cross-company evidence or market inputs fail before compilation.

`CompanyResearchModelTemplate` is the typed seam through which a company adapter
supplies module structure, metric classification (revenue/cost/capital), driver
to module mappings, minimum operating-baseline requirements, and the exact
Base/Bull/Bear mechanism mapping. The generic builder must consume this template
and must not guess classifications from metric names or attach all drivers to an
arbitrary first module. Modules named only by a gap remain present in the output.
The template keeps operating-driver bindings separate from the exactly six
financial-driver ownership bindings. Confirmed facts build reported/derived
operating drivers (Search use, monetization, TAC, YouTube, Cloud workload,
capital intensity, dilution, or a future adapter's equivalents); the six
strategy paths are only the closed financial/scenario compiler inputs. The
engine requires those six as a subset and does not limit the driver map to them.
Metric classifications are executable routing rules: confirmed numeric facts
must be placed into the module's revenue, cost, or capital evidence according to
their declared category, not merely checked against an allowlist.

`FrozenMarketSnapshotBinding` binds every UUID to the exact typed market role,
Security key where applicable, and `SourceLineageReference` consumed by the
`MarketBridgeArtifact`. `FrozenMarketContext` rejects a bridge whose refs do not
match those bindings. Its `ReverseDcfRequest` is computed by the governed market
resolver from the frozen price, class-count, and capital-structure inputs; the
generic builder passes it to the pure engine and never invents an implied market
enterprise value.

Strategy-assumption hashing uses `canonical_decimal_string` for every Decimal so
numerically equal values have one hash regardless of exponent/scale. Builder-
generated gaps use a reserved namespace that raw governed gaps cannot occupy;
combining gaps is deterministic and cannot leak a lower-level duplicate-code
error.

- [ ] **Step 4: Separate sourced facts, deterministic derivations, and strategy assumptions**

Extend the driver representation with a controlled input-state field:

```python
class ModelInputState(StrEnum):
    REPORTED = "reported"
    DERIVED = "derived"
    ASSUMPTION = "assumption"


@dataclass(frozen=True, slots=True)
class DriverInput:
    driver_key: str
    state: ModelInputState
    values: tuple[Decimal, ...]
    source_refs: tuple[SourceLineageReference, ...]
    assumption_key: str | None
```

Rules:

- `reported` requires at least one exact source ref and no `assumption_key`;
- `derived` requires source refs and a closed equation identifier;
- `assumption` requires a versioned strategy `assumption_key` and is never labeled reported;
- missing values create `ResearchGap` entries rather than zeroes.

The controlled input state must survive into `DriverMetricArtifact` and
`ScenarioFinancialDriverForecast`, together with the applicable
`assumption_key` or `equation_id`; source-lineage validation is conditional on
that persisted state. Do not weaken the existing non-empty business module
invariants. Adapter templates provide non-empty revenue, cost, and capital
descriptors even when a corresponding numeric fact is still a gap.
Every emitted business module must carry at least one confirmed fact ref or one
explicit gap ref. A template-only empty shell becomes a generated gap and cannot
silently remain `answerable`. Facts used for baseline or drivers require a
canonical finite numeric value, value kind, unit/currency/period, and exact
lineage; labels alone never satisfy an operating-baseline requirement.
Every module fact ref is classified exactly once as revenue, cost, or capital at
the domain boundary. Financial bridge rows preserve factual and assumption
lineage separately. Reverse DCF results are emitted only when the bounded solver
meets its residual tolerance; exhausting `max_iterations` fails closed instead
of publishing an unconverged implied value.

`StrategyAssumptionSet` is a separate, hash-addressed candidate artifact. It must
contain exactly the six five-year engine paths (`revenue`, `operating_margin`,
`cash_tax_rate`, `depreciation`, `capex`, and `working_capital_change`), all with
`state=assumption`, and mechanism-specific Base/Bull/Bear overrides. The builder
must never synthesize numeric forecast paths from the authenticated historical
facts. The Alphabet adapter may validate driver/module vocabulary, but the
numeric paths enter only through this explicit versioned contract and remain
candidate assumptions until the later judgment-review boundary.

Add `CompanyResearchMemoArtifact` as a frozen structured candidate containing
`assessment_status`, `business_map_ref`, `driver_map_ref`, `financial_bridge_ref`,
`scenario_set_ref`, optional `valuation_set_ref`, `gap_keys`,
`strongest_counterevidence`, and `next_verification_events`. It is a machine
draft awaiting judgment review, not a published memo.

- [ ] **Step 5: Implement the minimal builder around the existing pure engine**

The public method must have one closed path:

```python
class CompanyResearchModelBuilder:
    def build(self, value: CompanyResearchBuildInput) -> CompanyResearchBuildResult:
        reviewed = self._reviewed_facts(value)
        business_map = self._business_map(reviewed, value.gap_payload)
        drivers = self._drivers(reviewed)
        scenarios, bridges = self._scenario_inputs(reviewed, drivers)
        market_bridge = self._market_bridge(value.market_context, reviewed)
        typed = self._engine_input(
            value=value,
            business_map=business_map,
            drivers=drivers,
            scenarios=scenarios,
            bridges=bridges,
            market_bridge=market_bridge,
        )
        compiled = CompanyResearchEngine().compile(typed)
        return self._result(value, typed, compiled)
```

Alphabet-specific module names and forecast templates remain in `AlphabetCompanyResearchAdapter`; the generic builder must not import CATL or hard-code Search/Cloud vocabulary.

- [ ] **Step 6: Run focused tests and commit**

```bash
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_model_builder.py \
  tests/underwriting/test_company_research_engine.py -q
git add backend/app/underwriting/domain/company_research.py \
  backend/app/underwriting/services/company_research_model_builder.py \
  backend/tests/underwriting/test_company_research_model_builder.py \
  backend/tests/underwriting/test_company_research_engine.py
git commit -m "feat: build reviewed company research model bundle"
```

### Task 2: Resolve governed market and strategy inputs without browser-authored internal forms

**Files:**
- Create: `backend/app/underwriting/services/company_research_market_inputs.py`
- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/market_inputs.json`
- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/strategy_assumptions.json`
- Modify: `backend/app/underwriting/fixtures/alphabet_golden_case/__init__.py`
- Modify: `backend/app/underwriting/adapters/company_research/alphabet.py`
- Modify: `backend/app/underwriting/services/company_research_model_builder.py`
- Modify: `backend/app/underwriting/services/company_research_initializer.py`
- Modify: `docs/research/2026-08-26-alphabet-golden-case-primary-source-audit.md`
- Test: `backend/tests/underwriting/test_company_research_market_inputs.py`
- Test: `backend/tests/underwriting/test_alphabet_golden_case.py`
- Test: `backend/tests/underwriting/test_company_research_initializer.py`

- [ ] **Step 1: Write RED tests for exact cutoff and fail-closed resolution**

```python
def test_market_inputs_select_only_snapshots_available_by_cutoff(session, initialized) -> None:
    resolved = CompanyResearchMarketInputs(session).resolve(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
    )
    assert resolved.market_at <= CUTOFF
    assert resolved.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")


def test_market_inputs_do_not_replace_missing_provider_data(session, initialized) -> None:
    with pytest.raises(ValidationError, match="market inputs are incomplete"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )
    assert session.query(PriceSnapshot).count() == 0
```

- [ ] **Step 2: Implement one read resolver over existing immutable product snapshots**

```python
class CompanyResearchMarketInputs:
    def resolve(self, *, project_id: UUID, cutoff_at: datetime) -> FrozenMarketContext:
        project, memberships = self._project(project_id, cutoff_at)
        prices = self._exact_prices(memberships, cutoff_at)
        fx = self._exact_fx(prices, "CNY", cutoff_at)
        capital = self._capital_structure(project.primary_company_id, cutoff_at)
        rights = self._security_rights(memberships, cutoff_at)
        return self._validated_context(prices, fx, capital, rights)
```

The resolver reads only immutable `PriceSnapshot`, `FXSnapshot`, `CapitalStructureSnapshot`, and `SecurityRightsVersion` rows. It rejects missing, duplicate, post-cutoff, cross-company, cross-security, wrong-currency, or hash-invalid rows.

Snapshot provenance remains replayable after installation: the immutable rows
or a content-addressed capture-envelope artifact retain the exact source
locator, provider-policy version, and component raw-file references. The
resolver restores those fields verbatim instead of synthesizing a locator from
timestamps. `created_at` records the real installation clock; historical
eligibility is decided by authenticated `available_at`, never by backdating row
creation to the research cutoff. Before insert, prepare checks the business
identity/time without `raw_hash`: identical content is idempotent and different
content raises `ConflictError` for price, FX, capital, and rights.

- [ ] **Step 3: Add a byte-authenticated fixed-cutoff market-input bundle**

`market_inputs.json` uses a closed schema with one GOOGL price, one GOOG price,
one USD/CNY observation, one company capital-structure snapshot, and exact
security-rights references. Define and validate the price member with this
closed model; parallel models use the same provenance fields for FX and
capital structure:

```python
class CapturedMarketPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["price"]
    security_external_key: Literal["NASDAQ:GOOGL", "NASDAQ:GOOG"]
    value: Decimal
    currency: Literal["USD"]
    market_at: AwareDatetime
    available_at: AwareDatetime
    source_url: AnyHttpUrl
    source_locator: str
    raw_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
```

The fixture loader requires canonical decimals and aware datetimes, verifies
every raw hash against captured bytes, and recomputes the manifest hash.
Capture is allowed only from the sources named below; a missing or unavailable
response leaves the existing explicit gap and does not create a partial bundle.

- [ ] **Step 4: Keep acquisition outside the browser and preserve honest gaps**

The initializer continues to create the research foundation without synthetic prices. During the model stage, `CompanyResearchMarketInputs.prepare()` installs the authenticated bundle through the existing `MarketSnapshotService`, then patches the draft with exact immutable refs using its current lock version. Repeating the same bundle is idempotent; a different hash for the same identity/time conflicts. If no governed bundle exists, the model builder receives `market_context=None`, keeps the market gaps, and returns `not_answerable`; it must not call the old low-level form endpoints or invent a current quote.

The fixture loader also loads `strategy_assumptions.json` as a separate
hash-addressed candidate artifact. Every numeric forecast value must be labeled
`assumption`, carry a versioned `assumption_key`, name its deterministic rationale
or equation, and remain visibly distinct from source facts. Missing or invalid
strategy assumptions fail the model stage closed; neither the loader nor the
Alphabet adapter derives replacement forecasts from historical facts.

Driver paths, every scenario override, and terminal growth keep their own
typed `state`, versioned assumption key, rationale, and equation through the
adapter and builder seams. The original fixture candidate digest is preserved;
an adapter must not silently re-hash a metadata-stripped projection.

The Alphabet adapter also emits the concrete `CompanyResearchModelTemplate`:
all six business modules, metric classifications, minimum operating-driver map,
financial-driver ownership, and exact scenario-to-mechanism mapping. This is
vocabulary and structure only; it contains no forecast numbers. A reviewed fact
marked `rejected` is excluded, and any now-unsatisfied required template input
becomes an explicit gap and keeps the candidate `not_answerable`.

The minimum operating-driver map is the complete set approved in the Alphabet
design (Search/query intensity, ad monetization, TAC, YouTube use/ads/
subscriptions, Cloud workload/growth/margin, AI/data-center capex,
depreciation/infrastructure opex/FCF, and SBC/repurchase/dilution). Every
unavailable required binding generates its own critical gap; placeholder module
copy is not sufficient.

Class B remains an independent, non-listed legal-rights component with ten
votes per unit and 1:1 conversion to Class A. Its 835 million units may use the
GOOGL price only through an explicit price-proxy field. They must never be
merged into the GOOGL rights row. The frozen bridge separately preserves Class
A, B, and C quantities, legal-rights lineage, and proxy lineage.

For the frozen Alphabet acceptance set, capture source envelopes from:

- Alphabet Q2 2026 SEC 10-Q for cash, debt, securities, diluted shares and class counts;
- Nasdaq historical quote source for GOOGL and GOOG closes at or before the fixed cutoff;
- Federal Reserve H.10 for the latest USD/CNY observation available at or before the fixed cutoff.

Every captured row stores source URL, exact locator/date, market/effective time, availability time, raw hash, and provider policy version.

- [ ] **Step 5: Run tests and commit**

```bash
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_market_inputs.py \
  tests/underwriting/test_alphabet_golden_case.py \
  tests/underwriting/test_company_research_initializer.py -q
git add backend/app/underwriting/services/company_research_market_inputs.py \
  backend/app/underwriting/fixtures/alphabet_golden_case/market_inputs.json \
  backend/app/underwriting/fixtures/alphabet_golden_case/strategy_assumptions.json \
  backend/app/underwriting/fixtures/alphabet_golden_case/__init__.py \
  backend/app/underwriting/adapters/company_research/alphabet.py \
  backend/app/underwriting/services/company_research_model_builder.py \
  backend/app/underwriting/services/company_research_initializer.py \
  docs/research/2026-08-26-alphabet-golden-case-primary-source-audit.md \
  backend/tests/underwriting/test_company_research_market_inputs.py \
  backend/tests/underwriting/test_alphabet_golden_case.py \
  backend/tests/underwriting/test_company_research_initializer.py
git commit -m "feat: resolve governed Alphabet market inputs"
```

### Task 3: Append the complete model artifact bundle atomically

**Files:**
- Modify: `backend/app/underwriting/persistence/company_research_repository.py`
- Modify: `backend/app/underwriting/services/company_research_workbench.py`
- Test: `backend/tests/underwriting/test_company_research_persistence.py`
- Test: `backend/tests/underwriting/test_company_research_workbench.py`

- [ ] **Step 1: Write RED persistence tests for all-or-nothing bundle publication**

```python
@pytest.mark.parametrize(
    "fail_after",
    ["business_map", "driver_map", "financial_bridge", "scenario_set", "valuation_set"],
)
def test_model_bundle_rolls_back_every_artifact_on_failure(
    session, prepared, fail_after, monkeypatch
) -> None:
    original = prepared.repository.append_artifact
    def fail_on_kind(*args, kind, **kwargs):
        if kind == fail_after:
            raise RuntimeError("injected bundle failure")
        return original(*args, kind=kind, **kwargs)
    monkeypatch.setattr(prepared.repository, "append_artifact", fail_on_kind)
    with pytest.raises(RuntimeError):
        prepared.repository.complete_model_bundle(
            prepared.input,
        )
    session.rollback()
    existing = {
        kind for kind in COMPANY_RESEARCH_ARTIFACT_KINDS
        if prepared.repository.current_artifact(prepared.project_id, kind) is not None
    }
    assert existing == {
        "evidence_index",
        "research_gaps",
    }
```

- [ ] **Step 2: Add one repository command with exact parent/input hashes**

```python
@dataclass(frozen=True, slots=True)
class CompanyResearchPersistedBundle:
    business_map: Mapping[str, object]
    driver_map: Mapping[str, object]
    financial_bridge: Mapping[str, object]
    scenario_set: Mapping[str, object]
    valuation_set: Mapping[str, object] | None
    judgment_context: Mapping[str, object]
    research_gaps: Mapping[str, object]
    memo: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]
    market_snapshot_ids: tuple[UUID, ...]


def complete_model_bundle(
    self,
    preparation_id: UUID,
    *,
    bundle: CompanyResearchPersistedBundle,
    expected_claim_token: str,
    expected_request_hash: str,
    expected_strategy_version: str,
    created_at: datetime,
) -> tuple[CompanyResearchPreparation, tuple[CompanyResearchArtifactVersion, ...]]:
    preparation = self._preparation_for_update(preparation_id, populate_existing=True)
    if preparation is None:
        raise ValidationError("company research preparation not found")
    job = self._locked_prepare_job(preparation, populate_existing=True)
    if (
        preparation.status != "building_model"
        or preparation.current_step != "model_bundle"
        or job.status != "running"
        or job.step != "model_bundle"
        or job.claim_token != expected_claim_token
        or preparation.request_hash != expected_request_hash
        or preparation.strategy_version != expected_strategy_version
    ):
        raise ValidationError("company research preparation claim is stale")
    artifacts = []
    parent_hashes = {}
    for kind, payload in (
        ("business_map", bundle.business_map),
        ("driver_map", bundle.driver_map),
        ("financial_bridge", bundle.financial_bridge),
        ("scenario_set", bundle.scenario_set),
        ("valuation_set", bundle.valuation_set),
        ("judgment_context", bundle.judgment_context),
        ("research_gaps", bundle.research_gaps),
        ("memo", bundle.memo),
    ):
        if payload is None:
            continue
        current = self.current_artifact(preparation.project_id, kind, lock=True)
        expected_parent_id = current.id if current is not None else None
        row = self.append_artifact(
            project_id=preparation.project_id,
            kind=kind,
            input_hash=canonical_hash(
                {
                    "request_hash": expected_request_hash,
                    "parents": parent_hashes,
                    "market_snapshot_ids": [str(x) for x in bundle.market_snapshot_ids],
                }
            ),
            payload=dict(payload),
            source_refs=bundle.source_refs,
            expected_parent_id=expected_parent_id,
            created_at=created_at,
        )
        artifacts.append(row)
        parent_hashes[kind] = row.content_hash
    preparation.status = "awaiting_judgment_review"
    preparation.current_step = "judgment_context"
    preparation.progress = 85
    preparation.updated_at = created_at
    job.status = "waiting_for_review"
    job.step = "judgment_context"
    job.progress = 85
    job.claim_token = None
    self._session.flush([preparation, job])
    return preparation, tuple(artifacts)
```

Append in this exact order: `business_map`, `driver_map`, `financial_bridge`, `scenario_set`, optional `valuation_set`, `judgment_context`, successor `research_gaps`, then `memo`. Each artifact input hash includes the exact upstream artifact IDs and content hashes. The operation runs in one nested transaction and moves preparation/job to:

```text
status=awaiting_judgment_review
current_step=judgment_context
progress=85
job.status=waiting_for_review
job.step=judgment_context
```

No artifact is called ready when one of its required parents is absent.

- [ ] **Step 3: Strengthen `_heads()` cross-artifact lineage validation**

Validate:

```text
driver_map -> business_map
financial_bridge -> driver_map
scenario_set -> driver_map
valuation_set -> scenario_set + financial_bridge + exact market snapshots
judgment_context -> evidence + gaps + model bundle
memo -> exact judgment_context candidate
```

Tampered, missing, foreign-project, duplicate, or current-head-substituted refs raise `ValidationError`; the workbench never omits the broken module and returns a plausible partial answer.

- [ ] **Step 4: Run tests and commit**

```bash
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_persistence.py \
  tests/underwriting/test_company_research_workbench.py -q
git add backend/app/underwriting/persistence/company_research_repository.py \
  backend/app/underwriting/services/company_research_workbench.py \
  backend/tests/underwriting/test_company_research_persistence.py \
  backend/tests/underwriting/test_company_research_workbench.py
git commit -m "feat: persist complete company research model bundle"
```

### Task 4: Replace the business-map stop with an executable worker stage

**Files:**
- Modify: `backend/app/underwriting/services/company_research_preparation.py`
- Modify: `backend/app/scripts/run_company_research_worker.py`
- Test: `backend/tests/underwriting/test_company_research_worker.py`

- [ ] **Step 1: Write RED worker tests for the full post-review stage**

```python
def test_worker_builds_all_model_artifacts_after_last_evidence_review(session, prepared) -> None:
    claim = CompanyResearchPreparationWorker(session, now=clock).claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert CompanyResearchPreparationWorker(session, now=clock).run_claim(claim) \
        == "awaiting_judgment_review"
    repository = CompanyResearchRepository(session)
    heads = {
        kind: repository.current_artifact(prepared.project_id, kind)
        for kind in COMPANY_RESEARCH_ARTIFACT_KINDS
        if repository.current_artifact(prepared.project_id, kind) is not None
    }
    assert set(heads) >= {
        "business_map", "driver_map", "scenario_set", "judgment_context", "memo"
    }
```

Add separate cases for complete market inputs (includes `financial_bridge` and `valuation_set`), missing market inputs (`not_answerable`, gaps retained), provider failure, stale lease, crash before commit, crash after commit, and duplicate worker convergence.

- [ ] **Step 2: Change the queued step from `business_map` to `model_bundle`**

Update the last evidence-review transition and the claim validator. `run_claim()` must:

```python
if claim.step == "model_bundle":
    build_input = self._model_input(claim)
    bundle = self._model_builder.build(build_input)
    self._repository.complete_model_bundle(
        claim.preparation_id,
        bundle=self._persisted_bundle(bundle),
        expected_claim_token=claim.claim_token,
        expected_request_hash=claim.request_hash,
        expected_strategy_version=claim.strategy_version,
        created_at=self._utcnow(),
    )
    self._session.commit()
    return "awaiting_judgment_review"
```

The builder may perform bounded provider reads outside locks. The commit path reacquires the exact claim token and discards stale output exactly as the evidence stage does.

- [ ] **Step 3: Preserve failure classification**

- network/provider timeout: `recoverable_failure`, bounded retry/backoff;
- missing governed market inputs: successful model stage with explicit gaps and `not_answerable`;
- schema/hash/lineage/financial-closure error: `blocked`;
- stale claim: discard output without changing current heads.

- [ ] **Step 4: Run tests and commit**

```bash
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_worker.py \
  tests/underwriting/test_company_research_workbench.py -q
git add backend/app/underwriting/services/company_research_preparation.py \
  backend/app/scripts/run_company_research_worker.py \
  backend/tests/underwriting/test_company_research_worker.py
git commit -m "feat: run complete Alphabet model preparation"
```

### Task 5: Expose a strict nine-module pipeline snapshot and release gate

**Files:**
- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Modify: `backend/tests/underwriting/test_company_research_api.py`
- Modify: `backend/tests/underwriting/test_company_research_workbench.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`

- [ ] **Step 1: Write RED API tests for every module state and artifact kind**

Assert the ordered module keys are exactly:

```python
EXPECTED_MODULES = (
    "overview",
    "business_map",
    "operating_drivers",
    "evidence_and_gaps",
    "industry_competition_regulation",
    "financials_cash_flow_capital_allocation",
    "scenarios_valuation_implied_expectations",
    "counterevidence_risks_next_checks",
    "versions_changes_memo",
)
```

The response must expose typed payload variants rather than an unconstrained `dict`. Unknown fields, duplicate refs, impossible state/artifact pairs, missing units/currencies/periods, and a valuation without exact market refs must fail response construction.

- [ ] **Step 2: Add discriminated artifact summary schemas**

Use a `kind` discriminator for:

```text
evidence_index
research_gaps
business_map
driver_map
financial_bridge
scenario_set
valuation_set
judgment_context
memo
```

Numeric values remain canonical decimal strings. Every numeric observation carries `unit`, `currency`, `period`, `state`, and either an exact source ref or an explicit gap/assumption key.

- [ ] **Step 3: Regenerate the TypeScript contract from a temporary OpenAPI file**

```bash
cd /Users/xiongjiali/code/fund-engine/.worktrees/alphabet-company-research/backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python scripts/dump_openapi.py \
  --output /tmp/alphabet-pipeline-openapi.json
cd ../frontend
npx openapi-typescript /tmp/alphabet-pipeline-openapi.json -o src/contracts/v1.ts
```

Do not overwrite or stage the protected user-owned `frontend/openapi.json` delta.

- [ ] **Step 4: Extend frontend runtime guards and prove fail-closed behavior**

`isCompanyResearchWorkspace()` must reject extra keys, mismatched project IDs, duplicate module keys, missing artifact parents, invalid decimals, an unsupported `kind`, and a `ready` module without the required artifact.

- [ ] **Step 5: Run the completion gate**

```bash
cd /Users/xiongjiali/code/fund-engine/.worktrees/alphabet-company-research/backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/underwriting/test_company_research_policy.py \
  tests/underwriting/test_company_research_persistence.py \
  tests/underwriting/test_company_research_initializer.py \
  tests/underwriting/test_company_research_api.py \
  tests/underwriting/test_alphabet_golden_case.py \
  tests/underwriting/test_company_research_engine.py \
  tests/underwriting/test_company_research_model_builder.py \
  tests/underwriting/test_company_research_market_inputs.py \
  tests/underwriting/test_company_research_worker.py \
  tests/underwriting/test_company_research_workbench.py -q
cd ../frontend
npm test -- --run src/data/InvestmentResearchApi.test.ts
npm run typecheck
```

Expected: all commands exit 0; the workbench API reaches `awaiting_judgment_review` at 85% with nine ordered modules, exact gaps, and either a governed valuation bundle or an honest `not_answerable` result.

- [ ] **Step 6: Commit**

```bash
git add backend/app/underwriting/api/company_research_schemas.py \
  backend/app/underwriting/api/company_research_router.py \
  backend/tests/underwriting/test_company_research_api.py \
  backend/tests/underwriting/test_company_research_workbench.py \
  frontend/src/contracts/v1.ts \
  frontend/src/data/investmentResearchApi.ts \
  frontend/src/data/InvestmentResearchApi.test.ts
git commit -m "feat: expose complete Alphabet research pipeline"
```

## Self-review

- Spec coverage: this plan covers the missing executable path from reviewed evidence through drivers, financial bridge, scenarios, valuation, gaps, judgment context, memo candidate, and nine-module read model.
- Deliberate follow-ons: judgment confirmation, approved prototype UI, immutable freeze/replay, ChangeSet, and export remain separate implementation plans.
- Product boundary: no internal UUID/hash/market forms are added to the browser.
- Source boundary: external failure never falls back to mock; missing market data produces gaps and `not_answerable`.
- Type consistency: artifact kind strings, module keys, preparation states, Pydantic discriminators, generated TypeScript unions, and runtime guards use one exact closed vocabulary.
- Placeholder scan: no incomplete requirements or unspecified error behavior remain in this plan.
