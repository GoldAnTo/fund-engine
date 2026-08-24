#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly COMPOSE_FILE="$REPO_ROOT/docker-compose.one-click.yml"
readonly BASE_ENV_FILE="$REPO_ROOT/.env"
readonly RUNTIME_ENV_FILE="$REPO_ROOT/.env.one-click.local"
readonly LEGACY_PROJECT="fund-engine-event"
readonly LEGACY_STOPPED_STATE_DIR="$REPO_ROOT/.one-click-runtime"
readonly LEGACY_STOPPED_STATE_FILE="$LEGACY_STOPPED_STATE_DIR/legacy-stopped-containers"
readonly FILES_VOLUME_LOGICAL_NAME="fund-engine-one-click-files"
readonly OPERATION_PROJECT_LABEL="com.fund-engine.one-click.project"
readonly OPERATION_ID_LABEL="com.fund-engine.one-click.operation"
readonly OPERATION_PURPOSE_LABEL="com.fund-engine.one-click.purpose"

die() {
  printf 'one-click runtime: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

validate_runtime_env_file() {
  require_command python3
  if ! python3 -c '
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError as exc:
    raise SystemExit(f"unable to open runtime environment safely: {exc}") from exc
try:
    descriptor_stat = os.fstat(descriptor)
    if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_uid != os.geteuid():
        raise SystemExit("runtime environment is not a regular owner-controlled file")
    os.fchmod(descriptor, 0o600)
    path_stat = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
    ):
        raise SystemExit("runtime environment identity changed during validation")
finally:
    os.close(descriptor)
' "$RUNTIME_ENV_FILE"; then
    die "runtime environment must be a regular, owner-controlled file: $RUNTIME_ENV_FILE"
  fi
}

create_runtime_env_file() {
  local postgres_password="$1"
  local bearer_token="$2"
  if ! printf '%s\n%s\n' "$postgres_password" "$bearer_token" | \
    python3 -c '
import json
import os
import stat
import sys

path = sys.argv[1]
secrets = sys.stdin.read().splitlines()
if len(secrets) != 2 or not all(secrets):
    raise SystemExit("generated runtime credentials are invalid")
postgres_password, bearer_token = secrets
payload = "\n".join(
    (
        "ONE_CLICK_POSTGRES_DB=fund_engine_one_click",
        "ONE_CLICK_POSTGRES_USER=one_click",
        f"ONE_CLICK_POSTGRES_PASSWORD={postgres_password}",
        f"RESEARCH_BEARER_TOKEN={bearer_token}",
        "RESEARCH_TENANT_TOKENS=" + json.dumps({bearer_token: "local-one-click"}, separators=(",", ":")),
        "ACQUISITION_ENABLED_ADAPTERS=sse,szse",
        "",
    )
)
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags, 0o600)
except FileExistsError as exc:
    raise SystemExit("runtime environment appeared during atomic creation; refusing to overwrite") from exc
with os.fdopen(descriptor, "w", encoding="utf-8") as runtime_file:
    runtime_file.write(payload)
    runtime_file.flush()
    os.fsync(runtime_file.fileno())
    descriptor_stat = os.fstat(runtime_file.fileno())
    path_stat = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
        or path_stat.st_uid != os.geteuid()
    ):
        raise SystemExit("runtime environment identity changed during atomic creation")
' "$RUNTIME_ENV_FILE"; then
    die "unable to atomically create runtime environment"
  fi
}

