#!/usr/bin/env bash
#
# Build the Linux offline installer: a single self-extracting `flocks-offline.run`
# that carries Python, Node.js, uv, a prebuilt virtualenv, the built WebUI and
# (optionally) a Pro bundle. Run it on the target distribution/architecture, e.g.
# inside a `quay.io/centos/centos:stream9` container for CentOS Stream 9 x86_64.
#
# The virtualenv is built *at the final install path* (default /opt/flocks) because
# venvs are not relocatable, so the staging root must be writable and empty.
#
# Usage:
#   packaging/linux/build-offline-run.sh [--repo-root DIR] [--output-dir DIR]
#       [--install-root /opt/flocks] [--cache-dir DIR]
#       [--flockspro-wheel PATH] [--pro-bundle-version VER] [--pro-release-id ID] [--pro-build-id ID]
#       [--skip-webui-build] [--no-dev-group] [--ship-uv-cache] [--repack] [--allow-dirty]
#   --repack reuses an existing staging root (toolchain, venv, WebUI) and only refreshes
#   the sources, installer files, the Pro bundle and versions.json before packing again.
#   --allow-dirty exports the working tree as Git sees it (uncommitted changes and untracked
#   files included, ignored files excluded) instead of the exact HEAD commit; see export-source.sh.
#
# Environment overrides (mirrors):
#   FLOCKS_PBS_BASE_URL   python-build-standalone release base URL
#   FLOCKS_NODE_BASE_URL  Node.js dist base URL (e.g. https://registry.npmmirror.com/-/binary/node)
#   FLOCKS_UV_BASE_URL    uv release base URL
#   FLOCKS_MAKESELF_URL   makeself source tarball URL
#   FLOCKS_UV_DEFAULT_INDEX  PyPI index used while building the venv
#   FLOCKS_NPM_REGISTRY      npm registry used while building the WebUI

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/versions.manifest.json"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/packaging/linux/Output"
INSTALL_ROOT=""
CACHE_DIR="${FLOCKS_CACHE_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/flocks-offline-build}"
FLOCKSPRO_WHEEL=""
PRO_BUNDLE_VERSION=""
PRO_RELEASE_ID=""
PRO_BUILD_ID=""
SKIP_WEBUI_BUILD=0
DEV_GROUP=1
SHIP_UV_CACHE=0
REPACK=0
ALLOW_DIRTY=0

info() { printf '[build-offline-run] %s\n' "$*"; }
fail() { printf '[build-offline-run] error: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --install-root) INSTALL_ROOT="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --flockspro-wheel) FLOCKSPRO_WHEEL="$2"; shift 2 ;;
    --pro-bundle-version) PRO_BUNDLE_VERSION="$2"; shift 2 ;;
    --pro-release-id) PRO_RELEASE_ID="$2"; shift 2 ;;
    --pro-build-id) PRO_BUILD_ID="$2"; shift 2 ;;
    --skip-webui-build) SKIP_WEBUI_BUILD=1; shift ;;
    --no-dev-group) DEV_GROUP=0; shift ;;
    --ship-uv-cache) SHIP_UV_CACHE=1; shift ;;
    --repack) REPACK=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "this builder must run on Linux (the venv is built at the target path)."
command -v curl >/dev/null || fail "curl is required."
command -v tar >/dev/null || fail "tar is required."
command -v python3 >/dev/null || fail "python3 is required to read $MANIFEST."
if [[ ! -f "$REPO_ROOT/.source-export.json" ]]; then
  command -v git >/dev/null || fail "git is required to export the source tree from the repository."
fi
[[ -f "$REPO_ROOT/pyproject.toml" && -f "$REPO_ROOT/uv.lock" ]] || fail "repo root $REPO_ROOT has no pyproject.toml/uv.lock."
[[ -e "$REPO_ROOT/.git" || -f "$REPO_ROOT/.source-export.json" ]] || fail "$REPO_ROOT is neither a git checkout nor a tree exported by export-source.sh."

manifest_get() {
  python3 - "$MANIFEST" "$@" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
node = data
for key in sys.argv[2:]:
    node = node[key]
print(node)
PY
}

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64) ;;
  *) fail "unsupported architecture: $ARCH (x86_64 or aarch64)" ;;
esac
TRIPLE="$(manifest_get arch "$ARCH" triple)"
NODE_ARCH="$(manifest_get arch "$ARCH" node_arch)"
[[ -n "$INSTALL_ROOT" ]] || INSTALL_ROOT="$(manifest_get install_root)"

