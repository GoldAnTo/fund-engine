"""Dump the FastAPI OpenAPI spec to clients/research/openapi.json.

Run from the repo root after backend schema changes so the research client contract
can be regenerated and drift-checked:
    backend/.venv/bin/python backend/scripts/dump_openapi.py

Pass ``--output PATH`` to write a disposable or alternate contract without
touching the research client's default contract file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
# The shared virtual environment may have a different worktree installed as
# ``app``.  Prefer the repository containing this script so generated contracts
# always describe the code being committed.
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402


DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "clients" / "research" / "openapi.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    spec = app.openapi()
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
