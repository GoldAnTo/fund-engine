"""Keep an owned POSIX process group anchored until its outer owner reaps it."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Sequence

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def _mirror_child_returncode(returncode: int) -> int:
    if returncode >= 0:
        return returncode
    signal_number = -returncode
    signal.signal(signal_number, signal.SIG_DFL)
    os.kill(os.getpid(), signal_number)
    return 128 + signal_number


def supervise(command: Sequence[str]) -> int:
    if not command:
        return 127

    shutdown_requested = False

    def record_shutdown(_signal_number: int, _frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True

    for signal_number in _SHUTDOWN_SIGNALS:
        signal.signal(signal_number, record_shutdown)

    try:
        child_pid = os.posix_spawnp(
            command[0], command, os.environ, setsigdef=_SHUTDOWN_SIGNALS
        )
    except OSError:
        return 127

    _, wait_status = os.waitpid(child_pid, 0)
    returncode = os.waitstatus_to_exitcode(wait_status)
    if shutdown_requested:
        while True:
            signal.pause()
    return _mirror_child_returncode(returncode)


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1:]))
