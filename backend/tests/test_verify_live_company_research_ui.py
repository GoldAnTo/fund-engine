from __future__ import annotations

import errno
import inspect
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

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
OUTER_TIMEOUT_SECONDS = VERIFIER_INTERNAL_TIMEOUT_SECONDS + OUTER_CLEANUP_MARGIN_SECONDS
# The verifier's SIGTERM handler owns exact detached-browser-group cleanup. Keep
# a dedicated npm-group supervisor alive and unreaped through that bounded work.
TERM_GRACE_SECONDS = 70
KILL_GRACE_SECONDS = 10
OWNED_PROCESS_GROUP_SUPERVISOR = Path(__file__).with_name(
    "owned_process_group_supervisor.py"
)
OWNED_PROCESS_GROUP_SHUTDOWN_SIGNALS = (
    signal.SIGTERM,
    signal.SIGHUP,
    signal.SIGINT,
)
_EXACT_GROUP_KILL = os.killpg


class OwnedProcessGroupTimeout(TimeoutError):
    def __init__(self, process_group_id: int) -> None:
        super().__init__("live verifier exceeded its outer timeout")
        self.process_group_id = process_group_id


def _close_owned_status_writer(descriptor: int) -> None:
    os.close(descriptor)


class _OwnedProcessGroupOwner:
    def __init__(
        self, process: subprocess.Popen[str], *, kill_grace_seconds: float
    ) -> None:
        self.process = process
        self.process_group_id = process.pid
        self.kill_grace_seconds = kill_grace_seconds
        self.phase = "anchored"
        self.stdout_parts: list[str] = []
        self.stderr_parts: list[str] = []
        self.output_threads: list[threading.Thread] = []

    @staticmethod
    def _drain(stream: TextIO, parts: list[str]) -> None:
        try:
            output = stream.read()
            if output:
                parts.append(output)
        finally:
            stream.close()

    def start_output_capture(self) -> None:
        if self.process.stdout is None or self.process.stderr is None:
            raise AssertionError("owned process output pipes are unavailable")
        threads = (
            threading.Thread(
                target=self._drain,
                args=(self.process.stdout, self.stdout_parts),
                daemon=True,
            ),
            threading.Thread(
                target=self._drain,
                args=(self.process.stderr, self.stderr_parts),
                daemon=True,
            ),
        )
        for thread in threads:
            try:
                thread.start()
            finally:
                if thread.ident is not None:
                    self.output_threads.append(thread)

    def _anchor_is_zombie(self) -> bool:
        result = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(self.process.pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip().startswith("Z")

    def _enter_kill_sent_phase(
        self, send_kill: Callable[[int, signal.Signals], None]
    ) -> None:
        if self.phase != "anchored":
            raise AssertionError("numeric process-group capability is no longer live")
        blockable_signals = signal.valid_signals() - {signal.SIGKILL, signal.SIGSTOP}
        previous_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, blockable_signals)
            try:
                send_kill(self.process_group_id, signal.SIGKILL)
            except BaseException as signal_error:
                try:
                    _EXACT_GROUP_KILL(self.process_group_id, signal.SIGKILL)
                except OSError as fallback_error:
                    group_is_gone = (
                        fallback_error.errno
                        in (
                            errno.EPERM,
                            errno.ESRCH,
                        )
                        and self._anchor_is_zombie()
                    )
                    if group_is_gone:
                        self.phase = "kill_sent"
                    else:
                        signal_error.add_note(
                            f"fallback exact-group SIGKILL failed: {fallback_error!r}"
                        )
                    raise signal_error
                self.phase = "kill_sent"
                raise
            self.phase = "kill_sent"
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)

    @staticmethod
    def _retain_first_error(
        first_error: BaseException | None, error: BaseException
    ) -> BaseException:
        if first_error is None:
            return error
        first_error.add_note(f"additional owned-group cleanup failure: {error!r}")
        return first_error

    def _bounded_reap_and_close(self) -> None:
        if self.phase == "anchored":
            raise AssertionError("cannot reap before exact-group SIGKILL")
        deadline = time.monotonic() + self.kill_grace_seconds
        first_error: BaseException | None = None
        while self.phase != "reaped":
            try:
                self.process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                first_error = self._retain_first_error(
                    first_error,
                    AssertionError(
                        "exact live verifier process group did not exit after SIGKILL"
                    ),
                )
                if time.monotonic() >= deadline:
                    break
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)
                if self.process.returncode is not None:
                    self.phase = "reaped"
                elif time.monotonic() >= deadline:
                    break
            else:
                self.phase = "reaped"

        for thread in self.output_threads:
            while thread.is_alive() and time.monotonic() < deadline:
                try:
                    thread.join(timeout=max(0, deadline - time.monotonic()))
                except BaseException as error:  # noqa: BLE001
                    first_error = self._retain_first_error(first_error, error)

        for stream in (self.process.stdout, self.process.stderr):
            if stream is None or stream.closed:
                continue
            try:
                stream.close()
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)

        if self.phase != "reaped":
            raise AssertionError(
                "exact live verifier process group remained unreaped after SIGKILL"
            ) from first_error
        if any(thread.is_alive() for thread in self.output_threads):
            raise AssertionError(
                "live verifier output pipes remained open after SIGKILL"
            ) from first_error
        if first_error is not None:
            raise first_error

    def ensure_cleanup(
        self,
        send_kill: Callable[[int, signal.Signals], None] | None = None,
    ) -> None:
        first_error: BaseException | None = None
        if self.phase == "anchored":
            try:
                self._enter_kill_sent_phase(send_kill or _EXACT_GROUP_KILL)
            except BaseException as error:  # noqa: BLE001
                first_error = error

        deadline = time.monotonic() + self.kill_grace_seconds
        while self.phase == "anchored" and time.monotonic() < deadline:
            try:
                self._enter_kill_sent_phase(_EXACT_GROUP_KILL)
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)

        if self.phase == "anchored":
            cleanup_error = AssertionError(
                "exact live verifier process group could not be killed"
            )
            if first_error is not None:
                raise cleanup_error from first_error
            raise cleanup_error

        try:
            self._bounded_reap_and_close()
        except BaseException as error:  # noqa: BLE001
            first_error = self._retain_first_error(first_error, error)
        if first_error is not None:
            raise first_error

    def completed_process(
        self, command: Sequence[str], returncode: int
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            returncode,
            "".join(self.stdout_parts),
            "".join(self.stderr_parts),
        )


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
    status_read, status_write = os.pipe()
    status_read_closed = False
    status_write_closed = False
    supervisor_command = [
        sys.executable,
        str(OWNED_PROCESS_GROUP_SUPERVISOR),
        "--status-fd",
        str(status_write),
        "--",
        *command,
    ]
    owner: _OwnedProcessGroupOwner | None = None
    try:
        previous_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            signal.pthread_sigmask(
                signal.SIG_BLOCK, OWNED_PROCESS_GROUP_SHUTDOWN_SIGNALS
            )
            process = subprocess.Popen(
                supervisor_command,
                cwd=cwd,
                env=dict(env),
                pass_fds=(status_write,),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            owner = _OwnedProcessGroupOwner(
                process, kill_grace_seconds=kill_grace_seconds
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)

        _close_owned_status_writer(status_write)
        status_write_closed = True
        owner.start_output_capture()
        readable, _, _ = select.select([status_read], [], [], timeout_seconds)
        if readable:
            raw_status = os.read(status_read, 32)
            try:
                child_returncode = int(raw_status.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError) as error:
                raise AssertionError(
                    "owned process group supervisor status is invalid"
                ) from error
            owner.ensure_cleanup(kill_group)
            return owner.completed_process(command, child_returncode)

        kill_group(owner.process_group_id, signal.SIGTERM)
        # Do not communicate, poll, or otherwise reap the supervisor
        # during this grace period: the live leader remains the exact capability.
        time.sleep(term_grace_seconds)
        owner.ensure_cleanup(kill_group)
        raise OwnedProcessGroupTimeout(owner.process_group_id)
    except BaseException as primary_error:
        if owner is not None:
            try:
                owner.ensure_cleanup(kill_group)
            except BaseException as cleanup_error:
                raise primary_error from cleanup_error
        raise
    finally:
        if not status_read_closed:
            os.close(status_read)
            status_read_closed = True
        if not status_write_closed:
            _close_owned_status_writer(status_write)
            status_write_closed = True


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


def test_process_group_supervisor_stays_anchored_after_child_status(
    tmp_path: Path,
) -> None:
    status_read, status_write = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            str(OWNED_PROCESS_GROUP_SUPERVISOR),
            "--status-fd",
            str(status_write),
            "--",
            sys.executable,
            "-c",
            "print('released output')",
        ],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        pass_fds=(status_write,),
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(status_write)
    try:
        readable, _, _ = select.select([status_read], [], [], 2)
        assert readable == [status_read]
        assert os.read(status_read, 32) == b"0\n"
        anchor_state = _process_state(process.pid)
        assert anchor_state and not anchor_state.startswith("Z")
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == -signal.SIGKILL
        assert stdout == "released output\n"
        assert stderr == ""
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        os.close(status_read)


