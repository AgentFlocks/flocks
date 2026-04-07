#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

append_path() {
  local path_entry="$1"
  [[ -n "$path_entry" && -d "$path_entry" ]] || return 0
  [[ ":$PATH:" == *":$path_entry:"* ]] || export PATH="$path_entry:$PATH"
}

refresh_path() {
  append_path "$HOME/.local/bin"
  append_path "$HOME/.cargo/bin"
  append_path "$HOME/.bun/bin"
  append_path "$HOME/.npm-global/bin"
}

main() {
  refresh_path
  "$ROOT_DIR/scripts/run_legacy_task_migration.sh" "${1:-}" || true

  if command -v flocks >/dev/null 2>&1; then
    exec flocks "$@"
  fi

  if command -v uv >/dev/null 2>&1; then
    cd "$ROOT_DIR"
    exec uv run flocks "$@"
  fi

  cat <<EOF >&2
[flocks] error: 未检测到 flocks 或 uv，请先执行安装脚本后重试。
[flocks] 可用命令:
  flocks start
  flocks stop
  flocks restart
  flocks status
  flocks logs
EOF
  exit 1
}

main "$@"
