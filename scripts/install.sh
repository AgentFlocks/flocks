#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

REPO_URL="https://github.com/AgentFlocks/Flocks.git"
RAW_INSTALL_SH_URL="https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh"
RAW_INSTALL_PS1_URL="https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1"
ROOT_DIR=""
INSTALL_TUI=0
MIN_NODE_MAJOR=22
PATH_UPDATE_REQUIRED=0
PATH_UPDATE_FILES=""
PATH_UPDATE_DIRS=""
PATH_REFRESH_HINT_REQUIRED=0

info() {
  printf '[flocks] %s\n' "$1"
}

fail() {
  printf '[flocks] error: %s\n' "$1" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

run_with_privilege() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
    return
  fi

  if has_cmd sudo; then
    sudo "$@"
    return
  fi

  fail "当前操作需要管理员权限，请使用 root 运行，或先安装并配置 sudo。"
}

append_path() {
  local path_entry="$1"
  [[ -n "$path_entry" && -d "$path_entry" ]] || return 0
  [[ ":$PATH:" == *":$path_entry:"* ]] || export PATH="$path_entry:$PATH"
}

record_path_update_file() {
  local file_path="$1"
  [[ -n "$file_path" ]] || return 0

  while IFS= read -r existing; do
    [[ -z "$existing" ]] && continue
    [[ "$existing" == "$file_path" ]] && return 0
  done <<< "$PATH_UPDATE_FILES"

  if [[ -n "$PATH_UPDATE_FILES" ]]; then
    PATH_UPDATE_FILES+=$'\n'
  fi
  PATH_UPDATE_FILES+="$file_path"
}

record_path_update_dir() {
  local dir_path="$1"
  [[ -n "$dir_path" ]] || return 0

  while IFS= read -r existing; do
    [[ -z "$existing" ]] && continue
    [[ "$existing" == "$dir_path" ]] && return 0
  done <<< "$PATH_UPDATE_DIRS"

  if [[ -n "$PATH_UPDATE_DIRS" ]]; then
    PATH_UPDATE_DIRS+=$'\n'
  fi
  PATH_UPDATE_DIRS+="$dir_path"
}

detect_shell_rc_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "$shell_name" in
    zsh)
      printf '%s' "$HOME/.zshrc"
      ;;
    *)
      printf '%s' "$HOME/.bashrc"
      ;;
  esac
}

ensure_path_persisted() {
  local path_entry="$1"
  local rc_file export_line

  [[ -n "$path_entry" && -d "$path_entry" ]] || return 0

  PATH_REFRESH_HINT_REQUIRED=1
  rc_file="$(detect_shell_rc_file)"
  mkdir -p "$(dirname "$rc_file")"
  touch "$rc_file"

  export_line="export PATH=\"$path_entry:\$PATH\""
  if ! grep -Fqs "$path_entry" "$rc_file"; then
    {
      printf '\n# Added by flocks installer\n'
      printf '%s\n' "$export_line"
    } >> "$rc_file"
    PATH_UPDATE_REQUIRED=1
    record_path_update_file "$rc_file"
    record_path_update_dir "$path_entry"
    info "已将 PATH 写入: $rc_file"
  fi
}

show_path_update_hint() {
  [[ "$PATH_REFRESH_HINT_REQUIRED" -eq 1 ]] || return 0

  local rc_file first_rc_file
  first_rc_file=""

  if [[ "$PATH_UPDATE_REQUIRED" -eq 1 ]]; then
    printf '\n[flocks] 已写入 shell 配置，请刷新当前终端环境：\n'
  else
    printf '\n[flocks] 检测到 shell 配置变更，请确认已在当前终端生效：\n'
  fi

  if [[ -n "$PATH_UPDATE_DIRS" ]]; then
    printf '\n[flocks] 已追加到 PATH 的目录：\n'
    while IFS= read -r path_entry; do
      [[ -z "$path_entry" ]] && continue
      printf '  - %s\n' "$path_entry"
    done <<< "$PATH_UPDATE_DIRS"
  fi

  if [[ -n "$PATH_UPDATE_FILES" ]]; then
    printf '\n[flocks] 请执行以下命令刷新当前终端，或直接重新打开一个新终端：\n'
    while IFS= read -r rc_file; do
      [[ -z "$rc_file" ]] && continue
      printf '  source "%s"\n' "$rc_file"
      if [[ -z "$first_rc_file" ]]; then
        first_rc_file="$rc_file"
      fi
    done <<< "$PATH_UPDATE_FILES"
  fi

  if [[ -n "$first_rc_file" ]]; then
    printf '\n[flocks] 如果 `source "%s"` 后仍未生效，请直接打开新终端再继续。\n' "$first_rc_file"
  fi
}

