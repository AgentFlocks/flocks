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
# a non-default port on purpose: only the first install passes FLOCKS_OFFLINE_PORT, every later
# run of the installer must pick the port up from /etc/flocks/flocks.env (R4)
PORT="${SMOKE_PORT:-5273}"
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
if in_ctr "env $INSTALL_ENV FLOCKS_OFFLINE_PORT=$PORT bash /var/tmp/flocks-offline.run" > /tmp/smoke-install-1.log 2>&1; then
  record PASS "unattended install exits 0"
else
  record FAIL "unattended install exits 0"
  tail -n 40 /tmp/smoke-install-1.log
  exit 1
fi
check "last line is the access URL with the chosen port" grep -q "安装完成：http://.*:$PORT\$" /tmp/smoke-install-1.log
check "FLOCKS_PORT written to the env file" in_ctr_q "grep -q '^FLOCKS_PORT=$PORT\$' /etc/flocks/flocks.env"
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
  check "firewalld port $PORT/tcp opened (runtime + permanent)" in_ctr_q "firewall-cmd --query-port=$PORT/tcp && firewall-cmd --permanent --query-port=$PORT/tcp"
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

# ---------------------------------------------------------------- local Pro bundle: --check first (must not install), then the real install
check "flocks update --check --pro-bundle describes the local bundle" in_ctr_q "flocks update --check --pro-bundle | grep -q '/opt/flocks/bundle'"
check "--check --pro-bundle installed nothing" in_ctr_q "! /opt/flocks/flocks/.venv/bin/python -c 'import flockspro' 2>/dev/null && test ! -f /var/lib/flocks/.flocks/run/pro-bundle-installed.json"

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
check "re-run without FLOCKS_OFFLINE_PORT kept port $PORT" bash -c "grep -q \"安装完成：http://.*:$PORT\$\" /tmp/smoke-install-2.log"
check "install log written" in_ctr_q "grep -q '安装完成' /var/log/flocks-offline-install.log"

# ---------------------------------------------------------------- upgrade an activated Pro instance with a package that ships no Pro bundle (R2)
# same .run, bundle/ moved aside after extraction: the installer must carry the current bundle over
# (same core), and refuse when no compatible bundle exists, without touching the running instance
if in_ctr_q "grep -q flockspro_component_version /var/lib/flocks/.flocks/run/pro-bundle-installed.json"; then
  in_ctr_q "bash /var/tmp/flocks-offline.run --noexec --target /var/tmp/staging-nobundle >/dev/null 2>&1 && mv /var/tmp/staging-nobundle/bundle /var/tmp/staging-nobundle/bundle.moved"
  if in_ctr "cd /var/tmp/staging-nobundle && env $INSTALL_ENV bash installer/install.sh" > /tmp/smoke-nobundle.log 2>&1; then
    record PASS "Pro-less package over an activated instance: installer exits 0 by carrying the bundle over"
  else
    record FAIL "Pro-less package over an activated instance: installer exits 0 by carrying the bundle over"
    tail -n 20 /tmp/smoke-nobundle.log
  fi
  check "carry-over logged" grep -q '沿用当前已激活的 Pro bundle' /tmp/smoke-nobundle.log
  check "Pro runtime still importable after Pro-less upgrade" in_ctr_q "/opt/flocks/flocks/.venv/bin/python -c 'import flockspro'"
  check "FLOCKS_PRO_BUNDLE_DIR kept in env" in_ctr_q "grep -q '^FLOCKS_PRO_BUNDLE_DIR=/opt/flocks/bundle' /etc/flocks/flocks.env"
  check "service healthy after Pro-less upgrade" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"

  # no compatible bundle anywhere → must refuse before changing anything
  in_ctr_q "bash /var/tmp/flocks-offline.run --noexec --target /var/tmp/staging-nobundle2 >/dev/null 2>&1 && mv /var/tmp/staging-nobundle2/bundle /var/tmp/staging-nobundle2/bundle.moved && mv /opt/flocks/bundle /opt/flocks/bundle.moved-by-smoke"
  parked_before="$(in_ctr "ls -d /opt/flocks/*.bak-* 2>/dev/null | wc -l" | tr -d '[:space:]')"
  if in_ctr "cd /var/tmp/staging-nobundle2 && env $INSTALL_ENV bash installer/install.sh" > /tmp/smoke-nobundle2.log 2>&1; then
    record FAIL "Pro-less package with no bundle to carry over is refused"
  else
    record PASS "Pro-less package with no bundle to carry over is refused"
  fi
  check "refusal explains itself" grep -q '本次未做任何改动' /tmp/smoke-nobundle2.log
  check "refused install left the running instance untouched" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health && /opt/flocks/flocks/.venv/bin/python -c 'import flockspro'"
  parked_after="$(in_ctr "ls -d /opt/flocks/*.bak-* 2>/dev/null | wc -l" | tr -d '[:space:]')"
  check "refused install parked nothing (bak dirs before=$parked_before after=$parked_after)" test "$parked_before" = "$parked_after"
  check "refused install parked its payload instead of leaving it for the next package" in_ctr_q "test ! -e /var/tmp/staging-nobundle2/versions.json && ls -d /var/tmp/staging-nobundle2/flocks-offline-failed-*/versions.json >/dev/null && test -d /var/tmp/staging-nobundle2/bundle.moved"
  in_ctr_q "mv /opt/flocks/bundle.moved-by-smoke /opt/flocks/bundle"
