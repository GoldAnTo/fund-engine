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
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

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
OWNED_OUTPUT_CAPTURE_LIMIT_BYTES = 16_384
OWNED_OUTPUT_READ_CHUNK_BYTES = 65_536
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


class OwnedProcessGroupOutputOverflow(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "live verifier output exceeded its fixed per-stream capture limit"
        )


def _close_owned_status_writer(descriptor: int) -> None:
    os.close(descriptor)


class _OwnedProcessGroupOwner:
    __slots__ = (
        "phase",
        "process",
        "status_buffer",
        "status_read_fd",
        "status_write_fd",
        "stderr_buffer",
        "stderr_eof",
        "stderr_overflow",
        "stdout_buffer",
        "stdout_eof",
        "stdout_overflow",
    )

    def __init__(self) -> None:
        # Allocate every Python-owned container before Popen. Once Popen returns,
        # the single slot assignment in run_owned_process_group is the ownership
        # transfer; no allocating wrapper can fail in between.
        self.process: subprocess.Popen[bytes] | None = None
        # The phase is valid before attachment; process=None distinguishes the
        # harmless pre-spawn state. No second state write is needed after Popen.
        self.phase = "anchored"
        self.status_read_fd = -1
        self.status_write_fd = -1
        self.status_buffer = bytearray()
        self.stdout_buffer = bytearray()
        self.stderr_buffer = bytearray()
        self.stdout_eof = False
        self.stderr_eof = False
        self.stdout_overflow = False
        self.stderr_overflow = False

    @property
    def process_group_id(self) -> int:
        if self.process is None:
            raise AssertionError("owned process group is not attached")
        return self.process.pid

    def close_status_writer(self) -> None:
        descriptor = self.status_write_fd
        if descriptor < 0:
            return
        # Retire numeric authority before an ambiguous close. If close performs
        # its side effect and then raises, this descriptor can already be reused.
        self.status_write_fd = -1
        _close_owned_status_writer(descriptor)

    @staticmethod
    def _close_stream(stream: BinaryIO) -> None:
        stream.close()

    def _close_parent_descriptors(self) -> BaseException | None:
        first_error: BaseException | None = None
        if self.status_read_fd >= 0:
            descriptor = self.status_read_fd
            self.status_read_fd = -1
            try:
                os.close(descriptor)
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)
        if self.status_write_fd >= 0:
            try:
                self.close_status_writer()
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)
        if self.process is not None:
            for stream in (self.process.stdout, self.process.stderr):
                if stream is None or stream.closed:
                    continue
                try:
                    self._close_stream(stream)
                except BaseException as error:  # noqa: BLE001
                    first_error = self._retain_first_error(first_error, error)
        return first_error

    def prepare_nonblocking_capture(self) -> None:
        if self.process is None:
            raise AssertionError("owned process group is not attached")
        if self.process.stdout is None or self.process.stderr is None:
            raise AssertionError("owned process output pipes are unavailable")
        for descriptor in (
            self.status_read_fd,
            self.process.stdout.fileno(),
            self.process.stderr.fileno(),
        ):
            os.set_blocking(descriptor, False)

    def _capture_descriptors(self) -> dict[int, str]:
        if self.process is None:
            return {}
        descriptors: dict[int, str] = {}
        if self.status_read_fd >= 0:
            descriptors[self.status_read_fd] = "status"
        if self.process.stdout is not None and not self.stdout_eof:
            descriptors[self.process.stdout.fileno()] = "stdout"
        if self.process.stderr is not None and not self.stderr_eof:
            descriptors[self.process.stderr.fileno()] = "stderr"
        return descriptors

    def _read_ready(self, descriptors: dict[int, str], timeout: float) -> None:
        if not descriptors:
            return
        readable, _, _ = select.select(list(descriptors), [], [], max(0, timeout))
        for descriptor in readable:
            try:
                chunk = os.read(descriptor, OWNED_OUTPUT_READ_CHUNK_BYTES)
            except BlockingIOError:
                continue
            stream_name = descriptors[descriptor]
            if chunk:
                if stream_name == "status":
                    self.status_buffer.extend(chunk)
                elif stream_name == "stdout":
                    self.stdout_overflow = self._append_bounded_output(
                        self.stdout_buffer, chunk, self.stdout_overflow
                    )
                else:
                    self.stderr_overflow = self._append_bounded_output(
                        self.stderr_buffer, chunk, self.stderr_overflow
                    )
                continue
            if stream_name == "status":
                retired_descriptor = self.status_read_fd
                self.status_read_fd = -1
                os.close(retired_descriptor)
            elif stream_name == "stdout":
                self.stdout_eof = True
            else:
                self.stderr_eof = True

    @staticmethod
    def _append_bounded_output(
        buffer: bytearray, chunk: bytes, overflowed: bool
    ) -> bool:
        remaining = OWNED_OUTPUT_CAPTURE_LIMIT_BYTES - len(buffer)
        if remaining > 0:
            buffer.extend(memoryview(chunk)[:remaining])
        return overflowed or len(chunk) > remaining

    def read_child_status(self, timeout_seconds: float) -> int | None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if b"\n" in self.status_buffer or self.status_read_fd < 0:
                raw_status = bytes(self.status_buffer).strip()
                try:
                    return int(raw_status.decode("ascii"))
                except (UnicodeDecodeError, ValueError) as error:
                    raise AssertionError(
                        "owned process group supervisor status is invalid"
                    ) from error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._read_ready(self._capture_descriptors(), remaining)

    def _enter_kill_sent_phase(
        self,
        send_kill: Callable[[int, signal.Signals], None],
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
                    if fallback_error.errno in (errno.EPERM, errno.ESRCH):
                        # The anchor is deliberately still unreaped, so its PID
                        # (and therefore this numeric PGID) cannot have been
                        # reused. A kernel answer that the exact group is absent
                        # or unsignalable retires authority without a probe; only
                        # handle-based bounded reap is permitted after this write.
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

    def _bounded_reap_and_close(self, deadline: float) -> None:
        if self.phase == "anchored":
            raise AssertionError("cannot reap before exact-group SIGKILL")
        if self.process is None:
            raise AssertionError("owned process group is not attached")
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

        while (
            not self.stdout_eof or not self.stderr_eof
        ) and time.monotonic() < deadline:
            try:
                self._read_ready(
                    self._capture_descriptors(),
                    max(0, deadline - time.monotonic()),
                )
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)
                break

        close_error = self._close_parent_descriptors()
        if close_error is not None:
            first_error = self._retain_first_error(first_error, close_error)

        if self.phase != "reaped":
            raise AssertionError(
                "exact live verifier process group remained unreaped after SIGKILL"
            ) from first_error
        if self.stdout_overflow or self.stderr_overflow:
            overflow_error = OwnedProcessGroupOutputOverflow()
            if not self.stdout_eof or not self.stderr_eof:
                raise overflow_error from AssertionError(
                    "live verifier output pipes remained open after SIGKILL"
                )
            if first_error is not None:
                raise overflow_error from first_error
            raise overflow_error
        if not self.stdout_eof or not self.stderr_eof:
            pipe_error = AssertionError(
                "live verifier output pipes remained open after SIGKILL"
            )
            if first_error is not None:
                raise pipe_error from first_error
            raise pipe_error
        if first_error is not None:
            raise first_error

    def ensure_cleanup(
        self,
        deadline: float,
        send_kill: Callable[[int, signal.Signals], None] | None = None,
    ) -> None:
        if self.process is None:
            close_error = self._close_parent_descriptors()
            if close_error is not None:
                raise close_error
            return
        first_error: BaseException | None = None
        if self.phase == "anchored":
            try:
                self._enter_kill_sent_phase(send_kill or _EXACT_GROUP_KILL)
            except BaseException as error:  # noqa: BLE001
                first_error = error

        while self.phase == "anchored" and time.monotonic() < deadline:
            try:
                self._enter_kill_sent_phase(_EXACT_GROUP_KILL)
            except BaseException as error:  # noqa: BLE001
                first_error = self._retain_first_error(first_error, error)

        if self.phase == "anchored":
            cleanup_error = AssertionError(
                "exact live verifier process group could not be killed"
            )
            close_error = self._close_parent_descriptors()
            if close_error is not None:
                first_error = self._retain_first_error(first_error, close_error)
            if first_error is not None:
                raise cleanup_error from first_error
            raise cleanup_error

        try:
            self._bounded_reap_and_close(deadline)
        except BaseException as error:  # noqa: BLE001
            first_error = self._retain_first_error(first_error, error)
        if first_error is not None:
            raise first_error

    def completed_process(
        self, command: Sequence[str], returncode: int
    ) -> subprocess.CompletedProcess[str]:
        if self.stdout_overflow or self.stderr_overflow:
            raise OwnedProcessGroupOutputOverflow
        return subprocess.CompletedProcess(
            command,
            returncode,
            bytes(self.stdout_buffer).decode("utf-8", errors="replace"),
            bytes(self.stderr_buffer).decode("utf-8", errors="replace"),
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
    owner = _OwnedProcessGroupOwner()
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    child_returncode: int | None = None
    cleanup_deadline: float | None = None
    try:
        previous_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            signal.pthread_sigmask(
                signal.SIG_BLOCK, OWNED_PROCESS_GROUP_SHUTDOWN_SIGNALS
            )
            owner.status_read_fd, owner.status_write_fd = os.pipe()
            supervisor_command = [
                sys.executable,
                str(OWNED_PROCESS_GROUP_SUPERVISOR),
                "--status-fd",
                str(owner.status_write_fd),
                "--",
                *command,
            ]
            owner.process = subprocess.Popen(
                supervisor_command,
                cwd=cwd,
                env=dict(env),
                pass_fds=(owner.status_write_fd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)

        owner.close_status_writer()
        owner.prepare_nonblocking_capture()
        child_returncode = owner.read_child_status(timeout_seconds)
        if child_returncode is None:
            cleanup_deadline = (
                time.monotonic() + term_grace_seconds + kill_grace_seconds
            )
            kill_group(owner.process_group_id, signal.SIGTERM)
            # Do not communicate, poll, or otherwise reap the supervisor during
            # this grace: its unreaped leader is still the exact capability.
            time.sleep(
                min(
                    term_grace_seconds,
                    max(0, cleanup_deadline - time.monotonic()),
                )
            )
            primary_error = OwnedProcessGroupTimeout(owner.process_group_id)
    except BaseException as error:  # noqa: BLE001
        primary_error = error
    finally:
        if cleanup_deadline is None:
            cleanup_deadline = time.monotonic() + kill_grace_seconds
        try:
            owner.ensure_cleanup(cleanup_deadline, kill_group)
        except BaseException as error:  # noqa: BLE001
            cleanup_error = error

    if primary_error is not None:
        if cleanup_error is not None:
            raise primary_error from cleanup_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if child_returncode is None:
        raise AssertionError("owned process group did not report a child status")
    return owner.completed_process(command, child_returncode)


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


def test_owned_process_group_nonblocking_setup_error_still_reaps_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started_processes: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen
    original_set_blocking = os.set_blocking
    failure_injected = False

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    def fail_first_set_blocking(descriptor: int, blocking: bool) -> None:
        nonlocal failure_injected
        if not failure_injected:
            failure_injected = True
            raise RuntimeError("injected nonblocking capture setup failure")
        original_set_blocking(descriptor, blocking)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setattr(os, "set_blocking", fail_first_set_blocking)

    try:
        with pytest.raises(
            RuntimeError, match="injected nonblocking capture setup failure"
        ):
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
            )

        assert len(started_processes) == 1
        assert failure_injected
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
            original_close_status_writer(descriptor)
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


def test_owned_process_group_owner_is_allocated_before_supervisor_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started_processes: list[subprocess.Popen[str]] = []
    original_owner = _OwnedProcessGroupOwner
    original_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    def fail_owner_allocation(*args: object, **kwargs: object) -> None:
        raise MemoryError("injected owner allocation failure")

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "_OwnedProcessGroupOwner",
        fail_owner_allocation,
    )

    try:
        with pytest.raises(MemoryError, match="injected owner allocation failure"):
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
            )

        assert started_processes == []
    finally:
        monkeypatch.setitem(
            run_owned_process_group.__globals__,
            "_OwnedProcessGroupOwner",
            original_owner,
        )
        for process in started_processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait(timeout=1)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def test_owned_pipe_acquisition_is_signal_blocked_until_owner_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_pipe = os.pipe
    acquired_descriptors: list[int] = []

    def pipe_with_async_interrupt_window() -> tuple[int, int]:
        descriptors = original_pipe()
        if acquired_descriptors:
            return descriptors
        acquired_descriptors.extend(descriptors)
        current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        if signal.SIGINT not in current_mask:
            raise KeyboardInterrupt
        return descriptors

    monkeypatch.setattr(os, "pipe", pipe_with_async_interrupt_window)

    try:
        result = run_owned_process_group(
            [sys.executable, "-c", "print('captured after blocked pipe')"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

        assert result.returncode == 0
        assert result.stdout == "captured after blocked pipe\n"
        assert len(acquired_descriptors) == 2
        for descriptor in acquired_descriptors:
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(descriptor)
    finally:
        for descriptor in acquired_descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_owned_acquisition_restores_mask_when_blocking_raises_after_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_pthread_sigmask = signal.pthread_sigmask
    original_pipe = os.pipe
    original_popen = subprocess.Popen
    initial_mask = original_pthread_sigmask(signal.SIG_BLOCK, set())
    pipe_calls = 0
    process_calls = 0
    block_failure_injected = False

    def interrupt_acquisition_block_after_apply(
        how: int, mask: set[signal.Signals] | tuple[signal.Signals, ...]
    ) -> set[signal.Signals]:
        nonlocal block_failure_injected
        previous_mask = original_pthread_sigmask(how, mask)
        if (
            how == signal.SIG_BLOCK
            and set(mask) == set(OWNED_PROCESS_GROUP_SHUTDOWN_SIGNALS)
            and not block_failure_injected
        ):
            block_failure_injected = True
            raise KeyboardInterrupt
        return previous_mask

    def recording_pipe() -> tuple[int, int]:
        nonlocal pipe_calls
        pipe_calls += 1
        return original_pipe()

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        nonlocal process_calls
        process_calls += 1
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(
        signal, "pthread_sigmask", interrupt_acquisition_block_after_apply
    )
    monkeypatch.setattr(os, "pipe", recording_pipe)
    monkeypatch.setattr(subprocess, "Popen", recording_popen)

    try:
        with pytest.raises(KeyboardInterrupt):
            run_owned_process_group(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
            )

        assert block_failure_injected
        assert pipe_calls == 0
        assert process_calls == 0
        assert original_pthread_sigmask(signal.SIG_BLOCK, set()) == initial_mask
    finally:
        original_pthread_sigmask(signal.SIG_SETMASK, initial_mask)


def test_owned_status_writer_retires_before_ambiguous_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_close_status_writer = _close_owned_status_writer
    close_injected = False
    reused_descriptor: int | None = None

    def close_reuse_then_interrupt(descriptor: int) -> None:
        nonlocal close_injected, reused_descriptor
        if not close_injected:
            close_injected = True
            original_close_status_writer(descriptor)
            reused_descriptor = os.open(os.devnull, os.O_RDONLY)
            assert reused_descriptor == descriptor
            raise KeyboardInterrupt
        original_close_status_writer(descriptor)

    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "_close_owned_status_writer",
        close_reuse_then_interrupt,
    )

    try:
        with pytest.raises(KeyboardInterrupt):
            run_owned_process_group(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=1,
            )

        assert reused_descriptor is not None
        os.fstat(reused_descriptor)
    finally:
        if reused_descriptor is not None:
            try:
                os.close(reused_descriptor)
            except OSError:
                pass


def _record_owned_group_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_OwnedProcessGroupOwner]:
    owner_type = _OwnedProcessGroupOwner
    owners: list[_OwnedProcessGroupOwner] = []

    def recording_owner() -> _OwnedProcessGroupOwner:
        owner = owner_type()
        owners.append(owner)
        return owner

    monkeypatch.setitem(
        run_owned_process_group.__globals__, "_OwnedProcessGroupOwner", recording_owner
    )
    return owners