PY_VERSION="$(manifest_get python version)"
PY_RELEASE="$(manifest_get python python_build_standalone_release)"
PY_TEMPLATE="$(manifest_get python archive_template)"
PBS_BASE_URL="${FLOCKS_PBS_BASE_URL:-$(manifest_get python base_url)}"
NODE_VERSION="$(manifest_get nodejs version)"
NODE_TEMPLATE="$(manifest_get nodejs archive_template)"
NODE_BASE_URL="${FLOCKS_NODE_BASE_URL:-$(manifest_get nodejs base_url)}"
UV_VERSION="$(manifest_get uv version)"
UV_TEMPLATE="$(manifest_get uv archive_template)"
UV_BASE_URL="${FLOCKS_UV_BASE_URL:-$(manifest_get uv base_url)}"
MAKESELF_VERSION="$(manifest_get makeself version)"
MAKESELF_URL="${FLOCKS_MAKESELF_URL:-$(manifest_get makeself archive_url)}"
MAKESELF_URL="${MAKESELF_URL//\{version\}/$MAKESELF_VERSION}"

CORE_VERSION="$(python3 -c 'import tomllib,sys; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$REPO_ROOT/pyproject.toml" 2>/dev/null \
  || python3 -c 'import re,sys; print(re.search(r"^version\s*=\s*\"([^\"]+)\"", open(sys.argv[1]).read(), re.M).group(1))' "$REPO_ROOT/pyproject.toml")"
CORE_VERSION_PLAIN="${CORE_VERSION#v}"
GIT_SHA="unknown"   # set from the export stamp below
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
UPDATE_CHANNEL="flockspro-offline-${CORE_VERSION_PLAIN}"

info "core ${CORE_VERSION} arch ${ARCH} → ${INSTALL_ROOT}"
info "python ${PY_VERSION}+${PY_RELEASE}, node ${NODE_VERSION}, uv ${UV_VERSION}, makeself ${MAKESELF_VERSION}"

# ---------------------------------------------------------------------------
# staging root must be the final install path and must be empty
# ---------------------------------------------------------------------------
if [[ "$REPACK" -eq 1 ]]; then
  [[ -x "$INSTALL_ROOT/flocks/.venv/bin/python" && -f "$INSTALL_ROOT/flocks/webui/dist/index.html" ]] \
    || fail "--repack needs a finished staging root at $INSTALL_ROOT (venv + webui/dist)."
elif [[ -e "$INSTALL_ROOT" ]] && [[ -n "$(ls -A "$INSTALL_ROOT" 2>/dev/null)" ]]; then
  fail "$INSTALL_ROOT exists and is not empty; build in a fresh container or move it aside first."
fi
mkdir -p "$INSTALL_ROOT/tools" "$INSTALL_ROOT/cache/uv" "$CACHE_DIR" "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
CACHE_DIR="$(cd "$CACHE_DIR" && pwd)"

fetch() {
  local url="$1" dest="$2"
  if [[ -s "$dest" ]]; then
    info "cached: $(basename "$dest")"
    return 0
  fi
  info "download: $url"
  curl -fL --retry 3 --retry-delay 5 --connect-timeout 30 -C - -o "$dest.partial" "$url"
  mv "$dest.partial" "$dest"
}

# ---------------------------------------------------------------------------
# toolchain
# ---------------------------------------------------------------------------
PYTHON_BIN="$INSTALL_ROOT/tools/python/bin/python3"
UV_BIN="$INSTALL_ROOT/tools/uv/uv"
if [[ "$REPACK" -eq 0 ]]; then
PY_ARCHIVE="${PY_TEMPLATE//\{version\}/$PY_VERSION}"
PY_ARCHIVE="${PY_ARCHIVE//\{release\}/$PY_RELEASE}"
PY_ARCHIVE="${PY_ARCHIVE//\{triple\}/$TRIPLE}"
fetch "$PBS_BASE_URL/$PY_RELEASE/$PY_ARCHIVE" "$CACHE_DIR/$PY_ARCHIVE"
tar -xzf "$CACHE_DIR/$PY_ARCHIVE" -C "$INSTALL_ROOT/tools"   # extracts tools/python
[[ -x "$PYTHON_BIN" ]] || fail "bundled python not found at $PYTHON_BIN"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'

