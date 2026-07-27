#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/flocks}"
service_started=false

cleanup() {
  trap - EXIT INT TERM
  if [[ "$service_started" == true ]]; then
    flocks stop || true
  fi
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"
flocks start --no-browser --skip-webui-build
service_started=true
flocks logs --follow
