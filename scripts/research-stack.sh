#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=${COMPOSE_ENV_FILE:-"$ROOT_DIR/.env.compose"}
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.live.yml")
LONG_RUNNING=(postgres keycloak-db keycloak api research-worker acquisition-worker scheduler)

usage() {
  echo "usage: $0 {up|status|logs|restart-api|restart-research-worker|restart-acquisition-worker|restart-scheduler|down [--volumes]}"
}

require_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "missing $ENV_FILE; copy .env.compose.example and configure real providers" >&2
    exit 2
  fi
}

env_value() {
  local key=$1
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

validate_providers() {
  local key value
  for key in LLM_API_KEY GILDATA_TOKEN; do
    value=$(env_value "$key")
    if [[ -z "$value" || "$value" == replace-* ]]; then
      echo "$key must contain a real provider credential" >&2
      exit 2
    fi
  done
}

validate_local_identity() {
  if [[ $(env_value LIVE_KEYCLOAK_USER) != alice \
    || $(env_value LIVE_KEYCLOAK_PASSWORD) != alice-local-only-change-me \
    || $(env_value LIVE_SECOND_KEYCLOAK_USER) != bob \
    || $(env_value LIVE_SECOND_KEYCLOAK_PASSWORD) != bob-local-only-change-me ]]; then
    echo "local acceptance credentials must match the imported local-only realm" >&2
    exit 2
  fi
}

container_state() {
  local service=$1 container_id observed_state
  local container_ids=()
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] && container_ids+=("$container_id")
  done < <("${COMPOSE[@]}" ps -q "$service")
  if (( ${#container_ids[@]} == 0 )); then
    echo missing
    return
  fi
  for container_id in "${container_ids[@]}"; do
    observed_state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
    if [[ "$observed_state" != healthy ]]; then
      echo "$observed_state"
      return
    fi
  done
  echo healthy
}

wait_for_stack() {
  local deadline=$((SECONDS + 300)) service state migrate_id migrate_status migrate_exit
  while (( SECONDS < deadline )); do
    migrate_status=missing
    migrate_exit=1
    migrate_id=$("${COMPOSE[@]}" ps -aq migrate)
    if [[ -n "$migrate_id" ]]; then
      migrate_status=$(docker inspect --format '{{.State.Status}}' "$migrate_id")
      migrate_exit=$(docker inspect --format '{{.State.ExitCode}}' "$migrate_id")
      if [[ "$migrate_status" == exited && "$migrate_exit" != 0 ]]; then
        echo "migration failed with exit code $migrate_exit" >&2
        "${COMPOSE[@]}" logs migrate >&2
        exit 1
      fi
    fi
    local all_healthy=true
    for service in "${LONG_RUNNING[@]}"; do
      state=$(container_state "$service")
      if [[ "$state" != healthy ]]; then
        all_healthy=false
        break
      fi
    done
    if [[ "$all_healthy" == true && "$migrate_status" == exited && "$migrate_exit" == 0 ]]; then
      return
    fi
    sleep 2
  done
  echo "stack did not become healthy within 300 seconds" >&2
  "${COMPOSE[@]}" ps >&2
  exit 1
}

wait_for_service() {
  local service=$1 deadline=$((SECONDS + 180)) state
  while (( SECONDS < deadline )); do
    state=$(container_state "$service")
    if [[ "$state" == healthy ]]; then
      echo "$service: healthy"
      return
    fi
    sleep 2
  done
  echo "$service did not become healthy within 180 seconds" >&2
  "${COMPOSE[@]}" ps "$service" >&2
  "${COMPOSE[@]}" logs --tail 200 "$service" >&2
  exit 1
}

restart_service() {
  local service=$1
  "${COMPOSE[@]}" restart "$service"
  wait_for_service "$service"
}

crash_service() {
  local service=$1
  local container_id
  local container_ids=()
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] && container_ids+=("$container_id")
  done < <("${COMPOSE[@]}" ps --all -q "$service")
  # A stopped scheduler is a valid acceptance setup. In that case there is no
  # process to signal. Start the exact declared containers so a scaled worker
  # service keeps every replica instead of collapsing to Compose's default.
  if (( ${#container_ids[@]} == 0 )); then
    "${COMPOSE[@]}" up -d "$service"
  else
    docker kill --signal SIGKILL "${container_ids[@]}" || true
    docker start "${container_ids[@]}"
  fi
  wait_for_service "$service"
}

stop_service() {
  local service=$1
  "${COMPOSE[@]}" stop "$service"
}

require_env_file
command=${1:-}
case "$command" in
  up)
    validate_providers
    validate_local_identity
    "${COMPOSE[@]}" build migrate
    "${COMPOSE[@]}" up -d
    wait_for_stack
    api_port=$(env_value API_PORT); api_port=${api_port:-8000}
    echo "api: http://localhost:${api_port}/api/v1/health"
    echo "keycloak: http://localhost:8081/realms/fund-engine"
    "${COMPOSE[@]}" ps
    ;;
  status)
    "${COMPOSE[@]}" ps
    ;;
  logs)
    shift
    "${COMPOSE[@]}" logs --tail 200 -f "$@"
    ;;
  restart-api)
    restart_service api
    ;;
  restart-research-worker)
    restart_service research-worker
    ;;
  restart-acquisition-worker)
    restart_service acquisition-worker
    ;;
  restart-scheduler)
    restart_service scheduler
    ;;
  crash-research-worker)
    crash_service research-worker
    ;;
  crash-acquisition-worker)
    crash_service acquisition-worker
    ;;
  crash-scheduler)
    crash_service scheduler
    ;;
  stop-scheduler)
    stop_service scheduler
    ;;
  down)
    if [[ ${2:-} == --volumes ]]; then
      "${COMPOSE[@]}" down --volumes
    elif [[ -n ${2:-} ]]; then
      usage
      exit 2
    else
      "${COMPOSE[@]}" down
    fi
    ;;
  *)
    usage
    exit 2
    ;;
esac
