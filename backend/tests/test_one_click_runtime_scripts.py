import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]

PROFILE_KEYS = (
    "ONE_CLICK_ACQUISITION_REPLICAS",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT_SECONDS",
    "DATABASE_POOL_RECYCLE_SECONDS",
    "ONE_CLICK_POSTGRES_MEMORY_LIMIT",
    "ONE_CLICK_API_MEMORY_LIMIT",
    "ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT",
    "ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT",
    "ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT",
    "ONE_CLICK_FRONTEND_MEMORY_LIMIT",
    "ONE_CLICK_POSTGRES_CPU_LIMIT",
    "ONE_CLICK_API_CPU_LIMIT",
    "ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT",
    "ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT",
    "ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT",
    "ONE_CLICK_FRONTEND_CPU_LIMIT",
)


def sanitized_process_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in PROFILE_KEYS}


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
    assert '--scale "acquisition-worker=${acquisition_replicas}"' in script
    assert " down" in script
    assert "--volumes" not in script


def test_runtime_stops_existing_writers_before_schema_migration() -> None:
    script = (ROOT / "scripts" / "one-click-runtime.sh").read_text()
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    start = script[
        script.index("start_one_click_runtime"):script.index(
            "stop_one_click_runtime"
        )
    ]

    stop = start.index(
        "compose stop api research-worker acquisition-worker "
        "company-research-worker frontend"
    )
    launch = start.index("compose up -d --no-build")
    assert stop < launch
    assert "alembic upgrade head" in compose
    assert "python -m app.scripts.verify_company_research_schema" in compose
    assert compose.index("alembic upgrade head") < compose.index(
        "python -m app.scripts.verify_company_research_schema"
    )


def test_rendered_compose_services_match_the_one_click_stop_command() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker compose is not installed")
    rendered = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(ROOT / ".env.one-click.example"),
            "-f",
            str(ROOT / "docker-compose.one-click.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    assert "scheduler" not in services
    assert {
        "api",
        "research-worker",
        "acquisition-worker",
        "company-research-worker",
        "frontend",
    }.issubset(services)
    migrate_command = services["migrate"]["command"]
    if isinstance(migrate_command, list):
        migrate_command = " ".join(migrate_command)
    assert "alembic upgrade head" in migrate_command
    assert "python -m app.scripts.verify_company_research_schema" in migrate_command


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


def run_fake_up(
    tmp_path: Path,
    *,
    docker_memory: int | str,
    runtime_values: dict[str, str] | None = None,
    process_values: dict[str, str] | None = None,
    fail_up: bool = False,
    fail_down: bool = False,
    fail_docker_memory_query: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    api_short = "89c5b6eb2322"
    api_full = api_short + "a" * 52
    frontend_short = "3d70c9b8e735"
    frontend_full = frontend_short + "b" * 52
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "one-click-runtime.sh"
    shutil.copy(ROOT / "scripts" / "one-click-runtime.sh", script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "docker-compose.one-click.yml").touch()
    (tmp_path / ".env").touch()
    runtime_env = tmp_path / ".env.one-click.local"
    runtime_env.write_text(
        "ONE_CLICK_POSTGRES_PASSWORD=test-password\n"
        "RESEARCH_BEARER_TOKEN=test-token\n"
        + "".join(f"{key}={value}\n" for key, value in (runtime_values or {}).items())
    )
    runtime_env.chmod(0o600)

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
    [[ "$*" == *" stop api research-worker acquisition-worker company-research-worker frontend"* ]] && exit 0
    [[ "$*" == *" up -d --no-build"* ]] && {{ [[ "${{FAIL_UP:-0}}" == 1 ]] && exit 1 || exit 0; }}
    [[ "$*" == *" down"* ]] && {{ [[ "${{FAIL_DOWN:-0}}" == 1 ]] && exit 39 || exit 0; }}
    ;;
  info)
    [[ "$*" == *" --format {{{{.MemTotal}}}}"* ]] && {{ [[ "${{FAIL_DOCKER_MEMORY_QUERY:-0}}" == 1 ]] && exit 41 || {{ printf '%s\n' "$DOCKER_MEMORY"; exit 0; }}; }}
    exit 0
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
    env = {
        **sanitized_process_environment(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "DOCKER_MEMORY": str(docker_memory),
        "FAIL_UP": "1" if fail_up else "0",
        "FAIL_DOWN": "1" if fail_down else "0",
        "FAIL_DOCKER_MEMORY_QUERY": "1" if fail_docker_memory_query else "0",
        **(process_values or {}),
    }

    completed = subprocess.run([script, "up"], capture_output=True, text=True, env=env)
    return completed, log.read_text().splitlines() if log.exists() else []


def test_up_builds_before_cutover_and_restores_only_recorded_containers_on_failure(tmp_path: Path) -> None:
    api_full = "89c5b6eb2322" + "a" * 52
    frontend_full = "3d70c9b8e735" + "b" * 52
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        fail_up=True,
    )

    assert completed.returncode != 0
    config_index = next(index for index, command in enumerate(commands) if " config -q" in command)
    migrate_build_index = next(
        index for index, command in enumerate(commands) if command.endswith(" build migrate")
    )
    frontend_build_index = next(
        index for index, command in enumerate(commands) if command.endswith(" build frontend")
    )
    first_stop_index = next(index for index, command in enumerate(commands) if command.startswith("stop "))
    up_index = next(index for index, command in enumerate(commands) if " up -d --no-build" in command)
    down_index = next(index for index, command in enumerate(commands) if command.endswith(" down"))
    first_start_index = next(
        index for index, command in enumerate(commands) if command.startswith("start ")
    )
    assert config_index < migrate_build_index < frontend_build_index < first_stop_index < up_index
    assert commands[up_index].endswith("--scale acquisition-worker=1")
    assert up_index < down_index < first_start_index
    assert {command for command in commands if command.startswith("stop ")} == {f"stop {api_full}", f"stop {frontend_full}"}
    assert {command for command in commands if command.startswith("start ")} == {f"start {api_full}", f"start {frontend_full}"}
    assert not (tmp_path / ".one-click-runtime" / "legacy-stopped-containers").exists()

    blocked, blocked_commands = run_fake_up(
        tmp_path / "blocked",
        docker_memory=8 * 1024**3,
        fail_up=True,
        fail_down=True,
    )
    assert blocked.returncode != 0
    assert "state is preserved for manual recovery" in blocked.stderr
    assert any(command.endswith(" down") for command in blocked_commands)
    assert not any(command.startswith("start ") for command in blocked_commands)
    assert (tmp_path / "blocked" / ".one-click-runtime" / "legacy-stopped-containers").exists()


def test_up_uses_runtime_configured_acquisition_replica_count(tmp_path: Path) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": "4"},
    )

    assert completed.returncode == 0
    launch = next(command for command in commands if " up -d --no-build" in command)
    assert launch.endswith("--scale acquisition-worker=4")


