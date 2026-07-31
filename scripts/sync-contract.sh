#!/usr/bin/env bash
# Single-command contract gate: dump backend OpenAPI -> regenerate TypeScript
# contract -> fail if the committed contract drifted.
#
# Run from the repo root:
#   bash scripts/sync-contract.sh
#
# Exits non-zero if openapi.json or src/contracts/v1.ts would change, so it can
# be used as a CI gate. Pass --update to write the updated files instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${1:-check}"
PY="backend/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "backend venv not found at $PY" >&2
  exit 1
fi

"$PY" backend/scripts/dump_openapi.py
( cd frontend && npx openapi-typescript openapi.json -o src/contracts/v1.ts )

if [ "$MODE" = "--update" ]; then
  echo "contract updated."
  exit 0
fi

if ! git diff --exit-code -- frontend/openapi.json frontend/src/contracts/v1.ts; then
  echo "" >&2
  echo "ERROR: frontend contract is out of sync with the backend OpenAPI spec." >&2
  echo "Run 'bash scripts/sync-contract.sh --update' and commit the result." >&2
  exit 1
fi

echo "contract is in sync."