show_path_update_hint_inline() {
  [[ "$PATH_REFRESH_HINT_REQUIRED" -eq 1 ]] || return 0

  local rc_file
  if [[ -n "$PATH_UPDATE_FILES" ]]; then
    printf '请执行以下命令刷新当前终端，或直接重新打开一个新终端：\n'
    while IFS= read -r rc_file; do
      [[ -z "$rc_file" ]] && continue
      printf '  source "%s"\n' "$rc_file"
    done <<< "$PATH_UPDATE_FILES"
    printf '\n'
  fi
}

is_repo_root() {
  local dir="$1"
  [[ -n "$dir" ]] \
    && [[ -f "$dir/pyproject.toml" ]] \
    && [[ -d "$dir/flocks" ]] \
    && [[ -d "$dir/tui" ]] \
    && [[ -d "$dir/webui" ]] \
    && [[ -f "$dir/scripts/install.sh" ]]
}

resolve_root_dir() {
  local script_source script_dir candidate
  script_source="${BASH_SOURCE[0]}"
  script_dir="$(cd "$(dirname "$script_source")" && pwd)"

  for candidate in "$(dirname "$script_dir")" "$(pwd)"; do
    if is_repo_root "$candidate"; then
      ROOT_DIR="$candidate"
      return 0
    fi
  done

  return 1
}

print_clone_hint_and_exit() {
  cat <<EOF
[flocks] 当前未检测到 Flocks 仓库代码。

如需源码安装，请先拉取代码，再执行安装：

  git clone $REPO_URL
  cd Flocks
  ./scripts/install.sh

或直接使用 GitHub 一键安装入口：

  curl -fsSL $RAW_INSTALL_SH_URL | bash
  iwr -useb $RAW_INSTALL_PS1_URL | iex
EOF
  exit 1
}

refresh_path() {
  append_path "$HOME/.local/bin"
  append_path "$HOME/.cargo/bin"
  append_path "$HOME/.bun/bin"
  append_path "$HOME/.npm-global/bin"

  if has_cmd npm; then
    local npm_prefix
    npm_prefix="$(npm config get prefix 2>/dev/null | tr -d '\r' || true)"
    if [[ -n "$npm_prefix" && "$npm_prefix" != "undefined" && "$npm_prefix" != "null" ]]; then
      append_path "$npm_prefix"
      append_path "$npm_prefix/bin"
    fi
  fi
}

get_npm_prefix() {
  if ! has_cmd npm; then
    return 1
  fi

  local npm_prefix
  npm_prefix="$(npm config get prefix 2>/dev/null | tr -d '\r' || true)"
  if [[ -z "$npm_prefix" || "$npm_prefix" == "undefined" || "$npm_prefix" == "null" ]]; then
    return 1
  fi

  printf '%s' "$npm_prefix"
}

ensure_agent_browser_user_path_if_needed() {
  local npm_prefix prefix_bin agent_browser_bin resolved_path

  npm_prefix="$(get_npm_prefix || true)"
  [[ -n "$npm_prefix" && "$npm_prefix" == "$HOME/"* ]] || return 0

  prefix_bin="$npm_prefix/bin"
  agent_browser_bin="$prefix_bin/agent-browser"
  [[ -x "$agent_browser_bin" ]] || return 0

  resolved_path="$(command -v agent-browser 2>/dev/null || true)"
  if [[ -n "$resolved_path" && "$resolved_path" != "$agent_browser_bin" ]]; then
    return 0
  fi

  append_path "$prefix_bin"
  ensure_path_persisted "$prefix_bin"
}

