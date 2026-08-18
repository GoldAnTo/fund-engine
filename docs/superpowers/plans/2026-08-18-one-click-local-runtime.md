# One-Click Local Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the current one-click automatic-research application at `127.0.0.1:8080` and `127.0.0.1:8000` on a new durable Docker database without changing the legacy Alembic-`0062` database.

**Architecture:** Add a `fund-engine-one-click` Compose project: isolated PostgreSQL, one-off migration, API, research worker, acquisition worker, and Nginx frontend. An ignored local environment file contains generated credentials. Nginx injects the opaque token only into same-origin API proxy traffic, so the browser bundle never receives it.

**Tech Stack:** Docker Compose, PostgreSQL 16, Alembic, FastAPI/Uvicorn, Python 3.13, React/Vite, Node 22, nginx-unprivileged.

---

## File map

- Create `backend/Dockerfile`: current backend API and worker image, including Alembic files.
- Create `frontend/Dockerfile`: current production frontend image.
- Create `frontend/nginx.one-click.conf.template`: same-origin API proxy with server-side token injection.
- Create `docker-compose.one-click.yml`: isolated database and runtime topology.
- Create `.env.one-click.example`: non-secret configuration template.
- Create `scripts/one-click-runtime.sh`: init, up, down, status and rollback operations.
- Create `scripts/verify-one-click-runtime.sh`: health and migration isolation checks.
- Create `backend/tests/test_one_click_runtime_assets.py`: runtime boundary regression tests.
- Modify `.gitignore`: ignore only `.env.one-click.local`.
- Modify `README.md`: startup and rollback instructions.

## Task 1: Build images without exposing the bearer token

**Files:**
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.one-click.conf.template`
- Create: `backend/tests/test_one_click_runtime_assets.py`

- [ ] **Step 1: Write the failing asset test**

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]

def test_runtime_images_include_migrations_and_server_side_auth() -> None:
    backend = (ROOT / "backend" / "Dockerfile").read_text()
    frontend = (ROOT / "frontend" / "Dockerfile").read_text()
    nginx = (ROOT / "frontend" / "nginx.one-click.conf.template").read_text()
    assert "COPY alembic ./alembic" in backend
    assert "USER app" in backend
    assert "npm run build" in frontend
    assert 'proxy_set_header Authorization "Bearer ${RESEARCH_BEARER_TOKEN}";' in nginx
    assert "VITE_RESEARCH_BEARER_TOKEN" not in nginx
```

- [ ] **Step 2: Run RED verification**

Run: `cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py`

Expected: FAIL because the assets are absent.

- [ ] **Step 3: Add the backend image**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
WORKDIR /app
RUN python -m venv /opt/venv && addgroup --system app && adduser --system --ingroup app --home /nonexistent --no-create-home app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Add the frontend image and proxy template**

Create `frontend/Dockerfile`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.one-click.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
```

Create `frontend/nginx.one-click.conf.template`:

```nginx
server {
    listen 8080;
    root /usr/share/nginx/html;
    index index.html;
    location = /health { add_header Content-Type text/plain; return 200 "ok\n"; }
    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Authorization "Bearer ${RESEARCH_BEARER_TOKEN}";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location / { try_files $uri $uri/ /index.html; }
}
```

- [ ] **Step 5: Verify GREEN and images**

Run:
```bash
cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py
cd .. && docker build -t fund-engine-one-click-backend:local backend
docker build -t fund-engine-one-click-frontend:local frontend
```

Expected: test passes and both images build.

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile frontend/Dockerfile frontend/nginx.one-click.conf.template backend/tests/test_one_click_runtime_assets.py
git commit -m "ops: add one-click runtime images"
```

## Task 2: Define the isolated Compose topology

**Files:**
- Create: `docker-compose.one-click.yml`
- Create: `.env.one-click.example`
- Modify: `.gitignore`
- Modify: `backend/tests/test_one_click_runtime_assets.py`

- [ ] **Step 1: Extend the test for separate data and all required services**

```python
def test_compose_has_separate_data_and_automatic_services() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    assert "fund-engine-one-click-data" in compose
    for name in ("postgres:", "migrate:", "api:", "research-worker:", "acquisition-worker:", "frontend:"):
        assert name in compose
    assert "127.0.0.1:8000:8000" in compose
    assert "127.0.0.1:8080:8080" in compose
    assert "fund-engine-event" not in compose
```

- [ ] **Step 2: Run RED verification**

Run: `cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py`

Expected: FAIL because the Compose file is absent.

- [ ] **Step 3: Add the Compose file**

Create `docker-compose.one-click.yml` with project name `fund-engine-one-click`.

- `postgres` uses image `postgres:16`, named volume `fund-engine-one-click-data`, no host port, and `pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB` health checking.
- `migrate` runs `alembic upgrade head`, has `restart: "no"`, and waits for PostgreSQL health.
- `api` uses host port `127.0.0.1:8000:8000`, waits for successful migration, and receives `RESEARCH_TENANT_TOKENS`, `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.
- `research-worker` runs `python -m app.scripts.run_research_worker --loop --poll-seconds 1`.
- `acquisition-worker` runs `python -m app.scripts.run_acquisition_worker --loop --poll-seconds 1` and receives `GILDATA_TOKEN` plus `ACQUISITION_ENABLED_ADAPTERS`.
- `frontend` uses host port `127.0.0.1:8080:8080` and receives only `RESEARCH_BEARER_TOKEN`.
- All long-lived services use `restart: unless-stopped`, the current local image names, and the shared database URL:
  ```
  postgresql+psycopg://${ONE_CLICK_POSTGRES_USER:?}:${ONE_CLICK_POSTGRES_PASSWORD:?}@postgres:5432/${ONE_CLICK_POSTGRES_DB:?}
  ```

- [ ] **Step 4: Add the local configuration template**

Create `.env.one-click.example`:

```dotenv
ONE_CLICK_POSTGRES_DB=fund_engine_one_click
ONE_CLICK_POSTGRES_USER=one_click
ONE_CLICK_POSTGRES_PASSWORD=replace-with-generated-local-password
RESEARCH_BEARER_TOKEN=replace-with-generated-local-token
RESEARCH_TENANT_TOKENS={"replace-with-generated-local-token":"local-one-click"}
ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata
```

Add exactly `.env.one-click.local` to `.gitignore`.

- [ ] **Step 5: Verify configuration**

Run:
```bash
cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py
docker compose -f docker-compose.one-click.yml --env-file .env --env-file .env.one-click.example config --quiet
```

Expected: tests pass and Compose renders without actual secrets.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.one-click.yml .env.one-click.example .gitignore backend/tests/test_one_click_runtime_assets.py
git commit -m "ops: define isolated one-click compose stack"
```

