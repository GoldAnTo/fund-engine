#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=${COMPOSE_ENV_FILE:-"$ROOT_DIR/.env.compose"}
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
export COMPOSE_PROJECT_NAME="fund-engine-live-${RUN_STAMP}-$$-$RANDOM"
ARTIFACT_DIR=${LIVE_ARTIFACT_DIR:-"$ROOT_DIR/artifacts/live-acceptance/$COMPOSE_PROJECT_NAME"}
CONTROL_PORT=${LIVE_CONTROL_PORT:-$((20000 + ($$ % 20000)))}
export LIVE_CONTROL_PORT=$CONTROL_PORT
export LIVE_CONTROL_TOKEN=${LIVE_CONTROL_TOKEN:-$(openssl rand -hex 32)}
export LIVE_CONTROL_URL="http://127.0.0.1:$CONTROL_PORT"
export COMPOSE_ENV_FILE=$ENV_FILE
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.live.yml")
CONTROL_PID=
TEST_STATUS=1

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE; copy .env.compose.example and configure real providers" >&2
  exit 2
fi
if [[ ! "$COMPOSE_PROJECT_NAME" =~ ^fund-engine-live-[A-Za-z0-9_-]+$ ]]; then
  echo "refusing non-ephemeral COMPOSE_PROJECT_NAME: $COMPOSE_PROJECT_NAME" >&2
  exit 2
fi

env_value() {
  local key=$1
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

collect_evidence() {
  local failed=0
  mkdir -p "$ARTIFACT_DIR" || return 1
  "${COMPOSE[@]}" ps --all >"$ARTIFACT_DIR/compose-ps.txt" 2>&1 || failed=1
  "${COMPOSE[@]}" images --format json >"$ARTIFACT_DIR/compose-images.jsonl" 2>&1 || failed=1
  "${COMPOSE[@]}" logs --no-color >"$ARTIFACT_DIR/compose.log" 2>&1 || failed=1
  if [[ -d "$ROOT_DIR/frontend/test-results/live" ]]; then
    cp -R "$ROOT_DIR/frontend/test-results/live" "$ARTIFACT_DIR/playwright-test-results" || failed=1
  fi
  if [[ -d "$ROOT_DIR/frontend/playwright-report/live" ]]; then
    cp -R "$ROOT_DIR/frontend/playwright-report/live" "$ARTIFACT_DIR/playwright-html-report" || failed=1
  fi
  {
    echo "# Live mainline verification"
    echo
    echo "- Compose project: \`$COMPOSE_PROJECT_NAME\`"
    echo "- Started: \`$RUN_STAMP\`"
    echo "- Result: \`$([[ $TEST_STATUS == 0 ]] && echo passed || echo failed)\`"
    echo "- Browser artifacts: \`$ARTIFACT_DIR/playwright-test-results\` and \`$ARTIFACT_DIR/playwright-html-report\`"
    echo "- Runtime artifacts: \`$ARTIFACT_DIR\`"
    echo
    echo "Case, orchestration, research-run, query-plan, adapter, restart timestamps, hashes, and citation IDs are recorded by the redacted Playwright attachments."
    echo "Compose logs, traces, and videos are private raw diagnostics and must be reviewed before sharing."
  } >"$ARTIFACT_DIR/verification.md" || failed=1
  return "$failed"
}

cleanup() {
  local original_status=$? evidence_status control_status=0 control_exit=0 teardown_status final_status summary_status=0
  trap - EXIT
  set +e
  TEST_STATUS=$original_status
  collect_evidence
  evidence_status=$?
  if [[ -n "$CONTROL_PID" ]]; then
    if kill -0 "$CONTROL_PID" 2>/dev/null; then
      kill "$CONTROL_PID" 2>/dev/null || control_status=1
      wait "$CONTROL_PID" 2>/dev/null
      control_exit=$?
      if (( control_exit != 0 )); then
        control_status=1
      fi
    else
      wait "$CONTROL_PID" 2>/dev/null
      control_status=1
    fi
  fi
  bash "$ROOT_DIR/scripts/research-stack.sh" down --volumes
  teardown_status=$?
  final_status=$original_status
  if (( evidence_status != 0 || control_status != 0 || teardown_status != 0 )); then
    final_status=1
  fi
  if [[ -d "$ARTIFACT_DIR" ]]; then
    {
      echo
      echo "- Evidence collection status: \`$evidence_status\`"
      echo "- Control shutdown status: \`$control_status\`"
      echo "- Compose teardown status: \`$teardown_status\`"
      echo "- Final runner status: \`$final_status\`"
    } >>"$ARTIFACT_DIR/verification.md" 2>/dev/null || summary_status=1
  else
    summary_status=1
  fi
  if (( summary_status != 0 )); then
    final_status=1
  fi
  exit "$final_status"
}
trap cleanup EXIT

export LIVE_BASE_URL=${LIVE_BASE_URL:-http://127.0.0.1:8080}
export LIVE_KEYCLOAK_USER=${LIVE_KEYCLOAK_USER:-$(env_value LIVE_KEYCLOAK_USER)}
export LIVE_KEYCLOAK_PASSWORD=${LIVE_KEYCLOAK_PASSWORD:-$(env_value LIVE_KEYCLOAK_PASSWORD)}
export LIVE_SECOND_KEYCLOAK_USER=${LIVE_SECOND_KEYCLOAK_USER:-$(env_value LIVE_SECOND_KEYCLOAK_USER)}
export LIVE_SECOND_KEYCLOAK_PASSWORD=${LIVE_SECOND_KEYCLOAK_PASSWORD:-$(env_value LIVE_SECOND_KEYCLOAK_PASSWORD)}
export LIVE_OFFICIAL_ADAPTERS=${LIVE_OFFICIAL_ADAPTERS:-$(env_value ACQUISITION_ENABLED_ADAPTERS)}
export LIVE_WORKFLOW_TIMEOUT_MS=${LIVE_WORKFLOW_TIMEOUT_MS:-900000}

mkdir -p "$ARTIFACT_DIR"
bash "$ROOT_DIR/scripts/research-stack.sh" up
node "$ROOT_DIR/frontend/scripts/with-project-node.mjs" "$ROOT_DIR/scripts/live-stack-control.mjs" \
  >"$ARTIFACT_DIR/live-control.log" 2>&1 &
CONTROL_PID=$!

for _attempt in $(seq 1 60); do
  if curl --fail --silent "$LIVE_CONTROL_URL/health" >"$ARTIFACT_DIR/live-control-health.json"; then
    break
  fi
  sleep 1
done
curl --fail --silent "$LIVE_CONTROL_URL/health" >"$ARTIFACT_DIR/live-control-health.json"

set +e
npm --prefix "$ROOT_DIR/frontend" run e2e:live
TEST_STATUS=$?
set -e
exit "$TEST_STATUS"