def test_up_prefers_exported_acquisition_replica_count_over_runtime_file(tmp_path: Path) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": "1"},
        process_values={"ONE_CLICK_ACQUISITION_REPLICAS": "4"},
    )

    assert completed.returncode == 0
    launch = next(command for command in commands if " up -d --no-build" in command)
    assert launch.endswith("--scale acquisition-worker=4")


@pytest.mark.parametrize(
    ("name", "value", "expected_error"),
    (
        ("DATABASE_POOL_SIZE", "999", "DATABASE_POOL_SIZE must be an integer from 1 through 10"),
        (
            "ONE_CLICK_ACQUISITION_REPLICAS",
            "",
            "ONE_CLICK_ACQUISITION_REPLICAS must be an integer from 1 through 4",
        ),
        (
            "ONE_CLICK_API_MEMORY_LIMIT",
            "0m",
            "ONE_CLICK_API_MEMORY_LIMIT has an invalid one-click resource limit",
        ),
        (
            "ONE_CLICK_FRONTEND_CPU_LIMIT",
            "0",
            "ONE_CLICK_FRONTEND_CPU_LIMIT has an invalid one-click resource limit",
        ),
    ),
)
def test_up_validates_exported_profile_values_before_docker(
    tmp_path: Path, name: str, value: str, expected_error: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        process_values={name: value},
    )

    assert completed.returncode != 0
    assert completed.stderr == f"one-click runtime: {expected_error}\n"
    assert commands == []


@pytest.mark.parametrize("value", ("0", "5", "-1", "01", "many"))
def test_up_rejects_invalid_acquisition_replica_count_before_build_or_cutover(
    tmp_path: Path, value: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": value},
    )

    assert completed.returncode != 0
    assert "ONE_CLICK_ACQUISITION_REPLICAS must be an integer from 1 through 4" in completed.stderr
    assert commands == []


