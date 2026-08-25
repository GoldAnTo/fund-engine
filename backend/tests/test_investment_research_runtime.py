from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


ROOT = Path(__file__).parents[2]


def _runtime_copy(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "docker-compose.one-click.yml").touch()
    (tmp_path / ".env").touch()
    (tmp_path / ".env.one-click.local").write_text(
        "ONE_CLICK_POSTGRES_DB=fund_engine_one_click\n"
        "ONE_CLICK_POSTGRES_USER=one_click\n"
        "ONE_CLICK_POSTGRES_PASSWORD=secret\n"
    )
    return script


def _write_backup_bundle(backup: Path) -> None:
    backup.mkdir()
    (backup / "postgres.dump").write_bytes(b"custom dump")
    source = backup.parent / f"{backup.name}-archive-source"
    source.mkdir()
    subprocess.run(
        ["tar", "-C", source, "-czf", backup / "research-files.tar.gz", "."],
        check=True,
    )
    manifest = subprocess.run(
        [
            "shasum",
            "-a",
            "256",
            "postgres.dump",
            "research-files.tar.gz",
        ],
        cwd=backup,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (backup / "manifest.sha256").write_text(manifest)


def _stateful_volume_docker(extra_cases: str = "") -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *' config --format json'*) printf '%s' '{{"name":"test-project","volumes":{{"fund-engine-one-click-data":{{"name":"fund-engine-one-click-data"}},"fund-engine-one-click-files":{{"name":"fund-engine-one-click-files"}}}}}}'; exit 0 ;;
{extra_cases}
  *' ps --status running --services'*) printf 'postgres\\n'; exit 0 ;;
esac
if [[ "$1 $2" == "volume inspect" ]]; then
  name="${{!#}}"
  if [[ "$name" == "fund-engine-one-click-files" ]]; then
    printf '%s\\n' 'fund-engine-one-click-files|test-project|fund-engine-one-click-files|||'
  elif [[ "$name" == "fund-engine-one-click-data" ]]; then
    printf '%s\\n' 'fund-engine-one-click-data|test-project|fund-engine-one-click-data|||'
  elif [[ -f "$VOLUME_STATE/$name" ]]; then
    cat "$VOLUME_STATE/$name"
  else
    exit 1
  fi
  exit 0
fi
if [[ "$1 $2" == "volume create" ]]; then
  operation=""
  project=""
  purpose=""
  while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "--label" ]]; then
      shift
      case "$1" in
        com.fund-engine.one-click.project=*) project="${{1#*=}}" ;;
        com.fund-engine.one-click.operation=*) operation="${{1#*=}}" ;;
        com.fund-engine.one-click.purpose=*) purpose="${{1#*=}}" ;;
      esac
    fi
    shift
  done
  [[ -n "$operation" && -n "$project" && -n "$purpose" ]] || exit 91
  name="generated-$purpose-$operation-$RANDOM"
  printf '%s\\n' "$name|||$project|$operation|$purpose" > "$VOLUME_STATE/$name"
  if [[ "${{INTERRUPT_OPERATION_CREATE:-}}" == "$purpose" ]]; then
    kill -TERM "$PPID"
    sleep 0.1
  fi
  printf '%s\\n' "$name"
  exit 0
