# Investment Research Product Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Increment A: an independent product entry backed by ResearchProject, independent research/market boundaries, optimistic drafts and atomic immutable ResearchRevision publication without invalidating existing underwriting history.

**Architecture:** Preserve legacy kernel rows and hash recipes as read-only compatibility. New product services write additive project/scope/agenda/snapshot/boundary/manifest tables, reuse existing evidence-basis and research-version identities, and mark new revisions with a product manifest schema. A minimal React shell exercises the real product API from search through an `insufficient_evidence` foundation revision; it does not expose legacy Event Research or pretend that company modeling and valuation are complete.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest; React 18, TypeScript, React Router, Vitest, generated OpenAPI TypeScript; Docker Compose and shell scripts.

---

## File map

### Create

- `backend/app/underwriting/domain/product_contracts.py` — product enums and immutable input contracts.
- `backend/app/underwriting/persistence/product_models.py` — product aggregate persistence rows.
- `backend/app/underwriting/persistence/product_repository.py` — focused repositories and lock operations.
- `backend/app/underwriting/services/product_project.py` — project, mandate, scope, agenda and HistoricalBasis use cases.
- `backend/app/underwriting/services/market_snapshots.py` — market snapshot normalization and freezing.
- `backend/app/underwriting/services/workspace_draft.py` — draft create/read/compare-and-swap.
- `backend/app/underwriting/services/revision_publisher.py` — preview, fail-closed assessment, gates, manifest and atomic publish.
- `backend/app/underwriting/api/product_schemas.py` — strict product DTOs.
- `backend/app/underwriting/api/product_router.py` — product endpoints only.
- `backend/app/underwriting/api/transactions.py` — shared commit/rollback and HTTP error translation.
- `backend/alembic/versions/0065_investment_research_product_foundation.py` — additive schema and immutable triggers.
- `backend/tests/underwriting/test_product_contracts.py`
- `backend/tests/underwriting/test_product_persistence.py`
- `backend/tests/underwriting/test_product_project.py`
- `backend/tests/underwriting/test_market_snapshots.py`
- `backend/tests/underwriting/test_workspace_draft.py`
- `backend/tests/underwriting/test_revision_publisher.py`
- `backend/tests/underwriting/test_product_api.py`
- `backend/tests/underwriting/test_product_legacy_compatibility.py`
- `backend/app/scripts/verify_underwriting_revision_manifests.py` — restore-time product manifest verification.
- `frontend/src/app/InvestmentResearchShell.tsx`
- `frontend/src/data/investmentResearchApi.ts`
- `frontend/src/features/investment-research/ResearchHomePage.tsx`
- `frontend/src/features/investment-research/NewResearchPage.tsx`
- `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- `frontend/src/features/investment-research/InvestmentResearchShell.test.tsx`
- `frontend/src/features/investment-research/NewResearchPage.test.tsx`
- `frontend/src/data/InvestmentResearchApi.test.ts`
- `scripts/verify-investment-research-foundation.sh`

### Modify

- `backend/app/underwriting/domain/types.py` — keep legacy types; re-export only stable shared enums.
- `backend/app/underwriting/persistence/models.py` — nullable product compatibility columns on mandate/research-version rows.
- `backend/app/underwriting/persistence/__init__.py` — register product models.
- `backend/app/underwriting/api/router.py` — include the product router; no product handlers inline.
- `backend/app/main.py` — only if router registration is not already transitive.
- `backend/app/underwriting/services/research_revision_diff.py` — schema-dispatched product manifest reading.
- `backend/tests/underwriting/test_kernel_persistence.py` — metadata registration.
- `backend/tests/underwriting/test_kernel_postgres.py` — 0065 migration and trigger coverage.
- `backend/tests/underwriting/test_openapi_dump.py` — product path/schema guards.
- `frontend/src/app/routes.tsx` — mount the independent `/research` shell.
- `frontend/openapi.json` and `frontend/src/contracts/v1.ts` — generated contracts.
- `docker-compose.one-click.yml` — file-store volume and optional product runtime environment.
- `scripts/one-click-runtime.sh` — `backup` and `restore` commands.
- `scripts/verify-one-click-runtime.sh` — current migration and product-route checks.
- `docs/architecture/underwriting-research.md` — legacy/product schema dispatch and publication invariant.

## Task 1: Freeze the legacy compatibility boundary

**Files:**

- Create: `backend/tests/underwriting/test_product_legacy_compatibility.py`
- Modify: `docs/architecture/underwriting-research.md`

- [x] **Step 1: Write the legacy replay tests before adding product tables.** Seed the existing CATL evidence-only fixture, read its revision summary, boundary and diff twice, then assert the current v3 content hash and serialized response are deterministic. Task 7 extends this same test after product rows exist.

```python
def test_legacy_v3_revision_ignores_product_boundaries(session, catl_revision) -> None:
    reader = ResearchRevisionDiffService(session)
    before = reader.revision_summary(catl_revision.id)
    after = reader.revision_summary(catl_revision.id)
    assert after == before
    assert after.content_hash == catl_revision.content_hash
    assert [ref.reference for ref in after.parent_refs] == sorted(
        ref.reference for ref in before.parent_refs
    )
```

- [x] **Step 2: Run the focused baseline and record GREEN.**

Run: `cd backend && pytest -q tests/underwriting/test_product_legacy_compatibility.py tests/underwriting/test_historical_replay.py tests/underwriting/test_research_revision_diff.py`

Expected: PASS before product implementation; this is the non-regression baseline.

- [x] **Step 3: Add explicit architecture invariants.** Document that legacy v1-v3 revision hashes continue to include the stored legacy basis fields, while new product revisions use `underwriting.research-revision-manifest.v1` and never reinterpret or backfill a legacy hash.

- [x] **Step 4: Add a forbidden compatibility dependency assertion.** Reject imports from the future product service modules in the legacy hash and reader modules. The migration-specific no-backfill assertion is added in Task 3 after migration 0065 exists.

```python
kernel_source = Path("app/underwriting/services/kernel.py").read_text()
assert "revision_publisher" not in kernel_source
assert "workspace_draft" not in kernel_source
```

- [x] **Step 5: Commit the compatibility guard.**

```bash
git add backend/tests/underwriting/test_product_legacy_compatibility.py docs/architecture/underwriting-research.md
git commit -m "test: freeze legacy underwriting revision compatibility"
```

## Task 2: Define the product contracts and classification axes

**Files:**

- Create: `backend/app/underwriting/domain/product_contracts.py`
- Create: `backend/tests/underwriting/test_product_contracts.py`
- Modify: `backend/app/underwriting/domain/__init__.py`

- [x] **Step 1: Write RED tests for controlled values and cross-field rules.** Cover enum rejection, timezone requirements, ISO currency, Security-bound price, nonzero FX, positive diluted shares, mutually valid periods and `not_answerable` having null direction/confidence.

```python
def test_not_answerable_cannot_carry_direction_or_confidence() -> None:
    with pytest.raises(ValueError, match="direction and confidence must be null"):
        AssessmentState(
            answerability=AnswerabilityState.NOT_ANSWERABLE,
            direction=AssessmentDirection.PROVISIONAL_BULLISH,
            confidence=AssessmentConfidence.LOW,
            publication_status=PublicationStatus.DRAFT,
        )
