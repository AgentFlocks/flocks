#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"
PY_MIGRATION_SCRIPT="$ROOT_DIR/scripts/migrate_legacy_task_tables.py"

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
  case "$ACTION" in
    start|restart|"")
      ;;
    *)
      exit 0
      ;;
  esac

  [[ -f "$PY_MIGRATION_SCRIPT" ]] || exit 0

  refresh_path

  if command -v python3 >/dev/null 2>&1; then
    python3 "$PY_MIGRATION_SCRIPT" || true
    exit 0
  fi

  if command -v python >/dev/null 2>&1; then
    python "$PY_MIGRATION_SCRIPT" || true
    exit 0
  fi

  if command -v uv >/dev/null 2>&1; then
    (
      cd "$ROOT_DIR"
      uv run python "$PY_MIGRATION_SCRIPT"
    ) || true
  fi
}

main "$@"
