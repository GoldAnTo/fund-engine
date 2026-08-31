from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).parents[2]
FRONTEND = ROOT / "frontend"
PASS_LINE = (
    "PASS: default frontend completed live Alphabet company research through "
    "reviewed evidence, frozen revision replay, and verified Markdown export"
)
SENSITIVE_OUTPUT_MARKERS = (
    "ALL_PROXY",
    "Authorization",
    "BASH_ENV",
    "Bearer",
    "DATABASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "PYTHONPATH",
    "RESEARCH_BEARER_TOKEN",
    "RESEARCH_TENANT_TOKENS",
    "VITE_RESEARCH_CLIENT",
    "company-research.sqlite",
    "live-company-research-verifier-token",
)
SENSITIVE_HOST_ENV_KEYS = (
    "ALL_PROXY",
    "DATABASE_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "RESEARCH_BEARER_TOKEN",
    "RESEARCH_TENANT_TOKENS",
)
RUN_LIVE_COMPANY_RESEARCH = os.environ.get("RUN_LIVE_COMPANY_RESEARCH") == "1"
VERIFIER_INTERNAL_TIMEOUT_SECONDS = 180
# The verifier owns a 180-second workflow bound. The outer process owner leaves
# another 120 seconds for sequential browser, API, worker, Vite, and authenticated
# private-runtime cleanup before it escalates against only the npm session group.
OUTER_CLEANUP_MARGIN_SECONDS = 120
OUTER_TIMEOUT_SECONDS = (
    VERIFIER_INTERNAL_TIMEOUT_SECONDS + OUTER_CLEANUP_MARGIN_SECONDS
)
# The verifier's SIGTERM handler owns exact detached-browser-group cleanup. Keep
# a dedicated npm-group supervisor alive and unreaped through that bounded work.
TERM_GRACE_SECONDS = 70
KILL_GRACE_SECONDS = 10
OWNED_PROCESS_GROUP_SUPERVISOR = Path(__file__).with_name(
    "owned_process_group_supervisor.py"
)


class OwnedProcessGroupTimeout(TimeoutError):
    def __init__(self, process_group_id: int) -> None:
        super().__init__("live verifier exceeded its outer timeout")
        self.process_group_id = process_group_id


