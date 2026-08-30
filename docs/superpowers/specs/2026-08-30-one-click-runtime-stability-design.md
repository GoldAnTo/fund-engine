# One-Click Runtime Stability Design

Date: 2026-08-30

Status: approved for implementation planning

## 1. Objective

Make the local `fund-engine-one-click` runtime stable on a 16 GiB developer
Mac with approximately 8 GiB assigned to Docker Desktop. The default topology
must keep the API, PostgreSQL, frontend, research worker, company-research
worker, and governed acquisition worker healthy without an API OOM, database
connection storm, or silent worker restart.

The runtime remains explicitly scalable. A user may opt into additional
acquisition workers after the one-worker default has passed the same stability
gate.

## 2. Current Failure Boundary

The current operator hard-codes three acquisition-worker replicas. Together
with the API, PostgreSQL, two other workers, frontend, migration container, and
image builds, this creates an avoidable local resource peak. The application
also creates a default SQLAlchemy engine in every process without bounding the
pool or enabling stale-connection recovery.

The observed live failure was:

- the API exited with code 137 and `OOMKilled=true`;
- every worker became unhealthy;
- research and company-research workers saw PostgreSQL close their connections;
- both HTTP health endpoints timed out;
- Docker management commands later became slow or unresponsive under pressure.

Existing health checks identify the terminal unhealthy state, but the verifier
does not prove that the runtime remains healthy over time or that a container
did not restart during the check.

## 3. Scope

### In scope

- Default to one acquisition worker while allowing an explicit bounded scale.
- Bound PostgreSQL connection pools per process and recover stale connections.
- Give every long-lived service an explicit, configurable CPU and memory ceiling.
- Reduce startup peak memory by building backend and frontend images serially.
- Check Docker memory before stopping the legacy application or starting the
  one-click runtime.
- Extend verification to detect OOM, restarts, unhealthy replicas, HTTP failure,
  and excessive database connections over a caller-selected stability window.
- Preserve the isolated database, file volume, backup/restore behavior, local
  bearer-token boundary, and legacy-runtime rollback contract.

### Out of scope

- Dynamic autoscaling or a distributed scheduler.
- Changing research, acquisition, evidence, modeling, or publication semantics.
- Adding the real Company Research browser E2E. That is the next sub-project and
  will consume the stability verifier introduced here.
- Closing the Alphabet research gaps or adding successor research revisions.
- Configuring Docker Desktop's global VM memory on the user's behalf.

## 4. Selected Architecture

Use a low-resource default profile with explicit expansion controls. This is
preferred over only lowering the worker count because it also closes the
connection and diagnostic failure modes. It is preferred over autoscaling
because this product is a local single-user runtime and has no durable demand
signal that justifies another control plane.

The topology remains unchanged:

```text
frontend -> api -> PostgreSQL
                 ^
research-worker -|
acquisition-worker (1 by default, 1-4 explicitly)
company-research-worker
```

No new service is introduced. Stability is owned by three focused seams:

1. `app.db` owns validated database-engine configuration.
2. `one-click-runtime.sh` owns local resource policy and safe startup.
3. `verify-one-click-runtime.sh` owns point-in-time and sustained health proof.

## 5. Configuration Contract

### 5.1 Worker scale

`ONE_CLICK_ACQUISITION_REPLICAS` is an integer from 1 through 4 and defaults to
`1`. The operator validates it before building images or stopping legacy
services. The verifier reads the same value and requires exactly that many
containers. Existing private environment files that omit the setting remain
valid and receive the one-worker default.

### 5.2 Database pool

Every PostgreSQL process uses these validated settings:

| Variable | Default | Constraint |
|---|---:|---:|
| `DATABASE_POOL_SIZE` | 2 | 1-10 |
| `DATABASE_MAX_OVERFLOW` | 2 | 0-10 |
| `DATABASE_POOL_TIMEOUT_SECONDS` | 30 | 1-120 |
| `DATABASE_POOL_RECYCLE_SECONDS` | 300 | 30-3600 |

The engine enables `pool_pre_ping=True` and `pool_use_lifo=True`. Pool options
are applied only to PostgreSQL; SQLite keeps its existing pool behavior. Invalid
configuration fails during process startup with a bounded message that names
the invalid variable but never prints `DATABASE_URL` or credentials.

With the default topology, the long-lived maximum is 16 pooled/overflow
connections across API plus three worker processes. The verifier allows five
additional maintenance connections and derives the cap from the configured
replica and pool values.

### 5.3 Service resource ceilings

Compose applies configurable ceilings with these defaults:

| Service | Memory | CPUs |
|---|---:|---:|
| PostgreSQL | 1536 MiB | 1.5 |
| API | 1536 MiB | 1.5 |
| research worker | 768 MiB | 1.0 |
| acquisition worker, each | 768 MiB | 1.0 |
| company-research worker | 1280 MiB | 1.5 |
| frontend | 256 MiB | 0.5 |

