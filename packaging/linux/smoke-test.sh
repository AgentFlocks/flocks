#!/usr/bin/env bash
#
# End-to-end smoke test for flocks-offline.run inside a throw-away container on a simulated
# customer intranet: install, bootstrap the admin, restart, re-run the installer as an upgrade
# and check that data survived. The package upgrades online like a source install: the test
# checks that the env file carries no offline pins and that a re-run switches an env file an
# older package left over, and that installs the package must not take over (a supervised one,
# one carrying a component the package does not ship) are refused without changing anything.
#
# Usage: packaging/linux/smoke-test.sh /path/to/flocks-offline.run [image]
#   image defaults to the build_image of versions.manifest.json (RHEL 8.10 / UBI 8.10, the oldest
#   supported runtime; must match the package arch).
#   Network (SMOKE_NETWORK): `intranet` (default) runs the container behind intranet-sim.sh, so the
#   server reaches only what the offline install manual opens for it (intranet-allowlist.txt) and
#   nothing else resolves or connects; the test host needs internet for that. `none` = no network.
#   Set SMOKE_KEEP=1 to keep the container (and its gateway) for inspection.
#   Set SMOKE_INIT=systemd to boot the image with systemd as PID 1 (the image must contain
#   systemd, firewalld and chrony, e.g. one committed after `dnf install systemd firewalld chrony`):
#   the installer then registers the real unit, opens firewalld and the test also simulates a
#   reboot by restarting the container.
#   Set SMOKE_BASE=/data to install below a base directory (FLOCKS_OFFLINE_BASE), on a volume of
#   its own like a separate data disk: the first install passes it, the upgrade re-run leaves it
#   out (the installer has to find the install by itself), moving the install to another base
#   and unusable bases are refused, and nothing may appear under /opt/flocks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_FILE="${1:?path to flocks-offline.run}"
IMAGE="${2:-$(sed -n 's/^[[:space:]]*"build_image"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SCRIPT_DIR/versions.manifest.json")}"
[[ -n "$IMAGE" ]] || { echo "no image given and no build_image in versions.manifest.json" >&2; exit 2; }
NAME="flocks-offline-smoke-$$"
# a non-default port on purpose: only the first install passes FLOCKS_OFFLINE_PORT, every later
# run of the installer must pick the port up from the env file (R4)
PORT="${SMOKE_PORT:-5273}"
# where the installer puts things: the default layout, or everything below $SMOKE_BASE/flocks
BASE_DIR="${SMOKE_BASE:-}"
if [[ -n "$BASE_DIR" ]]; then
  IR="$BASE_DIR/flocks/app"
  DH="$BASE_DIR/flocks/data"
  EF="$BASE_DIR/flocks/etc/flocks.env"
  INSTALL_LOG="$BASE_DIR/flocks/logs/flocks-offline-install.log"
  BASE_ENV="FLOCKS_OFFLINE_BASE=$BASE_DIR"
  BASE_VOLUME=(-v "$BASE_DIR")
else
  IR=/opt/flocks
  DH=/var/lib/flocks
  EF=/etc/flocks/flocks.env
  INSTALL_LOG=/var/log/flocks-offline-install.log
  BASE_ENV=""
  BASE_VOLUME=()
fi
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
NETWORK_MODE="${SMOKE_NETWORK:-intranet}"
GATEWAY="$NAME-gw"
case "$NETWORK_MODE" in
  intranet) NET_ARGS=(--network "container:$GATEWAY") ;;
  none) NET_ARGS=(--network none) ;;
  *) echo "SMOKE_NETWORK must be intranet or none, not $NETWORK_MODE" >&2; exit 2 ;;
esac
# cleanup removes only what this run created: a container or gateway that already had the name
# (say, kept by an earlier SMOKE_KEEP=1 run with the same PID) makes the start fail, not disappear
CONTAINER_CREATED=0
GATEWAY_CREATED=0
cleanup() {
  if [[ "${SMOKE_KEEP:-0}" == "1" ]]; then
    if [[ "$CONTAINER_CREATED" -eq 1 ]]; then say "container kept: $NAME"; fi
    if [[ "$GATEWAY_CREATED" -eq 1 ]]; then say "gateway kept: $GATEWAY (intranet-sim.sh down --name $GATEWAY)"; fi
  else
    if [[ "$CONTAINER_CREATED" -eq 1 ]]; then docker rm -f -v "$NAME" >/dev/null 2>&1 || true; fi
    if [[ "$GATEWAY_CREATED" -eq 1 ]]; then bash "$SCRIPT_DIR/intranet-sim.sh" down --name "$GATEWAY" >/dev/null 2>&1 || true; fi
  fi
}
trap cleanup EXIT