print_usage() {
  cat <<EOF
Usage: ./scripts/install.sh [--with-tui]

Options:
  --with-tui, -t  安装 TUI 依赖（此时会自动安装 bun）
  --help, -h      查看帮助
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-tui|-t)
        INSTALL_TUI=1
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

get_node_major_version() {
  if ! has_cmd node; then
    return 1
  fi

  local version
  version="$(node -v 2>/dev/null | tr -d '\r' || true)"
  version="${version#v}"
  version="${version%%.*}"
  [[ "$version" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$version"
}

node_version_satisfies_requirement() {
  local major
  major="$(get_node_major_version)" || return 1
  [[ "$major" -ge "$MIN_NODE_MAJOR" ]]
}

install_nodejs_macos() {
  has_cmd brew || fail "未检测到满足要求的 npm，macOS 自动安装或升级 Node.js 22+ 需要 Homebrew。请先安装 Homebrew 后重试。安装命令：/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""

  info "尝试使用 Homebrew 安装或升级 Node.js..."
  brew install node
}

install_nodejs_linux() {
  info "未检测到满足要求的 npm，尝试自动安装或升级 Node.js..."

  if has_cmd apt-get; then
    has_cmd curl || fail "Debian/Ubuntu 自动安装 Node.js 22 需要 curl。请先安装 curl 后重试。"
    info "检测到 Debian/Ubuntu，优先使用 NodeSource 安装 Node.js 22..."
    run_with_privilege apt-get update
    curl -fsSL https://deb.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege apt-get install -y nodejs
    return
  fi

  if has_cmd dnf; then
    has_cmd curl || fail "dnf 自动安装 Node.js 22 需要 curl。请先安装 curl 后重试。"
    info "检测到 dnf，优先使用 NodeSource 安装 Node.js 22..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege dnf install -y nodejs
    return
  fi

  if has_cmd yum; then
    has_cmd curl || fail "yum 自动安装 Node.js 22 需要 curl。请先安装 curl 后重试。"
    info "检测到 yum，优先使用 NodeSource 安装 Node.js 22..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege yum install -y nodejs
    return
  fi

  if has_cmd pacman; then
    info "检测到 pacman，使用仓库安装 Node.js，并在安装后校验版本是否满足 Node.js ${MIN_NODE_MAJOR}+..."
    run_with_privilege pacman -Sy --noconfirm nodejs npm
    return
  fi

  if has_cmd zypper; then
    info "检测到 zypper，优先尝试安装 nodejs22..."
    if run_with_privilege zypper --non-interactive install nodejs22; then
      return
    fi
    info "未能直接安装 nodejs22，回退到仓库默认 nodejs/npm，并在安装后校验版本..."
    run_with_privilege zypper --non-interactive install nodejs npm
    return
  fi

  if has_cmd apk; then
    info "检测到 apk，使用仓库安装 Node.js，并在安装后校验版本是否满足 Node.js ${MIN_NODE_MAJOR}+..."
    run_with_privilege apk add --no-cache nodejs npm
    return
  fi

  fail "未检测到可用的 Linux 包管理器，无法自动安装 Node.js（包含 npm）。"
}

ensure_npm_installed() {
  if has_cmd npm && node_version_satisfies_requirement; then
    return
  fi

  if has_cmd node; then
    local current_major
    current_major="$(get_node_major_version || true)"
    if [[ -n "$current_major" ]]; then
      info "检测到当前 Node.js 版本为 v${current_major}，尝试升级到 Node.js ${MIN_NODE_MAJOR}+..."
    fi
  else
    info "未检测到满足要求的 npm，尝试自动安装 Node.js..."
  fi

  case "$(uname -s)" in
    Darwin)
      install_nodejs_macos
      ;;
    Linux)
      install_nodejs_linux
      ;;
    *)
      fail "当前系统不支持自动安装 Node.js（包含 npm），请手动安装后重试。"
      ;;
  esac

  refresh_path
  has_cmd npm || fail "Node.js（包含 npm）安装完成后仍不可用，请检查 PATH。"
  node_version_satisfies_requirement || fail "检测到的 Node.js 版本过低。当前项目至少需要 Node.js ${MIN_NODE_MAJOR}+，请安装或升级后重试。"
}

