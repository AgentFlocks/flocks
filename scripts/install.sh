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
UV_DEFAULT_INDEX="${FLOCKS_UV_DEFAULT_INDEX:-https://pypi.org/simple}"
NPM_REGISTRY="${FLOCKS_NPM_REGISTRY:-https://registry.npmjs.org/}"

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

select_install_sources() {
  info "Using PyPI index: $UV_DEFAULT_INDEX"
  info "Using npm registry: $NPM_REGISTRY"
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

install_nodejs_macos() {
  has_cmd brew || fail "A compatible npm installation was not found. Homebrew is required to install or upgrade Node.js 22+ automatically on macOS. Install Homebrew first and retry: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""

  info "Trying to install or upgrade Node.js with Homebrew..."
  brew install node
}

install_nodejs_linux() {
  info "A compatible npm installation was not found. Trying to install or upgrade Node.js automatically..."

  if has_cmd apt-get; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically on Debian/Ubuntu."
    info "Detected Debian/Ubuntu. Installing Node.js 22 from NodeSource..."
    run_with_privilege apt-get update
    curl -fsSL https://deb.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege apt-get install -y nodejs
    return
  fi

  if has_cmd dnf; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically with dnf."
    info "Detected dnf. Installing Node.js 22 from NodeSource..."
    curl -fsSL https://rpm.nodesource.com/setup_22.x | run_with_privilege bash -
    run_with_privilege dnf install -y nodejs
    return
  fi

  if has_cmd yum; then
    has_cmd curl || fail "curl is required to install Node.js 22 automatically with yum."
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

  fail "No supported Linux package manager was detected, so Node.js (including npm) cannot be installed automatically."
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
      fail "Automatic installation of Node.js (including npm) is not supported on this system. Install it manually and retry."
      ;;
  esac

  refresh_path
  has_cmd npm || fail "Node.js (including npm) was installed, but npm is still not available. Check PATH and retry."
  node_version_satisfies_requirement || fail "Detected Node.js version is too old. This project requires Node.js ${MIN_NODE_MAJOR}+."
}

ensure_npm_global_prefix_writable() {
  has_cmd npm || fail "npm was not found. Install Node.js 22+ (including npm) and retry."

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
  info "Global npm directory is not writable. Switching to user prefix: $user_prefix"
  mkdir -p "$user_prefix"
  npm config set prefix "$user_prefix"
  refresh_path
}

install_uv() {
  if has_cmd uv; then
    return
  fi

  has_cmd curl || fail "curl is required to install uv automatically."
  info "uv was not found. Installing it automatically..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  refresh_path
  ensure_path_persisted "$HOME/.local/bin"
  has_cmd uv || fail "uv finished installing, but it is still not available. Check PATH and retry."
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
  local tool_bin

  info "Installing the global flocks CLI..."
  (
    cd "$ROOT_DIR"
    run_with_lock_retry "Global flocks CLI installation" uv tool install --editable "$ROOT_DIR" --force --default-index "$UV_DEFAULT_INDEX"
  )

  tool_bin="$(uv tool dir --bin 2>/dev/null | tr -d '\r' || true)"
  if [[ -n "$tool_bin" ]]; then
    append_path "$tool_bin"
    ensure_path_persisted "$tool_bin"
  fi

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
    info "DingTalk channel dependencies already exist. Skipping installation."
    return 0
  fi

  info "Detected DingTalk channel plugin. Installing npm dependencies..."
  (
    cd "$connector_dir"
    npm_config_registry="$NPM_REGISTRY" npm install
  )
  info "DingTalk channel dependencies installed."
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

install_chrome_for_testing() {
  local browser_dir install_output browser_path="" line candidate tmpfile
  has_cmd npx || fail "npx was not found. Install Node.js (including npm) and retry."
  browser_dir="$(get_chrome_for_testing_dir)"
  mkdir -p "$browser_dir"

  info "System Chrome/Chromium was not found. Installing Chrome for Testing to: $browser_dir" >&2

  tmpfile="$(mktemp)"
  set +e
  npm_config_registry="$NPM_REGISTRY" npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir" 2>&1 | tee "$tmpfile" >&2
  local install_status=${PIPESTATUS[0]}
  set -e
  install_output="$(<"$tmpfile")"
  rm -f "$tmpfile"

  if [[ "$install_status" -ne 0 ]]; then
    fail "Chrome for Testing installation failed."
  fi

  while IFS= read -r line; do
    case "$line" in
      chrome@*' '*|chromium@*' '*)
        candidate="${line#* }"
        if [[ "$candidate" = /* && -x "$candidate" ]]; then
          browser_path="$candidate"
        fi
        ;;
    esac
  done <<< "$install_output"

  [[ -n "$browser_path" ]] || fail "Chrome for Testing finished installing, but the browser path could not be parsed from the installer output."
  printf '%s' "$browser_path"
}

configure_agent_browser_browser() {
  local browser_path=""

  browser_path="$(detect_system_browser_path || true)"
  if [[ -n "$browser_path" ]]; then
    info "Detected system Chrome/Chromium. agent-browser will use: $browser_path"
  else
    browser_path="$(install_chrome_for_testing)"
    info "Installed Chrome for Testing. agent-browser will use: $browser_path"
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

  info "Project directory: $ROOT_DIR"
  install_uv
  ensure_npm_installed
  select_install_sources

  info "Installing Python backend dependencies (including tests and lint tools) with uv sync --group dev..."
  (
    cd "$ROOT_DIR"
    run_with_lock_retry "Python backend dependency installation" uv sync --group dev --default-index "$UV_DEFAULT_INDEX"
  )

  install_flocks_cli

  info "Installing WebUI dependencies..."
  (
    cd "$ROOT_DIR/webui"
    npm_config_registry="$NPM_REGISTRY" npm install
  )

  install_dingtalk_channel_deps

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    install_bun
    info "Installing TUI dependencies..."
    (
      cd "$ROOT_DIR/tui"
      bun install
    )
  else
    info "Skipping TUI dependency installation. Re-run ./scripts/install.sh --with-tui to install them."
  fi

  install_agent_browser

  cat <<EOF

[flocks] Installation complete.

Start a new terminal session to load the updated environment and enable the installed commands.

Next commands:
  1. Start the backend and WebUI in daemon mode
     flocks start

  2. Show command help
     flocks --help
EOF

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    cat <<EOF
  3. Launch the TUI
     flocks tui
EOF
  fi

  # show_path_update_hint
}

main "$@"