def test_owned_process_group_is_signal_safe_before_supervisor_python_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delayed_supervisor = tmp_path / "delayed_supervisor.py"
    delayed_supervisor.write_text(
        "import time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "OWNED_PROCESS_GROUP_SUPERVISOR",
        delayed_supervisor,
    )
    states_before_signal: list[str] = []
    sent_signals: list[signal.Signals] = []

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        states_before_signal.append(_process_state(process_group_id))
        sent_signals.append(sent_signal)
        os.killpg(process_group_id, sent_signal)

    with pytest.raises(OwnedProcessGroupTimeout):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.03,
            term_grace_seconds=0.02,
            kill_grace_seconds=1,
            kill_group=kill_group,
        )

    assert sent_signals == [signal.SIGTERM, signal.SIGKILL]
    assert len(states_before_signal) == 2
    assert all(state and not state.startswith("Z") for state in states_before_signal)


def test_owned_process_group_normal_exit_removes_same_group_descendant(
    tmp_path: Path,
) -> None:
    descendant_pid_path = tmp_path / "normal-exit-descendant.pid"
    descendant_program = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    command_program = "\n".join(
        (
            "import pathlib, subprocess, sys, time",
            f"subprocess.Popen([sys.executable, '-c', {descendant_program!r}])",
            f"path = pathlib.Path({str(descendant_pid_path)!r})",
            "deadline = time.monotonic() + 2",
            "while not path.exists() and time.monotonic() < deadline: time.sleep(0.01)",
            "print('normal child output')",
            "raise SystemExit(3)",
        )
    )
    descendant_pid: int | None = None
    try:
        result = run_owned_process_group(
            [sys.executable, "-c", command_program],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=3,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        assert result.returncode == 3
        assert result.stdout == "normal child output\n"
        assert result.stderr == ""
        state = _process_state(descendant_pid)
        assert not state or state.startswith("Z")
    finally:
        if descendant_pid is None and descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None and _process_state(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_owned_process_group_spawn_failure_keeps_anchor_until_cleanup(
    tmp_path: Path,
) -> None:
    states_before_signal: list[str] = []
    sent_signals: list[tuple[int, signal.Signals]] = []

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        states_before_signal.append(_process_state(process_group_id))
        sent_signals.append((process_group_id, sent_signal))
        os.killpg(process_group_id, sent_signal)

    result = run_owned_process_group(
        ["/definitely/missing/owned-command"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        timeout_seconds=1,
        term_grace_seconds=0.05,
        kill_grace_seconds=1,
        kill_group=kill_group,
    )

    assert result.returncode == 127
    assert result.stdout == ""
    assert result.stderr == ""
    assert len(sent_signals) == 1
    assert sent_signals[0][1] == signal.SIGKILL
    assert states_before_signal[0] and not states_before_signal[0].startswith("Z")


def test_owned_process_group_invalid_status_still_reaps_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor_pid_path = tmp_path / "invalid-status-supervisor.pid"
    invalid_supervisor = tmp_path / "invalid_status_supervisor.py"
    invalid_supervisor.write_text(
        "\n".join(
            (
                "import os, pathlib",
                f"pathlib.Path({str(supervisor_pid_path)!r}).write_text(str(os.getpid()))",
                "raise SystemExit(91)",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "OWNED_PROCESS_GROUP_SUPERVISOR",
        invalid_supervisor,
    )

    with pytest.raises(AssertionError, match="supervisor status is invalid"):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

    supervisor_pid = int(supervisor_pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ChildProcessError):
        os.waitpid(supervisor_pid, os.WNOHANG)


def test_owned_process_group_normal_status_kill_error_still_reaps_anchor(
    tmp_path: Path,
) -> None:
    anchor_pid: int | None = None

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        nonlocal anchor_pid
        anchor_pid = process_group_id
        raise PermissionError("injected normal cleanup signal failure")

    with pytest.raises(PermissionError, match="injected normal cleanup signal failure"):
        run_owned_process_group(
            [sys.executable, "-c", "print('completed before cleanup error')"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=kill_group,
        )

    assert anchor_pid is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(anchor_pid, os.WNOHANG)


def test_owned_process_group_thread_start_error_still_reaps_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started_processes: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    def fail_thread_start(_thread: threading.Thread) -> None:
        raise RuntimeError("injected output thread start failure")

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setattr(threading.Thread, "start", fail_thread_start)

    try:
        with pytest.raises(RuntimeError, match="injected output thread start failure"):
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
            )

        assert len(started_processes) == 1
        assert started_processes[0].stdout is not None
        assert started_processes[0].stdout.closed
        assert started_processes[0].stderr is not None
        assert started_processes[0].stderr.closed
        with pytest.raises(ChildProcessError):
            os.waitpid(started_processes[0].pid, os.WNOHANG)
    finally:
        for process in started_processes:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)


def test_owned_process_group_status_pipe_close_error_still_reaps_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started_processes: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen
    original_close_status_writer = _close_owned_status_writer
    close_failure_injected = False

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        started_processes.append(process)
        return process

    def fail_first_status_write_close(descriptor: int) -> None:
        nonlocal close_failure_injected
        if not close_failure_injected:
            close_failure_injected = True
            raise OSError("injected status pipe close failure")
        original_close_status_writer(descriptor)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "_close_owned_status_writer",
        fail_first_status_write_close,
    )

    with pytest.raises(OSError, match="injected status pipe close failure"):
        run_owned_process_group(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

    assert close_failure_injected
    assert len(started_processes) == 1
    assert started_processes[0].stdout is not None
    assert started_processes[0].stdout.closed
    assert started_processes[0].stderr is not None
    assert started_processes[0].stderr.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(started_processes[0].pid, os.WNOHANG)


@pytest.mark.parametrize("raise_before_signal", [False, True])
def test_owned_process_group_startup_failure_uses_shared_baseexception_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raise_before_signal: bool,
) -> None:
    started_processes: list[subprocess.Popen[str]] = []
    states_before_signal: list[str] = []
    original_popen = subprocess.Popen
    original_killpg = os.killpg
    original_close_status_writer = _close_owned_status_writer
    close_failure_injected = False
    signal_failure_injected = False

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    def fail_first_status_write_close(descriptor: int) -> None:
        nonlocal close_failure_injected
        if not close_failure_injected:
            close_failure_injected = True
            raise OSError("injected startup status close failure")
        original_close_status_writer(descriptor)

    def interrupt_first_group_kill(
        process_group_id: int, sent_signal: signal.Signals
    ) -> None:
        nonlocal signal_failure_injected
        states_before_signal.append(_process_state(process_group_id))
        if not signal_failure_injected:
            signal_failure_injected = True
            if raise_before_signal:
                raise KeyboardInterrupt
            original_killpg(process_group_id, sent_signal)
            raise KeyboardInterrupt
        original_killpg(process_group_id, sent_signal)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setattr(os, "killpg", interrupt_first_group_kill)
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "_close_owned_status_writer",
        fail_first_status_write_close,
    )

    try:
        with pytest.raises(
            OSError, match="injected startup status close failure"
        ) as captured:
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
                kill_group=interrupt_first_group_kill,
            )

        assert isinstance(captured.value.__cause__, KeyboardInterrupt)
        assert close_failure_injected
        assert signal_failure_injected
        assert len(started_processes) == 1
        process = started_processes[0]
        assert process.returncode is not None
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed
        assert states_before_signal
        assert all(
            state and not state.startswith("Z") for state in states_before_signal
        )
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
    finally:
        for process in started_processes:
            if process.returncode is None:
                try:
                    original_killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait(timeout=1)
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def test_owned_process_group_never_signals_group_after_wait_reaps_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_popen = subprocess.Popen
    original_killpg = os.killpg
    sent_signals: list[tuple[int, signal.Signals]] = []
    wait_failure_injected = False

    def popen_with_wait_failure(
        *args: object, **kwargs: object
    ) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        original_wait = process.wait

        def wait_then_raise(*wait_args: object, **wait_kwargs: object) -> int:
            nonlocal wait_failure_injected
            returncode = original_wait(*wait_args, **wait_kwargs)
            if not wait_failure_injected:
                wait_failure_injected = True
                raise RuntimeError("injected failure after OS reap")
            return returncode

        process.wait = wait_then_raise  # type: ignore[method-assign]
        return process

    def recording_killpg(process_group_id: int, sent_signal: signal.Signals) -> None:
        sent_signals.append((process_group_id, sent_signal))
        original_killpg(process_group_id, sent_signal)

    monkeypatch.setattr(subprocess, "Popen", popen_with_wait_failure)
    monkeypatch.setattr(os, "killpg", recording_killpg)

    with pytest.raises(RuntimeError, match="injected failure after OS reap"):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=recording_killpg,
        )

    assert wait_failure_injected
    assert len(sent_signals) == 1
    assert sent_signals[0][1] == signal.SIGKILL


