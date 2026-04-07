#!/usr/bin/env bash

[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

REPO_SLUG="${FLOCKS_REPO_SLUG:-AgentFlocks/Flocks}"
DEFAULT_BRANCH="${FLOCKS_DEFAULT_BRANCH:-main}"
VERSION="${VERSION:-$DEFAULT_BRANCH}"
INSTALL_TUI=0
TMP_DIR=""
DEFAULT_INSTALL_DIR="${PWD%/}/flocks"
INSTALL_DIR="${FLOCKS_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

info() {
  printf '[flocks-bootstrap] %s\n' "$1"
}

fail() {
  printf '[flocks-bootstrap] error: %s\n' "$1" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}

print_usage() {
  cat <<EOF
Usage: install.sh [--with-tui] [--version <tag-or-branch>]

Bootstrap installer for Flocks.
This script downloads the GitHub source archive, extracts it to a temporary
directory, copies it to a persistent install location, then delegates to
scripts/install.sh inside the repository. By default it creates a "flocks"
subdirectory under the current working directory.

Options:
  --with-tui, -t         Also install TUI dependencies.
  --version <value>      Install from a Git tag or branch. Defaults to: $DEFAULT_BRANCH
  --help, -h             Show this help message.

Environment variables:
  VERSION                Same as --version.
  FLOCKS_INSTALL_DIR     Override the persistent install location. Defaults to: $INSTALL_DIR
  FLOCKS_REPO_SLUG       Override GitHub repo, e.g. owner/repo.
  FLOCKS_DEFAULT_BRANCH  Override default branch. Defaults to: $DEFAULT_BRANCH
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-tui|-t)
        INSTALL_TUI=1
        ;;
      --version)
        [[ $# -ge 2 ]] || fail "--version requires a value."
        VERSION="$2"
        shift
        ;;
      --help|-h)
        print_usage
        exit 0
        ;;
      *)
        print_usage
        fail "Unsupported argument: $1"
        ;;
    esac
    shift
  done
}

ensure_dependencies() {
  has_cmd curl || fail "curl is required to download the GitHub source archive."
  has_cmd tar || fail "tar is required to extract the GitHub source archive."
  has_cmd bash || fail "bash is required to run the repository installer."
}

build_candidate_urls() {
  local repo_url_base="https://github.com/$REPO_SLUG/archive/refs"

  if [[ "$VERSION" == "$DEFAULT_BRANCH" ]]; then
    printf '%s\n' "$repo_url_base/heads/$VERSION.tar.gz"
    return 0
  fi

  printf '%s\n' "$repo_url_base/tags/$VERSION.tar.gz"
  printf '%s\n' "$repo_url_base/heads/$VERSION.tar.gz"
}

download_archive() {
  local archive_path="$1"
  local last_error=""
  local url=""

  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    info "Trying source archive URL: $url"
    if curl -fsSL "$url" -o "$archive_path"; then
      printf '%s' "$url"
      return 0
    fi
    last_error="$url"
  done < <(build_candidate_urls)

  fail "Failed to download source archive for version \"$VERSION\" from GitHub. Last attempted URL: ${last_error:-<none>}"
}

resolve_project_dir() {
  local candidate=""
  for candidate in "$TMP_DIR"/*; do
    [[ -d "$candidate" ]] || continue
    if [[ -f "$candidate/scripts/install.sh" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}

main() {
  local archive_path download_url project_dir install_parent

  trap cleanup EXIT
  parse_args "$@"
  ensure_dependencies

  TMP_DIR="$(mktemp -d)"
  archive_path="$TMP_DIR/flocks.tar.gz"

  info "Repository: $REPO_SLUG"
  info "Version: $VERSION"
  info "Temporary directory: $TMP_DIR"
  download_url="$(download_archive "$archive_path")"

  info "Extracting source archive..."
  tar -xzf "$archive_path" -C "$TMP_DIR"

  project_dir="$(resolve_project_dir)" || fail "Archive extracted, but scripts/install.sh was not found."

  install_parent="$(dirname "$INSTALL_DIR")"
  mkdir -p "$install_parent"
  rm -rf "$INSTALL_DIR"
  cp -R "$project_dir" "$INSTALL_DIR"

  info "Downloaded from: $download_url"
  info "Install directory: $INSTALL_DIR"
  info "Delegating to installer: $INSTALL_DIR/scripts/install.sh"

  if [[ "$INSTALL_TUI" -eq 1 ]]; then
    bash "$INSTALL_DIR/scripts/install.sh" --with-tui
  else
    bash "$INSTALL_DIR/scripts/install.sh"
  fi
}

main "$@"
