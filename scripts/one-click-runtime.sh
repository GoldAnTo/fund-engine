#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly LEGACY_PROJECT="fund-engine-event"

die() {
  printf 'one-click runtime: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

require_runtime_files() {
  [[ -f "$COMPOSE_FILE" ]] || die "missing compose file: $COMPOSE_FILE"
  [[ -f "$BASE_ENV_FILE" ]] || die "missing base environment file: $BASE_ENV_FILE"
  [[ -f "$RUNTIME_ENV_FILE" ]] || die "run '$0 init' first"
}

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$BASE_ENV_FILE" --env-file "$RUNTIME_ENV_FILE" "$@"
}

init_runtime_environment() {
  require_command openssl

  if [[ -e "$RUNTIME_ENV_FILE" ]]; then
    printf 'One-click runtime environment already exists.\n'
    return 0
  fi

  local postgres_password bearer_token
  postgres_password="$(openssl rand -hex 32)"
  bearer_token="$(openssl rand -hex 32)"

  (
    umask 077
    printf '%s\n' \
      'ONE_CLICK_POSTGRES_DB=fund_engine_one_click' \
      'ONE_CLICK_POSTGRES_USER=one_click' \
      "ONE_CLICK_POSTGRES_PASSWORD=${postgres_password}" \
      "RESEARCH_BEARER_TOKEN=${bearer_token}" \
      "RESEARCH_TENANT_TOKENS={\"${bearer_token}\":\"local-one-click\"}" \
      'ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata' \
      > "$RUNTIME_ENV_FILE"
  )

  printf 'Created local one-click runtime environment.\n'
}

stop_legacy_application_services() {
  local service container_id
  for service in api frontend research-worker acquisition-worker scheduler; do
    while IFS= read -r container_id; do
      [[ -n "$container_id" ]] || continue
      docker stop "$container_id" >/dev/null
    done < <(
      docker ps -q \
        --filter "label=com.docker.compose.project=${LEGACY_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}"
    )
  done
}

start_legacy_application_services() {
  local service container_id running
  for service in api frontend research-worker acquisition-worker scheduler; do
    while IFS= read -r container_id; do
      [[ -n "$container_id" ]] || continue
      running="$(docker inspect --format '{{.State.Running}}' "$container_id")"
      [[ "$running" == "true" ]] && continue
      docker start "$container_id" >/dev/null
    done < <(
      docker ps -aq \
        --filter "label=com.docker.compose.project=${LEGACY_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}"
    )
  done
}

start_one_click_runtime() {
  require_command docker
  init_runtime_environment
  require_runtime_files
  stop_legacy_application_services
  compose up -d --build --scale acquisition-worker=3
}

stop_one_click_runtime() {
  require_command docker
  require_runtime_files
  compose down
}

show_runtime_status() {
  require_command docker
  if [[ ! -f "$RUNTIME_ENV_FILE" ]]; then
    printf 'One-click runtime is not initialized. Run %s init first.\n' "$0"
    return 0
  fi
  require_runtime_files
  compose ps
}

rollback_runtime() {
  stop_one_click_runtime
  start_legacy_application_services
  printf 'Restored legacy application containers.\n'
}

usage() {
  printf 'Usage: %s {init|up|down|status|rollback}\n' "$0" >&2
}

case "${1:-}" in
  init) init_runtime_environment ;;
  up) start_one_click_runtime ;;
  down) stop_one_click_runtime ;;
  status) show_runtime_status ;;
  rollback) rollback_runtime ;;
  *) usage; exit 2 ;;
esac