require_runtime_files() {
  [[ -f "$COMPOSE_FILE" ]] || die "missing compose file: $COMPOSE_FILE"
  [[ -f "$BASE_ENV_FILE" ]] || die "missing base environment file: $BASE_ENV_FILE"
  [[ -e "$RUNTIME_ENV_FILE" || -L "$RUNTIME_ENV_FILE" ]] || die "run '$0 init' first"
  validate_runtime_env_file
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

compose_project_name() {
  local value
  if ! value="$(compose config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
name = config.get("name")
if not isinstance(name, str) or not name:
    raise SystemExit("rendered Compose config has no project name")
print(name, end="")
')"; then
    die "unable to resolve one-click project from rendered Compose config"
  fi
  [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] \
    || die "invalid one-click Compose project name"
  printf '%s' "$value"
}

volume_identity() {
  local volume_name="$1"
  docker volume inspect --format \
    '{{.Name}}|{{with index .Labels "com.docker.compose.project"}}{{.}}{{end}}|{{with index .Labels "com.docker.compose.volume"}}{{.}}{{end}}|{{with index .Labels "com.fund-engine.one-click.project"}}{{.}}{{end}}|{{with index .Labels "com.fund-engine.one-click.operation"}}{{.}}{{end}}|{{with index .Labels "com.fund-engine.one-click.purpose"}}{{.}}{{end}}' \
    "$volume_name"
}

validate_live_files_volume() {
  local volume_name="$1"
  local project_name="$2"
  local actual expected
  expected="${volume_name}|${project_name}|${FILES_VOLUME_LOGICAL_NAME}|||"
  actual="$(volume_identity "$volume_name")" \
    || die "unable to inspect one-click files volume identity"
  [[ "$actual" == "$expected" ]] \
    || die "one-click files volume identity does not match rendered Compose ownership"
}

validate_operation_volume() {
  local volume_name="$1"
  local project_name="$2"
  local operation="$3"
  local purpose="$4"
  local actual expected
  expected="${volume_name}|||${project_name}|${operation}|${purpose}"
  actual="$(volume_identity "$volume_name")" || return 1
  [[ "$actual" == "$expected" ]]
}

create_operation_volume() {
  local volume_name="$1"
  local project_name="$2"
  local operation="$3"
  local purpose="$4"
  if docker volume inspect "$volume_name" >/dev/null 2>&1; then
    die "refusing to reuse existing restore operation volume: $volume_name"
  fi
  docker volume create \
    --label "${OPERATION_PROJECT_LABEL}=${project_name}" \
    --label "${OPERATION_ID_LABEL}=${operation}" \
    --label "${OPERATION_PURPOSE_LABEL}=${purpose}" \
    "$volume_name" >/dev/null
  validate_operation_volume "$volume_name" "$project_name" "$operation" "$purpose" \
    || die "created restore operation volume has unexpected identity"
}

remove_operation_volume() {
  local volume_name="$1"
  local project_name="$2"
  local operation="$3"
  local purpose="$4"
  local actual expected
  expected="${volume_name}|||${project_name}|${operation}|${purpose}"
  actual="$(volume_identity "$volume_name" 2>/dev/null)" || return 0
  if [[ "$actual" != "$expected" ]]; then
    printf 'one-click runtime: refusing to remove operation volume with unexpected identity: %s\n' \
      "$volume_name" >&2
    return 1
  fi
  docker volume rm "$volume_name" >/dev/null
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
  require_command python3

  if [[ -e "$RUNTIME_ENV_FILE" || -L "$RUNTIME_ENV_FILE" ]]; then
    validate_runtime_env_file
    upgrade_legacy_runtime_defaults
    printf 'One-click runtime environment already exists.\n'
    return 0
  fi

  local postgres_password bearer_token
  postgres_password="$(openssl rand -hex 32)"
  bearer_token="$(openssl rand -hex 32)"

  create_runtime_env_file "$postgres_password" "$bearer_token"
  validate_runtime_env_file

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

prepare_legacy_stop_state() {
  local service container_id full_container_id actual_project actual_service
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] && return 0
  begin_legacy_stop_state

  for service in api frontend research-worker acquisition-worker scheduler; do
    while IFS= read -r container_id; do
      [[ -n "$container_id" ]] || continue
      full_container_id="$(docker inspect --format '{{.Id}}' "$container_id")" || return 1
      actual_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$full_container_id")" || return 1
      actual_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$full_container_id")" || return 1
      [[ "$full_container_id" =~ ^[0-9a-f]{64}$ \
        && "$actual_project" == "$LEGACY_PROJECT" \
        && "$actual_service" == "$service" ]] || return 1
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

validate_prepared_legacy_stop_state() {
  local service container_id extra_field actual_id actual_project actual_service
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] || return 0
  normalize_legacy_stop_state || return 1
  while IFS=$'\t' read -r service container_id extra_field; do
    [[ -n "$service" && -n "$container_id" && -z "$extra_field" ]] || return 1
    legacy_service_is_allowed "$service" || return 1
    actual_id="$(docker inspect --format '{{.Id}}' "$container_id")" || return 1
    actual_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id")" || return 1
    actual_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")" || return 1
    [[ "$actual_id" == "$container_id" \
      && "$actual_project" == "$LEGACY_PROJECT" \
      && "$actual_service" == "$service" ]] || return 1
  done < "$LEGACY_STOPPED_STATE_FILE"
}

stop_legacy_application_services() {
  local service container_id extra_field failed="false"
  prepare_legacy_stop_state || return 1
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] || return 0
  validate_prepared_legacy_stop_state || return 1
  while IFS=$'\t' read -r service container_id extra_field; do
    [[ -n "$service" && -n "$container_id" && -z "$extra_field" ]] || return 1
    if ! docker stop "$container_id" >/dev/null; then
      failed="true"
      break
    fi
  done < "$LEGACY_STOPPED_STATE_FILE"
  [[ "$failed" != "true" ]]
}

