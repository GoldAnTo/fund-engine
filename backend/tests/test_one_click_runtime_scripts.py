import os
import shutil
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_runtime_control_script_keeps_credentials_local_and_switches_only_app_services() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()

    assert "umask 077" in script
    assert "openssl rand -hex 32" in script
    assert "RESEARCH_TENANT_TOKENS=" in script
    assert "local-one-click" in script
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse" in script
    assert 'LEGACY_PROJECT="fund-engine-event"' in script
    assert "label=com.docker.compose.project=" in script
    for service in ("api", "frontend", "research-worker", "acquisition-worker", "scheduler"):
        assert service in script
    assert "postgres" not in script[script.index("stop_legacy_application_services"): script.index("start_one_click_runtime")]
    assert "--scale acquisition-worker=3" in script
    assert " down" in script
    assert "--volumes" not in script


def test_rollback_restarts_only_legacy_application_containers() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()
    legacy_restore = script[
        script.index("restore_legacy_application_services") : script.index(
            "start_one_click_runtime"
        )
    ]
    rollback = script[
        script.index("restore_legacy_application_services") : script.index(
            "validate_research_archive"
        )
    ]

    assert "rollback_runtime" in rollback
    assert "stop_one_click_runtime" in rollback
    assert "docker start" in rollback
    assert "LEGACY_STOPPED_STATE_FILE" in rollback
    assert "docker ps" not in rollback
    allowed_services = script[script.index("legacy_service_is_allowed"): script.index("begin_legacy_stop_state")]
    for service in ("api", "frontend", "research-worker", "acquisition-worker", "scheduler"):
        assert service in allowed_services
    assert "postgres" not in legacy_restore
    assert "keycloak" not in legacy_restore


def test_up_builds_before_cutover_and_restores_only_recorded_containers_on_failure(tmp_path: Path) -> None:
    api_short = "89c5b6eb2322"
    api_full = api_short + "a" * 52
    frontend_short = "3d70c9b8e735"
    frontend_full = frontend_short + "b" * 52
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "docker-compose.one-click.yml").touch()
    (tmp_path / ".env").touch()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$1" in
  compose)
    [[ "$*" == *" config --format json"* ]] && {{ printf '%s' '{{"name":"test-project","volumes":{{"fund-engine-one-click-data":{{"name":"test-db"}}}}}}'; exit 0; }}
    [[ "$*" == *" config -q"* || "$*" == *" build"* ]] && exit 0
    [[ "$*" == *" create postgres"* ]] && exit 0
    [[ "$*" == *" up -d --no-build"* ]] && exit 1
    [[ "$*" == *" down"* ]] && {{ [[ "${{FAIL_DOWN:-0}}" == 1 ]] && exit 39 || exit 0; }}
    ;;
  volume)
    [[ "$2" == "inspect" && "${{!#}}" == "test-db" ]] && {{ printf '%s\n' 'test-db|test-project|fund-engine-one-click-data|||'; exit 0; }}
    ;;
  ps)
    [[ "$*" == *"service=api"* ]] && printf '{api_short}\\n'
    [[ "$*" == *"service=frontend"* ]] && printf '{frontend_short}\\n'
    exit 0
    ;;
  inspect)
    container_id="${{!#}}"
    case "$container_id" in
      {api_short}|{api_full}) canonical_id="{api_full}"; service="api" ;;
      {frontend_short}|{frontend_full}) canonical_id="{frontend_full}"; service="frontend" ;;
      *) exit 1 ;;
    esac
    [[ "$*" == *"{{{{.Id}}}}"* ]] && printf '%s\\n' "$canonical_id"
    [[ "$*" == *"com.docker.compose.project"* ]] && printf 'fund-engine-event\\n'
    [[ "$*" == *"com.docker.compose.service"* ]] && printf '%s\\n' "$service"
    [[ "$*" == *".State.Running"* ]] && printf 'false\\n'
    exit 0
    ;;
  stop|start) exit 0 ;;
