"""Local environment loading.

Reads ``backend/.env`` (gitignored, never committed) into ``os.environ``
without overriding variables that are already set.  Call once at process
entry points (FastAPI app, CLI scripts) so real credentials like
``GILDATA_TOKEN`` live in one local file instead of shell exports.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_local_env() -> None:
    """Load ``backend/.env`` if it exists; existing env vars win."""
    load_dotenv(ENV_PATH, override=False)
