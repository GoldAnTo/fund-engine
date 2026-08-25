# Alphabet Golden-Case Company Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户只选择 Alphabet 并确认默认方案，就能进入一条可恢复、可审计、可冻结和可回放的完整公司研究流程；完成 Alphabet 后，以 CATL 验证同一通用公司研究内核。

**Architecture:** 在现有 investment-research product 内增加一个高层 `CompanyResearchInitializer` 和独立的 company-research 深模块。初始化器原子创建 Project、Mandate、Scope、Agenda、WorkspaceDraft 与准备任务；异步 worker 通过公司适配器生成有来源引用的业务地图、驱动、财务桥、三情景、DCF/反向 DCF、缺口与判断产物；发布器只冻结通过严格引用和可回答性门控的不可变产物。浏览器不再编排底层写接口，只消费预览、初始化、状态和工作台聚合接口。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL/SQLite、React 19、TypeScript、Vite、Vitest/Testing Library、OpenAPI-generated TypeScript contracts、Docker Compose。

---

## 0. Execution guardrails

- [ ] 开始前确认基线并保护用户改动。

Run:

```bash
cd /Users/xiongjiali/code/fund-engine
git status --short
git diff -- frontend/openapi.json
git diff -- docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md
```

Expected protected working-tree changes:

```text
 M docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md
 M frontend/openapi.json
```

The existing verified search fix in these two files belongs to Task 1 and must not be discarded:

```text
backend/app/underwriting/services/product_project.py
backend/tests/underwriting/test_product_project.py
```

- [ ] Never stage the two protected user files. Generate OpenAPI to a temporary path and structurally merge only the feature-owned schema/path additions into the tracked baseline.
- [ ] Keep product code isolated from legacy `ResearchCase`, `ResearchRun`, `ResearchPreparation`, and `AutoResearchService`. The generic `jobs`/`job_events` operational tables may be reused because `research_case_id` is nullable, but the product gets its own repository, service, worker entry point, and target type.
- [ ] Do not invent Alphabet facts, market prices, source hashes, or financial values. Runtime fixtures must be built from captured official-source extracts or explicitly marked `synthetic-test-only` data. Missing real inputs become `ResearchGap` rows and keep the result `not_answerable`.

## Task 1: Make company discovery resolve names, brands, and harmless suffixes

**Files:**

- Create: `backend/alembic/versions/0066_research_object_aliases.py`
- Modify: `backend/app/underwriting/persistence/product_models.py`
- Modify: `backend/app/underwriting/persistence/product_repository.py`
- Modify: `backend/app/underwriting/services/product_project.py`
- Modify: `backend/app/underwriting/fixtures/product_foundation/__init__.py`
- Modify: `backend/app/underwriting/fixtures/product_foundation/manifest.json`
- Modify: `backend/app/underwriting/services/product_foundation_fixture.py`
- Modify: `backend/tests/underwriting/test_product_project.py`
- Modify: `backend/tests/underwriting/test_product_foundation_fixture.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py`

- [ ] Write failing discovery tests for formal name, brand alias, Chinese alias, symbol, and harmless `公司` suffix.

Add tests equivalent to:

```python
@pytest.mark.parametrize(
    ("query", "expected_keys"),
    [
        ("Alphabet", {"US:ALPHABET:COMPANY", "NASDAQ:GOOGL", "NASDAQ:GOOG"}),
        ("Google", {"US:ALPHABET:COMPANY", "NASDAQ:GOOGL", "NASDAQ:GOOG"}),
        ("谷歌", {"US:ALPHABET:COMPANY", "NASDAQ:GOOGL", "NASDAQ:GOOG"}),
        ("GOOGL", {"US:ALPHABET:COMPANY", "NASDAQ:GOOGL", "NASDAQ:GOOG"}),
        ("宁德时代公司", {"CN:300750:COMPANY", "SZSE:300750"}),
    ],
)
def test_product_search_resolves_company_brand_and_security(
    fixture_session, query: str, expected_keys: set[str]
) -> None:
    rows = ResearchProjectService(fixture_session, now=_now).search_objects(query)
    assert {row.external_key for row in rows} == expected_keys
```

- [ ] Run the test and confirm RED because alias persistence is absent and `Google`/`谷歌` return no company.

