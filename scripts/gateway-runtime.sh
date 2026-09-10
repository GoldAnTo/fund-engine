#!/usr/bin/env bash
# Dedicated local Gateway project; never reads the legacy one-click environment.
set -euo pipefail
readonly GATEWAY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly GATEWAY_ENV_FILE="$GATEWAY_ROOT/.env.gateway.local"

case "${1:-help}" in
  help|--help|-h)
    printf '%s\n' 'Usage: scripts/gateway-runtime.sh {validate|build|up|status|down}' \
      'Configure .env.gateway.local (mode 600) first. up starts the company study scheduler and two professional workers.' \
      'Single-principal localhost deployment only; down preserves data volumes.'
    exit 0 ;;
  validate|build|up|status|down) action="$1" ;;
  *) printf '%s\n' 'Unknown Gateway runtime action' >&2; exit 2 ;;
esac
if [[ $# -ne 1 ]]; then printf '%s\n' 'Exactly one Gateway runtime action is required' >&2; exit 2; fi
python3 - "$GATEWAY_ENV_FILE" <<'PY'
import os
import stat
import sys
try:
    info = os.lstat(sys.argv[1])
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError
except (OSError, ValueError):
    raise SystemExit('Gateway environment must be an owner-controlled regular file with mode 600') from None
PY
compose=(docker compose --project-name fundclaw-gateway --project-directory "$GATEWAY_ROOT" --env-file "$GATEWAY_ENV_FILE" -f "$GATEWAY_ROOT/docker-compose.gateway.yml")
case "$action" in
  validate) "${compose[@]}" config --quiet ;;
  build) "${compose[@]}" build ;;
  up) "${compose[@]}" up -d --build --scale professional-worker=2 ;;
  status) "${compose[@]}" ps ;;
  down) "${compose[@]}" down ;;
esac