def run_owned_process_group(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    term_grace_seconds: float,
    kill_grace_seconds: float,
    kill_group: Callable[[int, signal.Signals], None] = os.killpg,
) -> subprocess.CompletedProcess[str]:
    supervisor_command = [
        sys.executable,
        str(OWNED_PROCESS_GROUP_SUPERVISOR),
        *command,
    ]
    process = subprocess.Popen(
        supervisor_command,
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    process_group_id = process.pid
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        pass

    try:
        kill_group(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Do not communicate, poll, or otherwise reap the supervisor during this
    # grace period: its live session-leader PID is the exact group capability.
    time.sleep(term_grace_seconds)
    try:
        kill_group(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=kill_grace_seconds)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            "exact live verifier process group did not exit after SIGKILL"
        ) from error
    raise OwnedProcessGroupTimeout(process_group_id)


def _assert_no_sensitive_output(
    result: subprocess.CompletedProcess[str], sensitive_values: Sequence[str]
) -> None:
    combined_output = result.stdout + result.stderr
    if any(marker in combined_output for marker in SENSITIVE_OUTPUT_MARKERS):
        raise AssertionError("live verifier emitted sensitive output")
    if any(value and value in combined_output for value in sensitive_values):
        raise AssertionError("live verifier emitted sensitive output")


def safe_failure_message(
    result: subprocess.CompletedProcess[str], *, sensitive_values: Sequence[str]
) -> str:
    _assert_no_sensitive_output(result, sensitive_values)
    diagnostic = result.stdout + result.stderr
    if "owned browser launch failed" in diagnostic:
        return (
            "live company research verifier could not launch its browser; install "
            "Playwright Chromium or select an installed Chrome channel"
        )
    if "ERR_MODULE_NOT_FOUND" in diagnostic or "Cannot find package" in diagnostic:
        return (
            "live company research verifier is missing frontend dependencies; "
            "run npm ci in frontend"
        )
    if "Node " in diagnostic and "required" in diagnostic:
        return (
            "live company research verifier requires the Node major pinned in .nvmrc; "
            "run nvm install and nvm use"
        )
    if any(
        phrase in diagnostic
        for phrase in (
            "configured PYTHON is outside",
            "repository backend Python",
            "trusted Python dependency probe failed",
        )
    ):
        return (
            "live company research verifier requires backend/.venv with backend[dev] "
            "dependencies installed"
        )
    return (
        "live company research workflow verification failed; run the documented public "
        "command locally for the verifier's bounded diagnostic"
    )


def assert_pinned_node_major(
    result: subprocess.CompletedProcess[str], *, expected_major: int
) -> None:
    _assert_no_sensitive_output(result, ())
    if (
        result.returncode != 0
        or result.stdout != f"{expected_major}\n"
        or result.stderr != ""
    ):
        raise AssertionError(
            f"live company research acceptance requires Node major {expected_major} "
            "selected through scripts/with-project-node.mjs"
        )


def test_live_company_research_marker_is_registered() -> None:
    config = tomllib.loads(
        (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert (
        "live_company_research: requires the repository backend venv, frontend "
        "dependencies, and an installed Playwright browser"
        in config["tool"]["pytest"]["ini_options"]["markers"]
    )


def test_backend_ci_provisions_the_opt_in_live_company_research_acceptance() -> None:
    workflow = (ROOT / ".github" / "workflows" / "backend.yml").read_text(
        encoding="utf-8"
    )
    for watched_path in ("frontend/**", ".nvmrc", "README.md"):
        assert workflow.count(f'      - "{watched_path}"') == 2
    recall_job_header = workflow.split("  recall-eval:\n", maxsplit=1)[1].split(
        "    steps:\n", maxsplit=1
    )[0]
    assert "\n    paths:\n" not in recall_job_header
    for required_step in (
        "company-research-live:",
        "python -m venv backend/.venv",
        'node-version-file: ".nvmrc"',
        "npm ci",
        "npx playwright install --with-deps chromium",
        'RUN_LIVE_COMPANY_RESEARCH: "1"',
        "backend/.venv/bin/python -m pytest backend/tests/test_verify_live_company_research_ui.py -q",
    ):
        assert required_step in workflow


def test_owned_process_group_timeout_stops_term_ignoring_descendant(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "child.pid"
    child_program = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    parent_program = (
        "import pathlib,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"child=subprocess.Popen([sys.executable, '-c', {child_program!r}]); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    sent_signals: list[tuple[int, signal.Signals]] = []

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        sent_signals.append((process_group_id, sent_signal))
        os.killpg(process_group_id, sent_signal)

    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [sys.executable, "-c", parent_program],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.5,
            term_grace_seconds=0.2,
            kill_grace_seconds=1.0,
            kill_group=kill_group,
        )

    process_group_id = captured.value.process_group_id
    assert sent_signals == [
        (process_group_id, signal.SIGTERM),
        (process_group_id, signal.SIGKILL),
    ]
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while True:
        state = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(child_pid)],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            break
        if time.monotonic() >= deadline:
            raise AssertionError(f"owned descendant {child_pid} remained running")
        time.sleep(0.05)


def _process_state(process_id: int) -> str:
    return subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(process_id)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_owned_process_group_normal_exit_preserves_child_status_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNED_HOST_ONLY", "must-not-leak")
    result = run_owned_process_group(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; print(os.environ['OWNED_ALLOWED']); "
                "print(os.environ.get('OWNED_HOST_ONLY', 'absent')); "
                "print('owned stderr', file=sys.stderr); sys.exit(7)"
            ),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "OWNED_ALLOWED": "owned stdout"},
        timeout_seconds=2,
        term_grace_seconds=0.1,
        kill_grace_seconds=1,
    )

    assert result.returncode == 7
    assert result.stdout == "owned stdout\nabsent\n"
    assert result.stderr == "owned stderr\n"


def test_owned_process_group_timeout_diagnostic_contains_no_child_output(
    tmp_path: Path,
) -> None:
    sensitive_child_output = "forbidden-timeout-child-output"
    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [
                sys.executable,
                "-c",
                f"import time; print({sensitive_child_output!r}, flush=True); time.sleep(60)",
            ],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

    assert str(captured.value) == "live verifier exceeded its outer timeout"
    assert sensitive_child_output not in str(captured.value)


def test_owned_process_group_anchor_remains_live_until_every_timeout_signal(
    tmp_path: Path,
) -> None:
    for iteration in range(30):
        states_before_signal: list[str] = []
        sent_signals: list[tuple[int, signal.Signals]] = []

        def kill_group(
            process_group_id: int,
            sent_signal: signal.Signals,
            states: list[str] = states_before_signal,
            signals: list[tuple[int, signal.Signals]] = sent_signals,
        ) -> None:
            states.append(_process_state(process_group_id))
            signals.append((process_group_id, sent_signal))
            os.killpg(process_group_id, sent_signal)

        with pytest.raises(OwnedProcessGroupTimeout) as captured:
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"], "ITERATION": str(iteration)},
                timeout_seconds=0.03,
                term_grace_seconds=0.01,
                kill_grace_seconds=1,
                kill_group=kill_group,
            )

        assert sent_signals == [
            (captured.value.process_group_id, signal.SIGTERM),
            (captured.value.process_group_id, signal.SIGKILL),
        ]
        assert len(states_before_signal) == 2
        assert all(state and not state.startswith("Z") for state in states_before_signal)