@pytest.mark.parametrize(
    ("descriptor", "payload_expression", "sensitive_fragment"),
    [
        (1, "b'x' * (16_384 + 1)", ""),
        (2, "b'y' * (16_384 + 1)", ""),
        (1, "b'a' * 16_383 + '密'.encode('utf-8')", "密"),
        (
            2,
            (
                "b'OPENAI_API_KEY=super-secret "
                "/private/secret-runtime/path\\n' + b'z' * 16_384"
            ),
            "OPENAI_API_KEY=super-secret",
        ),
    ],
)
def test_owned_output_overflow_is_capped_and_fails_with_fixed_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor: int,
    payload_expression: str,
    sensitive_fragment: str,
) -> None:
    completed_process_calls = 0
    original_completed_process = _OwnedProcessGroupOwner.completed_process

    def recording_completed_process(
        self: _OwnedProcessGroupOwner,
        command: Sequence[str],
        returncode: int,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal completed_process_calls
        completed_process_calls += 1
        return original_completed_process(self, command, returncode)

    monkeypatch.setattr(
        _OwnedProcessGroupOwner, "completed_process", recording_completed_process
    )
    owners = _record_owned_group_owners(monkeypatch)

    with pytest.raises(
        RuntimeError,
        match="live verifier output exceeded its fixed per-stream capture limit",
    ) as captured:
        run_owned_process_group(
            [
                sys.executable,
                "-c",
                f"import os; os.write({descriptor}, {payload_expression})",
            ],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

    assert str(captured.value) == (
        "live verifier output exceeded its fixed per-stream capture limit"
    )
    if sensitive_fragment:
        assert sensitive_fragment not in repr(captured.value)
    assert "/private/secret-runtime/path" not in repr(captured.value)
    assert completed_process_calls == 0
    assert len(owners) == 1
    assert len(owners[0].stdout_buffer) <= 16_384
    assert len(owners[0].stderr_buffer) <= 16_384
    assert owners[0].stdout_overflow is (descriptor == 1)
    assert owners[0].stderr_overflow is (descriptor == 2)
    assert owners[0].process is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(owners[0].process.pid, os.WNOHANG)


def test_owned_continuous_output_timeout_retains_timeout_and_reports_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owners = _record_owned_group_owners(monkeypatch)
    program = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "os.write(1, b'x'*65_536); chunk=b'y'*4_096; "
        "exec('while True:\\n os.write(1, chunk)\\n time.sleep(0.001)')"
    )

    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
        )

    assert str(captured.value) == "live verifier exceeded its outer timeout"
    assert isinstance(captured.value.__cause__, OwnedProcessGroupOutputOverflow)
    assert str(captured.value.__cause__) == (
        "live verifier output exceeded its fixed per-stream capture limit"
    )
    assert len(owners) == 1
    assert len(owners[0].stdout_buffer) <= 16_384
    assert len(owners[0].stderr_buffer) <= 16_384
    assert owners[0].process is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(owners[0].process.pid, os.WNOHANG)


