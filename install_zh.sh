#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

REPO_SLUG="${FLOCKS_REPO_SLUG:-flocks/flocks}"
DEFAULT_BRANCH="${FLOCKS_DEFAULT_BRANCH:-main}"
VERSION="${VERSION:-$DEFAULT_BRANCH}"
INSTALL_TUI=0
TMP_DIR=""
DEFAULT_INSTALL_DIR="${PWD%/}/flocks"
INSTALL_DIR="${FLOCKS_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
RAW_INSTALL_ZH_SH_URL="${FLOCKS_RAW_INSTALL_ZH_SH_URL:-https://gitee.com/flocks/flocks/raw/main/install_zh.sh}"
RAW_INSTALL_ZH_PS1_URL="${FLOCKS_RAW_INSTALL_ZH_PS1_URL:-https://gitee.com/flocks/flocks/raw/main/install_zh.ps1}"

info() {
  printf '[flocks-bootstrap-zh] %s\n' "$1"
}

fail() {
  printf '[flocks-bootstrap-zh] 错误: %s\n' "$1" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}

print_usage() {
  cat <<EOF
用法: install_zh.sh [--with-tui] [--version <标签或分支>]

Flocks 中国用户一键安装脚本。
该脚本会从 Gitee 下载仓库源码压缩包，解压到临时目录后复制到持久化安装目录，
然后转交 scripts/install_zh.sh 继续安装。默认会在当前工作目录下创建 "flocks" 子目录。

远程使用：
  curl -fsSL $RAW_INSTALL_ZH_SH_URL | bash
  curl -fsSL $RAW_INSTALL_ZH_SH_URL | bash -s -- --with-tui
  powershell -c "irm $RAW_INSTALL_ZH_PS1_URL | iex"

选项：
  --with-tui, -t         同时安装 TUI 依赖。
  --version <value>      指定标签或分支。默认值: $DEFAULT_BRANCH
  --help, -h             显示帮助信息。

环境变量：
  VERSION                等同于 --version。
  FLOCKS_INSTALL_DIR     指定持久化安装目录。默认值: $INSTALL_DIR
  FLOCKS_REPO_SLUG       指定 Gitee 仓库，例如 owner/repo。
  FLOCKS_DEFAULT_BRANCH  指定默认分支。默认值: $DEFAULT_BRANCH
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-tui|-t)
        INSTALL_TUI=1
        ;;
      --version)
        [[ $# -ge 2 ]] || fail "--version 需要提供取值。"
        VERSION="$2"
        shift
        ;;
      --help|-h)
        print_usage
        exit 0
        ;;
      *)
        print_usage
        fail "不支持的参数: $1"
        ;;
    esac
    shift
  done
}

ensure_dependencies() {
  has_cmd curl || fail "缺少 curl，无法下载 Gitee 源码压缩包。"
  has_cmd unzip || fail "缺少 unzip，无法解压 Gitee zip 源码包。"
  has_cmd bash || fail "缺少 bash，无法执行仓库内安装脚本。"
}

build_candidate_urls() {
  printf 'https://gitee.com/%s/archive/refs/tags/%s.zip\n' "$REPO_SLUG" "$VERSION"
}

download_archive() {
  local archive_path="$1"
  local last_error=""
  local url=""

  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    info "尝试下载源码压缩包: $url"
    if curl -fsSL "$url" -o "$archive_path"; then
      printf '%s' "$url"
      return 0
    fi
    last_error="$url"
  done < <(build_candidate_urls)

  fail "无法从 Gitee 下载版本 \"$VERSION\" 的源码压缩包。最后尝试的地址: ${last_error:-<none>}"
}

resolve_project_dir() {
  local candidate=""
  for candidate in "$TMP_DIR"/*; do
    [[ -d "$candidate" ]] || continue
    if [[ -f "$candidate/scripts/install_zh.sh" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}

main() {
  local archive_path download_url project_dir install_parent

  trap cleanup EXIT
  parse_args "$@"
  ensure_dependencies

  TMP_DIR="$(mktemp -d)"
  archive_path="$TMP_DIR/flocks.zip"

  info "仓库: $REPO_SLUG"
  info "版本: $VERSION"
  info "临时目录: $TMP_DIR"
  download_url="$(download_archive "$archive_path")"

  info "正在解压源码压缩包..."
  unzip -q "$archive_path" -d "$TMP_DIR"

  project_dir="$(resolve_project_dir)" || fail "压缩包已解压，但未找到 scripts/install_zh.sh。"

  install_parent="$(dirname "$INSTALL_DIR")"
  mkdir -p "$install_parent"
  rm -rf "$INSTALL_DIR"
  cp -R "$project_dir" "$INSTALL_DIR"

  info "下载来源: $download_url"
  info "安装目录: $INSTALL_DIR"
  info "开始执行: $INSTALL_DIR/scripts/install_zh.sh"

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    bash "$INSTALL_DIR/scripts/install_zh.sh" --with-tui
  else
    bash "$INSTALL_DIR/scripts/install_zh.sh"
  fi
}

main "$@"
