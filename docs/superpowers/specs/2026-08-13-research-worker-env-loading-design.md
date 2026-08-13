# Research Worker Environment Loading Design

## Goal

Ensure the automatic-research worker loads `backend/.env` before importing
database and provider-dependent application modules, matching the FastAPI and
AI-engine entry points.

## Design

`app.scripts.run_research_worker` will import `load_local_env` from `app.env`
and call it immediately after standard-library imports. Application imports
such as `app.db` remain below that call. This ordering is required because the
database engine and provider clients can capture environment-backed settings
during module import.

The existing environment contract remains unchanged:

- exported process variables take precedence over `backend/.env`;
- `APP_ENV=test` prevents local credentials from being loaded;
- only `APP_ENV=test` without `LLM_API_KEY` may use deterministic mock mode;
- every non-test runtime without `LLM_API_KEY` fails closed.

## Verification

A subprocess regression test redirects `app.env.ENV_PATH` to a temporary env
file, imports the worker in a clean interpreter, and asserts that `app.db`
observes the temporary `DATABASE_URL`. The subprocess proves the ordering at a
real process boundary without reading the developer's credentials.

The focused entry-point test and the existing automatic-research test suite
must pass. No external LLM or data-provider request is made by the tests.