def test_owned_output_capture_is_bounded_when_detached_descendant_holds_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descendant_pid_path = tmp_path / "detached-pipe-holder.pid"
    descendant_program = "import time; time.sleep(2)"
    command_program = "\n".join(
        (
            "import pathlib, subprocess, sys, time",
            (
                "child = subprocess.Popen([sys.executable, '-c', "
                f"{descendant_program!r}], start_new_session=True)"
            ),
            f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(child.pid))",
            "print('output before detached holder', flush=True)",
        )
    )
    started_at = time.monotonic()
    descendant_pid: int | None = None
    started_processes: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording_popen)

    try:
        try:
            result = run_owned_process_group(
                [sys.executable, "-c", command_program],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=0.1,
            )
        except AssertionError as error:
            assert "output pipes remained open" in str(error)
        else:
            assert result.stdout == "output before detached holder\n"

        assert time.monotonic() - started_at < 0.75
        assert len(started_processes) == 1
        assert started_processes[0].stdout is not None
        assert started_processes[0].stdout.closed
        assert started_processes[0].stderr is not None
        assert started_processes[0].stderr.closed
    finally:
        if descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None and _process_state(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("group_gone_errno", [errno.EPERM, errno.ESRCH])
def test_owned_group_gone_reaps_post_signal_anchor_without_test_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    group_gone_errno: int,
) -> None:
    original_killpg = os.killpg
    anchor_pid: int | None = None

    def kill_then_raise(process_group_id: int, sent_signal: signal.Signals) -> None:
        nonlocal anchor_pid
        anchor_pid = process_group_id
        original_killpg(process_group_id, sent_signal)
        deadline = time.monotonic() + 1
        while True:
            state = _process_state(process_group_id)
            if not state or state.startswith("Z"):
                break
            if time.monotonic() >= deadline:
                raise AssertionError("anchor did not exit after injected SIGKILL")
            time.sleep(0.001)
        raise RuntimeError("injected post-signal callback failure")

    def group_gone(*args: object, **kwargs: object) -> None:
        raise OSError(group_gone_errno, "injected group-gone fallback")

    monkeypatch.setitem(
        run_owned_process_group.__globals__, "_EXACT_GROUP_KILL", group_gone
    )

    with pytest.raises(RuntimeError, match="post-signal callback failure"):
        run_owned_process_group(
            [sys.executable, "-c", "raise SystemExit(0)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=1,
            term_grace_seconds=0.05,
            kill_grace_seconds=1,
            kill_group=kill_then_raise,
        )

    assert anchor_pid is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(anchor_pid, os.WNOHANG)


def test_owned_pre_signal_group_gone_error_retires_authority_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_killpg = os.killpg
    original_popen = subprocess.Popen
    started_processes: list[subprocess.Popen[bytes]] = []
    callback_calls = 0
    fallback_calls = 0

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and str(OWNED_PROCESS_GROUP_SUPERVISOR) in command:
            started_processes.append(process)
        return process

    def fail_before_signal(process_group_id: int, sent_signal: signal.Signals) -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise RuntimeError("injected pre-signal callback failure")

    def artificial_group_gone(*args: object, **kwargs: object) -> None:
        nonlocal fallback_calls
        fallback_calls += 1
        raise PermissionError(errno.EPERM, "injected pre-signal EPERM")

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setitem(
        run_owned_process_group.__globals__,
        "_EXACT_GROUP_KILL",
        artificial_group_gone,
    )

    try:
        with pytest.raises(RuntimeError, match="pre-signal callback failure"):
            run_owned_process_group(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd=tmp_path,
                env={"PATH": os.environ["PATH"]},
                timeout_seconds=1,
                term_grace_seconds=0.05,
                kill_grace_seconds=0.05,
                kill_group=fail_before_signal,
            )

        assert callback_calls == 1
        assert fallback_calls == 1
        assert len(started_processes) == 1
        process = started_processes[0]
        assert process.returncode is None
        state = _process_state(process.pid)
        assert state and not state.startswith("Z")
    finally:
        for process in started_processes:
            if process.returncode is None:
                original_killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=1)
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


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
            original_close_status_writer(descriptor)
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