ensure_npm_global_prefix_writable() {
  has_cmd npm || fail "未检测到 npm，请先安装 Node.js 22+（包含 npm）后重试。"

  local npm_prefix target_dir user_prefix
  npm_prefix="$(get_npm_prefix || true)"
  if [[ -z "$npm_prefix" ]]; then
    return
  fi

  target_dir="$npm_prefix"
  if [[ -d "$npm_prefix/lib" ]]; then
    target_dir="$npm_prefix/lib"
  fi

  if [[ -w "$target_dir" ]]; then
    return
  fi

  user_prefix="$HOME/.npm-global"
  info "检测到 npm 全局目录无写权限，切换到用户目录: $user_prefix"
  mkdir -p "$user_prefix"
  npm config set prefix "$user_prefix"
  refresh_path
}

install_uv() {
  if has_cmd uv; then
    return
  fi

  has_cmd curl || fail "curl 未安装，无法自动安装 uv。"
  info "未检测到 uv，开始自动安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  refresh_path
  ensure_path_persisted "$HOME/.local/bin"
  has_cmd uv || fail "uv 安装完成后仍不可用，请检查 PATH。"
}

is_lock_error_output() {
  local output="$1"
  [[ "$output" =~ 拒绝访问|Access\ is\ denied|os\ error\ 5|WinError\ 5|failed\ to\ remove\ directory|Failed\ to\ update\ Windows\ PE\ resources ]]
}

get_runtime_pid_file_paths() {
  local flocks_root="${FLOCKS_ROOT:-$HOME/.flocks}"
  local run_dir="$flocks_root/run"
  printf '%s\n' \
    "$run_dir/backend.pid" \
    "$run_dir/webui.pid" \
    "$run_dir/upgrade_server.pid"
}

get_pid_from_runtime_file() {
  local pid_file="$1" raw
  [[ -n "$pid_file" && -f "$pid_file" ]] || return 1
  raw="$(<"$pid_file")" || return 1

  if [[ "$raw" =~ ^[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$raw" =~ \"pid\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi

  return 1
}

stop_tracked_process() {
  local process_id="$1"
  local reason="${2:-tracked process}"
  [[ -n "$process_id" && "$process_id" =~ ^[0-9]+$ && "$process_id" -gt 0 ]] || return 0

  kill "$process_id" >/dev/null 2>&1 || kill -9 "$process_id" >/dev/null 2>&1 || true
  info "已停止 ${reason} (PID: ${process_id})"
}

list_flocks_process_ids() {
  local tool_dir=""
  if has_cmd uv; then
    tool_dir="$(uv tool dir 2>/dev/null | tr -d '\r' || true)"
  fi

  ps -ax -o pid= -o command= | while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    local pid="${line%% *}"
    local command="${line#"$pid"}"
    command="${command#"${command%%[![:space:]]*}"}"
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    [[ "$pid" != "$$" ]] || continue

    case "$command" in
      *flocks.server.app*|*uvicorn*flocks.server.app*)
        printf '%s\n' "$pid"
        ;;
      *"$ROOT_DIR"*uv\ tool*|*"$ROOT_DIR"*preview*)
        printf '%s\n' "$pid"
        ;;
      *)
        if [[ -n "$tool_dir" && "$command" == *"$tool_dir"*flocks* ]]; then
          printf '%s\n' "$pid"
        fi
        ;;
    esac
  done
}

