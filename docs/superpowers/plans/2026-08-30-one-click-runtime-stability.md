# One-Click Runtime Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the default one-click stack healthy on a 16 GiB Mac by using one acquisition worker, bounded PostgreSQL pools, explicit service resource ceilings, serialized builds, and a sustained stability gate.

**Architecture:** Add a small pure database-runtime configuration module, keep resource policy in Compose and the existing operator, and add a pure Python snapshot validator consumed by the existing verifier. The runtime topology, volumes, authentication boundary, research semantics, and rollback behavior remain unchanged.

**Tech Stack:** Python 3.11/3.13, SQLAlchemy 2, pytest, Bash, Docker Compose, PostgreSQL 16.

---

## File map

- Create `backend/app/db_runtime.py` — validate PostgreSQL pool settings and return exact SQLAlchemy engine options without opening a connection.
- Modify `backend/app/db.py` — construct the global engine through `db_runtime.database_engine_kwargs`.
- Create `backend/tests/test_db_runtime.py` — default, override, invalid-value, secrecy, and SQLite compatibility tests.
- Modify `docker-compose.one-click.yml` — add pool environment and per-service CPU/memory ceilings.
- Modify `.env.one-click.example` — document the complete low-resource profile.
- Modify `backend/tests/test_one_click_runtime_assets.py` — lock the Compose resource/pool contract.
- Modify `scripts/one-click-runtime.sh` — validate the profile, require sufficient Docker memory, serialize builds, and use configurable acquisition scale.
- Modify `backend/tests/test_one_click_runtime_scripts.py` — lock default/override scale, pre-cutover failure, and serialized build order.
- Create `scripts/one_click_stability.py` — pure Docker-inspect snapshot validation and comparison CLI.
- Create `backend/tests/test_one_click_stability.py` — exact count, health, OOM, replacement, restart, and connection-cap tests.
- Modify `scripts/verify-one-click-runtime.sh` — integrate sustained polling, bounded HTTP checks, connection budget, and safe diagnostics.
- Create `backend/tests/test_one_click_runtime_verifier.py` — verifier argument and helper-integration tests using controlled command shims.
- Modify `README.md` — document the default profile, overrides, stability gate, and Docker memory requirement.

Protected user files remain untouched:

```text
docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md
frontend/openapi.json
```

## Task 1: Bound every application PostgreSQL pool

**Files:**

- Create: `backend/app/db_runtime.py`
- Modify: `backend/app/db.py`
- Create: `backend/tests/test_db_runtime.py`

- [ ] **Step 1: Write failing tests for default and configured PostgreSQL pools**

Create `backend/tests/test_db_runtime.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.db_runtime import database_engine_kwargs


POSTGRES_URL = "postgresql+psycopg://user:secret@postgres:5432/evidence"


def test_postgres_engine_kwargs_use_the_bounded_local_defaults() -> None:
    assert database_engine_kwargs(POSTGRES_URL, {}) == {
        "future": True,
        "pool_size": 2,
        "max_overflow": 2,
        "pool_timeout": 30,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_use_lifo": True,
    }


def test_postgres_engine_kwargs_accept_bounded_explicit_values() -> None:
    environment = {
        "DATABASE_POOL_SIZE": "4",
        "DATABASE_MAX_OVERFLOW": "1",
        "DATABASE_POOL_TIMEOUT_SECONDS": "12",
        "DATABASE_POOL_RECYCLE_SECONDS": "90",
    }
    assert database_engine_kwargs(POSTGRES_URL, environment) == {
        "future": True,
        "pool_size": 4,
        "max_overflow": 1,
        "pool_timeout": 12,
        "pool_recycle": 90,
        "pool_pre_ping": True,
        "pool_use_lifo": True,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_POOL_SIZE", "0"),
        ("DATABASE_POOL_SIZE", "11"),
        ("DATABASE_MAX_OVERFLOW", "-1"),
        ("DATABASE_MAX_OVERFLOW", "11"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "0"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "121"),
        ("DATABASE_POOL_RECYCLE_SECONDS", "29"),
        ("DATABASE_POOL_RECYCLE_SECONDS", "3601"),
        ("DATABASE_POOL_SIZE", "not-an-integer"),
    ],
)
def test_postgres_engine_kwargs_reject_invalid_values_without_url_disclosure(
    name: str, value: str
) -> None:
    with pytest.raises(ValueError, match=rf"^{name} must be an integer from ") as error:
        database_engine_kwargs(POSTGRES_URL, {name: value})
    assert "secret" not in str(error.value)
    assert POSTGRES_URL not in str(error.value)


def test_sqlite_keeps_its_existing_pool_and_ignores_postgres_pool_environment() -> None:
    options = database_engine_kwargs(
        "sqlite://",
        {"DATABASE_POOL_SIZE": "invalid-for-postgres"},
    )
    assert options == {"future": True}
    engine = create_engine("sqlite://", **options)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest tests/test_db_runtime.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.db_runtime'`.

- [ ] **Step 3: Implement the pure engine-option builder**

Create `backend/app/db_runtime.py`:

```python
"""Validated runtime-only SQLAlchemy engine configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url


_BOUNDS = {
    "DATABASE_POOL_SIZE": (2, 1, 10),
    "DATABASE_MAX_OVERFLOW": (2, 0, 10),
    "DATABASE_POOL_TIMEOUT_SECONDS": (30, 1, 120),
    "DATABASE_POOL_RECYCLE_SECONDS": (300, 30, 3600),
}


def _bounded_integer(
    environment: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}") from exc
    if value < minimum or value > maximum or str(value) != raw:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def database_engine_kwargs(
    database_url: str, environment: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Return closed engine options without opening a database connection."""
    options: dict[str, object] = {"future": True}
    if make_url(database_url).get_backend_name() != "postgresql":
        return options
    values = os.environ if environment is None else environment
    parsed = {
        name: _bounded_integer(values, name, default, minimum, maximum)
        for name, (default, minimum, maximum) in _BOUNDS.items()
    }
    return options | {
        "pool_size": parsed["DATABASE_POOL_SIZE"],
        "max_overflow": parsed["DATABASE_MAX_OVERFLOW"],
        "pool_timeout": parsed["DATABASE_POOL_TIMEOUT_SECONDS"],
        "pool_recycle": parsed["DATABASE_POOL_RECYCLE_SECONDS"],
        "pool_pre_ping": True,
        "pool_use_lifo": True,
    }
```

- [ ] **Step 4: Route the application engine through the validated builder**

Modify `backend/app/db.py` so engine construction is exactly:

```python
from app.db_runtime import database_engine_kwargs

engine = create_engine(
    DATABASE_URL,
    **database_engine_kwargs(DATABASE_URL),
)
```

Keep the existing lazy import behavior, SQLite foreign-key listener, `SessionLocal`, and `get_db` unchanged.

- [ ] **Step 5: Run focused and import-regression tests GREEN**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_db_runtime.py \
  tests/test_worker_heartbeat_probe.py \
  tests/test_worker_heartbeat_publisher.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db_runtime.py backend/app/db.py backend/tests/test_db_runtime.py
git commit -m "fix: bound runtime database pools"
```

## Task 2: Add an explicit low-resource Compose profile

**Files:**

- Modify: `docker-compose.one-click.yml`
- Modify: `.env.one-click.example`
- Modify: `backend/tests/test_one_click_runtime_assets.py`

- [ ] **Step 1: Write a failing asset test for pool propagation and service ceilings**

Append to `backend/tests/test_one_click_runtime_assets.py`:

```python
def test_compose_applies_the_low_resource_profile_without_exposing_database_to_frontend() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    example = (ROOT / ".env.one-click.example").read_text()
    for variable, default in (
        ("DATABASE_POOL_SIZE", "2"),
        ("DATABASE_MAX_OVERFLOW", "2"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "30"),
        ("DATABASE_POOL_RECYCLE_SECONDS", "300"),
    ):
        assert f"{variable}: ${{{variable}:-{default}}}" in compose
        assert f"{variable}={default}" in example
    for variable, default in (
        ("ONE_CLICK_POSTGRES_MEMORY_LIMIT", "1536m"),
        ("ONE_CLICK_API_MEMORY_LIMIT", "1536m"),
        ("ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT", "768m"),
        ("ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT", "768m"),
        ("ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT", "1280m"),
        ("ONE_CLICK_FRONTEND_MEMORY_LIMIT", "256m"),
    ):
        assert f"mem_limit: ${{{variable}:-{default}}}" in compose
        assert f"{variable}={default}" in example
    for variable, default in (
        ("ONE_CLICK_POSTGRES_CPU_LIMIT", "1.5"),
        ("ONE_CLICK_API_CPU_LIMIT", "1.5"),
        ("ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT", "1.0"),
        ("ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT", "1.0"),
        ("ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT", "1.5"),
        ("ONE_CLICK_FRONTEND_CPU_LIMIT", "0.5"),
    ):
        assert f"cpus: ${{{variable}:-{default}}}" in compose
        assert f"{variable}={default}" in example
    frontend = compose[compose.index("  frontend:\n") : compose.index("\nvolumes:\n")]
    assert "DATABASE_POOL_SIZE" not in frontend
    assert "DATABASE_URL" not in frontend
```

- [ ] **Step 2: Run the asset test and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_one_click_runtime_assets.py::test_compose_applies_the_low_resource_profile_without_exposing_database_to_frontend -q
```

Expected: FAIL because the pool environment and resource ceilings are absent.

- [ ] **Step 3: Add the Compose pool environment and resource ceilings**

At the top of `docker-compose.one-click.yml`, after `name`, add:

```yaml
x-database-pool-environment: &database-pool-environment
  DATABASE_POOL_SIZE: ${DATABASE_POOL_SIZE:-2}
  DATABASE_MAX_OVERFLOW: ${DATABASE_MAX_OVERFLOW:-2}
  DATABASE_POOL_TIMEOUT_SECONDS: ${DATABASE_POOL_TIMEOUT_SECONDS:-30}
  DATABASE_POOL_RECYCLE_SECONDS: ${DATABASE_POOL_RECYCLE_SECONDS:-300}
```

Merge `*database-pool-environment` into the `environment` mapping for `api`, `research-worker`, `acquisition-worker`, and `company-research-worker`:

```yaml
    environment:
      <<: *database-pool-environment
      APP_ENV: production
```

Add these exact ceilings to the corresponding long-lived services:

```yaml
# postgres
    mem_limit: ${ONE_CLICK_POSTGRES_MEMORY_LIMIT:-1536m}
    cpus: ${ONE_CLICK_POSTGRES_CPU_LIMIT:-1.5}
# api
    mem_limit: ${ONE_CLICK_API_MEMORY_LIMIT:-1536m}
    cpus: ${ONE_CLICK_API_CPU_LIMIT:-1.5}
# research-worker
    mem_limit: ${ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT:-768m}
    cpus: ${ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT:-1.0}
# acquisition-worker
    mem_limit: ${ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT:-768m}
    cpus: ${ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT:-1.0}
# company-research-worker
    mem_limit: ${ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT:-1280m}
    cpus: ${ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT:-1.5}
# frontend
    mem_limit: ${ONE_CLICK_FRONTEND_MEMORY_LIMIT:-256m}
    cpus: ${ONE_CLICK_FRONTEND_CPU_LIMIT:-0.5}
```