esac
"""
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "docker.log"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "DOCKER_LOG": str(log)}

    completed = subprocess.run([script, "up"], capture_output=True, text=True, env=env)

    assert completed.returncode != 0
    commands = log.read_text().splitlines()
    config_index = next(index for index, command in enumerate(commands) if " config -q" in command)
    build_index = next(index for index, command in enumerate(commands) if command.endswith(" build"))
    first_stop_index = next(index for index, command in enumerate(commands) if command.startswith("stop "))
    up_index = next(index for index, command in enumerate(commands) if " up -d --no-build" in command)
    down_index = next(index for index, command in enumerate(commands) if command.endswith(" down"))
    first_start_index = next(
        index for index, command in enumerate(commands) if command.startswith("start ")
    )
    assert config_index < build_index < first_stop_index < up_index
    assert up_index < down_index < first_start_index
    assert {command for command in commands if command.startswith("stop ")} == {f"stop {api_full}", f"stop {frontend_full}"}
    assert {command for command in commands if command.startswith("start ")} == {f"start {api_full}", f"start {frontend_full}"}
    assert not (tmp_path / ".one-click-runtime" / "legacy-stopped-containers").exists()

    log.write_text("")
    blocked = subprocess.run(
        [script, "up"],
        capture_output=True,
        text=True,
        env={**env, "FAIL_DOWN": "1"},
    )
    assert blocked.returncode != 0
    assert "state is preserved for manual recovery" in blocked.stderr
    blocked_commands = log.read_text().splitlines()
    assert any(command.endswith(" down") for command in blocked_commands)
    assert not any(command.startswith("start ") for command in blocked_commands)
    assert (tmp_path / ".one-click-runtime" / "legacy-stopped-containers").exists()


def test_up_restores_every_prerecorded_container_when_stop_reports_failure(
    tmp_path: Path,
) -> None:
    api_short = "89c5b6eb2322"
    api_full = api_short + "a" * 52
    frontend_short = "3d70c9b8e735"
    frontend_full = frontend_short + "b" * 52
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "docker-compose.one-click.yml").touch()
    (tmp_path / ".env").touch()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$1" in
  compose)
    [[ "$*" == *" config --format json"* ]] && {{ printf '%s' '{{"name":"test-project","volumes":{{"fund-engine-one-click-data":{{"name":"test-db"}}}}}}'; exit 0; }}
    [[ "$*" == *" config -q"* || "$*" == *" build"* ]] && exit 0
    [[ "$*" == *" create postgres"* ]] && exit 0
    [[ "$*" == *" down"* ]] && exit 0
    ;;
  volume)
    [[ "$2" == "inspect" && "${{!#}}" == "test-db" ]] && {{ printf '%s\n' 'test-db|test-project|fund-engine-one-click-data|||'; exit 0; }}
    ;;
  ps)
    [[ "$*" == *"service=api"* ]] && printf '{api_short}\\n'
    [[ "$*" == *"service=frontend"* ]] && printf '{frontend_short}\\n'
    exit 0
    ;;
  inspect)
    container_id="${{!#}}"
    case "$container_id" in
      {api_short}|{api_full}) canonical_id="{api_full}"; service="api" ;;
      {frontend_short}|{frontend_full}) canonical_id="{frontend_full}"; service="frontend" ;;
      *) exit 1 ;;
    esac
    [[ "$*" == *"{{{{.Id}}}}"* ]] && printf '%s\\n' "$canonical_id"
    [[ "$*" == *"com.docker.compose.project"* ]] && printf 'fund-engine-event\\n'
    [[ "$*" == *"com.docker.compose.service"* ]] && printf '%s\\n' "$service"
    [[ "$*" == *".State.Running"* ]] && printf 'false\\n'
    exit 0
    ;;
  stop)
    [[ "$2" == "{api_full}" ]] && exit 37
    exit 0
    ;;
  start) exit 0 ;;
esac
"""
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "docker.log"

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
    commands = log.read_text().splitlines()
    first_stop = next(index for index, value in enumerate(commands) if value.startswith("stop "))
    api_recorded = next(
        index
        for index, value in enumerate(commands)
        if value == f"inspect --format {{{{.Id}}}} {api_short}"
    )
    frontend_recorded = next(
        index
        for index, value in enumerate(commands)
        if value == f"inspect --format {{{{.Id}}}} {frontend_short}"
    )
    assert api_recorded < first_stop
    assert frontend_recorded < first_stop
    assert {value for value in commands if value.startswith("start ")} == {
        f"start {api_full}",
        f"start {frontend_full}",
    }
    assert not (tmp_path / ".one-click-runtime" / "legacy-stopped-containers").exists()


def test_runtime_verifier_checks_new_stack_and_legacy_database_revision() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()

    assert "config -q" in script
    assert 'API_URL="${ONE_CLICK_API_URL:-http://127.0.0.1:' in script
    assert 'FRONTEND_URL="${ONE_CLICK_FRONTEND_URL:-http://127.0.0.1:' in script
    assert '"$API_URL/health"' in script
    assert '"$FRONTEND_URL/health"' in script
    for service in ("postgres", "api", "research-worker", "acquisition-worker", "frontend"):
        assert service in script
    assert "0065" in script
    assert "0062" in script
    assert "fund-engine-event" in script
    assert 'LEGACY_DATABASE_CONTAINER="fund-engine-event-postgres-1"' in script
    assert 'docker inspect --format \'{{.Id}}\' "$LEGACY_DATABASE_CONTAINER"' in script
    assert '"$legacy_name" == "/$LEGACY_DATABASE_CONTAINER"' in script
    assert '"$legacy_project" == "$LEGACY_PROJECT"' in script
    assert '"$legacy_service" == "$LEGACY_DATABASE_SERVICE"' in script
    assert "require_expected_healthy_replicas" in script
    assert 'require_expected_healthy_replicas research-worker 1' in script
    assert 'require_expected_healthy_replicas acquisition-worker 3' in script
    assert "compose ps --all --quiet" in script
    assert "mapfile" not in script
    for shell_script in ("one-click-runtime.sh", "verify-one-click-runtime.sh"):
        subprocess.run(["/bin/bash", "-n", ROOT / "scripts" / shell_script], check=True)


