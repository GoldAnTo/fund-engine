# Runtime Identity and Live Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the event-research mainline as one observable PostgreSQL topology with real OIDC users and server-enforced Case permissions, then prove through a real browser—without mocks or direct database manipulation—that API and worker restarts recover the same Case and complete the workflow.

**Architecture:** Keycloak issues OIDC tokens; FastAPI validates issuer, audience, signature, and claims, resolves a stable server principal, and authorizes every Case access through an explicit grant service. Docker Compose runs migration, API, research worker, acquisition worker, scheduler, frontend, PostgreSQL, and Keycloak with health/readiness checks. A separate Playwright live project creates and observes Cases only through UI/HTTP and controls restarts through Compose, never by SQL.

**Tech Stack:** FastAPI, PyJWT with cryptographic verification, SQLAlchemy, PostgreSQL 16, Keycloak, Docker Compose, React, `oidc-client-ts`, nginx, Playwright, pytest.

---

## Preconditions and boundaries

- Start only after every completion criterion in `docs/superpowers/plans/2026-08-15-event-research-mainline.md` passes.
- Continue on branch `codex/event-research-mainline`; determine the Alembic head before creating the identity migration. The expected revision after the first plan is `0055`, so this plan normally uses `0056`.
- Browser acceptance may call only the rendered UI and public HTTP endpoints. It must not import backend models, open a SQLAlchemy session, run SQL, mutate fixture databases, route requests to fake handlers, or use `VITE_RESEARCH_CLIENT=mock`.
- Unit and deterministic component tests may retain existing fake adapters. Live tests use a separate config and fail closed if the real provider, OIDC issuer, PostgreSQL, or worker topology is absent.
- The browser never sends `actor`, `reviewer`, `created_by`, `changed_by`, `reviewer_id`, tenant ID, or a Case permission decision as authoritative identity data.
- Service identities are explicit OIDC client credentials or signed internal principals with narrow roles; they are not user strings copied from environment variables.
- Existing user data and `CaseTenantAdmission` remain valid. The migration adds user-level permissions without silently reassigning a Case to a different tenant.

## File map

- Create `backend/app/models/identity.py` and an Alembic migration for external users and Case grants.
- Create `backend/app/security/oidc.py`, `backend/app/security/principal.py`, and `backend/app/services/case_authorization.py`.
- Replace `backend/app/api/v1/tenant_context.py` token-map resolution and `backend/app/api/v1/commands/common.py` `X-Actor` resolution.
- Remove identity fields from command schemas and derive audit actors in routers/services.
- Create `frontend/src/auth/oidc.ts`, `frontend/src/auth/AuthProvider.tsx`, and guarded app startup.
- Create backend/frontend Dockerfiles, nginx config, Keycloak realm import, and a full Compose topology.
- Create a dedicated scheduler entry point and runtime readiness projection.
- Create `frontend/playwright.live.config.ts`, live browser fixtures, mainline/recovery/authorization specs, and a shell runner.

## Task 1: Persist stable external users and explicit Case grants

**Files:**
- Create: `backend/app/models/identity.py`
- Create: `backend/alembic/versions/0056_oidc_case_permissions.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Test: `backend/tests/test_identity_schema.py`
- Test: `backend/tests/test_identity_migration_postgres.py`

- [ ] **Step 1: Write the failing schema contract**

```python
assert {"research_users", "case_access_grants"} <= set(Base.metadata.tables)
```

Assert uniqueness on `(issuer, subject)`, `(tenant_id, normalized_email)` when email exists, and `(research_case_id, user_id)`. Assert grant role is one of `owner`, `editor`, `reviewer`, or `viewer`.

- [ ] **Step 2: Run and verify missing tables**

Run: `cd backend && .venv/bin/python -m pytest tests/test_identity_schema.py -v`

Expected: FAIL because identity models are absent.

- [ ] **Step 3: Implement identity records**

`ResearchUser` stores issuer, immutable OIDC subject, tenant ID, display name, normalized email, active flag, and last-seen time. `CaseAccessGrant` stores Case, user, role, granted-by principal ID, reason, and timestamps. Grant rows are mutable only through the authorization service; changes append an audit event.

- [ ] **Step 4: Migrate existing tenant-owned Cases safely**

Do not invent users for old opaque tokens. Existing Cases remain tenant-visible only to a `tenant_administrator` until that administrator grants a real user access. Newly created Cases receive an `owner` grant for the authenticated creator in the same transaction.

- [ ] **Step 5: Run PostgreSQL migration tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_identity_schema.py tests/test_identity_migration_postgres.py -v`