INIT_MODE="${SMOKE_INIT:-none}"
INSTALL_ENV="FLOCKS_OFFLINE_SKIP_SYSTEMD=1"
say "container $NAME from $IMAGE (network $NETWORK_MODE, init=$INIT_MODE, base=${BASE_DIR:-default})"
if [[ "$NETWORK_MODE" == "intranet" ]]; then
  # the server shares the gateway's network namespace: only the manual's endpoints get out
  bash "$SCRIPT_DIR/intranet-sim.sh" up --name "$GATEWAY"
  GATEWAY_CREATED=1
fi
if [[ "$INIT_MODE" == "systemd" ]]; then
  docker create --name "$NAME" "${NET_ARGS[@]}" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock ${BASE_VOLUME[@]+"${BASE_VOLUME[@]}"} \
    "$IMAGE" /usr/lib/systemd/systemd >/dev/null
  CONTAINER_CREATED=1
  docker start "$NAME" >/dev/null
  INSTALL_ENV=""
  for _ in $(seq 1 30); do
    if in_ctr_q "systemctl is-system-running --wait >/dev/null 2>&1 || systemctl list-units >/dev/null 2>&1"; then break; fi
    sleep 1
  done
else
  docker create --name "$NAME" "${NET_ARGS[@]}" ${BASE_VOLUME[@]+"${BASE_VOLUME[@]}"} "$IMAGE" sleep infinity >/dev/null
  CONTAINER_CREATED=1
  docker start "$NAME" >/dev/null
fi
if [[ "$NETWORK_MODE" == "intranet" ]]; then
  if bash "$SCRIPT_DIR/intranet-sim.sh" check "$NAME" > /tmp/smoke-intranet.log 2>&1; then
    record PASS "intranet: the server reaches only the manual's endpoints"
  else
    record FAIL "intranet: the server reaches only the manual's endpoints"
  fi
  sed 's/^/  /' /tmp/smoke-intranet.log
fi
docker cp "$RUN_FILE" "$NAME:/var/tmp/flocks-offline.run"
docker cp "$SCRIPT_DIR/glibc_floor.py" "$NAME:/var/tmp/glibc_floor.py"
docker cp "$SCRIPT_DIR/versions.manifest.json" "$NAME:/var/tmp/versions.manifest.json"

# ---------------------------------------------------------------- install (the manual's step 2)
check "archive self-check (--check)" in_ctr_q "bash /var/tmp/flocks-offline.run --check"
if [[ -n "$BASE_DIR" ]]; then
  # a base that does not exist is refused by the .run header, before it extracts anything
  if in_ctr "env $INSTALL_ENV FLOCKS_OFFLINE_BASE=/no-such-base bash /var/tmp/flocks-offline.run" > /tmp/smoke-base-missing.log 2>&1; then
    record FAIL "a base directory that does not exist is refused"
  else
    record PASS "a base directory that does not exist is refused"
  fi
  check "refused before extracting anything" bash -c "grep -q '不存在' /tmp/smoke-base-missing.log && docker exec $NAME test ! -e /no-such-base"
  # /var/tmp is cleaned by systemd-tmpfiles: install.sh refuses it after extraction, before any change
  if in_ctr "mkdir -p /var/tmp/smoke-base && env $INSTALL_ENV FLOCKS_OFFLINE_BASE=/var/tmp/smoke-base bash /var/tmp/flocks-offline.run" > /tmp/smoke-base-tmp.log 2>&1; then
    record FAIL "a base below /var/tmp is refused"
  else
    record PASS "a base below /var/tmp is refused"
  fi
  check "refusal names the reason, installs nothing and parks its payload" bash -c "grep -q '定期清理' /tmp/smoke-base-tmp.log && grep -q '本次未做任何改动' /tmp/smoke-base-tmp.log && docker exec $NAME bash -c 'test ! -e /usr/local/bin/flocks && test ! -e $IR/flocks && ls -d /var/tmp/smoke-base/flocks/app/.staging/flocks-offline-failed-*/versions.json'"