def test_outer_timeout_kills_same_group_survivor_after_leader_exits(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "same-group-child.pid"
    child_program = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    parent_program = (
        "import pathlib,signal,subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_program!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143))); "
        "time.sleep(60)"
    )
    sent_signals: list[tuple[int, signal.Signals]] = []
    anchor_states_before_signal: list[str] = []

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        anchor_states_before_signal.append(_process_state(process_group_id))
        sent_signals.append((process_group_id, sent_signal))
        os.killpg(process_group_id, sent_signal)

    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [sys.executable, "-c", parent_program],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.5,
            term_grace_seconds=0.5,
            kill_grace_seconds=1.0,
            kill_group=kill_group,
        )
    process_group_id = captured.value.process_group_id
    assert sent_signals == [
        (process_group_id, signal.SIGTERM),
        (process_group_id, signal.SIGKILL),
    ]
    assert len(anchor_states_before_signal) == 2
    assert all(
        state and not state.startswith("Z") for state in anchor_states_before_signal
    )
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    state = _process_state(child_pid)
    assert not state or state.startswith("Z")


def test_outer_timeout_allows_cooperative_cleanup_of_detached_browser_tree(
    tmp_path: Path,
) -> None:
    browser_pid_path = tmp_path / "browser.pid"
    renderer_pid_path = tmp_path / "renderer.pid"
    signal_path = tmp_path / "signals"
    program_path = tmp_path / "cooperative_verifier.py"
    renderer_program = (
        "import os,pathlib,signal,time; "
        f"pathlib.Path({str(renderer_pid_path)!r}).write_text(str(os.getpid())); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    browser_program = (
        "import os,pathlib,signal,subprocess,sys,time; "
        f"pathlib.Path({str(browser_pid_path)!r}).write_text(str(os.getpid())); "
        f"subprocess.Popen([sys.executable, '-c', {renderer_program!r}]); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    program_path.write_text(
        "\n".join(
            (
                "import os, pathlib, signal, subprocess, sys, time",
                f"browser_pid_path = pathlib.Path({str(browser_pid_path)!r})",
                f"renderer_pid_path = pathlib.Path({str(renderer_pid_path)!r})",
                f"signal_path = pathlib.Path({str(signal_path)!r})",
                f"browser_program = {browser_program!r}",
                "browser = subprocess.Popen([sys.executable, '-c', browser_program], start_new_session=True)",
                "browser_pgid = browser.pid",
                "def stop(_signal, _frame):",
                "    with signal_path.open('a') as stream: stream.write(f'{browser_pgid}:SIGTERM\\n')",
                "    os.killpg(browser_pgid, signal.SIGTERM)",
                "    time.sleep(0.1)",
                "    with signal_path.open('a') as stream: stream.write(f'{browser_pgid}:SIGKILL\\n')",
                "    os.killpg(browser_pgid, signal.SIGKILL)",
                "    browser.wait(timeout=2)",
                "    os._exit(143)",
                "signal.signal(signal.SIGTERM, stop)",
                "while not browser_pid_path.exists() or not renderer_pid_path.exists(): time.sleep(0.01)",
                "time.sleep(60)",
            )
        ),
        encoding="utf-8",
    )
    outer_signals: list[tuple[int, signal.Signals]] = []
    anchor_states_before_signal: list[str] = []

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        anchor_states_before_signal.append(_process_state(process_group_id))
        outer_signals.append((process_group_id, sent_signal))
        os.killpg(process_group_id, sent_signal)

    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [sys.executable, str(program_path)],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.5,
            term_grace_seconds=0.5,
            kill_grace_seconds=1.0,
            kill_group=kill_group,
        )

    browser_pid = int(browser_pid_path.read_text(encoding="utf-8"))
    renderer_pid = int(renderer_pid_path.read_text(encoding="utf-8"))
    assert outer_signals == [
        (captured.value.process_group_id, signal.SIGTERM),
        (captured.value.process_group_id, signal.SIGKILL),
    ]
    assert len(anchor_states_before_signal) == 2
    assert all(
        state and not state.startswith("Z") for state in anchor_states_before_signal
    )
    assert signal_path.read_text(encoding="utf-8").splitlines() == [
        f"{browser_pid}:SIGTERM",
        f"{browser_pid}:SIGKILL",
    ]
    for pid in (browser_pid, renderer_pid):
        state = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert not state or state.startswith("Z")


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    (
        (
            "owned browser launch failed at /private/raw/path",
            (
                "live company research verifier could not launch its browser; install "
                "Playwright Chromium or select an installed Chrome channel"
            ),
        ),
        (
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find package at /private/raw/path",
            (
                "live company research verifier is missing frontend dependencies; "
                "run npm ci in frontend"
            ),
        ),
        (
            "Node 24+ is required; install it under /private/raw/path",
            (
                "live company research verifier requires the Node major pinned in .nvmrc; "
                "run nvm install and nvm use"
            ),
        ),
        (
            "repository backend Python is not a validated executable: /private/raw/path",
            (
                "live company research verifier requires backend/.venv with backend[dev] "
                "dependencies installed"
            ),
        ),
    ),
)
def test_failure_diagnostic_classifies_prerequisites_without_raw_output(
    diagnostic: str, expected: str
) -> None:
    result = subprocess.CompletedProcess(
        args=["npm"],
        returncode=1,
        stdout="",
        stderr=diagnostic,
    )

    message = safe_failure_message(result, sensitive_values=())

    assert message == expected
    assert "/private/raw/path" not in message


