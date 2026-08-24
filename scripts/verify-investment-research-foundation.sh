#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${BACKEND_PYTHON:-}" ]]; then
  readonly PYTHON_BIN="$BACKEND_PYTHON"
elif [[ -x "$REPO_ROOT/backend/.venv/bin/python" ]]; then
  readonly PYTHON_BIN="$REPO_ROOT/backend/.venv/bin/python"
else
  printf 'investment research gate: set BACKEND_PYTHON or create backend/.venv\n' >&2
  exit 1
fi

(
  cd "$REPO_ROOT/backend"
  "$PYTHON_BIN" -m pytest -q \
    tests/underwriting/test_product_contracts.py \
    tests/underwriting/test_product_persistence.py \
    tests/underwriting/test_product_project.py \
    tests/underwriting/test_market_snapshots.py \
    tests/underwriting/test_workspace_draft.py \
    tests/underwriting/test_revision_publisher.py \
    tests/underwriting/test_product_api.py \
    tests/underwriting/test_product_legacy_compatibility.py \
    tests/test_acquisition_worker_recovery.py \
    tests/test_one_click_runtime_assets.py \
    tests/test_one_click_runtime_scripts.py \
    tests/test_investment_research_runtime.py
  "$PYTHON_BIN" -m compileall -q app tests
)

(
  cd "$REPO_ROOT/frontend"
  npm test -- \
    InvestmentResearchShell.test.tsx \
    NewResearchPage.test.tsx \
    InvestmentResearchApi.test.ts \
    productAccessibility.test.ts \
    researchFoundation.test.ts
  npm run typecheck
  npm run build
)

printf 'Investment research foundation gate passed.\n'