def test_owned_process_group_never_waits_while_numeric_authority_is_live() -> None:
    owner_source = inspect.getsource(_OwnedProcessGroupOwner)
    anchored_phase_source = owner_source.split(
        "    def _bounded_reap_and_close", maxsplit=1
    )[0]

    for forbidden in ("wait(", "waitpid(", ".poll("):
        assert forbidden not in anchored_phase_source


def test_owned_process_group_never_signals_after_wnohang_reaps_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_killpg = os.killpg
    original_waitpid = os.waitpid
    sent_signals: list[tuple[int, signal.Signals]] = []
    anchor_pid: int | None = None
    wait_failure_injected = False

    def kill_then_raise(process_group_id: int, sent_signal: signal.Signals) -> None:
        nonlocal anchor_pid
        anchor_pid = process_group_id
        sent_signals.append((process_group_id, sent_signal))
        original_killpg(process_group_id, sent_signal)
        deadline = time.monotonic() + 1
        while True:
            state = _process_state(process_group_id)
            if not state or state.startswith("Z"):
                break
            if time.monotonic() >= deadline:
                raise AssertionError("anchor did not exit after injected SIGKILL")
            time.sleep(0.01)
        raise RuntimeError("injected post-signal callback failure")

    def waitpid_then_raise(process_id: int, options: int) -> tuple[int, int]:
        nonlocal wait_failure_injected
        result = original_waitpid(process_id, options)
        if (
            process_id == anchor_pid
            and options == os.WNOHANG
            and result[0] == process_id
            and not wait_failure_injected
        ):
            wait_failure_injected = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(os, "killpg", kill_then_raise)
    monkeypatch.setattr(os, "waitpid", waitpid_then_raise)

    with pytest.raises(RuntimeError, match="injected post-signal callback failure"):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=kill_then_raise,
        )

    assert wait_failure_injected
    assert len(sent_signals) == 1
    assert sent_signals[0][1] == signal.SIGKILL
    assert anchor_pid is not None
    with pytest.raises(ChildProcessError):
        original_waitpid(anchor_pid, os.WNOHANG)


