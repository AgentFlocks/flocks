#!/usr/bin/env bash
#
# Flocks offline installer (runs unattended as root from the self-extracting
# flocks-offline.run). Installs or upgrades /opt/flocks, creates the `flocks`
# service user, writes /etc/flocks/flocks.env, registers a systemd unit, opens the
# service port in firewalld and waits until the WebUI answers.
#
# Re-running is safe: an existing installation is stopped, its program directories
# are renamed to *.bak-<timestamp> (never deleted), user data under /var/lib/flocks
# is left untouched and the Pro component is re-installed when it was active.
#
# Optional environment knobs (for operators / CI, never needed by the manual):
#   FLOCKS_OFFLINE_PORT=5173          public port (default: the FLOCKS_PORT an existing install already uses, else 5173)
#   FLOCKS_OFFLINE_SKIP_SYSTEMD=1     start with `flocks start` instead of systemd (containers)
#   FLOCKS_OFFLINE_SKIP_FIREWALL=1    do not touch firewalld
#   FLOCKS_OFFLINE_SKIP_START=1       install only, do not start the service
#   FLOCKS_OFFLINE_DRY_RUN=1          print the plan and exit without changing anything
#   FLOCKS_OFFLINE_HEALTH_TIMEOUT=180 seconds to wait for /api/health

set -Eeuo pipefail
# makeself runs the installer with umask 077; everything we create must be world-readable
umask 022

SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="/var/log/flocks-offline-install.log"
SERVICE_USER="flocks"
DATA_HOME="${FLOCKS_OFFLINE_DATA_HOME:-/var/lib/flocks}"   # override is for tests only
ENV_FILE="${FLOCKS_OFFLINE_ENV_FILE:-/etc/flocks/flocks.env}"   # override is for tests only
UNIT_FILE="/etc/systemd/system/flocks.service"
WRAPPER="/usr/local/bin/flocks"
PORT_OVERRIDE="${FLOCKS_OFFLINE_PORT:-}"
DRY_RUN="${FLOCKS_OFFLINE_DRY_RUN:-0}"
HEALTH_TIMEOUT="${FLOCKS_OFFLINE_HEALTH_TIMEOUT:-180}"
STAMP="$(date +%Y%m%d%H%M%S)"

log() {
  local line
  line="[flocks] $*"
  printf '%s\n' "$line"
  if [[ "$DRY_RUN" != "1" ]]; then
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line" >> "$LOG_FILE" 2>/dev/null || true
  fi
}
PAYLOAD_ENTRIES="versions.json installer tools flocks bundle cache"
FAILED_DIR_PREFIX="flocks-offline-failed-"
same_dir() { [[ "$(cd "$1" 2>/dev/null && pwd -P)" == "$(cd "$2" 2>/dev/null && pwd -P)" ]]; }
fail() {
  trap - ERR
  log "错误: $*"
  # makeself keeps the --target directory when the installer fails; a later package would untar
  # on top of it and carry files of two versions into the install. Park what is ours — the
  # payload entries only, moved into a subdirectory of the same place. The directory itself
  # is never renamed: with `--target /var/tmp` it is a shared system directory.
  if [[ "$DRY_RUN" != "1" && -n "${INSTALL_ROOT:-}" && -d "$SRC_ROOT" ]] && ! same_dir "$SRC_ROOT" "$INSTALL_ROOT"; then
    local parked="$SRC_ROOT/$FAILED_DIR_PREFIX$STAMP" entry moved=0
    for entry in $PAYLOAD_ENTRIES; do
      if [[ -e "$SRC_ROOT/$entry" ]]; then
        mkdir -p "$parked" 2>/dev/null || break
        mv "$SRC_ROOT/$entry" "$parked/" 2>/dev/null && moved=1
      fi
    done
    if [[ "$moved" -eq 1 ]]; then
      log "已解包的安装文件移到 ${parked}（可自行清理；下次安装不会与它混在一起）"
    fi
  fi
  log "安装未完成。完整日志: $LOG_FILE"
  exit 1
}
# any command that would make `set -e` bail out goes through fail() instead: the log gets a
# line saying what died, and the extracted payload is parked like on every other failure
trap 'fail "命令失败: ${BASH_COMMAND}（第 ${LINENO} 行）"' ERR