stop_flocks_processes() {
  info "检查并停止可能锁定安装目录的 Flocks 相关进程..."

  if has_cmd flocks; then
    flocks stop >/dev/null 2>&1 || true
    sleep 2
  fi

  local pid_file pid
  get_runtime_pid_file_paths | while IFS= read -r pid_file; do
    pid="$(get_pid_from_runtime_file "$pid_file" || true)"
    [[ -n "$pid" ]] || continue
    stop_tracked_process "$pid" "runtime process from $pid_file"
  done

  list_flocks_process_ids | awk '!seen[$0]++' | while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    stop_tracked_process "$pid" "Flocks related process"
  done

  sleep 1
}

run_with_lock_retry() {
  local description="$1"
  shift

  local output status
  set +e
  output="$("$@" 2>&1)"
  status=$?
  set -e
  [[ -n "$output" ]] && printf '%s\n' "$output"

  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  if ! is_lock_error_output "$output"; then
    fail "${description}失败。"
  fi

  info "${description}检测到文件锁定，尝试清理残留进程后重试..."
  stop_flocks_processes
  sleep 3

  "$@" || fail "${description}失败。"
}

install_flocks_cli() {
  local tool_bin

  info "安装 flocks 全局 CLI..."
  (
    cd "$ROOT_DIR"
    run_with_lock_retry "flocks 全局 CLI 安装" uv tool install --editable "$ROOT_DIR" --force
  )

  tool_bin="$(uv tool dir --bin 2>/dev/null | tr -d '\r' || true)"
  if [[ -n "$tool_bin" ]]; then
    append_path "$tool_bin"
    ensure_path_persisted "$tool_bin"
  fi

  has_cmd flocks || fail "flocks CLI 安装完成后仍不可用，请检查 PATH。"
}

install_bun() {
  if has_cmd bun; then
    return
  fi

  has_cmd curl || fail "curl 未安装，无法自动安装 bun。"
  info "未检测到 bun，开始自动安装..."
  curl -fsSL https://bun.sh/install | bash
  refresh_path
  ensure_path_persisted "$HOME/.bun/bin"
  has_cmd bun || fail "bun 安装完成后仍不可用，请检查 PATH。"
}

install_dingtalk_channel_deps() {
  local connector_dir="$ROOT_DIR/.flocks/plugins/channels/dingtalk/dingtalk-openclaw-connector"
  [[ -f "$connector_dir/package.json" ]] || return 0

  local node_modules_dir="$connector_dir/node_modules"
  if [[ -d "$node_modules_dir" ]]; then
    info "钉钉 channel 依赖已存在，跳过安装。"
    return 0
  fi

  info "检测到钉钉 channel 插件，安装 npm 依赖..."
  (
    cd "$connector_dir"
    npm install
  )
  info "钉钉 channel 依赖安装完成。"
}

ensure_env_var_persisted() {
  local var_name="$1"
  local var_value="$2"
  local rc_file export_line

  [[ -n "$var_name" && -n "$var_value" ]] || return 0

  PATH_REFRESH_HINT_REQUIRED=1
  rc_file="$(detect_shell_rc_file)"
  mkdir -p "$(dirname "$rc_file")"
  touch "$rc_file"

  export_line="export ${var_name}=\"$var_value\""
  if ! grep -Fqs "$export_line" "$rc_file"; then
    {
      printf '\n# Added by flocks installer\n'
      printf '%s\n' "$export_line"
    } >> "$rc_file"
    PATH_UPDATE_REQUIRED=1
    record_path_update_file "$rc_file"
    info "已将 ${var_name} 写入: $rc_file"
  fi
}