restore_legacy_application_services() {
  local service container_id extra_field actual_id actual_project actual_service running
  local failed="false"
  [[ -e "$LEGACY_STOPPED_STATE_FILE" ]] || return 0
  normalize_legacy_stop_state || {
    printf 'one-click runtime: unable to normalize legacy stop state\n' >&2
    return 1
  }

  while IFS=$'\t' read -r service container_id extra_field; do
    [[ -n "$service" && -n "$container_id" && -z "$extra_field" ]] || {
      printf 'one-click runtime: invalid legacy stop state\n' >&2
      failed="true"
      continue
    }
    legacy_service_is_allowed "$service" || {
      printf 'one-click runtime: unexpected legacy service in stop state\n' >&2
      failed="true"
      continue
    }
    if ! actual_id="$(docker inspect --format '{{.Id}}' "$container_id" 2>/dev/null)" \
      || ! actual_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container_id")" \
      || ! actual_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")"; then
      failed="true"
      continue
    fi
    [[ "$actual_id" == "$container_id" && "$actual_project" == "$LEGACY_PROJECT" && "$actual_service" == "$service" ]] || {
      printf 'one-click runtime: legacy stop state identity check failed\n' >&2
      failed="true"
      continue
    }
    if ! running="$(docker inspect --format '{{.State.Running}}' "$container_id")"; then
      failed="true"
      continue
    fi
    if [[ "$running" != "true" ]] && ! docker start "$container_id" >/dev/null; then
      failed="true"
    fi
  done < "$LEGACY_STOPPED_STATE_FILE"

  [[ "$failed" != "true" ]] || return 1
  rm -f "$LEGACY_STOPPED_STATE_FILE"
  rmdir "$LEGACY_STOPPED_STATE_DIR" 2>/dev/null || true
}

start_one_click_runtime() (
  local rollback_required="false"
  cleanup_start() {
    local status="$?"
    trap - EXIT INT TERM
    if [[ "$rollback_required" == "true" ]]; then
      if ! compose down; then
        printf 'one-click runtime: unable to stop a partially started one-click stack; legacy container state is preserved for manual recovery\n' >&2
        status=1
      elif ! restore_legacy_application_services; then
        printf 'one-click runtime: failed to restore all prerecorded legacy containers\n' >&2
        status=1
      fi
    fi
    exit "$status"
  }
  trap cleanup_start EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  require_command docker
  init_runtime_environment
  require_runtime_files
  compose config -q
  compose build
  rollback_required="true"
  prepare_legacy_stop_state || die "failed to prerecord legacy application containers"
  stop_legacy_application_services || die "failed to stop legacy application containers"
  compose up -d --no-build --scale acquisition-worker=3 \
    || die "one-click startup failed"
  rollback_required="false"
  trap - EXIT INT TERM
)

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
  python3 - "$archive" <<'PY'