NODE_ARCHIVE="${NODE_TEMPLATE//\{version\}/$NODE_VERSION}"
NODE_ARCHIVE="${NODE_ARCHIVE//\{node_arch\}/$NODE_ARCH}"
fetch "$NODE_BASE_URL/v$NODE_VERSION/$NODE_ARCHIVE" "$CACHE_DIR/$NODE_ARCHIVE"
mkdir -p "$INSTALL_ROOT/tools/node"
tar -xzf "$CACHE_DIR/$NODE_ARCHIVE" -C "$INSTALL_ROOT/tools/node" --strip-components=1
[[ -x "$INSTALL_ROOT/tools/node/bin/node" ]] || fail "bundled node not found"

UV_ARCHIVE="${UV_TEMPLATE//\{triple\}/$TRIPLE}"
fetch "$UV_BASE_URL/$UV_VERSION/$UV_ARCHIVE" "$CACHE_DIR/$UV_ARCHIVE"
mkdir -p "$INSTALL_ROOT/tools/uv"
tar -xzf "$CACHE_DIR/$UV_ARCHIVE" -C "$INSTALL_ROOT/tools/uv" --strip-components=1
[[ -x "$UV_BIN" ]] || fail "bundled uv not found"

fi  # REPACK toolchain guard
MAKESELF_TARBALL="$CACHE_DIR/makeself-release-${MAKESELF_VERSION}.tar.gz"
fetch "$MAKESELF_URL" "$MAKESELF_TARBALL"
mkdir -p "$CACHE_DIR/makeself"
tar -xzf "$MAKESELF_TARBALL" -C "$CACHE_DIR/makeself" --strip-components=1
MAKESELF="$CACHE_DIR/makeself/makeself.sh"
[[ -f "$MAKESELF" ]] || fail "makeself.sh not found in $MAKESELF_TARBALL"
chmod +x "$MAKESELF"

# ---------------------------------------------------------------------------
# source tree: exported from Git (HEAD, or the Git-visible working tree with
# --allow-dirty), never copied raw from the checkout, so .env / .secret.json /
# logs / local data cannot leak into the package; on --repack this refreshes the
# sources on top of the existing staging tree without touching .venv or webui/dist
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_ROOT/flocks"
stamp_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }
if [[ -f "$REPO_ROOT/.source-export.json" && ! -e "$REPO_ROOT/.git" ]]; then
  # a tree already exported by export-source.sh (build-in-docker.sh exports on the host,
  # where the Git metadata lives, and mounts only the result): copy it and re-verify
  info "using pre-exported source tree $REPO_ROOT"
  tar -C "$REPO_ROOT" -cf - --exclude='./.source-export.json' . | tar -C "$INSTALL_ROOT/flocks" -xf -
  bash "$SCRIPT_DIR/export-source.sh" --verify-only "$INSTALL_ROOT/flocks"
  STAMP_FILE="$REPO_ROOT/.source-export.json"
else
  info "exporting repository → $INSTALL_ROOT/flocks"
  EXPORT_ARGS=()
  if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
    EXPORT_ARGS+=(--allow-dirty)
  fi
  EXPORT_SOURCE_ALLOW_NONEMPTY="$REPACK" \
    bash "$SCRIPT_DIR/export-source.sh" "$REPO_ROOT" "$INSTALL_ROOT/flocks" ${EXPORT_ARGS[@]+"${EXPORT_ARGS[@]}"}
  STAMP_FILE="$INSTALL_ROOT/flocks/.source-export.json"
fi
# the stamp is the single source of truth for what was exported; it does not ship
GIT_SHA="$(stamp_field "$STAMP_FILE" commit)"
SOURCE_DIRTY="$(stamp_field "$STAMP_FILE" dirty)"
rm -f "$INSTALL_ROOT/flocks/.source-export.json"
info "sources: commit ${GIT_SHA}, dirty=${SOURCE_DIRTY}"

# ---------------------------------------------------------------------------
# python environment at the final path
# ---------------------------------------------------------------------------
if [[ "$REPACK" -eq 0 ]]; then
info "building virtualenv with uv sync --frozen"
UV_SYNC_ARGS=(sync --frozen --no-python-downloads)
if [[ "$DEV_GROUP" -eq 1 ]]; then
  UV_SYNC_ARGS+=(--group dev)
fi
if [[ -n "${FLOCKS_UV_DEFAULT_INDEX:-}" ]]; then
  UV_SYNC_ARGS+=(--default-index "$FLOCKS_UV_DEFAULT_INDEX")