Expected: PASS with one Alembic head and preserved legacy admissions.

```bash
git add backend/app/models/identity.py backend/app/models/__init__.py backend/app/models/ledger.py backend/alembic/versions/0056_oidc_case_permissions.py backend/tests/test_identity_schema.py backend/tests/test_identity_migration_postgres.py
git commit -m "feat: persist users and case grants"
```

## Task 2: Validate OIDC tokens and resolve one trusted principal

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/security/__init__.py`
- Create: `backend/app/security/principal.py`
- Create: `backend/app/security/oidc.py`
- Modify: `backend/app/api/v1/tenant_context.py`
- Modify: `backend/app/api/v1/research_session.py`
- Test: `backend/tests/test_oidc_authentication.py`
- Test: `backend/tests/test_research_session_api.py`

- [ ] **Step 1: Add cryptographic JWT dependency**

Add `PyJWT[crypto]>=2.9,<3` to backend dependencies and refresh the editable environment.

- [ ] **Step 2: Write failing issuer/audience/signature tests**

Generate an RSA test keypair in the test process. Cover valid token, wrong issuer, wrong audience, expired token, unknown key ID, missing subject, missing tenant claim, disabled user, and JWKS cache refresh after key rotation.

- [ ] **Step 3: Implement a typed principal**

```python
@dataclass(frozen=True, slots=True)
class ResearchPrincipal:
    user_id: UUID
    issuer: str
    subject: str
    tenant_id: str
    display_name: str
    roles: frozenset[str]

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"
```

OIDC configuration comes from `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, and `OIDC_TENANT_CLAIM`. Validate RS256 only, require `exp`, `iat`, `iss`, `aud`, and `sub`, bound clock skew, cache JWKS with a short refresh-on-missing-key path, and never accept unsigned or symmetric tokens.

- [ ] **Step 4: Resolve/update the local user server-side**

On successful token validation, upsert `ResearchUser` by issuer/subject, reject tenant changes for the same subject, and return `ResearchPrincipal`. `GET /api/v1/research-session` returns user ID, display name, tenant, roles, issuer, and expiry; it never returns raw claims or tokens.

- [ ] **Step 5: Delete production opaque-token and X-Actor fallbacks**

Replace `RESEARCH_TENANT_TOKENS` and `X-Actor` handling in production dependencies. A test-only dependency override may construct a `ResearchPrincipal`; production code has no anonymous write fallback.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_oidc_authentication.py tests/test_research_session_api.py -v`

Expected: PASS.

```bash
git add backend/pyproject.toml backend/app/security backend/app/api/v1/tenant_context.py backend/app/api/v1/research_session.py backend/tests/test_oidc_authentication.py backend/tests/test_research_session_api.py
git commit -m "feat: authenticate research users with oidc"
```

## Task 3: Enforce Case permissions at one service boundary

**Files:**
- Create: `backend/app/services/case_authorization.py`
- Modify: `backend/app/api/v1/dependencies.py`
- Modify: `backend/app/api/v1/event_research.py`
- Modify: `backend/app/api/v1/research_workflow.py`
- Modify: `backend/app/api/v1/acquisition.py`
- Modify: other Case read/write routers discovered by the audit
- Test: `backend/tests/test_case_authorization.py`
- Test: `backend/tests/test_case_authorization_api.py`

- [ ] **Step 1: Inventory all Case routes**

Run:

```bash
rg -n '@router\.(get|post|put|patch|delete).*\{case_id\}' backend/app/api/v1
```

Record every route and required permission (`view`, `edit`, `review`, `admin`) in `backend/tests/test_case_authorization_api.py`; the test fails when a new Case route lacks a policy declaration.

- [ ] **Step 2: Write cross-tenant and same-tenant denial tests**

Cover owner, editor, reviewer, viewer, ungranted user in the same tenant, another tenant, tenant administrator, and service principal. A 404 hides inaccessible Case existence; a 403 is reserved for an accessible Case with insufficient action permission.

- [ ] **Step 3: Implement `CaseAuthorizationService`**

Expose `require(case_id, principal, permission)`, `grant`, `change_role`, and `revoke`. Resolve legacy tenant admission plus explicit user grant in one query. New Case creation writes tenant admission and owner grant atomically.

- [ ] **Step 4: Apply dependencies to every route**

Use typed dependencies such as `RequireCaseView`, `RequireCaseEdit`, and `RequireCaseReview`. Query and service layers still filter by tenant/Case to preserve defense in depth. Acquisition jobs must match both the principal tenant and the authorized Case.

- [ ] **Step 5: Run authorization tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_case_authorization.py tests/test_case_authorization_api.py -v`

