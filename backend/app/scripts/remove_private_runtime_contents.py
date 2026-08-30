#!/usr/bin/env python3
"""Delete contents through an inherited private-directory fd only.

The caller excludes concurrent same-UID mutation while the held directory fd is
being traversed; this helper never follows symlinks and never removes the root.
"""

import os
import stat
import sys


def fail(message):
    raise RuntimeError(message)


def assert_identity(fd, expected_dev, expected_ino, expected_uid, expected_mode):
    current = os.fstat(fd)
    if (not stat.S_ISDIR(current.st_mode)
            or current.st_dev != expected_dev
            or current.st_ino != expected_ino
            or current.st_uid != expected_uid
            or stat.S_IMODE(current.st_mode) != expected_mode):
        fail("private runtime identity changed")


def remove_contents(fd):
    for name in os.listdir(fd):
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            try:
                opened = os.fstat(child_fd)
                if opened.st_dev != entry.st_dev or opened.st_ino != entry.st_ino:
                    fail("private runtime entry identity changed")
                remove_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


def main(argv):
    if argv[1:] == ["--self-test"]:
        return
    if len(argv) != 6:
        fail("usage: remove_private_runtime_contents.py FD DEV INO UID MODE")
    fd, dev, ino, uid, mode = (int(value, 10) for value in argv[1:])
    assert_identity(fd, dev, ino, uid, mode)
    remove_contents(fd)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as error:  # stdout/stderr are controlled by the caller.
        print(str(error), file=sys.stderr)
        sys.exit(1)