- [ ] **Step 4: Add the complete example profile**

Append to `.env.one-click.example`:

```dotenv
ONE_CLICK_ACQUISITION_REPLICAS=1
DATABASE_POOL_SIZE=2
DATABASE_MAX_OVERFLOW=2
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=300
ONE_CLICK_POSTGRES_MEMORY_LIMIT=1536m
ONE_CLICK_POSTGRES_CPU_LIMIT=1.5
ONE_CLICK_API_MEMORY_LIMIT=1536m
ONE_CLICK_API_CPU_LIMIT=1.5
ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT=768m
ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT=1.0
ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT=768m
ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT=1.0
ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT=1280m
ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT=1.5
ONE_CLICK_FRONTEND_MEMORY_LIMIT=256m
ONE_CLICK_FRONTEND_CPU_LIMIT=0.5
```

- [ ] **Step 5: Render Compose and run asset tests GREEN**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest tests/test_one_click_runtime_assets.py -q
cd ..
docker compose --env-file .env.one-click.example -f docker-compose.one-click.yml config --quiet
```

Expected: all asset tests pass and Compose renders successfully.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.one-click.yml .env.one-click.example backend/tests/test_one_click_runtime_assets.py
git commit -m "ops: add one-click resource profile"
```

## Task 3: Make startup low-resource and fail before cutover

**Files:**

- Modify: `scripts/one-click-runtime.sh`
- Modify: `backend/tests/test_one_click_runtime_scripts.py`

- [ ] **Step 1: Replace the hard-coded-scale assertion with failing behavior tests**

In `backend/tests/test_one_click_runtime_scripts.py`, change the existing static assertion from three replicas to the configured value and add:

```python
def test_up_defaults_to_one_acquisition_worker_and_serializes_builds(tmp_path: Path) -> None:
    completed, commands = run_fake_up(tmp_path, docker_memory=8 * 1024**3)
    assert completed.returncode == 0
    backend_build = commands.index("compose build migrate")
    frontend_build = commands.index("compose build frontend")
    first_stop = next(i for i, command in enumerate(commands) if command.startswith("stop "))
    launch = next(i for i, command in enumerate(commands) if "up -d --no-build" in command)
    assert backend_build < frontend_build < first_stop < launch
    assert commands[launch].endswith("--scale acquisition-worker=1")


def test_up_honors_a_bounded_acquisition_worker_override(tmp_path: Path) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": "4"},
    )
    assert completed.returncode == 0
    assert any(command.endswith("--scale acquisition-worker=4") for command in commands)


@pytest.mark.parametrize("value", ["0", "5", "-1", "01", "many"])
def test_up_rejects_an_invalid_scale_before_build_or_cutover(tmp_path: Path, value: str) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": value},
    )
    assert completed.returncode != 0
    assert "ONE_CLICK_ACQUISITION_REPLICAS must be an integer from 1 through 4" in completed.stderr
    assert not any(" build " in f" {command} " for command in commands)
    assert not any(command.startswith("stop ") for command in commands)


def test_up_rejects_insufficient_docker_memory_before_build_or_cutover(tmp_path: Path) -> None:
    completed, commands = run_fake_up(tmp_path, docker_memory=5 * 1024**3)
    assert completed.returncode != 0
    assert "Docker must expose at least 6 GiB" in completed.stderr
    assert not any(" build " in f" {command} " for command in commands)
    assert not any(command.startswith("stop ") for command in commands)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ONE_CLICK_API_MEMORY_LIMIT", "unbounded"),
        ("ONE_CLICK_FRONTEND_MEMORY_LIMIT", "0m"),
        ("ONE_CLICK_API_CPU_LIMIT", "all"),
        ("ONE_CLICK_FRONTEND_CPU_LIMIT", "0"),
    ],
)
def test_up_rejects_invalid_resource_limits_before_build_or_cutover(
    tmp_path: Path, name: str, value: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={name: value},
    )
    assert completed.returncode != 0
    assert f"{name} has an invalid one-click resource limit" in completed.stderr
    assert not any(" build " in f" {command} " for command in commands)
    assert not any(command.startswith("stop ") for command in commands)
```

Refactor only the setup body of
`test_up_builds_before_cutover_and_restores_only_recorded_containers_on_failure`
into `run_fake_up(tmp_path, *, docker_memory, runtime_values=None)`. Preserve its
existing 64-character legacy IDs, volume identity, rollback branches, and
`DOCKER_LOG`. Extend that exact fake `docker` script with branches equivalent to:

```bash
[[ "$*" == "info --format {{.MemTotal}}" ]] && { printf '%s\n' "$DOCKER_MEMORY"; exit 0; }
[[ "$*" == *" build migrate"* || "$*" == *" build frontend"* ]] && exit 0
[[ "$*" == *" up -d --no-build"* ]] && exit "${FAIL_UP:-0}"
```

The helper writes `runtime_values` as additional `KEY=value` lines in the
owner-mode `0600` runtime file, exports `DOCKER_MEMORY`, runs `[script, "up"]`,
and returns `(completed, log.read_text().splitlines())`. Keep the original
rollback test as a caller that passes `FAIL_UP=1`; do not move or weaken any of
its backup, restore, volume-identity, or rollback assertions.

- [ ] **Step 2: Run the new operator tests and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_one_click_runtime_scripts.py \
  -k 'defaults_to_one or bounded_acquisition or invalid_scale or invalid_resource or insufficient_docker_memory' -q