fi
if in_ctr "env $INSTALL_ENV $BASE_ENV FLOCKS_OFFLINE_PORT=$PORT bash /var/tmp/flocks-offline.run" > /tmp/smoke-install-1.log 2>&1; then
  record PASS "unattended install exits 0"
else
  record FAIL "unattended install exits 0"
  tail -n 40 /tmp/smoke-install-1.log
  exit 1
fi
check "last line is the access URL with the chosen port" grep -q "安装完成：http://.*:$PORT\$" /tmp/smoke-install-1.log
check "FLOCKS_PORT written to the env file" in_ctr_q "grep -q '^FLOCKS_PORT=$PORT\$' $EF"
check "staging directory removed" in_ctr_q "test ! -e $IR/.staging"
check "service user flocks exists" in_ctr_q "getent passwd flocks"
if [[ -n "$BASE_DIR" ]]; then
  check "nothing created under the default root /opt/flocks" in_ctr_q "test ! -e /opt/flocks"
  check "program, data, env file and install log below $BASE_DIR/flocks, none in the default places" in_ctr_q "test -x $IR/flocks/.venv/bin/flocks && test -d $DH/.flocks && test -f $EF && grep -q '安装完成' $INSTALL_LOG && test ! -e /etc/flocks && test ! -e /var/lib/flocks"
  check "venv rewritten for $IR (no /opt/flocks left, python link moved)" in_ctr_q "! grep -rIl --exclude='*.pyc' /opt/flocks $IR/flocks/.venv $IR/tools && readlink $IR/flocks/.venv/bin/python | grep -q '^$IR/tools/python/bin/'"
  check "service user home is $DH" in_ctr_q "getent passwd flocks | cut -d: -f6 | grep -qx '$DH'"
  check "CLI wrapper and env file point below $BASE_DIR" in_ctr_q "grep -q '^ENV_FILE=\"$EF\"' /usr/local/bin/flocks && grep -q '^FLOCKS_ROOT=$DH/.flocks' $EF && grep -q '^FLOCKS_INSTALL_ROOT=$IR' $EF && grep -q '^UV_PYTHON=$IR/tools/python/bin/python3' $EF"
fi
# the package upgrades online like a source install; the offline pins older packages wrote must
# never appear (OFFLINE_ONLY_KEYS_RE in install.sh)
OFFLINE_PINS_RE='^(FLOCKS_OFFLINE_INSTALL|FLOCKS_UPDATE_CHANNEL|UV_OFFLINE|npm_config_offline)='
check "$EF written (source mode, no offline pins)" in_ctr_q "grep -q '^FLOCKS_HOST=0.0.0.0' $EF && grep -q '^FLOCKS_DEPLOY_MODE=source\$' $EF && ! grep -Eq '$OFFLINE_PINS_RE' $EF"
check "no bundle/ in the install root" in_ctr_q "test ! -e $IR/bundle"
check "$EF has UV_NO_SYNC=1 (the skills' uv run uses the shipped venv as it is)" in_ctr_q "grep -qx 'UV_NO_SYNC=1' $EF"
check "install_profile.json zh-CN" in_ctr_q "grep -q zh-CN $DH/.flocks/config/install_profile.json"
check "health endpoint answers" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"
check "WebUI index served to a browser" in_ctr_q "curl -fsSL -H 'Accept: text/html' http://127.0.0.1:$PORT/ | grep -qi '<html'"
check "listening on all interfaces" in_ctr_q "curl -fsS http://\$(hostname -i 2>/dev/null || echo 127.0.0.1):$PORT/api/health"
check "CLI wrapper works as root" in_ctr_q "flocks status | grep 'PID=' >/dev/null"