import os
import pathlib
import gzip
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])


def positive_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be a positive integer")
    return value


max_compressed = positive_limit("ONE_CLICK_BACKUP_MAX_COMPRESSED_BYTES", 1_073_741_824)
max_members = positive_limit("ONE_CLICK_BACKUP_MAX_MEMBERS", 100_000)
max_single_file = positive_limit("ONE_CLICK_BACKUP_MAX_SINGLE_FILE_BYTES", 536_870_912)
max_total = positive_limit("ONE_CLICK_BACKUP_MAX_TOTAL_BYTES", 2_147_483_648)
max_ratio = positive_limit("ONE_CLICK_BACKUP_MAX_COMPRESSION_RATIO", 200)
compressed_size = archive.stat().st_size
if compressed_size > max_compressed:
    raise SystemExit("research archive exceeds compressed byte limit")
if compressed_size <= 0:
    raise SystemExit("research archive is empty")

raw_member_count = 0
raw_total = 0
file_total = 0


def account_raw(size: int) -> None:
    global raw_total
    raw_total += size
    if raw_total > max_total:
        raise SystemExit("research archive exceeds total byte limit")
    if raw_total > compressed_size * max_ratio:
        raise SystemExit("research archive exceeds compression ratio limit")


def discard_exact(stream: gzip.GzipFile, size: int) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            raise SystemExit("research archive is truncated")
        account_raw(len(chunk))
        remaining -= len(chunk)


def parse_ustar_size(field: bytes) -> int:
    if field and field[0] & 0x80:
        raise SystemExit("research archive uses unsupported base-256 size encoding")
    raw = field.rstrip(b"\0 ").lstrip(b" ") or b"0"
    if any(value not in b"01234567" for value in raw):
        raise SystemExit("research archive has an invalid USTAR size")
    return int(raw, 8)


try:
    with gzip.open(archive, mode="rb") as raw_bundle:
        zero_blocks = 0
        while True:
            header = raw_bundle.read(512)
            if not header:
                break
            account_raw(len(header))
            if len(header) != 512:
                raise SystemExit("research archive has a partial USTAR header")
            if header == bytes(512):
                zero_blocks += 1
                if zero_blocks >= 2:
                    while True:
                        trailing = raw_bundle.read(64 * 1024)
                        if not trailing:
                            break
                        account_raw(len(trailing))
                        if any(trailing):
                            raise SystemExit("research archive has data after its end marker")
                    break
                continue
            zero_blocks = 0
            raw_member_count += 1
            if raw_member_count > max_members:
                raise SystemExit("research archive exceeds member count limit")
            member_size = parse_ustar_size(header[124:136])
            member_type = header[156:157]
            if member_type in (b"", b"\0", b"0"):
                if member_size > max_single_file:
                    raise SystemExit("research archive exceeds single file byte limit")
                file_total += member_size
                if file_total > max_total:
                    raise SystemExit("research archive exceeds total byte limit")
            elif member_type == b"5":
                if member_size != 0:
                    raise SystemExit("research archive directory has a non-zero payload")
            else:
                raise SystemExit("unsafe research archive member type is unsupported by the USTAR policy")
            discard_exact(raw_bundle, member_size)
            padding = (-member_size) % 512
            if padding:
                discard_exact(raw_bundle, padding)

    member_count = 0
    semantic_file_total = 0
    bundle = tarfile.open(archive, mode="r|gz")
    with bundle:
        for member in bundle:
            member_count += 1
            if member_count > max_members:
                raise SystemExit("research archive exceeds member count limit")
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
            if member.isfile():
                if member.size < 0 or member.size > max_single_file:
                    raise SystemExit("research archive exceeds single file byte limit")
                semantic_file_total += member.size
                if semantic_file_total > max_total:
                    raise SystemExit("research archive exceeds total byte limit")
            elif member.size != 0:
                raise SystemExit("research archive directory has a non-zero payload")