fi
# the uv cache lives outside the payload so repeated builds reuse the downloads; it is only
# shipped on request (a Pro-only offline install never needs it, see updater prebuilt mode)
UV_BUILD_CACHE="$CACHE_DIR/uv"
mkdir -p "$UV_BUILD_CACHE"
(
  cd "$INSTALL_ROOT/flocks"
  UV_PYTHON="$PYTHON_BIN" UV_CACHE_DIR="$UV_BUILD_CACHE" UV_NO_PYTHON_DOWNLOADS=1 \
    "$UV_BIN" "${UV_SYNC_ARGS[@]}"
)
if [[ "$SHIP_UV_CACHE" -eq 1 ]]; then
  info "shipping uv cache into the payload"
  tar -C "$UV_BUILD_CACHE" -cf - . | tar -C "$INSTALL_ROOT/cache/uv" -xf -
fi
VENV_PY="$INSTALL_ROOT/flocks/.venv/bin/python"
[[ -x "$VENV_PY" ]] || fail "venv python missing after uv sync"
# run from / so the current directory cannot shadow the editable install
FLOCKS_MODULE_FILE="$(cd / && "$VENV_PY" -c 'import flocks, uvicorn, fastapi, litellm; print(flocks.__file__)')"
case "$FLOCKS_MODULE_FILE" in
  "$INSTALL_ROOT/flocks/flocks/"*) info "venv ok: $FLOCKS_MODULE_FILE" ;;
  *) fail "venv imports flocks from $FLOCKS_MODULE_FILE, expected $INSTALL_ROOT/flocks/flocks/" ;;
esac
(cd / && "$INSTALL_ROOT/flocks/.venv/bin/flocks" --version >/dev/null)

# ---------------------------------------------------------------------------
# WebUI bundle (built in a scratch copy so node_modules never enters the payload)
# ---------------------------------------------------------------------------
if [[ "$SKIP_WEBUI_BUILD" -eq 1 && -f "$REPO_ROOT/webui/dist/index.html" ]]; then
  info "reusing existing webui/dist"
  mkdir -p "$INSTALL_ROOT/flocks/webui/dist"
  tar -C "$REPO_ROOT/webui/dist" -cf - . | tar -C "$INSTALL_ROOT/flocks/webui/dist" -xf -
else
  info "building WebUI with bundled node ${NODE_VERSION}"
  WEBUI_SCRATCH="$CACHE_DIR/webui-build-${CORE_VERSION_PLAIN}-${GIT_SHA}"
  mkdir -p "$WEBUI_SCRATCH"
  tar -C "$REPO_ROOT/webui" -cf - --exclude='./node_modules' --exclude='./dist' . | tar -C "$WEBUI_SCRATCH" -xf -
  (
    cd "$WEBUI_SCRATCH"
    export PATH="$INSTALL_ROOT/tools/node/bin:$PATH"
    if [[ -n "${FLOCKS_NPM_REGISTRY:-}" ]]; then
      export npm_config_registry="$FLOCKS_NPM_REGISTRY"
    fi
    export npm_config_audit=false npm_config_fund=false
    npm ci
    npm run build
  )
  [[ -f "$WEBUI_SCRATCH/dist/index.html" ]] || fail "WebUI build produced no dist/index.html"
  mkdir -p "$INSTALL_ROOT/flocks/webui/dist"
  tar -C "$WEBUI_SCRATCH/dist" -cf - . | tar -C "$INSTALL_ROOT/flocks/webui/dist" -xf -
fi
# make sure the dist is newer than every source file so `flocks start` never tries to rebuild
find "$INSTALL_ROOT/flocks/webui/dist" -exec touch {} +
fi  # REPACK build guard

# ---------------------------------------------------------------------------
# optional Pro bundle (manifest.json + wheels/), consumed via FLOCKS_PRO_BUNDLE_DIR
# ---------------------------------------------------------------------------
HAS_PRO_BUNDLE=0
if [[ -n "$FLOCKSPRO_WHEEL" ]]; then
  [[ -f "$FLOCKSPRO_WHEEL" ]] || fail "flockspro wheel not found: $FLOCKSPRO_WHEEL"
  WHEEL_NAME="$(basename "$FLOCKSPRO_WHEEL")"
  [[ "$WHEEL_NAME" == flockspro-*.whl ]] || fail "expected a flockspro-*.whl, got $WHEEL_NAME"
  if [[ -d "$INSTALL_ROOT/bundle" ]]; then
    mv "$INSTALL_ROOT/bundle" "$CACHE_DIR/bundle.previous-$(date +%s)"
  fi
  mkdir -p "$INSTALL_ROOT/bundle/wheels"
  cp "$FLOCKSPRO_WHEEL" "$INSTALL_ROOT/bundle/wheels/$WHEEL_NAME"
  PRO_COMPONENT_VERSION="$(printf '%s' "$WHEEL_NAME" | sed -E 's/^flockspro-([^-]+)-.*/\1/')"
  [[ -n "$PRO_BUNDLE_VERSION" ]] || PRO_BUNDLE_VERSION="v${CORE_VERSION_PLAIN}"
  WHEEL_SHA256="$(sha256sum "$INSTALL_ROOT/bundle/wheels/$WHEEL_NAME" | awk '{print $1}')"
  "$PYTHON_BIN" - "$INSTALL_ROOT/bundle/manifest.json" <<PY