```

- [x] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_product_contracts.py`

Expected: import failure for `app.underwriting.domain.product_contracts`.

- [x] **Step 3: Implement the controlled enums.**

```python
class ValueNature(StrEnum):
    ACTUAL = "actual"
    FORECAST = "forecast"
    DERIVED = "derived"
    ASSUMPTION = "assumption"

class Provenance(StrEnum):
    COMPANY_GUIDANCE = "company_guidance"
    CONSENSUS = "consensus"
    HOUSE = "house"
    MARKET_IMPLIED = "market_implied"
    THIRD_PARTY = "third_party"
    SOURCE_REPORTED = "source_reported"

class ScenarioKey(StrEnum):
    BASE = "base"
    BULL = "bull"
    BEAR = "bear"

class EpistemicStatus(StrEnum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"

class ReviewStatus(StrEnum):
    CANDIDATE = "candidate"
    SELF_REVIEWED = "self_reviewed"
    ADOPTED = "adopted"
    CHALLENGED = "challenged"
    SUPERSEDED = "superseded"

class AssessmentDirection(StrEnum):
    PROVISIONAL_BULLISH = "provisional_bullish"
    PROVISIONAL_NEUTRAL = "provisional_neutral"
    PROVISIONAL_CAUTIOUS = "provisional_cautious"

class AssessmentConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class PublicationStatus(StrEnum):
    DRAFT = "draft"
    USER_FROZEN = "user_frozen"
    SUPERSEDED = "superseded"
```

- [x] **Step 4: Implement frozen value objects.** Define `ResearchScopeInput`, `ResearchAgendaInput`, `ProductHistoricalBasisInput`, `PriceSnapshotInput`, `FXSnapshotInput`, `CapitalStructureSnapshotInput`, `SecurityRightsInput`, `AssessmentState`, `RevisionBoundaryInput` and `ProductRevisionView`. Normalize datetimes to UTC only in services; domain objects reject naive values.

```python
@dataclass(frozen=True, slots=True)
class ProductHistoricalBasisInput:
    cutoff_at: datetime
    source_manifest_hash: str
    definition_bundle_hash: str
    parser_bundle_hash: str

@dataclass(frozen=True, slots=True)
class RevisionBoundaryInput:
    historical_basis_id: UUID
    mandate_id: UUID
    scope_id: UUID
    agenda_id: UUID
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    parent_revision_id: UUID | None

@dataclass(frozen=True, slots=True)
class ProductRevisionView:
    id: UUID
    project_id: UUID
    boundary_id: UUID
    manifest_hash: str
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus

@dataclass(frozen=True, slots=True)
class AssessmentState:
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus

    def __post_init__(self) -> None:
        if self.answerability is AnswerabilityState.NOT_ANSWERABLE and (
            self.direction is not None or self.confidence is not None
        ):
            raise ValueError("direction and confidence must be null when not_answerable")
```

- [x] **Step 5: Confirm GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_product_contracts.py tests/underwriting/test_domain_types.py`

Expected: PASS.

```bash
git add backend/app/underwriting/domain/product_contracts.py backend/app/underwriting/domain/__init__.py backend/tests/underwriting/test_product_contracts.py
git commit -m "feat: define investment research product contracts"
```

## Task 3: Add the additive product schema and immutable guards

**Files:**

- Create: `backend/alembic/versions/0065_investment_research_product_foundation.py`
- Create: `backend/app/underwriting/persistence/product_models.py`
- Modify: `backend/app/underwriting/persistence/models.py`
- Modify: `backend/app/underwriting/persistence/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Modify: `backend/tests/underwriting/test_kernel_persistence.py`
- Modify: `backend/tests/underwriting/test_kernel_postgres.py`
- Modify: `backend/tests/test_sqlite_migration_bootstrap.py`
- Create: `backend/tests/underwriting/test_product_persistence.py`

- [x] **Step 1: Write RED metadata and migration tests.** Require these tables:

```python
PRODUCT_TABLES = {
    "uw_object_identity_versions",
    "uw_research_projects",
    "uw_research_project_securities",
    "uw_research_scope_versions",
    "uw_research_agenda_versions",
    "uw_price_snapshots",
    "uw_fx_snapshots",
    "uw_capital_structure_snapshots",
    "uw_security_rights_versions",
    "uw_research_assessment_versions",
    "uw_workspace_drafts",
    "uw_revision_boundaries",
    "uw_revision_manifests",
}
```

Assert every table except `uw_workspace_drafts` is registered in `IMMUTABLE_TABLES`; register drafts in a separate delete-protected set so controlled optimistic-lock UPDATE remains possible but application and PostgreSQL paths reject DELETE.

- [x] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_product_persistence.py tests/underwriting/test_kernel_persistence.py`

Expected: FAIL because the product tables are absent.

- [x] **Step 3: Write migration 0065.** Create the thirteen tables with UUID primary keys, explicit foreign keys, hashes, timestamps and checks. `uw_object_identity_versions` freezes symbol, exchange, share class, trading currency and effective interval for a Company/Security identity; `uw_research_project_securities` has a unique `(project_id, security_id)` pair and makes multi-Security project membership relational rather than opaque JSON. `uw_research_assessment_versions` stores the immutable answerability/direction/confidence/publication tuple; Increment A permits only the fail-closed `not_answerable` shape, while Increment C adds the gates that can produce provisional directions. Add nullable compatibility columns to existing tables without backfilling existing rows:

```text
uw_mandate_versions:
  project_id, benchmark_key, required_excess_return, effective_at, expires_at, content_hash

uw_historical_bases:
  definition_bundle_hash, parser_bundle_hash, boundary_schema_version, content_hash

uw_research_versions:
  project_id, boundary_id, manifest_id, manifest_schema, publication_status