# ---------------------------------------------------------------- runs on this distribution, offline
glibc_check() {
  local label="$1"
  if in_ctr "$IR/tools/python/bin/python3 /var/tmp/glibc_floor.py --manifest /var/tmp/versions.manifest.json $IR/tools $IR/flocks" > /tmp/smoke-glibc.log 2>&1; then
    record PASS "$label"
  else
    record FAIL "$label"
  fi
  sed -n 's/^glibc_floor: /  /p' /tmp/smoke-glibc.log | head -n 8
}
glibc_check "every shipped binary fits glibc_floor"
check "cryptography native bindings load (wecom / weixin / ssh / jwt need them)" in_ctr_q "cd / && $IR/flocks/.venv/bin/python -c 'import cryptography.hazmat.bindings._rust; from cryptography.fernet import Fernet; f = Fernet(Fernet.generate_key()); assert f.decrypt(f.encrypt(b\"x\")) == b\"x\"'"
# what the agent's skills run: bash tool + `uv run python -c ...` from the repo root with the
# service environment; uv must use the shipped venv as is, not rebuild flocks or fetch anything
if in_ctr "runuser -u flocks -- bash -c 'set -a; . $EF; set +a; cd $IR/flocks && timeout 120 uv run python -c \"from flocks.skill.onboarding_status import print_onboarding_preflight_status; print_onboarding_preflight_status()\"'" > /tmp/smoke-uvrun.log 2>&1; then
  record PASS "skills' uv run python works offline as the service user"
else
  record FAIL "skills' uv run python works offline as the service user"
  tail -n 20 /tmp/smoke-uvrun.log
fi
check "uv run left the venv alone (no build, install or download)" bash -c "! grep -Eq 'Building|Installed|Uninstalled|Failed to download' /tmp/smoke-uvrun.log"
if [[ "$NETWORK_MODE" == "intranet" ]]; then
  # the installer opened firewalld (and reloaded it, with systemd) in the namespace the rules live in
  check "intranet still in force after the install" bash "$SCRIPT_DIR/intranet-sim.sh" check "$NAME"
fi
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
# read-only on purpose: with online upgrades POST /api/update/apply would start a real upgrade
check "update check reports source deploy mode, upgrade allowed" in_ctr_q "out=\$(curl -fsS --max-time 120 -b /tmp/cookie 'http://127.0.0.1:$PORT/api/update/check?edition=flocks'); grep -q '\"deploy_mode\": *\"source\"' <<<\"\$out\" && grep -q '\"update_allowed\": *true' <<<\"\$out\""

# ---------------------------------------------------------------- re-run the installer (upgrade / repair)
# pretend the env file was left by an older offline package with its offline pins: the re-run must
# switch it over to online upgrades, the plain merge would keep the pins
in_ctr_q "sed -i 's/^FLOCKS_DEPLOY_MODE=.*/FLOCKS_DEPLOY_MODE=offline/' $EF && printf 'FLOCKS_OFFLINE_INSTALL=1\nFLOCKS_UPDATE_CHANNEL=offline-old\nUV_OFFLINE=1\nnpm_config_offline=true\n' >> $EF"
# (without FLOCKS_OFFLINE_BASE on purpose: an install below a base has to be found and upgraded where it is)
if in_ctr "env $INSTALL_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-install-2.log 2>&1; then
  record PASS "re-running the installer exits 0"
else
  record FAIL "re-running the installer exits 0"
  tail -n 40 /tmp/smoke-install-2.log
fi
if [[ -n "$BASE_DIR" ]]; then
  check "re-run without FLOCKS_OFFLINE_BASE upgraded the install below $BASE_DIR" grep -q '按原目录升级' /tmp/smoke-install-2.log
  check "re-run extracted to the default place and left nothing there" in_ctr_q "test ! -e /opt/flocks"
