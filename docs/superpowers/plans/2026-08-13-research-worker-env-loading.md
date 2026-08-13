# Research Worker Environment Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load `backend/.env` before the automatic-research worker initializes database or provider-dependent modules.

**Architecture:** Reuse the existing `app.env.load_local_env` boundary at the worker process entry point. Keep the call above application imports so import-time settings see the same environment as FastAPI and other CLI scripts.

**Tech Stack:** Python 3, python-dotenv, pytest, subprocess

---

### Task 1: Protect worker environment loading order

**Files:**
- Create: `backend/tests/test_research_worker_entrypoint.py`
- Modify: `backend/app/scripts/run_research_worker.py`

- [x] **Step 1: Write the failing subprocess test**

Create a temporary `.env` containing a unique SQLite `DATABASE_URL`. In a clean
Python subprocess, redirect `app.env.ENV_PATH`, import the worker, then print
the database engine URL. Assert the worker import made the engine observe the
temporary value.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest -q tests/test_research_worker_entrypoint.py
```

Expected: failure because the worker currently imports `app.db` without first
calling `load_local_env`.

- [x] **Step 3: Add the minimal entry-point load**

In `run_research_worker.py`, import `load_local_env`, call it immediately after
standard-library imports, and leave all `app.db`, model, and service imports
below the call.

- [x] **Step 4: Run focused and related tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest -q tests/test_research_worker_entrypoint.py tests/test_auto_research_api.py
```

Expected: all tests pass, with PostgreSQL-only tests allowed to skip when
`TEST_DATABASE_URL` is not configured.

- [x] **Step 5: Verify repository hygiene**

Run `git diff --check` and inspect `git status --short`. Only the worker,
regression test, design, and plan files belong to this task; pre-existing user
changes remain untouched.