fi

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

# ---------------------------------------------------------------- Pro → OSS downgrade on the offline instance (R3)
# (after the reboot block on purpose: that block asserts the Pro component survives a reboot)
# the restart handoff must run in prebuilt mode: no uv sync, no npm; the service must come back as OSS
if in_ctr_q "curl -fsS -b /tmp/cookie http://127.0.0.1:$PORT/api/console/pro-package-status | grep -q '\"runtime_importable\": *true'"; then
  in_ctr "curl -sS -N --max-time 120 -b /tmp/cookie -H 'Content-Type: application/json' -d '{\"reason\":\"smoke\"}' -X POST http://127.0.0.1:$PORT/api/console/pro-package/downgrade" > /tmp/smoke-downgrade.log 2>&1 || true
  check "downgrade stream reached the restarting stage" grep -Eq '"stage": ?"restarting"' /tmp/smoke-downgrade.log
  back=0
  for _ in $(seq 1 60); do
    if in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health && ! /opt/flocks/flocks/.venv/bin/python -c 'import flockspro' 2>/dev/null"; then back=1; break; fi
    sleep 3
  done
  if [[ "$back" -eq 1 ]]; then record PASS "service back as OSS after offline downgrade (handoff did not need uv sync / npm)"; else record FAIL "service back as OSS after offline downgrade (handoff did not need uv sync / npm)"; in_ctr "tail -n 30 /var/lib/flocks/.flocks/logs/backend.log; grep -rh restart_handoff /var/lib/flocks/.flocks/logs/ | tail -n 20" || true; fi
  check "downgrade handoff ran in prebuilt mode (sync_skipped_prebuilt logged)" in_ctr_q "grep -rq -- 'sync_skipped_prebuilt' /var/lib/flocks/.flocks/logs/"
  check "Pro marker archived after downgrade" in_ctr_q "test ! -f /var/lib/flocks/.flocks/run/pro-bundle-installed.json"
  check "admin still logs in after downgrade" in_ctr_q "curl -fsS -c /tmp/cookie -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
fi

# ---------------------------------------------------------------- install-only run (FLOCKS_OFFLINE_SKIP_START=1, R5)
if in_ctr "env $INSTALL_ENV FLOCKS_OFFLINE_SKIP_START=1 bash /var/tmp/flocks-offline.run" > /tmp/smoke-skipstart.log 2>&1; then
  record PASS "FLOCKS_OFFLINE_SKIP_START=1 install exits 0"
else
  record FAIL "FLOCKS_OFFLINE_SKIP_START=1 install exits 0"
  tail -n 20 /tmp/smoke-skipstart.log
fi
check "skip-start run says it did not start the service" grep -q '未启动服务' /tmp/smoke-skipstart.log
check "service really not running after skip-start" in_ctr_q "! curl -fsS --max-time 3 http://127.0.0.1:$PORT/api/health"
check "staging cleaned up after skip-start" in_ctr_q "test ! -e /opt/flocks/.staging && test -f /opt/flocks/installer/install.sh"
if [[ "$INIT_MODE" == "systemd" ]]; then
  in_ctr_q "systemctl start flocks" || true
else
  in_ctr_q "flocks start --no-browser --skip-webui-build" || true
fi
up=0
for _ in $(seq 1 60); do
  if in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"; then up=1; break; fi
  sleep 3
done
if [[ "$up" -eq 1 ]]; then record PASS "manual start after the skip-start install"; else record FAIL "manual start after the skip-start install"; fi

say "----"
printf '%s\n' "${RESULTS[@]}"
say "PASS $PASS / FAIL $FAIL"
[[ "$FAIL" -eq 0 ]]