fi
check "old program dirs parked, not deleted" in_ctr_q "ls -d $IR/flocks.bak-* $IR/tools.bak-*"
check "data survived the re-run (admin login)" in_ctr_q "curl -fsS -c /tmp/cookie2 -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
check "re-run switched an offline env file over to online upgrades" in_ctr_q "grep -q '^FLOCKS_DEPLOY_MODE=source\$' $EF && ! grep -Eq '$OFFLINE_PINS_RE' $EF"
check "restarted service runs in source mode" in_ctr_q "out=\$(curl -fsS --max-time 120 -b /tmp/cookie2 'http://127.0.0.1:$PORT/api/update/check?edition=flocks'); grep -q '\"deploy_mode\": *\"source\"' <<<\"\$out\""
check "env file kept and backed up" in_ctr_q "ls $EF.bak-* && grep -q '^FLOCKS_HOST=0.0.0.0' $EF"
check "service healthy after re-run" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"
check "re-run without FLOCKS_OFFLINE_PORT kept port $PORT" bash -c "grep -q \"安装完成：http://.*:$PORT\$\" /tmp/smoke-install-2.log"
check "install log written" in_ctr_q "grep -q '安装完成' $INSTALL_LOG"

# ---------------------------------------------------------------- base directory: never moved, carried on after an interruption
if [[ -n "$BASE_DIR" ]]; then
  # an install is upgraded where it is: the .run header sees the install and stops before extracting
  if in_ctr "mkdir -p /srv/other-base && env $INSTALL_ENV FLOCKS_OFFLINE_BASE=/srv/other-base bash /var/tmp/flocks-offline.run" > /tmp/smoke-base-move.log 2>&1; then
    record FAIL "moving the install to another base is refused"
  else
    record PASS "moving the install to another base is refused"
  fi
  check "refusal points at the re-run without FLOCKS_OFFLINE_BASE, nothing extracted" bash -c "grep -q '去掉 FLOCKS_OFFLINE_BASE' /tmp/smoke-base-move.log && grep -q '本次未做任何改动' /tmp/smoke-base-move.log && docker exec $NAME test ! -e /srv/other-base/flocks"
  check "instance untouched after the refusals" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health && grep -q '^ENV_FILE=\"$EF\"' /usr/local/bin/flocks"

  # an upgrade that stopped half-way: service stopped, old program parked, new one never moved in.
  # A re-run without the variable must carry on in the same place, with the same data and port.
  if [[ "$INIT_MODE" == "systemd" ]]; then
    in_ctr_q "systemctl stop flocks" || true
  else
    in_ctr_q "flocks stop" || true
  fi
  in_ctr_q "mv $IR/flocks $IR/flocks.bak-interrupted && mv $IR/tools $IR/tools.bak-interrupted"
  if in_ctr "env $INSTALL_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-base-unfinished.log 2>&1; then
    record PASS "re-run after an interrupted upgrade exits 0"
  else
    record FAIL "re-run after an interrupted upgrade exits 0"
    tail -n 30 /tmp/smoke-base-unfinished.log
  fi
  check "it said it carries on the unfinished install below $BASE_DIR" grep -q '没有做完，按原目录接着装' /tmp/smoke-base-unfinished.log
  check "program back in $IR, nothing under /opt/flocks" in_ctr_q "test -f $IR/flocks/pyproject.toml && test ! -e /opt/flocks"
  check "data and port survived the interrupted upgrade (admin login on $PORT)" in_ctr_q "curl -fsS -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
fi

# ---------------------------------------------------------------- installs the package must not take over
# a component the package does not ship (its marker in the data dir), and an install a supervising process
# manages (deployment.json says managed): both refused before the running instance is touched
refused_takeover() {
  local label="$1" log="$2"
  if in_ctr "env $INSTALL_ENV $BASE_ENV bash /var/tmp/flocks-offline.run" > "$log" 2>&1; then
    record FAIL "$label is refused"
  else
    record PASS "$label is refused"
  fi
  check "$label: refusal changed nothing" bash -c "grep -q '本次未做任何改动' $log"
  check "$label: instance still serving" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health && test -f $IR/flocks/pyproject.toml"
}
in_ctr_q "printf '{\"bundle_version\": \"v0\"}' > $DH/.flocks/run/sample-bundle-installed.json"
refused_takeover "package over an install carrying a component it does not ship" /tmp/smoke-component.log
check "refusal names the marker" bash -c "grep -q 'sample-bundle-installed.json' /tmp/smoke-component.log"
in_ctr_q "rm -f $DH/.flocks/run/sample-bundle-installed.json"
in_ctr_q "printf '{\"mode\": \"managed\", \"managed_by\": \"supervisor\"}' > $DH/.flocks/config/deployment.json"
refused_takeover "package over a supervised install" /tmp/smoke-managed.log
check "refusal names the descriptor" bash -c "grep -q 'deployment.json' /tmp/smoke-managed.log"
in_ctr_q "rm -f $DH/.flocks/config/deployment.json"