@pytest.mark.parametrize("value", ("18446744073709551617", "9" * 100))
def test_up_rejects_oversized_acquisition_replica_count_before_docker(
    tmp_path: Path, value: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={"ONE_CLICK_ACQUISITION_REPLICAS": value},
    )

    assert completed.returncode != 0
    assert completed.stderr == (
        "one-click runtime: ONE_CLICK_ACQUISITION_REPLICAS must be an integer "
        "from 1 through 4\n"
    )
    assert commands == []


@pytest.mark.parametrize(
    ("name", "minimum", "maximum"),
    (
        ("DATABASE_POOL_SIZE", 1, 10),
        ("DATABASE_MAX_OVERFLOW", 0, 10),
        ("DATABASE_POOL_TIMEOUT_SECONDS", 1, 120),
        ("DATABASE_POOL_RECYCLE_SECONDS", 30, 3600),
    ),
)
def test_up_rejects_oversized_database_pool_settings_before_docker(
    tmp_path: Path, name: str, minimum: int, maximum: int
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={name: "18446744073709551617"},
    )

    assert completed.returncode != 0
    assert completed.stderr == (
        f"one-click runtime: {name} must be an integer from {minimum} through {maximum}\n"
    )
    assert commands == []


@pytest.mark.parametrize(
    ("runtime_values", "expected_error"),
    (
        (
            {
                "ACQUISITION_ENABLED_ADAPTERS": "sse,szse,gildata",
                "ONE_CLICK_ACQUISITION_REPLICAS": "0",
            },
            "ONE_CLICK_ACQUISITION_REPLICAS must be an integer from 1 through 4",
        ),
        (
            {
                "ACQUISITION_ENABLED_ADAPTERS": "sse,szse,gildata",
                "DATABASE_POOL_SIZE": "0",
            },
            "DATABASE_POOL_SIZE must be an integer from 1 through 10",
        ),
        (
            {
                "ACQUISITION_ENABLED_ADAPTERS": "sse,szse,gildata",
                "ONE_CLICK_POSTGRES_MEMORY_LIMIT": "unbounded",
            },
            "ONE_CLICK_POSTGRES_MEMORY_LIMIT has an invalid one-click resource limit",
        ),
    ),
)
def test_up_validates_profile_before_upgrading_legacy_adapter_defaults(
    tmp_path: Path, runtime_values: dict[str, str], expected_error: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values=runtime_values,
    )

    assert completed.returncode != 0
    assert completed.stderr == f"one-click runtime: {expected_error}\n"
    assert commands == []


def test_up_rejects_insufficient_docker_memory_before_build_or_cutover(tmp_path: Path) -> None:
    completed, commands = run_fake_up(tmp_path, docker_memory=6 * 1024**3 - 1)

    assert completed.returncode != 0
    assert "Docker must expose at least 6 GiB" in completed.stderr
    assert not any(" build" in command or command.startswith("stop ") for command in commands)


def test_up_reports_unreadable_docker_memory_before_build_or_cutover(tmp_path: Path) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        fail_docker_memory_query=True,
    )

    assert completed.returncode != 0
    assert completed.stderr == "one-click runtime: unable to read Docker memory\n"
    assert not any(" build" in command or command.startswith("stop ") for command in commands)


@pytest.mark.parametrize("docker_memory", ("0", "01", "not-a-number"))
def test_up_rejects_invalid_docker_memory_total_before_build_or_cutover(
    tmp_path: Path, docker_memory: str
) -> None:
    completed, commands = run_fake_up(tmp_path, docker_memory=docker_memory)

    assert completed.returncode != 0
    assert completed.stderr == "one-click runtime: Docker reported an invalid memory total\n"
    assert not any(" build" in command or command.startswith("stop ") for command in commands)


@pytest.mark.parametrize("docker_memory", (6 * 1024**3, 8 * 1024**3 - 1))
def test_up_warns_when_docker_memory_is_between_six_and_eight_gib(
    tmp_path: Path, docker_memory: int
) -> None:
    completed, _ = run_fake_up(tmp_path, docker_memory=docker_memory)

    assert completed.returncode == 0
    assert "less than 8 GiB" in completed.stderr


def test_up_does_not_warn_when_docker_memory_is_at_least_eight_gib(tmp_path: Path) -> None:
    completed, _ = run_fake_up(tmp_path, docker_memory=8 * 1024**3)

    assert completed.returncode == 0
    assert "less than 8 GiB" not in completed.stderr