except (OSError, tarfile.TarError) as exc:
    raise SystemExit(f"research archive is unreadable: {exc}") from exc
PY
}

snapshot_backup_bundle() {
  local source_dir="$1"
  local destination_dir="$2"
  python3 - "$source_dir" "$destination_dir" <<'PY'
import os
import stat
import sys

source_path, destination_path = sys.argv[1:]
artifacts = ("manifest.sha256", "postgres.dump", "research-files.tar.gz")


def positive_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be a positive integer")
    return value


artifact_limits = {
    "manifest.sha256": positive_limit("ONE_CLICK_BACKUP_MAX_MANIFEST_BYTES", 65_536),
    "postgres.dump": positive_limit("ONE_CLICK_BACKUP_MAX_POSTGRES_DUMP_BYTES", 8_589_934_592),
    "research-files.tar.gz": positive_limit("ONE_CLICK_BACKUP_MAX_COMPRESSED_BYTES", 1_073_741_824),
}


def limit_error(artifact: str, during_copy: bool = False) -> str:
    if artifact == "research-files.tar.gz":
        return "research archive exceeds compressed byte limit"
    suffix = " while copying" if during_copy else ""
    return f"backup artifact exceeds byte limit{suffix}: {artifact}"


directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
source_fd = os.open(source_path, directory_flags)
try:
    if set(os.listdir(source_fd)) != set(artifacts):
        raise SystemExit("backup directory contains unexpected artifacts")
    for artifact in artifacts:
        source_file_fd = os.open(
            artifact,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_fd,
        )
        try:
            metadata = os.fstat(source_file_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise SystemExit(f"backup artifact is not a regular file: {artifact}")
            limit = artifact_limits[artifact]
            if metadata.st_size > limit:
                raise SystemExit(limit_error(artifact))
            destination_file_fd = os.open(
                os.path.join(destination_path, artifact),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(source_file_fd, "rb", closefd=False) as source_file:
                with os.fdopen(destination_file_fd, "wb") as destination_file:
                    copied = 0
                    while True:
                        chunk = source_file.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > limit:
                            raise SystemExit(limit_error(artifact, during_copy=True))
                        destination_file.write(chunk)
                    destination_file.flush()
                    os.fsync(destination_file.fileno())
                    if copied != metadata.st_size or os.fstat(source_file_fd).st_size != metadata.st_size:
                        raise SystemExit(f"backup artifact changed while copying: {artifact}")
        finally:
            os.close(source_file_fd)
finally:
    os.close(source_fd)
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
  local parent temporary_dir volume_name project_name
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
  project_name="$(compose_project_name)"
  validate_live_files_volume "$volume_name" "$project_name"

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
  [[ "$database_state" != "finalizing_uncertain" ]] || return 0
  printf 'one-click runtime: file recovery command: docker run --rm -v %q:/source:ro -v %q:/target alpine sh -eu -c %q\n' \
    "$rollback_volume" "$files_volume" \
    'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; cp -a /source/. /target/' >&2
}

restore_runtime() (
  set -euo pipefail
  local backup_dir="$1"
  local private_backup="" private_backup_parent=""
  local database_user="" database_name="" suffix="" staging_database="" old_database=""
  local project_name="" files_volume="" staging_volume="" rollback_volume=""
  local database_swapped="false"
  local preserve_recovery_state="false"
  local database_state="pre_cutover"
  local staging_volume_tracked="false"
  local rollback_volume_tracked="false"

  cleanup_restore() {
    if [[ -n "$private_backup" ]]; then
      rm -rf -- "$private_backup"
    fi
    if [[ -z "$staging_volume" ]]; then
      return 0
    fi
    if [[ "$preserve_recovery_state" == "true" ]]; then
      print_restore_recovery_instructions \
        "$database_user" "$database_name" "$staging_database" "$old_database" \
        "$database_state" \
        "$files_volume" "$staging_volume" "$rollback_volume"
      return 0
    fi
    if [[ "$staging_volume_tracked" == "true" ]]; then
      remove_operation_volume \
        "$staging_volume" "$project_name" "$suffix" restore-staging || true
    fi
    if [[ "$rollback_volume_tracked" == "true" ]]; then
      remove_operation_volume \
        "$rollback_volume" "$project_name" "$suffix" restore-rollback || true
    fi
    if [[ "$database_swapped" != "true" && -n "$staging_database" ]]; then
      compose exec -T postgres dropdb --if-exists -U "$database_user" "$staging_database" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup_restore EXIT

  validate_existing_backup_path "$backup_dir"
  require_command python3
  require_command shasum
  private_backup_parent="$(cd -P "${TMPDIR:-/tmp}" && pwd)"
  private_backup="$(mktemp -d "${private_backup_parent}/one-click-restore.XXXXXX")"
  chmod 700 "$private_backup"
  snapshot_backup_bundle "$backup_dir" "$private_backup"
  validate_backup_bundle "$private_backup"
  require_command docker
  require_command openssl
  require_runtime_files
  require_application_services_stopped restore

  database_user="$(runtime_env_value ONE_CLICK_POSTGRES_USER)"
  database_name="$(runtime_env_value ONE_CLICK_POSTGRES_DB)"
  [[ "$database_name" =~ ^[A-Za-z_][A-Za-z0-9_]{0,39}$ ]] \
    || die "one-click database name is unsafe for restore"
  suffix="$(openssl rand -hex 4)"
  staging_database="${database_name}_restore_${suffix}"
  old_database="${database_name}_before_${suffix}"
  files_volume="$(files_volume_name)"
  project_name="$(compose_project_name)"
  staging_volume="${files_volume}-restore-${suffix}"
  rollback_volume="${files_volume}-before-${suffix}"
  validate_live_files_volume "$files_volume" "$project_name"

  compose exec -T postgres createdb -U "$database_user" "$staging_database"
  compose exec -T postgres pg_restore --exit-on-error --no-owner --no-privileges \
    -U "$database_user" -d "$staging_database" < "$private_backup/postgres.dump"
  staging_volume_tracked="true"
  create_operation_volume \
    "$staging_volume" "$project_name" "$suffix" restore-staging
  validate_operation_volume \
    "$staging_volume" "$project_name" "$suffix" restore-staging \
    || die "restore staging volume identity changed before extraction"
  docker run --rm \
    -v "$staging_volume:/target" \
    -v "$private_backup:/backup:ro" \
    alpine tar -C /target -xzf /backup/research-files.tar.gz

  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps migrate
  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps api python -m app.scripts.load_product_foundation_fixture
  ONE_CLICK_POSTGRES_DB="$staging_database" ONE_CLICK_FILES_VOLUME="$staging_volume" \
    compose run --rm --no-deps api python -m app.scripts.verify_underwriting_revision_manifests

  rollback_volume_tracked="true"
  create_operation_volume \
    "$rollback_volume" "$project_name" "$suffix" restore-rollback
  validate_live_files_volume "$files_volume" "$project_name"
  validate_operation_volume \
    "$rollback_volume" "$project_name" "$suffix" restore-rollback \
    || die "restore rollback volume identity changed before snapshot"
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
  validate_live_files_volume "$files_volume" "$project_name"
  validate_operation_volume \
    "$staging_volume" "$project_name" "$suffix" restore-staging \
    || die "restore staging volume identity changed before activation"
  if ! copy_volume_contents "$staging_volume" "$files_volume"; then
    local files_rollback_succeeded="true"
    local database_rollback_succeeded="true"
    if ! validate_live_files_volume "$files_volume" "$project_name" \
      || ! validate_operation_volume \
        "$rollback_volume" "$project_name" "$suffix" restore-rollback \
      || ! copy_volume_contents "$rollback_volume" "$files_volume"; then
      files_rollback_succeeded="false"
    fi
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
