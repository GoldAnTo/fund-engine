import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).parents[1] / "app" / "scripts" / "remove_private_runtime_contents.py"


class RemovePrivateRuntimeContentsTests(unittest.TestCase):
    def run_helper(self, directory, *, ino=None):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = os.fstat(descriptor)
            return subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(HELPER),
                    str(descriptor),
                    str(current.st_dev),
                    str(current.st_ino if ino is None else ino),
                    str(current.st_uid),
                    str(stat.S_IMODE(current.st_mode)),
                ],
                check=False,
                close_fds=True,
                pass_fds=(descriptor,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={},
                stdin=subprocess.DEVNULL,
            )
        finally:
            os.close(descriptor)

    def test_removes_nested_contents_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            nested = Path(root) / "nested" / "deeper"
            nested.mkdir(parents=True)
            (nested / "owned.txt").write_text("owned")
            outside_sentinel = Path(outside) / "outside.txt"
            outside_sentinel.write_text("outside")
            os.symlink(outside, Path(root) / "outside-link")

            result = self.run_helper(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.listdir(root), [])
            self.assertEqual(outside_sentinel.read_text(), "outside")

    def test_rejects_fstat_identity_mismatch_without_deleting(self):
        with tempfile.TemporaryDirectory() as root:
            sentinel = Path(root) / "sentinel.txt"
            sentinel.write_text("preserve")

            result = self.run_helper(root, ino=0)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity changed", result.stderr)
            self.assertEqual(sentinel.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