def test_up_accepts_an_oversized_canonical_docker_memory_total(tmp_path: Path) -> None:
    completed, _ = run_fake_up(
        tmp_path,
        docker_memory="18446744073709551617",
    )

    assert completed.returncode == 0
    assert "less than 8 GiB" not in completed.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("ONE_CLICK_POSTGRES_MEMORY_LIMIT", "unbounded"),
        ("ONE_CLICK_POSTGRES_MEMORY_LIMIT", "0m"),
        ("ONE_CLICK_POSTGRES_CPU_LIMIT", "all"),
        ("ONE_CLICK_POSTGRES_CPU_LIMIT", "0"),
        ("ONE_CLICK_POSTGRES_CPU_LIMIT", "0.0"),
        ("ONE_CLICK_API_MEMORY_LIMIT", "unbounded"),
        ("ONE_CLICK_FRONTEND_MEMORY_LIMIT", "0m"),
        ("ONE_CLICK_API_CPU_LIMIT", "all"),
        ("ONE_CLICK_FRONTEND_CPU_LIMIT", "0"),
        ("ONE_CLICK_FRONTEND_CPU_LIMIT", "0.0"),
    ),
)
def test_up_rejects_invalid_resource_limits_before_build_or_cutover(
    tmp_path: Path, name: str, value: str
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=8 * 1024**3,
        runtime_values={name: value},
    )

    assert completed.returncode != 0
    assert f"{name} has an invalid one-click resource limit" in completed.stderr
    assert not any(" build" in command or command.startswith("stop ") for command in commands)


def test_up_does_not_upgrade_legacy_adapter_when_docker_memory_is_insufficient(
    tmp_path: Path,
) -> None:
    completed, commands = run_fake_up(
        tmp_path,
        docker_memory=6 * 1024**3 - 1,
        runtime_values={"ACQUISITION_ENABLED_ADAPTERS": "sse,szse,gildata"},
    )

    assert completed.returncode != 0
    assert "Docker must expose at least 6 GiB" in completed.stderr
    assert (tmp_path / ".env.one-click.local").read_text() == (
        "ONE_CLICK_POSTGRES_PASSWORD=test-password\n"
        "RESEARCH_BEARER_TOKEN=test-token\n"
        "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata\n"
    )
    assert not any(" build" in command or command.startswith("stop ") for command in commands)


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
      info)
        [[ "$*" == *" --format {{{{.MemTotal}}}}"* ]] && {{ printf '%s\n' 8589934592; exit 0; }}
        exit 0
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
            **sanitized_process_environment(),
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
    for service in (
        "postgres",
        "api",
        "research-worker",
        "acquisition-worker",
        "company-research-worker",
        "frontend",
    ):
        assert service in script
    assert 'require_revision "$new_revision" 0070' in script
    assert "0062" in script
    assert "fund-engine-event" in script
    assert 'LEGACY_DATABASE_CONTAINER="fund-engine-event-postgres-1"' in script
    assert 'docker inspect --format \'{{.Id}}\' "$LEGACY_DATABASE_CONTAINER"' in script
    assert '"$legacy_name" == "/$LEGACY_DATABASE_CONTAINER"' in script
    assert '"$legacy_project" == "$LEGACY_PROJECT"' in script
    assert '"$legacy_service" == "$LEGACY_DATABASE_SERVICE"' in script
    assert "one_click_stability.py" in script
    assert '--expect "acquisition-worker=${ACQUISITION_REPLICAS}"' in script
    assert "acquisition-worker 3" not in script
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
    for text in (
        "ONE_CLICK_ACQUISITION_REPLICAS=1",
        "ONE_CLICK_ACQUISITION_REPLICAS=2",
        "默认只启动一个资料采集 worker",
        "该变量默认值为 1",
        "提高吞吐时请改为 2–4",
        "Docker Desktop 分配约 8 GiB",
        "Docker Desktop 至少分配 6 GiB",
        "停止旧服务前",
        "允许值为 1–4",
        "scripts/verify-one-click-runtime.sh --stability-seconds 600",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_POOL_RECYCLE_SECONDS",
        ".env.one-click.example",
        "资源上限",
    ):
        assert text in readme
    default_description = readme.index("该变量默认值为 1")
    default_example = readme.index("ONE_CLICK_ACQUISITION_REPLICAS=1")
    scaling_description = readme.index("提高吞吐时请改为 2–4")
    scaling_example = readme.index("ONE_CLICK_ACQUISITION_REPLICAS=2")
    assert (
        default_description < default_example < scaling_description < scaling_example
    )


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