## Task 3: Add idempotent local operations

**Files:**
- Create: `scripts/one-click-runtime.sh`
- Create: `scripts/verify-one-click-runtime.sh`
- Modify: `backend/tests/test_one_click_runtime_assets.py`

- [ ] **Step 1: Write failing operator tests**

```python
def test_operator_generates_private_credentials_without_printing_them() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()
    assert "umask 077" in script
    assert "openssl rand -hex 32" in script
    assert "RESEARCH_TENANT_TOKENS" in script
    assert "cat .env.one-click.local" not in script

def test_operator_stops_only_old_application_services() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()
    for service in ("api", "frontend", "research-worker", "acquisition-worker", "scheduler"):
        assert service in script
    section = script.split("stop_legacy_application_services", 1)[1].split("}", 1)[0]
    assert "postgres" not in section
    assert "keycloak" not in section
```

- [ ] **Step 2: Run RED verification**

Run: `cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py`

Expected: FAIL because operator scripts are absent.

- [ ] **Step 3: Implement the operator**

Create `scripts/one-click-runtime.sh` with commands `init`, `up`, `down`, `status`, and `rollback`.

- `init` creates `.env.one-click.local` once with `umask 077`, `openssl rand -hex 32`, and `printf`; it writes a generated database password, bearer token, matching `RESEARCH_TENANT_TOKENS` JSON, and adapter list without printing any generated value.
- `up` initializes credentials, stops only existing Docker containers labelled project `fund-engine-event` and service `api`, `frontend`, `research-worker`, `acquisition-worker`, or `scheduler`; then executes:
  ```bash
  docker compose -f "$repo_root/docker-compose.one-click.yml" --env-file "$repo_root/.env" --env-file "$runtime_env" up -d --build --scale acquisition-worker=3
  ```
- `down` and `rollback` run Compose `down` without `--volumes`. Rollback must state the old application stack needs its original compose project restarted and must not alter legacy data.

- [ ] **Step 4: Implement the verifier**

Create `scripts/verify-one-click-runtime.sh` that requires the private env file, validates rendered Compose config, requires 200 from both health endpoints, requires running API/research/acquisition/frontend services, requires isolated database revision `0059`, and requires legacy `fund-engine-event-postgres-1` revision `0062`.

- [ ] **Step 5: Verify scripts**

Run:
```bash
cd backend && .venv/bin/pytest -q tests/test_one_click_runtime_assets.py
bash -n scripts/one-click-runtime.sh scripts/verify-one-click-runtime.sh
scripts/one-click-runtime.sh init
stat -f '%Lp' .env.one-click.local
```

Expected: tests pass, scripts parse, initialization is idempotent, and private config is mode `600`.

- [ ] **Step 6: Commit**

```bash
git add scripts/one-click-runtime.sh scripts/verify-one-click-runtime.sh backend/tests/test_one_click_runtime_assets.py
git commit -m "ops: add one-click runtime operator"
```

## Task 4: Switch and prove the live runtime

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the commands**

Add:

```markdown
### Isolated one-click Docker runtime

This stack owns a separate PostgreSQL volume and never changes the legacy
`fund-engine-event` database.

```bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh
```

It serves `http://127.0.0.1:8080/events/new`; `down` preserves its data volume.
```

- [ ] **Step 2: Confirm legacy revision before cutover**

Run:
```bash
docker exec fund-engine-event-postgres-1 psql -U evidence -d evidence -tAc 'SELECT version_num FROM alembic_version'
```

Expected: `0062`.

- [ ] **Step 3: Start the isolated runtime**

Run:
```bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh
```

Expected: old application containers stop, isolated PostgreSQL upgrades to `0059`, and API, research worker, three acquisition workers, and frontend run.

- [ ] **Step 4: Verify the entry and regressions**

Run:
```bash
curl --fail --silent http://127.0.0.1:8080/events/new | rg '自动研究|研究主题或材料'
cd backend && .venv/bin/pytest -q tests/test_automatic_research_api.py tests/test_automatic_research_pipeline.py tests/test_one_click_runtime_assets.py
cd ../frontend && npm run typecheck && npm test -- --run src/tests/EventCreatePage.test.tsx src/tests/AutomaticResearchPage.test.tsx
```

Expected: current automatic entry is served and selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: run one-click research locally"
```

## Final acceptance checklist

- [ ] Ports `8080` and `8000` serve current-main images.
- [ ] Legacy PostgreSQL and Keycloak remain running; legacy revision remains `0062`.
- [ ] Isolated PostgreSQL reaches current head `0059`.
- [ ] API, research worker, three acquisition workers, and frontend are supervised.
- [ ] The browser bundle has no bearer token.
- [ ] Down/rollback preserve the isolated database volume.
