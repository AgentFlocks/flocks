#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

REPO_URL="${FLOCKS_INSTALL_REPO_URL:-https://github.com/AgentFlocks/Flocks.git}"
RAW_INSTALL_SH_URL="${FLOCKS_RAW_INSTALL_SH_URL:-https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh}"
RAW_INSTALL_PS1_URL="${FLOCKS_RAW_INSTALL_PS1_URL:-https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.ps1}"
ROOT_DIR=""
INSTALL_TUI=0
MIN_NODE_MAJOR=22
PATH_UPDATE_REQUIRED=0
PATH_UPDATE_FILES=""
PATH_UPDATE_DIRS=""
PATH_REFRESH_HINT_REQUIRED=0
INSTALL_LANGUAGE="${FLOCKS_INSTALL_LANGUAGE:-en}"
UV_DEFAULT_INDEX="${FLOCKS_UV_DEFAULT_INDEX:-https://pypi.org/simple}"
UV_INSTALL_SH_URL="${FLOCKS_UV_INSTALL_SH_URL:-https://astral.sh/uv/install.sh}"
NPM_REGISTRY="${FLOCKS_NPM_REGISTRY:-https://registry.npmjs.org/}"
NODEJS_MANUAL_DOWNLOAD_URL="${FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL:-https://nodejs.org/en/download}"
NVM_INSTALL_SCRIPT_URL="${FLOCKS_NVM_INSTALL_SCRIPT_URL:-https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh}"

info() {
  printf '[flocks] %s\n' "$1"
}

fail() {
  if is_zh_install; then
    printf '[flocks] 错误: %s\n' "$1" >&2
  else
    printf '[flocks] error: %s\n' "$1" >&2
  fi
  exit 1
}