def test_readme_documents_the_local_one_click_runtime_without_secrets() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "## 一键本地运行（Docker）" in readme
    for command in (
        "scripts/one-click-runtime.sh init",
        "scripts/one-click-runtime.sh up",
        "scripts/one-click-runtime.sh status",
        "scripts/one-click-runtime.sh down",
        "scripts/one-click-runtime.sh rollback",
        "scripts/one-click-runtime.sh backup /absolute/path/to/new-backup",
        "scripts/one-click-runtime.sh restore /absolute/path/to/backup",
    ):
        assert command in readme
    assert "http://127.0.0.1:8080/events/new" in readme
    assert "http://127.0.0.1:8000" in readme
    assert "`.env`" in readme
    assert "不会打印密钥" in readme
    assert "旧版 PostgreSQL 和 Keycloak" in readme


def test_init_generates_private_local_credentials_without_echoing_them(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    created = subprocess.run(
        ["/bin/bash", script, "init"], check=True, capture_output=True, text=True
    )
    runtime_env = tmp_path / ".env.one-click.local"
    contents = runtime_env.read_text()

    assert created.stdout == "Created local one-click runtime environment.\n"
    assert stat.S_IMODE(runtime_env.stat().st_mode) == 0o600
    assert "RESEARCH_TENANT_TOKENS={\"" in contents
    assert "\":\"local-one-click\"}" in contents
    assert "ONE_CLICK_POSTGRES_PASSWORD=" in contents
    assert "RESEARCH_BEARER_TOKEN=" in contents
    assert "ONE_CLICK_POSTGRES_PASSWORD=" not in created.stdout
    assert "RESEARCH_BEARER_TOKEN=" not in created.stdout

    existing = subprocess.run([script, "init"], check=True, capture_output=True, text=True)
    assert existing.stdout == "One-click runtime environment already exists.\n"


def test_init_rejects_symlink_runtime_env_and_repairs_private_mode(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    target = tmp_path / "outside.env"
    target.write_text("ONE_CLICK_POSTGRES_PASSWORD=secret\n")
    runtime_env = tmp_path / ".env.one-click.local"
    runtime_env.symlink_to(target)

    rejected = subprocess.run([script, "init"], capture_output=True, text=True)

    assert rejected.returncode != 0
    assert "regular, owner-controlled file" in rejected.stderr
    assert target.read_text() == "ONE_CLICK_POSTGRES_PASSWORD=secret\n"

    runtime_env.unlink()
    runtime_env.write_text("ONE_CLICK_POSTGRES_PASSWORD=secret\n")
    runtime_env.chmod(0o644)
    repaired = subprocess.run([script, "init"], capture_output=True, text=True)

    assert repaired.returncode == 0
    assert stat.S_IMODE(runtime_env.stat().st_mode) == 0o600


def test_init_atomic_create_does_not_follow_symlink_inserted_during_generation(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    outside = tmp_path / "outside.env"
    outside.write_text("must remain unchanged\n")
    runtime_env = tmp_path / ".env.one-click.local"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    openssl = fake_bin / "openssl"
    openssl.write_text(
        "#!/bin/sh\n"
        'if [ ! -e "$RACE_MARKER" ]; then\n'
        '  : > "$RACE_MARKER"\n'
        '  ln -s "$RACE_TARGET" "$RUNTIME_ENV_PATH"\n'
        "fi\n"
        "printf '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\\n'\n"
    )
    openssl.chmod(openssl.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        ["/bin/bash", script, "init"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RACE_MARKER": str(tmp_path / "race-marker"),
            "RACE_TARGET": str(outside),
            "RUNTIME_ENV_PATH": str(runtime_env),
        },
    )

    assert completed.returncode != 0
    assert "atomically create runtime environment" in completed.stderr
    assert runtime_env.is_symlink()
    assert outside.read_text() == "must remain unchanged\n"


def test_init_upgrades_the_previous_default_gildata_adapter_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / ".env").touch()
    (tmp_path / "docker-compose.one-click.yml").touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '{\"services\":{\"acquisition-worker\":{\"environment\":{\"GILDATA_TOKEN\":\"%s\"}}}}' \"${GILDATA_TOKEN:-}\"\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    command_env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    runtime_env = tmp_path / ".env.one-click.local"
    runtime_env.write_text(
        "ONE_CLICK_POSTGRES_PASSWORD=do-not-print-me\n"
        'GILDATA_TOKEN=""\n'
        "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata\n"
    )

    completed = subprocess.run(
        [script, "init"],
        check=True,
        capture_output=True,
        text=True,
        env=command_env,
    )

    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse\n" in runtime_env.read_text()
    assert "gildata" not in runtime_env.read_text()
    assert "do-not-print-me" not in completed.stdout

    runtime_env.write_text(
        "ONE_CLICK_POSTGRES_PASSWORD=do-not-print-me\n"
        "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata\n"
    )
    subprocess.run(
        [script, "init"],
        check=True,
        capture_output=True,
        text=True,
        env={**command_env, "GILDATA_TOKEN": "explicitly-configured"},
    )
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata\n" in runtime_env.read_text()
