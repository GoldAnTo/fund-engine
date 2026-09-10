#!/usr/bin/env bash
# Reproduce the AI-compute evidence-pack release gate end to end.
# Fail-closed: any missing dataset/manifest or failed check exits non-zero.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT/backend"
PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=.venv/bin/python
fi
if [ ! -x "$PY" ]; then
  PY=python
fi

# Both verification scripts must inspect the same fresh ledger.  The first
# gate intentionally recreates its database; the complete semiconductor case
# is then seeded before its own integrity verifier runs.
RELEASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fund-engine-release.XXXXXX")"
RELEASE_DB="$RELEASE_DIR/ledger.db"
trap 'rm -rf "$RELEASE_DIR"' EXIT
export DATABASE_URL="sqlite:///$RELEASE_DB"

"$PY" scripts/verify_ai_compute_slice.py
"$PY" -c '
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.ledger import Base
from app.scripts.seed_semiconductor_complete_theme_case import seed
engine = create_engine(os.environ["DATABASE_URL"], future=True)
Base.metadata.create_all(engine)
with sessionmaker(bind=engine, future=True)() as session:
    seed(session)
    session.commit()
'
"$PY" scripts/verify_semiconductor_complete_case.py