# ---------------------------------------------------------------- an online upgrade left a newer version on the machine
# the handoff swaps the source in before uv sync / npm and does not roll back, so the package is the way
# back from an upgrade that never finished; a finished newer version must not be overwritten silently
pkg_version="$(docker exec "$NAME" sed -n 's/.*"core_version": *"\([^"]*\)".*/\1/p' $IR/versions.json)"
in_ctr_q "sed -i 's/^version = .*/version = \"v2099.1.1\"/' $IR/flocks/pyproject.toml && mkdir -p $DH/.flocks/version && printf '2099.1.1\n' > $DH/.flocks/version/.current_version"
if in_ctr "env $INSTALL_ENV $BASE_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-downgrade-refused.log 2>&1; then
  record FAIL "older package over a finished newer version is refused"
else
  record PASS "older package over a finished newer version is refused"
fi
check "downgrade refusal names the override and changed nothing" bash -c "grep -q 'FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1' /tmp/smoke-downgrade-refused.log && grep -q '本次未做任何改动' /tmp/smoke-downgrade-refused.log"
check "instance untouched after the downgrade refusal" in_ctr_q "grep -q 'v2099.1.1' $IR/flocks/pyproject.toml && curl -fsS http://127.0.0.1:$PORT/api/health"
in_ctr_q "printf '{\"phase\": \"failed\", \"version\": \"v2099.1.1\", \"current_version\": \"%s\", \"failed_stage\": \"sync\"}' '${pkg_version#v}' > $DH/.flocks/run/upgrade-result.json"
if in_ctr "env $INSTALL_ENV $BASE_ENV bash /var/tmp/flocks-offline.run" > /tmp/smoke-downgrade-recover.log 2>&1; then
  record PASS "package recovers an online upgrade that never finished"
else
  record FAIL "package recovers an online upgrade that never finished"
  tail -n 20 /tmp/smoke-downgrade-recover.log
fi
check "recovery logged" grep -q '没有完成' /tmp/smoke-downgrade-recover.log
check "code back to the package version" in_ctr_q "grep -q '^version = \"$pkg_version\"' $IR/flocks/pyproject.toml"
check "version marker rewritten to the package version" in_ctr_q "grep -qx '${pkg_version#v}' $DH/.flocks/version/.current_version"
check "used upgrade record parked, cannot let a later package through" in_ctr_q "test ! -f $DH/.flocks/run/upgrade-result.json && ls $DH/.flocks/run/upgrade-result.json.bak-*"
check "service healthy after the recovery" in_ctr_q "curl -fsS http://127.0.0.1:$PORT/api/health"

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
  if [[ "$NETWORK_MODE" == "intranet" ]]; then
    check "intranet still in force after the reboot" bash "$SCRIPT_DIR/intranet-sim.sh" check "$NAME"
  fi
  check "admin can still log in after reboot" in_ctr_q "curl -fsS -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"Smoke-Test-2026\"}' http://127.0.0.1:$PORT/api/auth/login | grep -q admin"
fi

# ---------------------------------------------------------------- install-only run (FLOCKS_OFFLINE_SKIP_START=1, R5)
if in_ctr "env $INSTALL_ENV $BASE_ENV FLOCKS_OFFLINE_SKIP_START=1 bash /var/tmp/flocks-offline.run" > /tmp/smoke-skipstart.log 2>&1; then
  record PASS "FLOCKS_OFFLINE_SKIP_START=1 install exits 0"
else
  record FAIL "FLOCKS_OFFLINE_SKIP_START=1 install exits 0"
  tail -n 20 /tmp/smoke-skipstart.log
fi
check "skip-start run says it did not start the service" grep -q '未启动服务' /tmp/smoke-skipstart.log
check "service really not running after skip-start" in_ctr_q "! curl -fsS --max-time 3 http://127.0.0.1:$PORT/api/health"
check "staging cleaned up after skip-start" in_ctr_q "test ! -e $IR/.staging && test -f $IR/installer/install.sh"
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