def test_owned_process_group_never_probes_or_reaps_live_numeric_authority() -> None:
    owner_source = inspect.getsource(_OwnedProcessGroupOwner)
    anchored_phase_source = owner_source.split(
        "    def _bounded_reap_and_close", maxsplit=1
    )[0]

    assert '"/bin/ps"' not in owner_source
    assert "subprocess.run(" not in owner_source
    assert "waitid(" not in anchored_phase_source
    for forbidden in ("process.wait(", "waitpid(", ".poll("):
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

    with pytest.raises(OwnedProcessGroupTimeout) as captured:
        run_owned_process_group(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout_seconds=0.05,
            term_grace_seconds=0.01,
            kill_grace_seconds=1,
            kill_group=kill_group,
        )

    assert isinstance(captured.value.__cause__, PermissionError)
    assert "injected" in str(captured.value.__cause__)
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


def test_owned_process_group_preserves_small_utf8_output_and_child_status(
    tmp_path: Path,
) -> None:
    result = run_owned_process_group(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "os.write(1, '标准输出🙂\\n'.encode()); "
                "os.write(2, '标准错误€\\n'.encode()); "
                "raise SystemExit(23)"
            ),
        ],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        timeout_seconds=2,
        term_grace_seconds=0.1,
        kill_grace_seconds=1,
    )

    assert result.returncode == 23
    assert result.stdout == "标准输出🙂\n"
    assert result.stderr == "标准错误€\n"


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


def test_package_and_container_expose_current_gateway_delivery_commands() -> None:
    """Legacy company UI is retired; validate the supported build and server."""
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == "tsc --noEmit && vite build"
    assert package["scripts"]["test"] == "vitest run"
    dockerfile = (FRONTEND / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["node", "server/gatewayServer.mjs"]' in dockerfile
    assert (FRONTEND / "server" / "gatewayServer.test.mjs").is_file()
    routes = (FRONTEND / "src" / "app" / "GatewayRoutes.tsx").read_text()
    assert "GatewayConversationPanel" in routes


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