fi
if [[ "$1 $2" == "volume ls" ]]; then
  filters="$*"
  for state in "$VOLUME_STATE"/*; do
    [[ -e "$state" ]] || continue
    IFS='|' read -r stored_name _ _ stored_project stored_operation stored_purpose < "$state"
    [[ "$filters" == *"label=com.fund-engine.one-click.project=$stored_project"* ]] || continue
    [[ "$filters" == *"label=com.fund-engine.one-click.operation=$stored_operation"* ]] || continue
    [[ "$filters" == *"label=com.fund-engine.one-click.purpose=$stored_purpose"* ]] || continue
    printf '%s\\n' "$stored_name"
  done
  exit 0
fi
if [[ "$1 $2" == "volume rm" ]]; then
  name="${{!#}}"
  rm -f "$VOLUME_STATE/$name"
  exit 0
fi
exit 0
"""


def _replace_research_archive_with_raw_member(
    backup: Path,
    *,
    member_type: bytes,
    member_size: int,
) -> None:
    member = tarfile.TarInfo("raw-member")
    member.type = member_type
    member.size = member_size
    raw_archive = (
        member.tobuf(format=tarfile.USTAR_FORMAT)
        + bytes(member_size)
        + bytes((-member_size) % 512)
        + bytes(1024)
    )
    _replace_research_archive_bytes(backup, raw_archive)


def _replace_research_archive_bytes(backup: Path, raw_archive: bytes) -> None:
    with gzip.open(backup / "research-files.tar.gz", "wb") as archive:
        archive.write(raw_archive)
    manifest = subprocess.run(
        ["shasum", "-a", "256", "postgres.dump", "research-files.tar.gz"],
        cwd=backup,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (backup / "manifest.sha256").write_text(manifest)


def _refresh_ustar_checksum(header: bytearray) -> None:
    header[148:156] = b"        "
    checksum = sum(header)
    header[148:156] = f"{checksum:06o}\0 ".encode()


@pytest.mark.parametrize("target", ["relative", "/", "/tmp", "/var", "/Users"])
def test_backup_rejects_non_specific_targets_before_docker(
    tmp_path: Path, target: str
) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "backup", target],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert "specific absolute" in completed.stderr
    assert "docker must not run" not in completed.stderr


def test_backup_rejects_existing_and_symlink_targets_before_docker(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    symlink = tmp_path / "linked"
    symlink.symlink_to(existing, target_is_directory=True)

    for target in (existing, symlink):
        completed = subprocess.run(
            [script, "backup", str(target)], capture_output=True, text=True
        )
        assert completed.returncode != 0
        assert "must not already exist" in completed.stderr


def test_runtime_compose_installs_fixture_and_has_optional_integrations() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()

    assert "alembic upgrade head" in compose
    assert "python -m app.scripts.load_product_foundation_fixture" in compose
    assert compose.index("alembic upgrade head") < compose.index(
        "python -m app.scripts.load_product_foundation_fixture"
    )
    for value in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "GILDATA_TOKEN"):
        assert f"${{{value}:-}}" in compose
        assert f"${{{value}:?}}" not in compose
    assert "${ACQUISITION_ENABLED_ADAPTERS:-sse,szse}" in compose
    assert "${ONE_CLICK_API_PORT:-8000}" in compose
    assert "${ONE_CLICK_FRONTEND_PORT:-8080}" in compose
    assert "${ONE_CLICK_BACKEND_IMAGE:-fund-engine-one-click-backend:local}" in compose
    assert (
        "${ONE_CLICK_FRONTEND_IMAGE:-fund-engine-one-click-frontend:local}" in compose
    )
    assert "file-store-init:" in compose
    assert compose.count("context: ./backend") == 1
    assert "install -d -o app -g app -m 700 /data/research-files" in compose
    following_service = {
        "api": "research-worker",
        "research-worker": "acquisition-worker",
        "acquisition-worker": "frontend",
    }
    for service in ("api", "research-worker", "acquisition-worker"):
        section_start = compose.index(f"  {service}:\n")
        section_end = compose.index(f"  {following_service[service]}:\n")
        section = compose[section_start:section_end]
        assert "fund-engine-one-click-files:/data/research-files" in section


def test_runtime_backup_restore_contract_is_fail_closed() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()

    assert "pg_dump -Fc" in script
    assert "research-files.tar.gz" in script
    assert "manifest.sha256" in script
    assert "mktemp -d" in script
    assert "mv --" in script
    assert "--exclude=.env" in script
    assert "--exclude=.env*" in script
    assert "sha256" in script
    assert "verify_underwriting_revision_manifests" in script
    assert "load_product_foundation_fixture" in script
    assert script.count("compose run --rm --no-deps") >= 4
    assert "require_application_services_stopped backup" in script
    assert "require_application_services_stopped restore" in script
    assert 'preserve_recovery_state="true"' in script
    cutover_start = script.index(
        'copy_volume_contents "$files_volume" "$rollback_volume"'
    )
    first_destructive_rename = script.index(
        "compose exec -T postgres psql", cutover_start
    )
    assert script.index('preserve_recovery_state="true"', cutover_start) < (
        first_destructive_rename
    )
    final_drop = script.index(
        'compose exec -T postgres dropdb -U "$database_user" "$old_database"'
    )
    assert script.index('database_state="finalizing_uncertain"') < final_drop
    assert script.index('preserve_recovery_state="false"', final_drop) > final_drop
    assert "automatic restore rollback failed; preserved" in script
    assert "manual recovery required; keep application services stopped" in script
    assert "docker volume inspect" in script
    assert "database rollback step 1" in script
    assert "database rollback step 2" in script
    assert "file recovery command" in script
    assert "compose config --format json" in script
    assert "com.docker.compose.project" in script
    assert "com.docker.compose.volume" in script
    assert "com.fund-engine.one-click.operation" in script
    assert "com.fund-engine.one-click.purpose" in script
    assert "snapshot_backup_bundle" in script
    assert 'mode="r|gz"' in script
    assert "getmembers" not in script
    for limit in (
        "ONE_CLICK_BACKUP_MAX_COMPRESSED_BYTES",
        "ONE_CLICK_BACKUP_MAX_MEMBERS",
        "ONE_CLICK_BACKUP_MAX_SINGLE_FILE_BYTES",
        "ONE_CLICK_BACKUP_MAX_TOTAL_BYTES",
        "ONE_CLICK_BACKUP_MAX_COMPRESSION_RATIO",
        "ONE_CLICK_BACKUP_MAX_MANIFEST_BYTES",
        "ONE_CLICK_BACKUP_MAX_POSTGRES_DUMP_BYTES",
    ):
        assert limit in script
    assert "postgres.dump" in script and "research-files.tar.gz" in script
    assert "--volumes" not in script


def test_restore_rejects_unexpected_directory_before_docker(tmp_path: Path) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    (backup / "unexpected").mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert "unexpected artifacts" in completed.stderr
    assert "docker must not run" not in completed.stderr


def test_restore_rejects_checksum_and_unsafe_tar_before_docker(tmp_path: Path) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    bad_checksum = tmp_path / "bad-checksum"
    _write_backup_bundle(bad_checksum)
    (bad_checksum / "postgres.dump").write_bytes(b"tampered")
    checksum_result = subprocess.run(
        [script, "restore", str(bad_checksum)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert checksum_result.returncode != 0
    assert "checksum validation failed" in checksum_result.stderr
    assert "docker must not run" not in checksum_result.stderr

    unsafe_tar = tmp_path / "unsafe-tar"
    _write_backup_bundle(unsafe_tar)
    link = unsafe_tar.parent / "escape-link"
    link.symlink_to("../outside")
    with tarfile.open(unsafe_tar / "research-files.tar.gz", "w:gz") as archive:
        archive.add(link, arcname="escape-link", recursive=False)
    manifest = subprocess.run(
        [
            "shasum",
            "-a",
            "256",
            "postgres.dump",
            "research-files.tar.gz",
        ],
        cwd=unsafe_tar,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (unsafe_tar / "manifest.sha256").write_text(manifest)
    tar_result = subprocess.run(
        [script, "restore", str(unsafe_tar)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert tar_result.returncode != 0
    assert "unsafe research archive member" in tar_result.stderr
    assert "docker must not run" not in tar_result.stderr

    secret_tar = tmp_path / "secret-tar"
    _write_backup_bundle(secret_tar)
    secret_source = secret_tar.parent / "secret-source"
    (secret_source / "nested").mkdir(parents=True)
    (secret_source / "nested" / ".env.production").write_text("TOKEN=secret\n")
    with tarfile.open(
        secret_tar / "research-files.tar.gz",
        "w:gz",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        archive.add(secret_source / "nested", arcname="nested")
    manifest = subprocess.run(
        [
            "shasum",
            "-a",
            "256",
            "postgres.dump",
            "research-files.tar.gz",
        ],
        cwd=secret_tar,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (secret_tar / "manifest.sha256").write_text(manifest)
    secret_result = subprocess.run(
        [script, "restore", str(secret_tar)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert secret_result.returncode != 0
    assert "secret research archive member" in secret_result.stderr
    assert "docker must not run" not in secret_result.stderr


def test_restore_rejects_archive_resource_limit_before_docker(tmp_path: Path) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "ONE_CLICK_BACKUP_MAX_COMPRESSED_BYTES": "1",
        },
    )

    assert completed.returncode != 0
    assert "compressed byte limit" in completed.stderr
    assert "docker must not run" not in completed.stderr


@pytest.mark.parametrize(
    ("member_type", "member_size", "expected_error"),
    [
        (tarfile.DIRTYPE, 1_048_576, "directory has a non-zero payload"),
        (tarfile.XHDTYPE, 1, "USTAR policy"),
        (tarfile.GNUTYPE_LONGNAME, 1, "USTAR policy"),
    ],
)
def test_restore_rejects_raw_tar_metadata_payloads_before_docker(
    tmp_path: Path,
    member_type: bytes,
    member_size: int,
    expected_error: str,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    _replace_research_archive_with_raw_member(
        backup,
        member_type=member_type,
        member_size=member_size,
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert "docker must not run" not in completed.stderr


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("base256_uid", "base-256 uid encoding"),
        ("invalid_magic", "USTAR magic or version"),
        ("missing_end", "double-zero end marker"),
    ],
)
def test_restore_rejects_noncanonical_ustar_headers_before_docker(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    member = tarfile.TarInfo("plain-file")
    member.size = 0
    header = bytearray(member.tobuf(format=tarfile.USTAR_FORMAT))
    ending = bytes(1024)
    if mutation == "base256_uid":
        header[108:116] = b"\x80" + bytes(7)
        _refresh_ustar_checksum(header)
    elif mutation == "invalid_magic":
        header[257:265] = b"invalid!"
        _refresh_ustar_checksum(header)
    else:
        ending = b""
    _replace_research_archive_bytes(backup, bytes(header) + ending)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr
    assert "docker must not run" not in completed.stderr


@pytest.mark.parametrize(
    ("limit_name", "expected_artifact"),
    [
        ("ONE_CLICK_BACKUP_MAX_MANIFEST_BYTES", "manifest.sha256"),
        ("ONE_CLICK_BACKUP_MAX_POSTGRES_DUMP_BYTES", "postgres.dump"),
    ],
)
def test_restore_caps_artifacts_before_private_snapshot_copy(
    tmp_path: Path,
    limit_name: str,
    expected_artifact: str,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nprintf 'docker must not run\\n' >&2\nexit 93\n")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            limit_name: "1",
        },
    )

    assert completed.returncode != 0
    assert f"byte limit: {expected_artifact}" in completed.stderr
    assert "docker must not run" not in completed.stderr


def test_restore_checks_stopped_services_before_database_mutation(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  *' ps --status running --services'*) printf 'api\\npostgres\\n' ;;\n"
        "esac\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
        },
    )

    assert completed.returncode != 0
    assert "stop application services before restore" in completed.stderr
    assert "createdb" not in log.read_text()


def test_restore_refuses_foreign_live_files_volume_before_mutation(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *\' config --format json\'*) printf \'%s\' \'{"name":"expected-project","volumes":{"fund-engine-one-click-data":{"name":"expected-db"},"fund-engine-one-click-files":{"name":"expected-files"}}}\' ;;\n'
        "  *' ps --status running --services'*) printf 'postgres\\n' ;;\n"
        "  *'volume inspect --format'*' expected-db'*) printf '%s\\n' 'expected-db|expected-project|fund-engine-one-click-data|||' ;;\n"
        "  *'volume inspect --format'*' expected-files'*) printf '%s\\n' 'expected-files|foreign-project|foreign-logical|||' ;;\n"
        "esac\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
        },
    )

    assert completed.returncode != 0
    assert "volume identity" in completed.stderr
    commands = log.read_text()
    assert "createdb" not in commands
    assert "docker run" not in commands
    assert "volume rm" not in commands


def test_up_refuses_foreign_postgres_volume_before_start_or_legacy_stop(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *\' config --format json\'*) printf \'%s\' \'{"name":"expected-project","volumes":{"fund-engine-one-click-data":{"name":"expected-db"},"fund-engine-one-click-files":{"name":"expected-files"}}}\'; exit 0 ;;\n'
        "  *' config -q'*) exit 0 ;;\n"
        "  *' build'*) exit 0 ;;\n"
        "esac\n"
        'if [[ "$1 $2" == "volume inspect" && "${!#}" == "expected-db" ]]; then\n'
        "  printf '%s\\n' 'expected-db|foreign-project|fund-engine-one-click-data|||'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "up"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
        },
    )

    assert completed.returncode != 0
    assert "postgres volume identity" in completed.stderr
    commands = log.read_text()
    assert " up -d --no-build" not in commands
    assert " create postgres" not in commands
    assert not any(line.startswith("stop ") for line in commands.splitlines())


def test_up_creates_and_validates_fresh_postgres_volume_before_full_start(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    state = tmp_path / "postgres-volume-created"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *\' config --format json\'*) printf \'%s\' \'{"name":"fresh-project","volumes":{"fund-engine-one-click-data":{"name":"fresh-db"},"fund-engine-one-click-files":{"name":"fresh-files"}}}\'; exit 0 ;;\n'
        "  *' config -q'*) exit 0 ;;\n"
        "  *' build'*) exit 0 ;;\n"
        "  *' create postgres'*) : > \"$VOLUME_STATE\"; exit 0 ;;\n"
        "  *' up -d --no-build'*) exit 0 ;;\n"
        "esac\n"
        'if [[ "$1 $2" == "volume inspect" && "${!#}" == "fresh-db" ]]; then\n'
        "  if [[ ! -e \"$VOLUME_STATE\" ]]; then printf 'No such volume: fresh-db\\n' >&2; exit 1; fi\n"
        "  printf '%s\\n' 'fresh-db|fresh-project|fund-engine-one-click-data|||'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "up"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(state),
        },
    )

    assert completed.returncode == 0, completed.stderr
    commands = log.read_text().splitlines()
    create_index = next(
        i for i, value in enumerate(commands) if " create postgres" in value
    )
    validate_index = next(
        i
        for i, value in enumerate(commands)
        if value.startswith("volume inspect --format") and value.endswith(" fresh-db")
    )
    up_index = next(
        i for i, value in enumerate(commands) if " up -d --no-build" in value
    )
    assert create_index < validate_index < up_index


def test_up_fails_closed_when_postgres_volume_inspection_is_uncertain(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *\' config --format json\'*) printf \'%s\' \'{"name":"test-project","volumes":{"fund-engine-one-click-data":{"name":"test-db"}}}\'; exit 0 ;;\n'
        "  *' config -q'*) exit 0 ;;\n"
        "  *' build'*) exit 0 ;;\n"
        "esac\n"
        'if [[ "$1 ${2:-}" == "volume inspect" ]]; then printf \'daemon timeout\\n\' >&2; exit 71; fi\n'
        'if [[ "$1" == "info" ]]; then exit 0; fi\n'
        "exit 0\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "up"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
        },
    )

    assert completed.returncode != 0
    assert "determine whether postgres volume exists safely" in completed.stderr
    commands = log.read_text()
    assert " create postgres" not in commands
    assert " up -d --no-build" not in commands


def test_restore_createdb_collision_never_drops_preexisting_database(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        _stateful_volume_docker(
            "  *' exec -T postgres createdb -U one_click '*) exit 48 ;;"
        )
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    openssl = fake_bin / "openssl"
    operation = "0123456789abcdef0123456789abcdef"
    openssl.write_text(f"#!/bin/sh\nprintf '%s\\n' '{operation}'\n")
    openssl.chmod(openssl.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
        },
    )

    assert completed.returncode != 0
    commands = log.read_text()
    staging_database = f"restore_{operation}"
    assert f"createdb -U one_click {staging_database}" in commands
    assert f"dropdb --if-exists -U one_click {staging_database}" not in commands


def test_restore_uses_docker_generated_operation_volumes(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(_stateful_volume_docker())
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
        },
    )

    assert completed.returncode == 0, completed.stderr
    commands = log.read_text()
    create_commands = [
        line for line in commands.splitlines() if line.startswith("volume create ")
    ]
    assert len(create_commands) == 2
    assert all(
        "com.fund-engine.one-click.operation=" in line for line in create_commands
    )
    assert all(
        "fund-engine-one-click-files-restore-" not in line for line in create_commands
    )
    assert "generated-restore-staging-" in commands
    assert "generated-restore-rollback-" in commands
    assert not tuple(volume_state.iterdir())


def test_restore_cleanup_is_scoped_to_current_operation_nonce(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    existing_name = "preexisting-unrelated-operation-volume"
    existing_identity = (
        f"{existing_name}|||test-project|different-operation|restore-staging\n"
    )
    (volume_state / existing_name).write_text(existing_identity)
    docker = fake_bin / "docker"
    docker.write_text(_stateful_volume_docker())
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert (volume_state / existing_name).read_text() == existing_identity
    assert f"volume rm {existing_name}" not in log.read_text()


def test_restore_interrupt_during_operation_volume_create_cleans_owned_volume(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(_stateful_volume_docker())
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
            "INTERRUPT_OPERATION_CREATE": "restore-staging",
        },
    )

    assert completed.returncode != 0
    assert not tuple(volume_state.iterdir())
    assert "volume rm generated-restore-staging-" in log.read_text()


def test_backup_checks_stopped_services_before_dump(tmp_path: Path) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        "  *' ps --status running --services'*) printf 'migrate\\npostgres\\n' ;;\n"
        "esac\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "backup", str(tmp_path / "backup")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
        },
    )

    assert completed.returncode != 0
    assert "stop application services before backup" in completed.stderr
    assert "pg_dump" not in log.read_text()


def test_backup_preserves_quoted_path_and_cleans_up_after_dump_failure(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf \'<%s>\\n\' "$@" >> "$DOCKER_LOG"\n'
        'case "$*" in\n'
        '  *\' config --format json\'*) printf \'%s\' \'{"name":"test-project","volumes":{"fund-engine-one-click-data":{"name":"custom-task11-db"},"fund-engine-one-click-files":{"name":"custom-task11-files"}}}\' ;;\n'
        "  *' ps --status running --services'*) printf 'postgres\\n' ;;\n"
        "  *'volume inspect --format'*' custom-task11-db'*) printf '%s\\n' 'custom-task11-db|test-project|fund-engine-one-click-data|||' ;;\n"
        "  *'volume inspect --format'*' custom-task11-files'*) printf '%s\\n' 'custom-task11-files|test-project|fund-engine-one-click-files|||' ;;\n"
        "  *' pg_dump -Fc '*) if [ \"${FAIL_DUMP:-0}\" = 1 ]; then exit 17; else printf 'custom-dump'; fi ;;\n"
        "  *' alpine sh -eu -c '*) tar -czf - --files-from /dev/null ;;\n"
        "esac\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
    }
    with (tmp_path / ".env.one-click.local").open("a") as runtime_env:
        runtime_env.write("ONE_CLICK_FILES_VOLUME=custom-task11-files\n")
    output = tmp_path / "backup with spaces"

    completed = subprocess.run(
        [script, "backup", str(output)], capture_output=True, text=True, env=env
    )

    assert completed.returncode == 0, completed.stderr
    assert {item.name for item in output.iterdir()} == {
        "manifest.sha256",
        "postgres.dump",
        "research-files.tar.gz",
    }
    assert "<custom-task11-files:/source:ro>" in log.read_text()

    failed_output = tmp_path / "failed backup"
    failed = subprocess.run(
        [script, "backup", str(failed_output)],
        capture_output=True,
        text=True,
        env={**env, "FAIL_DUMP": "1"},
    )
    assert failed.returncode != 0
    assert not failed_output.exists()
    assert not tuple(tmp_path.glob(".one-click-backup.*"))

    for limit_name in (
        "ONE_CLICK_BACKUP_MAX_POSTGRES_DUMP_BYTES",
        "ONE_CLICK_BACKUP_MAX_MANIFEST_BYTES",
    ):
        limited_output = tmp_path / f"limited-{limit_name.lower()}"
        limited = subprocess.run(
            [script, "backup", str(limited_output)],
            capture_output=True,
            text=True,
            env={**env, limit_name: "1"},
        )
        assert limited.returncode != 0
        assert "backup artifact exceeds byte limit" in limited.stderr
        assert not limited_output.exists()


def test_restore_preserves_recovery_artifacts_when_compensation_fails(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        _stateful_volume_docker(
            "  *'run --rm -v generated-restore-staging-'*':/source:ro'*) exit 41 ;;\n"
            "  *'run --rm -v generated-restore-rollback-'*':/source:ro'*) exit 42 ;;"
        )
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
        },
    )

    commands = log.read_text()
    assert completed.returncode != 0
    assert "automatic restore rollback failed; preserved" in completed.stderr
    assert (
        "manual recovery required; keep application services stopped"
        in completed.stderr
    )
    assert "docker volume inspect" in completed.stderr
    assert "recovery command" in completed.stderr
    assert "volume rm" not in commands
    assert "dropdb" not in commands


def test_restore_finalization_uncertainty_prints_inventory_only(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        _stateful_volume_docker(
            "  *' exec -T postgres dropdb -U one_click before_'*) exit 44 ;;"
        )
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
        },
    )

    assert completed.returncode != 0
    assert "final database cleanup may have completed" in completed.stderr
    assert "inspect databases with" in completed.stderr
    assert "docker volume inspect" in completed.stderr
    assert "database rollback" not in completed.stderr
    assert "file recovery command" not in completed.stderr
    assert "volume rm" not in log.read_text()


def test_restore_uses_private_snapshot_after_source_bundle_changes(
    tmp_path: Path,
) -> None:
    script = _runtime_copy(tmp_path)
    backup = tmp_path / "backup"
    _write_backup_bundle(backup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    volume_state = tmp_path / "volumes"
    volume_state.mkdir()
    private_tmp = tmp_path / "private-tmp"
    private_tmp.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        _stateful_volume_docker(
            "  *' ps --status running --services'*) printf 'mutated source' > \"$SOURCE_BACKUP/postgres.dump\"; printf 'postgres\\n'; exit 0 ;;\n"
            "  *' exec -T postgres pg_restore '* ) content=$(cat); [[ \"$content\" == 'custom dump' ]] || exit 88; exit 0 ;;"
        )
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        [script, "restore", str(backup)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_LOG": str(log),
            "VOLUME_STATE": str(volume_state),
            "SOURCE_BACKUP": str(backup),
            "TMPDIR": str(private_tmp),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert (backup / "postgres.dump").read_text() == "mutated source"
    assert str(backup) not in log.read_text()
    assert not tuple(private_tmp.iterdir())


def test_runtime_verifier_checks_increment_a_shell_api_and_revision() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()

    assert 'require_revision "$new_revision" 0065' in script
    assert 'FRONTEND_URL="${ONE_CLICK_FRONTEND_URL:-http://127.0.0.1:' in script
    assert 'API_URL="${ONE_CLICK_API_URL:-http://127.0.0.1:' in script
    assert '"$FRONTEND_URL/research"' in script
    assert "投资研究" in script
    assert '"$API_URL/api/underwriting/v1/product/objects?query=CATL"' in script
    assert "CATL object foundation is incomplete" in script
    assert "printf 'Authorization: Bearer %s\\n'" in script
    assert "--header @-" in script
    assert '-H "Authorization: Bearer ${bearer_token}"' not in script


def test_increment_a_gate_uses_repo_python_and_covers_all_layers() -> None:
    script = ROOT / "scripts" / "verify-investment-research-foundation.sh"
    contents = script.read_text()

    assert "BACKEND_PYTHON" in contents
    assert "backend/.venv/bin/python" in contents
    assert "pytest" in contents
    assert "test_product_legacy_compatibility.py" in contents
    assert "test_one_click_runtime_scripts.py" in contents
    assert "test_investment_research_runtime.py" in contents
    assert "npm test" in contents
    assert "npm run typecheck" in contents
    assert "npm run build" in contents
    assert "compileall" in contents
    assert "set -euo pipefail" in contents


def test_manifest_verifier_rejects_schema_without_product_columns() -> None:
    from app.scripts.verify_underwriting_revision_manifests import (
        RevisionManifestVerificationError,
        verify_revision_manifests,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE uw_research_versions (id VARCHAR(36), version_kind VARCHAR(64))"
        )

    with (
        Session(engine) as session,
        pytest.raises(RevisionManifestVerificationError, match="schema"),
    ):
        verify_revision_manifests(session)


def test_manifest_verifier_declares_every_product_replay_table() -> None:
    from app.scripts.verify_underwriting_revision_manifests import (
        _REQUIRED_PRODUCT_TABLES,
    )

    assert _REQUIRED_PRODUCT_TABLES == {
        "uw_research_objects",
        "uw_historical_bases",
        "uw_mandate_versions",
        "uw_research_projects",
        "uw_research_project_securities",
        "uw_research_scope_versions",
        "uw_research_agenda_versions",
        "uw_price_snapshots",
        "uw_fx_snapshots",
        "uw_capital_structure_snapshots",
        "uw_security_rights_versions",
        "uw_research_assessment_versions",
        "uw_revision_boundaries",
        "uw_revision_manifests",
    }


def test_manifest_verifier_stops_at_first_corrupt_independent_revision(
    session, monkeypatch
) -> None:
    from app.scripts.verify_underwriting_revision_manifests import (
        RevisionManifestVerificationError,
        verify_revision_manifests,
    )

    revision_ids = [uuid4(), uuid4()]
    monkeypatch.setattr(session, "scalars", lambda _statement: revision_ids)
    seen = []

    class CorruptingReader:
        def __init__(self, _session) -> None:
            pass

        def revision_summary(self, revision_id):
            seen.append(revision_id)
            raise ValueError("corrupt manifest")

    with pytest.raises(RevisionManifestVerificationError, match=str(revision_ids[0])):
        verify_revision_manifests(session, reader_factory=CorruptingReader)

    assert seen == [revision_ids[0]]


def test_manifest_verifier_replays_a_real_catl_foundation_revision(session) -> None:
    from app.scripts.verify_underwriting_revision_manifests import (
        verify_revision_manifests,
    )
    from app.underwriting.services.revision_publisher import RevisionPublisher
    from tests.underwriting.test_revision_publisher import (
        NOW,
        _foundation_ready_graph,
    )

    graph = _foundation_ready_graph(
        session,
        company_key="CN:300750:COMPANY",
        security_keys=("SZSE:300750",),
    )
    revision = RevisionPublisher(session, now=lambda: NOW).publish(
        graph["project"].id,
        graph["draft"].lock_version,
        idempotency_key="runtime-manifest-verifier",
    )
    session.flush()

    result = verify_revision_manifests(session)

    assert result.count == 1
    assert result.revision_hashes == ((revision.id, revision.manifest_hash),)


def test_fixture_loader_commits_only_in_explicit_runner() -> None:
    loader = (
        ROOT / "backend" / "app" / "scripts" / "load_product_foundation_fixture.py"
    ).read_text()
    service = (
        ROOT
        / "backend"
        / "app"
        / "underwriting"
        / "services"
        / "product_foundation_fixture.py"
    ).read_text()

    assert "ProductFoundationFixtureService" in loader
    assert "session.commit()" in loader
    assert "session.commit()" not in service