Expected: PASS with every Case endpoint classified.

```bash
git add backend/app/services/case_authorization.py backend/app/api/v1 backend/tests/test_case_authorization.py backend/tests/test_case_authorization_api.py
git commit -m "feat: enforce case permissions"
```

## Task 4: Remove client-authored audit identities from write contracts

**Files:**
- Modify: `backend/app/schemas/v1/commands.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/schemas/v1/research_protocol.py`
- Modify: `backend/app/schemas/v1/operational.py`
- Modify: `backend/app/api/v1/commands/common.py`
- Modify: all affected command routers/services
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: affected frontend forms in `frontend/src/features`
- Test: `backend/tests/test_server_owned_actor.py`
- Test: `frontend/src/tests/ServerOwnedIdentity.test.tsx`

- [ ] **Step 1: Add an actor-field inventory test**

Inspect every OpenAPI request schema and fail if an authoritative write model contains any of:

```python
FORBIDDEN = {"actor", "reviewer", "reviewer_id", "created_by", "changed_by", "tenant_id"}
```

Allow names only in administrative grant payloads where they identify the target user, never the acting user.

- [ ] **Step 2: Run and capture all current violations**

Run: `cd backend && .venv/bin/python -m pytest tests/test_server_owned_actor.py -v`

Expected: FAIL and list current request models.

- [ ] **Step 3: Thread `ResearchPrincipal` through commands**

Replace request identity fields with `principal.actor` in service calls, append-only records, provenance, and audit logs. Machine work uses a server-created actor such as `service:research-worker` plus initiating user ID in separate provenance; it never impersonates that user.

- [ ] **Step 4: Remove frontend constants and inputs**

Delete hard-coded `human:researcher`, `human:reviewer`, and editable acting-user fields from real commands. Display names come from `research-session`; optional target-user selectors use stable user IDs.

- [ ] **Step 5: Sync contracts and run regressions**

Run:

```bash
./scripts/sync-contract.sh --update
cd backend && .venv/bin/python -m pytest tests/test_server_owned_actor.py tests/test_event_research_api.py tests/test_acquisition_api.py -v
npm --prefix frontend test -- --run src/tests/ServerOwnedIdentity.test.tsx
npm --prefix frontend run typecheck
```

Expected: PASS; generated request contracts have no acting-user fields.

- [ ] **Step 6: Commit contract migration**

```bash
git add backend/app/schemas/v1 backend/app/api/v1 backend/app/services frontend/src frontend/openapi.json
git commit -m "refactor: make audit identity server owned"
```

## Task 5: Add OIDC login and guarded frontend startup

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/auth/oidc.ts`
- Create: `frontend/src/auth/AuthProvider.tsx`
- Create: `frontend/src/auth/RequireAuth.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Create: `frontend/src/tests/AuthProvider.test.tsx`

- [ ] **Step 1: Install the OIDC client**

Run: `npm --prefix frontend install oidc-client-ts`

Expected: `package.json` and lockfile contain the exact installed version.

- [ ] **Step 2: Write login/refresh/logout tests**

Test initial callback, silent refresh, expired session, login error, logout, and adapter token replacement. Mock only the OIDC library in unit tests; the live browser test later talks to Keycloak.

- [ ] **Step 3: Implement Authorization Code + PKCE**

Configure authority, client ID, redirect URI, and scope from `VITE_OIDC_*`. Keep tokens in session storage, attach the current access token per request, and retry one request only after a successful refresh. Never compile a bearer token into `VITE_RESEARCH_BEARER_TOKEN`.

- [ ] **Step 4: Guard application and preserve return route**

Before Case data loads, resolve authentication and `/research-session`. Show explicit loading, login failure, and permission-denied states. After login, return to the original event URL.

- [ ] **Step 5: Verify and commit**

Run: `npm --prefix frontend test -- --run src/tests/AuthProvider.test.tsx src/tests/ServerOwnedIdentity.test.tsx`

Run: `npm --prefix frontend run typecheck && npm --prefix frontend run build`

