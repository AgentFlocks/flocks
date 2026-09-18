#!/usr/bin/env bash
#
# End-to-end smoke test for flocks-offline.run inside a throw-away container with
# NO network: install, bootstrap the admin, install the bundled Pro component from
# disk, restart, re-run the installer as an upgrade and check that data survived.
#
# Usage: packaging/linux/smoke-test.sh /path/to/flocks-offline.run [image]
#   image defaults to quay.io/centos/centos:stream9 (must match the package arch).
#   Set SMOKE_KEEP=1 to keep the container for inspection.
#   Set SMOKE_INIT=systemd to boot the image with systemd as PID 1 (the image must contain
#   systemd, firewalld and chrony, e.g. one committed after `dnf install systemd firewalld chrony`):
#   the installer then registers the real unit, opens firewalld and the test also simulates a
#   reboot by restarting the container.

set -euo pipefail

RUN_FILE="${1:?path to flocks-offline.run}"
IMAGE="${2:-quay.io/centos/centos:stream9}"
NAME="flocks-offline-smoke-$$"
PORT=5173
PASS=0
FAIL=0
RESULTS=()

[[ -f "$RUN_FILE" ]] || { echo "no such file: $RUN_FILE" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

say() { printf '[smoke] %s\n' "$*"; }
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
in_ctr() { docker exec "$NAME" bash -c "$*"; }
in_ctr_q() { docker exec "$NAME" bash -c "$*" >/dev/null 2>&1; }
cleanup() {
  if [[ "${SMOKE_KEEP:-0}" == "1" ]]; then
    say "container kept: $NAME"
  else
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

INIT_MODE="${SMOKE_INIT:-none}"
INSTALL_ENV="FLOCKS_OFFLINE_SKIP_SYSTEMD=1"
say "container $NAME from $IMAGE (network none, init=$INIT_MODE)"
if [[ "$INIT_MODE" == "systemd" ]]; then
  docker run -d --name "$NAME" --network none --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    "$IMAGE" /usr/lib/systemd/systemd >/dev/null
  INSTALL_ENV=""
  for _ in $(seq 1 30); do
    if in_ctr_q "systemctl is-system-running --wait >/dev/null 2>&1 || systemctl list-units >/dev/null 2>&1"; then break; fi
    sleep 1
  done
else
  docker run -d --name "$NAME" --network none "$IMAGE" sleep infinity >/dev/null
fi
docker cp "$RUN_FILE" "$NAME:/var/tmp/flocks-offline.run"

# ---------------------------------------------------------------- install (the manual's step 2)
check "archive self-check (--check)" in_ctr_q "bash /var/tmp/flocks-offline.run --check"
if in_ctr "env $INSTALL_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-install-1.log 2>&1; then
  record PASS "unattended install exits 0"
else
  record FAIL "unattended install exits 0"
  tail -n 40 /tmp/smoke-install-1.log
  exit 1
fi
check "last line is the access URL" grep -q '安装完成：http://' /tmp/smoke-install-1.log
check "staging directory removed" in_ctr_q "test ! -e /opt/flocks/.staging"
check "service user flocks exists" in_ctr_q "getent passwd flocks"
check "/etc/flocks/flocks.env written" in_ctr_q "grep -q '^FLOCKS_HOST=0.0.0.0' /etc/flocks/flocks.env && grep -q '^FLOCKS_PRO_BUNDLE_DIR=/opt/flocks/bundle' /etc/flocks/flocks.env"
check "install_profile.json zh-CN" in_ctr_q "grep -q zh-CN /var/lib/flocks/.flocks/config/install_profile.json"
check "health endpoint answers" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"
check "WebUI index served to a browser" in_ctr_q "curl -fsSL -H 'Accept: text/html' http://127.0.0.1:$PORT/ | grep -qi '<html'"
check "listening on all interfaces" in_ctr_q "curl -fsS http://\$(hostname -i 2>/dev/null || echo 127.0.0.1):$PORT/api/health"
check "CLI wrapper works as root" in_ctr_q "flocks status | grep 'PID=' >/dev/null"
if [[ "$INIT_MODE" == "systemd" ]]; then
  check "systemd unit active" in_ctr_q "systemctl is-active flocks"
  check "systemd unit enabled at boot" in_ctr_q "systemctl is-enabled flocks"
  check "firewalld port 5173/tcp opened" in_ctr_q "firewall-cmd --query-port=5173/tcp"
  check "chronyd enabled" in_ctr_q "systemctl is-enabled chronyd"
  check "systemctl restart flocks works" in_ctr_q "systemctl restart flocks && curl -fsS http://127.0.0.1:$PORT/api/health"
fi

# ---------------------------------------------------------------- bootstrap admin (the manual's step 3)
check "bootstrap status reports admin missing" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/auth/bootstrap-status | grep -q '\"bootstrapped\": *false'"
check "create admin and log in" in_ctr_q "curl -fsS -c /tmp/cookie -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/bootstrap-admin | grep -q admin"
check "session cookie valid (/api/auth/me)" in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/auth/me | grep -q admin"
check "Pro not installed before upgrade" in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/console/pro-package-status | grep -q '\"runtime_importable\": *false'"
check "edition reported as oss" in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/console/pro-package-status | grep -q '\"install_marker_present\": *false'"
check "update check reports offline deploy mode, upgrade not allowed" in_ctr_q "curl -fsS -b /tmp/cookie 'http://127.0.0.1:$PORT/api/update/check?edition=flocks&force=true' | grep -q '\"deploy_mode\": *\"offline\"' && curl -fsS -b /tmp/cookie 'http://127.0.0.1:$PORT/api/update/check?edition=flocks' | grep -q '\"update_allowed\": *false'"
check "network upgrade refused before touching files" in_ctr_q "curl -sS -N --max-time 20 -b /tmp/cookie -X POST 'http://127.0.0.1:$PORT/api/update/apply?edition=flocks' | grep -q 'flocks-offline.run' && test -f /opt/flocks/flocks/pyproject.toml"

# ---------------------------------------------------------------- local Pro bundle install (what "开始升级" does, without Console)
if in_ctr "flocks update --pro-bundle --no-restart" > /tmp/smoke-pro.log 2>&1; then
  record PASS "flocks update --pro-bundle installs from /opt/flocks/bundle offline"
else
  record FAIL "flocks update --pro-bundle installs from /opt/flocks/bundle offline"
  tail -n 20 /tmp/smoke-pro.log
fi
check "no download / no uv sync in the Pro install" bash -c "! grep -qi 'Downloading Flocks Pro bundle\|uv sync' /tmp/smoke-pro.log && grep -q 'Using local Flocks Pro bundle' /tmp/smoke-pro.log"
check "pro-bundle-installed.json marker written" in_ctr_q "grep -q rel_offline_test_1 /var/lib/flocks/.flocks/run/pro-bundle-installed.json"
check "flockspro importable in the venv" in_ctr_q "/opt/flocks/flocks/.venv/bin/python -c 'import flockspro'"
check "backend restart after Pro install" in_ctr_q "flocks restart --server-only"
check "Pro package reported as installed after restart" in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/console/pro-package-status | grep -q '\"runtime_importable\": *true'"
check "license status route served by Pro runtime" in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/flockspro/license/status | grep -qv flockspro_not_installed"

# ---------------------------------------------------------------- re-run the installer (upgrade / repair)
if in_ctr "env $INSTALL_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-install-2.log 2>&1; then
  record PASS "re-running the installer exits 0"
else
  record FAIL "re-running the installer exits 0"
  tail -n 40 /tmp/smoke-install-2.log
fi
check "old program dirs parked, not deleted" in_ctr_q "ls -d /opt/flocks/flocks.bak-* /opt/flocks/tools.bak-*"
check "data survived the re-run (admin login)" in_ctr_q "curl -fsS -c /tmp/cookie2 -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
check "Pro component re-installed into the new venv" in_ctr_q "/opt/flocks/flocks/.venv/bin/python -c 'import flockspro' && grep -q flockspro_component_version /var/lib/flocks/.flocks/run/pro-bundle-installed.json"
check "env file kept and backed up" in_ctr_q "ls /etc/flocks/flocks.env.bak-* && grep -q '^FLOCKS_HOST=0.0.0.0' /etc/flocks/flocks.env"
check "service healthy after re-run" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"
check "install log written" in_ctr_q "grep -q '安装完成' /var/log/flocks-offline-install.log"

# ---------------------------------------------------------------- reboot simulation (systemd mode only)
if [[ "$INIT_MODE" == "systemd" ]]; then
  say "restarting the container to simulate a reboot"
  docker restart "$NAME" >/dev/null
  booted=0
  for _ in $(seq 1 90); do
    if in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"; then booted=1; break; fi
    sleep 2
  done
  if [[ "$booted" -eq 1 ]]; then record PASS "service back after reboot without manual start"; else record FAIL "service back after reboot without manual start"; fi
  check "admin can still log in after reboot" in_ctr_q "curl -fsS -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
  check "Pro component survives reboot" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/auth/bootstrap-status >/dev/null && /opt/flocks/flocks/.venv/bin/python -c 'import flockspro'"
fi

say "----"
printf '%s\n' "${RESULTS[@]}"
say "PASS $PASS / FAIL $FAIL"
[[ "$FAIL" -eq 0 ]]
