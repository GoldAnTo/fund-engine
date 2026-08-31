"""Keep an owned POSIX process group anchored until its outer owner reaps it."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Sequence

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def _wait_for_group_owner() -> None:
    while True:
        signal.pause()


def supervise(command: Sequence[str], *, status_fd: int) -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, _SHUTDOWN_SIGNALS)
    if not command:
        os.write(status_fd, b"127\n")
        _wait_for_group_owner()

    try:
        child_pid = os.posix_spawnp(
            command[0],
            command,
            os.environ,
            file_actions=((os.POSIX_SPAWN_CLOSE, status_fd),),
            setsigdef=_SHUTDOWN_SIGNALS,
            setsigmask=(),
        )
    except OSError:
        os.write(status_fd, b"127\n")
        _wait_for_group_owner()

    _, wait_status = os.waitpid(child_pid, 0)
    returncode = os.waitstatus_to_exitcode(wait_status)
    os.write(status_fd, f"{returncode}\n".encode("ascii"))
    _wait_for_group_owner()


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] != "--status-fd" or sys.argv[3] != "--":
        raise SystemExit(127)
    supervise(sys.argv[4:], status_fd=int(sys.argv[2]))
