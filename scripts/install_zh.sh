#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RAW_INSTALL_ZH_SH_URL="${FLOCKS_RAW_INSTALL_ZH_SH_URL:-https://gitee.com/flocks/flocks/raw/main/install_zh.sh}"
RAW_INSTALL_ZH_PS1_URL="${FLOCKS_RAW_INSTALL_ZH_PS1_URL:-https://gitee.com/flocks/flocks/raw/main/install_zh.ps1}"

print_usage() {
  cat <<EOF
用法: ./scripts/install_zh.sh [--with-tui]

Flocks 中国用户源码安装脚本。
该脚本会默认使用国内软件源，并转交 ./scripts/install.sh 执行实际安装流程。

默认软件源：
  PyPI: https://mirrors.aliyun.com/pypi/simple
  npm : https://registry.npmmirror.com/
  uv  : https://astral.org.cn/uv/install.sh
  uv 备用源: https://uv.agentsmirror.com/install-cn.sh

一键安装入口：
  curl -fsSL $RAW_INSTALL_ZH_SH_URL | bash
  curl -fsSL $RAW_INSTALL_ZH_SH_URL | bash -s -- --with-tui
  powershell -c "irm $RAW_INSTALL_ZH_PS1_URL | iex"

选项：
  --with-tui, -t  同时安装 TUI 依赖
  --help, -h      显示帮助信息
EOF
}

configure_cn_environment() {
  export FLOCKS_INSTALL_LANGUAGE="${FLOCKS_INSTALL_LANGUAGE:-zh-CN}"
  export FLOCKS_INSTALL_REPO_URL="${FLOCKS_INSTALL_REPO_URL:-https://gitee.com/flocks/flocks.git}"
  export FLOCKS_RAW_INSTALL_SH_URL="${FLOCKS_RAW_INSTALL_SH_URL:-$RAW_INSTALL_ZH_SH_URL}"
  export FLOCKS_RAW_INSTALL_PS1_URL="${FLOCKS_RAW_INSTALL_PS1_URL:-$RAW_INSTALL_ZH_PS1_URL}"
  export FLOCKS_UV_DEFAULT_INDEX="${FLOCKS_UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
  export FLOCKS_UV_INSTALL_SH_URL="${FLOCKS_UV_INSTALL_SH_URL:-https://astral.org.cn/uv/install.sh}"
  export FLOCKS_UV_INSTALL_SH_FALLBACK_URL="${FLOCKS_UV_INSTALL_SH_FALLBACK_URL:-https://uv.agentsmirror.com/install-cn.sh}"
  export FLOCKS_UV_INSTALL_PS1_URL="${FLOCKS_UV_INSTALL_PS1_URL:-https://astral.org.cn/uv/install.ps1}"
  export FLOCKS_NPM_REGISTRY="${FLOCKS_NPM_REGISTRY:-https://registry.npmmirror.com/}"
  export PUPPETEER_CHROME_DOWNLOAD_BASE_URL="${PUPPETEER_CHROME_DOWNLOAD_BASE_URL:-https://cdn.npmmirror.com/binaries/chrome-for-testing}"
  export FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL="${FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL:-https://nodejs.org/zh-cn/download}"
}

main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    print_usage
    exit 0
  fi

  configure_cn_environment
  exec bash "$SCRIPT_DIR/install.sh" "$@"
}

main "$@"