def test_failure_diagnostic_rejects_sensitive_values_before_classification() -> None:
    result = subprocess.CompletedProcess(
        args=["npm"], returncode=1, stdout="", stderr="secret-value-from-host"
    )

    with pytest.raises(AssertionError, match="emitted sensitive output"):
        safe_failure_message(result, sensitive_values=("secret-value-from-host",))


@pytest.mark.parametrize("sensitive_name", ("LLM_API_KEY", "OPENAI_API_KEY"))
def test_failure_diagnostic_rejects_literal_sensitive_names(
    sensitive_name: str,
) -> None:
    result = subprocess.CompletedProcess(
        args=["npm"], returncode=1, stdout="", stderr=sensitive_name
    )

    with pytest.raises(AssertionError, match="emitted sensitive output"):
        safe_failure_message(result, sensitive_values=())


def test_pinned_node_major_rejects_a_newer_launcher_result() -> None:
    result = subprocess.CompletedProcess(
        args=["node"], returncode=0, stdout="25\n", stderr=""
    )

    with pytest.raises(AssertionError, match="requires Node major 24"):
        assert_pinned_node_major(result, expected_major=24)


def test_package_exposes_the_closed_live_company_research_command() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["verify:live-company-research"] == (
        "node scripts/with-project-node.mjs scripts/verify-live-company-research-ui.mjs"
    )
    assert package["scripts"]["test:live-company-research-support"] == (
        "node scripts/with-project-node.mjs --test "
        "scripts/live-company-research-support.test.mjs"
    )