detect_system_browser_path() {
  case "$(uname -s)" in
    Darwin)
      local mac_browser
      for mac_browser in \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
        if [[ -x "$mac_browser" ]]; then
          printf '%s' "$mac_browser"
          return 0
        fi
      done
      ;;
    Linux)
      local linux_browser
      for linux_browser in google-chrome google-chrome-stable chromium-browser chromium chrome; do
        if has_cmd "$linux_browser"; then
          command -v "$linux_browser"
          return 0
        fi
      done
      ;;
  esac

  return 1
}

get_chrome_for_testing_dir() {
  printf '%s' "$HOME/.flocks/browser"
}

install_chrome_for_testing() {
  local browser_dir install_output browser_path="" line candidate
  has_cmd npx || fail "未检测到 npx，请先安装 Node.js（包含 npm）后重试。"
  browser_dir="$(get_chrome_for_testing_dir)"
  mkdir -p "$browser_dir"

  info "未检测到系统 Chrome/Chromium，开始安装 Chrome for Testing 到: $browser_dir" >&2
  if ! install_output="$(npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir" 2>&1)"; then
    printf '%s\n' "$install_output" >&2
    fail "Chrome for Testing 安装失败。"
  fi

  while IFS= read -r line; do
    [[ -n "$line" ]] && printf '%s\n' "$line" >&2
    case "$line" in
      chrome@*' '*|chromium@*' '*)
        candidate="${line#* }"
        if [[ "$candidate" = /* && -x "$candidate" ]]; then
          browser_path="$candidate"
        fi
        ;;
    esac
  done <<< "$install_output"

  [[ -n "$browser_path" ]] || fail "Chrome for Testing 安装成功，但未能从安装输出解析浏览器路径。"
  printf '%s' "$browser_path"
}

configure_agent_browser_browser() {
  local browser_path=""

  browser_path="$(detect_system_browser_path || true)"
  if [[ -n "$browser_path" ]]; then
    info "检测到系统 Chrome/Chromium，agent-browser 将默认使用: $browser_path"
  else
    browser_path="$(install_chrome_for_testing)"
    info "已安装 Chrome for Testing，agent-browser 将默认使用: $browser_path"
  fi

  export AGENT_BROWSER_EXECUTABLE_PATH="$browser_path"
  ensure_env_var_persisted "AGENT_BROWSER_EXECUTABLE_PATH" "$browser_path"
}

install_agent_browser() {
  ensure_agent_browser_user_path_if_needed

  if ! has_cmd agent-browser; then
    ensure_npm_global_prefix_writable
    info "安装 agent-browser CLI..."
    npm install --global agent-browser
    refresh_path
    ensure_agent_browser_user_path_if_needed
    has_cmd agent-browser || fail "agent-browser 安装完成后仍不可用，请检查 PATH。"
  else
    info "检测到 agent-browser，跳过 CLI 安装。"
  fi

  configure_agent_browser_browser
}

main() {
  parse_args "$@"
  refresh_path

  resolve_root_dir || print_clone_hint_and_exit

  info "项目目录: $ROOT_DIR"
  install_uv
  ensure_npm_installed

  info "使用 uv sync --group dev 安装 Python 后端依赖（含测试与 lint）..."
  (
    cd "$ROOT_DIR"
    run_with_lock_retry "Python 后端依赖安装" uv sync --group dev
  )

  install_flocks_cli

  info "安装 WebUI 依赖..."
  (
    cd "$ROOT_DIR/webui"
    npm install
  )

  install_dingtalk_channel_deps

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    install_bun
    info "安装 TUI 依赖..."
    (
      cd "$ROOT_DIR/tui"
      bun install
    )
  else
    info "跳过 TUI 依赖安装。如需安装，请重新执行 ./scripts/install.sh --with-tui"
  fi

  install_agent_browser

  cat <<EOF

[flocks] 安装完成。

请启动新的终端会话，以加载环境变量并启用相关命令

后续可用命令：
  1. 以 daemon 模式启动后端 + WebUI
     flocks start

  2. 查看更多命令帮助
     flocks --help
EOF

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    cat <<EOF
  3. 启动 TUI
     flocks tui
EOF
  fi

  # show_path_update_hint
}

main "$@"