Expected: PASS.

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/auth frontend/src/main.tsx frontend/src/data/httpResearchAdapter.ts frontend/src/tests/AuthProvider.test.tsx
git commit -m "feat: authenticate the research frontend"
```

## Task 6: Package API, workers, scheduler, and frontend

**Files:**
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `.dockerignore`
- Create: `backend/app/scripts/run_scheduler.py`
- Create: `backend/app/scripts/runtime_probe.py`
- Modify: `backend/app/scripts/run_research_worker.py`
- Modify: `backend/app/scripts/run_acquisition_worker.py`
- Test: `backend/tests/test_scheduler_entrypoint.py`
- Test: `backend/tests/test_runtime_probe.py`

- [ ] **Step 1: Write scheduler and probe tests**

The scheduler dispatches monitor jobs and orchestration reconciliation on a lease, while workers only claim their own queues. The probe checks PostgreSQL, migration head, service heartbeat freshness, and required provider configuration without performing source fetches.

- [ ] **Step 2: Separate scheduler work from the research worker**

Move `MonitorScheduler.dispatch_due` and orchestration scanning into `run_scheduler.py`. Keep `run_research_worker.py` focused on research jobs and `run_acquisition_worker.py` focused on acquisition jobs. All three write named heartbeats with instance IDs and current state.

- [ ] **Step 3: Build immutable production images**

Backend image installs the package and runs as a non-root user. Frontend image builds static assets and serves them through nginx, proxying `/api/` to API and preserving SPA routes. Do not bake secrets, provider credentials, Keycloak passwords, or test users into either image.

- [ ] **Step 4: Verify image builds and entrypoints**

Run:

```bash
docker build -t fund-engine-backend:local backend
docker build -t fund-engine-frontend:local frontend
cd backend && .venv/bin/python -m pytest tests/test_scheduler_entrypoint.py tests/test_runtime_probe.py -v
```

Expected: both images build and tests pass.

- [ ] **Step 5: Commit packaging**

```bash
git add backend/Dockerfile frontend/Dockerfile frontend/nginx.conf .dockerignore backend/app/scripts backend/tests/test_scheduler_entrypoint.py backend/tests/test_runtime_probe.py
git commit -m "build: package research runtime services"
```

## Task 7: Define one startable and observable Compose topology

**Files:**
- Modify: `docker-compose.yml`
- Create: `docker-compose.live.yml`
- Create: `infra/keycloak/realm-export.json`
- Create: `.env.compose.example`
- Create: `scripts/research-stack.sh`
- Create: `docs/runbooks/local-research-stack.md`

- [ ] **Step 1: Define services and health dependencies**

The topology contains `postgres`, `keycloak-db`, `keycloak`, one-shot `migrate`, `api`, `research-worker`, `acquisition-worker`, `scheduler`, and `frontend`. Neo4j remains optional unless a tested mainline path requires it. API/workers/scheduler wait for successful migration, not merely an open database port.

- [ ] **Step 2: Import a local OIDC realm safely**

Create public client `fund-engine-web`, confidential service clients for workers, tenant roles, and two local acceptance users in the local-only realm file. Store local acceptance passwords in `.env.compose.example` as explicit non-production values and document that production must provide an external realm and secret manager.

- [ ] **Step 3: Add readiness and restart policies**

API readiness requires database plus migration head. Worker readiness requires a fresh heartbeat. Frontend readiness checks nginx. Use `restart: unless-stopped`; set explicit stop grace periods so leases expire or release cleanly.

- [ ] **Step 4: Add one operator command**

`scripts/research-stack.sh up` validates required provider/OIDC values, starts Compose, waits for every readiness check, and prints frontend/API/Keycloak URLs plus service state. `status`, `logs`, `restart-api`, `restart-research-worker`, `restart-acquisition-worker`, and `down` call Compose without touching data volumes unless `down --volumes` is explicitly requested.

- [ ] **Step 5: Boot and inspect the topology**

Run:

```bash
cp .env.compose.example .env.compose
./scripts/research-stack.sh up
./scripts/research-stack.sh status
docker compose --env-file .env.compose -f docker-compose.yml -f docker-compose.live.yml ps
```

Expected: all long-running services are healthy and migration exits successfully.

- [ ] **Step 6: Commit topology and runbook**

```bash
git add docker-compose.yml docker-compose.live.yml infra/keycloak/realm-export.json .env.compose.example scripts/research-stack.sh docs/runbooks/local-research-stack.md
git commit -m "ops: add observable research stack"
```

## Task 8: Expose runtime health without inventing Case status

**Files:**
- Create: `backend/app/queries/runtime_status.py`
- Create: `backend/app/schemas/v1/runtime_status.py`
- Create: `backend/app/api/v1/runtime_status.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `frontend/src/features/system/RuntimeStatusNotice.tsx`
- Create: `frontend/src/tests/RuntimeStatusNotice.test.tsx`
- Test: `backend/tests/test_runtime_status_api.py`

