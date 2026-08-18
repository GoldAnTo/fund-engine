#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly LEGACY_PROJECT="fund-engine-event"
readonly LEGACY_STOPPED_STATE_DIR="$REPO_ROOT/.one-click-runtime"
readonly LEGACY_STOPPED_STATE_FILE="$LEGACY_STOPPED_STATE_DIR/legacy-stopped-containers"

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

legacy_service_is_allowed() {
  case "$1" in
    api|frontend|research-worker|acquisition-worker|scheduler) return 0 ;;
    *) return 1 ;;
  esac
}

begin_legacy_stop_state() {
  local temporary_state_file
  (
    umask 077
    mkdir -p "$LEGACY_STOPPED_STATE_DIR"
    chmod 700 "$LEGACY_STOPPED_STATE_DIR"
    temporary_state_file="$(mktemp "$LEGACY_STOPPED_STATE_DIR/legacy-stopped-containers.XXXXXX")"
    chmod 600 "$temporary_state_file"
    mv "$temporary_state_file" "$LEGACY_STOPPED_STATE_FILE"
  )
}

record_stopped_legacy_container() {
  local service="$1"
  local container_id="$2"
  printf '%s\t%s\n' "$service" "$container_id" >> "$LEGACY_STOPPED_STATE_FILE"
}

normalize_legacy_stop_state() {
  local service container_id extra_field actual_id temporary_state_file
  (
    umask 077
    temporary_state_file="$(mktemp "$LEGACY_STOPPED_STATE_DIR/legacy-stopped-containers.XXXXXX")" || exit 1
    chmod 600 "$temporary_state_file"
    while IFS=$'\t' read -r service container_id extra_field; do
      [[ -n "$service" && -n "$container_id" && -z "$extra_field" ]] || {
        rm -f "$temporary_state_file"
        exit 1
      }
      legacy_service_is_allowed "$service" || {
        rm -f "$temporary_state_file"
        exit 1
      }
      [[ "$container_id" =~ ^[0-9a-f]{12}([0-9a-f]{52})?$ ]] || {
        rm -f "$temporary_state_file"
        exit 1
      }
      actual_id="$(docker inspect --format '{{.Id}}' "$container_id" 2>/dev/null)" || {
        rm -f "$temporary_state_file"
        exit 1
      }
      [[ "$actual_id" == "$container_id"* ]] || {
        rm -f "$temporary_state_file"
        exit 1
      }
      printf '%s\t%s\n' "$service" "$actual_id" >> "$temporary_state_file"
    done < "$LEGACY_STOPPED_STATE_FILE"
    mv "$temporary_state_file" "$LEGACY_STOPPED_STATE_FILE"
  )
}

stop_legacy_application_services() {
  local service container_id full_container_id
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] && return 0
  begin_legacy_stop_state

  for service in api frontend research-worker acquisition-worker scheduler; do
    while IFS= read -r container_id; do
      [[ -n "$container_id" ]] || continue
      full_container_id="$(docker inspect --format '{{.Id}}' "$container_id")" || return 1
      if ! docker stop "$full_container_id" >/dev/null; then
        return 1
      fi
      record_stopped_legacy_container "$service" "$full_container_id"
    done < <(
      docker ps -q \
        --filter "label=com.docker.compose.project=${LEGACY_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}"
    )
  done

  if [[ ! -s "$LEGACY_STOPPED_STATE_FILE" ]]; then
    rm -f "$LEGACY_STOPPED_STATE_FILE"
    rmdir "$LEGACY_STOPPED_STATE_DIR" 2>/dev/null || true
  fi
}

restore_legacy_application_services() {
  local service container_id extra_field actual_id actual_project actual_service running
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] || return 0
  normalize_legacy_stop_state || {
    printf 'one-click runtime: unable to normalize legacy stop state\n' >&2
    return 1
  }

  while IFS=$'\t' read -r service container_id extra_field; do
    [[ -n "$service" && -n "$container_id" && -z "$extra_field" ]] || {
      printf 'one-click runtime: invalid legacy stop state\n' >&2
      return 1
    }
    legacy_service_is_allowed "$service" || {
      printf 'one-click runtime: unexpected legacy service in stop state\n' >&2
      return 1
    }
    actual_id="$(docker inspect --format '{{.Id}}' "$container_id" 2>/dev/null)" || return 1
    actual_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id")" || return 1
    actual_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")" || return 1
    [[ "$actual_id" == "$container_id" && "$actual_project" == "$LEGACY_PROJECT" && "$actual_service" == "$service" ]] || {
      printf 'one-click runtime: legacy stop state identity check failed\n' >&2
      return 1
    }
    running="$(docker inspect --format '{{.State.Running}}' "$container_id")" || return 1
    [[ "$running" == "true" ]] || docker start "$container_id" >/dev/null || return 1
  done < "$LEGACY_STOPPED_STATE_FILE"

  rm -f "$LEGACY_STOPPED_STATE_FILE"
  rmdir "$LEGACY_STOPPED_STATE_DIR" 2>/dev/null || true
}

start_one_click_runtime() {
  require_command docker
  init_runtime_environment
  require_runtime_files
  compose config -q
  compose build
  if ! stop_legacy_application_services; then
    restore_legacy_application_services || die "failed to restore legacy application containers after cutover failed"
    die "failed to stop legacy application containers"
  fi
  if ! compose up -d --no-build --scale acquisition-worker=3; then
    restore_legacy_application_services || die "failed to restore legacy application containers after one-click startup failed"
    die "one-click startup failed; restored legacy application containers"
  fi
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
  restore_legacy_application_services
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
