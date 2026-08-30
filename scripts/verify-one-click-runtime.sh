#!/usr/bin/env bash
# Read-only verifier: it never changes the runtime.
set -euo pipefail

usage() { printf 'usage: %s [--stability-seconds NON_NEGATIVE_CANONICAL_INTEGER]\n' "$0" >&2; }
parse_arguments() {
  STABILITY_SECONDS=0
  case "$#" in
    0) ;;
    2) [[ "$1" == --stability-seconds ]] || { usage; exit 2; }; STABILITY_SECONDS="$2" ;;
    *) usage; exit 2 ;;
  esac
  [[ "$STABILITY_SECONDS" =~ ^(0|[1-9][0-9]*)$ ]] && { [[ ${#STABILITY_SECONDS} -lt 10 ]] || { [[ ${#STABILITY_SECONDS} -eq 10 ]] && [[ "$STABILITY_SECONDS" < 2147483648 ]]; }; } || { usage; exit 2; }
}
parse_arguments "$@"

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly STABILITY_HELPER="$REPO_ROOT/scripts/one_click_stability.py"
readonly LEGACY_PROJECT="fund-engine-event"
readonly LEGACY_DATABASE_SERVICE="postgres"
readonly LEGACY_DATABASE_CONTAINER="fund-engine-event-postgres-1"
readonly API_URL="${ONE_CLICK_API_URL:-http://127.0.0.1:${ONE_CLICK_API_PORT:-8000}}" FRONTEND_URL="${ONE_CLICK_FRONTEND_URL:-http://127.0.0.1:${ONE_CLICK_FRONTEND_PORT:-8080}}"
TMPDIR_EXACT='' SETUP_COMPLETE=false BASELINE_SNAPSHOT='' BASELINE_LEGACY_ID='' ACQUISITION_REPLICAS='' CONNECTION_CAP=''

die() { printf 'one-click runtime verification: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }
require_file() { [[ -f "$1" ]] || die "missing required file: $1"; }
compose() { docker compose -f "$COMPOSE_FILE" --env-file "$BASE_ENV_FILE" --env-file "$RUNTIME_ENV_FILE" "$@"; }

# Process values precede runtime values, base values, and defaults.  This mirrors
# Compose and the operator; values are intentionally never printed.
optional_environment_value() {
  local key="$1" default_value="$2" file matches value
  if [[ -n "${!key+x}" ]]; then printf '%s' "${!key}"; return; fi
  for file in "$RUNTIME_ENV_FILE" "$BASE_ENV_FILE"; do
    matches="$(grep -E "^${key}=" "$file" || true)"
    if [[ -n "$matches" ]]; then
      [[ "$(printf '%s\n' "$matches" | wc -l | tr -d ' ')" == 1 ]] || die "missing or duplicate ${key} in environment"
      value="${matches#*=}"; [[ -n "$value" ]] || die "empty ${key} in environment"; printf '%s' "$value"; return
    fi
  done
  printf '%s' "$default_value"
}
runtime_environment_value() { local value; value="$(optional_environment_value "$1" '')"; [[ -n "$value" ]] || die "missing $1 in runtime environment"; printf '%s' "$value"; }
bounded_integer_value() {
  local key="$1" default_value="$2" minimum="$3" maximum="$4" value
  value="$(optional_environment_value "$key" "$default_value")"
  [[ "$value" =~ ^(0|[1-9][0-9]*)$ ]] || die "${key} must be an integer from ${minimum} through ${maximum}"
  if (( ${#value} > ${#maximum} )) || { (( ${#value} == ${#maximum} )) && [[ "$value" > "$maximum" ]]; }; then die "${key} must be an integer from ${minimum} through ${maximum}"; fi
  (( 10#$value >= minimum && 10#$value <= maximum )) || die "${key} must be an integer from ${minimum} through ${maximum}"; printf '%s' "$value"
}
at_most() {
  local actual="$1" maximum="$2"
  [[ "$actual" =~ ^(0|[1-9][0-9]*)$ ]] || die 'database connection count is invalid'
  if (( ${#actual} > ${#maximum} )) || { (( ${#actual} == ${#maximum} )) && [[ "$actual" > "$maximum" ]]; }; then die 'database connection count exceeds configured cap'; fi
}
require_revision() { [[ "$1" == "$2" ]] || die "expected Alembic revision $2, got ${1:-none}"; }
valid_private_directory() {
  local relative_path
  [[ -n "$TMPDIR_EXACT" && "$TMPDIR_EXACT" == "$REPO_ROOT/"* && -d "$TMPDIR_EXACT" ]] || return 1
  relative_path="${TMPDIR_EXACT#"$REPO_ROOT/"}"
  [[ "$relative_path" == .verify-one-click-runtime.* && "$relative_path" != */* && "$relative_path" != . && "$relative_path" != .. ]]
}

diagnostics() {
  local id ids=()
  printf 'one-click runtime verification diagnostics (read-only):\n' >&2
  compose ps --all >&2 || true
  while IFS= read -r id; do [[ -n "$id" ]] && ids[${#ids[@]}]="$id"; done < <(compose ps --all --quiet 2>/dev/null || true)
  for id in "${ids[@]}"; do
    docker inspect --format 'Name={{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} OOM={{.State.OOMKilled}} restarts={{.RestartCount}}' "$id" >&2 || true
    docker logs --tail 40 "$id" >&2 || true
  done
  [[ ${#ids[@]} -eq 0 ]] || docker stats --no-stream "${ids[@]}" >&2 || true
}
cleanup() {
  local status="$?" cleanup_failed=false
  trap - EXIT
  if [[ -n "$TMPDIR_EXACT" ]] && valid_private_directory; then
    rm -rf -- "$TMPDIR_EXACT" || cleanup_failed=true
  fi
  [[ "$status" -eq 0 || "$SETUP_COMPLETE" != true ]] || diagnostics
  if [[ "$status" -ne 0 ]]; then exit "$status"; fi
  if [[ "$cleanup_failed" == true ]]; then
    printf 'one-click runtime verification: private verification directory cleanup failed\n' >&2
    exit 1
  fi
  exit 0
}

capture_snapshot() {
  local destination="$1" id ids=() ids_output
  ids_output="$(compose ps --all --quiet)" || die 'unable to list one-click containers'
  while IFS= read -r id; do [[ -n "$id" ]] && ids[${#ids[@]}]="$id"; done <<< "$ids_output"
  [[ ${#ids[@]} -gt 0 ]] || die 'one-click compose project has no containers'
  if ! docker inspect "${ids[@]}" | python3 "$STABILITY_HELPER" snapshot --expect postgres=1 --expect api=1 --expect research-worker=1 --expect "acquisition-worker=${ACQUISITION_REPLICAS}" --expect company-research-worker=1 --expect frontend=1 > "$destination"; then die 'container snapshot is invalid'; fi
}
http_checks() {
  local bearer_token="$1" product_objects
  curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "$API_URL/health" >/dev/null || die 'API health endpoint check failed'
  curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "$FRONTEND_URL/health" >/dev/null || die 'frontend health endpoint check failed'
  curl --fail --silent --show-error --connect-timeout 2 --max-time 5 "$FRONTEND_URL/research" | grep -q '投资研究' || die 'frontend research route check failed'
  product_objects="$(printf 'Authorization: Bearer %s\n' "$bearer_token" | curl --fail --silent --show-error --connect-timeout 2 --max-time 5 --header @- "$API_URL/api/underwriting/v1/product/objects?query=CATL")" || die 'product foundation API check failed'
  printf '%s' "$product_objects" | python3 -c 'import json,sys; value=json.load(sys.stdin); items=value.get("items") if isinstance(value,dict) else None; keys={item.get("external_key") for item in items if isinstance(item,dict)} if isinstance(items,list) else set(); required={"CN:300750:COMPANY","SZSE:300750"}; required.issubset(keys) or (_ for _ in ()).throw(SystemExit("CATL object foundation is incomplete"))'
}
legacy_checks() {
  local legacy_container legacy_name legacy_project legacy_service legacy_running legacy_revision
  legacy_container="$(docker inspect --format '{{.Id}}' "$LEGACY_DATABASE_CONTAINER" 2>/dev/null)" || die "legacy postgres container is unavailable: $LEGACY_DATABASE_CONTAINER"
  [[ "$legacy_container" =~ ^[0-9a-f]{64}$ ]] || die 'legacy database container identity is malformed'
  legacy_name="$(docker inspect --format '{{.Name}}' "$legacy_container")"; legacy_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$legacy_container")"; legacy_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$legacy_container")"; legacy_running="$(docker inspect --format '{{.State.Running}}' "$legacy_container")"
  [[ "$legacy_name" == "/$LEGACY_DATABASE_CONTAINER" && "$legacy_project" == "$LEGACY_PROJECT" && "$legacy_service" == "$LEGACY_DATABASE_SERVICE" && "$legacy_running" == true ]] || die 'legacy database container identity does not match'
  [[ -n "$BASELINE_LEGACY_ID" ]] || BASELINE_LEGACY_ID="$legacy_container"; [[ "$legacy_container" == "$BASELINE_LEGACY_ID" ]] || die 'legacy database container identity changed'
  legacy_revision="$(docker exec "$legacy_container" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"')"; require_revision "$legacy_revision" 0062
}
poll_runtime() {
  local database_user="$1" database_name="$2" bearer_token="$3" snapshot connection_count new_revision
  snapshot="$TMPDIR_EXACT/current.json"; capture_snapshot "$snapshot"
  if [[ -z "$BASELINE_SNAPSHOT" ]]; then BASELINE_SNAPSHOT="$TMPDIR_EXACT/baseline.json"; cp "$snapshot" "$BASELINE_SNAPSHOT"; elif ! python3 "$STABILITY_HELPER" compare "$BASELINE_SNAPSHOT" "$snapshot"; then die 'container stability comparison failed'; fi
  http_checks "$bearer_token"
  connection_count="$(compose exec -T postgres psql -U "$database_user" -d "$database_name" -Atc 'SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();')"; at_most "$connection_count" "$CONNECTION_CAP"
  new_revision="$(compose exec -T postgres psql -U "$database_user" -d "$database_name" -Atc 'SELECT version_num FROM alembic_version;')"; require_revision "$new_revision" 0070
  legacy_checks
}
main() {
  local database_user database_name bearer_token pool_size max_overflow remaining sleep_seconds
  require_command docker; require_command curl; require_command python3; require_file "$STABILITY_HELPER"; require_file "$COMPOSE_FILE"; require_file "$BASE_ENV_FILE"; require_file "$RUNTIME_ENV_FILE"
  umask 077; TMPDIR_EXACT="$(mktemp -d "$REPO_ROOT/.verify-one-click-runtime.XXXXXX")" || die 'unable to create private verification directory'
  valid_private_directory || die 'private verification directory validation failed'
  trap cleanup EXIT; SETUP_COMPLETE=true
  compose config -q
  ACQUISITION_REPLICAS="$(bounded_integer_value ONE_CLICK_ACQUISITION_REPLICAS 1 1 4)"; pool_size="$(bounded_integer_value DATABASE_POOL_SIZE 2 1 10)"; max_overflow="$(bounded_integer_value DATABASE_MAX_OVERFLOW 2 0 10)"
  CONNECTION_CAP="$(python3 "$STABILITY_HELPER" connection-cap --replicas "$ACQUISITION_REPLICAS" --pool-size "$pool_size" --max-overflow "$max_overflow")" || die 'unable to derive database connection cap'; [[ "$CONNECTION_CAP" =~ ^[1-9][0-9]*$ ]] || die 'derived database connection cap is invalid'
  database_user="$(runtime_environment_value ONE_CLICK_POSTGRES_USER)"; database_name="$(runtime_environment_value ONE_CLICK_POSTGRES_DB)"; bearer_token="$(runtime_environment_value RESEARCH_BEARER_TOKEN)"
  poll_runtime "$database_user" "$database_name" "$bearer_token"
  remaining="$STABILITY_SECONDS"; while (( remaining > 0 )); do sleep_seconds=5; (( remaining < sleep_seconds )) && sleep_seconds="$remaining"; sleep "$sleep_seconds" || die 'stability sleep failed'; poll_runtime "$database_user" "$database_name" "$bearer_token"; remaining=$((remaining - sleep_seconds)); done
  printf 'One-click investment-research runtime is healthy; isolated database is at 0070 and legacy database remains at 0062.\n'
}
main
