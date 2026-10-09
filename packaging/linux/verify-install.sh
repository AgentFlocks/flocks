#!/usr/bin/env bash
#
# Acceptance checks for an installed offline package, run ON the target host as root
# (no docker). Mirrors smoke-test.sh but against the real machine: service, systemd,
# firewalld, admin bootstrap, offline Pro bundle install (packages that ship one), restart,
# optional re-run of the installer. Prints one PASS/FAIL line per check and a final summary.
# On an open-source package (no bundle/ in the install root) upgrades go online: the script only
# reads the update status and never starts an upgrade. The layout (default, or below a base
# directory set with FLOCKS_OFFLINE_BASE) is read from the /usr/local/bin/flocks the installer wrote;
# --rerun leaves FLOCKS_OFFLINE_BASE out, the installer has to find the install by itself.
#
# Usage (as root):
#   packaging/linux/verify-install.sh [--port <port>] [--rerun /var/tmp/flocks-offline.run]
#     --port defaults to the FLOCKS_PORT in the env file (5173 when nothing is installed)
#       [--admin-user admin] [--admin-pass '<password>'] [--skip-pro]
#
# The admin account is created only when the instance has not been bootstrapped yet;
# without --admin-pass a random password is generated and printed at the end. Pass the
# same --admin-pass on later runs against an already bootstrapped instance.

set -euo pipefail

PORT=""
RERUN=""
ADMIN_USER="admin"
ADMIN_PASS=""
SKIP_PRO=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --rerun) RERUN="$2"; shift 2 ;;
    --admin-user) ADMIN_USER="$2"; shift 2 ;;
    --admin-pass) ADMIN_PASS="$2"; shift 2 ;;
    --skip-pro) SKIP_PRO=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# where the installer put things: the wrapper names the env file and the CLI inside the install root
WRAPPER=/usr/local/bin/flocks
ENV_FILE="$(sed -n 's/^ENV_FILE="\(.*\)"$/\1/p' "$WRAPPER" 2>/dev/null | head -n 1 || true)"
WRAPPED_CLI="$(sed -n 's/^FLOCKS_CLI="\(.*\)"$/\1/p' "$WRAPPER" 2>/dev/null | head -n 1 || true)"
IR="${WRAPPED_CLI%/flocks/.venv/bin/flocks}"
if [[ -z "$WRAPPED_CLI" || "$IR" == "$WRAPPED_CLI" ]]; then IR=/opt/flocks; fi
[[ -n "$ENV_FILE" ]] || ENV_FILE=/etc/flocks/flocks.env
installed_data_home() {
  # the HOME the service runs with; HOME="/data/flocks/data" is as valid in an EnvironmentFile as the
  # unquoted form the installer writes
  local home
  home="$(sed -n 's/^HOME=//p' "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  if [[ "$home" =~ ^\"(.*)\"$ || "$home" =~ ^\'(.*)\'$ ]]; then home="${BASH_REMATCH[1]}"; fi
  printf '%s\n' "${home:-/var/lib/flocks}"
}
DH="$(installed_data_home)"
if [[ "$IR" == */flocks/app ]]; then
  INSTALL_LOG="${IR%/flocks/app}/flocks/logs/flocks-offline-install.log"
else
  INSTALL_LOG=/var/log/flocks-offline-install.log
fi

installed_port() {
  # the port the installed instance actually uses; 5173 only when nothing is installed yet.
  # (--rerun passes this port to the installer as an explicit override, so a wrong default
  #  would silently move the instance to another port)
  local from_env
  from_env="$(sed -n 's/^FLOCKS_PORT=//p' "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d '[:space:]"'"'"'' || true)"
  if [[ "$from_env" =~ ^[0-9]+$ ]] && (( from_env >= 1 && from_env <= 65535 )); then
    printf '%s\n' "$from_env"
  else
    printf '5173\n'
  fi
}
[[ -n "$PORT" ]] || PORT="$(installed_port)"
BASE="http://127.0.0.1:${PORT}"
COOKIE="$(mktemp)"
GENERATED_PASS=0
gen_password() {
  # never bake a default password into the script: generate one per run and print it.
  # Not `tr </dev/urandom | head -c 16`: head closes the pipe early, tr dies with SIGPIPE
  # and under `set -o pipefail` that is exit 141 before a single check has run.
  local py
  for py in "$IR/tools/python/bin/python3" python3; do
    if command -v "$py" >/dev/null 2>&1; then
      "$py" -c 'import secrets, string; a = string.ascii_letters + string.digits; print("Verify-" + "".join(secrets.choice(a) for _ in range(16)))' && return 0
    fi
  done
  # no python at all: read a fixed amount so nothing upstream is left writing into a closed pipe
  printf 'Verify-%s\n' "$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-16)"
}
if [[ -z "$ADMIN_PASS" ]]; then
  ADMIN_PASS="$(gen_password)"
  GENERATED_PASS=1
fi
PASS=0
FAIL=0
RESULTS=()
trap 'rm -f "$COOKIE"' EXIT

say() { printf '[verify] %s\n' "$*"; }
record() {
  local status="$1" label="$2"
  if [[ "$status" == PASS ]]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
  RESULTS+=("$status  $label")
  say "$status  $label"
}
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then record PASS "$label"; else record FAIL "$label"; fi
}
api() { curl -fsS --max-time 10 -b "$COOKIE" "$@"; }
login() {
  curl -fsS --max-time 10 -c "$COOKIE" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" "$BASE/api/auth/login" | grep -q "\"username\""
}
bootstrap_admin() {
  curl -fsS --max-time 10 -c "$COOKIE" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" "$BASE/api/auth/bootstrap-admin" | grep -q '"username"'
}
me_ok() { api "$BASE/api/auth/me" | grep -q '"username"'; }
index_ok() { curl -fsSL --max-time 10 -H 'Accept: text/html' "$BASE/" | grep -qi '<html'; }
listening_ok() { ss -ltn | grep -q "0.0.0.0:${PORT} "; }
status_ok() { flocks status | grep 'PID=' >/dev/null; }  # no -q: let the CLI finish writing (pipefail)
restart_ok() { systemctl restart flocks && curl -fsS --max-time 10 "$BASE/api/health"; }
pro_not_installed() { api "$BASE/api/console/pro-package-status" | grep -q '"runtime_importable": *false'; }
pro_installed() { login && api "$BASE/api/console/pro-package-status" | grep -q '"runtime_importable": *true'; }
pro_license_route() { api "$BASE/api/flockspro/license/status" | grep -qv flockspro_not_installed; }
pro_log_local() { grep -q 'Using local Flocks Pro bundle' /tmp/verify-pro.log && ! grep -qi 'Downloading Flocks Pro bundle\|uv sync' /tmp/verify-pro.log; }
server_restart_ok() { flocks restart --server-only && curl -fsS --max-time 10 "$BASE/api/health"; }
parked_ok() { ls -d "$IR"/flocks.bak-* "$IR"/tools.bak-*; }