```

Expected: failures show the hard-coded three-worker launch, single combined build, and missing memory guard.

- [ ] **Step 3: Add optional profile lookup and bounded integer validation**

Add to `scripts/one-click-runtime.sh` after `runtime_env_value`:

```bash
configured_value_or_default() {
  local key="$1"
  local default="$2"
  local file matches value
  for file in "$RUNTIME_ENV_FILE" "$BASE_ENV_FILE"; do
    matches="$(grep -E "^${key}=" "$file" || true)"
    if [[ -n "$matches" ]]; then
      [[ "$(printf '%s\n' "$matches" | wc -l | tr -d ' ')" == "1" ]] \
        || die "duplicate ${key} in runtime configuration"
      value="${matches#*=}"
      [[ -n "$value" ]] || die "empty ${key} in runtime configuration"
      printf '%s' "$value"
      return 0
    fi
  done
  printf '%s' "$default"
}

bounded_integer_setting() {
  local key="$1" default="$2" minimum="$3" maximum="$4"
  local value
  value="$(configured_value_or_default "$key" "$default")"
  [[ "$value" =~ ^(0|[1-9][0-9]*)$ ]] \
    && (( value >= minimum && value <= maximum )) \
    || die "${key} must be an integer from ${minimum} through ${maximum}"
  printf '%s' "$value"
}

validate_runtime_profile() {
  bounded_integer_setting ONE_CLICK_ACQUISITION_REPLICAS 1 1 4 >/dev/null
  bounded_integer_setting DATABASE_POOL_SIZE 2 1 10 >/dev/null
  bounded_integer_setting DATABASE_MAX_OVERFLOW 2 0 10 >/dev/null
  bounded_integer_setting DATABASE_POOL_TIMEOUT_SECONDS 30 1 120 >/dev/null
  bounded_integer_setting DATABASE_POOL_RECYCLE_SECONDS 300 30 3600 >/dev/null

  local key default value
  while IFS=' ' read -r key default; do
    value="$(configured_value_or_default "$key" "$default")"
    [[ "$value" =~ ^[1-9][0-9]*(b|k|m|g)$ ]] \
      || die "${key} has an invalid one-click resource limit"
  done <<'EOF'
ONE_CLICK_POSTGRES_MEMORY_LIMIT 1536m
ONE_CLICK_API_MEMORY_LIMIT 1536m
ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT 768m
ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT 768m
ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT 1280m
ONE_CLICK_FRONTEND_MEMORY_LIMIT 256m
EOF
  while IFS=' ' read -r key default; do
    value="$(configured_value_or_default "$key" "$default")"
    [[ "$value" =~ ^([1-9][0-9]*|0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*\.[0-9]+)$ ]] \
      || die "${key} has an invalid one-click resource limit"
  done <<'EOF'
ONE_CLICK_POSTGRES_CPU_LIMIT 1.5
ONE_CLICK_API_CPU_LIMIT 1.5
ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT 1.0
ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT 1.0
ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT 1.5
ONE_CLICK_FRONTEND_CPU_LIMIT 0.5
EOF
}
```

Runtime-file values take precedence over the root `.env`, matching the order
of the two Compose `--env-file` arguments.

- [ ] **Step 4: Add the Docker memory guard**

Add:

```bash
validate_docker_memory() {
  local memory_bytes
  memory_bytes="$(docker info --format '{{.MemTotal}}')" \
    || die "unable to read Docker memory"
  [[ "$memory_bytes" =~ ^[1-9][0-9]*$ ]] \
    || die "Docker reported an invalid memory total"
  (( memory_bytes >= 6 * 1024 * 1024 * 1024 )) \
    || die "Docker must expose at least 6 GiB for the one-click runtime"
  if (( memory_bytes < 8 * 1024 * 1024 * 1024 )); then
    printf 'one-click runtime: warning: Docker exposes less than 8 GiB; the 6 GiB profile has limited headroom\n' >&2
  fi
}
```

- [ ] **Step 5: Serialize builds and launch the configured scale**

In `start_one_click_runtime`, before any build or legacy-stop action, run:

```bash
  local acquisition_replicas
  validate_runtime_profile
  compose config -q
  validate_docker_memory
  acquisition_replicas="$(bounded_integer_setting ONE_CLICK_ACQUISITION_REPLICAS 1 1 4)"
  compose build migrate
  compose build frontend
```

Remove the existing combined `compose build`. Change the launch to:

```bash
  compose up -d --no-build \
    --scale "acquisition-worker=${acquisition_replicas}" \
    || die "one-click startup failed"
```

Keep volume validation, stop order, and rollback traps unchanged.

- [ ] **Step 6: Extend newly generated private environments without rewriting old ones**

Add these non-secret lines to the payload in `create_runtime_env_file`:

```text
ONE_CLICK_ACQUISITION_REPLICAS=1
DATABASE_POOL_SIZE=2
DATABASE_MAX_OVERFLOW=2
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=300
```

Do not append them to an existing private file; missing values intentionally use
backward-compatible defaults.

- [ ] **Step 7: Run the full operator suite GREEN**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_one_click_runtime_scripts.py \
  tests/test_one_click_runtime_assets.py -q
```

Expected: all tests pass, including existing rollback and backup/restore cases.

- [ ] **Step 8: Commit**

```bash
git add scripts/one-click-runtime.sh backend/tests/test_one_click_runtime_scripts.py
git commit -m "ops: default one-click runtime to low resource"
```

## Task 4: Add a pure stability snapshot validator

**Files:**

- Create: `scripts/one_click_stability.py`
- Create: `backend/tests/test_one_click_stability.py`

- [ ] **Step 1: Write failing pure tests for snapshot and connection invariants**

Create `backend/tests/test_one_click_stability.py`:

```python
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from one_click_stability import (  # noqa: E402
    connection_cap,
    stable_snapshot,
    validate_snapshot,
)


def container(service: str, suffix: str = "1") -> dict:
    return {
        "Id": hashlib.sha256(f"{service}:{suffix}".encode()).hexdigest(),
        "Name": f"/fund-engine-one-click-{service}-{suffix}",
        "Config": {"Labels": {"com.docker.compose.service": service}},
        "RestartCount": 0,
        "State": {
            "Status": "running",
            "OOMKilled": False,
            "Health": {"Status": "healthy"},
        },
    }


def healthy_payload() -> list[dict]:
    return [
        container("postgres"),
        container("api"),
        container("research-worker"),
        container("acquisition-worker"),
        container("company-research-worker"),
        container("frontend"),
    ]


def test_snapshot_requires_exact_healthy_non_oom_service_counts() -> None:
    expected = {
        "postgres": 1,
        "api": 1,
        "research-worker": 1,
        "acquisition-worker": 1,
        "company-research-worker": 1,
        "frontend": 1,
    }
    snapshot = validate_snapshot(healthy_payload(), expected)
    assert snapshot["api"][0]["restart_count"] == 0
    for mutate, message in (
        (lambda rows: rows.pop(), "frontend: expected 1 containers, got 0"),
        (lambda rows: rows[1]["State"].update(Status="exited"), "api: container is not running"),
        (lambda rows: rows[1]["State"]["Health"].update(Status="unhealthy"), "api: container is not healthy"),
        (lambda rows: rows[1]["State"].update(OOMKilled=True), "api: container reports OOMKilled"),
    ):
        payload = copy.deepcopy(healthy_payload())
        mutate(payload)
        with pytest.raises(ValueError, match=message):
            validate_snapshot(payload, expected)


def test_stability_rejects_container_replacement_or_restart() -> None:
    expected = {service: 1 for service in (
        "postgres", "api", "research-worker", "acquisition-worker",
        "company-research-worker", "frontend",
    )}
    baseline = validate_snapshot(healthy_payload(), expected)
    replaced_payload = healthy_payload()
    replaced_payload[1]["Id"] = "f" * 64
    with pytest.raises(ValueError, match="api: container identity changed"):
        stable_snapshot(baseline, validate_snapshot(replaced_payload, expected))
    restarted_payload = healthy_payload()
    restarted_payload[1]["RestartCount"] = 1
    with pytest.raises(ValueError, match="api: restart count changed"):
        stable_snapshot(baseline, validate_snapshot(restarted_payload, expected))


@pytest.mark.parametrize(
    ("replicas", "pool_size", "overflow", "expected"),
    [(1, 2, 2, 21), (4, 2, 2, 33), (1, 4, 1, 25)],
)
def test_connection_cap_tracks_process_and_pool_limits(
    replicas: int, pool_size: int, overflow: int, expected: int
) -> None:
    assert connection_cap(replicas, pool_size, overflow) == expected
```

- [ ] **Step 2: Run the pure tests and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest tests/test_one_click_stability.py -q
```

Expected: collection fails because `scripts/one_click_stability.py` is absent.

- [ ] **Step 3: Implement exact snapshot validation**

Create `scripts/one_click_stability.py` with these public functions:

```python
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence


def connection_cap(replicas: int, pool_size: int, max_overflow: int) -> int:
    if replicas < 1 or pool_size < 1 or max_overflow < 0:
        raise ValueError("connection-cap inputs are invalid")
    return (3 + replicas) * (pool_size + max_overflow) + 5


def validate_snapshot(
    payload: object, expected_counts: Mapping[str, int]
) -> dict[str, list[dict[str, object]]]:
    if not isinstance(payload, list):
        raise ValueError("docker inspect payload must be an array")
    grouped = {service: [] for service in expected_counts}
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("docker inspect item must be an object")
        labels = raw.get("Config", {}).get("Labels", {})
        service = labels.get("com.docker.compose.service")
        if service not in grouped:
            continue
        state = raw.get("State")
        if not isinstance(state, dict):
            raise ValueError(f"{service}: container state is malformed")
        health = state.get("Health")
        if state.get("Status") != "running":
            raise ValueError(f"{service}: container is not running")
        if not isinstance(health, dict) or health.get("Status") != "healthy":
            raise ValueError(f"{service}: container is not healthy")
        if state.get("OOMKilled") is not False:
            raise ValueError(f"{service}: container reports OOMKilled")
        container_id = raw.get("Id")
        restart_count = raw.get("RestartCount")
        if (
            not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        ):
            raise ValueError(f"{service}: container identity is malformed")
        if not isinstance(restart_count, int) or restart_count < 0:
            raise ValueError(f"{service}: restart count is malformed")
        grouped[service].append({"id": container_id, "restart_count": restart_count})
    for service, expected in expected_counts.items():
        actual = len(grouped[service])
        if actual != expected:
            raise ValueError(f"{service}: expected {expected} containers, got {actual}")
        grouped[service].sort(key=lambda item: str(item["id"]))
    return grouped


