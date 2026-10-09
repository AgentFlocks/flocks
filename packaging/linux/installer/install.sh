#!/usr/bin/env bash
#
# Flocks offline installer (runs unattended as root from the self-extracting
# flocks-offline.run). Installs or upgrades /opt/flocks, creates the `flocks`
# service user, writes /etc/flocks/flocks.env, registers a systemd unit, opens the
# service port in firewalld and waits until the WebUI answers.
#
# With FLOCKS_OFFLINE_BASE=<dir> everything goes below <dir>/flocks instead: app/ (what
# /opt/flocks is otherwise), data/ (the service user's home with all data, /var/lib/flocks),
# etc/flocks.env and logs/ (this installer's log). The prebuilt venv names the path the
# package was built at, so installer/relocate.py rewrites it for the new place. Only the
# systemd unit, /usr/local/bin/flocks, the service user and SELinux rules live outside.
#
# Re-running is safe: an existing installation is stopped, its program directories
# are renamed to *.bak-<timestamp> (never deleted), user data under /var/lib/flocks
# (or <dir>/flocks/data) is left untouched and the Pro component is re-installed when it
# was active. A re-run finds the directory of the existing install by itself; moving an
# install to another directory is refused.
#
# A package that ships a Pro bundle is fully offline: Pro is installed from that bundle,
# uv / npm are pinned offline and network upgrades are refused. A package without one is
# the open-source edition: it installs offline but upgrades like a source install (the Pro
# upgrade in the WebUI and core updates go online).
#
# Optional environment knobs (FLOCKS_OFFLINE_BASE is in the manual, the rest is for operators / CI):
#   FLOCKS_OFFLINE_BASE=/data         install below /data/flocks (the directory must exist; not needed on re-runs)
#   FLOCKS_OFFLINE_PORT=5173          public port (default: the FLOCKS_PORT an existing install already uses, else 5173)
#   FLOCKS_OFFLINE_SKIP_SYSTEMD=1     start with `flocks start` instead of systemd (containers)
#   FLOCKS_OFFLINE_SKIP_FIREWALL=1    do not touch firewalld
#   FLOCKS_OFFLINE_SKIP_START=1       install only, do not start the service
#   FLOCKS_OFFLINE_DRY_RUN=1          print the plan and exit without changing anything
#   FLOCKS_OFFLINE_HEALTH_TIMEOUT=180 seconds to wait for /api/health
#   FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1  install over a newer version (not needed to recover from an online upgrade that never finished)

set -Eeuo pipefail
# makeself runs the installer with umask 077; everything we create must be world-readable
umask 022

SRC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# until the layout is known (see "where to install") the log goes where the default layout keeps it
LOG_FILE="/var/log/flocks-offline-install.log"
SERVICE_USER="flocks"
UNIT_FILE="${FLOCKS_OFFLINE_UNIT_FILE:-/etc/systemd/system/flocks.service}"   # override is for tests only
WRAPPER="${FLOCKS_OFFLINE_WRAPPER:-/usr/local/bin/flocks}"   # override is for tests only
BASE_REQUEST="${FLOCKS_OFFLINE_BASE:-}"
PORT_OVERRIDE="${FLOCKS_OFFLINE_PORT:-}"
DRY_RUN="${FLOCKS_OFFLINE_DRY_RUN:-0}"
HEALTH_TIMEOUT="${FLOCKS_OFFLINE_HEALTH_TIMEOUT:-180}"
STAMP="$(date +%Y%m%d%H%M%S)"
# set once SRC_ROOT is known to be an extracted package and not an install root (see fail)
PAYLOAD_PARKABLE=0

log() {
  local line
  line="[flocks] $*"
  printf '%s\n' "$line"
  if [[ "$DRY_RUN" != "1" ]]; then
    # 2>/dev/null comes first so a refused >> (non-root dry runs, read-only /var/log) stays silent
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line" 2>/dev/null >> "$LOG_FILE" || true
  fi
}
PAYLOAD_ENTRIES="versions.json installer tools flocks bundle cache"
FAILED_DIR_PREFIX="flocks-offline-failed-"
same_dir() { [[ "$(cd "$1" 2>/dev/null && pwd -P)" == "$(cd "$2" 2>/dev/null && pwd -P)" ]]; }
fail() {
  trap - ERR
  # Everything fail() prints goes to stderr. Under `set -E` the ERR trap also fires inside a
  # $(...) substitution; if these lines went to stdout they would become the value of the
  # variable being assigned (and a later fail() would act on that garbage as INSTALL_ROOT).
  {
    log "错误: $*"
    # makeself keeps the --target directory when the installer fails; a later package would
    # untar on top of it and carry files of two versions into the install. Park what is ours —
    # the payload entries only, moved into a subdirectory of the same place. The directory
    # itself is never renamed: with `--target /var/tmp` it is a shared system directory.
    # Nothing is parked before SRC_ROOT has been ruled out as an install root: run from the copy of
    # this script an install keeps in <install root>/installer, parking would move the live program.
    if [[ "$DRY_RUN" != "1" && "$PAYLOAD_PARKABLE" -eq 1 && -d "$SRC_ROOT" ]]; then
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
  } >&2
  exit 1
}
# Any command that would make `set -e` bail out goes through fail() instead: the log gets a
# line saying what died, and the extracted payload is parked like on every other failure.
# Inside a $(...) subshell only that subshell exits; the parent then reports the outer command.
# (BASH_COMMAND / LINENO are read first: anything the trap runs would overwrite them)
trap 'failed_cmd=$BASH_COMMAND; failed_line=$LINENO; if (( BASH_SUBSHELL > 0 )); then exit 1; fi; fail "命令失败: ${failed_cmd}（第 ${failed_line} 行）"' ERR