Run:

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_product_project.py tests/underwriting/test_product_foundation_fixture.py -q
```

- [ ] Add append-only aliases instead of changing canonical identity names.

The new model must have this closed shape:

```python
class UnderwritingResearchObjectAlias(Base):
    __tablename__ = "uw_research_object_aliases"
    __table_args__ = (
        CheckConstraint("length(trim(alias)) > 0", name="ck_uw_object_alias_text"),
        CheckConstraint("normalized_alias = lower(trim(normalized_alias))", name="ck_uw_object_alias_normalized"),
        UniqueConstraint("object_id", "normalized_alias", name="uq_uw_object_alias_object_value"),
        Index("ix_uw_object_alias_normalized", "normalized_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_objects.id"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(160), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

The foundation manifest must attach these aliases to `US:ALPHABET:COMPANY`:

```json
{
  "object_key": "US:ALPHABET:COMPANY",
  "locale": "en",
  "values": ["Google"]
}
```

```json
{
  "object_key": "US:ALPHABET:COMPANY",
  "locale": "zh-CN",
  "values": ["谷歌"]
}
```

Recompute and replace the fixture `content_hash`; keep the loader exact-key, Unicode-normalization, idempotency, conflict, and rollback checks.

- [ ] Query aliases with the same effective company-to-security expansion as canonical names. Only retry a query stripped of a terminal `公司` when the original query has zero results; never strip the suffix from stored identity text.
- [ ] Verify migration head is `0066`, SQLite fresh bootstrap works, aliases are immutable, repeated foundation load returns the same IDs, and runtime search returns the exact expected object set.

Run:

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_product_project.py tests/underwriting/test_product_foundation_fixture.py tests/test_sqlite_migration_bootstrap.py -q
uv run ruff check app/underwriting/persistence/product_models.py app/underwriting/services/product_project.py app/underwriting/services/product_foundation_fixture.py tests/underwriting/test_product_project.py tests/underwriting/test_product_foundation_fixture.py
```

- [ ] Commit only Task 1 files.

```bash
git add backend/alembic/versions/0066_research_object_aliases.py backend/app/underwriting/persistence/product_models.py backend/app/underwriting/persistence/product_repository.py backend/app/underwriting/services/product_project.py backend/app/underwriting/fixtures/product_foundation/__init__.py backend/app/underwriting/fixtures/product_foundation/manifest.json backend/app/underwriting/services/product_foundation_fixture.py backend/tests/underwriting/test_product_project.py backend/tests/underwriting/test_product_foundation_fixture.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: resolve company research identities by alias"
```

## Task 2: Define the versioned default company-research policy

**Files:**

- Create: `backend/app/underwriting/domain/company_research.py`
- Create: `backend/app/underwriting/adapters/company_research/__init__.py`
- Create: `backend/app/underwriting/adapters/company_research/alphabet.py`
- Create: `backend/tests/underwriting/test_company_research_policy.py`

- [ ] Write RED domain tests proving the default is deterministic and contains no browser-authored internal fields.

The core expectation is:

```python
def test_alphabet_default_policy_is_recomputable(alphabet_identity_set) -> None:
    adapter = AlphabetCompanyResearchAdapter()
    first = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    second = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    assert first == second
    assert first.strategy_version == "company-research-default.v1"
    assert first.horizon_years == 5
    assert first.base_currency == "CNY"
    assert first.required_return == Decimal("0.12")
    assert first.permanent_loss_limit == Decimal("0.25")
    assert first.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    assert first.input_hash == canonical_hash(first.canonical_payload())
```

- [ ] Run the test and confirm RED because `company_research` and the Alphabet adapter do not exist.
- [ ] Implement frozen, strict dataclasses/enums for:

```python
CompanyResearchIdentitySet
CompanyResearchDefaultPolicy
CompanyResearchPreview
CompanyResearchModule
ResearchGap
CompanyResearchAdapter
```

The generic policy must own only cross-company concerns. Use this exact module order:

```python
DEFAULT_MODULES = (
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

The Alphabet adapter owns segment vocabulary and returns:

```python
ALPHABET_BUSINESS_MODULES = (
    "search_and_other_ads",
    "youtube_ads_and_subscriptions",
    "google_cloud",
    "other_google_services",
    "other_bets",
    "corporate_capital_allocation",
)
```

It must not create data values or claim that a module is complete.

- [ ] Reject naive datetimes, missing Company, unrelated Security, duplicate Security, non-CNY default policy, changed strategy version, and unordered security identity sets.
- [ ] Run the focused domain tests and Ruff.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_policy.py -q
uv run ruff check app/underwriting/domain/company_research.py app/underwriting/adapters/company_research tests/underwriting/test_company_research_policy.py
```

- [ ] Commit.

```bash
git add backend/app/underwriting/domain/company_research.py backend/app/underwriting/adapters/company_research backend/tests/underwriting/test_company_research_policy.py
git commit -m "feat: define default company research policy"
```

## Task 3: Persist initialization, preparation state, and immutable artifact versions

**Files:**

- Create: `backend/alembic/versions/0067_company_research_workbench.py`
- Create: `backend/app/underwriting/persistence/company_research_models.py`
- Create: `backend/app/underwriting/persistence/company_research_repository.py`
- Create: `backend/tests/underwriting/test_company_research_persistence.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py`

- [ ] Write RED persistence tests for idempotency, immutable artifacts, strict parent chains, current-version lookup, job ownership, and caller rollback.
- [ ] Add these three tables in migration `0067`:

```text
uw_company_research_preparations
uw_company_research_artifact_versions
uw_company_research_events
```

Use this preparation state machine:

```python
PreparationStatus = Literal[
    "queued",
    "preparing_sources",
    "awaiting_evidence_review",
    "building_model",
    "awaiting_judgment_review",
    "ready_to_freeze",
    "recoverable_failure",
    "blocked",
    "completed",
]
```

The preparation row is the one mutable operational projection and must include:

```text
id, project_id, idempotency_key, request_hash, strategy_version,
status, current_step, progress, attempt, next_attempt_at, last_error_code,
job_id, created_at, updated_at
```

Required constraints:

```text
UNIQUE(project_id)
UNIQUE(idempotency_key)
CHECK(progress BETWEEN 0 AND 100)
CHECK(attempt >= 1)
job_id -> jobs.id
```

The immutable artifact row must include:

```text
id, project_id, kind, version, supersedes_id, input_hash,
payload JSON, source_refs JSON, content_hash, created_at
```

Required constraints:

```text
UNIQUE(project_id, kind, version)
UNIQUE(supersedes_id)
CHECK(version >= 1)
CHECK(length(input_hash) = 64)
CHECK(length(content_hash) = 64)
```

Artifact kinds are closed to:

```python
COMPANY_RESEARCH_ARTIFACT_KINDS = frozenset(
    {
        "evidence_index",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
        "research_gaps",
        "judgment_context",
        "memo",
    }
)
```

- [ ] Install UPDATE/DELETE rejection triggers on artifact and event tables for SQLite and PostgreSQL. Do not make the preparation row immutable.
- [ ] Reuse generic `Job` with `kind="prepare_company_research"`, `target_type="company_research_preparation"`, `target_id=preparation.id`, and `research_case_id=None`; repository queries must filter all four fields and never claim legacy jobs.
- [ ] Ensure the repository verifies canonical hashes on every read, loads a full parent chain iteratively, detects cycles before replay, and never silently chooses among duplicate current heads.
- [ ] Verify fresh migration to `0067`, SQLite and PostgreSQL constraints, savepoint rollback, and artifact tamper failure.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_persistence.py tests/test_sqlite_migration_bootstrap.py -q
uv run ruff check app/underwriting/persistence/company_research_models.py app/underwriting/persistence/company_research_repository.py tests/underwriting/test_company_research_persistence.py
```

- [ ] Commit.

```bash
git add backend/alembic/versions/0067_company_research_workbench.py backend/app/underwriting/persistence/company_research_models.py backend/app/underwriting/persistence/company_research_repository.py backend/tests/underwriting/test_company_research_persistence.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: persist company research preparation"
```

## Task 4: Build the atomic, idempotent CompanyResearchInitializer

**Files:**

- Create: `backend/app/underwriting/services/company_research_initializer.py`
- Create: `backend/tests/underwriting/test_company_research_initializer.py`
- Modify: `backend/app/underwriting/services/workspace_draft.py`
- Modify: `backend/tests/underwriting/test_workspace_draft.py`

- [ ] Write RED tests for zero-write preview, atomic initialization, same-key replay, different-key conflict, response-loss recovery, unrelated Security rejection, and rollback at each write stage.

The public service seam must be exactly high-level:

```python
class CompanyResearchInitializer:
    def preview(
        self,
        *,
        company_id: UUID,
        cutoff_at: datetime,
    ) -> CompanyResearchPreview:
        return self._preview(company_id=company_id, cutoff_at=cutoff_at)

    def initialize(
        self,
        *,
        preview_hash: str,
        company_id: UUID,
        cutoff_at: datetime,
        idempotency_key: str,
    ) -> CompanyResearchInitialization:
        return self._initialize(
            preview_hash=preview_hash,
            company_id=company_id,
            cutoff_at=cutoff_at,
            idempotency_key=idempotency_key,
        )
```

No public initializer argument may accept mandate IDs, scope IDs, agenda IDs, hashes other than `preview_hash`, price values, raw source IDs, capital structure values, or security-rights ledger fields.

- [ ] `preview()` must resolve all effective related securities, choose the adapter by Company external key, freeze the instant to UTC, and write nothing or trigger autoflush.
- [ ] `initialize()` must validate the recomputed preview hash and then, inside one caller-owned transaction, create:

```text
ResearchProject
ProjectSecurity memberships
InvestmentMandate v1
ResearchScope v1
ResearchAgenda v1
WorkspaceDraft
CompanyResearchPreparation
prepare_company_research Job
CompanyResearchEvent(initialized)
```

The hidden foundation values are:

```python
InvestmentMandateInput(
    horizon_years=5,
    base_currency="CNY",
    required_return=Decimal("0.12"),
    permanent_loss_limit=Decimal("0.25"),
    comparison_set=("absolute_intrinsic_value",),
    benchmark_key=None,
    required_excess_return=None,
)
```

The draft references the generated mandate/scope/agenda but leaves evidence, market, model, and memo references empty.

- [ ] Make idempotency bind the key to `request_hash`. A repeated same key/same request returns the original project and preparation; same key/different request raises domain `ConflictError`; concurrent different keys for the same Company/cutoff must not create two active preparations for one project.
- [ ] Ensure SQLite uses a real outer transaction before `begin_nested()` and PostgreSQL uses row/unique-key serialization. A caller rollback after successful `initialize()` must leave zero product rows.
- [ ] Run focused and neighboring regression tests.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_initializer.py tests/underwriting/test_product_project.py tests/underwriting/test_workspace_draft.py tests/underwriting/test_product_persistence.py -q
uv run ruff check app/underwriting/services/company_research_initializer.py app/underwriting/services/workspace_draft.py tests/underwriting/test_company_research_initializer.py
```

- [ ] Commit.

```bash
git add backend/app/underwriting/services/company_research_initializer.py backend/app/underwriting/services/workspace_draft.py backend/tests/underwriting/test_company_research_initializer.py backend/tests/underwriting/test_workspace_draft.py
git commit -m "feat: initialize company research atomically"
```

## Task 5: Expose closed high-level HTTP contracts and regenerate clients safely

**Files:**

- Create: `backend/app/underwriting/api/company_research_router.py`
- Create: `backend/app/underwriting/api/company_research_schemas.py`
- Create: `backend/tests/underwriting/test_company_research_api.py`
- Modify: `backend/app/underwriting/api/router.py`
- Verify: `backend/scripts/dump_openapi.py`
- Verify: `backend/tests/underwriting/test_openapi_dump.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`

- [ ] Write RED API tests for these endpoints and exact statuses:

```text
POST /api/underwriting/v1/product/company-research/preview                    200
POST /api/underwriting/v1/product/company-research/initializations            201
GET  /api/underwriting/v1/product/company-research/projects/{project_id}       200
POST /api/underwriting/v1/product/company-research/projects/{project_id}/retry 202
```

Initialization requires `Idempotency-Key`; preview does not. The request bodies are closed:

```python
class CompanyResearchPreviewRequest(UnderwritingModel):
    company_id: UUID
    cutoff_at: datetime


class InitializeCompanyResearchRequest(UnderwritingModel):
    company_id: UUID
    cutoff_at: datetime
    preview_hash: str = Field(pattern=SHA256_PATTERN)
```

The preview response must expose only user-meaningful values:

```text
company identity
security identities
strategy version
5-year horizon
CNY base currency
12% required return
25% permanent-loss boundary
cutoff instant
9-module agenda summary
preview hash
```

It must not expose mandate/scope/agenda UUIDs or source/raw hashes.

- [ ] Confirm RED for absent routes, then add thin handlers that call the service and use `commit_write` only for initialize/retry. Preview and status perform no commit/rollback.
- [ ] Add strict TypeScript response guards. They must reject extra keys, invalid UUID/hash/datetime/decimal values, duplicate securities, mismatched company/project/preparation references, out-of-range progress, invalid state/step pairs, and request/response preview binding drift.
- [ ] Generate OpenAPI to a temporary file, generate `v1.ts` from that temporary contract, and leave the user's 14-line `frontend/openapi.json` patch unstaged and byte-for-byte unchanged.

Run:

```bash
cd /Users/xiongjiali/code/fund-engine/backend
tmp_dir=$(mktemp -d)
uv run python scripts/dump_openapi.py --output "$tmp_dir/openapi.json"
cd ../frontend
npx openapi-typescript "$tmp_dir/openapi.json" -o src/contracts/v1.ts
npm test -- InvestmentResearchApi.test.ts
npm run typecheck
```

- [ ] Verify API/OpenAPI and commit without `frontend/openapi.json`.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_api.py tests/underwriting/test_openapi_dump.py tests/underwriting/test_product_api.py -q
cd ../frontend
npm test -- InvestmentResearchApi.test.ts
npm run typecheck
cd ..
git add backend/app/underwriting/api/company_research_router.py backend/app/underwriting/api/company_research_schemas.py backend/app/underwriting/api/router.py backend/scripts/dump_openapi.py backend/tests/underwriting/test_company_research_api.py backend/tests/underwriting/test_openapi_dump.py frontend/src/contracts/v1.ts frontend/src/data/investmentResearchApi.ts frontend/src/data/InvestmentResearchApi.test.ts
git commit -m "feat: expose company research initialization api"
```

## Task 6: Replace the internal setup form with a two-step company flow

**Files:**

- Modify: `frontend/src/features/investment-research/NewResearchPage.tsx`
- Modify: `frontend/src/features/investment-research/NewResearchPage.test.tsx`
- Modify: `frontend/src/features/investment-research/ResearchHomePage.tsx`
- Modify: `frontend/src/features/investment-research/InvestmentResearchShell.test.tsx`
- Modify: `frontend/src/styles/underwriting-research.css`

- [ ] Write RED browser tests for the user path:

```text
search Google
see Alphabet Inc. with GOOGL and GOOG
click Research Alphabet
see one default-plan summary
click Start research once
land at /research/projects/{project_id}
```

The tests must prove there are no visible inputs for:

```text
mandate, scope, agenda, source hash, price, FX, capital structure,
security rights, market snapshot, revision boundary
```

- [ ] Replace the multi-form component with three explicit UI states: `selecting_company`, `reviewing_default`, `initializing`.

The summary copy must show:

```text
研究对象：Alphabet Inc.
关联证券：GOOGL Class A；GOOG Class C
研究期限：5 年
基准币种：CNY
最低要求回报：12%
永久损失边界：25%
资料截止：<Asia/Shanghai local time>
系统将准备：业务地图、经营驱动、证据与缺口、财务桥、三种情景、DCF/反向 DCF、反证与版本
```

- [ ] Generate a fresh random idempotency key once per user intent and retain it across retry/response loss. Disable double submit synchronously with a ref. On network loss, retry the same key; never create bottom-level foundation calls from the page.
- [ ] Industry search results remain browseable but their action copy is `查看相关公司`, not `建立行业研究`. A Security-only query returns its related Company group, and the primary action remains on the Company card; invalid relations are still rejected by the backend.
- [ ] Preserve focus, `aria-live`, `aria-busy`, no-results, retry, stale-promise, route-change, and unmount protections.
- [ ] Run focused frontend tests, typecheck, and build.

```bash
cd /Users/xiongjiali/code/fund-engine/frontend
npm test -- NewResearchPage.test.tsx InvestmentResearchShell.test.tsx InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

- [ ] Commit.

```bash
git add frontend/src/features/investment-research/NewResearchPage.tsx frontend/src/features/investment-research/NewResearchPage.test.tsx frontend/src/features/investment-research/ResearchHomePage.tsx frontend/src/features/investment-research/InvestmentResearchShell.test.tsx frontend/src/styles/underwriting-research.css
git commit -m "feat: simplify company research entry flow"
```

## Task 7: Install an authenticated Alphabet golden-case source adapter

**Files:**

- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/__init__.py`
- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/manifest.json`
- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/business_map.json`
- Create: `backend/app/underwriting/fixtures/alphabet_golden_case/source_facts.json`
- Modify: `backend/app/underwriting/adapters/company_research/alphabet.py`
- Create: `backend/app/underwriting/services/company_research_sources.py`
- Create: `backend/tests/underwriting/test_alphabet_golden_case.py`

- [ ] Write RED fixture/authentication tests before adding source data. The bundled loader must reject duplicate JSON keys, non-NFC text, leading/trailing whitespace, non-round-tripping dates, unknown keys, unsupported currencies/units, hash mismatch, duplicate fact identity, missing source locator, and facts whose `available_at` is later than the frozen cutoff.
- [ ] Define a source fact contract with exact fields:

```python
@dataclass(frozen=True, slots=True)
class CompanySourceFact:
    fact_key: str
    company_external_key: str
    business_module: str
    metric_key: str
    value: Decimal | str
    value_kind: Literal["reported", "derived", "management_guidance"]
    currency: str | None
    unit: str | None
    period_start: date | None
    period_end: date | None
    published_at: datetime
    available_at: datetime
    source_role: Literal["regulatory_filing", "company_material", "official_regulator"]
    source_url: str
    source_locator: str
    raw_hash: str
```

- [ ] Build the fixture only from primary-source extracts captured during implementation. The minimum source roles are Alphabet regulatory filing, Alphabet earnings material, and an official regulatory document when regulatory claims are present. Each JSON file has its own `content_hash`; the Python package also pins an exact-byte SHA-256 trusted root for the bundled manifest.
- [ ] Include business modules for Search/ads, YouTube, Cloud, other services, Other Bets, and corporate capital allocation. Every module must have either at least one source-backed fact or an explicit `ResearchGap`; an empty module cannot be called complete.
- [ ] Preserve GOOGL and GOOG as distinct securities. Company facts are shared; price, voting rights, economic units, and per-share valuation refs remain security-specific.
- [ ] `CompanyResearchSourceService.prepare_evidence_index()` writes only an immutable `evidence_index` artifact plus a `research_gaps` artifact. It does not directly publish an assessment or silently promote machine extraction to confirmed fact.
- [ ] Test source unavailability and parser failure: preparation becomes `recoverable_failure`, emits a safe structured error, leaves no partial artifact head, and never switches to mock data.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_alphabet_golden_case.py tests/underwriting/test_candidate_evidence.py tests/underwriting/test_source_freeze.py -q
uv run ruff check app/underwriting/fixtures/alphabet_golden_case app/underwriting/adapters/company_research/alphabet.py app/underwriting/services/company_research_sources.py tests/underwriting/test_alphabet_golden_case.py
```

- [ ] Commit.

```bash
git add backend/app/underwriting/fixtures/alphabet_golden_case backend/app/underwriting/adapters/company_research/alphabet.py backend/app/underwriting/services/company_research_sources.py backend/tests/underwriting/test_alphabet_golden_case.py
git commit -m "feat: add authenticated Alphabet research fixture"
```

## Task 8: Compile business map, drivers, financial bridge, scenarios, and valuation

**Files:**

- Modify: `backend/app/underwriting/domain/company_research.py`
- Create: `backend/app/underwriting/services/company_research_engine.py`
- Create: `backend/tests/underwriting/test_company_research_engine.py`

- [ ] Write RED domain tests for exact source lineage, financial closure, mechanism-distinct scenarios, DCF, reverse DCF, and no-probability value range.

Use these closed artifact payloads:

```text
BusinessMapArtifact: modules -> revenue_sources, cost_structure, capital_needs, fact_refs, gap_refs
DriverMapArtifact: driver_key, module_key, fact_refs, assumption_refs, equation, output_metric
FinancialBridgeArtifact: forecast years, revenue, operating_income, tax, depreciation, capex, working_capital_change, fcff
ScenarioSetArtifact: base, bull, bear with distinct mechanism_id and explicit driver overrides
ValuationSetArtifact: scenario DCF values, reverse-DCF implied assumptions, security value ranges, required-return comparisons
JudgmentContextArtifact: answerability inputs, strongest counterevidence, next verification events
```

- [ ] Make the engine accept only typed, validated artifacts; never accept free-form AI prose as a number or formula. Use Decimal throughout and canonical decimal strings at persistence boundaries.
- [ ] Enforce the financial identity for every year:

```python
expected_fcff = (
    row.operating_income * (Decimal("1") - row.cash_tax_rate)
    + row.depreciation
    - row.capex
    - row.working_capital_change
)
if row.fcff != expected_fcff:
    raise ValidationError("financial bridge does not close")
```

- [ ] Enforce mechanism-distinct scenarios:

```python
mechanisms = {scenario.mechanism_id for scenario in scenario_set.scenarios}
if mechanisms != {
    "search_cloud_resilience",
    "ai_monetization_and_utilization",
    "search_disruption_and_capital_drag",
}:
    raise ValidationError("Alphabet scenarios must use distinct mechanisms")
```

- [ ] DCF must discount five annual FCFF values and terminal value at the mandate required return; reject terminal growth greater than or equal to the discount rate. Reverse DCF must solve a bounded named driver with deterministic bisection and return the achieved residual and iteration count.
- [ ] ValueRange is the ordered min/max of mechanism scenarios, not a probability distribution. The payload must not contain `probability`, `odds`, `expected_value`, or scenario weights.
- [ ] Convert enterprise value through exact capital-structure and Security-rights references to GOOGL and GOOG per-share values, then through exact USD/CNY FX to CNY return ranges. Refuse missing or duplicated Security, price, FX, capital, or rights refs.
- [ ] Assessment readiness rules:

```text
missing operating baseline -> not_answerable
financial bridge not closed -> not_answerable
missing exact market/security bridge -> not_answerable
unresolved critical ResearchGap -> not_answerable
otherwise answerable or partially_answerable according to closed rule table
not_answerable -> direction=None and confidence=None
```

- [ ] Run focused engine tests and property-style boundary tests for decimal scales, negative cash/debt combinations, zero/negative shares, terminal growth boundary, scenario ordering, and multi-security set ordering.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_engine.py tests/underwriting/test_market_snapshots.py -q
uv run ruff check app/underwriting/domain/company_research.py app/underwriting/services/company_research_engine.py tests/underwriting/test_company_research_engine.py
```

- [ ] Commit.

```bash
git add backend/app/underwriting/domain/company_research.py backend/app/underwriting/services/company_research_engine.py backend/tests/underwriting/test_company_research_engine.py
git commit -m "feat: build auditable company research model"
```

## Task 9: Add the recoverable product worker and preparation state machine

**Files:**

- Create: `backend/app/scripts/run_company_research_worker.py`
- Create: `backend/app/underwriting/services/company_research_preparation.py`
- Create: `backend/tests/underwriting/test_company_research_worker.py`
- Modify: `backend/app/services/research_worker_heartbeat.py`
- Modify: `backend/app/scripts/check_worker_heartbeat.py`
- Modify: `docker-compose.one-click.yml`
- Modify: `backend/tests/test_one_click_runtime_assets.py`
- Modify: `backend/tests/test_investment_research_runtime.py`

- [ ] Write RED tests for claim exclusivity, lease ownership, stage commit boundaries, stale-output discard, retry/backoff, restart recovery, cancellation, and provider failure sanitization.
- [ ] Implement this ordered stage graph:

```python
COMPANY_RESEARCH_STAGES = (
    "prepare_sources",
    "build_business_map",
    "build_driver_map",
    "build_financial_bridge",
    "build_scenarios",
    "build_valuation",
    "evaluate_readiness",
)
```

Each stage reads exact predecessor artifact IDs and hashes, performs provider/file work outside a database transaction, then reacquires the preparation row and writes one immutable artifact version plus event in one short transaction. If strategy version, input hash, job claim token, predecessor head, or cutoff changed, discard the output and emit `stale_output_discarded`.

After `prepare_sources`, the state must stop at `awaiting_evidence_review`. Only an exact reviewed evidence-index successor may enqueue `build_business_map`; rejected facts remain visible, unresolved critical candidates become ResearchGaps, and downstream artifacts are invalidated when a review changes their input hash.

- [ ] Worker retries only recoverable external failures with fixed attempts/backoff. Validation, hash, relation, ownership, and lineage failures are `blocked` and require a new preparation version, not blind retry.
- [ ] Add a separate Compose service:

```yaml
  company-research-worker:
    image: ${ONE_CLICK_BACKEND_IMAGE:-fund-engine-one-click-backend:local}
    restart: unless-stopped
    command: ["python", "-m", "app.scripts.run_company_research_worker", "--loop", "--poll-seconds", "1"]
```

It uses `worker_kind="company_research"`. It may start without an LLM key, but any AI-dependent stage must fail closed; deterministic golden-fixture compilation remains available.

- [ ] Verify a process crash after provider work but before artifact commit leaves the job retryable, and a crash after artifact commit converges by detecting the exact artifact head. Verify two PostgreSQL workers cannot execute one claim concurrently.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_worker.py tests/test_one_click_runtime_assets.py tests/test_investment_research_runtime.py -q
uv run ruff check app/scripts/run_company_research_worker.py app/underwriting/services/company_research_preparation.py tests/underwriting/test_company_research_worker.py
```

- [ ] Commit.

```bash
git add backend/app/scripts/run_company_research_worker.py backend/app/underwriting/services/company_research_preparation.py backend/app/services/research_worker_heartbeat.py backend/app/scripts/check_worker_heartbeat.py docker-compose.one-click.yml backend/tests/underwriting/test_company_research_worker.py backend/tests/test_one_click_runtime_assets.py backend/tests/test_investment_research_runtime.py
git commit -m "feat: run recoverable company research preparation"
```

## Task 10: Aggregate a strict company workbench read model

**Files:**

- Create: `backend/app/underwriting/services/company_research_workbench.py`
- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Create: `backend/tests/underwriting/test_company_research_workbench.py`
- Modify: `backend/tests/underwriting/test_company_research_api.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`

- [ ] Write RED tests for:

```text
GET /api/underwriting/v1/product/company-research/projects/{project_id}/workspace
POST /api/underwriting/v1/product/company-research/projects/{project_id}/evidence-reviews
```

The response must be one closed snapshot containing project identity, preparation status, module readiness, current immutable artifact summaries, source/gap counts, draft lock, selected revision, and change summary. It must not issue one query per module.

- [ ] Build the service with bounded batched queries. For every artifact, verify project ownership, kind, version chain, payload schema, source refs, input refs, and content hash before returning it. Historical selected revision reads use the frozen manifest and do not consult current project memberships or current artifact heads.
- [ ] Return module state as one of:

```python
ModuleState = Literal["not_started", "preparing", "needs_review", "ready", "blocked"]
```

`ready` means a candidate artifact exists and validates; it does not mean a user has frozen a conclusion.

- [ ] Add an append-only evidence review command with this closed body:

```python
class ReviewCompanyEvidenceRequest(UnderwritingModel):
    evidence_artifact_id: UUID
    fact_key: StrictStr = Field(min_length=1, max_length=160)
    decision: Literal["confirmed", "rejected"]
    expected_head_id: UUID
```

The command must lock the preparation, verify the candidate and current head, append an `evidence_index` successor, recompute its hash, emit an event, invalidate downstream input hashes, and enqueue only the next now-eligible stage. Stale, duplicate, foreign-project, unknown-fact, already-reviewed, or extra-field requests fail closed. The same exact review request is idempotent.

- [ ] Add strict frontend guards for all nested discriminated artifact summaries. Unknown kind/schema/field, duplicated refs, a `ready` module without its required artifact, or a revision that references a different project must fail closed as `invalid_response`.
- [ ] Generate OpenAPI/TypeScript through temporary files and preserve the user's OpenAPI patch exactly as in Task 5.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_workbench.py tests/underwriting/test_company_research_api.py -q
cd ../frontend
npm test -- InvestmentResearchApi.test.ts
npm run typecheck
```

- [ ] Commit without `frontend/openapi.json`.

```bash
git add backend/app/underwriting/services/company_research_workbench.py backend/app/underwriting/api/company_research_schemas.py backend/app/underwriting/api/company_research_router.py backend/tests/underwriting/test_company_research_workbench.py backend/tests/underwriting/test_company_research_api.py frontend/src/contracts/v1.ts frontend/src/data/investmentResearchApi.ts frontend/src/data/InvestmentResearchApi.test.ts
git commit -m "feat: expose company research workbench snapshot"
```

## Task 11: Render the nine-module Alphabet workbench

**Files:**

- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Modify: `frontend/src/features/investment-research/InvestmentResearchShell.test.tsx`
- Create: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`
- Create: `frontend/src/features/investment-research/companyResearchView.ts`
- Create: `frontend/src/features/investment-research/companyResearchView.test.ts`
- Modify: `frontend/src/styles/underwriting-research.css`

- [ ] Write RED UI tests for all nine modules, progress, gaps, module retry, source trace, GOOGL/GOOG distinction, DCF and reverse-DCF display, not-answerable behavior, and revision change display.
- [ ] Add RED interaction tests proving a candidate fact can be confirmed or rejected, the exact reviewed successor is returned, downstream modules become stale/preparing, and a failed review retains focus and does not optimistically mark the fact formal.
- [ ] Replace the current two-enabled-module placeholder with these user labels:

```typescript
const companyResearchModules = [
  ["overview", "概览与当前判断"],
  ["business_map", "Google 如何赚钱"],
  ["operating_drivers", "关键经营变量"],
  ["evidence_and_gaps", "来源、事实与缺口"],
  ["industry_competition_regulation", "行业、竞争与监管"],
  ["financials_cash_flow_capital_allocation", "财务、现金流与资本配置"],
  ["scenarios_valuation_implied_expectations", "情景、估值与当前价格隐含"],
  ["counterevidence_risks_next_checks", "反证、风险与下一验证"],
  ["versions_changes_memo", "版本、变化与研究备忘录"],
] as const;
```

- [ ] Keep audit concepts behind an `审计详情` disclosure. Do not show mandate/scope/agenda/boundary UUIDs in the primary reading hierarchy.
- [ ] Every numeric card must show unit, currency, period/cutoff, state (`reported`, `derived`, `assumption`), and source/gap link. Do not render an absent number as zero, dash-only, or inferred text.
- [ ] Scenario UI shows mechanism, changed drivers, financial effect, value range, and counterevidence. It must not render probability bars or a probability-weighted target price.
- [ ] Overview rules:

```text
not_answerable -> no direction, confidence, target, or return badge
partially_answerable -> provisional label and visible blockers
answerable -> value range, return range, strongest counterevidence, next validation event
```

- [ ] Poll status with bounded backoff while preparation is active; stop on unmount, project switch, blocked, completed, or browser hidden state. Retry only the server-declared failed stage. Prevent stale responses from replacing a newer workspace snapshot.
- [ ] In `来源、事实与缺口`, render candidate state, source locator, cutoff, unit/currency, conflict group, and explicit `确认`/`驳回` actions. Never infer confirmation from an official-looking source label.
- [ ] Meet keyboard, focus, contrast, responsive, reduced-motion, `aria-live`, and `aria-busy` requirements. Keep the legacy research bundle lazy and out of product chunks.

```bash
cd /Users/xiongjiali/code/fund-engine/frontend
npm test -- ResearchWorkbenchPage.test.tsx companyResearchView.test.ts InvestmentResearchShell.test.tsx InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

- [ ] Commit.

```bash
git add frontend/src/features/investment-research/ResearchWorkbenchPage.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx frontend/src/features/investment-research/InvestmentResearchShell.test.tsx frontend/src/features/investment-research/companyResearchView.ts frontend/src/features/investment-research/companyResearchView.test.ts frontend/src/styles/underwriting-research.css
git commit -m "feat: render Alphabet company research workbench"
```

## Task 12: Freeze model artifacts in product revisions and replay them strictly

**Files:**

- Modify: `backend/app/underwriting/services/workspace_draft.py`
- Modify: `backend/app/underwriting/services/revision_publisher.py`
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Modify: `backend/app/underwriting/domain/product_contracts.py`
- Modify: `backend/app/underwriting/api/product_schemas.py`
- Modify: `backend/tests/underwriting/test_workspace_draft.py`
- Modify: `backend/tests/underwriting/test_revision_publisher.py`
- Modify: `backend/tests/underwriting/test_research_revision_diff.py`
- Modify: `backend/tests/underwriting/test_product_api.py`

- [ ] Write RED tests proving a complete artifact set can publish, incomplete/foreign/tampered/current-head-only refs cannot publish, and a later artifact version does not change an older revision.
- [ ] Add canonical `model_refs` and `memo_ref` to `WorkspaceDraftContent` and its patch contract. Store UUIDs canonically sorted and reject duplicates.
- [ ] A publishable company revision must freeze exactly one current artifact of each required kind:

```python
REQUIRED_COMPANY_MODEL_KINDS = frozenset(
    {
        "evidence_index",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
        "research_gaps",
        "judgment_context",
    }
)
```

`memo_ref` points to one immutable `memo` artifact. Publication preview validates exact project ownership, hashes, parent chains, cross-artifact input refs, market boundary, and source cutoff.
- [ ] Replace the fixed `not_answerable` publication assessment with the closed readiness output from `judgment_context`. Preserve the invariant:

```python
if answerability == AnswerabilityState.NOT_ANSWERABLE:
    if direction is not None or confidence is not None:
        raise ValidationError("not-answerable research cannot have direction or confidence")
```

- [ ] Manifest `model_refs` must contain frozen IDs, not current heads. The strict reader loads each exact ID, revalidates its canonical content hash and lineage, and rejects unknown kinds, duplicates, missing refs, or post-cutoff source facts.
- [ ] Extend revision summary/diff with artifact changes grouped as `fact`, `mechanism`, `forecast`, `valuation`, `judgment`, and `memo`. Use iterative parent replay to support at least 1,200 revisions without recursion.
- [ ] Test atomic rollback at boundary, assessment, manifest, revision, and draft-CAS stages; idempotent same-key publish; concurrent different-key conflict; SQLite explicit outer transaction; PostgreSQL concurrency; corrupted historical reads return internal integrity error rather than 422 client validation.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_workspace_draft.py tests/underwriting/test_revision_publisher.py tests/underwriting/test_research_revision_diff.py tests/underwriting/test_product_api.py -q
uv run ruff check app/underwriting/services/workspace_draft.py app/underwriting/services/revision_publisher.py app/underwriting/services/research_revision_diff.py
```

- [ ] Commit.

```bash
git add backend/app/underwriting/services/workspace_draft.py backend/app/underwriting/services/revision_publisher.py backend/app/underwriting/services/research_revision_diff.py backend/app/underwriting/domain/product_contracts.py backend/app/underwriting/api/product_schemas.py backend/tests/underwriting/test_workspace_draft.py backend/tests/underwriting/test_revision_publisher.py backend/tests/underwriting/test_research_revision_diff.py backend/tests/underwriting/test_product_api.py
git commit -m "feat: freeze company research model artifacts"
```

## Task 13: Complete memo, freeze, replay, and export in the user flow

**Files:**

- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Create: `backend/app/underwriting/services/company_research_publication.py`
- Create: `backend/tests/underwriting/test_company_research_publication.py`
- Modify: `backend/tests/underwriting/test_company_research_api.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`

- [ ] Write RED HTTP and UI tests for a user-authored memo, zero-write publication preview, explicit confirmation, idempotent freeze, response-loss replay, frozen revision selection, second-cutoff successor, ChangeSet, and deterministic export.
- [ ] Add these high-level endpoints so the browser never calls low-level draft/publisher routes directly:

```text
PATCH /api/underwriting/v1/product/company-research/projects/{project_id}/memo
POST  /api/underwriting/v1/product/company-research/projects/{project_id}/publication-preview
POST  /api/underwriting/v1/product/company-research/projects/{project_id}/publish
GET   /api/underwriting/v1/product/company-research/projects/{project_id}/revisions/{revision_id}/export
```

Memo input is closed and optimistic:

```python
class PatchCompanyResearchMemoRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    markdown: StrictStr = Field(min_length=1, max_length=100_000)
```

The service normalizes line endings, rejects whitespace-only text, appends one immutable `memo` artifact, and CAS-updates only `memo_ref` and canonical model refs in the draft. It never lets the browser submit artifact IDs.
- [ ] Publication preview returns answerability, direction/confidence when allowed, value/return ranges, blockers, strongest counterevidence, next checks, exact cutoff, and manifest hash. It performs zero writes and does not commit/rollback or autoflush caller pending state.
- [ ] Publish requires `Idempotency-Key` and the preview's exact draft lock/manifest hash. The UI must show a confirmation dialog naming Company, Securities, cutoff, answerability, value range, strongest counterevidence, and the fact that the version becomes immutable.
- [ ] Export returns a closed JSON envelope with `filename`, `media_type="text/markdown"`, `content`, and SHA-256 `content_hash`. Recompute and verify the hash client-side before offering the `.md` download. The document includes source locators, gaps, assumptions, model version, exact Security values, assessment, memo, revision ID, and cutoff; it never silently substitutes current heads.
- [ ] After a successful freeze, show the frozen revision and ChangeSet. A second cutoff creates successor artifacts/revision and the old selected revision remains byte-for-byte and semantically unchanged.
- [ ] Regenerate OpenAPI and TypeScript through temporary files, preserving the user's OpenAPI patch. Add strict runtime guards for all new DTOs and success statuses.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_publication.py tests/underwriting/test_company_research_api.py tests/underwriting/test_revision_publisher.py tests/underwriting/test_research_revision_diff.py -q
cd ../frontend
npm test -- InvestmentResearchApi.test.ts ResearchWorkbenchPage.test.tsx
npm run typecheck
npm run build
```

- [ ] Commit without `frontend/openapi.json`.

```bash
git add backend/app/underwriting/api/company_research_schemas.py backend/app/underwriting/api/company_research_router.py backend/app/underwriting/services/company_research_publication.py backend/tests/underwriting/test_company_research_publication.py backend/tests/underwriting/test_company_research_api.py frontend/src/contracts/v1.ts frontend/src/data/investmentResearchApi.ts frontend/src/data/InvestmentResearchApi.test.ts frontend/src/features/investment-research/ResearchWorkbenchPage.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx
git commit -m "feat: complete company research publication flow"
```

## Task 14: Prove point-to-surface reuse with CATL

**Files:**

- Create: `backend/app/underwriting/adapters/company_research/catl.py`
- Create: `backend/tests/underwriting/test_company_research_adapter_reuse.py`
- Modify: `backend/app/underwriting/adapters/company_research/__init__.py`
- Modify: `backend/app/underwriting/fixtures/catl_baseline/manifest.json`
- Modify: `backend/tests/underwriting/test_company_research_api.py`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`

- [ ] Write RED reuse tests before adding CATL-specific mapping. Instantiate the same initializer, preparation service, engine, workbench service, publisher, and UI DTOs for Alphabet and CATL. Only the adapter and fixture differ.
- [ ] CATL adapter owns these business terms:

```python
CATL_BUSINESS_MODULES = (
    "power_battery",
    "energy_storage",
    "battery_materials_and_recycling",
    "overseas_capacity",
    "customer_and_technology_platform",
    "corporate_capital_allocation",
)
```

It maps operating drivers such as shipment, utilization, price, material cost, product mix, overseas ramp, and customer certification without adding battery-specific fields to generic artifact tables or workbench DTOs.
- [ ] CATL uses CNY Security and capital data, so the exact required FX set is empty. Alphabet retains exact USD/CNY FX. Both use the same financial-closure, scenario-discipline, valuation, answerability, publication, and replay code paths.
- [ ] Add an architectural test that rejects imports of `alphabet` or `catl` adapters from generic domain/persistence/services. The adapter registry may import both; the core may only depend on the `CompanyResearchAdapter` protocol.
- [ ] Record any generic seam forced by CATL in the design document's decision log. Do not add standalone Industry or Geography project APIs.

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting/test_company_research_adapter_reuse.py tests/underwriting/test_company_research_api.py tests/underwriting/test_alphabet_golden_case.py -q
cd ../frontend
npm test -- ResearchWorkbenchPage.test.tsx NewResearchPage.test.tsx
```

- [ ] Commit.

```bash
git add backend/app/underwriting/adapters/company_research/catl.py backend/app/underwriting/adapters/company_research/__init__.py backend/tests/underwriting/test_company_research_adapter_reuse.py backend/app/underwriting/fixtures/catl_baseline/manifest.json backend/tests/underwriting/test_company_research_api.py frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx docs/superpowers/specs/2026-08-25-alphabet-golden-case-company-research-design.md
git commit -m "feat: prove company research kernel reuse with CATL"
```

## Task 15: Release gate, runtime proof, and documentation

**Files:**

- Modify: `backend/scripts/verify_one_click_runtime.py`
- Modify: `backend/tests/test_investment_research_runtime.py`
- Modify: `docs/architecture/independent-investment-research.md`
- Modify: `README.md`

- [ ] Add a RED runtime contract that fails unless a fresh database reaches Alembic `0067`, loads foundation identities/aliases, reports a healthy `company-research-worker`, resolves `Google`, initializes Alphabet once, returns the nine-module workbench, freezes a deterministic golden revision when all required frozen inputs are present, and strictly replays it after restart.
- [ ] Extend backup/restore verification so company preparation rows, immutable artifact chains, generic jobs/events, and frozen revision model refs survive restore and pass strict replay. Runtime scripts must keep the existing volume/database ownership, tar limits, TOCTOU, Bash 3, and recovery protections.
- [ ] Update architecture documentation with:

```text
user-facing company research flow
hidden audit foundation
company adapter boundary
artifact schemas and lineage
preparation state machine
answerability/publication gates
Alphabet evidence and fixture hashes
CATL reuse result
known gaps and next source cutoff procedure
```

- [ ] Run the complete verification gate before claiming completion.

Backend:

```bash
cd /Users/xiongjiali/code/fund-engine/backend
uv run pytest tests/underwriting -q
uv run pytest -q
uv run ruff check app tests
uv run ruff format --check app tests
python -m compileall -q app
```

Frontend:

```bash
cd /Users/xiongjiali/code/fund-engine/frontend
npm test
npm run typecheck
npm run build
```

Repository integrity:

```bash
cd /Users/xiongjiali/code/fund-engine
git diff --check
git status --short
```

Expected remaining unstaged files are only the user's protected changes. Confirm `frontend/openapi.json` still has the same 14 `Unprocessable Entity` to `Unprocessable Content` edits and no feature-owned drift.

- [ ] Run a disposable, uniquely named Docker project with unique ports, images, database volume, files volume, and operation IDs. Verify fresh start, worker health, Google search, Alphabet initialization, workbench progression, freeze/replay, backup, restore, restart, and exact cleanup. Never stop or mutate existing user Docker projects or volumes.
- [ ] Request a fresh specification review and a fresh quality review. Fix all P0–P2 findings with new RED tests; rerun the relevant focused suites and the complete release gate.
- [ ] Commit release evidence only after all commands pass.

```bash
git add backend/scripts/verify_one_click_runtime.py backend/tests/test_investment_research_runtime.py docs/architecture/independent-investment-research.md README.md
git commit -m "docs: verify Alphabet company research release"
```

## Final self-review checklist

- [ ] Spec coverage: every item in design sections 3, 5–14 maps to at least one task and one executable test.
- [ ] Product language: primary UI never asks users to author Mandate, Scope, Agenda, Boundary, UUID, source hash, raw hash, price, FX, capital structure, or rights-ledger fields.
- [ ] Identity: Alphabet is the Company; Google is a brand/business alias; GOOGL and GOOG remain distinct Securities.
- [ ] Context: Industry and Geography are company-research dimensions only; no standalone project routes were added.
- [ ] Evidence: no unsupported fact is formal, every formal number has exact source/cutoff/unit/currency/state, and external failure cannot fall back to mock.
- [ ] Modeling: financial bridge closes, Base/Bull/Bear mechanisms differ, valuation is not probability-weighted, reverse DCF exposes implied assumptions.
- [ ] Judgment: answerability, direction, confidence, and publication status stay orthogonal; `not_answerable` has null direction/confidence.
- [ ] History: exact artifact and market refs are frozen; later heads cannot change historical reads; parent replay is iterative and tamper-evident.
- [ ] Reuse: CATL changes adapters/data only; generic core contains no Alphabet/CATL vocabulary.
- [ ] Placeholder scan:

```bash
cd /Users/xiongjiali/code/fund-engine
rg -n "TODO|TBD|FIXME|placeholder|mock target|coming soon|尚未实现" backend/app/underwriting backend/tests/underwriting frontend/src/features/investment-research frontend/src/data/investmentResearchApi.ts
```

Every match must be either removed or asserted as an explicit user-visible ResearchGap state; no plan step is complete with a hidden placeholder.
- [ ] Type consistency: Python domain enums, Pydantic wire literals, OpenAPI schemas, generated TypeScript unions, runtime guards, and UI discriminants have the same exact values.
- [ ] Protected changes: `frontend/openapi.json` and `docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md` remain user-owned unless the user separately authorizes them.