def stable_snapshot(
    baseline: Mapping[str, Sequence[Mapping[str, object]]],
    current: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    for service in baseline:
        before = list(baseline[service])
        after = list(current.get(service, ()))
        if [item["id"] for item in before] != [item["id"] for item in after]:
            raise ValueError(f"{service}: container identity changed")
        if [item["restart_count"] for item in before] != [item["restart_count"] for item in after]:
            raise ValueError(f"{service}: restart count changed")


def _expectation(raw: str) -> tuple[str, int]:
    service, separator, count_text = raw.partition("=")
    if not separator or not service or re.fullmatch(r"[1-9][0-9]*", count_text) is None:
        raise ValueError("--expect must use service=positive-integer")
    return service, int(count_text)


def _json_from_stream(stream, message: str) -> object:
    try:
        return json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(message) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--expect", action="append", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("baseline")
    compare.add_argument("current")
    cap = commands.add_parser("connection-cap")
    cap.add_argument("--replicas", type=int, required=True)
    cap.add_argument("--pool-size", type=int, required=True)
    cap.add_argument("--max-overflow", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "snapshot":
            expected: dict[str, int] = {}
            for raw in arguments.expect:
                service, count = _expectation(raw)
                if service in expected:
                    raise ValueError(f"duplicate expected service: {service}")
                expected[service] = count
            result = validate_snapshot(
                _json_from_stream(sys.stdin, "docker inspect payload is invalid JSON"),
                expected,
            )
            json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
            sys.stdout.write("\n")
        elif arguments.command == "compare":
            try:
                with open(arguments.baseline, encoding="utf-8") as baseline_file:
                    baseline = _json_from_stream(baseline_file, "baseline snapshot is invalid JSON")
                with open(arguments.current, encoding="utf-8") as current_file:
                    current = _json_from_stream(current_file, "current snapshot is invalid JSON")
            except OSError as exc:
                raise ValueError("snapshot file cannot be read") from exc
            if not isinstance(baseline, dict) or not isinstance(current, dict):
                raise ValueError("snapshot file is malformed")
            stable_snapshot(baseline, current)
        else:
            print(
                connection_cap(
                    arguments.replicas,
                    arguments.pool_size,
                    arguments.max_overflow,
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "snapshot file is malformed"
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The CLI exposes three subcommands:

```text
snapshot --expect service=count [--expect service=count ...]
compare BASELINE_JSON CURRENT_JSON
connection-cap --replicas N --pool-size N --max-overflow N
```

`snapshot` reads Docker inspect JSON from stdin and writes canonical compact JSON
with sorted keys. `compare` loads the two snapshot files and produces no output
on success. Every `ValueError` prints only its bounded message to stderr and exits
2; no input JSON is echoed.

- [ ] **Step 4: Add CLI round-trip tests**

Append tests using `subprocess.run` that:

```python
def test_snapshot_cli_emits_canonical_json_and_compare_is_silent(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "one_click_stability.py"
    arguments = [
        sys.executable, str(script), "snapshot",
        "--expect", "postgres=1", "--expect", "api=1",
        "--expect", "research-worker=1", "--expect", "acquisition-worker=1",
        "--expect", "company-research-worker=1", "--expect", "frontend=1",
    ]
    first = subprocess.run(arguments, input=json.dumps(healthy_payload()), text=True, capture_output=True)
    assert first.returncode == 0
    assert first.stderr == ""
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(first.stdout)
    current.write_text(first.stdout)
    compared = subprocess.run(
        [sys.executable, str(script), "compare", str(baseline), str(current)],
        text=True, capture_output=True,
    )
    assert compared.returncode == 0
    assert compared.stdout == compared.stderr == ""
```

Add the missing `json` and `subprocess` imports to the test file.

- [ ] **Step 5: Run validator tests GREEN**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest tests/test_one_click_stability.py -q
```

Expected: all validator and CLI tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/one_click_stability.py backend/tests/test_one_click_stability.py
git commit -m "ops: validate one-click stability snapshots"
```

## Task 5: Integrate sustained verification and safe diagnostics

**Files:**

- Modify: `scripts/verify-one-click-runtime.sh`
- Create: `backend/tests/test_one_click_runtime_verifier.py`
- Modify: `backend/tests/test_one_click_runtime_scripts.py`

- [ ] **Step 1: Write failing verifier argument and static integration tests**

Create `backend/tests/test_one_click_runtime_verifier.py`:

```python
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def copied_verifier(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    verifier = scripts / "verify-one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "verify-one-click-runtime.sh", verifier)
    shutil.copy(ROOT / "scripts" / "one_click_stability.py", scripts / "one_click_stability.py")
    verifier.chmod(verifier.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "docker-compose.one-click.yml").touch()
    (tmp_path / ".env").touch()
    (tmp_path / ".env.one-click.local").write_text(
        "ONE_CLICK_POSTGRES_USER=one_click\n"
        "ONE_CLICK_POSTGRES_DB=fund_engine_one_click\n"
        "RESEARCH_BEARER_TOKEN=token\n"
    )
    return verifier


@pytest.mark.parametrize("arguments", [["--stability-seconds"], ["--stability-seconds", "-1"], ["--unknown"]])
def test_verifier_rejects_invalid_arguments_before_docker(tmp_path: Path, arguments: list[str]) -> None:
    verifier = copied_verifier(tmp_path)
    completed = subprocess.run([verifier, *arguments], text=True, capture_output=True, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert completed.returncode != 0
    assert "usage:" in completed.stderr


def test_verifier_declares_bounded_http_stability_and_diagnostics() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()
    assert "--stability-seconds" in script
    assert "one_click_stability.py" in script
    assert "--connect-timeout 2 --max-time 5" in script
    assert "pg_stat_activity" in script
    assert "docker stats --no-stream" in script
    assert "docker logs --tail 40" in script
    assert "sleep 5" in script
    assert '--expect "acquisition-worker=${acquisition_replicas}"' in script


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("oom", "api: container reports OOMKilled"),
        ("restart", "api: restart count changed"),
        ("replacement", "api: container identity changed"),
        ("connections", "database connections exceed the configured one-click budget"),
        ("transient-http", "curl"),
    ],
)
def test_sustained_verifier_fails_closed_and_emits_bounded_diagnostics(
    tmp_path: Path, mode: str, message: str
) -> None:
    completed, calls = run_fake_verifier(
        tmp_path, mode=mode, arguments=["--stability-seconds", "1"]
    )
    assert completed.returncode != 0
    assert message in completed.stderr
    assert "stats --no-stream" in calls
    assert "logs --tail 40" in calls
    assert "Config.Env" not in completed.stderr
    assert "RESEARCH_BEARER_TOKEN" not in completed.stderr
```

Implement `run_fake_verifier` beside `copied_verifier`. It creates executable
`docker`, `curl`, and `sleep` shims in a temporary `PATH`, a JSON inspect payload
for exactly the six expected default-profile containers, and a call log. The
shim contract is exhaustive:

- `docker compose ... config -q`, `ps --status running --services`, and `ps
  --all --quiet` succeed and return the required services/IDs;
- `docker inspect ID...` emits the JSON payload; on the second payload request,
  `restart` increments top-level `RestartCount`, `replacement` changes the API
  ID, and `oom` sets `State.OOMKilled=true` on the first request;
- formatted legacy-container inspect calls return the canonical 64-hex ID,
  expected name/project/service, and `true` running state;
- one-click and legacy Alembic queries return `0070` and `0062` respectively;
- the `pg_stat_activity` query returns `22` only in `connections`, otherwise a
  value under the derived cap;
- `curl` emits the expected health/research/product payloads and exits nonzero
  on the second health request only in `transient-http`;
- `sleep` returns immediately so a one-second requested window exercises both
  the baseline and final poll without slowing the test;
- `docker logs --tail 40` and `docker stats --no-stream` emit fixed bounded
  sentinel text, never environment or secret values.

Assert the same harness in `healthy` mode exits zero, makes at least two inspect
payload requests, checks both Alembic revisions at least twice, and performs no
diagnostic calls. This dynamic test is the integration proof; the pure tests in
Task 4 remain the detailed invariant tests.

- [ ] **Step 2: Run the new verifier tests and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest tests/test_one_click_runtime_verifier.py -q
```

Expected: failures show the missing option, helper integration, bounded curl,
database budget, sustained failure modes, and diagnostics.

- [ ] **Step 3: Parse the sustained-verification option before external commands**

At the start of `verify-one-click-runtime.sh`, define:

```bash
readonly STABILITY_HELPER="$REPO_ROOT/scripts/one_click_stability.py"
STABILITY_SECONDS=0

usage() {
  printf 'usage: %s [--stability-seconds NON_NEGATIVE_INTEGER]\n' "$0" >&2
}

parse_arguments() {
  while (( $# > 0 )); do
    case "$1" in
      --stability-seconds)
        (( $# == 2 )) || { usage; return 2; }
        [[ "$2" =~ ^(0|[1-9][0-9]*)$ ]] || { usage; return 2; }
        STABILITY_SECONDS="$2"
        shift 2
        ;;
      *) usage; return 2 ;;
    esac
  done
}
```

Call `parse_arguments "$@"` before `main`; change `main` to accept no arguments.

- [ ] **Step 4: Add profile resolution, bounded HTTP, and connection budget**

Add optional-runtime lookup equivalent to the operator's precedence rules, then:

```bash
acquisition_replicas="$(bounded_integer_setting ONE_CLICK_ACQUISITION_REPLICAS 1 1 4)"
pool_size="$(bounded_integer_setting DATABASE_POOL_SIZE 2 1 10)"
max_overflow="$(bounded_integer_setting DATABASE_MAX_OVERFLOW 2 0 10)"
connection_limit="$(python3 "$STABILITY_HELPER" connection-cap \
  --replicas "$acquisition_replicas" \
  --pool-size "$pool_size" \
  --max-overflow "$max_overflow")"
```

Use this HTTP command for API, frontend health, and `/research`:

```bash
curl --fail --silent --show-error --connect-timeout 2 --max-time 5
```

Add:

```bash
require_connection_budget() {
  local actual
  actual="$(compose exec -T postgres psql -U "$database_user" -d "$database_name" -Atc \
    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();")"
  [[ "$actual" =~ ^[0-9]+$ ]] || die "database connection count is invalid"
  (( actual <= connection_limit )) \
    || die "database connections exceed the configured one-click budget"
}
```

- [ ] **Step 5: Add canonical snapshots and sustained comparison**

Implement:

```bash
write_snapshot() {
  local destination="$1"
  local id
  local ids=()
  while IFS= read -r id; do
    [[ -n "$id" ]] && ids+=("$id")
  done < <(compose ps --all --quiet)
  (( ${#ids[@]} > 0 )) || die "one-click runtime has no containers"
  docker inspect "${ids[@]}" | python3 "$STABILITY_HELPER" snapshot \
    --expect postgres=1 \
    --expect api=1 \
    --expect research-worker=1 \
    --expect "acquisition-worker=${acquisition_replicas}" \
    --expect company-research-worker=1 \
    --expect frontend=1 > "$destination"
}
```

Use an owner-only `mktemp -d`, write `baseline.json`, and centralize one poll in
`require_runtime_invariants`. Each poll must write `current.json`, call helper
`compare`, run the bounded HTTP checks, run `require_connection_budget`, query
the one-click Alembic revision and require `0070`, then re-resolve the legacy
container ID/labels/running state and require its Alembic revision to remain
`0062`. Run this function at the start, after every five-second sleep, and once
at the exact requested deadline when the duration is not divisible by five.
Remove the temporary directory through a trap. The zero-second path still runs
one complete point-in-time poll.

- [ ] **Step 6: Add bounded failure diagnostics**

Use one `EXIT` trap so explicit `die` calls are covered as well as failed
external commands. Install it after arguments and required files are validated;
the trap must preserve the original exit status, remove the private snapshot
directory, and emit diagnostics only for a nonzero status:

```bash
emit_diagnostics() {
  printf 'one-click runtime verification diagnostics:\n' >&2
  compose ps --all >&2 || true
  local id
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    docker inspect --format '{{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' "$id" >&2 || true
    docker logs --tail 40 "$id" >&2 || true
  done < <(compose ps --all --quiet 2>/dev/null || true)
  docker stats --no-stream >&2 || true
}

cleanup_verification() {
  local status="$?"
  trap - EXIT
  [[ -z "${snapshot_directory:-}" ]] || rm -rf -- "$snapshot_directory"
  if (( status != 0 )); then
    emit_diagnostics
  fi
  exit "$status"
}

trap cleanup_verification EXIT
```

Do not print Compose config, `.env` files, `docker inspect` environment fields,
or unbounded logs. Validate that `snapshot_directory` is the exact non-empty
path returned by `mktemp -d` before installing the trap; never pass an unresolved
or broad path to recursive removal.

- [ ] **Step 7: Update existing verifier assertions and run shell tests GREEN**

Change the old three-replica assertion in
`backend/tests/test_one_click_runtime_scripts.py` to the configured variable.
Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_one_click_stability.py \
  tests/test_one_click_runtime_verifier.py \
  tests/test_one_click_runtime_scripts.py \
  tests/test_one_click_runtime_assets.py -q
/bin/bash -n ../scripts/one-click-runtime.sh ../scripts/verify-one-click-runtime.sh
```

Expected: all selected tests pass and both shell scripts parse.

- [ ] **Step 8: Commit**

```bash
git add scripts/verify-one-click-runtime.sh backend/tests/test_one_click_runtime_verifier.py backend/tests/test_one_click_runtime_scripts.py
git commit -m "ops: add sustained one-click verification"
```

## Task 6: Document and prove the live default profile

**Files:**

- Modify: `README.md`
- Modify: `backend/tests/test_one_click_runtime_scripts.py`

- [ ] **Step 1: Write a failing documentation assertion**

Extend `test_readme_documents_the_local_one_click_runtime_without_secrets`:

```python
for text in (
    "ONE_CLICK_ACQUISITION_REPLICAS=1",
    "Docker Desktop 至少分配 6 GiB",
    "scripts/verify-one-click-runtime.sh --stability-seconds 600",
    "默认只启动一个资料采集 worker",
):
    assert text in readme
```

- [ ] **Step 2: Run the documentation test and verify RED**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_one_click_runtime_scripts.py::test_readme_documents_the_local_one_click_runtime_without_secrets -q
```

Expected: FAIL because the low-resource profile is not documented.

- [ ] **Step 3: Document operation and expansion**

Update the one-click README section to state:

```markdown
默认只启动一个资料采集 worker，适合 16 GiB Mac、Docker Desktop 分配约
8 GiB 的本地环境。Docker Desktop 至少分配 6 GiB；不足时 `up` 会在停止旧服务前
失败关闭。

需要提高采集吞吐时，编辑受保护的 `.env.one-click.local`：

```dotenv
ONE_CLICK_ACQUISITION_REPLICAS=1
```

允许值为 1–4。扩容后仍必须运行同一稳定性门禁：

```bash
scripts/verify-one-click-runtime.sh --stability-seconds 600
```
```

Also document the pool variables as advanced local tuning and state that resource
ceilings are configurable in `.env.one-click.example`.

- [ ] **Step 4: Run all automated stability checks**

Run:

```bash
cd backend
/Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest \
  tests/test_db_runtime.py \
  tests/test_one_click_runtime_assets.py \
  tests/test_one_click_runtime_scripts.py \
  tests/test_one_click_stability.py \
  tests/test_one_click_runtime_verifier.py \
  tests/test_worker_heartbeat_probe.py \
  tests/test_worker_heartbeat_publisher.py \
  tests/test_investment_research_runtime.py -q
cd ..
docker compose --env-file .env.one-click.example -f docker-compose.one-click.yml config --quiet
git diff --check
```

Expected: all selected tests pass, Compose renders, and the diff has no whitespace errors.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md backend/tests/test_one_click_runtime_scripts.py
git commit -m "docs: explain one-click stability profile"
```

- [ ] **Step 6: Stop resource-heavy unrelated verification before live cutover**

Run the read-only check:

```bash
pgrep -af 'pytest|vitest|playwright' || true
```

If an unrelated user-owned test process is still running, do not terminate it.
Wait for it to finish before the live acceptance so the result measures the
runtime rather than competing verification workloads.

- [ ] **Step 7: Start and verify the live default profile**

Run from the isolated worktree, using the existing ignored local secrets only
after copying or securely referencing them without printing their values:

```bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh --stability-seconds 600
```

Expected: one acquisition worker and every other required service remain healthy
for ten minutes, container IDs/restart counts remain unchanged, OOM remains false,
both HTTP endpoints answer, the database connection budget holds, and the legacy
database stays at revision `0062`.

- [ ] **Step 8: Record final evidence without committing secrets or runtime state**

Run:

```bash
scripts/one-click-runtime.sh status
git status --short
git log --oneline --decorate -8
```

Expected: status shows the healthy low-resource stack; Git shows only intentional
tracked changes or is clean, and no `.env.one-click.local` or `.one-click-runtime`
path is tracked.

## Completion audit for sub-project 1

Before claiming runtime stability complete, verify each design requirement against
current evidence:

- Pool defaults, bounds, SQLite compatibility, stale-connection recovery: Task 1 tests.
- Resource ceilings and secret boundary: Task 2 rendered Compose and asset tests.
- One-worker default, 1-4 override, pre-cutover validation, serialized builds: Task 3 tests.
- Exact health, OOM, restart, replacement, and connection invariants: Tasks 4-5 tests.
- Ten-minute live stability, unchanged legacy database, and clean Git state: Task 6 live evidence.
- Existing backup/restore and rollback behavior: full `test_one_click_runtime_scripts.py`.

Only after every item has direct evidence may sub-project 1 be marked complete and
sub-project 2 (real Company Research E2E) begin.