# ---------------------------------------------------------------------------
# payload facts
# ---------------------------------------------------------------------------
[[ -f "$SRC_ROOT/versions.json" ]] || fail "安装包不完整：缺少 versions.json（${SRC_ROOT}）"
# The architecture check must not depend on the bundled python: on the wrong architecture it
# cannot even start ("Exec format error") and the operator would never see the real reason.
# versions.json is small and flat, so a text read is enough here; python re-reads it below.
json_text_get() { sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -n 1; }
# the path the package was built at; its venv names it in shebangs, pyvenv.cfg and the like
BUILD_ROOT="$(json_text_get "$SRC_ROOT/versions.json" install_root)"
BUILD_ROOT="${BUILD_ROOT%/}"
[[ "$BUILD_ROOT" =~ ^(/[A-Za-z0-9._-]+)+$ ]] || fail "versions.json 里的 install_root 无效: $BUILD_ROOT"

installed_layout() {
  # "<install root><TAB><env file><TAB><state>" of the Flocks an earlier run of this installer set up,
  # read from the CLI wrapper every install writes (or the systemd unit). "installed": the program is
  # there. "unfinished": an upgrade stopped half-way (the old program parked as *.bak-*, the new one
  # not moved in yet) or only the env file is left; the next run carries on in the same place.
  # Nothing when the directories it names hold neither. An install at the build root counts even
  # without wrapper or unit.
  local cli="" root="" env="" state=""
  if [[ -f "$WRAPPER" ]] && grep -q '^# Run the packaged Flocks CLI' "$WRAPPER" 2>/dev/null; then
    cli="$(sed -n 's/^FLOCKS_CLI="\(.*\)"$/\1/p' "$WRAPPER" 2>/dev/null | head -n 1 || true)"
    env="$(sed -n 's/^ENV_FILE="\(.*\)"$/\1/p' "$WRAPPER" 2>/dev/null | head -n 1 || true)"
    if [[ "$cli" == /*/flocks/.venv/bin/flocks ]]; then
      root="${cli%/flocks/.venv/bin/flocks}"
    fi
  fi
  if [[ -z "$root" && -f "$UNIT_FILE" ]] && grep -q '(offline installation)' "$UNIT_FILE" 2>/dev/null; then
    root="$(sed -n 's|^WorkingDirectory=\(/.*\)/flocks$|\1|p' "$UNIT_FILE" 2>/dev/null | head -n 1 || true)"
    env="$(sed -n 's/^EnvironmentFile=//p' "$UNIT_FILE" 2>/dev/null | head -n 1 || true)"
  fi
  if [[ -n "$root" ]]; then
    if [[ -f "$root/flocks/pyproject.toml" ]]; then
      state="installed"
    elif [[ -n "$env" && -f "$env" ]] || compgen -G "$root/flocks.bak-*" >/dev/null; then
      state="unfinished"
    else
      root=""
      env=""
    fi
  fi
  if [[ -z "$root" && -f "$BUILD_ROOT/flocks/pyproject.toml" ]]; then
    root="$BUILD_ROOT"
    state="installed"
  fi
  if [[ -n "$root" ]]; then
    printf '%s\t%s\t%s\n' "$root" "$env" "$state"
  fi
}
INSTALLED="$(installed_layout)"
INSTALLED_ROOT="${INSTALLED%%$'\t'*}"
INSTALLED_REST="${INSTALLED#*$'\t'}"
INSTALLED_ENV="${INSTALLED_REST%%$'\t'*}"
INSTALLED_STATE="${INSTALLED_REST#*$'\t'}"

# An install keeps this script in <install root>/installer; run from there, SRC_ROOT is the live
# program. Told by where it is (the build root, the install the wrapper or unit names, the install
# root of the base it was given) and, for an install below a base directory that lost its wrapper,
# by a venv that no longer names the build root. Before any check that can fail: until this has
# passed, fail() parks nothing.
PAYLOAD_VENV_HOME="$(sed -n 's/^home[[:space:]]*=[[:space:]]*//p' "$SRC_ROOT/flocks/.venv/pyvenv.cfg" 2>/dev/null | head -n 1 || true)"
if same_dir "$SRC_ROOT" "$BUILD_ROOT" \
  || { [[ -n "$INSTALLED_ROOT" ]] && same_dir "$SRC_ROOT" "$INSTALLED_ROOT"; } \
  || { [[ -n "$BASE_REQUEST" ]] && same_dir "$SRC_ROOT" "${BASE_REQUEST%/}/flocks/app"; } \
  || [[ -n "$PAYLOAD_VENV_HOME" && "$PAYLOAD_VENV_HOME" != "$BUILD_ROOT/tools/python/bin" ]]; then
  fail "这是已安装实例里保留的安装脚本副本（${SRC_ROOT}/installer），不能直接执行；请重新运行 flocks-offline.run"
fi
PAYLOAD_PARKABLE=1
HOST_ARCH="$(uname -m)"
PKG_ARCH="$(json_text_get "$SRC_ROOT/versions.json" arch)"
if [[ -n "$PKG_ARCH" && "$PKG_ARCH" != "$HOST_ARCH" ]]; then
  fail "安装包是 $PKG_ARCH 版本，这台机器是 $HOST_ARCH"
fi
mounted_noexec() {
  # true when the filesystem holding $1 is mounted noexec: nothing on it can be run
  local opts
  opts="$(findmnt -no OPTIONS --target "$1" 2>/dev/null || true)"
  [[ ",$opts," == *",noexec,"* ]]
}
if mounted_noexec "$SRC_ROOT"; then
  fail "安装包解压在 ${SRC_ROOT}，这个分区挂载了 noexec，里面的程序不能运行；请换到别的目录解压（--target）"
fi
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

CORE_VERSION="$(json_get "$SRC_ROOT/versions.json" core_version)"
PKG_ARCH="$(json_get "$SRC_ROOT/versions.json" arch)"
UPDATE_CHANNEL="$(json_get "$SRC_ROOT/versions.json" update_channel)"
HAS_PRO_BUNDLE="$(json_get "$SRC_ROOT/versions.json" pro_bundle)"

# ---------------------------------------------------------------------------
# where to install
# ---------------------------------------------------------------------------
# By default the build root (/opt/flocks), /var/lib/flocks, /etc/flocks/flocks.env and /var/log;
# with a base directory all of it below <base>/flocks. With FLOCKS_OFFLINE_BASE set the makeself
# header extracts to <base>/flocks/app/.staging (build-offline-run.sh): keep the two in step.
set_layout() {
  BASE="$1"
  if [[ -n "$BASE" ]]; then
    INSTALL_ROOT="$BASE/flocks/app"
    DATA_HOME="$BASE/flocks/data"
    ENV_FILE="$BASE/flocks/etc/flocks.env"
    LOG_FILE="$BASE/flocks/logs/flocks-offline-install.log"
  else
    INSTALL_ROOT="$BUILD_ROOT"
    DATA_HOME="/var/lib/flocks"
    ENV_FILE="/etc/flocks/flocks.env"
    LOG_FILE="/var/log/flocks-offline-install.log"
  fi
}
base_problem() {
  # prints why $1 cannot be the base directory, nothing when it can. The path ends up in sed
  # replacements, the systemd unit and shebangs, hence the plain character set; at 90 characters
  # "#!<base>/flocks/app/flocks/.venv/bin/python" stays within the 127 bytes older kernels read.
  # With $2=existing (the base an install already uses) the rules on where it may be are skipped.
  local base="$1"
  if [[ -z "$base" ]]; then
    echo "不能是 /"
  elif [[ "$base" != /* ]]; then
    echo "需要绝对路径，例如 /data"
  elif [[ ! "$base" =~ ^(/[A-Za-z0-9._-]+)+$ ]]; then
    echo "只能由字母、数字和 . _ - / 组成"
  elif [[ "$base/" == */./* || "$base/" == */../* ]]; then
    echo "不能含 . 或 .. 这样的路径段"
  elif (( ${#base} > 90 )); then
    echo "路径太长（最多 90 个字符）"
  elif [[ "${2:-}" != "existing" ]]; then
    case "$base" in
      /tmp|/tmp/*|/var/tmp|/var/tmp/*|/run|/run/*) echo "${base} 下的文件会被系统定期清理" ;;
      /dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/boot|/boot/*) echo "不能装在 ${base} 下" ;;
      *)
        if [[ "$base/flocks/app/" == "$BUILD_ROOT/"* || "$BUILD_ROOT/" == "$base/flocks/app/"* ]]; then
          echo "会和默认安装目录 ${BUILD_ROOT} 套在一起"
        fi ;;
    esac
  fi
}
if [[ -n "$BASE_REQUEST" ]]; then
  BASE_REQUEST="${BASE_REQUEST%/}"
  if [[ -n "$INSTALLED_ROOT" && "$INSTALLED_ROOT" == "$BASE_REQUEST/flocks/app" ]]; then
    base_error="$(base_problem "$BASE_REQUEST" existing)"
  else
    base_error="$(base_problem "$BASE_REQUEST")"
  fi
  if [[ -z "$base_error" && "$DRY_RUN" != "1" ]]; then
    if [[ ! -d "$BASE_REQUEST" ]]; then
      base_error="目录不存在，请先建好（或挂载好）"
    elif mounted_noexec "$BASE_REQUEST"; then
      base_error="所在分区挂载了 noexec，程序不能从这里运行"
    fi
  fi
  if [[ -n "$base_error" ]]; then
    fail "FLOCKS_OFFLINE_BASE=${FLOCKS_OFFLINE_BASE} 不能用：${base_error}。本次未做任何改动"
  fi
  set_layout "$BASE_REQUEST"
elif [[ "$INSTALLED_ROOT" == /*/flocks/app && "$INSTALLED_ENV" == "${INSTALLED_ROOT%/flocks/app}/flocks/etc/flocks.env" ]]; then
  # a re-run without FLOCKS_OFFLINE_BASE upgrades an install that was made with one where it is
  set_layout "${INSTALLED_ROOT%/flocks/app}"
  base_error="$(base_problem "$BASE" existing)"
  # the package unpacked to the default place, so the noexec check on that partition says nothing
  # about this one; found only after the old program is parked, the install would be left half done
  if [[ -z "$base_error" ]] && mounted_noexec "$BASE"; then
    base_error="所在分区挂载了 noexec，程序不能从这里运行，去掉 noexec 重新挂载后再升级"
  fi
  if [[ -n "$base_error" ]]; then
    fail "已安装实例的基目录 ${BASE} 没法处理：${base_error}。本次未做任何改动"
  fi
  if [[ "$INSTALLED_STATE" == "unfinished" ]]; then
    log "上次在基目录 ${BASE} 下的安装或升级没有做完，按原目录接着装（下次带上 FLOCKS_OFFLINE_BASE=${BASE}，安装包就直接解压到 ${BASE} 下）"
  else
    log "Flocks 装在基目录 ${BASE} 下，按原目录升级（下次可以带上 FLOCKS_OFFLINE_BASE=${BASE}，安装包就直接解压到 ${BASE} 下）"
  fi
else
  set_layout ""
fi
DATA_HOME="${FLOCKS_OFFLINE_DATA_HOME:-$DATA_HOME}"   # override is for tests only
ENV_FILE="${FLOCKS_OFFLINE_ENV_FILE:-$ENV_FILE}"   # override is for tests only
# an install is upgraded where it is; moving it somewhere else is not something a package does
if [[ -n "$INSTALLED_ROOT" && "$INSTALLED_ROOT" != "$INSTALL_ROOT" ]] && ! same_dir "$INSTALLED_ROOT" "$INSTALL_ROOT"; then
  installed_where="$INSTALLED_ROOT"
  let_go=""
  if [[ "$INSTALLED_STATE" == "unfinished" ]]; then
    # nothing of the old program is left to run: say what ties the machine to it
    installed_where="${INSTALLED_ROOT}，上次安装或升级没有做完"
    # only what an earlier run of this installer wrote (the markers installed_layout reads)
    leftovers=""
    if [[ -f "$WRAPPER" ]] && grep -q '^# Run the packaged Flocks CLI' "$WRAPPER" 2>/dev/null; then
      leftovers="$WRAPPER"
    fi
    if [[ -f "$UNIT_FILE" ]] && grep -q '(offline installation)' "$UNIT_FILE" 2>/dev/null; then
      leftovers="${leftovers:+$leftovers }$UNIT_FILE"
    fi
    if [[ -n "$INSTALLED_ENV" && -f "$INSTALLED_ENV" ]]; then
      leftovers="${leftovers:+$leftovers }$INSTALLED_ENV"
    fi
    let_go="确定不要旧实例了，先把 ${leftovers:-旧实例留下的文件} 改名挪走再装。"
  fi
  if [[ -n "$BASE_REQUEST" && "$INSTALLED_STATE" == "unfinished" ]]; then
    fail "这台机器上已经装了 Flocks（${installed_where}），安装包不会把它搬到 ${INSTALL_ROOT}；去掉 FLOCKS_OFFLINE_BASE 重跑会在原目录接着装。${let_go}本次未做任何改动"
  elif [[ -n "$BASE_REQUEST" ]]; then
    fail "这台机器上已经装了 Flocks（${installed_where}），安装包不会把它搬到 ${INSTALL_ROOT}；升级请去掉 FLOCKS_OFFLINE_BASE 重跑，会按原目录升级。本次未做任何改动"
  fi
  fail "这台机器上已经装了 Flocks（${installed_where}），目录布局和这个安装包对不上，不能按原目录升级。${let_go}本次未做任何改动"
fi
if [[ -n "$INSTALLED_ENV" && "$INSTALLED_ENV" != "$ENV_FILE" ]]; then
  fail "这台机器上的 Flocks 用的配置文件是 ${INSTALLED_ENV}，不是 ${ENV_FILE}，不能按原目录升级。本次未做任何改动"
fi
if [[ "$DRY_RUN" != "1" && -n "$BASE" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
fi
REPO_DIR="$INSTALL_ROOT/flocks"
VENV_BIN="$REPO_DIR/.venv/bin"
FLOCKS_CLI="$VENV_BIN/flocks"
UV_BIN="$INSTALL_ROOT/tools/uv/uv"
MIGRATE_HELPER="$SRC_ROOT/installer/migrate_data.py"
[[ -f "$MIGRATE_HELPER" ]] || fail "安装包不完整：缺少 installer/migrate_data.py"
# The data root is the FLOCKS_ROOT an existing installation already runs with (the env merge
# below keeps that line), else the default. The Pro marker, the data dirs and the migration of
# a previous installation's data all use this one value.
DATA_ROOT="$DATA_HOME/.flocks"
CONFIGURED_ROOT=""
if ! CONFIGURED_ROOT="$("$BUNDLED_PY" "$MIGRATE_HELPER" env-root "$ENV_FILE")"; then
  fail "$CONFIGURED_ROOT"
fi
if [[ -n "$CONFIGURED_ROOT" ]]; then
  DATA_ROOT="$CONFIGURED_ROOT"
fi
MARKER="$DATA_ROOT/run/pro-bundle-installed.json"
# the venv names the build root: anywhere else it is rewritten (after the last refusal, in staging)
RELOCATE=0
if [[ "$INSTALL_ROOT" != "$BUILD_ROOT" ]]; then
  RELOCATE=1
  if [[ ! -f "$SRC_ROOT/installer/relocate.py" || ! -f "$SRC_ROOT/installer/relocate.list" ]]; then
    fail "这个安装包只能装到 ${BUILD_ROOT}（没有 installer/relocate.list），不支持换目录。本次未做任何改动"
  fi
fi

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

[[ "$HOST_ARCH" == "$PKG_ARCH" ]] || fail "安装包是 $PKG_ARCH 版本，这台机器是 $HOST_ARCH"

if [[ "$DRY_RUN" != "1" && "$(id -u)" -ne 0 ]]; then
  fail "需要 root 权限，请用 sudo 执行"
fi

# the service runs as an unprivileged user that must get through the base directory and every
# directory above it (below it everything is ours); probe as that user, or as nobody before it exists
if [[ "$DRY_RUN" != "1" && -n "$BASE" ]]; then
  probe_user="$SERVICE_USER"
  getent passwd "$probe_user" >/dev/null 2>&1 || probe_user="nobody"
  if getent passwd "$probe_user" >/dev/null 2>&1; then
    probe_dir="$BASE"
    while :; do
      if ! runuser -u "$probe_user" -- test -x "$probe_dir" 2>/dev/null; then
        fail "服务用户 ${SERVICE_USER} 进不了 ${probe_dir}（$(stat -c '%A %U:%G' "$probe_dir" 2>/dev/null || true)），请先放开，例如 chmod o+x ${probe_dir}。本次未做任何改动"
      fi
      [[ "$probe_dir" != "/" ]] || break
      probe_dir="$(dirname "$probe_dir")"
    done
  fi
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

# A data root without user data (the first offline install on this machine) gets a copy of the
# previous installation's data: FLOCKS_OFFLINE_MIGRATE_FROM, else the FLOCKS_ROOT the installer
# was started with, else the default ~/.flocks of the local users (several → stop and ask).
# The source directory is only ever read.
MIGRATE_FROM=""
if ! MIGRATE_FROM="$("$BUNDLED_PY" "$MIGRATE_HELPER" plan --target "$DATA_ROOT" \
    --explicit "${FLOCKS_OFFLINE_MIGRATE_FROM:-}" --env-root "${FLOCKS_ROOT:-}" --sudo-user "${SUDO_USER:-}")"; then
  fail "$MIGRATE_FROM"
fi

log "Flocks 离线安装 ${CORE_VERSION} (${PKG_ARCH}) → ${INSTALL_ROOT}"
if [[ "$EXISTING_INSTALL" -eq 1 ]]; then
  log "检测到已安装的 Flocks，将升级并保留 ${DATA_ROOT} 下的数据"
elif port_in_use; then
  if [[ -n "$MIGRATE_FROM" ]]; then
    fail "端口 ${PORT} 已被占用，多半是使用 ${MIGRATE_FROM} 的旧 Flocks 还在运行：请先以原来的用户执行 flocks stop 再安装，数据会自动带过来。本次未做任何改动"
  fi
  fail "端口 ${PORT} 已被其他程序占用，且没有检测到旧的 Flocks 安装；请先释放端口再安装"
fi
if [[ -n "$MIGRATE_FROM" ]]; then
  MIGRATE_SIZE=""
  if ! MIGRATE_SIZE="$("$BUNDLED_PY" "$MIGRATE_HELPER" check --source "$MIGRATE_FROM" --target "$DATA_ROOT")"; then
    fail "$MIGRATE_SIZE"
  fi
  log "检测到旧的 Flocks 数据 ${MIGRATE_FROM}（约 ${MIGRATE_SIZE}），将拷贝到 ${DATA_ROOT}，原目录不动"
fi

version_older() {
  # true when $1 < $2; calendar versions like v2026.9.23 (a leading "v" and non-numeric tails are ignored)
  "$BUNDLED_PY" - "$1" "$2" <<'PYVER'
import re, sys
def key(version):
    return tuple(int(re.match(r"\d*", part).group() or 0) for part in re.split(r"[.\-+]", version.strip().lstrip("vV")))
sys.exit(0 if key(sys.argv[1]) < key(sys.argv[2]) else 1)
PYVER
}
unfinished_upgrade_to() {
  # succeeds (printing "<phase> <failed_stage>") when the updater's last upgrade targeted $1, never
  # reached "done" and started from a version this package ($2) is not older than: the handoff swaps
  # the source in first and leaves it there when uv sync / npm fail afterwards, it does not roll back
  "$BUNDLED_PY" - "$DATA_ROOT/run/upgrade-result.json" "$1" "$2" <<'PYRES'
import json, re, sys
def key(version):
    return tuple(int(re.match(r"\d*", part).group() or 0) for part in re.split(r"[.\-+]", str(version).strip().lstrip("vV")))
try:
    result = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
if not isinstance(result, dict) or result.get("phase") not in ("failed", "running"):
    sys.exit(1)
previous = str(result.get("current_version") or "").strip()
if not previous or key(result.get("version") or "") != key(sys.argv[2]) or key(sys.argv[3]) < key(previous):
    sys.exit(1)
print(result.get("phase"), result.get("failed_stage") or "-")
PYRES
}
# An install that upgraded online (core updates / Pro in the WebUI) may already be newer than this
# package: re-running an older package would put old code on data that newer code has already used.
# Let it through only when that newer version never finished installing and this package is not older
# than what ran before it (re-running the package is the recovery then), or when the operator asks for
# it with FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1.
if [[ "$EXISTING_INSTALL" -eq 1 ]]; then
  INSTALLED_VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_DIR/pyproject.toml" | head -n 1 || true)"
  if [[ -n "$INSTALLED_VERSION" ]] && version_older "$CORE_VERSION" "$INSTALLED_VERSION"; then
    if upgrade_state="$(unfinished_upgrade_to "$INSTALLED_VERSION" "$CORE_VERSION")"; then
      log "上次在线升级到 ${INSTALLED_VERSION} 没有完成（${upgrade_state}），按安装包 ${CORE_VERSION} 恢复"
      case "$upgrade_state" in
        running*|*" start") log "警告: 上次升级可能已经用 ${INSTALLED_VERSION} 启动过，它改过的数据不会跟着回退" ;;
      esac
    elif [[ "${FLOCKS_OFFLINE_ALLOW_DOWNGRADE:-0}" == "1" ]]; then
      log "警告: 按 FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1 从 ${INSTALLED_VERSION} 回退到 ${CORE_VERSION}；新版本改过的数据不会跟着回退"
    else
      fail "这台机器上已经是 ${INSTALLED_VERSION}（可能在页面上在线升级过），比安装包的 ${CORE_VERSION} 新；用旧安装包覆盖会让程序比数据旧，请换用不低于 ${INSTALLED_VERSION} 的安装包，确认要回退就加 FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1 重跑。本次未做任何改动"
    fi
  fi
fi
# The same holds for data brought over from another installation: it may have been used by a newer
# version (upgraded online there). The updater's version marker is the only version a data directory
# carries; the marker rewrite further down would otherwise hide it.
if [[ -n "$MIGRATE_FROM" && -f "$MIGRATE_FROM/version/.current_version" ]]; then
  MIGRATE_VERSION="$(tr -d '[:space:]' < "$MIGRATE_FROM/version/.current_version" || true)"
  if [[ -n "$MIGRATE_VERSION" ]] && version_older "$CORE_VERSION" "$MIGRATE_VERSION"; then
    if [[ "${FLOCKS_OFFLINE_ALLOW_DOWNGRADE:-0}" == "1" ]]; then
      log "警告: 按 FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1 把 ${MIGRATE_VERSION} 用过的数据 ${MIGRATE_FROM} 迁给 ${CORE_VERSION}；新版本改过的数据不会跟着回退"
    else
      fail "要迁移的旧数据 ${MIGRATE_FROM} 已经被 ${MIGRATE_VERSION} 用过（可能在页面上在线升级过），比安装包的 ${CORE_VERSION} 新；旧程序用新数据可能出错，请换用不低于 ${MIGRATE_VERSION} 的安装包，确认要这样迁就加 FLOCKS_OFFLINE_ALLOW_DOWNGRADE=1 重跑。本次未做任何改动"
    fi
  fi
fi

# An activated Pro instance (marker present, here or in the data being migrated) must not
# silently turn into OSS when the new package ships no Pro bundle: either the previous bundle
# can be carried over (same core version, wheel matches its manifest sha256) or we stop here,
# before anything is changed.
PRO_ACTIVE=0
if [[ -f "$MARKER" ]] || [[ -n "$MIGRATE_FROM" && -f "$MIGRATE_FROM/run/pro-bundle-installed.json" ]]; then
  PRO_ACTIVE=1
fi
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
if [[ "$PRO_ACTIVE" -eq 1 && ! -d "$SRC_ROOT/bundle/wheels" ]]; then
  OLD_BUNDLE="$INSTALL_ROOT/bundle"
  if [[ -f "$OLD_BUNDLE/manifest.json" ]] && ls "$OLD_BUNDLE"/wheels/flockspro-*.whl >/dev/null 2>&1; then
    if bundle_compatible "$OLD_BUNDLE" "$CORE_VERSION"; then
      CARRY_OVER_BUNDLE=1
      log "新安装包不含 Pro 组件，沿用当前已激活的 Pro bundle（core 版本一致）"
    else
      fail "这台机器已激活 Flocks Pro，但新安装包不含 Pro 组件，且现有 Pro bundle 与新版本不匹配；请使用带 Pro 组件的配套安装包，或先在页面上把 Pro 降级为开源版再升级。本次未做任何改动"
    fi
  elif grep -Eq '^(FLOCKS_DEPLOY_MODE=offline|FLOCKS_OFFLINE_INSTALL=|FLOCKS_PRO_BUNDLE_DIR=)' "$ENV_FILE" 2>/dev/null; then
    # an offline (Pro-bundle) install whose bundle is gone: it cannot upgrade online either
    fail "这台机器已激活 Flocks Pro，但新安装包不含 Pro 组件，机器上的 Pro bundle 也找不到；请使用带 Pro 组件的配套安装包，或先在页面上把 Pro 降级为开源版再升级。本次未做任何改动"
  else
    # Pro was upgraded online on top of an open-source package: /opt/flocks/bundle never existed
    fail "这台机器已激活 Flocks Pro，但新安装包只含开源版，覆盖安装会让 Pro 失效；请在页面上在线升级 Pro，或先在页面上把 Pro 降级为开源版再用新安装包升级。本次未做任何改动"
  fi
fi

# offline: Pro comes from a bundle on this machine (shipped or carried over), uv / npm pinned offline;
# online: open-source package, the Pro upgrade and core updates go online like a source install
if [[ -d "$SRC_ROOT/bundle" || "$CARRY_OVER_BUNDLE" -eq 1 ]]; then
  UPGRADE_MODE="offline"
  DEPLOY_MODE="offline"
else
  UPGRADE_MODE="online"
  DEPLOY_MODE="source"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run: 将安装到 ${INSTALL_ROOT}，基目录 ${BASE:-（默认布局）}，服务用户 ${SERVICE_USER}，数据目录 ${DATA_ROOT}，配置 ${ENV_FILE}，日志 ${LOG_FILE}，端口 ${PORT}，systemd=${HAVE_SYSTEMD}，Pro bundle=${HAS_PRO_BUNDLE}，carry-over-bundle=${CARRY_OVER_BUNDLE}，upgrade=${UPGRADE_MODE}，relocate=${RELOCATE}，迁移旧数据=${MIGRATE_FROM:-无}"
  if [[ -n "$BASE" && ! -d "$BASE" ]]; then
    log "dry-run: 基目录 ${BASE} 还不存在，正式安装前要先建好（或挂载好）"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# another install root than the build root: rewrite the venv before anything is stopped
# ---------------------------------------------------------------------------
# (the staging copy only; a failure here leaves the running instance alone)
if [[ "$RELOCATE" -eq 1 ]]; then
  relocate_out="$("$BUNDLED_PY" "$SRC_ROOT/installer/relocate.py" apply "$SRC_ROOT" "$SRC_ROOT/installer/relocate.list" "$BUILD_ROOT" "$INSTALL_ROOT" 2>&1)" \
    || fail "Python 环境的路径没能改到 ${INSTALL_ROOT}，运行中的实例未动: ${relocate_out}"
  log "${relocate_out}"
fi

# ---------------------------------------------------------------------------
# service user and directories
# ---------------------------------------------------------------------------
if ! getent passwd "$SERVICE_USER" >/dev/null; then
  # useradd only creates the last path component of the home directory
  mkdir -p "$(dirname "$DATA_HOME")"
  useradd --system --create-home --home-dir "$DATA_HOME" --shell /bin/bash "$SERVICE_USER"
  log "已创建服务用户 ${SERVICE_USER}（HOME=${DATA_HOME}）"
elif [[ "$(getent passwd "$SERVICE_USER" | cut -d: -f6)" != "$DATA_HOME" ]]; then
  log "服务用户 ${SERVICE_USER} 已存在，系统里登记的 HOME 不是 ${DATA_HOME}；服务按 ${ENV_FILE} 里的 HOME=${DATA_HOME} 运行，不受影响"
fi
mkdir -p "$DATA_HOME"
# first thing that changes the machine: a failed copy leaves the previous installation as it was
if [[ -n "$MIGRATE_FROM" ]]; then
  log "拷贝旧数据 ${MIGRATE_FROM} → ${DATA_ROOT}"
  MIGRATE_OUT=""
  # --owner: the copy is handed to the service user before the rename that puts it in place, so an
  # interrupted run leaves either the untouched target (a re-run migrates again) or a complete,
  # service-owned data dir; the data of an existing installation is never re-owned
  if ! MIGRATE_OUT="$("$BUNDLED_PY" "$MIGRATE_HELPER" copy --source "$MIGRATE_FROM" --target "$DATA_ROOT" --stamp "$STAMP" --owner "$SERVICE_USER")"; then
    fail "$MIGRATE_OUT"
  fi
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      log "$line"
    fi
  done <<< "$MIGRATE_OUT"
fi
mkdir -p "$DATA_ROOT/config" "$DATA_ROOT/run" "$DATA_ROOT/logs" "$INSTALL_ROOT" "$(dirname "$ENV_FILE")"
chmod 755 "$(dirname "$ENV_FILE")" "$INSTALL_ROOT"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_HOME" "$DATA_ROOT" "$DATA_ROOT/config" "$DATA_ROOT/run" "$DATA_ROOT/logs"
chmod 750 "$DATA_HOME"
if [[ -n "$BASE" ]]; then
  # makeself created <base>/flocks with umask 077 on the way to the staging dir; the service user
  # has to get through it to app/ and data/
  mkdir -p "$(dirname "$LOG_FILE")"
  chmod 755 "$BASE/flocks"
fi

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
selinux_label_base() {
  # SELinux knows no file contexts below a directory like /data: everything there is default_t,
  # which systemd may not be allowed to execute or read. Register each part as equivalent to the
  # directory it stands in for, so it is labelled exactly like the default layout.
  local pairs pair path equiv listed failed=0
  pairs="$INSTALL_ROOT=/opt $DATA_HOME=/var/lib $(dirname "$ENV_FILE")=/etc $(dirname "$LOG_FILE")=/var/log"
  if command -v semanage >/dev/null 2>&1; then
    listed="$(semanage fcontext -l -C 2>/dev/null || true)"
    for pair in $pairs; do
      path="${pair%=*}"
      equiv="${pair#*=}"
      if ! grep -qxF "$path = $equiv" <<<"$listed"; then
        semanage fcontext -a -e "$equiv" "$path" >/dev/null 2>&1 \
          || semanage fcontext -m -e "$equiv" "$path" >/dev/null 2>&1 || failed=1
      fi
    done
    restorecon -R "$BASE/flocks" >/dev/null 2>&1 || failed=1
    if [[ "$failed" -eq 1 ]]; then
      log "警告: SELinux 规则没有全部登记上（semanage fcontext -l -C 可查）；服务起不来时看 ausearch -m avc -ts recent"
    else
      log "SELinux：${BASE}/flocks 下的 app、data、etc、logs 已按 /opt、/var/lib、/etc、/var/log 登记标签"
    fi
  else
    # no semanage (policycoreutils-python-utils): label directly; a full relabel would undo this
    for pair in $pairs; do
      chcon -R --reference="${pair#*=}" "${pair%=*}" >/dev/null 2>&1 || failed=1
    done
    find "$INSTALL_ROOT" -type d -name bin -prune -exec chcon -R --reference=/usr/bin {} + >/dev/null 2>&1 || failed=1
    log "警告: 没有 semanage 命令（policycoreutils-python-utils 包），SELinux 标签是用 chcon 直接打的，整机重打标签（restorecon -R / 或 /.autorelabel）后会丢；装上这个包后再运行一次安装包，就会登记成永久规则"
    if [[ "$failed" -eq 1 ]]; then
      log "警告: 部分 chcon 没成功；服务起不来时看 ausearch -m avc -ts recent"
    fi
  fi
}
# files extracted under /tmp keep tmp_t labels after mv; put the default contexts back
if [[ -n "$BASE" ]] && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
  selinux_label_base
elif command -v restorecon >/dev/null 2>&1; then
  restorecon -R "$INSTALL_ROOT" "$DATA_HOME" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# /etc/flocks/flocks.env  (managed keys are overwritten, everything else kept)
# ---------------------------------------------------------------------------
# keys that only make sense with a Pro bundle on the machine; in the online mode none may stay:
# UV_OFFLINE / npm_config_offline break the online dependency sync, the offline channel asks Console
# for a release that is not there, and FLOCKS_OFFLINE_INSTALL=1 turns the updater's refusal back on
# as soon as FLOCKS_DEPLOY_MODE goes missing from the file
OFFLINE_ONLY_KEYS_RE='^(FLOCKS_OFFLINE_INSTALL|FLOCKS_UPDATE_CHANNEL|FLOCKS_PRO_BUNDLE_DIR|UV_OFFLINE|npm_config_offline)='
render_env_template() {
  sed \
    -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
    -e "s|@DATA_HOME@|$DATA_HOME|g" \
    -e "s|@PORT@|$PORT|g" \
    -e "s|@UPDATE_CHANNEL@|$UPDATE_CHANNEL|g" \
    -e "s|@DEPLOY_MODE@|$DEPLOY_MODE|g" \
    "$SRC_ROOT/installer/flocks.env.template" \
  | if [[ "$UPGRADE_MODE" == "online" ]]; then sed -E "/$OFFLINE_ONLY_KEYS_RE/d"; else cat; fi
}
# FLOCKS_DEPLOY_MODE is managed so that a package of the other kind switches an existing install over
MANAGED_KEYS="FLOCKS_INSTALL_ROOT FLOCKS_REPO_ROOT FLOCKS_NODE_HOME FLOCKS_DEPLOY_MODE FLOCKS_UPDATE_CHANNEL FLOCKS_PRO_BUNDLE_DIR UV_CACHE_DIR UV_PYTHON PATH"
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
if [[ "$UPGRADE_MODE" == "online" ]]; then
  # no Pro bundle here: also drop the offline pins an earlier install of a Pro-bundle package left
  # in this file, otherwise the updater keeps refusing the online Pro upgrade and uv / npm stay offline
  sed -i -E "/$OFFLINE_ONLY_KEYS_RE/d" "$ENV_FILE"
fi
log "升级方式：$([[ "$UPGRADE_MODE" == "online" ]] && echo "在线（开源版安装包，Pro 在页面上在线升级）" || echo "离线（Pro 组件来自 ${INSTALL_ROOT}/bundle）")"
chmod 640 "$ENV_FILE"
chown "root:$SERVICE_USER" "$ENV_FILE"

PROFILE="$DATA_ROOT/config/install_profile.json"
if [[ ! -f "$PROFILE" ]]; then
  printf '{\n  "Language": "zh-CN"\n}\n' > "$PROFILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$PROFILE"
fi

if [[ "$RELOCATE" -eq 1 ]]; then
  # the rewritten venv has to work from its new place, run the way the service runs it, before anything
  # is started on it (-I: not from the current directory, only through the venv's editable install)
  module_file="$(run_as_service_user "$VENV_BIN/python" -I -c 'import flocks; print(flocks.__file__)' 2>/dev/null || true)"
  if [[ "$module_file" != "$REPO_DIR/flocks/"* ]] || ! run_as_service_user "$FLOCKS_CLI" --version >/dev/null 2>&1; then
    fail "改到 ${INSTALL_ROOT} 的 Python 环境不能用（import flocks: ${module_file:-报错}）"
  fi
  log "Python 环境已改到 ${INSTALL_ROOT}（import flocks、flocks --version 正常）"
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

# the code on disk is now CORE_VERSION. The updater's version marker can still name a newer version (a
# downgrade, or a hand-made rollback before a repair run); get_current_version() reports the higher of
# marker and pyproject, so the WebUI would show a version that is not installed.
VERSION_MARKER="$DATA_ROOT/version/.current_version"
if [[ -f "$VERSION_MARKER" ]] && version_older "$CORE_VERSION" "$(tr -d '[:space:]' < "$VERSION_MARKER")"; then
  printf '%s\n' "${CORE_VERSION#v}" > "$VERSION_MARKER"
  log "版本标记 ${VERSION_MARKER} 已改回 ${CORE_VERSION#v}"
fi
# the updater's last upgrade record describes code this package has just replaced: keep it for diagnosis
# but out of the way, so it can never let a later package through the downgrade guard again
UPGRADE_RESULT="$DATA_ROOT/run/upgrade-result.json"
if [[ -f "$UPGRADE_RESULT" ]]; then
  mv "$UPGRADE_RESULT" "$UPGRADE_RESULT.bak-$STAMP"
  log "上次在线升级的记录已改名为 ${UPGRADE_RESULT}.bak-${STAMP}"
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
  # installed elsewhere from the default extraction dir (a base install re-run without
  # FLOCKS_OFFLINE_BASE): makeself created the build root only to hold .staging
  if [[ "$RELOCATE" -eq 1 && "$SRC_ROOT" == "$BUILD_ROOT/.staging" ]]; then
    rmdir "$BUILD_ROOT" 2>/dev/null || true
  fi
}
report_migration() {
  if [[ -n "$MIGRATE_FROM" ]]; then
    log "旧数据目录 ${MIGRATE_FROM} 原样保留；确认新实例里的账号、会话和配置都在之后可自行清理"
  fi
}

if [[ "${FLOCKS_OFFLINE_SKIP_START:-0}" == "1" ]]; then
  finish_staging
  log "已安装，未启动服务（FLOCKS_OFFLINE_SKIP_START=1）"
  report_migration
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
report_migration
