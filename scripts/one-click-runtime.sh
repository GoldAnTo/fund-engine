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

runtime_env_value() {
  local key="$1"
  local matches value
  matches="$(grep -E "^${key}=" "$RUNTIME_ENV_FILE" || true)"
  [[ -n "$matches" && "$(printf '%s\n' "$matches" | wc -l | tr -d ' ')" == "1" ]] \
    || die "missing or duplicate ${key} in runtime environment"
  value="${matches#*=}"
  [[ -n "$value" ]] || die "empty ${key} in runtime environment"
  printf '%s' "$value"
}

files_volume_name() {
  local value
  if ! value="$(compose config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
name = config.get("volumes", {}).get("fund-engine-one-click-files", {}).get("name")
if not isinstance(name, str) or not name:
    raise SystemExit("rendered Compose config has no research files volume name")
print(name, end="")
')"; then
    die "unable to resolve one-click files volume from rendered Compose config"
  fi
  [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] \
    || die "invalid one-click files volume name"
  printf '%s' "$value"
}

gildata_token_is_configured() {
  local value
  if ! value="$(compose config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
value = config.get("services", {}).get("acquisition-worker", {}).get("environment", {}).get("GILDATA_TOKEN", "")
if value is None:
    value = ""
if not isinstance(value, str):
    raise SystemExit("rendered Compose GILDATA_TOKEN is not text")
print(value, end="")
')"; then
    die "unable to resolve GILDATA_TOKEN from rendered Compose config"
  fi
  [[ -n "$value" ]]
}

validate_specific_path() {
  local path="$1"
  [[ "$path" = /* ]] || die "path must be a specific absolute directory"
  case "$path" in
    /|/tmp|/private/tmp|/var|/private/var|/Users|/Volumes|/opt|/usr|/etc)
      die "path must be a specific absolute directory"
      ;;
  esac
}

validate_new_backup_path() {
  local path="$1"
  local parent canonical_parent canonical_path
  validate_specific_path "$path"
  [[ ! -e "$path" && ! -L "$path" ]] \
    || die "backup directory must not already exist"
  parent="${path%/*}"
  [[ -d "$parent" && ! -L "$parent" ]] \
    || die "backup parent must be an existing non-symlink directory"
  canonical_parent="$(cd -P "$parent" && pwd)"
  canonical_path="${canonical_parent}/${path##*/}"
  [[ "$canonical_path" == "$path" ]] \
    || die "backup path must not escape through a symlink"
}

validate_existing_backup_path() {
  local path="$1"
  local canonical_path
  validate_specific_path "$path"
  [[ -d "$path" && ! -L "$path" ]] || die "invalid backup directory"
  canonical_path="$(cd -P "$path" && pwd)"
  [[ "$canonical_path" == "$path" ]] \
    || die "backup path must not escape through a symlink"
}

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$BASE_ENV_FILE" --env-file "$RUNTIME_ENV_FILE" "$@"
}

upgrade_legacy_runtime_defaults() {
  local temporary_file
  grep -Fxq 'ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata' "$RUNTIME_ENV_FILE" \
    || return 0
  require_command docker
  require_command python3
  [[ -f "$COMPOSE_FILE" && -f "$BASE_ENV_FILE" ]] \
    || die "cannot inspect rendered Compose environment for legacy runtime upgrade"
  gildata_token_is_configured && return 0
  temporary_file="$(mktemp "${RUNTIME_ENV_FILE}.XXXXXX")"
  (
    umask 077
    trap 'rm -f -- "$temporary_file"' EXIT
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" == 'ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata' ]]; then
        printf '%s\n' 'ACQUISITION_ENABLED_ADAPTERS=sse,szse'
      else
        printf '%s\n' "$line"
      fi
    done < "$RUNTIME_ENV_FILE" > "$temporary_file"
    chmod 600 "$temporary_file"
    mv -- "$temporary_file" "$RUNTIME_ENV_FILE"
    trap - EXIT
  )
  printf 'Updated previous optional Gildata adapter default.\n'
}