- [x] **Step 1: Write stale-service tests**

Assert API returns database/migration/provider configuration and per-service heartbeat with `healthy`, `degraded`, or `unavailable`. A missing worker heartbeat cannot be translated into a Case state; the event workflow separately reports the persisted checkpoint and recovery status.

- [x] **Step 2: Implement protected diagnostics**

Expose `/api/v1/runtime-status` to administrators and a narrower Case-safe status summary to ordinary users. Do not expose hostnames, secrets, tokens, stack traces, raw provider responses, or database URLs.

- [x] **Step 3: Render actionable failure states**

When a workflow is active but its required worker is unavailable, show which service is unavailable, the last durable checkpoint, whether automatic recovery will occur, and a retry-status action. Do not show the prior lifecycle as if it were live.

- [x] **Step 4: Verify and commit**

Run:

```bash
cd backend && .venv/bin/python -m pytest tests/test_runtime_status_api.py -v
npm --prefix frontend test -- --run src/tests/RuntimeStatusNotice.test.tsx
```

Expected: PASS.

```bash
git add backend/app/queries/runtime_status.py backend/app/schemas/v1/runtime_status.py backend/app/api/v1/runtime_status.py backend/app/api/v1/router.py backend/tests/test_runtime_status_api.py frontend/src/features/system/RuntimeStatusNotice.tsx frontend/src/tests/RuntimeStatusNotice.test.tsx
git commit -m "feat: report research runtime readiness"
```

## Task 9: Add a no-Mock, no-SQL live browser project

**Files:**
- Create: `frontend/playwright.live.config.ts`
- Create: `frontend/e2e-live/fixtures/auth.ts`
- Create: `frontend/e2e-live/helpers/workflow.ts`
- Create: `frontend/e2e-live/event-mainline.spec.ts`
- Create: `frontend/e2e-live/case-permissions.spec.ts`
- Modify: `frontend/package.json`

- [x] **Step 1: Create a fail-closed live config**

Use `LIVE_BASE_URL`, `LIVE_KEYCLOAK_USER`, and `LIVE_KEYCLOAK_PASSWORD`; do not start `dev:mock`, intercept API routes, or provide backend fixtures. Set trace/video retention on failure and a workflow timeout suitable for real source acquisition.

- [x] **Step 2: Add anti-mock guards**

At suite startup, fetch `/api/v1/runtime-status`, verify PostgreSQL and required workers, verify the frontend build reports client mode `http`, and fail if any response carries a mock marker. Static source checks reject imports from `mockResearchAdapter` in `e2e-live`.

- [x] **Step 3: Implement real OIDC login**

Drive the Keycloak login page in the browser and verify `/research-session` displays the expected stable user and tenant. Attach only a redacted identity summary; do not persist token-bearing browser storage state or inject an access token through local storage by script.

- [x] **Step 4: Implement the real event mainline scenario**

Through the UI: create a new event, upload a real small source file, confirm scope, observe automatic acquisition against configured official source adapters, expand the query/source ledger, wait for system draft and monitoring, and verify `系统生成，未经人工审核`. Capture Case ID from the URL, not the database.

- [x] **Step 5: Implement authorization scenario**

Create a Case as user A, sign out, sign in as user B, and verify direct navigation cannot reveal it. Grant viewer access through the authenticated UI/API as an authorized administrator, then verify read succeeds and write remains forbidden.

- [ ] **Step 6: Run the live project**

Run: `npm --prefix frontend run e2e:live -- --project=chromium`

Expected: PASS against the running Compose stack; lack of provider credentials or real source access is a hard failure with diagnostics, not a skip or fallback.

- [x] **Step 7: Commit live acceptance**

```bash
git add frontend/playwright.live.config.ts frontend/e2e-live frontend/package.json frontend/package-lock.json
git commit -m "test: add live event workflow acceptance"
```

## Task 10: Prove API and worker restart recovery from the browser

**Files:**
- Create: `frontend/e2e-live/restart-recovery.spec.ts`
- Create: `scripts/run-live-acceptance.sh`
- Create: `docs/reports/2026-08-15-live-mainline-verification.md`

