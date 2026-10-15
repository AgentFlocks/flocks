#!/usr/bin/env bash
#
# One-click build of the Linux offline installer inside a RHEL 8.10 (UBI 8.10) container:
# built against glibc 2.28, the package runs on RHEL / Rocky / Alma 8 and newer (see glibc_floor
# in versions.manifest.json; the build fails if anything shipped needs a newer glibc).
# Produces packaging/linux/Output/<arch>/flocks-offline.run (+ .sha256, versions.json).
#
# Usage:
#   packaging/linux/build-in-docker.sh [--arch x86_64|aarch64|all] [--output-dir DIR] [--cache-dir DIR]
#       [--cn] [--image IMAGE] [--keep-container] [--allow-dirty]
#
#   --arch      defaults to the host architecture. Building the other architecture needs
#               QEMU user emulation in Docker (docker run --privileged --rm tonistiigi/binfmt --install all)
#               and is several times slower; a same-arch machine or the CI matrix is faster.
#   --cn        use China mirrors for Node.js / npm (uv and python-build-standalone still come from GitHub).
#   --allow-dirty  package the Git-visible working tree instead of the exact HEAD commit
#               (uncommitted changes and untracked files included, ignored files never).
#
# Environment: DOCKER (docker binary, default "docker").

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCKER="${DOCKER:-docker}"
# the build image is pinned next to the toolchain versions (versions.manifest.json: build_image)
IMAGE="$(sed -n 's/^[[:space:]]*"build_image"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SCRIPT_DIR/versions.manifest.json")"
[[ -n "$IMAGE" ]] || { printf '[build-in-docker] error: no build_image in versions.manifest.json\n' >&2; exit 1; }
ARCHES=""
OUTPUT_DIR="$SCRIPT_DIR/Output"
CACHE_DIR="${FLOCKS_CACHE_ROOT:-$HOME/.cache/flocks-offline-build}"
CN=0
KEEP=0
ALLOW_DIRTY=0

info() { printf '[build-in-docker] %s\n' "$*"; }
fail() { printf '[build-in-docker] error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCHES="$2"; shift 2 ;;
    --flockspro-wheel|--pro-release-id|--pro-bundle-version)
      fail "$1 is no longer supported: this package is the open-source edition, extensions are not packed into it" ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --cn) CN=1; shift ;;
    --image) IMAGE="$2"; shift 2 ;;
    --keep-container) KEEP=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  local mirror_env=()
  case "$arch" in
    x86_64) platform="linux/amd64" ;;
    aarch64) platform="linux/arm64" ;;
    *) fail "unsupported arch: $arch (x86_64 or aarch64)" ;;
  esac
  arch_cache="$CACHE_DIR/$arch"
  mkdir -p "$arch_cache/out"
  if [[ "$CN" -eq 1 ]]; then
    mirror_env=(-e FLOCKS_NODE_BASE_URL=https://registry.npmmirror.com/-/binary/node -e FLOCKS_NPM_REGISTRY=https://registry.npmmirror.com/)
  fi

  # export the sources here, where Git can see the checkout (a linked worktree's .git is a
  # pointer into the main repo, which the container does not see); the container only gets
  # the exported tree, so nothing ignored or uncommitted can leak into the package
  local src_export="$arch_cache/source"
  if [[ -e "$src_export" ]]; then
    mv "$src_export" "$arch_cache/source.previous-$(date +%s)"
  fi
  # earlier exports are kept (never deleted here), but they add up: say so once it matters.
  # (a glob loop, not `ls | wc -l`: with nothing to list `ls` exits 2 and pipefail would end
  #  the whole build here — on every fresh machine, before it even started)
  local previous_count=0 previous
  for previous in "$arch_cache"/source.previous-*; do
    if [[ -d "$previous" ]]; then previous_count=$((previous_count + 1)); fi
  done
  if [[ "$previous_count" -ge 3 ]]; then
    info "[$arch] $previous_count earlier source exports kept in $arch_cache/source.previous-* (~64 MB each); remove the ones you no longer need"
  fi
  local export_flags=()
  if [[ "$ALLOW_DIRTY" -eq 1 ]]; then export_flags+=(--allow-dirty); fi
  info "[$arch] exporting sources from Git → $src_export"
  bash "$SCRIPT_DIR/export-source.sh" "$REPO_ROOT" "$src_export" ${export_flags[@]+"${export_flags[@]}"}

  info "[$arch] checking that $platform containers can run on this host"
  if ! "$DOCKER" run --rm --platform "$platform" "$IMAGE" uname -m >/dev/null 2>&1; then
    fail "[$arch] cannot run $platform containers here. Same-arch host, or install QEMU emulation: docker run --privileged --rm tonistiigi/binfmt --install all"
  fi

  container="flocks-offline-build-$arch-$$"
  started="$(date +%s)"
  info "[$arch] building in $container (this takes 15-40 minutes; downloads are cached in $arch_cache)"
  "$DOCKER" run -d --name "$container" --platform "$platform" \
    -v "$src_export:/src:ro" \
    -v "$SCRIPT_DIR:/pkg:ro" \
    -v "$arch_cache:/root/.cache/flocks-offline-build" \
    ${mirror_env[@]+"${mirror_env[@]}"} \
    "$IMAGE" sleep infinity >/dev/null
  CURRENT_CONTAINER="$container"

  "$DOCKER" exec "$container" bash -c \
    'dnf -y install tar gzip findutils which diffutils procps-ng iproute python3 git >/root/.cache/flocks-offline-build/dnf.log 2>&1 || { tail -n 20 /root/.cache/flocks-offline-build/dnf.log; exit 1; }'
  "$DOCKER" exec "$container" bash -c \
    "HOME=/root bash /pkg/build-offline-run.sh --repo-root /src --output-dir /root/.cache/flocks-offline-build/out"

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
