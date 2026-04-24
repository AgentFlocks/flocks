#!/usr/bin/env bash
# 本地开发脚本：支持单独启动前端、后端，或同时启动。

set -euo pipefail
set -m

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
BACKEND_PID=""
BACKEND_PGID=""
CLEANUP_DONE=0

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

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

process_group_id() {
    local pid="$1"
    ps -o pgid= -p "${pid}" 2>/dev/null | tr -d '[:space:]'
}

collect_descendant_pids() {
    local pid="$1"
    local children=""
    local child_pid=""

    if command_exists pgrep; then
        children="$(pgrep -P "${pid}" 2>/dev/null || true)"
    else
        children="$(ps -eo pid=,ppid= | awk -v target_ppid="${pid}" '$2 == target_ppid { print $1 }')"
    fi

    if [ -z "${children}" ]; then
        return
    fi

    while IFS= read -r child_pid; do
        if [ -z "${child_pid}" ]; then
            continue
        fi
        collect_descendant_pids "${child_pid}"
        printf '%s\n' "${child_pid}"
    done <<EOF
${children}
EOF
}

collect_process_group_pids() {
    local pgid="$1"

    if [ -z "${pgid}" ]; then
        return
    fi

    if command_exists pgrep; then
        pgrep -g "${pgid}" 2>/dev/null || true
        return
    fi

    ps -eo pid=,pgid= | awk -v target_pgid="${pgid}" '$2 == target_pgid { print $1 }'
}

stop_process_tree() {
    local pid="$1"
    local label="$2"
    local child_pid=""
    local current_pid=""
    local -a kill_targets=()
    local -a remaining=()

    if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
        return
    fi

    while IFS= read -r child_pid; do
        if [ -n "${child_pid}" ]; then
            kill_targets+=("${child_pid}")
        fi
    done <<EOF
$(collect_descendant_pids "${pid}")
EOF
    kill_targets+=("${pid}")

    kill -TERM "${kill_targets[@]}" 2>/dev/null || true

    for _ in 1 2 3 4 5; do
        remaining=()
        for current_pid in "${kill_targets[@]}"; do
            if kill -0 "${current_pid}" 2>/dev/null; then
                remaining+=("${current_pid}")
            fi
        done
        if [ "${#remaining[@]}" -eq 0 ]; then
            wait "${pid}" 2>/dev/null || true
            return
        fi
        sleep 1
    done

    echo -e "${YELLOW}${label} 未在预期时间内退出，强制终止...${NC}"
    kill -KILL "${remaining[@]}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
}

stop_process_group() {
    local pgid="$1"
    local label="$2"
    local current_pid=""
    local member_pid=""
    local -a remaining=()

    if [ -z "${pgid}" ]; then
        return 1
    fi

    while IFS= read -r member_pid; do
        if [ -n "${member_pid}" ]; then
            remaining+=("${member_pid}")
        fi
    done <<EOF
$(collect_process_group_pids "${pgid}")
EOF

    if [ "${#remaining[@]}" -eq 0 ]; then
        return 1
    fi

    kill -TERM -- "-${pgid}" 2>/dev/null || true

    for _ in 1 2 3 4 5; do
        remaining=()
        while IFS= read -r current_pid; do
            if [ -n "${current_pid}" ] && kill -0 "${current_pid}" 2>/dev/null; then
                remaining+=("${current_pid}")
            fi
        done <<EOF
$(collect_process_group_pids "${pgid}")
EOF
        if [ "${#remaining[@]}" -eq 0 ]; then
            return 0
        fi
        sleep 1
    done

    echo -e "${YELLOW}${label} 未在预期时间内退出，强制终止...${NC}"
    kill -KILL -- "-${pgid}" 2>/dev/null || true
    return 0
}

cleanup() {
    local shell_pgid=""

    if [ "${CLEANUP_DONE}" -eq 1 ]; then
        return
    fi
    CLEANUP_DONE=1

    if [ -n "${BACKEND_PID}" ]; then
        echo -e "${YELLOW}\n🛑 停止后端服务...${NC}"
        shell_pgid="$(process_group_id "$$")"
        if [ -n "${BACKEND_PGID}" ] && [ "${BACKEND_PGID}" != "${shell_pgid}" ]; then
            stop_process_group "${BACKEND_PGID}" "后端服务" || stop_process_tree "${BACKEND_PID}" "后端服务"
        else
            stop_process_tree "${BACKEND_PID}" "后端服务"
        fi
    fi
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
    BACKEND_PGID="$(process_group_id "${BACKEND_PID}")"
    trap cleanup EXIT

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