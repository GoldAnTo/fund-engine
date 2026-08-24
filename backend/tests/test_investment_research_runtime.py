from __future__ import annotations

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
    assert "tar -tzf" in script
    assert "verify_underwriting_revision_manifests" in script
    assert "load_product_foundation_fixture" in script
    assert script.count("compose run --rm --no-deps") >= 4
    assert "require_application_services_stopped backup" in script
    assert "require_application_services_stopped restore" in script
    assert 'preserve_recovery_state="true"' in script
    assert "automatic restore rollback failed; preserved" in script
    assert "manual recovery required; keep application services stopped" in script
    assert "docker volume inspect" in script
    assert "compose config --format json" in script
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
    with tarfile.open(secret_tar / "research-files.tar.gz", "w:gz") as archive:
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
        "  *' ps --status running --services'*) printf 'api\\npostgres\\n' ;;\n"
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
        "  *' config --format json'*) printf '%s' '{\"volumes\":{\"fund-engine-one-click-files\":{\"name\":\"custom-task11-files\"}}}' ;;\n"
        "  *' ps --status running --services'*) printf 'postgres\\n' ;;\n"
        "  *' pg_dump -Fc '*) if [ \"${FAIL_DUMP:-0}\" = 1 ]; then exit 17; else printf 'custom-dump'; fi ;;\n"
        "  *' alpine sh -eu -c '*)\n"
        "    previous=''\n"
        '    for argument in "$@"; do\n'
        '      case "$argument" in *:/backup) host=${argument%:/backup}; tar -czf "$host/research-files.tar.gz" --files-from /dev/null ;; esac\n'
        "      previous=$argument\n"
        "    done ;;\n"
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
    assert any(
        ".one-click-backup." in line and line.endswith(":/backup>")
        for line in log.read_text().splitlines()
    )
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


def test_restore_preserves_recovery_artifacts_when_compensation_fails(
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
        "  *' config --format json'*) printf '%s' '{\"volumes\":{\"fund-engine-one-click-files\":{\"name\":\"fund-engine-one-click-files\"}}}' ;;\n"
        "  *' ps --status running --services'*) printf 'postgres\\n' ;;\n"
        "  *'run --rm -v fund-engine-one-click-files-restore-'*':/source:ro'*) exit 41 ;;\n"
        "  *'run --rm -v fund-engine-one-click-files-before-'*':/source:ro'*) exit 42 ;;\n"
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

    commands = log.read_text()
    assert completed.returncode != 0
    assert "automatic restore rollback failed; preserved" in completed.stderr
    assert "manual recovery required; keep application services stopped" in completed.stderr
    assert "docker volume inspect" in completed.stderr
    assert "volume rm" not in commands
    assert "dropdb" not in commands


def test_runtime_verifier_checks_increment_a_shell_api_and_revision() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()

    assert 'require_revision "$new_revision" 0065' in script
    assert 'FRONTEND_URL="${ONE_CLICK_FRONTEND_URL:-http://127.0.0.1:' in script
    assert 'API_URL="${ONE_CLICK_API_URL:-http://127.0.0.1:' in script
    assert '"$FRONTEND_URL/research"' in script
    assert "投资研究" in script
    assert '"$API_URL/api/underwriting/v1/product/objects?query=CATL"' in script
    assert "CATL object foundation is incomplete" in script
    assert 'printf \'Authorization: Bearer %s\\n\'' in script
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