- [x] **Step 1: Add an API restart scenario**

Start a Case through the UI, wait until `acquiring`, run `docker compose restart api`, reload the same event URL, and assert the same orchestration ID, scope version, run ID, frozen plan ID, and event history continue. The page may show `recovering`; it may not reset to initial or substitute stale lifecycle text.

- [x] **Step 2: Add acquisition-worker restart scenario**

Wait until a job is `running`, capture its public claim event, terminate `acquisition-worker` with `SIGKILL`, and observe lease recovery through public HTTP. Assert a second claim event with a higher fenced attempt for the same job and no intervening `retry_wait`, a heartbeat newer than the replacement container start, no duplicate retrieval artifact for the same successful attempt, no duplicate evidence link, and the technical retry uses the exact original query plan identity.

- [x] **Step 3: Add research-worker and scheduler restart scenario**

Terminate `research-worker` during an active synthesis lease and verify the same job returns with a higher attempt and explicit recovery count before reaching `monitoring`. Then stop scheduler, save an explicitly gated `acceptance_every_5_minutes` CaseMonitor version through the public API, restart scheduler, require a post-container-start heartbeat, and verify it creates a new durable run with a scope event whose trigger is `schedule`. The frequency must be rejected and inert unless the live-acceptance environment flag is enabled.

- [x] **Step 4: Build a reproducible runner**

`scripts/run-live-acceptance.sh` creates a unique Compose project name, starts a clean ephemeral stack, waits for health, runs Playwright, gathers Compose logs/runtime status/traces, and always stops the stack. It never connects to PostgreSQL directly and never deletes named developer volumes.

- [ ] **Step 5: Execute and record evidence**

Run: `./scripts/run-live-acceptance.sh`

Expected: success mainline, permission isolation, API restart, acquisition-worker restart, research-worker restart, and scheduler restart all pass against real HTTP, PostgreSQL, Keycloak, and configured source providers.

Record image IDs, migration head, browser, provider adapters used, Case IDs, orchestration/run/plan IDs shown by the UI/API, restart timestamps, test counts, and artifact paths in the verification report.

- [x] **Step 6: Commit recovery proof**

```bash
git add frontend/e2e-live/restart-recovery.spec.ts scripts/run-live-acceptance.sh docs/reports/2026-08-15-live-mainline-verification.md
git commit -m "test: prove live restart recovery"
```

## Task 11: Full security, contract, and release verification

**Files:**
- Verify only

- [x] **Step 1: Run backend and frontend suites**

```bash
cd backend && .venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all pass.

- [x] **Step 2: Verify migrations and contracts**

```bash
cd backend && DATABASE_URL=postgresql+psycopg://evidence:evidence@127.0.0.1:5432/evidence .venv/bin/alembic upgrade head
./scripts/sync-contract.sh --check
```

Expected: one migration head and no generated-contract drift.

- [x] **Step 3: Scan for forbidden trust paths and live-test shortcuts**

```bash
rg -n "RESEARCH_TENANT_TOKENS|X-Actor|human:anonymous|VITE_RESEARCH_BEARER_TOKEN" backend/app frontend/src
rg -n "mockResearch|route\(|sqlalchemy|psycopg|SELECT |INSERT |UPDATE |DELETE " frontend/e2e-live scripts/run-live-acceptance.sh
git diff --check
```

Expected: no production fallback identity, no live request interception/direct SQL, and no whitespace errors. Any literal SQL verb found in user-visible test text must be reviewed and documented.

- [ ] **Step 4: Run the complete live proof once more**

Run: `./scripts/run-live-acceptance.sh`

Expected: PASS from a new ephemeral stack.

## Completion criteria

- A real OIDC subject maps to a stable server user; Case owner/editor/reviewer/viewer permissions are enforced server-side.
- Acting identity and tenant are absent from ordinary write payloads and generated from the authenticated principal.
- `docker compose` starts migration, API, separate workers, scheduler, PostgreSQL, Keycloak, and frontend with useful readiness/health visibility.
- The live Playwright project uses real browser login, real HTTP, real PostgreSQL, and real configured source adapters, with no mock client, interception, or direct SQL.
- API, acquisition-worker, research-worker, and scheduler restarts recover the same persisted workflow and frozen query plan without duplicate evidence or manual continuation.
- Verification artifacts make provider scope and any unavailable source explicit; no fake success or old Case-status fallback is permitted.
