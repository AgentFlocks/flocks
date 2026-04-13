#!/usr/bin/env bash
# 本地开发脚本：支持单独启动前端、后端，或同时启动。

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_ACCESS_HOST="${BACKEND_HOST}"
if [ "${BACKEND_ACCESS_HOST}" = "0.0.0.0" ] || [ "${BACKEND_ACCESS_HOST}" = "::" ]; then
    BACKEND_ACCESS_HOST="127.0.0.1"
fi

BACKEND_BASE_URL="http://${BACKEND_ACCESS_HOST}:${BACKEND_PORT}"
BACKEND_WS_URL="ws://${BACKEND_ACCESS_HOST}:${BACKEND_PORT}"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/dev.sh              同时启动后端和前端
  ./scripts/dev.sh all          同时启动后端和前端
  ./scripts/dev.sh backend      只启动后端
  ./scripts/dev.sh frontend     只启动前端

Environment variables:
  BACKEND_HOST   默认 127.0.0.1
  BACKEND_PORT   默认 8000
  FRONTEND_HOST  默认 127.0.0.1
  FRONTEND_PORT  默认 5173
EOF
}

start_backend() {
    echo -e "${GREEN}🔧 启动后端服务: http://${BACKEND_HOST}:${BACKEND_PORT}${NC}"
    cd "${PROJECT_ROOT}"
    uv run uvicorn flocks.server.app:app \
        --host "${BACKEND_HOST}" \
        --port "${BACKEND_PORT}" \
        --reload \
        --reload-dir flocks
}

start_frontend() {
    echo -e "${GREEN}🎨 启动前端服务: http://${FRONTEND_HOST}:${FRONTEND_PORT}${NC}"
    cd "${PROJECT_ROOT}/webui"
    VITE_API_BASE_URL="${BACKEND_BASE_URL}" \
    VITE_WS_BASE_URL="${BACKEND_WS_URL}" \
    npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
}

start_all() {
    echo -e "${BLUE}🚀 同时启动前后端开发环境...${NC}"
    cd "${PROJECT_ROOT}"
    uv run uvicorn flocks.server.app:app \
        --host "${BACKEND_HOST}" \
        --port "${BACKEND_PORT}" \
        --reload \
        --reload-dir flocks &

    BACKEND_PID=$!
    trap 'echo -e "${YELLOW}\n🛑 停止后端服务...${NC}"; kill "${BACKEND_PID}" 2>/dev/null || true' EXIT

    echo -e "${YELLOW}后端 PID: ${BACKEND_PID}${NC}"
    echo -e "${YELLOW}前端将连接到: ${BACKEND_BASE_URL}${NC}"

    start_frontend
}

MODE="${1:-all}"

case "${MODE}" in
    all)
        start_all
        ;;
    backend)
        start_backend
        ;;
    frontend)
        start_frontend
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo -e "${RED}不支持的模式: ${MODE}${NC}"
        usage
        exit 1
        ;;
esac