init_runtime_environment() {
  require_command openssl

  if [[ -e "$RUNTIME_ENV_FILE" ]]; then
    upgrade_legacy_runtime_defaults
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
      'ACQUISITION_ENABLED_ADAPTERS=sse,szse' \
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

validate_research_archive() {
  local archive="$1"
  tar -tzf "$archive" >/dev/null || die "research file archive is unreadable"
  python3 - "$archive" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
with tarfile.open(archive, mode="r:gz") as bundle:
    for member in bundle.getmembers():
        normalized = pathlib.PurePosixPath(member.name)
        if (
            normalized.is_absolute()
            or not member.name
            or ".." in normalized.parts
            or member.issym()
            or member.islnk()
            or not (member.isdir() or member.isfile())
        ):
            raise SystemExit(f"unsafe research archive member: {member.name!r}")
        if any(part.startswith(".env") for part in normalized.parts):
            raise SystemExit(f"secret research archive member: {member.name!r}")
PY
}

require_application_services_stopped() {
  local action="$1"
  local all_running_services running_services
  if ! all_running_services="$(compose ps --status running --services)"; then
    die "unable to inspect one-click services before ${action}"
  fi
  running_services="$(printf '%s\n' "$all_running_services" | grep -Ev '^postgres$' || true)"
  [[ -z "$running_services" ]] || die "stop application services before ${action}"
  printf '%s\n' "$all_running_services" | grep -Fxq postgres \
    || die "one-click postgres must be running for ${action}"
}

backup_runtime() (
  set -euo pipefail
  local output_dir="$1"
  local parent temporary_dir volume_name
  validate_new_backup_path "$output_dir"
  require_command docker
  require_command python3
  require_command shasum
  require_runtime_files
  require_application_services_stopped backup
  parent="${output_dir%/*}"
  temporary_dir="$(mktemp -d "$parent/.one-click-backup.XXXXXX")"
  trap 'rm -rf -- "$temporary_dir"' EXIT
  volume_name="$(files_volume_name)"

  compose exec -T postgres pg_dump -Fc \
    -U "$(runtime_env_value ONE_CLICK_POSTGRES_USER)" \
    "$(runtime_env_value ONE_CLICK_POSTGRES_DB)" > "$temporary_dir/postgres.dump"
  [[ -s "$temporary_dir/postgres.dump" ]] || die "PostgreSQL backup is empty"
  docker run --rm \
    -v "$volume_name:/source:ro" \
    -v "$temporary_dir:/backup" \
    alpine sh -eu -c \
    'tar --exclude=.env --exclude=.env* -C /source -czf /backup/research-files.tar.gz .'
  validate_research_archive "$temporary_dir/research-files.tar.gz"
  (
    cd "$temporary_dir"
    shasum -a 256 postgres.dump research-files.tar.gz > manifest.sha256
  )
  mv -- "$temporary_dir" "$output_dir"
  trap - EXIT
  printf 'Created one-click backup at %s.\n' "$output_dir"
)

validate_backup_bundle() {
  local backup_dir="$1"
  local actual_files manifest_files
  validate_existing_backup_path "$backup_dir"
  for artifact in manifest.sha256 postgres.dump research-files.tar.gz; do
    [[ -f "$backup_dir/$artifact" && ! -L "$backup_dir/$artifact" ]] \
      || die "backup artifact is missing or unsafe: $artifact"
  done
  actual_files="$(find "$backup_dir" -mindepth 1 -maxdepth 1 -exec basename {} \; | LC_ALL=C sort)"
  [[ "$actual_files" == $'manifest.sha256\npostgres.dump\nresearch-files.tar.gz' ]] \
    || die "backup directory contains unexpected artifacts"
  manifest_files="$(awk 'NF == 2 && $1 ~ /^[0-9a-f]{64}$/ {sub(/^\*/, "", $2); print $2}' "$backup_dir/manifest.sha256" | LC_ALL=C sort)"
  [[ "$manifest_files" == $'postgres.dump\nresearch-files.tar.gz' ]] \
    || die "backup checksum manifest has an invalid artifact allowlist"
  [[ "$(wc -l < "$backup_dir/manifest.sha256" | tr -d ' ')" == "2" ]] \
    || die "backup checksum manifest has an invalid artifact count"
  (cd "$backup_dir" && shasum -a 256 -c manifest.sha256) \
    || die "backup checksum validation failed"
  validate_research_archive "$backup_dir/research-files.tar.gz"
}

copy_volume_contents() {
  local source_volume="$1"
  local target_volume="$2"
  docker run --rm \
    -v "$source_volume:/source:ro" \
    -v "$target_volume:/target" \
    alpine sh -eu -c \
    'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; cp -a /source/. /target/'
}

print_restore_recovery_instructions() {
  local database_user="$1"
  local database_name="$2"
  local staging_database="$3"
  local old_database="$4"
  local database_state="$5"
  local files_volume="$6"
  local staging_volume="$7"
  local rollback_volume="$8"
  local sql
  printf 'one-click runtime: manual recovery required; keep application services stopped and do not rerun restore.\n' >&2
  printf 'one-click runtime: inspect databases with: docker compose -f %q --env-file %q --env-file %q exec -T postgres psql -U %q -d postgres -lqt\n' \
    "$COMPOSE_FILE" "$BASE_ENV_FILE" "$RUNTIME_ENV_FILE" "$database_user" >&2
  case "$database_state" in
    original_renamed)
      printf 'one-click runtime: preserved database names: %q %q\n' \
        "$staging_database" "$old_database" >&2
      sql="ALTER DATABASE \"$old_database\" RENAME TO \"$database_name\";"
      printf 'one-click runtime: database recovery command: docker compose -f %q --env-file %q --env-file %q exec -T postgres psql -v ON_ERROR_STOP=1 -U %q -d postgres -c %q\n' \
        "$COMPOSE_FILE" "$BASE_ENV_FILE" "$RUNTIME_ENV_FILE" "$database_user" "$sql" >&2
      ;;
    restored_active)
      printf 'one-click runtime: preserved database names: %q %q\n' \
        "$database_name" "$old_database" >&2
      sql="ALTER DATABASE \"$database_name\" RENAME TO \"$staging_database\";"
      printf 'one-click runtime: database rollback step 1: docker compose -f %q --env-file %q --env-file %q exec -T postgres psql -v ON_ERROR_STOP=1 -U %q -d postgres -c %q\n' \
        "$COMPOSE_FILE" "$BASE_ENV_FILE" "$RUNTIME_ENV_FILE" "$database_user" "$sql" >&2
      sql="ALTER DATABASE \"$old_database\" RENAME TO \"$database_name\";"
      printf 'one-click runtime: database rollback step 2: docker compose -f %q --env-file %q --env-file %q exec -T postgres psql -v ON_ERROR_STOP=1 -U %q -d postgres -c %q\n' \
        "$COMPOSE_FILE" "$BASE_ENV_FILE" "$RUNTIME_ENV_FILE" "$database_user" "$sql" >&2
      ;;
    rolled_back)
      printf 'one-click runtime: preserved database names: %q %q\n' \
        "$database_name" "$staging_database" >&2
      ;;
    finalizing_uncertain)
      printf 'one-click runtime: final database cleanup may have completed; inspect possible names before taking action: %q %q\n' \
        "$database_name" "$old_database" >&2
      ;;
    *)
      printf 'one-click runtime: database cutover state is uncertain; inspect possible names: %q %q %q\n' \
        "$database_name" "$staging_database" "$old_database" >&2
      ;;
  esac
  printf 'one-click runtime: inspect volumes with: docker volume inspect %q %q %q\n' \
    "$files_volume" "$staging_volume" "$rollback_volume" >&2
  printf 'one-click runtime: file recovery command: docker run --rm -v %q:/source:ro -v %q:/target alpine sh -eu -c %q\n' \
    "$rollback_volume" "$files_volume" \
    'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; cp -a /source/. /target/' >&2
}

