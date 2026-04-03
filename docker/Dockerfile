
FROM node:22-bookworm

ARG VITE_API_BASE_URL=
ARG VITE_WS_BASE_URL=
ARG VITE_APP_ENV=docker
ARG VITE_APP_NAME=Flocks
ARG VITE_APP_VERSION=docker

ENV DEBIAN_FRONTEND=noninteractive \
    FLOCKS_DEPLOY_MODE=docker \
    FLOCKS_BACKEND_HOST=0.0.0.0 \
    FLOCKS_FRONTEND_HOST=0.0.0.0 \
    FLOCKS_FRONTEND_DIST_DIR=/opt/flocks/webui/dist \
    FLOCKS_ROOT=/home/flocks/.flocks \
    FLOCKS_CONFIG_DIR=/home/flocks/.flocks/config \
    HOME=/home/flocks \
    LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8 \
    PATH=/usr/local/bin:/home/flocks/.local/bin:/opt/flocks/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NO_AT_BRIDGE=1 \
    UV_INSTALL_DIR=/usr/local/bin \
    VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_WS_BASE_URL=${VITE_WS_BASE_URL} \
    VITE_APP_ENV=${VITE_APP_ENV} \
    VITE_APP_NAME=${VITE_APP_NAME} \
    VITE_APP_VERSION=${VITE_APP_VERSION}

WORKDIR /opt/flocks

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-wqy-zenhei \
    git \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libxshmfence1 \
    libxss1 \
    libasound2 \
    locales \
    lsof \
    procps \
    tini \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i 's/^# *zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen zh_CN.UTF-8 \
    && useradd --create-home --shell /bin/bash flocks \
    && chmod +x ./scripts/install.sh ./scripts/container-start.sh \
    && bash ./scripts/install.sh \
    && AGENT_BROWSER_BIN="$(npm prefix -g)/bin/agent-browser" \
    && if [ ! -x "$AGENT_BROWSER_BIN" ] && [ -x "/home/flocks/.npm-global/bin/agent-browser" ]; then \
         AGENT_BROWSER_BIN="/home/flocks/.npm-global/bin/agent-browser"; \
       fi \
    && if [ ! -x "$AGENT_BROWSER_BIN" ]; then \
         echo "agent-browser 安装后仍未找到可执行文件" >&2; \
         exit 1; \
       fi \
    && if [ "$AGENT_BROWSER_BIN" != "/usr/local/bin/agent-browser" ]; then \
         ln -sf "$AGENT_BROWSER_BIN" /usr/local/bin/agent-browser; \
       fi \
    && (cd ./webui && npm run build)

RUN mkdir -p "$HOME/.flocks" "$HOME/.flocks/config" "$HOME/.agent-browser" \
    && chown -R flocks:flocks /opt/flocks "$HOME"

EXPOSE 8000 5173

USER flocks

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "./scripts/container-start.sh"]
