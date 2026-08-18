#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly LEGACY_PROJECT="fund-engine-event"
readonly LEGACY_DATABASE_SERVICE="postgres"
readonly LEGACY_DATABASE_CONTAINER="fund-engine-event-postgres-1"

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

require_healthy_service() {
  local service="$1"
  local container_id health_status
  local count=0
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    count=$((count + 1))
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    [[ "$health_status" == "healthy" ]] || die "service is not healthy: $service"
  done < <(compose ps -q "$service")
  [[ "$count" -gt 0 ]] || die "service has no containers: $service"
}

require_revision() {
  local actual="$1"
  local expected="$2"
  [[ "$actual" == "$expected" ]] || die "expected Alembic revision ${expected}, got ${actual:-none}"
}

main() {
  local database_user database_name new_revision legacy_container legacy_name legacy_project legacy_service legacy_running legacy_revision

  require_command docker
  require_command curl
  require_file "$COMPOSE_FILE"
  require_file "$BASE_ENV_FILE"
  require_file "$RUNTIME_ENV_FILE"

  compose config -q

  for service in postgres api research-worker acquisition-worker frontend; do
    require_running_service "$service"
  done
  for service in research-worker acquisition-worker; do
    require_healthy_service "$service"
  done

  curl --fail --silent --show-error http://127.0.0.1:8000/health >/dev/null
  curl --fail --silent --show-error http://127.0.0.1:8080/health >/dev/null

  database_user="$(runtime_env_value ONE_CLICK_POSTGRES_USER)"
  database_name="$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  new_revision="$(compose exec -T postgres psql -U "$database_user" -d "$database_name" -Atc 'SELECT version_num FROM alembic_version;')"
  require_revision "$new_revision" 0059

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

  printf 'One-click runtime is healthy; isolated database is at 0059 and legacy database remains at 0062.\n'
}

main "$@"