def test_owned_process_group_distinguishes_post_signal_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_killpg = os.killpg
    sent_signals: list[tuple[int, signal.Signals]] = []

    def kill_then_interrupt(process_group_id: int, sent_signal: signal.Signals) -> None:
        sent_signals.append((process_group_id, sent_signal))
        original_killpg(process_group_id, sent_signal)
        deadline = time.monotonic() + 1
        while True:
            state = _process_state(process_group_id)
            if not state or state.startswith("Z"):
                break
            if time.monotonic() >= deadline:
                raise AssertionError("anchor did not exit after injected SIGKILL")
            time.sleep(0.01)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "killpg", kill_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=kill_then_interrupt,
        )

    assert len(sent_signals) == 1
    assert sent_signals[0][1] == signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(sent_signals[0][0], os.WNOHANG)


def test_owned_process_group_signal_mask_closes_keyboard_interrupt_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_killpg = os.killpg
    original_pthread_sigmask = signal.pthread_sigmask
    sent_signals: list[tuple[int, signal.Signals]] = []
    restore_count = 0

    def recording_killpg(process_group_id: int, sent_signal: signal.Signals) -> None:
        sent_signals.append((process_group_id, sent_signal))
        original_killpg(process_group_id, sent_signal)

    def interrupt_second_mask_restore(
        how: int, mask: set[signal.Signals]
    ) -> set[signal.Signals]:
        nonlocal restore_count
        previous_mask = original_pthread_sigmask(how, mask)
        if how == signal.SIG_SETMASK:
            restore_count += 1
            if restore_count == 2:
                raise KeyboardInterrupt
        return previous_mask

    monkeypatch.setattr(os, "killpg", recording_killpg)
    monkeypatch.setattr(signal, "pthread_sigmask", interrupt_second_mask_restore)

    with pytest.raises(KeyboardInterrupt):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=recording_killpg,
        )

    assert restore_count == 2
    assert len(sent_signals) == 1
    assert sent_signals[0][1] == signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(sent_signals[0][0], os.WNOHANG)


