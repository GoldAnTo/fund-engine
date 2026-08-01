#!/usr/bin/env bash
# Reproduce the AI-compute evidence-pack release gate end to end.
# Fail-closed: any missing dataset/manifest or failed check exits non-zero.
set -euo pipefail

cd "$(dirname "$0")/../../backend"
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=python
fi

exec "$PY" scripts/verify_ai_compute_slice.py
