"""Dump the FastAPI OpenAPI spec to frontend/openapi.json.

Run from the repo root after backend schema changes so the frontend contract
can be regenerated and drift-checked:
    backend/.venv/bin/python backend/scripts/dump_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def main() -> None:
    spec = app.openapi()
    out = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