def test_owned_process_group_restores_mask_if_blocking_raises_after_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_killpg = os.killpg
    original_pthread_sigmask = signal.pthread_sigmask
    initial_mask = original_pthread_sigmask(signal.SIG_BLOCK, set())
    sent_signals: list[tuple[int, signal.Signals]] = []
    block_count = 0

    def recording_killpg(process_group_id: int, sent_signal: signal.Signals) -> None:
        sent_signals.append((process_group_id, sent_signal))
        original_killpg(process_group_id, sent_signal)

    def interrupt_second_mask_block(
        how: int, mask: set[signal.Signals]
    ) -> set[signal.Signals]:
        nonlocal block_count
        previous_mask = original_pthread_sigmask(how, mask)
        if how == signal.SIG_BLOCK and mask:
            block_count += 1
            if block_count == 2:
                raise KeyboardInterrupt
        return previous_mask

    monkeypatch.setattr(os, "killpg", recording_killpg)
    monkeypatch.setattr(signal, "pthread_sigmask", interrupt_second_mask_block)
    monkeypatch.setitem(
        run_owned_process_group.__globals__, "_EXACT_GROUP_KILL", recording_killpg
    )

    try:
        with pytest.raises(KeyboardInterrupt):
            run_owned_process_group(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
                kill_group=recording_killpg,
            )

        assert block_count >= 2
        assert original_pthread_sigmask(signal.SIG_BLOCK, set()) == initial_mask
        assert len(sent_signals) == 1
        assert sent_signals[0][1] == signal.SIGKILL
        with pytest.raises(ChildProcessError):
            os.waitpid(sent_signals[0][0], os.WNOHANG)
    finally:
        original_pthread_sigmask(signal.SIG_SETMASK, initial_mask)


