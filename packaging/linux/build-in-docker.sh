#!/usr/bin/env bash
#
# One-click build of the Linux offline installer inside a CentOS Stream 9 container.
# Produces packaging/linux/Output/<arch>/flocks-offline.run (+ .sha256, versions.json).
#
# Usage:
#   packaging/linux/build-in-docker.sh [--arch x86_64|aarch64|all] [--flockspro-wheel PATH]
#       [--pro-release-id ID] [--pro-bundle-version VER] [--output-dir DIR] [--cache-dir DIR]
#       [--cn] [--image IMAGE] [--keep-container]
#
#   --arch      defaults to the host architecture. Building the other architecture needs
#               QEMU user emulation in Docker (docker run --privileged --rm tonistiigi/binfmt --install all)
#               and is several times slower; a same-arch machine or the CI matrix is faster.
#   --cn        use China mirrors for Node.js / npm (uv and python-build-standalone still come from GitHub).
#
# Environment: DOCKER (docker binary, default "docker").

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCKER="${DOCKER:-docker}"
IMAGE="quay.io/centos/centos:stream9"
ARCHES=""
FLOCKSPRO_WHEEL=""
PRO_RELEASE_ID=""
PRO_BUNDLE_VERSION=""
OUTPUT_DIR="$SCRIPT_DIR/Output"
CACHE_DIR="${FLOCKS_CACHE_ROOT:-$HOME/.cache/flocks-offline-build}"
CN=0
KEEP=0

info() { printf '[build-in-docker] %s\n' "$*"; }
fail() { printf '[build-in-docker] error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCHES="$2"; shift 2 ;;
    --flockspro-wheel) FLOCKSPRO_WHEEL="$2"; shift 2 ;;
    --pro-release-id) PRO_RELEASE_ID="$2"; shift 2 ;;
    --pro-bundle-version) PRO_BUNDLE_VERSION="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --cn) CN=1; shift ;;
    --image) IMAGE="$2"; shift 2 ;;
    --keep-container) KEEP=1; shift ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

command -v "$DOCKER" >/dev/null || fail "docker is required."
"$DOCKER" info >/dev/null 2>&1 || fail "docker daemon is not reachable."
[[ -f "$REPO_ROOT/pyproject.toml" && -f "$REPO_ROOT/uv.lock" ]] || fail "$REPO_ROOT is not the flocks repo root."

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64) HOST_ARCH="aarch64" ;;
  amd64) HOST_ARCH="x86_64" ;;
esac
[[ -n "$ARCHES" ]] || ARCHES="$HOST_ARCH"
if [[ "$ARCHES" == "all" ]]; then
  ARCHES="x86_64 aarch64"
fi

if [[ -n "$FLOCKSPRO_WHEEL" ]]; then
  [[ -f "$FLOCKSPRO_WHEEL" ]] || fail "flockspro wheel not found: $FLOCKSPRO_WHEEL"
  [[ "$(basename "$FLOCKSPRO_WHEEL")" == flockspro-*.whl ]] || fail "expected flockspro-*.whl"
fi

mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
CACHE_DIR="$(cd "$CACHE_DIR" && pwd)"

CURRENT_CONTAINER=""
cleanup() {
  if [[ -n "$CURRENT_CONTAINER" && "$KEEP" -eq 0 ]]; then
    "$DOCKER" rm -f "$CURRENT_CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

build_one() {
  local arch="$1" platform container arch_cache started
  local wheel_arg=() mirror_env=()
  case "$arch" in
    x86_64) platform="linux/amd64" ;;
    aarch64) platform="linux/arm64" ;;
    *) fail "unsupported arch: $arch (x86_64 or aarch64)" ;;
  esac
  arch_cache="$CACHE_DIR/$arch"
  mkdir -p "$arch_cache/pro-dist" "$arch_cache/out"
  if [[ -n "$FLOCKSPRO_WHEEL" ]]; then
    cp "$FLOCKSPRO_WHEEL" "$arch_cache/pro-dist/"
    wheel_arg=(--flockspro-wheel "/root/.cache/flocks-offline-build/pro-dist/$(basename "$FLOCKSPRO_WHEEL")")
  fi
  if [[ -n "$PRO_RELEASE_ID" ]]; then wheel_arg+=(--pro-release-id "$PRO_RELEASE_ID"); fi
  if [[ -n "$PRO_BUNDLE_VERSION" ]]; then wheel_arg+=(--pro-bundle-version "$PRO_BUNDLE_VERSION"); fi
  if [[ "$CN" -eq 1 ]]; then
    mirror_env=(-e FLOCKS_NODE_BASE_URL=https://registry.npmmirror.com/-/binary/node -e FLOCKS_NPM_REGISTRY=https://registry.npmmirror.com/)
  fi

  info "[$arch] checking that $platform containers can run on this host"
  if ! "$DOCKER" run --rm --platform "$platform" "$IMAGE" uname -m >/dev/null 2>&1; then
    fail "[$arch] cannot run $platform containers here. Same-arch host, or install QEMU emulation: docker run --privileged --rm tonistiigi/binfmt --install all"
  fi

  container="flocks-offline-build-$arch-$$"
  started="$(date +%s)"
  info "[$arch] building in $container (this takes 15-40 minutes; downloads are cached in $arch_cache)"
  "$DOCKER" run -d --name "$container" --platform "$platform" \
    -v "$REPO_ROOT:/src:ro" \
    -v "$arch_cache:/root/.cache/flocks-offline-build" \
    ${mirror_env[@]+"${mirror_env[@]}"} \
    "$IMAGE" sleep infinity >/dev/null
  CURRENT_CONTAINER="$container"

  "$DOCKER" exec "$container" bash -c \
    'dnf -y install tar gzip findutils which diffutils procps-ng iproute python3 >/root/.cache/flocks-offline-build/dnf.log 2>&1 || { tail -n 20 /root/.cache/flocks-offline-build/dnf.log; exit 1; }'
  local extra_args=""
  if [[ "${#wheel_arg[@]}" -gt 0 ]]; then
    extra_args="$(printf '%q ' "${wheel_arg[@]}")"
  fi
  "$DOCKER" exec "$container" bash -c \
    "cd /src && HOME=/root bash packaging/linux/build-offline-run.sh --output-dir /root/.cache/flocks-offline-build/out $extra_args"

  mkdir -p "$OUTPUT_DIR/$arch"
  cp "$arch_cache/out/flocks-offline.run" "$arch_cache/out/flocks-offline.run.sha256" "$arch_cache/out/versions.json" "$OUTPUT_DIR/$arch/"
  info "[$arch] done in $(( ($(date +%s) - started) / 60 )) min → $OUTPUT_DIR/$arch/flocks-offline.run ($(du -h "$OUTPUT_DIR/$arch/flocks-offline.run" | awk '{print $1}'))"
  cat "$OUTPUT_DIR/$arch/flocks-offline.run.sha256"
  if [[ "$KEEP" -eq 1 ]]; then
    info "[$arch] container kept: $container (staging root is /opt/flocks inside; use build-offline-run.sh --repack there)"
  else
    "$DOCKER" rm -f "$container" >/dev/null 2>&1 || true
  fi
  CURRENT_CONTAINER=""
}

for arch in $ARCHES; do
  build_one "$arch"
done
info "all done: $OUTPUT_DIR"