import json, sys
manifest = {
    "prebuilt": True,
    "bundle_version": "${PRO_BUNDLE_VERSION}",
    "core_version": "v${CORE_VERSION_PLAIN}",
    "flockspro_component_version": "${PRO_COMPONENT_VERSION}",
    "flockspro_wheel": "wheels/${WHEEL_NAME}",
    "bundle_sha256": "${WHEEL_SHA256}",
    "channel": "${UPDATE_CHANNEL}",
    "built_at": "${BUILD_TIME}",
}
if "${PRO_RELEASE_ID}":
    manifest["release_id"] = "${PRO_RELEASE_ID}"
    manifest["bundle_release_id"] = "${PRO_RELEASE_ID}"
if "${PRO_BUILD_ID}":
    manifest["build_id"] = "${PRO_BUILD_ID}"
json.dump(manifest, open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
  HAS_PRO_BUNDLE=1
  info "pro bundle: ${WHEEL_NAME} (component ${PRO_COMPONENT_VERSION}, bundle ${PRO_BUNDLE_VERSION})"
fi

# ---------------------------------------------------------------------------
# installer files + version stamp
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_ROOT/installer"
cp "$SCRIPT_DIR/installer/install.sh" "$SCRIPT_DIR/installer/flocks.service" \
   "$SCRIPT_DIR/installer/flocks.env.template" "$SCRIPT_DIR/installer/flocks-cli-wrapper.sh" \
   "$INSTALL_ROOT/installer/"
chmod 755 "$INSTALL_ROOT/installer/install.sh" "$INSTALL_ROOT/installer/flocks-cli-wrapper.sh"
"$PYTHON_BIN" - "$INSTALL_ROOT/versions.json" <<PY
import json, sys
json.dump({
    "product": "flocks",
    "core_version": "v${CORE_VERSION_PLAIN}",
    "git_sha": "${GIT_SHA}",
    "source_dirty": bool(${SOURCE_DIRTY:-0}),
    "built_at": "${BUILD_TIME}",
    "arch": "${ARCH}",
    "install_root": "${INSTALL_ROOT}",
    "update_channel": "${UPDATE_CHANNEL}",
    "python": "${PY_VERSION}",
    "nodejs": "${NODE_VERSION}",
    "uv": "${UV_VERSION}",
    "pro_bundle": bool(${HAS_PRO_BUNDLE}),
}, open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY

# ---------------------------------------------------------------------------
# self-extracting archive
# ---------------------------------------------------------------------------
RUN_NAME="flocks-offline.run"
OUT_RUN="$OUTPUT_DIR/$RUN_NAME"
info "packing $OUT_RUN (this takes a while)"
MAKESELF_COMPRESS=(--gzip)
if command -v pigz >/dev/null; then
  MAKESELF_COMPRESS=(--pigz)
fi
# --target: extract next to the final location (same filesystem, no /tmp space needed);
# install.sh moves the payload into place and removes the then-empty staging directory.
"$MAKESELF" "${MAKESELF_COMPRESS[@]}" --needroot --nox11 --sha256 \
  --tar-quietly --tar-extra "--owner=root --group=root --numeric-owner" \
  --target "$INSTALL_ROOT/.staging" \
  "$INSTALL_ROOT" "$OUT_RUN" \
  "Flocks offline installer ${CORE_VERSION} (${ARCH})" ./installer/install.sh
( cd "$OUTPUT_DIR" && sha256sum "$RUN_NAME" > "$RUN_NAME.sha256" )
cp "$INSTALL_ROOT/versions.json" "$OUTPUT_DIR/versions.json"
info "done: $OUT_RUN ($(du -h "$OUT_RUN" | awk '{print $1}'))"
info "sha256: $(cat "$OUTPUT_DIR/$RUN_NAME.sha256")"