@pytest.mark.live_company_research
@pytest.mark.skipif(
    not RUN_LIVE_COMPANY_RESEARCH,
    reason="set RUN_LIVE_COMPANY_RESEARCH=1 to run the live browser acceptance",
)
def test_live_company_research_browser_closes_the_full_public_workflow() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm")
    assert node, "Node.js is required for the live frontend verifier"
    assert npm, "npm is required for the live frontend verifier"

    tool_directories = dict.fromkeys(
        [str(Path(node).parent), str(Path(npm).parent), "/usr/bin", "/bin"]
    )
    with tempfile.TemporaryDirectory(prefix="fund-engine-live-npm-") as npm_runtime:
        env = {
            "PATH": os.pathsep.join(tool_directories),
            "PYTHON": sys.executable,
            "NPM_CONFIG_USERCONFIG": str(Path(npm_runtime) / "user-config"),
            "NPM_CONFIG_GLOBALCONFIG": str(Path(npm_runtime) / "global-config"),
            "NPM_CONFIG_CACHE": str(Path(npm_runtime) / "cache"),
        }
        for key in ("PW_BROWSER_CHANNEL", "SystemRoot", "WINDIR"):
            if key in os.environ:
                env[key] = os.environ[key]

        try:
            node_major = run_owned_process_group(
                [
                    node,
                    "scripts/with-project-node.mjs",
                    "-p",
                    'process.versions.node.split(".")[0]',
                ],
                cwd=FRONTEND,
                env=env,
                timeout_seconds=30,
                term_grace_seconds=TERM_GRACE_SECONDS,
                kill_grace_seconds=KILL_GRACE_SECONDS,
            )
        except OwnedProcessGroupTimeout as error:
            raise AssertionError(
                "project Node launcher exceeded its 30-second preflight bound; "
                "its exact owned process group was stopped"
            ) from error
        assert_pinned_node_major(
            node_major,
            expected_major=int(
                (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
            ),
        )
        try:
            result = run_owned_process_group(
                [npm, "run", "--silent", "verify:live-company-research"],
                cwd=FRONTEND,
                env=env,
                timeout_seconds=OUTER_TIMEOUT_SECONDS,
                term_grace_seconds=TERM_GRACE_SECONDS,
                kill_grace_seconds=KILL_GRACE_SECONDS,
            )
        except OwnedProcessGroupTimeout as error:
            raise AssertionError(
                "live company research verifier exceeded its 300-second outer bound; "
                "its exact owned process group was stopped"
            ) from error

    sensitive_values = tuple(
        value
        for key in SENSITIVE_HOST_ENV_KEYS
        if (value := os.environ.get(key))
    )
    _assert_no_sensitive_output(result, sensitive_values)
    if result.returncode != 0:
        raise AssertionError(safe_failure_message(result, sensitive_values=sensitive_values))
    if result.stdout != f"{PASS_LINE}\n":
        raise AssertionError(
            "live verifier stdout did not contain exactly one PASS line; output redacted"
        )
    if result.stderr != "":
        raise AssertionError("live verifier stderr was not empty; output redacted")
