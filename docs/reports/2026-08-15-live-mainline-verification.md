# Live event-research mainline verification

## Current status

Implementation and deterministic verification are complete. The real Compose/browser execution is
**not yet recorded as passed** because this checkout has no `.env.compose` with authorized LLM and
source-provider credentials. The live runner fails closed in that condition.

## What the live run must prove

- Real Keycloak Authorization Code + PKCE login for two stable users.
- Same-tenant Case invisibility before an explicit grant; viewer read and write denial afterward.
- Real official-adapter search, frozen HTTP source, SHA-256 identity, publication deduplication,
  automatic admission, contrary-search completion, and conclusion citations.
- API restart preserves Case, orchestration, research-run, scope version, query-plan IDs, and event
  history.
- Acquisition-worker hard termination recovers the same leased job with a higher fenced attempt,
  unchanged frozen plan identity, a second public claim event with no intervening `retry_wait`, and
  no duplicate durable retrieval/evidence output.
- Research-worker hard termination recovers the same persisted job with a higher attempt and an
  explicit recovery event, then reaches monitoring without a manual continuation action.
- Scheduler is stopped, an acceptance-only five-minute monitor version is saved through the public API, and the
  restarted scheduler must publish a fresh heartbeat and create a new run whose frozen scope event
  records `trigger=schedule`.

## Reproducible command

```bash
cp .env.compose.example .env.compose
# Replace provider placeholders with authorized real credentials.
./scripts/run-live-acceptance.sh
```

The runner writes a per-run report under `artifacts/live-acceptance/fund-engine-live-*` and copies
the Playwright result/HTML directories into that same isolated artifact directory. JSON attachments
keep only redacted Case/orchestration/run/plan/restart evidence. Raw Compose logs, traces, and videos
are private diagnostic artifacts: permissions are restricted, Git ignores the directory, and the
files still require review before sharing because they may contain Case metadata or page content.

## Evidence ledger

| Evidence | Status |
|---|---|
| Static no-Mock/no-route/no-subprocess/no-SQL browser guard | Passed |
| Backend permission, provenance, runtime, and recovery tests | 2397 passed, 63 environment skips |
| PostgreSQL-only tests | 61 passed; migration head `0062` |
| Frontend unit, type, and production build | 368 passed; typecheck and build passed |
| Generated OpenAPI/TypeScript contract | In sync |
| Independent implementation reviews | Approved; no P0/P1 findings |
| Real provider/Keycloak/PostgreSQL browser mainline | Pending authorized `.env.compose` |
| API/acquisition/research/scheduler restart timestamps and IDs | Pending real runner execution |

No Case ID, image ID, provider adapter result, migration head, or browser PASS is claimed until the
runner has actually produced those artifacts.