# ---------------------------------------------------------------------------
# payload facts
# ---------------------------------------------------------------------------
[[ -f "$SRC_ROOT/versions.json" ]] || fail "安装包不完整：缺少 versions.json（${SRC_ROOT}）"
BUNDLED_PY="$SRC_ROOT/tools/python/bin/python3"
[[ -x "$BUNDLED_PY" ]] || fail "安装包不完整：缺少 tools/python"

json_get() {
  "$BUNDLED_PY" - "$1" "$2" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data.get(sys.argv[2], "")
print("true" if value is True else "false" if value is False else value)
PY
}

INSTALL_ROOT="$(json_get "$SRC_ROOT/versions.json" install_root)"
INSTALL_ROOT="${INSTALL_ROOT%/}"
CORE_VERSION="$(json_get "$SRC_ROOT/versions.json" core_version)"
PKG_ARCH="$(json_get "$SRC_ROOT/versions.json" arch)"
UPDATE_CHANNEL="$(json_get "$SRC_ROOT/versions.json" update_channel)"
HAS_PRO_BUNDLE="$(json_get "$SRC_ROOT/versions.json" pro_bundle)"
[[ -n "$INSTALL_ROOT" && "$INSTALL_ROOT" == /* ]] || fail "versions.json 里的 install_root 无效: $INSTALL_ROOT"
if same_dir "$SRC_ROOT" "$INSTALL_ROOT"; then
  fail "这是已安装实例里保留的安装脚本副本（${SRC_ROOT}/installer），不能直接执行；请重新运行 flocks-offline.run"
fi
REPO_DIR="$INSTALL_ROOT/flocks"
VENV_BIN="$REPO_DIR/.venv/bin"
FLOCKS_CLI="$VENV_BIN/flocks"
UV_BIN="$INSTALL_ROOT/tools/uv/uv"
DATA_ROOT="$DATA_HOME/.flocks"
MARKER="$DATA_ROOT/run/pro-bundle-installed.json"

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
# One effective port for the env file, firewalld, the health check and the final URL:
# an explicit FLOCKS_OFFLINE_PORT wins, then the FLOCKS_PORT an existing install already
# runs on (a re-run must not silently fall back to 5173), then the default.
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }
if [[ -n "$PORT_OVERRIDE" ]]; then
  valid_port "$PORT_OVERRIDE" || fail "FLOCKS_OFFLINE_PORT 无效: ${PORT_OVERRIDE}（需要 1-65535 的端口号）"
  PORT="$PORT_OVERRIDE"
else
  PORT="$(sed -n 's/^FLOCKS_PORT=//p' "$ENV_FILE" 2>/dev/null | tail -n 1 | tr -d '[:space:]"'"'"'' || true)"
  if [[ -n "$PORT" ]] && ! valid_port "$PORT"; then
    log "警告: ${ENV_FILE} 里的 FLOCKS_PORT=${PORT} 无效，改用 5173（会改写该行）"
    PORT=""
    PORT_OVERRIDE=5173   # rewrite the broken value; the service cannot start with it
  fi
  [[ -n "$PORT" ]] || PORT=5173
fi

HOST_ARCH="$(uname -m)"
[[ "$HOST_ARCH" == "$PKG_ARCH" ]] || fail "安装包是 $PKG_ARCH 版本，这台机器是 $HOST_ARCH"

if [[ "$DRY_RUN" != "1" && "$(id -u)" -ne 0 ]]; then
  fail "需要 root 权限，请用 sudo 执行"
fi

HAVE_SYSTEMD=1
if [[ "${FLOCKS_OFFLINE_SKIP_SYSTEMD:-0}" == "1" ]] || ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
  HAVE_SYSTEMD=0
fi

EXISTING_INSTALL=0
if [[ -f "$REPO_DIR/pyproject.toml" ]]; then
  EXISTING_INSTALL=1
fi

payload_kb() { du -sk "$SRC_ROOT" 2>/dev/null | awk '{print $1}'; }
free_kb() {
  local probe="$1"
  while [[ ! -d "$probe" ]]; do probe="$(dirname "$probe")"; done
  df -Pk "$probe" | awk 'NR==2 {print $4}'
}
NEED_KB="$(payload_kb)"
HAVE_KB="$(free_kb "$INSTALL_ROOT")"
if [[ -n "$NEED_KB" && -n "$HAVE_KB" ]] && (( HAVE_KB < NEED_KB + 512000 )); then
  fail "磁盘空间不足：$INSTALL_ROOT 所在分区剩余 $((HAVE_KB/1024)) MB，至少需要 $(((NEED_KB+512000)/1024)) MB"
fi

port_in_use() {
  if command -v ss >/dev/null 2>&1; then
    ss -Hltn "( sport = :$PORT )" 2>/dev/null | grep -q .
  else
    "$BUNDLED_PY" - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
PY
  fi
}

log "Flocks 离线安装 ${CORE_VERSION} (${PKG_ARCH}) → ${INSTALL_ROOT}"
if [[ "$EXISTING_INSTALL" -eq 1 ]]; then
  log "检测到已安装的 Flocks，将升级并保留 ${DATA_ROOT} 下的数据"
elif port_in_use; then
  fail "端口 ${PORT} 已被其他程序占用，且没有检测到旧的 Flocks 安装；请先释放端口再安装"
fi

# An activated Pro instance (marker present) must not silently turn into OSS when the new
# package ships no Pro bundle: either the previous bundle can be carried over (same core
# version, wheel matches its manifest sha256) or we stop here, before anything is changed.
CARRY_OVER_BUNDLE=0
if [[ -d "$SRC_ROOT/bundle" && ! -d "$SRC_ROOT/bundle/wheels" ]]; then
  fail "安装包不完整：bundle/ 目录里没有 wheels/"
fi
bundle_compatible() {
  "$BUNDLED_PY" - "$1" "$2" <<'PYCHECK'
import hashlib, json, pathlib, sys
bundle, new_core = pathlib.Path(sys.argv[1]), sys.argv[2].lstrip("v")
manifest = json.load(open(bundle / "manifest.json", encoding="utf-8"))
old_core = str(manifest.get("core_version", "")).lstrip("v")
if old_core != new_core:
    print(f"pro bundle core {old_core} != new core {new_core}", file=sys.stderr)
    sys.exit(1)
wheel = bundle / manifest["flockspro_wheel"]
expected = str(manifest.get("bundle_sha256") or "").lower()
actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
if expected and expected != actual:
    print("pro wheel sha256 does not match its manifest", file=sys.stderr)
    sys.exit(1)
PYCHECK
}
if [[ -f "$MARKER" && ! -d "$SRC_ROOT/bundle/wheels" ]]; then
  OLD_BUNDLE="$INSTALL_ROOT/bundle"
  if [[ -f "$OLD_BUNDLE/manifest.json" ]] && ls "$OLD_BUNDLE"/wheels/flockspro-*.whl >/dev/null 2>&1; then
    if bundle_compatible "$OLD_BUNDLE" "$CORE_VERSION"; then
      CARRY_OVER_BUNDLE=1
      log "新安装包不含 Pro 组件，沿用当前已激活的 Pro bundle（core 版本一致）"
    else
      fail "这台机器已激活 Flocks Pro，但新安装包不含 Pro 组件，且现有 Pro bundle 与新版本不匹配；请使用带 Pro 组件的配套安装包，或先在页面上把 Pro 降级为开源版再升级。本次未做任何改动"
    fi
  else
    fail "这台机器已激活 Flocks Pro，但新安装包不含 Pro 组件；请使用带 Pro 组件的配套安装包，或先在页面上把 Pro 降级为开源版再升级。本次未做任何改动"
  fi
fi

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run: 将安装到 ${INSTALL_ROOT}，服务用户 ${SERVICE_USER}，数据目录 ${DATA_ROOT}，端口 ${PORT}，systemd=${HAVE_SYSTEMD}，Pro bundle=${HAS_PRO_BUNDLE}，carry-over-bundle=${CARRY_OVER_BUNDLE}"
  exit 0
fi

# ---------------------------------------------------------------------------
# service user and directories
# ---------------------------------------------------------------------------
if ! getent passwd "$SERVICE_USER" >/dev/null; then
  useradd --system --create-home --home-dir "$DATA_HOME" --shell /bin/bash "$SERVICE_USER"
  log "已创建服务用户 ${SERVICE_USER}（HOME=${DATA_HOME}）"
fi
mkdir -p "$DATA_HOME" "$DATA_ROOT/config" "$DATA_ROOT/run" "$DATA_ROOT/logs" "$INSTALL_ROOT" "$(dirname "$ENV_FILE")"
chmod 755 "$(dirname "$ENV_FILE")" "$INSTALL_ROOT"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_HOME" "$DATA_ROOT" "$DATA_ROOT/config" "$DATA_ROOT/run" "$DATA_ROOT/logs"
chmod 750 "$DATA_HOME"

# ---------------------------------------------------------------------------
# stop and park an existing installation
# ---------------------------------------------------------------------------
run_as_service_user() {
  # $0 is the env file, the remaining arguments are the command to run; start from the
  # repo dir because the installer's own cwd (the staging dir) is not readable by the service user
  runuser -u "$SERVICE_USER" -- bash -c 'set -a; if [ -f "$0" ]; then . "$0" || exit 97; fi; set +a; cd "${FLOCKS_REPO_ROOT:-/}" 2>/dev/null || cd /; exec "$@"' "$ENV_FILE" "$@"
}

if [[ "$EXISTING_INSTALL" -eq 1 ]]; then
  if [[ "$HAVE_SYSTEMD" -eq 1 ]] && systemctl list-unit-files flocks.service >/dev/null 2>&1 && systemctl is-active --quiet flocks; then
    log "停止 flocks 服务"
    systemctl stop flocks || true
  elif [[ -x "$FLOCKS_CLI" ]]; then
    log "停止正在运行的 Flocks"
    run_as_service_user "$FLOCKS_CLI" stop >/dev/null 2>&1 || true
  fi
fi
# park whatever is already there (a previous version or the leftovers of an aborted run)
PARKED=0
for dir in flocks tools bundle cache installer; do
  if [[ -e "$INSTALL_ROOT/$dir" ]]; then
    mv "$INSTALL_ROOT/$dir" "$INSTALL_ROOT/$dir.bak-$STAMP"
    PARKED=1
  fi
done
if [[ -f "$INSTALL_ROOT/versions.json" ]]; then
  mv "$INSTALL_ROOT/versions.json" "$INSTALL_ROOT/versions.json.bak-$STAMP"
  PARKED=1
fi
if [[ "$PARKED" -eq 1 ]]; then
  log "旧程序目录已改名为 *.bak-${STAMP}（确认新版本正常后可自行清理）"
fi
if [[ "$CARRY_OVER_BUNDLE" -eq 1 && -d "$INSTALL_ROOT/bundle.bak-$STAMP" ]]; then
  mv "$INSTALL_ROOT/bundle.bak-$STAMP" "$INSTALL_ROOT/bundle"
fi

# ---------------------------------------------------------------------------
# place the payload
# ---------------------------------------------------------------------------
for dir in tools flocks bundle cache; do
  if [[ -d "$SRC_ROOT/$dir" ]]; then
    mv "$SRC_ROOT/$dir" "$INSTALL_ROOT/$dir"
  fi
done
# installer/ (this script's directory) is moved into place last, see finish_staging
mv "$SRC_ROOT/versions.json" "$INSTALL_ROOT/versions.json"
# the payload now lives under the install root; use the python from its final location
BUNDLED_PY="$INSTALL_ROOT/tools/python/bin/python3"
[[ -x "$FLOCKS_CLI" ]] || fail "安装包不完整：缺少 ${FLOCKS_CLI}"
mkdir -p "$INSTALL_ROOT/cache/uv"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR" "$INSTALL_ROOT/cache"
if [[ -d "$INSTALL_ROOT/bundle" ]]; then
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_ROOT/bundle"
fi
chmod 755 "$INSTALL_ROOT"
# files extracted under /tmp keep tmp_t labels after mv; put the default contexts back
if command -v restorecon >/dev/null 2>&1; then
  restorecon -R "$INSTALL_ROOT" "$DATA_HOME" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# /etc/flocks/flocks.env  (managed keys are overwritten, everything else kept)
# ---------------------------------------------------------------------------
render_env_template() {
  sed \
    -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
    -e "s|@DATA_HOME@|$DATA_HOME|g" \
    -e "s|@PORT@|$PORT|g" \
    -e "s|@UPDATE_CHANNEL@|$UPDATE_CHANNEL|g" \
    "$SRC_ROOT/installer/flocks.env.template"
}
MANAGED_KEYS="FLOCKS_INSTALL_ROOT FLOCKS_REPO_ROOT FLOCKS_NODE_HOME FLOCKS_UPDATE_CHANNEL FLOCKS_PRO_BUNDLE_DIR UV_CACHE_DIR UV_PYTHON PATH"
if [[ -n "$PORT_OVERRIDE" ]]; then
  # only an explicit request changes the port of an existing install
  MANAGED_KEYS="$MANAGED_KEYS FLOCKS_PORT"
fi
if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ENV_FILE.bak-$STAMP"
  render_env_template > "$ENV_FILE.new"
  "$BUNDLED_PY" - "$ENV_FILE" "$ENV_FILE.new" "$MANAGED_KEYS" <<'PY'
import sys
existing_path, rendered_path, managed = sys.argv[1], sys.argv[2], set(sys.argv[3].split())
def parse(path):
    order, values = [], {}
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            order.append((None, line)); continue
        key, value = line.split("=", 1)
        order.append((key.strip(), value)); values[key.strip()] = value
    return order, values
old_order, old_values = parse(existing_path)
new_order, new_values = parse(rendered_path)
out = []
for key, value in old_order:
    if key is None:
        out.append(value)
    elif key in managed and key in new_values:
        out.append(f"{key}={new_values[key]}")
    else:
        out.append(f"{key}={value}")
for key, value in new_order:
    if key is not None and key not in old_values:
        out.append(f"{key}={value}")
open(existing_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
  rm -f "$ENV_FILE.new"
  log "已更新 ${ENV_FILE}（保留自定义项，原文件备份为 ${ENV_FILE}.bak-${STAMP}）"
else
  render_env_template > "$ENV_FILE"
  log "已写入 ${ENV_FILE}"
fi
if [[ ! -d "$INSTALL_ROOT/bundle" ]]; then
  # no Pro bundle in this package: drop the pointer so the updater uses the online path
  sed -i '/^FLOCKS_PRO_BUNDLE_DIR=/d' "$ENV_FILE"
fi
chmod 640 "$ENV_FILE"
chown "root:$SERVICE_USER" "$ENV_FILE"

PROFILE="$DATA_ROOT/config/install_profile.json"
if [[ ! -f "$PROFILE" ]]; then
  printf '{\n  "Language": "zh-CN"\n}\n' > "$PROFILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$PROFILE"
fi

# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------
sed -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@FLOCKS_CLI@|$FLOCKS_CLI|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
  "$SRC_ROOT/installer/flocks-cli-wrapper.sh" > "$WRAPPER"
chmod 755 "$WRAPPER"

# ---------------------------------------------------------------------------
# keep the Pro component when upgrading an activated installation
# ---------------------------------------------------------------------------
if [[ -f "$MARKER" && -d "$INSTALL_ROOT/bundle/wheels" ]]; then
  PRO_WHEEL="$(ls "$INSTALL_ROOT/bundle/wheels"/flockspro-*.whl 2>/dev/null | head -n 1 || true)"
  if [[ -n "$PRO_WHEEL" ]]; then
    log "重新安装 Pro 组件 $(basename "$PRO_WHEEL")"
    run_as_service_user "$UV_BIN" pip install --python "$VENV_BIN/python" --no-deps "$PRO_WHEEL" >/dev/null
    run_as_service_user "$BUNDLED_PY" - "$INSTALL_ROOT/bundle/manifest.json" "$MARKER" <<'PY'
import json, sys
from datetime import datetime, timezone
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
try:
    previous = json.load(open(sys.argv[2], encoding="utf-8"))
except Exception:
    previous = {}
release_id = manifest.get("release_id") or manifest.get("bundle_release_id") or previous.get("release_id")
payload = {
    "release_id": release_id,
    "bundle_release_id": manifest.get("bundle_release_id") or release_id,
    "bundle_version": manifest["bundle_version"],
    "core_version": manifest["core_version"],
    "flockspro_component_version": manifest["flockspro_component_version"],
    "build_id": manifest.get("build_id") or previous.get("build_id"),
    "bundle_sha256": manifest.get("bundle_sha256"),
    "installed_at": datetime.now(timezone.utc).isoformat(),
}
json.dump(payload, open(sys.argv[2], "w", encoding="utf-8"), sort_keys=True)
PY
  fi
fi

# ---------------------------------------------------------------------------
# systemd, firewall, time sync
# ---------------------------------------------------------------------------
if [[ "$HAVE_SYSTEMD" -eq 1 ]]; then
  sed -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    "$SRC_ROOT/installer/flocks.service" > "$UNIT_FILE"
  systemctl daemon-reload
  systemctl enable flocks >/dev/null 2>&1 || true
  if systemctl list-unit-files chronyd.service >/dev/null 2>&1; then
    systemctl enable --now chronyd >/dev/null 2>&1 || true
  fi
fi

open_firewall_port() {
  # firewalld: the port must be in the permanent *and* the runtime config of every zone that
  # is bound to an interface or source (plus the default zone). A rule someone added earlier
  # with a plain `--add-port` lives in runtime only and is gone after reload / reboot, so the
  # runtime query alone must never decide to skip the permanent rule.
  local port="$1" zone zones changed=0
  # every query is `|| true`: under set -e a failing firewall-cmd inside the substitution would
  # abort the whole installer at this point, after the files have already been replaced
  zones="$( { firewall-cmd --get-default-zone 2>/dev/null || true; firewall-cmd --get-active-zones 2>/dev/null | awk '/^[^[:space:]]/ {print $1}' || true; } | sort -u | tr '\n' ' ' || true)"
  if [[ -z "${zones// /}" ]]; then
    # zone queries failed although firewalld answered --state: fall back to firewalld's default zone
    zones="public"
  fi
  # writes are best effort too: the program files are already in place, a firewalld refusal
  # must be reported, not turn into an aborted install with the service left stopped
  local failed=0
  for zone in $zones; do
    if ! firewall-cmd --permanent --zone="$zone" --query-port="${port}/tcp" >/dev/null 2>&1; then
      if firewall-cmd --permanent --zone="$zone" --add-port="${port}/tcp" >/dev/null 2>&1; then changed=1; else failed=1; fi
    fi
  done
  if [[ "$changed" -eq 1 ]]; then
    firewall-cmd --reload >/dev/null 2>&1 || failed=1
  fi
  for zone in $zones; do
    if ! firewall-cmd --zone="$zone" --query-port="${port}/tcp" >/dev/null 2>&1; then
      if firewall-cmd --zone="$zone" --add-port="${port}/tcp" >/dev/null 2>&1; then changed=1; else failed=1; fi
    fi
  done
  if [[ "$failed" -eq 1 ]]; then
    log "警告: firewalld 未能放行 ${port}/tcp（zone: ${zones}），请手动执行 firewall-cmd --permanent --add-port=${port}/tcp && firewall-cmd --reload"
  elif [[ "$changed" -eq 1 ]]; then
    log "已在 firewalld 放行 ${port}/tcp（zone: ${zones}）"
  fi
}
if [[ "${FLOCKS_OFFLINE_SKIP_FIREWALL:-0}" != "1" ]] && command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  open_firewall_port "$PORT"
fi

# ---------------------------------------------------------------------------
# start and verify
# ---------------------------------------------------------------------------
finish_staging() {
  # the payload has been moved out; keep the installer files next to the install and
  # drop the now-empty staging directory (rmdir refuses anything that is not empty)
  if [[ -d "$SRC_ROOT/installer" && ! -e "$INSTALL_ROOT/installer" ]]; then
    mv "$SRC_ROOT/installer" "$INSTALL_ROOT/installer"
  fi
  # leftovers parked by an earlier failed run go next to the *.bak-* dirs, out of the way
  local leftover
  for leftover in "$SRC_ROOT/$FAILED_DIR_PREFIX"*; do
    [[ -d "$leftover" ]] || continue
    mv "$leftover" "$INSTALL_ROOT/" 2>/dev/null || true
  done
  rmdir "$SRC_ROOT" 2>/dev/null || true
}

if [[ "${FLOCKS_OFFLINE_SKIP_START:-0}" == "1" ]]; then
  finish_staging
  log "已安装，未启动服务（FLOCKS_OFFLINE_SKIP_START=1）"
  exit 0
fi

if [[ "$HAVE_SYSTEMD" -eq 1 ]]; then
  log "启动 flocks 服务"
  systemctl restart flocks || fail "flocks 服务启动失败，请查看: journalctl -u flocks -n 100 --no-pager"
else
  log "没有 systemd，直接以 ${SERVICE_USER} 用户启动"
  run_as_service_user "$FLOCKS_CLI" start --no-browser --skip-webui-build >/dev/null || fail "flocks start 失败，请查看 ${DATA_ROOT}/logs/backend.log"
fi

health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1
  else
    "$BUNDLED_PY" - "$PORT" <<'PY'
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/health", timeout=3)
except Exception:
    sys.exit(1)
PY
  fi
}
waited=0
until health_ok; do
  if (( waited >= HEALTH_TIMEOUT )); then
    tail -n 50 "$DATA_ROOT/logs/backend.log" 2>/dev/null || true
    fail "服务在 ${HEALTH_TIMEOUT} 秒内没有就绪，请查看 ${DATA_ROOT}/logs/backend.log"
  fi
  sleep 3
  waited=$((waited + 3))
done

SERVER_IP="$( (ip -4 route get 1.1.1.1 2>/dev/null || true) | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' | head -n 1 || true)"
[[ -n "$SERVER_IP" ]] || SERVER_IP="$( (hostname -I 2>/dev/null || true) | awk '{print $1}' || true)"
[[ -n "$SERVER_IP" ]] || SERVER_IP="<服务器IP>"
finish_staging
log "安装完成：http://${SERVER_IP}:${PORT}"