[[ "$(id -u)" -eq 0 ]] || { echo "run as root" >&2; exit 2; }
HAVE_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then HAVE_SYSTEMD=1; fi

# ---------------------------------------------------------------- installation layout
say "layout: program ${IR}, data ${DH}, env ${ENV_FILE}, install log ${INSTALL_LOG}"
check "install root ${IR} present" test -x "$IR/flocks/.venv/bin/flocks"
check "service user flocks exists" getent passwd flocks
check "env file has FLOCKS_HOST=0.0.0.0" grep -q '^FLOCKS_HOST=0.0.0.0' "$ENV_FILE"
check "env file pins the port" grep -q "^FLOCKS_PORT=${PORT}$" "$ENV_FILE"
check "install_profile.json zh-CN" grep -q zh-CN "$DH/.flocks/config/install_profile.json"
check "no staging dir left" test ! -e "$IR/.staging"
check "install log ends with the access URL" grep -q '安装完成：http://' "$INSTALL_LOG"
check "CLI wrapper works as root" status_ok

# ---------------------------------------------------------------- service
check "health endpoint answers" curl -fsS --max-time 10 "$BASE/api/health"
check "WebUI index served to a browser" index_ok
check "listening on 0.0.0.0:${PORT}" listening_ok
if [[ "$HAVE_SYSTEMD" -eq 1 ]]; then
  check "systemd unit active" systemctl is-active flocks
  check "systemd unit enabled" systemctl is-enabled flocks
  check "systemctl restart flocks works" restart_ok
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    check "firewalld port ${PORT}/tcp opened" firewall-cmd --query-port="${PORT}/tcp"
  else
    say "SKIP  firewalld not running on this host"
  fi
fi

# ---------------------------------------------------------------- admin (manual step 3)
if curl -fsS --max-time 10 "$BASE/api/auth/bootstrap-status" | grep -q '"bootstrapped": *false'; then
  check "create admin and log in (bootstrap-admin)" bootstrap_admin