warn() {
  if is_zh_install; then
    printf '[flocks] 警告: %s\n' "$1" >&2
  else
    printf '[flocks] warning: %s\n' "$1" >&2
  fi
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

is_zh_install() {
  [[ "$INSTALL_LANGUAGE" == zh* || "$INSTALL_LANGUAGE" == cn* ]]
}

nodejs_manual_download_hint() {
  printf ' Manual download: %s' "$NODEJS_MANUAL_DOWNLOAD_URL"
}

select_install_sources() {
  if is_zh_install; then
    info "使用 PyPI 源: $UV_DEFAULT_INDEX"
    info "使用 npm 源: $NPM_REGISTRY"
    info "使用 uv 安装脚本: $UV_INSTALL_SH_URL"
  else
    info "Using PyPI index: $UV_DEFAULT_INDEX"
    info "Using npm registry: $NPM_REGISTRY"
    info "Using uv install script: $UV_INSTALL_SH_URL"
  fi
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

  fail "This operation requires administrator privileges. Run as root or install and configure sudo first."
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
    info "Added PATH entry to: $rc_file"
  fi
}

show_path_update_hint() {
  [[ "$PATH_REFRESH_HINT_REQUIRED" -eq 1 ]] || return 0

  local rc_file first_rc_file
  first_rc_file=""

  if [[ "$PATH_UPDATE_REQUIRED" -eq 1 ]]; then
    printf '\n[flocks] Shell configuration was updated. Refresh the current terminal environment:\n'
  else
    printf '\n[flocks] Existing shell configuration was detected. Make sure it is active in the current terminal:\n'
  fi

  if [[ -n "$PATH_UPDATE_DIRS" ]]; then
    printf '\n[flocks] Added these directories to PATH:\n'
    while IFS= read -r path_entry; do
      [[ -z "$path_entry" ]] && continue
      printf '  - %s\n' "$path_entry"
    done <<< "$PATH_UPDATE_DIRS"
  fi

  if [[ -n "$PATH_UPDATE_FILES" ]]; then
    printf '\n[flocks] Run the following command to refresh the current terminal, or open a new terminal session:\n'
    while IFS= read -r rc_file; do
      [[ -z "$rc_file" ]] && continue
      printf '  source "%s"\n' "$rc_file"
      if [[ -z "$first_rc_file" ]]; then
        first_rc_file="$rc_file"
      fi
    done <<< "$PATH_UPDATE_FILES"
  fi

  if [[ -n "$first_rc_file" ]]; then
    printf '\n[flocks] If `source "%s"` still does not refresh the environment, open a new terminal and continue there.\n' "$first_rc_file"
  fi
}

show_path_update_hint_inline() {
  [[ "$PATH_REFRESH_HINT_REQUIRED" -eq 1 ]] || return 0

  local rc_file
  if [[ -n "$PATH_UPDATE_FILES" ]]; then
    printf 'Run the following command to refresh the current terminal, or open a new terminal session:\n'
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
  if is_zh_install; then
    cat <<EOF
[flocks] 当前目录未找到 Flocks 仓库源码。

如需从源码安装，请先克隆仓库后再执行：

  git clone $REPO_URL
  cd Flocks
  ./scripts/install_zh.sh

或者使用一键安装脚本：

  curl -fsSL $RAW_INSTALL_SH_URL | bash
  iwr -useb $RAW_INSTALL_PS1_URL | iex
EOF
  else
    cat <<EOF
[flocks] Flocks repository source was not found in the current location.

To install from source, clone the repository first and then run:

  git clone $REPO_URL
  cd Flocks
  ./scripts/install.sh

Or use the one-line GitHub bootstrap installer:

  curl -fsSL $RAW_INSTALL_SH_URL | bash
  iwr -useb $RAW_INSTALL_PS1_URL | iex
EOF
  fi
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

get_npm_global_write_check_dir() {
  local npm_prefix="$1"
  local candidate

  [[ -n "$npm_prefix" ]] || return 1

  for candidate in "$npm_prefix/lib/node_modules" "$npm_prefix/lib" "$npm_prefix"; do
    if [[ -e "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  printf '%s' "$npm_prefix/lib/node_modules"
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
  --with-tui, -t  Install TUI dependencies as well (bun will be installed automatically)
  --help, -h      Show this help message
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
        fail "Unsupported argument: $1"
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

load_nvm() {
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  [[ -s "$NVM_DIR/nvm.sh" ]] || return 1

  # shellcheck disable=SC1090
  . "$NVM_DIR/nvm.sh"
  command -v nvm >/dev/null 2>&1
}

install_nodejs_macos() {
  if has_cmd brew; then
    info "Trying to install or upgrade Node.js with Homebrew..."
    brew install node
    return
  fi

  has_cmd curl || fail "A compatible npm installation was not found. Homebrew is not installed, and curl is required to install nvm automatically on macOS. Install Homebrew first and retry: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"$(nodejs_manual_download_hint)"

  if load_nvm; then
    info "Homebrew was not found. Using the existing nvm installation..."
  else
    info "Homebrew was not found. Trying to install nvm..."
    curl -o- "$NVM_INSTALL_SCRIPT_URL" | bash
    load_nvm || fail "nvm was installed, but it could not be loaded from $NVM_DIR/nvm.sh. Open a new shell and retry.$(nodejs_manual_download_hint)"
  fi

  info "Trying to install Node.js ${MIN_NODE_MAJOR} with nvm..."
  nvm install "$MIN_NODE_MAJOR"
  nvm use "$MIN_NODE_MAJOR" >/dev/null
}

install_nodejs_linux() {
  info "A compatible npm installation was not found. Trying to install or upgrade Node.js automatically..."

  if has_cmd apt-get; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically on Debian/Ubuntu.$(nodejs_manual_download_hint)"
    info "Detected Debian/Ubuntu. Installing Node.js 22 from NodeSource..."
    run_with_privilege apt-get update
    curl -fsSL https://deb.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege apt-get install -y nodejs
    return
  fi

  if has_cmd dnf; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically with dnf.$(nodejs_manual_download_hint)"
    info "Detected dnf. Installing Node.js 22 from NodeSource..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege dnf install -y nodejs
    return
  fi

  if has_cmd yum; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically with yum.$(nodejs_manual_download_hint)"
    info "Detected yum. Installing Node.js 22 from NodeSource..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege yum install -y nodejs
    return
  fi

  if has_cmd pacman; then
    info "Detected pacman. Installing Node.js from the distribution repository and verifying that the version satisfies Node.js ${MIN_NODE_MAJOR}+..."
    run_with_privilege pacman -Sy --noconfirm nodejs npm
    return
  fi

  if has_cmd zypper; then
    info "Detected zypper. Trying nodejs22 first..."
    if run_with_privilege zypper --non-interactive install nodejs22; then
      return
    fi
    info "nodejs22 was not available directly. Falling back to the default nodejs/npm packages and validating the version afterwards..."
    run_with_privilege zypper --non-interactive install nodejs npm
    return
  fi

  if has_cmd apk; then
    info "Detected apk. Installing Node.js from the distribution repository and verifying that the version satisfies Node.js ${MIN_NODE_MAJOR}+..."
    run_with_privilege apk add --no-cache nodejs npm
    return
  fi

  fail "No supported Linux package manager was detected, so Node.js (including npm) cannot be installed automatically.$(nodejs_manual_download_hint)"
}

ensure_npm_installed() {
  if has_cmd npm && node_version_satisfies_requirement; then
    return
  fi

  if has_cmd node; then
    local current_major
    current_major="$(get_node_major_version || true)"
    if [[ -n "$current_major" ]]; then
      info "Detected current Node.js version v${current_major}. Trying to upgrade to Node.js ${MIN_NODE_MAJOR}+..."
    fi
  else
    info "A compatible npm installation was not found. Trying to install Node.js automatically..."
  fi

  case "$(uname -s)" in
    Darwin)
      install_nodejs_macos
      ;;
    Linux)
      install_nodejs_linux
      ;;
    *)
      fail "Automatic installation of Node.js (including npm) is not supported on this system. Install it manually and retry.$(nodejs_manual_download_hint)"
      ;;
  esac

  refresh_path
  has_cmd npm || fail "Node.js (including npm) was installed, but npm is still not available. Check PATH and retry.$(nodejs_manual_download_hint)"
  node_version_satisfies_requirement || fail "Detected Node.js version is too old. This project requires Node.js ${MIN_NODE_MAJOR}+.$(nodejs_manual_download_hint)"
}

ensure_npm_global_prefix_writable() {
  has_cmd npm || fail "npm was not found. Install Node.js 22+ (including npm) and retry.$(nodejs_manual_download_hint)"

  local npm_prefix target_dir user_prefix
  npm_prefix="$(get_npm_prefix || true)"
  if [[ -z "$npm_prefix" ]]; then
    return
  fi

  target_dir="$(get_npm_global_write_check_dir "$npm_prefix")"

  if [[ -w "$target_dir" ]]; then
    return
  fi

  user_prefix="$HOME/.npm-global"
  info "Global npm directory is not writable. Switching to user prefix: $user_prefix"
  mkdir -p "$user_prefix"
  npm config set prefix "$user_prefix"
  refresh_path
}

install_uv() {
  if has_cmd uv; then
    return
  fi

  if is_zh_install; then
    has_cmd curl || fail "自动安装 uv 需要 curl，请先安装 curl 后重试。"
    info "未检测到 uv，正在自动安装..."
  else
    has_cmd curl || fail "curl is required to install uv automatically."
    info "uv was not found. Installing it automatically..."
  fi
  curl -LsSf "$UV_INSTALL_SH_URL" | sh
  refresh_path
  ensure_path_persisted "$HOME/.local/bin"
  if is_zh_install; then
    has_cmd uv || fail "uv 安装已完成，但当前仍无法找到 uv。请检查 PATH 后重试。"
  else
    has_cmd uv || fail "uv finished installing, but it is still not available. Check PATH and retry."
  fi
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
  info "Stopped ${reason} (PID: ${process_id})"
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
      *"$ROOT_DIR/.venv"*flocks*)
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
  info "Checking for Flocks-related processes that may be locking the install directory..."

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

  local tmpfile output status
  tmpfile="$(mktemp)"

  set +e
  "$@" 2>&1 | tee "$tmpfile"
  status=${PIPESTATUS[0]}
  set -e
  output="$(<"$tmpfile")"
  rm -f "$tmpfile"

  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  if ! is_lock_error_output "$output"; then
    fail "${description} failed."
  fi

  info "${description} detected a file lock. Cleaning up leftover processes before retrying..."
  stop_flocks_processes
  sleep 3

  "$@" || fail "${description} failed."
}

install_flocks_cli() {
  info "Installing the global flocks CLI..."

  if has_cmd uv && uv tool list 2>/dev/null | grep -q '^flocks '; then
    info "Removing legacy uv tool installation..."
    uv tool uninstall flocks 2>/dev/null || true
  fi

  local target="$ROOT_DIR/.venv/bin/flocks"
  if [[ ! -x "$target" ]]; then
    fail "Expected CLI entry point not found: $target — run 'uv sync' first."
  fi

  local link_dir="$HOME/.local/bin"
  mkdir -p "$link_dir"
  ln -sf "$target" "$link_dir/flocks"

  append_path "$link_dir"
  ensure_path_persisted "$link_dir"

  has_cmd flocks || fail "The flocks CLI finished installing, but it is still not available. Check PATH and retry."
}

install_bun() {
  if has_cmd bun; then
    return
  fi

  has_cmd curl || fail "curl is required to install bun automatically."
  info "bun was not found. Installing it automatically..."
  curl -fsSL https://bun.sh/install | bash
  refresh_path
  ensure_path_persisted "$HOME/.bun/bin"
  has_cmd bun || fail "bun finished installing, but it is still not available. Check PATH and retry."
}

install_dingtalk_channel_deps() {
  local connector_dir="$ROOT_DIR/.flocks/plugins/channels/dingtalk/dingtalk-openclaw-connector"
  [[ -f "$connector_dir/package.json" ]] || return 0

  local node_modules_dir="$connector_dir/node_modules"
  if [[ -d "$node_modules_dir" ]]; then
    if is_zh_install; then
      info "钉钉频道依赖已存在，跳过安装。"
    else
      info "DingTalk channel dependencies already exist. Skipping installation."
    fi
    return 0
  fi

  if is_zh_install; then
    info "检测到钉钉频道插件，正在安装 npm 依赖..."
  else
    info "Detected DingTalk channel plugin. Installing npm dependencies..."
  fi
  (
    cd "$connector_dir"
    npm_config_registry="$NPM_REGISTRY" npm install
  )
  if is_zh_install; then
    info "钉钉频道依赖安装完成。"
  else
    info "DingTalk channel dependencies installed."
  fi
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
    info "Added ${var_name} to: $rc_file"
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

resolve_chrome_for_testing_path_from_dir() {
  local browser_dir="$1" candidate restore_globstar=0 restore_nullglob=0
  [[ -d "$browser_dir" ]] || return 1

  shopt -q globstar || restore_globstar=1
  shopt -q nullglob || restore_nullglob=1
  shopt -s globstar nullglob

  for candidate in \
    "$browser_dir"/**/"Google Chrome for Testing" \
    "$browser_dir"/**/chrome.exe \
    "$browser_dir"/**/chrome; do
    [[ -f "$candidate" && -x "$candidate" ]] || continue
    printf '%s' "$candidate"
    [[ "$restore_globstar" -eq 1 ]] && shopt -u globstar
    [[ "$restore_nullglob" -eq 1 ]] && shopt -u nullglob
    return 0
  done

  [[ "$restore_globstar" -eq 1 ]] && shopt -u globstar
  [[ "$restore_nullglob" -eq 1 ]] && shopt -u nullglob
  return 1
}

install_chrome_for_testing() {
  local browser_dir browser_path="" install_status
  if ! has_cmd npx; then
    if is_zh_install; then
      warn "未找到 npx，跳过浏览器安装；这不影响 Flocks 启动，可稍后重新安装。"
    else
      warn "npx was not found, so browser installation was skipped. This does not block Flocks startup; you can reinstall it later."
    fi
    return 1
  fi
  browser_dir="$(get_chrome_for_testing_dir)"
  mkdir -p "$browser_dir"

  info "System Chrome/Chromium was not found. Installing Chrome for Testing to: $browser_dir" >&2
  if is_zh_install; then
    info "正在下载 Chrome for Testing。" >&2
    warn "如浏览器安装失败，不影响 Flocks 启动，可稍后重新安装。" 
  else
    info "Downloading Chrome for Testing." >&2
    warn "If browser installation fails, Flocks can still start and you can reinstall it later."
  fi

  set +e
  npm_config_registry="$NPM_REGISTRY" npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir" 1>&2
  install_status=$?
  set -e

  if [[ "$install_status" -ne 0 ]]; then
    if is_zh_install; then
      warn "Chrome for Testing 安装失败，不影响 Flocks 启动，可稍后重新安装。"
    else
      warn "Chrome for Testing installation failed. This does not block Flocks startup; you can reinstall it later."
    fi
    return 1
  fi

  browser_path="$(resolve_chrome_for_testing_path_from_dir "$browser_dir" || true)"
  if [[ -z "$browser_path" ]]; then
    if is_zh_install; then
      warn "Chrome for Testing 已安装，但未能在目录中找到浏览器可执行文件；这不影响 Flocks 启动，可稍后重新安装。"
    else
      warn "Chrome for Testing finished installing, but the browser executable could not be located. This does not block Flocks startup; you can reinstall it later."
    fi
    return 1
  fi
  printf '%s' "$browser_path"
}

configure_agent_browser_browser() {
  local browser_path="" browser_dir=""

  browser_path="$(detect_system_browser_path || true)"
  if [[ -n "$browser_path" ]]; then
    info "Detected system Chrome/Chromium. agent-browser will use: $browser_path"
  else
    browser_dir="$(get_chrome_for_testing_dir)"
    browser_path="$(resolve_chrome_for_testing_path_from_dir "$browser_dir" || true)"
    if [[ -n "$browser_path" ]]; then
      info "Found existing Chrome for Testing. agent-browser will use: $browser_path"
    else
      browser_path="$(install_chrome_for_testing || true)"
      if [[ -n "$browser_path" ]]; then
        info "Installed Chrome for Testing. agent-browser will use: $browser_path"
      else
        return 0
      fi
    fi
  fi

  export AGENT_BROWSER_EXECUTABLE_PATH="$browser_path"
  ensure_env_var_persisted "AGENT_BROWSER_EXECUTABLE_PATH" "$browser_path"
}

install_agent_browser() {
  ensure_agent_browser_user_path_if_needed

  if ! has_cmd agent-browser; then
    ensure_npm_global_prefix_writable
    info "Installing the agent-browser CLI..."
    npm_config_registry="$NPM_REGISTRY" npm install --global agent-browser
    refresh_path
    ensure_agent_browser_user_path_if_needed
    has_cmd agent-browser || fail "agent-browser finished installing, but it is still not available. Check PATH and retry."
  else
    info "agent-browser is already installed. Skipping CLI installation."
  fi

  configure_agent_browser_browser
}

main() {
  parse_args "$@"
  refresh_path

  resolve_root_dir || print_clone_hint_and_exit

  if is_zh_install; then
    info "项目目录: $ROOT_DIR"
  else
    info "Project directory: $ROOT_DIR"
  fi
  install_uv
  ensure_npm_installed
  select_install_sources

  if is_zh_install; then
    info "正在使用 uv sync --group dev 安装 Python 后端依赖（含测试与 lint 工具）..."
  else
    info "Installing Python backend dependencies (including tests and lint tools) with uv sync --group dev..."
  fi
  (
    cd "$ROOT_DIR"
    run_with_lock_retry "Python backend dependency installation" uv sync --group dev --default-index "$UV_DEFAULT_INDEX"
  )

  install_flocks_cli

  if is_zh_install; then
    info "正在安装 WebUI 依赖..."
  else
    info "Installing WebUI dependencies..."
  fi
  (
    cd "$ROOT_DIR/webui"
    npm_config_registry="$NPM_REGISTRY" npm install
  )

  install_dingtalk_channel_deps

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    install_bun
    if is_zh_install; then
      info "正在安装 TUI 依赖..."
    else
      info "Installing TUI dependencies..."
    fi
    (
      cd "$ROOT_DIR/tui"
      bun install
    )
  fi

  install_agent_browser

  if is_zh_install; then
    cat <<EOF

[flocks] 安装完成。

请打开新的终端会话，以加载更新后的环境变量并启用新安装的命令。

接下来可以执行：
  1. 以守护进程模式启动后端和 WebUI
     flocks start

  2. 查看命令帮助
     flocks --help
EOF
  else
    cat <<EOF

[flocks] Installation complete.

Start a new terminal session to load the updated environment and enable the installed commands.

Next commands:
  1. Start the backend and WebUI in daemon mode
     flocks start

  2. Show command help
     flocks --help
EOF
  fi

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    if is_zh_install; then
      cat <<EOF
  3. 启动 TUI
     flocks tui
EOF
    else
      cat <<EOF
  3. Launch the TUI
     flocks tui
EOF
    fi
  fi

  # show_path_update_hint
}

main "$@"