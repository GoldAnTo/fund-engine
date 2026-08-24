#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly LEGACY_PROJECT="fund-engine-event"
readonly LEGACY_DATABASE_SERVICE="postgres"
readonly LEGACY_DATABASE_CONTAINER="fund-engine-event-postgres-1"
readonly API_URL="${ONE_CLICK_API_URL:-http://127.0.0.1:${ONE_CLICK_API_PORT:-8000}}"
readonly FRONTEND_URL="${ONE_CLICK_FRONTEND_URL:-http://127.0.0.1:${ONE_CLICK_FRONTEND_PORT:-8080}}"

die() {
  printf 'one-click runtime verification: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

require_file() {
  [[ -f "$1" ]] || die "missing required file: $1"
}

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$BASE_ENV_FILE" --env-file "$RUNTIME_ENV_FILE" "$@"
}

runtime_env_value() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$RUNTIME_ENV_FILE" || true)"
  [[ -n "$line" ]] || die "missing ${key} in runtime environment"
  printf '%s' "${line#*=}"
}

require_running_service() {
  local service="$1"
  compose ps --status running --services | grep -Fxq "$service" || die "service is not running: $service"
}

require_expected_healthy_replicas() {
  local service="$1"
  local expected_count="$2"
  local container_id health_status
  local running_status
  local container_ids=()
  mapfile -t container_ids < <(compose ps --all --quiet "$service")
  [[ "${#container_ids[@]}" -eq "$expected_count" ]] || die "expected ${expected_count} containers for ${service}, got ${#container_ids[@]}"
  for container_id in "${container_ids[@]}"; do
    [[ -n "$container_id" ]] || continue
    running_status="$(docker inspect --format '{{.State.Status}}' "$container_id")"
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    [[ "$running_status" == "running" ]] || die "service is not running: $service"
    [[ "$health_status" == "healthy" ]] || die "service is not healthy: $service"
  done
}

require_revision() {
  local actual="$1"
  local expected="$2"
  [[ "$actual" == "$expected" ]] || die "expected Alembic revision ${expected}, got ${actual:-none}"
}

main() {
  local database_user database_name bearer_token product_objects new_revision legacy_container legacy_name legacy_project legacy_service legacy_running legacy_revision

  require_command docker
  require_command curl
  require_command python3
  require_file "$COMPOSE_FILE"
  require_file "$BASE_ENV_FILE"
  require_file "$RUNTIME_ENV_FILE"

  compose config -q

  for service in postgres api research-worker acquisition-worker frontend; do
    require_running_service "$service"
  done
  require_expected_healthy_replicas research-worker 1
  require_expected_healthy_replicas acquisition-worker 3

  curl --fail --silent --show-error "$API_URL/health" >/dev/null
  curl --fail --silent --show-error "$FRONTEND_URL/health" >/dev/null
  curl --fail --silent --show-error "$FRONTEND_URL/research" | grep -q '投资研究'

  database_user="$(runtime_env_value ONE_CLICK_POSTGRES_USER)"
  database_name="$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  bearer_token="$(runtime_env_value RESEARCH_BEARER_TOKEN)"
  product_objects="$(printf 'Authorization: Bearer %s\n' "$bearer_token" | \
    curl --fail --silent --show-error --header @- \
      "$API_URL/api/underwriting/v1/product/objects?query=CATL")"
  printf '%s' "$product_objects" | python3 -c '
import json, sys
value = json.load(sys.stdin)
items = value.get("items") if isinstance(value, dict) else None
keys = {item.get("external_key") for item in items if isinstance(item, dict)} if isinstance(items, list) else set()
required = {"CN:300750:COMPANY", "SZSE:300750"}
if not required.issubset(keys):
    raise SystemExit("CATL object foundation is incomplete")
'
  new_revision="$(compose exec -T postgres psql -U "$database_user" -d "$database_name" -Atc 'SELECT version_num FROM alembic_version;')"
  require_revision "$new_revision" 0065

  legacy_container="$(docker inspect --format '{{.Id}}' "$LEGACY_DATABASE_CONTAINER" 2>/dev/null)" \
    || die "legacy postgres container is unavailable: $LEGACY_DATABASE_CONTAINER"
  legacy_name="$(docker inspect --format '{{.Name}}' "$legacy_container")"
  legacy_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$legacy_container")"
  legacy_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$legacy_container")"
  legacy_running="$(docker inspect --format '{{.State.Running}}' "$legacy_container")"
  [[ "$legacy_name" == "/$LEGACY_DATABASE_CONTAINER" ]] || die "legacy database container identity does not match"
  [[ "$legacy_project" == "$LEGACY_PROJECT" ]] || die "legacy database container belongs to unexpected compose project"
  [[ "$legacy_service" == "$LEGACY_DATABASE_SERVICE" ]] || die "legacy database container has unexpected compose service"
  [[ "$legacy_running" == "true" ]] || die "legacy postgres container is not running"
  legacy_revision="$(docker exec "$legacy_container" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"')"
  require_revision "$legacy_revision" 0062

  printf 'One-click investment-research runtime is healthy; isolated database is at 0065 and legacy database remains at 0062.\n'
}

main "$@"