```

New product services require all target fields. Null means a legacy row and remains readable by the legacy schema path.

`uw_revision_manifests` also stores `idempotency_key` with a unique constraint on `(project_id, idempotency_key)`, so publication retry can return the original revision rather than create another row.

```python
op.create_table(
    "uw_object_identity_versions",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("object_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("canonical_name", sa.Text(), nullable=False),
    sa.Column("symbol", sa.String(48)),
    sa.Column("exchange", sa.String(32)),
    sa.Column("share_class", sa.String(64)),
    sa.Column("trading_currency", sa.String(3)),
    sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
    sa.Column("effective_to", sa.DateTime(timezone=True)),
    sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_object_identity_versions.id")),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("object_id", "version", name="uq_uw_object_identity_version"),
)

op.create_table(
    "uw_research_project_securities",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("uw_research_projects.id"), nullable=False),
    sa.Column("security_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("project_id", "security_id", name="uq_uw_project_security"),
)

op.create_table(
    "uw_research_assessment_versions",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("uw_research_projects.id"), nullable=False),
    sa.Column("answerability", sa.String(32), nullable=False),
    sa.Column("direction", sa.String(32)),
    sa.Column("confidence", sa.String(16)),
    sa.Column("publication_status", sa.String(24), nullable=False),
    sa.Column("blockers", sa.JSON(), nullable=False),
    sa.Column("resolution_requirements", sa.JSON(), nullable=False),
    sa.Column("next_review_at", sa.DateTime(timezone=True)),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "answerability <> 'not_answerable' OR (direction IS NULL AND confidence IS NULL)",
        name="ck_uw_product_assessment_fail_closed",
    ),
)

