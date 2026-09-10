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
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata" in script
    assert 'LEGACY_PROJECT="fund-engine-event"' in script
    assert "label=com.docker.compose.project=" in script
    for service in ("api", "research-worker", "acquisition-worker", "scheduler"):
        assert service in script
    assert "postgres" not in script[script.index("stop_legacy_application_services"): script.index("start_one_click_runtime")]
    assert "--scale acquisition-worker=3" in script
    assert " down" in script
    assert "--volumes" not in script


def test_rollback_restarts_only_legacy_application_containers() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()
    rollback = script[script.index("restore_legacy_application_services"): script.index("usage()")]

    assert "rollback_runtime" in rollback
    assert "stop_one_click_runtime" in rollback
    assert "docker start" in rollback
    assert "LEGACY_STOPPED_STATE_FILE" in rollback
    assert "docker ps" not in rollback
    allowed_services = script[script.index("legacy_service_is_allowed"): script.index("begin_legacy_stop_state")]
    for service in ("api", "research-worker", "acquisition-worker", "scheduler"):
        assert service in allowed_services
    assert "postgres" not in rollback
    assert "keycloak" not in rollback


def test_up_builds_before_cutover_and_restores_only_recorded_containers_on_failure(tmp_path: Path) -> None:
    api_short = "89c5b6eb2322"
    api_full = api_short + "a" * 52
    acquisition_short = "3d70c9b8e735"
    acquisition_full = acquisition_short + "b" * 52
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
    [[ "$*" == *" config -q"* || "$*" == *" build"* ]] && exit 0
    [[ "$*" == *" up -d --no-build"* ]] && exit 1
    ;;
  ps)
    [[ "$*" == *"service=api"* ]] && printf '{api_short}\\n'
    [[ "$*" == *"service=acquisition-worker"* ]] && printf '{acquisition_short}\\n'
    exit 0
    ;;
  inspect)
    container_id="${{!#}}"
    case "$container_id" in
      {api_short}|{api_full}) canonical_id="{api_full}"; service="api" ;;
      {acquisition_short}|{acquisition_full}) canonical_id="{acquisition_full}"; service="acquisition-worker" ;;
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
    assert config_index < build_index < first_stop_index < up_index
    assert {command for command in commands if command.startswith("stop ")} == {f"stop {api_full}", f"stop {acquisition_full}"}
    assert {command for command in commands if command.startswith("start ")} == {f"start {api_full}", f"start {acquisition_full}"}
    assert not (tmp_path / ".one-click-runtime" / "legacy-stopped-containers").exists()


def test_runtime_verifier_checks_new_stack_and_legacy_database_revision() -> None:
    script = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()

    assert "config -q" in script
    assert "http://127.0.0.1:8000/health" in script
    for service in ("postgres", "api", "research-worker", "acquisition-worker"):
        assert service in script
    assert "0060" in script
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


def test_readme_documents_the_local_one_click_runtime_without_secrets() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "## 一键本地运行（Docker）" in readme
    for command in (
        "scripts/one-click-runtime.sh init",
        "scripts/one-click-runtime.sh up",
        "scripts/one-click-runtime.sh status",
        "scripts/one-click-runtime.sh down",
        "scripts/one-click-runtime.sh rollback",
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

    created = subprocess.run([script, "init"], check=True, capture_output=True, text=True)
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
