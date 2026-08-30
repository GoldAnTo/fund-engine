"""End-to-end boundary tests for the read-only one-click runtime verifier."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SERVICES = (
    "postgres", "api", "research-worker", "acquisition-worker",
    "company-research-worker", "frontend",
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
    _write(tmp_path / "docker-compose.one-click.yml", "name: test\n")
    _write(tmp_path / ".env", "DATABASE_POOL_SIZE=2\nDATABASE_MAX_OVERFLOW=2\n")
    runtime = tmp_path / ".env.one-click.local"
    _write(runtime, "ONE_CLICK_POSTGRES_USER=test_user\nONE_CLICK_POSTGRES_DB=test_db\nRESEARCH_BEARER_TOKEN=not-for-output\nONE_CLICK_ACQUISITION_REPLICAS=1\n")
    runtime.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "calls").touch()
    _write(bin_dir / "python3", f"#!/bin/sh\nexec {shutil.which('python3')} \"$@\"\n", True)
    _write(bin_dir / "mktemp", "#!/bin/sh\nif [ \"${HARNESS_MODE:-}\" = malicious-mktemp ]; then base=${1%.XXXXXX}; /bin/mkdir -p \"$base\"; /bin/mkdir -p \"$base/nested\"; echo \"$base/nested\"; exit 0; fi\npath=$(/usr/bin/mktemp \"$@\") || exit $?\necho \"mktemp:$path:$(stat -f %Lp \"$path\")\" >> \"$HARNESS_CALLS\"\necho \"$path\"\n", True)
    _write(bin_dir / "rm", "#!/bin/sh\necho rm:$* >> \"$HARNESS_CALLS\"\nif [ \"${HARNESS_RM_FAIL:-}\" = 1 ]; then /bin/rm \"$@\"; exit 71; fi\nexec /bin/rm \"$@\"\n", True)
    _write(bin_dir / "sleep", "#!/bin/sh\n[ \"$#\" = 1 ] && { [ \"$1\" = 1 ] || [ \"$1\" = 5 ]; } || exit 96\necho sleep:$1 >> \"$HARNESS_CALLS\"\nexit 0\n", True)
    _write(bin_dir / "curl", r'''#!/usr/bin/env python3
import os, sys
calls = os.environ["HARNESS_CALLS"]
args = sys.argv[1:]
with open(calls, "a") as f: f.write("curl " + " ".join(args) + "\n")
prefix = ["--fail", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "5"]
if args[:7] != prefix or len(args) not in (8, 10) or (len(args) == 10 and args[7:9] != ["--header", "@-"]): sys.exit(96)
url = args[-1]
counter = os.environ["HARNESS_ROOT"] + "/curl-count"
try: n = int(open(counter).read()) + 1
except OSError: n = 1
open(counter, "w").write(str(n))
if os.environ.get("HARNESS_MODE") == "http-second" and url.endswith("/health") and n >= 4:
    sys.exit(22)
if url.endswith("/research"): print("投资研究")
elif "product/objects" in url: print('{"items":[{"external_key":"CN:300750:COMPANY"},{"external_key":"SZSE:300750"}]}')
else: print("ok")
''', True)
    _write(bin_dir / "docker", r'''#!/usr/bin/env python3
import hashlib, json, os, sys
root, calls, mode = os.environ["HARNESS_ROOT"], os.environ["HARNESS_CALLS"], os.environ.get("HARNESS_MODE", "healthy")
args = sys.argv[1:]
with open(calls, "a") as f: f.write("docker " + " ".join(args) + "\n")
services = ["postgres", "api", "research-worker", "acquisition-worker", "company-research-worker", "frontend"]
replicas = int(os.environ.get("HARNESS_REPLICAS", "1"))
def ident(value): return hashlib.sha256(value.encode()).hexdigest()
def ids():
    result = []
    for service in services:
        for replica in range(replicas if service == "acquisition-worker" else 1):
            result.append(ident(service + str(replica)))
    return result
def item(service, replica=0):
    poll = int(open(root + "/snapshot-count").read()) if os.path.exists(root + "/snapshot-count") else 0
    ident_value = ident(service + str(replica))
    if mode == "replacement-second" and poll > 1 and service == "api": ident_value = ident("replacement")
    return {"Id": ident_value, "Name": "/" + service, "Config":{"Labels":{"com.docker.compose.service":service,"com.docker.compose.project":"test"}}, "State":{"Status":"running", "Health":{"Status":"healthy"}, "OOMKilled": mode == "oom-first" and poll == 1 and service == "api"}, "RestartCount": 1 if mode == "restart-second" and poll > 1 and service == "api" else 0}
if args and args[0] == "compose":
    command = args[1:]
    while len(command) >= 2 and command[0] in ("-f", "--env-file"):
        command = command[2:]
    if command == ["config", "-q"]: sys.exit(0)
    if command[:1] == ["ps"]:
        if command == ["ps", "--all", "--quiet"]:
            print("\n".join(ids())); sys.exit(23 if mode == "ps-partial" else 0)
        if command == ["ps", "--all"]: print("diagnostic ps"); sys.exit(0)
    if command[:3] == ["exec", "-T", "postgres"]:
        query = " ".join(command)
        if "pg_stat_activity" in query: print("999" if mode == "connections" else "3"); sys.exit(0)
        if "version_num" in query: print("0069" if mode == "revision-second" and os.path.exists(root + "/seen-revision") else "0070"); open(root + "/seen-revision", "w").write("1"); sys.exit(0)
    sys.exit(97)
if args and args[0] == "inspect":
    if "--format" in args:
        fmt, target = args[args.index("--format") + 1], args[-1]
        allowed_formats = {"{{.Id}}", "{{.Name}}", "{{ index .Config.Labels \"com.docker.compose.project\" }}", "{{ index .Config.Labels \"com.docker.compose.service\" }}", "{{.State.Running}}", "Name={{.Name}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} OOM={{.State.OOMKilled}} restarts={{.RestartCount}}"}
        if len(args) != 4 or fmt not in allowed_formats: sys.exit(97)
        legacy = target == "fund-engine-event-postgres-1" or target == ident("legacy")
        if ".Id" in fmt: print(ident("legacy2") if mode == "legacy-replacement" and os.path.exists(root + "/legacy-seen") else ident("legacy")); open(root + "/legacy-seen", "w").write("1")
        elif ".Name" in fmt: print("/fund-engine-event-postgres-1")
        elif "compose.project" in fmt: print("fund-engine-event")
        elif "compose.service" in fmt: print("postgres")
        elif ".State.Running" in fmt: print("true")
        else: print("healthy")
        sys.exit(0)
    if not all(len(value) == 64 for value in args[1:]): sys.exit(97)
    count_file = root + "/snapshot-count"
    n = int(open(count_file).read()) + 1 if os.path.exists(count_file) else 1
    open(count_file, "w").write(str(n))
    print(json.dumps([item(service, replica) for service in services for replica in range(replicas if service == "acquisition-worker" else 1)])); sys.exit(0)
if args and args[0] == "exec" and len(args) == 5 and len(args[1]) == 64 and args[2:] == ["sh", "-c", args[4]] and "version_num" in args[4]: print("0061" if mode == "legacy-revision" and os.path.exists(root + "/legacy-revision-seen") else "0062"); open(root + "/legacy-revision-seen", "w").write("1"); sys.exit(0)
if args and args[0] == "logs" and len(args) == 4 and args[1:3] == ["--tail", "40"] and len(args[3]) == 64: print("DIAGNOSTIC-SENTINEL"); sys.exit(0)
if args and args[0] == "stats" and len(args) == 8 and args[1] == "--no-stream" and all(len(value) == 64 for value in args[2:]): print("DIAGNOSTIC-SENTINEL"); sys.exit(0)
sys.exit(97)
''', True)
    return verifier, bin_dir


def run_verifier(tmp_path: Path, *arguments: str, mode: str = "healthy", **env_values: str) -> subprocess.CompletedProcess[str]:
    verifier, bin_dir = copied_verifier(tmp_path)
    environment = {key: value for key, value in os.environ.items() if key not in PROFILE_KEYS}
    environment |= {"PATH": f"{bin_dir}:/usr/bin:/bin", "HARNESS_ROOT": str(tmp_path), "HARNESS_CALLS": str(tmp_path / "calls"), "HARNESS_MODE": mode} | env_values
    return subprocess.run(["/bin/bash", str(verifier), *arguments], cwd=tmp_path, text=True, capture_output=True, env=environment)


@pytest.mark.parametrize("argument", ["--stability-seconds", "--stability-seconds=-1", "--stability-seconds=01", "--stability-seconds=999999999999999999999"])
def test_invalid_duration_is_rejected_before_external_setup(tmp_path: Path, argument: str) -> None:
    verifier, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run(["/bin/bash", str(verifier), *argument.split()], cwd=tmp_path, text=True, capture_output=True, env={"PATH": "/usr/bin:/bin", "HARNESS_ROOT": str(tmp_path), "HARNESS_CALLS": str(tmp_path / "calls")})
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert not (tmp_path / "calls").read_text()


def test_equals_duration_form_is_rejected_before_external_setup(tmp_path: Path) -> None:
    verifier, _ = copied_verifier(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(verifier), "--stability-seconds=1"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert not (tmp_path / "calls").read_text()


def test_stability_duration_polls_without_diagnostics(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "snapshot-count").read_text() == "2"
    assert calls.count("pg_stat_activity") == 2
    assert calls.count("version_num") == 4
    assert calls.count("sleep:1") == 1
    assert calls.count("curl ") == 8
    assert "logs --tail 40" not in calls and "stats --no-stream" not in calls


@pytest.mark.parametrize("mode, message", [("oom-first", "OOMKilled"), ("restart-second", "restart count changed"), ("replacement-second", "container identity changed"), ("connections", "connection count"), ("revision-second", "0070"), ("legacy-revision", "0062"), ("legacy-replacement", "identity")])
def test_failures_are_safe_and_collect_bounded_diagnostics(tmp_path: Path, mode: str, message: str) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1", mode=mode)
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert message.lower() in result.stderr.lower()
    assert "logs --tail 40" in calls and "stats --no-stream" in calls
    assert calls.count("docker logs --tail 40") == 6
    assert calls.count("docker stats --no-stream") == 1
    assert "not-for-output" not in result.stderr
    assert ".env.one-click.local" not in result.stderr
    assert '"Config"' not in result.stderr
    for forbidden in (" compose up", " compose down", " compose stop", "docker stop", "docker start", "docker restart", "docker rm", "docker kill"):
        assert forbidden not in calls


def test_transient_second_poll_http_failure_is_diagnosed(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "1", mode="http-second")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert "health endpoint check failed" in result.stderr
    assert "logs --tail 40" in calls and "stats --no-stream" in calls


def test_exported_replica_count_controls_the_exact_snapshot_expectation(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="healthy", ONE_CLICK_ACQUISITION_REPLICAS="2", HARNESS_REPLICAS="2")
    assert result.returncode == 0, result.stderr


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
    assert "mktemp:" in calls and calls.split("mktemp:")[1].splitlines()[0].endswith(":700")
    assert not list(tmp_path.glob(".verify-one-click-runtime.*"))


def test_failure_cleans_private_directory_and_keeps_diagnostics_if_rm_fails(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="connections", HARNESS_RM_FAIL="1")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 1
    assert "connection count exceeds" in result.stderr
    assert "logs --tail 40" in calls and "stats --no-stream" in calls
    assert not list(tmp_path.glob(".verify-one-click-runtime.*"))


def test_malicious_mktemp_path_is_rejected_without_rm(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="malicious-mktemp")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode != 0
    assert "private verification directory validation failed" in result.stderr
    assert "rm:" not in calls


def test_checked_compose_ps_failure_cannot_be_treated_as_a_snapshot(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, mode="ps-partial")
    assert result.returncode != 0
    assert "unable to list one-click containers" in result.stderr
    assert "logs --tail 40" in (tmp_path / "calls").read_text()


def test_harness_rejects_an_unknown_docker_command(tmp_path: Path) -> None:
    _, bin_dir = copied_verifier(tmp_path)
    result = subprocess.run([str(bin_dir / "docker"), "definitely-unknown"], text=True, capture_output=True, env=os.environ | {"HARNESS_ROOT": str(tmp_path), "HARNESS_CALLS": str(tmp_path / "calls")})
    assert result.returncode == 97


def test_long_duration_has_exact_poll_and_sleep_counts(tmp_path: Path) -> None:
    result = run_verifier(tmp_path, "--stability-seconds", "600")
    calls = (tmp_path / "calls").read_text()
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "snapshot-count").read_text() == "121"
    assert calls.count("sleep:5") == 120
    assert calls.count("pg_stat_activity") == 121
    assert calls.count("version_num") == 242
    assert calls.count("curl ") == 484


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
    environment = {name: current for name, current in os.environ.items() if name not in PROFILE_KEYS}
    environment |= {"PATH": f"{bin_dir}:/usr/bin:/bin", "HARNESS_ROOT": str(tmp_path), "HARNESS_CALLS": str(tmp_path / "calls"), "HARNESS_MODE": "healthy"}
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