else
  check "log in with existing admin" login
fi
check "session valid (/api/auth/me)" me_ok
offline_mode_ok() {
  api "$BASE/api/update/check?edition=flocks&force=true" | grep -q '"deploy_mode": *"offline"' \
    && api "$BASE/api/update/check?edition=flocks" | grep -q '"update_allowed": *false'
}
upgrade_refused_ok() {
  curl -sS -N --max-time 20 -b "$COOKIE" -X POST "$BASE/api/update/apply?edition=flocks" | grep -q 'flocks-offline.run' \
    && test -f "$IR/flocks/pyproject.toml"
}
online_env_ok() {
  grep -q '^FLOCKS_DEPLOY_MODE=source$' "$ENV_FILE" \
    && ! grep -Eq '^(FLOCKS_OFFLINE_INSTALL|FLOCKS_UPDATE_CHANNEL|FLOCKS_PRO_BUNDLE_DIR|UV_OFFLINE|npm_config_offline)=' "$ENV_FILE"
}
source_mode_ok() {
  # read-only: this instance upgrades online, so never POST /api/update/apply here
  local out
  out="$(curl -fsS --max-time 90 -b "$COOKIE" "$BASE/api/update/check?edition=flocks")"
  grep -q '"deploy_mode": *"source"' <<<"$out" && grep -q '"update_allowed": *true' <<<"$out"
}
if [[ -f "$IR/bundle/manifest.json" ]]; then
  check "update check reports offline deploy mode, upgrade not allowed" offline_mode_ok
  check "network upgrade refused before touching files" upgrade_refused_ok
else
  say "open-source package: upgrades (Pro in the WebUI, core updates) go online"
  check "env file has no offline pins (FLOCKS_DEPLOY_MODE=source)" online_env_ok
  check "update check reports source deploy mode, upgrade allowed" source_mode_ok
fi

# ---------------------------------------------------------------- Pro bundle from disk (what 开始升级 does without Console)
if [[ "$SKIP_PRO" -eq 0 && -f "$IR/bundle/manifest.json" ]]; then
  if api "$BASE/api/console/pro-package-status" | grep -q '"runtime_importable": *true'; then
    say "Pro component already installed; skipping local bundle install"
  else
    check "Pro not installed before upgrade" pro_not_installed
    if flocks update --pro-bundle --no-restart > /tmp/verify-pro.log 2>&1; then
      record PASS "flocks update --pro-bundle installs from $IR/bundle"
    else
      record FAIL "flocks update --pro-bundle installs from $IR/bundle"
      tail -n 20 /tmp/verify-pro.log
    fi
    check "Pro install used the local bundle, no download / uv sync" pro_log_local
    check "marker written" test -s "$DH/.flocks/run/pro-bundle-installed.json"
    check "flockspro importable in the venv" "$IR/flocks/.venv/bin/python" -c 'import flockspro'
    check "backend restart after Pro install" server_restart_ok
  fi
  check "Pro package reported as installed" pro_installed
  check "license status served by the Pro runtime" pro_license_route
fi

# ---------------------------------------------------------------- optional: re-run the installer (upgrade / repair)
if [[ -n "$RERUN" ]]; then
  [[ -f "$RERUN" ]] || { say "no such file: $RERUN"; exit 2; }
  if FLOCKS_OFFLINE_PORT="$PORT" bash "$RERUN" > /tmp/verify-rerun.log 2>&1; then
    record PASS "re-running the installer exits 0"
  else
    record FAIL "re-running the installer exits 0"
    tail -n 30 /tmp/verify-rerun.log
  fi
  check "old program dirs parked, not deleted" parked_ok
  check "data survived the re-run (admin login)" login
  check "service healthy after re-run" curl -fsS --max-time 10 "$BASE/api/health"
  if [[ -f "$IR/bundle/manifest.json" && "$SKIP_PRO" -eq 0 ]]; then
    check "Pro component re-installed into the new venv" "$IR/flocks/.venv/bin/python" -c 'import flockspro'
  fi
fi

say "----"
printf '%s\n' "${RESULTS[@]}"
if [[ "$GENERATED_PASS" -eq 1 ]]; then
  say "admin account used for these checks: $ADMIN_USER / $ADMIN_PASS (pass --admin-pass to reuse it)"
fi
say "PASS $PASS / FAIL $FAIL"
[[ "$FAIL" -eq 0 ]]
