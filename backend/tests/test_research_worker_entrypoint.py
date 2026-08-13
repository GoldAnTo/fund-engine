from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_worker_loads_local_env_before_database_import(tmp_path: Path) -> None:
    database_path = tmp_path / "worker.db"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"DATABASE_URL=sqlite:///{database_path}\n",
        encoding="utf-8",
    )
    backend = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment["APP_ENV"] = "development"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from app import env; "
                f"env.ENV_PATH = Path({str(env_path)!r}); "
                "from app.scripts import run_research_worker; "
                "from app.db import engine; "
                "print(engine.url)"
            ),
        ],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"sqlite:///{database_path}"
