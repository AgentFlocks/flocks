#!/bin/bash
# 构建前端并以生产模式启动服务（后台守护）
# 后端默认: 8000（uvicorn，无热重载，可通过 BACKEND_PORT 覆盖）
# 前端默认: 5173（vite preview 托管构建产物，代理 /api 到后端）

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_ACCESS_HOST="${BACKEND_HOST}"
if [ "${BACKEND_ACCESS_HOST}" = "0.0.0.0" ] || [ "${BACKEND_ACCESS_HOST}" = "::" ]; then
    BACKEND_ACCESS_HOST="127.0.0.1"
fi
BACKEND_BASE_URL="http://${BACKEND_ACCESS_HOST}:${BACKEND_PORT}"
BACKEND_WS_URL="ws://${BACKEND_ACCESS_HOST}:${BACKEND_PORT}"
BACKEND_HEALTH_URL="${BACKEND_HEALTH_URL:-${BACKEND_BASE_URL}/api/health}"
BACKEND_STARTUP_TIMEOUT="${BACKEND_STARTUP_TIMEOUT:-90}"
BACKEND_HEALTH_CHECK_INTERVAL="${BACKEND_HEALTH_CHECK_INTERVAL:-2}"

echo -e "${BLUE}🚀 启动 Flocks 生产环境...${NC}"

echo "🧹 清理现有进程..."
pkill -9 -f "uvicorn flocks.server.app" 2>/dev/null || true
pkill -9 -f "vite preview" 2>/dev/null || true
lsof -ti:"${BACKEND_PORT}" | xargs kill -9 2>/dev/null || true
lsof -ti:"${FRONTEND_PORT}" | xargs kill -9 2>/dev/null || true
sleep 1

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)
LOGS_DIR="${PROJECT_ROOT}/logs"
mkdir -p "$LOGS_DIR"

echo -e "${BLUE}📦 构建 WebUI 前端...${NC}"
cd webui
VITE_API_BASE_URL="${BACKEND_BASE_URL}" \
VITE_WS_BASE_URL="${BACKEND_WS_URL}" \
npm run build
cd "$PROJECT_ROOT"

if [ ! -d "webui/dist" ]; then
    echo -e "${RED}❌ 前端构建失败，webui/dist 不存在${NC}"
    exit 1
fi
echo -e "${GREEN}✓ 前端构建完成${NC}"

echo -e "${GREEN}🔧 启动后端服务（端口 ${BACKEND_PORT}）...${NC}"
source "${PROJECT_ROOT}/.venv/bin/activate"
nohup python -m uvicorn flocks.server.app:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" \
    > /tmp/flocks-backend.log 2>&1 &

BACKEND_PID=$!
echo -e "${YELLOW}Backend PID: ${BACKEND_PID}${NC}"

echo "⏳ 等待后端启动（超时 ${BACKEND_STARTUP_TIMEOUT}s）..."
BACKEND_CHECK_ATTEMPTS=$(( (BACKEND_STARTUP_TIMEOUT + BACKEND_HEALTH_CHECK_INTERVAL - 1) / BACKEND_HEALTH_CHECK_INTERVAL ))
for i in $(seq 1 "$BACKEND_CHECK_ATTEMPTS"); do
    if curl -s --max-time 2 "${BACKEND_HEALTH_URL}" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 后端服务启动成功${NC}"
        echo -e "${YELLOW}📋 后端日志: tail -f /tmp/flocks-backend.log${NC}"
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo -e "${RED}❌ 后端服务启动进程已退出，查看日志:${NC}"
        tail -20 /tmp/flocks-backend.log
        exit 1
    fi
    if [ "$i" -eq "$BACKEND_CHECK_ATTEMPTS" ]; then
        echo -e "${RED}❌ 后端服务启动失败（超时 ${BACKEND_STARTUP_TIMEOUT} 秒），查看日志:${NC}"
        tail -20 /tmp/flocks-backend.log
        kill "$BACKEND_PID" 2>/dev/null || true
        exit 1
    fi
    sleep "$BACKEND_HEALTH_CHECK_INTERVAL"
done

# 前端也用 nohup 后台启动，终端断开不会收到 SIGHUP
echo -e "${GREEN}🎨 启动 WebUI 前端（端口 ${FRONTEND_PORT}）...${NC}"
cd webui
nohup env \
    VITE_API_BASE_URL="${BACKEND_BASE_URL}" \
    VITE_WS_BASE_URL="${BACKEND_WS_URL}" \
    npm run preview -- --host 127.0.0.1 --port "${FRONTEND_PORT}" \
    > "${LOGS_DIR}/webui-preview.log" 2>&1 &
FRONTEND_PID=$!

sleep 2
if kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${GREEN}✓ 前端服务启动成功${NC}"
else
    echo -e "${RED}❌ 前端服务启动失败，查看日志: ${LOGS_DIR}/webui-preview.log${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Flocks 生产环境启动完成${NC}"
echo -e "${YELLOW}  后端 PID: ${BACKEND_PID}  日志: tail -f /tmp/flocks-backend.log${NC}"
echo -e "${YELLOW}  前端 PID: ${FRONTEND_PID}  日志: tail -f ${LOGS_DIR}/webui-preview.log${NC}"
echo -e "${YELLOW}  停止服务: kill ${BACKEND_PID} ${FRONTEND_PID}${NC}"

echo "$BACKEND_PID" > "${LOGS_DIR}/backend.pid"
echo "$FRONTEND_PID" > "${LOGS_DIR}/frontend.pid"