@pytest.mark.parametrize("raise_before_signal", [False, True])
def test_owned_process_group_reaps_anchor_when_kill_group_raises(
    tmp_path: Path,
    raise_before_signal: bool,
) -> None:
    anchor_pid: int | None = None

    def kill_group(process_group_id: int, sent_signal: signal.Signals) -> None:
        nonlocal anchor_pid
        anchor_pid = process_group_id
        if sent_signal == signal.SIGKILL and raise_before_signal:
            raise PermissionError("injected pre-signal failure")
        os.killpg(process_group_id, sent_signal)
        if sent_signal == signal.SIGKILL:
            raise PermissionError("injected post-signal failure")

    with pytest.raises(PermissionError, match="injected .* failure"):
        run_owned_process_group(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.05,
            term_grace_seconds=0.01,
            kill_grace_seconds=1,
            kill_group=kill_group,
        )

    assert anchor_pid is not None
    try:
        reaped_pid, _ = os.waitpid(anchor_pid, os.WNOHANG)
    except ChildProcessError:
        reaped_pid = 0
    if reaped_pid == 0 and _process_state(anchor_pid):
        os.killpg(anchor_pid, signal.SIGKILL)
        os.waitpid(anchor_pid, 0)
        reaped_pid = anchor_pid
    assert reaped_pid == 0, "run_owned_process_group leaked an unreaped anchor"


def test_owned_process_group_normal_exit_preserves_child_status_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    for iteration in range(100):
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
        assert all(
            state and not state.startswith("Z") for state in states_before_signal
        )


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
            expected_major=int((ROOT / ".nvmrc").read_text(encoding="utf-8").strip()),
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
        value for key in SENSITIVE_HOST_ENV_KEYS if (value := os.environ.get(key))
    )
    _assert_no_sensitive_output(result, sensitive_values)
    if result.returncode != 0:
        raise AssertionError(
            safe_failure_message(result, sensitive_values=sensitive_values)
        )
    if result.stdout != f"{PASS_LINE}\n":
        raise AssertionError(
            "live verifier stdout did not contain exactly one PASS line; output redacted"
        )
    if result.stderr != "":
        raise AssertionError("live verifier stderr was not empty; output redacted")
