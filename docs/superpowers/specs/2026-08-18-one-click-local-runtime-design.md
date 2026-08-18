# One-Click Automatic Research Local Runtime Design

## Goal

Run the current `main` one-click automatic-research frontend and backend on the
existing local addresses without modifying the legacy Docker database whose
Alembic history has a conflicting `0059` revision.

The runtime must expose:

- frontend: `http://127.0.0.1:8080`;
- backend: `http://127.0.0.1:8000`;
- continuously supervised research and governed-acquisition workers.

## Constraints

- The existing `fund-engine-event` PostgreSQL database is at revision `0062`.
  Its `0059` has a different meaning from the current branch's one-click
  migration and must not be downgraded, stamped, or patched in place.
- Existing PostgreSQL and Keycloak containers, volumes, and data must remain
  intact so the old runtime can be restored.
- The current browser bundle must not contain the local bearer credential.
- Only the automatic first-version runtime is required. The reviewed-flow
  scheduler and preparation worker are outside this restart.

## Selected Architecture

Create a separate Compose project named `fund-engine-one-click` with six
services:

1. `postgres`: a new PostgreSQL container with its own named volume;
2. `migrate`: the current backend image running `alembic upgrade head`;
3. `api`: the current FastAPI application on host port `8000`;
4. `research-worker`: the durable automatic-research worker;
5. `acquisition-worker`: the governed source-acquisition worker;
6. `frontend`: the current React production bundle behind unprivileged Nginx
   on host port `8080`.

The old API, frontend, scheduler, research worker, and acquisition workers are
stopped to release ports and avoid processing against the wrong schema. The old
PostgreSQL, Keycloak, and Keycloak database containers remain running.

## Authentication and Configuration

Generate one opaque local bearer token into an ignored, permission-restricted
runtime environment file. The backend receives a `RESEARCH_TENANT_TOKENS`
mapping for a single local tenant. Nginx receives the token at container
startup and injects it only into proxied `/api/` requests. The compiled browser
bundle uses same-origin `/api/v1` and never receives the token.

LLM and governed-source credentials are read from the existing root `.env`.
The runtime configuration must not print or commit their values.

## Data and Request Flow

The frontend posts the single research input to the same-origin API proxy.
Nginx adds the local authorization header and forwards the request to `api`.
The API writes the automatic Case and queued run to the isolated database.
`research-worker` dispatches source jobs, `acquisition-worker` processes them,
and `research-worker` resumes the run to persist the system-generated result.

All services share only the new Compose network and database. No request or
worker from the new runtime touches the legacy `evidence` database.

## Failure Handling and Rollback

- `api`, workers, and frontend depend on a successful migration and healthy
  database.
- Containers use restart policies and graceful stop periods.
- A failed migration prevents API and worker startup.
- Health verification checks the database revision, container state, frontend
  health, backend `/health`, and worker process state.
- Rollback stops the new Compose project and restarts the old
  `fund-engine-event` application services. The old data needs no restoration.

## Verification

Before handoff:

1. render and validate the Compose configuration without exposing secrets;
2. build backend and frontend images from the current working tree;
3. confirm the new database is at current head `0059`;
4. confirm all six new services are running and required health checks pass;
5. confirm `http://127.0.0.1:8000/health` and
   `http://127.0.0.1:8080/health` return HTTP 200;
6. confirm the frontend exposes the one-input automatic-research entry;
7. confirm legacy database revision remains `0062`.