Each value has a `ONE_CLICK_*_MEMORY_LIMIT` or `ONE_CLICK_*_CPU_LIMIT`
override in `.env.one-click.example`. The defaults are ceilings, not reserved
memory. The one-worker long-lived ceiling is approximately 6 GiB, leaving room
inside an 8 GiB Docker VM for the daemon and transient commands.

The operator reads Docker's reported memory before cutover. Less than 6 GiB is
a fail-closed configuration error; 6-8 GiB produces a warning; 8 GiB or more
continues without warning. This check runs before any legacy application
container is stopped.

## 6. Startup and Runtime Flow

`one-click-runtime.sh up` performs this order:

1. Validate private/runtime files and all numeric settings.
2. Render Compose configuration.
3. Read Docker memory and enforce the minimum.
4. Build the shared backend image, then build the frontend image. The two builds
   do not run concurrently.
5. Validate and pin the existing one-click PostgreSQL volume.
6. Record and stop only the allowed legacy application containers.
7. Stop old writers from this same one-click project.
8. Start Compose with
   `--scale acquisition-worker=$ONE_CLICK_ACQUISITION_REPLICAS`.
9. Preserve the existing rollback trap: any failed cutover stops the new stack
   and restarts only the exact legacy containers recorded before cutover.

The API and workers receive the database-pool variables through Compose. The
frontend receives no database setting or secret beyond the existing server-side
proxy token.

## 7. Stability Verification

`verify-one-click-runtime.sh` accepts an optional
`--stability-seconds <non-negative integer>`. Zero performs the existing fast
check. A positive value adds a sustained gate.

At the start, on every five-second poll, and at the end, the verifier checks:

- exact service/replica count;
- container state is `running` and health is `healthy`;
- `OOMKilled` is false;
- API and frontend health endpoints return HTTP 200 with bounded curl timeouts;
- database revision and legacy-database isolation remain correct;
- active PostgreSQL connections do not exceed the derived cap.

The verifier snapshots each container ID and restart count. During the window,
an ID change or restart-count increase is a failure even if the replacement is
healthy by the final poll. This prevents restart policies from hiding crashes.

On failure, diagnostics include service name, state, health, OOM flag, restart
count, a bounded tail of the failing container log, and one `docker stats
--no-stream` snapshot. Diagnostics never dump environment variables or secret
files.

## 8. Failure Handling

- Invalid scale, pool, resource, or stability values fail before external state
  changes.
- Insufficient Docker memory fails before legacy cutover.
- A stale PostgreSQL connection is detected by `pool_pre_ping`; the process
  reconnects through the bounded pool instead of issuing work on a dead socket.
- Pool exhaustion waits only for the configured timeout and then surfaces the
  existing application error path; it never creates unbounded overflow.
- A failed startup retains the existing recoverable rollback behavior.
- A failed sustained verification does not automatically restart, stop, delete,
  or roll back anything. It reports evidence so the operator chooses the next
  action.

## 9. Compatibility and Security

- Existing `.env.one-click.local` files remain readable without migration.
- No database or file volume is recreated by changing scale or resource limits.
- `down` continues to preserve volumes; backup/restore boundaries are unchanged.
- Legacy PostgreSQL and Keycloak containers remain outside the new Compose
  project's mutations.
- Secrets remain absent from browser assets, verifier output, container
  diagnostics, and committed environment examples.
- The generic event-research and Company Research workers continue to use the
  same `SessionLocal` API; only engine construction changes.

## 10. Tests and Acceptance

Implementation follows test-first development.

### Automated tests

- Database configuration tests prove valid PostgreSQL options, SQLite
  compatibility, boundary values, and fail-closed invalid variables.
- Runtime asset tests prove every service receives the intended resource and
  pool configuration without exposing it to the frontend.
- Operator tests prove the default scale is one, explicit scale is honored,
  invalid values stop before cutover, and builds are serialized.
- Verifier tests use a fake Docker/curl/psql boundary to prove OOM, restart,
  container replacement, excessive connections, and transient unhealthy states
  all fail.
- Existing backup, restore, migration, rollback, heartbeat, and Company Research
  runtime tests remain green.

### Live acceptance

The sub-project is complete only when all of the following hold on the default
profile:

```bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh --stability-seconds 600
```

Required evidence:

- one API, one research worker, one acquisition worker, one company-research
  worker, one frontend, and one PostgreSQL container remain healthy;
- no monitored container restarts, changes identity, or reports OOM;
- both HTTP endpoints answer every bounded poll;
- active PostgreSQL connections remain under the derived cap;
- the isolated database remains at the current Alembic head;
- the legacy database identity and revision remain unchanged;
- `git status` contains no generated secret or runtime state.

The subsequent real Company Research E2E will run on this verified default
profile rather than reimplementing runtime setup.