op.create_table(
    "uw_revision_manifests",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("uw_research_projects.id"), nullable=False),
    sa.Column("boundary_id", sa.Uuid(), sa.ForeignKey("uw_revision_boundaries.id"), nullable=False),
    sa.Column("idempotency_key", sa.String(120), nullable=False),
    sa.Column("manifest", sa.JSON(), nullable=False),
    sa.Column("content_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("project_id", "idempotency_key", name="uq_uw_revision_manifest_idempotency"),
)
```

- [x] **Step 4: Implement ORM rows and checks.** `UnderwritingWorkspaceDraft` has `lock_version`, `content`, `base_revision_id`, `created_at`, `updated_at`; all other new rows have `content_hash` and are append-only. Add unique constraints for snapshot natural identities and one successor per versioned family.

```python
class UnderwritingWorkspaceDraft(Base):
    __tablename__ = "uw_workspace_drafts"
    __table_args__ = (UniqueConstraint("project_id", name="uq_uw_workspace_draft_project"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("uw_research_projects.id"), nullable=False)
    base_revision_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("uw_research_versions.id"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Add a migration-source assertion now that 0065 exists:

```python
sql = Path("alembic/versions/0065_investment_research_product_foundation.py").read_text()
for statement in ("UPDATE uw_historical_bases", "DELETE FROM uw_research_versions"):
    assert statement not in sql
```

- [x] **Step 5: Verify SQLite/PostgreSQL migration and commit.**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_product_persistence.py tests/underwriting/test_kernel_persistence.py
pytest -q tests/underwriting/test_kernel_postgres.py -k 0065
```

Expected: SQLite tests PASS; PostgreSQL test PASS when `TEST_DATABASE_URL` exists, otherwise marked skipped by `pg_only`.

```bash
git add backend/alembic/versions/0065_investment_research_product_foundation.py backend/app/models/ledger.py backend/app/underwriting/persistence backend/tests/underwriting/test_product_persistence.py backend/tests/underwriting/test_kernel_persistence.py backend/tests/underwriting/test_kernel_postgres.py
git commit -m "feat: persist investment research product foundation"
```

## Task 4: Implement projects, mandates, scopes, agendas and product HistoricalBasis

**Files:**

- Create: `backend/app/underwriting/persistence/product_repository.py`
- Create: `backend/app/underwriting/services/product_project.py`
- Create: `backend/tests/underwriting/test_product_project.py`

- [x] **Step 1: Write RED service tests.** Cover effective Company/Security identity versions, company/security relation validation, multi-Security project membership, Industry search redirect semantics, mandate successor conflicts, scope identity, agenda provenance and product basis writes with `price_as_of is None`.

```python
def test_product_basis_never_writes_legacy_price_as_of(session, product_service) -> None:
    basis = product_service.create_historical_basis(
        ProductHistoricalBasisInput(CUTOFF, A64, B64, C64)
    )
    assert basis.price_as_of is None
    assert basis.boundary_schema_version == "product.historical-basis.v1"
```

- [x] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_product_project.py`

Expected: import failure for the new repository/service.

- [x] **Step 3: Implement focused repository methods.** Provide `append_identity_version`, `create_project`, `project`, `list_projects`, `search_objects`, `append_scope`, `append_agenda`, `append_product_mandate`, `create_product_basis` and exact expected-parent checks. Search resolves the identity version effective at the requested `as_of` time and returns persisted Industry/Company/Security identities only; it does not query legacy Event/Theme fixtures.

```python
class ProductRepository:
    def _append_versioned(self, *, model, family: dict[str, object], payload: dict[str, object],
                          content_hash: str, expected_parent_id: UUID | None,
                          created_at: datetime):
        statement = select(model).filter_by(**family).order_by(model.version.desc()).with_for_update()
        head = self._session.scalar(statement)
        actual_parent_id = head.id if head is not None else None
        if actual_parent_id != expected_parent_id:
            raise StaleParentError("version family head changed")
        row = model(
            **family, version=1 if head is None else head.version + 1, payload=payload,
            content_hash=content_hash, supersedes_id=actual_parent_id, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def create_project(self, *, primary_company_id: UUID,
                       target_security_ids: tuple[UUID, ...],
                       created_at: datetime) -> UnderwritingResearchProject:
        row = UnderwritingResearchProject(primary_company_id=primary_company_id, created_at=created_at)
        self._session.add(row)
        self._session.flush()
        for security_id in target_security_ids:
            self._session.add(UnderwritingResearchProjectSecurity(
                project_id=row.id,
                security_id=security_id,
                content_hash=canonical_hash({"project_id": str(row.id), "security_id": str(security_id)}),
                created_at=created_at,
            ))
        self._session.flush()
        return row

    def search_objects(self, *, query: str, limit: int) -> list[UnderwritingResearchObject]:
        normalized = query.strip().casefold()
        statement = select(UnderwritingResearchObject).where(
            func.lower(UnderwritingResearchObject.canonical_name).contains(normalized)
            | func.lower(UnderwritingResearchObject.external_key).contains(normalized)
        ).order_by(UnderwritingResearchObject.kind, UnderwritingResearchObject.external_key).limit(limit)
        return list(self._session.scalars(statement))

    def append_scope(self, *, project_id: UUID, payload: dict[str, object], content_hash: str,
                     expected_parent_id: UUID | None, created_at: datetime) -> UnderwritingResearchScopeVersion:
        return self._append_versioned(
            model=UnderwritingResearchScopeVersion, family={"project_id": project_id}, payload=payload,
            content_hash=content_hash, expected_parent_id=expected_parent_id, created_at=created_at,
        )

    def append_agenda(self, *, project_id: UUID, payload: dict[str, object], content_hash: str,
                      expected_parent_id: UUID | None, created_at: datetime) -> UnderwritingResearchAgendaVersion:
        return self._append_versioned(
            model=UnderwritingResearchAgendaVersion, family={"project_id": project_id}, payload=payload,
            content_hash=content_hash, expected_parent_id=expected_parent_id, created_at=created_at,
        )
```

- [x] **Step 4: Implement service validation and canonical hashes.** Require a Company primary object and at least one related target Security. An Industry result is selectable for browsing but cannot create a project until Company and Security are confirmed. Agenda provenance must be either deterministic template metadata or complete AI provenance.

```python
agenda_hash = canonical_hash({
    "schema_version": "product.research-agenda.v1",
    "project_id": str(project_id),
    "items": [item.canonical_dict() for item in value.items],
    "generator": value.generator.canonical_dict(),
})
```

- [x] **Step 5: Run GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_product_project.py tests/underwriting/test_kernel_service.py tests/underwriting/test_isolation_contract.py`

Expected: PASS; isolation test shows no imports from Event Research, funds, tenant or simulated-position modules.

```bash
git add backend/app/underwriting/persistence/product_repository.py backend/app/underwriting/services/product_project.py backend/tests/underwriting/test_product_project.py
git commit -m "feat: create investment research projects and scopes"
```

## Task 5: Freeze independent market snapshots

**Files:**

- Create: `backend/app/underwriting/services/market_snapshots.py`
- Create: `backend/tests/underwriting/test_market_snapshots.py`
- Modify: `backend/app/underwriting/persistence/product_repository.py`

- [x] **Step 1: Write RED validation tests.** Cover target Security identity, timezone-aware market/available times, positive prices, ISO currency, FX quotation direction, positive diluted shares, company-action basis and rights effective intervals. Prove a price after the evidence cutoff is accepted without changing HistoricalBasis.

```python
def test_price_may_follow_evidence_cutoff_without_future_evidence(session, services) -> None:
    basis = services.projects.create_historical_basis(product_basis(cutoff=T1))
    price = services.market.freeze_price(price_input(market_time=T1 + timedelta(days=15)))
    assert price.market_time > basis.cutoff
    assert basis.price_as_of is None
```

- [x] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_market_snapshots.py`

Expected: import failure for `market_snapshots`.

- [x] **Step 3: Implement snapshot normalization.** Quantize decimal strings without float conversion, normalize currency codes, hash provider identity plus raw content hash, and deduplicate exact natural identities. Never silently convert currency or infer corporate-action adjustments.

```python
def normalized_decimal(value: Decimal, field: str) -> str:
    if not value.is_finite() or value <= 0:
        raise ValidationError(f"{field} must be finite and positive")
    return format(value.normalize(), "f")

content_hash = canonical_hash({
    "schema_version": "product.price-snapshot.v1",
    "security_id": str(value.security_id),
    "price": normalized_decimal(value.price, "price"),
    "currency": value.currency,
    "market_time": value.market_time.astimezone(UTC).isoformat(),
    "available_at": value.available_at.astimezone(UTC).isoformat(),
    "source_id": value.source_id,
    "raw_content_hash": value.raw_content_hash,
})
```

- [x] **Step 4: Implement cross-snapshot checks.** A `RevisionBoundary` may reference only snapshots for its project Company/target Securities; FX is required when market or model currency differs from the mandate base currency; every target Security needs one effective SecurityRightsVersion.

```python
@dataclass(frozen=True, slots=True)
class BoundaryContext:
    target_security_ids: tuple[UUID, ...]
    price_security_ids: tuple[UUID, ...]
    rights_security_ids: tuple[UUID, ...]
    requires_fx: bool

def validate_market_coverage(boundary: RevisionBoundaryInput, context: BoundaryContext) -> None:
    if set(context.price_security_ids) != set(context.target_security_ids):
        raise ValidationError("every target security requires exactly one price snapshot")
    if context.requires_fx and not boundary.fx_snapshot_ids:
        raise ValidationError("fx snapshot is required for cross-currency valuation")
    if set(context.rights_security_ids) != set(context.target_security_ids):
        raise ValidationError("every target security requires effective rights")
```

- [x] **Step 5: Run GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_market_snapshots.py tests/underwriting/test_historical_replay.py`

Expected: PASS; legacy historical replay remains unchanged.

```bash
git add backend/app/underwriting/services/market_snapshots.py backend/app/underwriting/persistence/product_repository.py backend/tests/underwriting/test_market_snapshots.py
git commit -m "feat: freeze independent investment market snapshots"
```

## Task 6: Add optimistic WorkspaceDraft operations

**Files:**

- Create: `backend/app/underwriting/services/workspace_draft.py`
- Create: `backend/tests/underwriting/test_workspace_draft.py`
- Modify: `backend/app/underwriting/persistence/product_repository.py`

- [x] **Step 1: Write RED tests for create, save and stale writes.**

```python
def test_stale_draft_save_is_rejected(session, draft_service, project) -> None:
    created = draft_service.create(project.id)
    saved = draft_service.save(project.id, expected_lock_version=1, patch={"user_focus": "overseas"})
    assert saved.lock_version == 2
    with pytest.raises(ConflictError, match="draft changed"):
        draft_service.save(project.id, expected_lock_version=1, patch={"user_focus": "storage"})
```

- [x] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_workspace_draft.py`

Expected: import failure for `workspace_draft`.

- [x] **Step 3: Implement an allowlisted patch contract.** Draft content may contain only IDs and user-authored draft fields defined by `WorkspaceDraftContent`; reject arbitrary nested keys, formal status claims and any `publication_status=user_frozen` input.

```python
class WorkspaceDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_id: UUID | None = None
    scope_id: UUID | None = None
    agenda_id: UUID | None = None
    historical_basis_id: UUID | None = None
    price_snapshot_ids: tuple[UUID, ...] | None = None
    fx_snapshot_ids: tuple[UUID, ...] | None = None
    capital_structure_snapshot_id: UUID | None = None
    security_rights_ids: tuple[UUID, ...] | None = None
    user_focus: str | None = None
```

- [x] **Step 4: Implement one-statement compare-and-swap.** Execute `UPDATE ... WHERE project_id=:id AND lock_version=:expected`, increment lock version, set `updated_at`, and require `rowcount == 1`. Draft deletion is not exposed; a published revision resets the existing draft to a new base through the same compare-and-swap path.

```python
statement = (
    update(UnderwritingWorkspaceDraft)
    .where(
        UnderwritingWorkspaceDraft.project_id == project_id,
        UnderwritingWorkspaceDraft.lock_version == expected_lock_version,
    )
    .values(content=content, lock_version=expected_lock_version + 1, updated_at=updated_at)
)
if self._session.execute(statement).rowcount != 1:
    raise ConflictError("workspace draft changed; reload before saving")
```

- [x] **Step 5: Run GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_workspace_draft.py tests/underwriting/test_product_persistence.py`

Expected: PASS.

```bash
git add backend/app/underwriting/services/workspace_draft.py backend/app/underwriting/persistence/product_repository.py backend/tests/underwriting/test_workspace_draft.py
git commit -m "feat: add optimistic investment research drafts"
```

## Task 7: Preview and atomically publish ResearchRevision

**Files:**

- Create: `backend/app/underwriting/services/revision_publisher.py`
- Create: `backend/tests/underwriting/test_revision_publisher.py`
- Modify: `backend/app/underwriting/persistence/product_repository.py`
- Modify: `backend/app/underwriting/services/research_revision_diff.py`

- [ ] **Step 1: Write RED publication tests.** Cover preview determinism, missing boundary members, cross-project references, stale draft lock, idempotent retry, partial insert rollback, successor lineage and legacy reader compatibility.

```python
def test_publish_is_atomic_when_manifest_insert_fails(session, publisher, ready_draft, monkeypatch) -> None:
    monkeypatch.setattr(publisher.repository, "append_manifest", raising_integrity_error)
    with pytest.raises(ConflictError):
        publisher.publish(ready_draft.project_id, ready_draft.lock_version, idempotency_key="publish-1")
    assert count(session, UnderwritingResearchAssessmentVersion) == 0
    assert count(session, UnderwritingRevisionBoundary) == 0
    assert count_product_revisions(session) == 0
```

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_revision_publisher.py`

Expected: import failure for `revision_publisher`.

- [ ] **Step 3: Implement canonical boundary and manifest builders.**

```python
manifest = {
    "schema_version": "underwriting.research-revision-manifest.v1",
    "project_id": str(project.id),
    "primary_object_id": str(project.primary_company_id),
    "mandate_id": str(boundary.mandate_id),
    "scope_id": str(boundary.scope_id),
    "agenda_id": str(boundary.agenda_id),
    "historical_basis_id": str(boundary.historical_basis_id),
    "market_snapshot_refs": sorted(boundary.market_refs()),
    "model_refs": sorted(draft.model_refs),
    "assessment_ref": str(assessment.id),
    "memo_ref": draft.memo_ref,
    "parent_revision_id": str(boundary.parent_revision_id) if boundary.parent_revision_id else None,
}
```

The foundation permits empty model and memo refs only when the referenced assessment is `not_answerable`; `assessment_ref` is always required. The UI maps this fail-closed shape to `insufficient_evidence`. Increment A cannot persist a provisional direction.

- [ ] **Step 4: Implement preview and publish.** Preview runs identity, cutoff, snapshot, scope, mandate and legacy-boundary gates without writes and returns the canonical fail-closed Assessment payload. Publish begins by locking the project row, then checks `project_id + idempotency_key`; this serializes concurrent retries before they inspect the draft. It repeats the gates inside the transaction and inserts assessment → boundary → manifest → `UnderwritingResearchVersion(version_kind="independent_research")`, then compare-and-swap resets the draft base. Retry returns the same revision ID.

```python
def publish(self, project_id: UUID, expected_lock_version: int, idempotency_key: str):
    self._repository.lock_project(project_id)
    if existing := self._repository.revision_for_idempotency(project_id, idempotency_key):
        return existing
    draft = self._drafts.require(project_id, expected_lock_version)
    preview = self.preview(project_id, expected_lock_version)
    assessment = self._repository.append_assessment(preview.assessment)
    boundary = self._repository.append_boundary(preview.boundary)
    manifest_payload = preview.manifest.with_refs(
        assessment_id=assessment.id,
        boundary_id=boundary.id,
    )
    manifest = self._repository.append_manifest(boundary.id, idempotency_key, manifest_payload)
    revision = self._repository.append_product_revision(manifest, draft.base_revision_id)
    self._drafts.reset_after_publish(draft, revision.id)
    return revision
```

- [ ] **Step 5: Extend the reader by manifest schema dispatch.** Legacy rows keep the v3 hash path. New rows require project/boundary/manifest IDs, recompute the v1 canonical manifest and expose market snapshot references without querying latest rows.

```python
if revision.manifest_schema == "underwriting.research-revision-manifest.v1":
    return self._product_revision_summary(revision)
return self._legacy_revision_summary(revision)
```

- [ ] **Step 6: Run GREEN and commit.**

Run:

```bash
cd backend
pytest -q tests/underwriting/test_revision_publisher.py \
  tests/underwriting/test_product_legacy_compatibility.py \
  tests/underwriting/test_research_revision_diff.py \
  tests/underwriting/test_historical_replay.py
```

Expected: PASS.

```bash
git add backend/app/underwriting/services/revision_publisher.py backend/app/underwriting/services/research_revision_diff.py backend/app/underwriting/persistence/product_repository.py backend/tests/underwriting/test_revision_publisher.py
git commit -m "feat: atomically publish investment research revisions"
```

## Task 8: Expose strict product APIs and generated contracts

**Files:**

- Create: `backend/app/underwriting/api/product_schemas.py`
- Create: `backend/app/underwriting/api/product_router.py`
- Create: `backend/app/underwriting/api/transactions.py`
- Create: `backend/tests/underwriting/test_product_api.py`
- Modify: `backend/app/underwriting/api/router.py`
- Modify: `backend/tests/underwriting/test_openapi_dump.py`
- Modify/generated: `frontend/openapi.json`
- Modify/generated: `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write RED HTTP tests for the foundation path.** Cover object search, project list/create/detail, mandate/scope/agenda writes, product basis, four market snapshot types, draft GET/PATCH, publication preview/publish and selected revision read. Require strict extra-field rejection, 409 stale lock, idempotency and no target-price/action fields.

```text
GET  /api/underwriting/v1/product/objects?query=CATL
GET  /api/underwriting/v1/product/projects?limit=20
POST /api/underwriting/v1/product/projects
GET  /api/underwriting/v1/product/projects/{id}
POST /api/underwriting/v1/product/projects/{id}/mandates
POST /api/underwriting/v1/product/projects/{id}/scopes
POST /api/underwriting/v1/product/projects/{id}/agendas
POST /api/underwriting/v1/product/historical-bases
POST /api/underwriting/v1/product/market/price-snapshots
POST /api/underwriting/v1/product/market/fx-snapshots
POST /api/underwriting/v1/product/market/capital-structure-snapshots
POST /api/underwriting/v1/product/market/security-rights
GET  /api/underwriting/v1/product/projects/{id}/draft
PATCH /api/underwriting/v1/product/projects/{id}/draft
POST /api/underwriting/v1/product/projects/{id}/publication-preview
POST /api/underwriting/v1/product/projects/{id}/publish
GET  /api/underwriting/v1/product/revisions/{id}
```

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_product_api.py`

Expected: 404 for all product routes.

- [ ] **Step 3: Implement strict DTOs and thin handlers.** Every DTO extends `UnderwritingModel(extra="forbid")`; handlers delegate to one service and use existing 404/409/422 envelopes. `PATCH draft` requires `expected_lock_version`; publish requires both expected lock and `Idempotency-Key` header.

```python
from app.underwriting.api.transactions import commit_write

WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}

class PublishProductRevisionRequest(UnderwritingModel):
    expected_lock_version: int = Field(ge=1)

def _product_revision_response(value: ProductRevisionView) -> ProductRevisionResponse:
    return ProductRevisionResponse(
        id=value.id,
        project_id=value.project_id,
        boundary_id=value.boundary_id,
        manifest_hash=value.manifest_hash,
        answerability=value.answerability,
        direction=value.direction,
        confidence=value.confidence,
        publication_status=value.publication_status,
    )

@product_router.post(
    "/projects/{project_id}/publish",
    response_model=ProductRevisionResponse,
    responses=WRITE_ERROR_RESPONSES,
)
def publish_product_revision(
    project_id: UUID,
    payload: PublishProductRevisionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=120)],
    db: Session = Depends(get_db),
) -> ProductRevisionResponse:
    revision = commit_write(
        db,
        lambda: RevisionPublisher(db, now=lambda: datetime.now(UTC)).publish(
            project_id, payload.expected_lock_version, idempotency_key,
        ),
    )
    return _product_revision_response(revision)
```

- [ ] **Step 4: Include the router without growing the legacy handler file.** Move the existing `_write` body unchanged into `api/transactions.py` as `commit_write`, import it from both routers, and add `router.include_router(product_router)` after existing router construction.

```python
from app.underwriting.api.product_router import router as product_router
from app.underwriting.api.transactions import commit_write

router.include_router(product_router)
```

- [ ] **Step 5: Safely regenerate contracts.** First fingerprint the existing unstaged `frontend/openapi.json` patch. Generate to a temporary path, apply only the produced schema change, regenerate TypeScript, and restore the user's pre-existing hunk outside the staged feature diff.

Run:

```bash
contract_tmp="$(mktemp -d)"
git diff -- frontend/openapi.json > "$contract_tmp/user-openapi.patch"
shasum -a 256 frontend/openapi.json > "$contract_tmp/user-openapi.sha256"

cd backend && python scripts/dump_openapi.py
cd ../frontend && npm run gen:contract && npm run typecheck
cd ..

git add frontend/openapi.json frontend/src/contracts/v1.ts
git apply "$contract_tmp/user-openapi.patch"
git diff --cached --check
git diff --check -- frontend/openapi.json
```

Expected: generated product operations exist; the pre-existing unstaged OpenAPI hunk remains unstaged.

- [ ] **Step 6: Run GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_product_api.py tests/underwriting/test_openapi_dump.py tests/underwriting/test_kernel_api.py`

Expected: PASS.

```bash
git add backend/app/underwriting/api/product_schemas.py backend/app/underwriting/api/product_router.py backend/app/underwriting/api/transactions.py backend/app/underwriting/api/router.py backend/tests/underwriting/test_product_api.py backend/tests/underwriting/test_openapi_dump.py frontend/src/contracts/v1.ts
git commit -m "feat: expose investment research product foundation api"
```

## Task 9: Build the independent shell, setup flow and honest workbench skeleton

**Files:**

- Create: `frontend/src/app/InvestmentResearchShell.tsx`
- Create: `frontend/src/data/investmentResearchApi.ts`
- Create: `frontend/src/features/investment-research/ResearchHomePage.tsx`
- Create: `frontend/src/features/investment-research/NewResearchPage.tsx`
- Create: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Create: `frontend/src/features/investment-research/InvestmentResearchShell.test.tsx`
- Create: `frontend/src/features/investment-research/NewResearchPage.test.tsx`
- Create: `frontend/src/data/InvestmentResearchApi.test.ts`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/styles/underwriting-research.css`

- [ ] **Step 1: Write RED route/shell tests.** `/research`, `/research/new` and `/research/projects/:projectId` render outside `AppShell` and outside `UnderwritingArchiveShell`. Assert no event navigation, worker controls, fund pages, mock research client or automatic-research polling import is reachable.

- [ ] **Step 2: Write RED setup-flow tests.** Search CATL, distinguish Company from 300750.SZ Security, create mandate/scope/boundary, enter workbench, and render the publication preview as `insufficient_evidence`. Industry-only selection must require a Company/Security before project creation.

- [ ] **Step 3: Confirm RED.**

Run: `cd frontend && npm test -- InvestmentResearchShell.test.tsx NewResearchPage.test.tsx InvestmentResearchApi.test.ts`

Expected: missing module/route failures.

- [ ] **Step 4: Implement a generated-contract-only client.** Export typed methods for the Task 8 routes. Validate HTTP envelopes and identity-bind returned project/draft/revision IDs. Do not import `researchClient`, `researchOsApi`, `mockResearchOsApi` or Event Research types.

```typescript
import type { components } from "../contracts/v1";

type Schemas = components["schemas"];
export type ProductProject = Schemas["ProductProjectResponse"];
export type ProductProjectList = Schemas["ProductProjectListResponse"];
export type ProductDraft = Schemas["WorkspaceDraftResponse"];
export type DraftPatchRequest = Schemas["WorkspaceDraftPatchRequest"];

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`Investment research request failed (${response.status})`);
  }
  return await response.json() as T;
}

export class InvestmentResearchApi {
  constructor(private readonly baseUrl = "") {}

  async project(projectId: string): Promise<ProductProject> {
    return requestJson<ProductProject>(`${this.baseUrl}/api/underwriting/v1/product/projects/${projectId}`);
  }

  async saveDraft(projectId: string, body: DraftPatchRequest): Promise<ProductDraft> {
    const draft = await requestJson<ProductDraft>(
      `${this.baseUrl}/api/underwriting/v1/product/projects/${projectId}/draft`,
      { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify(body) },
    );
    if (draft.project_id !== projectId) throw new Error("draft project identity mismatch");
    return draft;
  }
}
```

- [ ] **Step 5: Implement the three pages.** The shell contains only product navigation. Home provides object search and recent projects. Setup captures InvestmentMandate, ResearchScope and RevisionBoundary. Workbench renders the nine module tabs, but only Overview, Versions and boundary metadata are enabled; unfinished modules display explicit “尚未建立” states and never fixture values.

```tsx
const modules = [
  "概览", "来源与证据", "行业", "公司模型", "预测与情景",
  "估值", "判断与反证", "版本与变化", "研究备忘录",
] as const;
```

- [ ] **Step 6: Run GREEN, typecheck, build and commit.**

Run:

```bash
cd frontend
npm test -- InvestmentResearchShell.test.tsx NewResearchPage.test.tsx InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

Expected: PASS.

```bash
git add frontend/src/app/InvestmentResearchShell.tsx frontend/src/app/routes.tsx frontend/src/data/investmentResearchApi.ts frontend/src/features/investment-research frontend/src/styles/underwriting-research.css
git commit -m "feat: add independent investment research entry flow"
```

## Task 10: Add CATL and Alphabet identity foundation fixtures

**Files:**

- Create: `backend/app/underwriting/fixtures/product_foundation/manifest.json`
- Create: `backend/app/underwriting/fixtures/product_foundation/__init__.py`
- Create: `backend/app/underwriting/services/product_foundation_fixture.py`
- Modify: `backend/tests/underwriting/test_product_project.py`
- Modify: `backend/tests/underwriting/test_revision_publisher.py`
- Modify: `frontend/src/features/investment-research/NewResearchPage.test.tsx`

- [ ] **Step 1: Write RED fixture tests.** Require CATL Company + 300750.SZ and Alphabet Company + GOOGL/GOOG, effective identity-version lookup, exact company_has_security relations, currencies, exchanges, multi-Security project membership and distinct SecurityRightsVersion rows. Fixture loading must be idempotent and content-hash checked.

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_product_project.py -k foundation_fixture`

Expected: fixture loader missing.

- [ ] **Step 3: Implement the minimal identity manifest and loader.** Include no financials, price, valuation, assessment or generated research content. GOOGL and GOOG remain different Securities even if their initial economic-rights payloads are equal.

```json
{
  "schema_version": "product.foundation-identities.v1",
  "companies": [
    {"external_key": "CN:300750:COMPANY", "canonical_name": "宁德时代新能源科技股份有限公司", "effective_from": "2011-12-16T00:00:00+08:00"},
    {"external_key": "US:ALPHABET:COMPANY", "canonical_name": "Alphabet Inc.", "effective_from": "2015-10-02T00:00:00-04:00"}
  ],
  "securities": [
    {"external_key": "SZSE:300750", "company_key": "CN:300750:COMPANY", "symbol": "300750", "exchange": "SZSE", "currency": "CNY", "share_class": "A", "effective_from": "2018-06-11T00:00:00+08:00"},
    {"external_key": "NASDAQ:GOOGL", "company_key": "US:ALPHABET:COMPANY", "symbol": "GOOGL", "exchange": "NASDAQ", "currency": "USD", "share_class": "Class A", "effective_from": "2015-10-02T00:00:00-04:00"},
    {"external_key": "NASDAQ:GOOG", "company_key": "US:ALPHABET:COMPANY", "symbol": "GOOG", "exchange": "NASDAQ", "currency": "USD", "share_class": "Class C", "effective_from": "2015-10-02T00:00:00-04:00"}
  ],
  "rights": [
    {"security_key": "SZSE:300750", "economic_units": "1", "votes_per_unit": "1", "effective_from": "2018-06-11"},
    {"security_key": "NASDAQ:GOOGL", "economic_units": "1", "votes_per_unit": "1", "effective_from": "2015-10-02"},
    {"security_key": "NASDAQ:GOOG", "economic_units": "1", "votes_per_unit": "0", "effective_from": "2015-10-02"}
  ]
}
```

- [ ] **Step 4: Add the foundation golden path.** Create a CATL project and a foundation `insufficient_evidence` revision; verify Alphabet object search and multi-Security setup preview without publishing an Alphabet research conclusion.

Use explicit synthetic snapshot values only inside tests; do not ship them in the identity manifest or present them as CATL facts.

```python
def test_foundation_golden_path_publishes_only_insufficient_evidence(product_stack, catl_identity) -> None:
    project = product_stack.create_project_for(catl_identity.company, catl_identity.security)
    draft = product_stack.complete_foundation_draft(project, snapshots=synthetic_test_snapshots())
    revision = product_stack.publisher.publish(project.id, draft.lock_version, "catl-foundation-1")
    assert revision.answerability == "not_answerable"
    assert revision.direction is None
    assert revision.confidence is None
```

- [ ] **Step 5: Run GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_product_project.py tests/underwriting/test_revision_publisher.py -k 'fixture or foundation'`

Expected: PASS.

```bash
git add backend/app/underwriting/fixtures/product_foundation backend/app/underwriting/services/product_foundation_fixture.py backend/tests/underwriting/test_product_project.py backend/tests/underwriting/test_revision_publisher.py frontend/src/features/investment-research/NewResearchPage.test.tsx
git commit -m "test: add investment research identity fixtures"
```

## Task 11: Complete runtime backup/restore and the Increment A release gate

**Files:**

- Modify: `docker-compose.one-click.yml`
- Modify: `scripts/one-click-runtime.sh`
- Modify: `scripts/verify-one-click-runtime.sh`
- Create: `scripts/verify-investment-research-foundation.sh`
- Modify: `docs/architecture/underwriting-research.md`

- [ ] **Step 1: Write shell-level contract checks.** Extend the verifier to assert current Alembic revision `0065`, `/research` returns the product shell, product API health succeeds, and runtime starts when optional commercial data and AI keys are absent.

```bash
require_revision "$new_revision" 0065
curl --fail --silent --show-error http://127.0.0.1:8080/research | grep -q '投资研究'
curl --fail --silent --show-error \
  'http://127.0.0.1:8000/api/underwriting/v1/product/objects?query=CATL' >/dev/null
```

- [ ] **Step 2: Add local file storage and backup.** Mount `fund-engine-one-click-files:/data/research-files` into API and workers. `one-click-runtime.sh backup <absolute-output-dir>` must create a PostgreSQL custom-format dump, a compressed file-store archive and `manifest.sha256`; it must reject broad or relative output targets.

```yaml
services:
  api:
    environment:
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_BASE_URL: ${LLM_BASE_URL:-}
      LLM_MODEL: ${LLM_MODEL:-}
      GILDATA_TOKEN: ${GILDATA_TOKEN:-}
    volumes:
      - fund-engine-one-click-files:/data/research-files
  research-worker:
    environment:
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_BASE_URL: ${LLM_BASE_URL:-}
      LLM_MODEL: ${LLM_MODEL:-}
    volumes:
      - fund-engine-one-click-files:/data/research-files
  acquisition-worker:
    environment:
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_BASE_URL: ${LLM_BASE_URL:-}
      LLM_MODEL: ${LLM_MODEL:-}
      GILDATA_TOKEN: ${GILDATA_TOKEN:-}
      ACQUISITION_ENABLED_ADAPTERS: ${ACQUISITION_ENABLED_ADAPTERS:-sse,szse}
    volumes:
      - fund-engine-one-click-files:/data/research-files
volumes:
  fund-engine-one-click-files:
    name: fund-engine-one-click-files
```

```bash
backup_runtime() {
  local output_dir="$1"
  [[ "$output_dir" = /* && "$output_dir" != "/" ]] || die "backup path must be a specific absolute directory"
  mkdir -p "$output_dir"
  compose exec -T postgres pg_dump -Fc -U "$(runtime_env_value ONE_CLICK_POSTGRES_USER)" \
    "$(runtime_env_value ONE_CLICK_POSTGRES_DB)" > "$output_dir/postgres.dump"
  docker run --rm -v fund-engine-one-click-files:/source:ro -v "$output_dir":/backup alpine \
    tar -C /source -czf /backup/research-files.tar.gz .
  (cd "$output_dir" && shasum -a 256 postgres.dump research-files.tar.gz > manifest.sha256)
}
```

- [ ] **Step 3: Add fail-closed restore.** `restore <absolute-backup-dir>` requires the one-click application services to be stopped, validates all checksums, restores into the isolated one-click volumes, runs Alembic to head and verifies stored ResearchRevision manifest hashes. Secrets and `.env*` files are never copied into backup artifacts.

The verifier opens one database session, loads every `independent_research` revision, calls `ResearchRevisionDiffService.revision_summary(id)` and exits nonzero on the first stored/recomputed hash mismatch:

```python
def main() -> int:
    with SessionLocal() as session:
        ids = session.scalars(
            select(UnderwritingResearchVersion.id).where(
                UnderwritingResearchVersion.version_kind == "independent_research"
            )
        )
        reader = ResearchRevisionDiffService(session)
        for revision_id in ids:
            reader.revision_summary(revision_id)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

```bash
restore_runtime() {
  local backup_dir="$1"
  [[ "$backup_dir" = /* && -f "$backup_dir/manifest.sha256" ]] || die "invalid backup directory"
  (cd "$backup_dir" && shasum -a 256 -c manifest.sha256) || die "backup checksum validation failed"
  [[ -z "$(compose ps --status running --services | grep -Ev '^postgres$' || true)" ]] \
    || die "stop application services before restore"
  compose exec -T postgres dropdb --if-exists -U "$(runtime_env_value ONE_CLICK_POSTGRES_USER)" \
    "$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  compose exec -T postgres createdb -U "$(runtime_env_value ONE_CLICK_POSTGRES_USER)" \
    "$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  compose exec -T postgres pg_restore --exit-on-error --clean --if-exists \
    -U "$(runtime_env_value ONE_CLICK_POSTGRES_USER)" -d "$(runtime_env_value ONE_CLICK_POSTGRES_DB)" \
    < "$backup_dir/postgres.dump"
  docker run --rm -v fund-engine-one-click-files:/target -v "$backup_dir":/backup:ro alpine sh -eu -c \
    'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -C /target -xzf /backup/research-files.tar.gz'
  compose run --rm migrate
  compose run --rm api python -m app.scripts.verify_underwriting_revision_manifests
}
```

- [ ] **Step 4: Add the full Increment A gate script.**

```bash
#!/usr/bin/env bash
set -euo pipefail

cd backend
pytest -q tests/underwriting/test_product_contracts.py \
  tests/underwriting/test_product_persistence.py \
  tests/underwriting/test_product_project.py \
  tests/underwriting/test_market_snapshots.py \
  tests/underwriting/test_workspace_draft.py \
  tests/underwriting/test_revision_publisher.py \
  tests/underwriting/test_product_api.py \
  tests/underwriting/test_product_legacy_compatibility.py

cd ../frontend
npm test -- InvestmentResearchShell.test.tsx NewResearchPage.test.tsx InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

- [ ] **Step 5: Run fresh full verification.**

Run:

```bash
./scripts/verify-investment-research-foundation.sh
cd backend && python -m compileall -q app tests
cd ../frontend && npm test
git diff --check
```

Expected: all commands exit 0. If Docker is available, also run `./scripts/one-click-runtime.sh up`, `./scripts/verify-one-click-runtime.sh`, backup, restore into a disposable isolated runtime, then compare the foundation revision manifest hash.

- [ ] **Step 6: Document and commit the gate.** Record actual test counts, skipped external-service checks, current migration, CATL foundation revision hash and legacy replay result in the architecture completion section.

```bash
git add docker-compose.one-click.yml scripts/one-click-runtime.sh scripts/verify-one-click-runtime.sh scripts/verify-investment-research-foundation.sh backend/app/scripts/verify_underwriting_revision_manifests.py docs/architecture/underwriting-research.md
git commit -m "chore: gate investment research product foundation"
```

## Increment A completion checklist

- [ ] Existing CATL evidence-only revisions retain their stored hashes and read responses.
- [ ] New HistoricalBasis rows use `price_as_of=NULL`; price/FX/capital structure/rights are independent frozen rows.
- [ ] ResearchProject requires a Company and at least one related Security.
- [ ] InvestmentMandate, ResearchScope and ResearchAgenda have immutable successor chains.
- [ ] WorkspaceDraft rejects stale writes and is never returned as a formal revision.
- [ ] Publication preview is write-free; publication is atomic and idempotent.
- [ ] Foundation publication can only produce `insufficient_evidence`, not a provisional direction.
- [ ] `/research` is isolated from Event Research and displays unfinished modules honestly.
- [ ] CATL and Alphabet identity fixtures prove one-company/multi-security contracts.
- [ ] Generated OpenAPI and TypeScript contracts compile without absorbing the user's pre-existing unstaged OpenAPI edit.
- [ ] Runtime startup, status, backup and restore verification pass or record the unavailable Docker prerequisite explicitly.