restore_runtime() (
  set -euo pipefail
  local backup_dir="$1"
  local database_user database_name suffix staging_database old_database
  local files_volume staging_volume rollback_volume
  local database_swapped="false"
  local preserve_recovery_state="false"
  local database_state="pre_cutover"

  validate_existing_backup_path "$backup_dir"
  require_command docker
  require_command openssl
  require_command python3
  require_command shasum
  require_runtime_files
  validate_backup_bundle "$backup_dir"
  require_application_services_stopped restore

  database_user="$(runtime_env_value ONE_CLICK_POSTGRES_USER)"
  database_name="$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  [[ "$database_name" =~ ^[A-Za-z_][A-Za-z0-9_]{0,39}$ ]] \
    || die "one-click database name is unsafe for restore"
  suffix="$(openssl rand -hex 4)"
  staging_database="${database_name}_restore_${suffix}"
  old_database="${database_name}_before_${suffix}"
  files_volume="$(files_volume_name)"
  staging_volume="${files_volume}-restore-${suffix}"
  rollback_volume="${files_volume}-before-${suffix}"

  cleanup_restore() {
    if [[ "$preserve_recovery_state" == "true" ]]; then
      print_restore_recovery_instructions \
        "$database_user" "$database_name" "$staging_database" "$old_database" \
        "$database_state" \
        "$files_volume" "$staging_volume" "$rollback_volume"
      return 0
    fi
    docker volume rm "$staging_volume" "$rollback_volume" >/dev/null 2>&1 || true
    if [[ "$database_swapped" != "true" ]]; then
      compose exec -T postgres dropdb --if-exists -U "$database_user" "$staging_database" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup_restore EXIT

  compose exec -T postgres createdb -U "$database_user" "$staging_database"
  compose exec -T postgres pg_restore --exit-on-error --no-owner --no-privileges \
    -U "$database_user" -d "$staging_database" < "$backup_dir/postgres.dump"
  docker volume create "$staging_volume" >/dev/null
  docker run --rm \
    -v "$staging_volume:/target" \
    -v "$backup_dir:/backup:ro" \
    alpine tar -C /target -xzf /backup/research-files.tar.gz

  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps migrate
  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps api python -m app.scripts.load_product_foundation_fixture
  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps api python -m app.scripts.verify_underwriting_revision_manifests

  docker volume create "$rollback_volume" >/dev/null
  copy_volume_contents "$files_volume" "$rollback_volume"
  preserve_recovery_state="true"
  database_state="cutover_uncertain"
  compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
    -c "ALTER DATABASE \"$database_name\" RENAME TO \"$old_database\";"
  database_state="original_renamed"
  # Set uncertainty before every destructive rename so an asynchronous EXIT
  # never reports a stale database topology.
  database_state="cutover_uncertain"
  if ! compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
    -c "ALTER DATABASE \"$staging_database\" RENAME TO \"$database_name\";"; then
    if ! compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
      -c "ALTER DATABASE \"$old_database\" RENAME TO \"$database_name\";"; then
      die "automatic restore rollback failed; preserved recovery artifacts and kept application services stopped"
    fi
    database_state="rolled_back"
    preserve_recovery_state="false"
    die "failed to activate restored database"
  fi
  database_state="restored_active"
  database_swapped="true"
  if ! copy_volume_contents "$staging_volume" "$files_volume"; then
    local files_rollback_succeeded="true"
    local database_rollback_succeeded="true"
    copy_volume_contents "$rollback_volume" "$files_volume" \
      || files_rollback_succeeded="false"
    database_state="cutover_uncertain"
    if ! compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
      -c "ALTER DATABASE \"$database_name\" RENAME TO \"$staging_database\";"; then
      database_rollback_succeeded="false"
    else
      database_state="original_renamed"
      # The next rename can be interrupted after PostgreSQL commits it but
      # before this shell observes success.
      database_state="cutover_uncertain"
      if ! compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
        -c "ALTER DATABASE \"$old_database\" RENAME TO \"$database_name\";"; then
        database_rollback_succeeded="false"
        if compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$database_user" -d postgres \
          -c "ALTER DATABASE \"$staging_database\" RENAME TO \"$database_name\";"; then
          database_state="restored_active"
        fi
      else
        database_state="rolled_back"
      fi
    fi
    if [[ "$files_rollback_succeeded" != "true" || "$database_rollback_succeeded" != "true" ]]; then
      die "automatic restore rollback failed; preserved recovery artifacts and kept application services stopped"
    fi
    database_swapped="false"
    preserve_recovery_state="false"
    die "failed to activate restored research files"
  fi
  if ! compose run --rm --no-deps api python -m app.scripts.verify_underwriting_revision_manifests; then
    die "restored runtime failed final verification; preserved recovery artifacts and kept application services stopped"
  fi
  database_state="finalizing_uncertain"
  compose exec -T postgres dropdb -U "$database_user" "$old_database"
  preserve_recovery_state="false"
  printf 'Restored and verified one-click backup from %s.\n' "$backup_dir"
)

usage() {
  printf 'Usage: %s {init|up|down|status|rollback|backup <absolute-output-dir>|restore <absolute-backup-dir>}\n' "$0" >&2
}

case "${1:-}" in
  init) init_runtime_environment ;;
  up) start_one_click_runtime ;;
  down) stop_one_click_runtime ;;
  status) show_runtime_status ;;
  rollback) rollback_runtime ;;
  backup) [[ "$#" -eq 2 ]] || { usage; exit 2; }; backup_runtime "$2" ;;
  restore) [[ "$#" -eq 2 ]] || { usage; exit 2; }; restore_runtime "$2" ;;
  *) usage; exit 2 ;;
esac
