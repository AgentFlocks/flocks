#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/flocks}"
BACKEND_HOST="${FLOCKS_BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${FLOCKS_BACKEND_PORT:-8000}"
FRONTEND_HOST="${FLOCKS_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FLOCKS_FRONTEND_PORT:-5173}"
FRONTEND_DIST_DIR="${FLOCKS_FRONTEND_DIST_DIR:-$ROOT_DIR/webui/dist}"
FRONTEND_PROXY_TARGET="${FLOCKS_FRONTEND_PROXY_TARGET:-http://127.0.0.1:${BACKEND_PORT}}"

backend_pid=""
frontend_pid=""
ready_hint_pid=""

cleanup() {
  local pid
  for pid in "$backend_pid" "$frontend_pid" "$ready_hint_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done

  for pid in "$backend_pid" "$frontend_pid" "$ready_hint_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup EXIT

cd "$ROOT_DIR"
[[ -f "$FRONTEND_DIST_DIR/index.html" ]] || {
  printf '[flocks] error: WebUI 构建产物不存在: %s\n' "$FRONTEND_DIST_DIR/index.html" >&2
  exit 1
}
[[ -f "$ROOT_DIR/scripts/serve_webui.py" ]] || {
  printf '[flocks] error: 缺少前端静态服务脚本: %s\n' "$ROOT_DIR/scripts/serve_webui.py" >&2
  exit 1
}

"$ROOT_DIR/scripts/run_legacy_task_migration.sh" start || true

printf '[flocks] publish ports to access from host: -p %s:%s -p %s:%s\n' \
  "$BACKEND_PORT" "$BACKEND_PORT" "$FRONTEND_PORT" "$FRONTEND_PORT"

printf '[flocks] starting backend on %s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"
python -m uvicorn flocks.server.app:app \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT" &
backend_pid=$!

printf '[flocks] starting frontend on %s:%s (proxy -> %s)\n' \
  "$FRONTEND_HOST" "$FRONTEND_PORT" "$FRONTEND_PROXY_TARGET"
( exec python "$ROOT_DIR/scripts/serve_webui.py" \
    --directory "$FRONTEND_DIST_DIR" \
    --host "$FRONTEND_HOST" \
    --port "$FRONTEND_PORT" \
    --proxy-target "$FRONTEND_PROXY_TARGET" ) &
frontend_pid=$!

(
  while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    if python - "$BACKEND_PORT" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
url = f"http://127.0.0.1:{port}/api/health"

try:
    with urllib.request.urlopen(url, timeout=1) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
    then
      printf '[flocks] open WebUI in your browser: http://127.0.0.1:%s\n' "$FRONTEND_PORT"
      exit 0
    fi

    sleep 1
  done
) &
ready_hint_pid=$!

wait -n "$backend_pid" "$frontend_pid"
exit $?
