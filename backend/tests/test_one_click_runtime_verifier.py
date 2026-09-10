"""End-to-end boundary tests for the read-only one-click runtime verifier."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SERVICES = (
    "postgres", "api", "research-worker", "acquisition-worker",
    "company-research-worker", "api-proxy",
)
PROFILE_KEYS = (
    "ONE_CLICK_ACQUISITION_REPLICAS",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
)


def _write(path: Path, content: str, executable: bool = False) -> None:
    path.write_text(content)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def copied_verifier(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the real entry point into a complete, harmless Docker boundary."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    verifier = scripts / "verify-one-click-runtime.sh"
    shutil.copy2(ROOT / "scripts" / verifier.name, verifier)
    shutil.copy2(ROOT / "scripts" / "one_click_stability.py", scripts / "one_click_stability.py")
    versions = tmp_path / "backend" / "alembic" / "versions"
    versions.mkdir(parents=True)
    _write(versions / "base.py", "revision = '0070'\ndown_revision = None\n")
    _write(tmp_path / "docker-compose.one-click.yml", "name: test\n")
    _write(tmp_path / ".env", "DATABASE_POOL_SIZE=2\nDATABASE_MAX_OVERFLOW=2\n")
    runtime = tmp_path / ".env.one-click.local"
    _write(runtime, "ONE_CLICK_POSTGRES_USER=test_user\nONE_CLICK_POSTGRES_DB=test_db\nRESEARCH_BEARER_TOKEN=not-for-output\nONE_CLICK_ACQUISITION_REPLICAS=1\n")
    runtime.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "calls").touch()
    _write(bin_dir / "python3", f"#!/bin/sh\nexec {shutil.which('python3')} \"$@\"\n", True)
    _write(bin_dir / "mktemp", r'''#!/bin/sh
for template; do :; done
base=${template%.XXXXXX}
if [ "${HARNESS_MODE:-}" = malicious-mktemp ]; then /bin/mkdir -p "$base/nested"; echo "$base/nested"; exit 0; fi
if [ "${HARNESS_MODE:-}" = victim-mktemp ]; then echo "${base}ABC123"; exit 0; fi
path=$(/usr/bin/mktemp "$@") || exit $?
mode=$(python3 -c 'import os, stat, sys; print(format(stat.S_IMODE(os.stat(sys.argv[1]).st_mode), "o"))' "$path") || exit $?
echo "mktemp:$path:$mode" >> "$HARNESS_CALLS"
echo "$path"
''', True)
    _write(bin_dir / "rm", "#!/bin/sh\necho rm:$* >> \"$HARNESS_CALLS\"\nif [ \"${HARNESS_RM_FAIL:-}\" = 1 ]; then /bin/rm \"$@\"; exit 71; fi\nexec /bin/rm \"$@\"\n", True)
    _write(bin_dir / "sleep", "#!/bin/sh\n[ \"$#\" = 1 ] && { [ \"$1\" = 1 ] || [ \"$1\" = 5 ]; } || exit 97\necho sleep:$1 >> \"$HARNESS_CALLS\"\nexit 0\n", True)
    _write(bin_dir / "curl", r'''#!/usr/bin/env python3
import os, sys
calls = os.environ["HARNESS_CALLS"]
args = sys.argv[1:]
with open(calls, "a") as f: f.write("curl " + " ".join(args) + "\n")
prefix = ["--fail", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5"]
root_probe = ["--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5", "--output", "/dev/null", "--write-out", "%{http_code}", "http://127.0.0.1:8080/"]
if args == root_probe:
    print("200" if os.environ.get("HARNESS_MODE") == "proxy-page" else "404"); sys.exit(0)
if args[:7] != prefix or len(args) not in (8, 10) or (len(args) == 10 and args[7:9] != ["--header", "@-"]): sys.exit(97)
url = args[-1]
api = "http://127.0.0.1:8000"
api_proxy = "http://127.0.0.1:8080"
allowed = {f"{api}/health", f"{api_proxy}/health", f"{api_proxy}/", f"{api}/api/underwriting/v1/product/objects?query=CATL"}
product = f"{api}/api/underwriting/v1/product/objects?query=CATL"
if url not in allowed or not ((url == product and len(args) == 10 and args[7:9] == ["--header", "@-"]) or (url != product and len(args) == 8)): sys.exit(97)
if url == product and sys.stdin.read() != "Authorization: Bearer not-for-output\n": sys.exit(97)
counter = os.environ["HARNESS_ROOT"] + "/curl-count"
try: n = int(open(counter).read()) + 1
except OSError: n = 1
open(counter, "w").write(str(n))
if os.environ.get("HARNESS_MODE") == "http-second" and url.endswith("/health") and n >= 4:
    sys.exit(22)
if "product/objects" in url: print('{"items":[{"external_key":"CN:300750:COMPANY"},{"external_key":"SZSE:300750"}]}')
else: print("ok")
''', True)
    _write(bin_dir / "docker", r'''#!/usr/bin/env python3
import hashlib, json, os, shutil, sys
root, calls, mode = os.environ["HARNESS_ROOT"], os.environ["HARNESS_CALLS"], os.environ.get("HARNESS_MODE", "healthy")
args = sys.argv[1:]
with open(calls, "a") as f: f.write("docker " + " ".join(args) + "\n")
services = ["postgres", "api", "research-worker", "acquisition-worker", "company-research-worker", "api-proxy"]
replicas = int(os.environ.get("HARNESS_REPLICAS", "1"))
def ident(value): return hashlib.sha256(value.encode()).hexdigest()
def ids():
    result = []
    for service in services:
        for replica in range(replicas if service == "acquisition-worker" else 1):
            result.append(ident(service + str(replica)))
    return result
def id_services():
    result = {}
    for service in services:
        for replica in range(replicas if service == "acquisition-worker" else 1): result[ident(service + str(replica))] = service
    return result
def item(service, replica=0):
    poll = int(open(root + "/snapshot-count").read()) if os.path.exists(root + "/snapshot-count") else 0
    ident_value = ident(service + str(replica))
    if mode == "replacement-second" and poll > 1 and service == "api": ident_value = ident("replacement")
    return {"Id": ident_value, "Name": "/" + service, "Config":{"Labels":{"com.docker.compose.service":service,"com.docker.compose.project":"test"}}, "State":{"Status":"running", "Health":{"Status":"healthy"}, "OOMKilled": mode == "oom-first" and poll == 1 and service == "api"}, "RestartCount": 1 if mode == "restart-second" and poll > 1 and service == "api" else 0}
def mutate_private_directory():
    if os.environ.get("HARNESS_MUTATE_TEMP") != "1": return
    for line in open(calls):
        if line.startswith("mktemp:"):
            path = line.split(":", 2)[1]
            shutil.rmtree(path)
            os.symlink(root, path)
            return
if args and args[0] == "compose":
    command = args[1:]
    while len(command) >= 2 and command[0] in ("-f", "--env-file"):
        command = command[2:]
    if command == ["config", "-q"]: sys.exit(0)
    if command[:1] == ["ps"]:
        if command == ["ps", "--all", "--quiet", *services]:
            output = ids()
            if mode == "malformed-ps": output = ["not-a-container"]
            if mode == "too-many-ps": output = [ident(f"extra-{index}") for index in range(10)]
            print("\n".join(output)); sys.exit(23 if mode == "ps-partial" else 0)
        if command == ["ps", "--all", "--quiet"]:
            print("\n".join(ids() + [ident("migrate"), ident("file-store-init")])); sys.exit(0)
        if command == ["ps", "--all"]: print("diagnostic ps"); sys.exit(0)
    connection_query = "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
    revision_query = "SELECT version_num FROM alembic_version;"
    exact_exec = ["exec", "-T", "postgres", "psql", "-U", "test_user", "-d", "test_db", "-Atc"]
    if command == [*exact_exec, connection_query]:
        if mode == "connections": mutate_private_directory()
        print("999" if mode == "connections" else "3"); sys.exit(0)
    if command == [*exact_exec, revision_query]: print("0069" if mode == "revision-second" and os.path.exists(root + "/seen-revision") else os.environ.get("HARNESS_REVISION", "0070")); open(root + "/seen-revision", "w").write("1"); sys.exit(0)
    sys.exit(97)
if args and args[0] == "inspect":
    if "--format" in args:
        fmt, target = args[args.index("--format") + 1], args[-1]
        diagnostic = "Name={{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} OOM={{.State.OOMKilled}} restarts={{.RestartCount}}"
        if len(args) != 4: sys.exit(97)
        if fmt == diagnostic and target in id_services(): print("Name=/" + id_services()[target] + " state=running health=healthy OOM=False restarts=0"); sys.exit(0)
        legacy_id = ident("legacy")
        if fmt == "{{.Id}}" and target == "fund-engine-event-postgres-1":
            current_legacy = ident("legacy2") if mode == "legacy-replacement" and os.path.exists(root + "/legacy-seen") else legacy_id
            open(root + "/legacy-seen", "w").write("1"); open(root + "/legacy-current", "w").write(current_legacy); print(current_legacy); sys.exit(0)
        try: current_legacy = open(root + "/legacy-current").read()
        except OSError: current_legacy = legacy_id
        if target != current_legacy: sys.exit(97)
        if fmt == "{{.Name}}": print("/fund-engine-event-postgres-1")
        elif fmt == "{{ index .Config.Labels \"com.docker.compose.project\" }}": print("fund-engine-event")
        elif fmt == "{{ index .Config.Labels \"com.docker.compose.service\" }}": print("postgres")
        elif fmt == "{{.State.Running}}": print("true")
        else: sys.exit(97)
        sys.exit(0)
    if args[1:] != ids(): sys.exit(97)
    count_file = root + "/snapshot-count"
    n = int(open(count_file).read()) + 1 if os.path.exists(count_file) else 1
    open(count_file, "w").write(str(n))
    print(json.dumps([item(service, replica) for service in services for replica in range(replicas if service == "acquisition-worker" else 1)])); sys.exit(0)
if args and args[0] == "exec" and len(args) == 5 and args[2:] == ["sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"']:
    try: current_legacy = open(root + "/legacy-current").read()
    except OSError: current_legacy = ident("legacy")
    if args[1] != current_legacy: sys.exit(97)
    mutate_private_directory()
    print("0061" if mode == "legacy-revision" and os.path.exists(root + "/legacy-revision-seen") else "0062"); open(root + "/legacy-revision-seen", "w").write("1"); sys.exit(0)
if args and args[0] == "logs" and len(args) == 4 and args[1:3] == ["--tail", "40"] and args[3] in ids(): print("DIAGNOSTIC-SENTINEL"); sys.exit(0)
if args and args[0] == "stats" and args == ["stats", "--no-stream", *ids()]: print("DIAGNOSTIC-SENTINEL"); sys.exit(0)
sys.exit(97)
''', True)
    return verifier, bin_dir


def harness_environment(tmp_path: Path, bin_dir: Path, mode: str = "healthy", **values: str) -> dict[str, str]:
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HARNESS_ROOT": str(tmp_path),
        "HARNESS_CALLS": str(tmp_path / "calls"),
        "HARNESS_MODE": mode,
    } | values


def run_verifier(tmp_path: Path, *arguments: str, mode: str = "healthy", **env_values: str) -> subprocess.CompletedProcess[str]:
    verifier, bin_dir = copied_verifier(tmp_path)
    if mode == "victim-mktemp":
        victim = tmp_path / ".verify-one-click-runtime.ABC123"
        victim.mkdir()
        (victim / "sentinel").touch()
    environment = harness_environment(tmp_path, bin_dir, mode, **env_values)
    result = subprocess.run(["/bin/bash", str(verifier), *arguments], cwd=tmp_path, text=True, capture_output=True, env=environment)
    assert_no_lifecycle_commands((tmp_path / "calls").read_text())
    return result


def assert_complete_poll_counts(calls: str, polls: int) -> None:
    lines = calls.splitlines()
    assert sum(line.endswith("http://127.0.0.1:8000/health") for line in lines) == polls
    assert sum(line.endswith("http://127.0.0.1:8080/health") for line in lines) == polls
    assert sum(line.endswith("http://127.0.0.1:8080/") for line in lines) == polls
    assert sum("product/objects?query=CATL" in line for line in lines) == polls
    assert sum("pg_stat_activity" in line for line in lines) == polls
    assert sum("docker compose" in line and "version_num" in line for line in lines) == polls
    assert sum(line == "docker inspect --format {{.Id}} fund-engine-event-postgres-1" for line in lines) == polls
    for fmt in ("{{.Name}}", '{{ index .Config.Labels "com.docker.compose.project" }}', '{{ index .Config.Labels "com.docker.compose.service" }}', "{{.State.Running}}"):
        assert sum(line.startswith(f"docker inspect --format {fmt} ") for line in lines) == polls
    assert sum(line.startswith("docker exec ") and "version_num" in line for line in lines) == polls
    assert sum(line.startswith("docker inspect ") and " --format " not in line for line in lines) == polls


def assert_no_lifecycle_commands(calls: str) -> None:
    forbidden = {"up", "down", "stop", "start", "restart", "rm", "kill", "pause", "unpause", "create", "run", "scale"}
    for line in calls.splitlines():
        parts = line.split()
        if not parts or parts[0] != "docker":
            continue
        command = parts[1:]
        if command[:1] == ["compose"]:
            command = command[1:]
            while len(command) >= 2 and command[0] in {"-f", "--env-file"}:
                command = command[2:]
        assert not command or command[0] not in forbidden


def expected_ids() -> list[str]:
    return [sha256(f"{service}0".encode()).hexdigest() for service in SERVICES]


@pytest.mark.parametrize("argument", ["--stability-seconds", "--stability-seconds=-1", "--stability-seconds=01", "--stability-seconds=999999999999999999999"])
def test_invalid_duration_is_rejected_before_external_setup(tmp_path: Path, argument: str) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run(["/bin/bash", str(verifier), *argument.split()], cwd=tmp_path, text=True, capture_output=True, env=harness_environment(tmp_path, bin_dir))
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert not (tmp_path / "calls").read_text()


def test_equals_duration_form_is_rejected_before_external_setup(tmp_path: Path) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(verifier), "--stability-seconds=1"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=harness_environment(tmp_path, bin_dir),
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert not (tmp_path / "calls").read_text()


def test_stability_duration_polls_without_diagnostics(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "snapshot-count").read_text() == "2"
    assert_complete_poll_counts(calls, 2)
    assert calls.count("sleep:1") == 1
    assert calls.count("curl ") == 8
    assert "logs --tail 40" not in calls and "stats --no-stream" not in calls
    assert_no_lifecycle_commands(calls)


@pytest.mark.parametrize("mode, message", [("oom-first", "OOMKilled"), ("restart-second", "restart count changed"), ("replacement-second", "container identity changed"), ("connections", "connection count"), ("revision-second", "0070"), ("legacy-revision", "0062"), ("legacy-replacement", "identity")])
def test_failures_are_safe_and_collect_bounded_diagnostics(tmp_path: Path, mode: str, message: str) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1", mode=mode)
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert "logs --tail 40" in calls and "stats --no-stream" in calls
    assert calls.count("docker logs --tail 40") == 6
    assert calls.count("docker stats --no-stream") == 1
    expected = expected_ids()
    formatted = [line.rsplit(" ", 1)[1] for line in calls.splitlines() if line.startswith("docker inspect --format Name=")]
    logs = [line.rsplit(" ", 1)[1] for line in calls.splitlines() if line.startswith("docker logs --tail 40 ")]
    stats = [line.split()[3:] for line in calls.splitlines() if line.startswith("docker stats --no-stream ")]
    assert Counter(formatted) == Counter(expected)
    assert Counter(logs) == Counter(expected)
    assert stats == [expected]
    assert "not-for-output" not in result.stderr
    assert ".env.one-click.local" not in result.stderr
    assert '"Config"' not in result.stderr
    assert_no_lifecycle_commands(calls)


def test_transient_second_poll_http_failure_is_diagnosed(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1", mode="http-second")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert "health endpoint check failed" in result.stderr
    assert "logs --tail 40" in calls and "stats --no-stream" in calls


def test_exported_replica_count_controls_the_exact_snapshot_expectation(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="healthy", ONE_CLICK_ACQUISITION_REPLICAS="2", HARNESS_REPLICAS="2")
    assert result.returncode == 0, result.stderr


def test_harness_uses_only_explicit_environment_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = {
        "ONE_CLICK_API_URL": "http://hostile.invalid",
        "ONE_CLICK_API_PROXY_URL": "http://hostile.invalid",
        "ONE_CLICK_API_PORT": "9999",
        "ONE_CLICK_API_PROXY_PORT": "9998",
        "ONE_CLICK_POSTGRES_USER": "hostile",
        "ONE_CLICK_POSTGRES_DB": "hostile",
        "RESEARCH_BEARER_TOKEN": "hostile",
        "ONE_CLICK_ACQUISITION_REPLICAS": "4",
        "DATABASE_POOL_SIZE": "10",
        "DATABASE_MAX_OVERFLOW": "10",
        "BASH_ENV": "/hostile/bash-env",
        "CDPATH": "/hostile/cdpath",
        "ENV": "/hostile/env",
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)
    result = run_verifier(tmp_path)
    assert result.returncode == 0, result.stderr


def test_explicit_profile_environment_values_still_override_fixture(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, ONE_CLICK_ACQUISITION_REPLICAS="2", HARNESS_REPLICAS="2")
    assert result.returncode == 0, result.stderr


def test_verifier_filters_one_shot_compose_containers_and_allows_scale_four(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, ONE_CLICK_ACQUISITION_REPLICAS="4", HARNESS_REPLICAS="4")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "ps --all --quiet postgres api research-worker acquisition-worker company-research-worker api-proxy" in calls


@pytest.mark.parametrize("value", ["", "01", "5", "999999999999999999999"])
def test_invalid_exported_profile_value_fails_bounded_validation(tmp_path: Path, value: str) -> None:
    result = run_verifier(tmp_path, ONE_CLICK_ACQUISITION_REPLICAS=value)
    assert result.returncode != 0
    assert "ONE_CLICK_ACQUISITION_REPLICAS must be an integer from 1 through 4" in result.stderr


def test_zero_duration_runs_one_complete_poll(tmp_path: Path) -> None:
    result = run_verifier(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "snapshot-count").read_text() == "1"
    assert "sleep:" not in (tmp_path / "calls").read_text()
    calls = (tmp_path / "calls").read_text()
    assert_complete_poll_counts(calls, 1)
    assert "mktemp:" in calls and calls.split("mktemp:")[1].splitlines()[0].endswith(":700")
    assert not list(tmp_path.glob(".verify-one-click-runtime.*"))


@pytest.mark.parametrize("database_revision, succeeds", [("future-head", True), ("0070", False)])
def test_verifier_compares_database_with_repository_head(tmp_path: Path, database_revision: str, succeeds: bool) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    _write(tmp_path / "backend/alembic/versions/new.py", "revision: str = 'future-head'\ndown_revision: str = '0070'\n")
    result = subprocess.run(
        ["/bin/bash", str(verifier)], cwd=tmp_path, text=True, capture_output=True,
        env=harness_environment(tmp_path, bin_dir, HARNESS_REVISION=database_revision),
    )
    assert (result.returncode == 0) is succeeds, result.stderr
    assert "future-head" in (result.stdout if succeeds else result.stderr)
    assert_no_lifecycle_commands((tmp_path / "calls").read_text())


def test_verifier_rejects_multiple_repository_heads_before_docker(tmp_path: Path) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    _write(tmp_path / "backend/alembic/versions/branch.py", "revision = 'branch'\ndown_revision = None\n")
    result = subprocess.run(
        ["/bin/bash", str(verifier)], cwd=tmp_path, text=True, capture_output=True,
        env=harness_environment(tmp_path, bin_dir),
    )
    assert result.returncode != 0
    assert "single Alembic head" in result.stderr
    assert not (tmp_path / "calls").read_text()


def test_failure_cleans_private_directory_and_keeps_diagnostics_if_rm_fails(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="connections", HARNESS_RM_FAIL="1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 1
    assert "connection count exceeds" in result.stderr
    assert "logs --tail 40" in calls and "stats --no-stream" in calls
    assert not list(tmp_path.glob(".verify-one-click-runtime.*"))


def test_cleanup_revalidation_refuses_a_mutated_temp_directory_after_success(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, HARNESS_MUTATE_TEMP="1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 1
    assert "private verification directory cleanup failed" in result.stderr
    assert "rm:" not in calls


def test_cleanup_revalidation_preserves_operational_failure_and_diagnoses(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="connections", HARNESS_MUTATE_TEMP="1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 1
    assert "connection count exceeds" in result.stderr
    assert "cleanup validation failed" in result.stderr
    assert "logs --tail 40" in calls and "rm:" not in calls


def test_malicious_mktemp_path_is_rejected_without_rm(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="malicious-mktemp")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert "private verification directory validation failed" in result.stderr
    assert "rm:" not in calls


def test_preexisting_direct_child_mktemp_victim_is_rejected_without_rm(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="victim-mktemp")
    calls = (tmp_path / "calls").read_text()
    victim = tmp_path / ".verify-one-click-runtime.ABC123"
    assert result.returncode != 0
    assert "private verification directory validation failed" in result.stderr
    assert (victim / "sentinel").exists()
    assert "rm:" not in calls


def test_checked_compose_ps_failure_cannot_be_treated_as_a_snapshot(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="ps-partial")
    assert result.returncode != 0
    assert "unable to list one-click containers" in result.stderr
    assert "logs --tail 40" in (tmp_path / "calls").read_text()


@pytest.mark.parametrize("mode", ["malformed-ps", "too-many-ps"])
def test_invalid_compose_container_ids_are_never_inspected_or_diagnosed(tmp_path: Path, mode: str) -> None:
    result = run_verifier(tmp_path, mode=mode)
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert "one-click container list is invalid" in result.stderr
    assert "not-a-container" not in calls
    assert not any(line.startswith("docker inspect ") and "--format" not in line for line in calls.splitlines())
    if mode == "malformed-ps":
        assert "docker logs" not in calls and "docker stats" not in calls
    else:
        assert calls.count("docker logs") <= 9


def test_harness_rejects_an_unknown_docker_command(tmp_path: Path) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run([str(bin_dir / "docker"), "definitely-unknown"], text=True, capture_output=True, env=harness_environment(tmp_path, bin_dir))
    assert result.returncode == 97


@pytest.mark.parametrize(("program", "arguments"), [("curl", ("unexpected",)), ("sleep", ("2",))])
def test_harness_rejects_unknown_curl_and_sleep_shapes(tmp_path: Path, program: str, arguments: tuple[str, ...]) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run([str(bin_dir / program), *arguments], text=True, capture_output=True, env=harness_environment(tmp_path, bin_dir))
    assert result.returncode == 97


@pytest.mark.parametrize(
    ("arguments",),
    [
        (("--fail", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5", "http://127.0.0.1:8000/api/underwriting/v1/product/objects?query=CATL"),),
        (("--fail", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5", "--header", "@-", "http://127.0.0.1:8000/health"),),
    ],
)
def test_harness_rejects_wrong_curl_header_shape(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run([str(bin_dir / "curl"), *arguments], text=True, capture_output=True, env=harness_environment(tmp_path, bin_dir))
    assert result.returncode == 97


def test_harness_rejects_wrong_legacy_logs_and_stats_container_ids(tmp_path: Path) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    environment = harness_environment(tmp_path, bin_dir)
    wrong = "f" * 64
    commands = [
        ("exec", wrong, "sh", "-c", "SELECT version_num FROM alembic_version;"),
        ("logs", "--tail", "40", wrong),
        ("stats", "--no-stream", *(wrong for _ in SERVICES)),
    ]
    for command in commands:
        result = subprocess.run([str(bin_dir / "docker"), *command], text=True, capture_output=True, env=environment)
        assert result.returncode == 97


def test_harness_allows_only_exact_read_only_compose_exec_argv_and_queries(tmp_path: Path) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    environment = harness_environment(tmp_path, bin_dir)
    prefix = ("compose", "-f", "compose.yml", "--env-file", "base", "--env-file", "runtime", "exec", "-T", "postgres")
    cases = [
        (*prefix, "definitely-unknown", "pg_stat_activity"),
        (*prefix, "psql", "-U", "test_user", "-d", "test_db", "-Atc", "SELECT version_num FROM alembic_version; DROP TABLE x;"),
    ]
    for command in cases:
        result = subprocess.run([str(bin_dir / "docker"), *command], text=True, capture_output=True, env=environment)
        assert result.returncode == 97


def test_harness_product_curl_requires_exact_stdin_header(tmp_path: Path) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    environment = harness_environment(tmp_path, bin_dir)
    command = [str(bin_dir / "curl"), "--fail", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5", "--header", "@-", "http://127.0.0.1:8000/api/underwriting/v1/product/objects?query=CATL"]
    for payload, expected in (("", 97), ("Authorization: Bearer wrong\n", 97), ("Authorization: Bearer not-for-output\n", 0)):
        result = subprocess.run(command, input=payload, text=True, capture_output=True, env=environment)
        assert result.returncode == expected


def test_long_duration_has_exact_poll_and_sleep_counts(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "600")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "snapshot-count").read_text() == "121"
    assert calls.count("sleep:5") == 120
    assert_complete_poll_counts(calls, 121)
    assert_no_lifecycle_commands(calls)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("ONE_CLICK_ACQUISITION_REPLICAS", "5", "1 through 4"),
        ("ONE_CLICK_ACQUISITION_REPLICAS", "01", "1 through 4"),
        ("DATABASE_POOL_SIZE", "0", "1 through 10"),
        ("DATABASE_POOL_SIZE", "11", "1 through 10"),
        ("DATABASE_MAX_OVERFLOW", "-1", "0 through 10"),
        ("DATABASE_MAX_OVERFLOW", "11", "0 through 10"),
    ],
)
def test_invalid_exported_profile_values_fail_bounded_validation(tmp_path: Path, key: str, value: str, message: str) -> None:
    result = run_verifier(tmp_path, **{key: value})
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("ONE_CLICK_ACQUISITION_REPLICAS", "5", "1 through 4"),
        ("ONE_CLICK_ACQUISITION_REPLICAS", "01", "1 through 4"),
        ("DATABASE_POOL_SIZE", "0", "1 through 10"),
        ("DATABASE_POOL_SIZE", "11", "1 through 10"),
        ("DATABASE_POOL_SIZE", "999999999999999999999", "1 through 10"),
        ("DATABASE_MAX_OVERFLOW", "-1", "0 through 10"),
        ("DATABASE_MAX_OVERFLOW", "11", "0 through 10"),
        ("DATABASE_MAX_OVERFLOW", "999999999999999999999", "0 through 10"),
    ],
)
def test_invalid_runtime_file_profile_values_fail_bounded_validation(tmp_path: Path, key: str, value: str, message: str) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    runtime = tmp_path / ".env.one-click.local"
    original = runtime.read_text()
    runtime.write_text(
        original.replace(f"{key}=1\n", f"{key}={value}\n")
        if key in original
        else original + f"{key}={value}\n"
    )
    environment = harness_environment(tmp_path, bin_dir)
    result = subprocess.run(["/bin/bash", str(verifier)], cwd=tmp_path, text=True, capture_output=True, env=environment)
    assert result.returncode != 0
    assert message in result.stderr


def test_source_locks_safe_sustained_verification_contract() -> None:
    source = (ROOT / "scripts" / "verify-one-click-runtime.sh").read_text()
    assert "--stability-seconds" in source
    assert "one_click_stability.py" in source
    assert "pg_stat_activity" in source
    assert "logs --tail 40" in source and "stats --no-stream" in source
    assert "--connect-timeout 2 --max-time 5" in source
    assert "acquisition-worker=${ACQUISITION_REPLICAS}" in source
    assert "acquisition-worker 3" not in source
    assert "trap" in source and "rm -rf" in source


def test_verifier_rejects_proxy_serving_a_page(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="proxy-page")
    assert result.returncode != 0
    assert "API proxy must not serve pages" in result.stderr
