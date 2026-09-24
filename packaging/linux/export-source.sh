#!/usr/bin/env bash
#
# Export the runtime source tree for the offline package from Git, never from the raw
# working directory: a developer's checkout may hold .env files, .flocks/.secret.json,
# logs or local data that must not end up in a customer package.
#
# Usage: export-source.sh <repo-root> <dest-dir> [--allow-dirty]
#        export-source.sh --verify-only <dir>
#
#   default        exact content of HEAD (`git archive`): no untracked, no ignored,
#                  no uncommitted modifications
#   --allow-dirty  working tree as seen by Git (`git ls-files --cached --others
#                  --exclude-standard`): uncommitted changes and untracked files are
#                  included, .gitignore'd files are not
#   --verify-only  run just the deny-list check on an already exported tree
#
# In both modes the development-only directories are dropped and the result is checked
# against a deny list of runtime/secret file names; any hit aborts the export. The
# destination gets a `.source-export.json` stamp ({commit, dirty, mode}) so a later build
# step can tell an exported tree from a raw checkout.
# Prints "commit=<sha> dirty=<0|1> mode=<clean|dirty>" on success.

set -euo pipefail

REPO_ROOT=""
DEST=""
ALLOW_DIRTY=0
VERIFY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      if [[ -z "$REPO_ROOT" ]]; then REPO_ROOT="$arg"
      elif [[ -z "$DEST" ]]; then DEST="$arg"
      else echo "export-source: unexpected argument: $arg" >&2; exit 2
      fi
      ;;
  esac
done
fail() { echo "export-source: error: $*" >&2; exit 1; }

verify_tree() {
  # deny list: runtime configuration, secrets, logs and local data must not ship
  # (.venv / webui/dist may already exist in the destination when a staging root is refreshed;
  #  they are build outputs, not repository content, so they are skipped here)
  local dest="$1" offenders
  offenders="$(
    cd "$dest" && find . \( -path './.venv' -o -path './webui/node_modules' -o -path './webui/dist' \) -prune -o \( \
        -name '.env' -o -name '.env.*' \
        -o -name '.secret.json' -o -name 'secret.json' \
        -o -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name 'id_rsa*' -o -name 'id_ed25519*' \
        -o -name '*.log' -o -name 'credentials.json' \
        -o -path './logs/*' -o -path './logs' \
        -o -path './flocks.json' -o -path './mcp_list.json' -o -path './.flocks/flocks.json' \
        -o -path './.flocks/mcp_list.json' -o -path './.flocks/.secret.json' \
        -o -path './.flocks/config/*' -o -path './.flocks/data/*' -o -path './.flocks/run/*' \
        -o -path './.flocks/logs/*' -o -path './.flocks/workspace/*' \
      \) -not -name '*.example' -print 2>/dev/null | sort
  )"
  if [[ -n "$offenders" ]]; then
    echo "export-source: refusing to ship runtime/secret files:" >&2
    while IFS= read -r offender; do printf '  %s\n' "$offender" >&2; done <<< "$offenders"
    return 1
  fi
  [[ -f "$dest/pyproject.toml" && -f "$dest/uv.lock" && -d "$dest/flocks" ]] || fail "export is incomplete (pyproject.toml / uv.lock / flocks/ missing)."
}

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  [[ -n "$REPO_ROOT" ]] || { echo "usage: export-source.sh --verify-only <dir>" >&2; exit 2; }
  verify_tree "$REPO_ROOT"
  echo "verified=$REPO_ROOT"
  exit 0
fi
[[ -n "$REPO_ROOT" && -n "$DEST" ]] || { echo "usage: export-source.sh <repo-root> <dest-dir> [--allow-dirty]" >&2; exit 2; }
# the repo may be owned by another uid (bind mount into a root container); do not
# touch the global git config, just trust this path for these commands
git_repo() { git -c safe.directory='*' -C "$REPO_ROOT" "$@"; }

command -v git >/dev/null || fail "git is required to export the source tree."
git_repo rev-parse --show-toplevel >/dev/null 2>&1 || fail "$REPO_ROOT is not a git repository."
[[ -f "$REPO_ROOT/pyproject.toml" ]] || fail "$REPO_ROOT has no pyproject.toml."

# development-only trees that never ship, as Git pathspecs anchored at the repository root.
# Not tar --exclude patterns: those are unanchored in bsdtar (macOS hosts), so './assets'
# would also drop every nested directory called assets (e.g. the hub skills' assets/).
PATHSPEC=(
  '.'
  ':(exclude).git' ':(exclude).venv' ':(exclude)webui/node_modules' ':(exclude)webui/dist'
  ':(exclude)tui/node_modules' ':(exclude)temp' ':(exclude)tests' ':(exclude)dist'
  ':(exclude)packaging' ':(exclude)npm-wrapper' ':(exclude)docs' ':(exclude)assets'
  ':(exclude).github' ':(exclude).claude'
  ':(exclude,glob)**/__pycache__/**' ':(exclude,glob)**/.pytest_cache/**'
  ':(exclude,glob)**/.mypy_cache/**' ':(exclude,glob)**/.ruff_cache/**' ':(exclude,glob)**/*.pyc'
)

count_tree() { find "$1" \( -type f -o -type l \) -not -name '.source-export.json' | wc -l | tr -d ' '; }

COMMIT="$(git_repo rev-parse --short HEAD)"
mkdir -p "$DEST"
if [[ -n "$(ls -A "$DEST" 2>/dev/null)" && "${EXPORT_SOURCE_ALLOW_NONEMPTY:-0}" != "1" ]]; then
  fail "destination $DEST is not empty."
fi

if [[ "$ALLOW_DIRTY" -eq 0 ]]; then
  if [[ -n "$(git_repo status --porcelain --untracked-files=no)" ]]; then
    fail "working tree has uncommitted changes; commit them or pass --allow-dirty."
  fi
  MODE="clean"
  DIRTY=0
  git_repo archive --format=tar --prefix=./ HEAD -- "${PATHSPEC[@]}" | tar -x -C "$DEST" -f -
  # the tree is clean (checked above), so the index equals HEAD for tracked files;
  # ls-tree does not take exclude pathspecs, ls-files does
  EXPECTED="$(git_repo ls-files --cached -- "${PATHSPEC[@]}" | wc -l | tr -d ' ')"
else
  MODE="dirty"
  DIRTY=1
  LIST="$(mktemp)"
  trap 'rm -f "$LIST"' EXIT
  # tracked + untracked-but-not-ignored; skip paths deleted from the working tree
  # (an `if` rather than `[[ ]] && printf`: with the latter a skipped last entry would leave
  #  the loop — and under pipefail the whole export — with exit status 1)
  git_repo ls-files -z --cached --others --exclude-standard -- "${PATHSPEC[@]}" \
    | tr '\0' '\n' \
    | while IFS= read -r rel; do
        if [[ -n "$rel" && -e "$REPO_ROOT/$rel" ]]; then printf './%s\n' "$rel"; fi
      done > "$LIST"
  tar -C "$REPO_ROOT" -cf - -T "$LIST" | tar -x -C "$DEST" -f -
  EXPECTED="$(wc -l < "$LIST" | tr -d ' ')"
fi
# every listed file must have landed: catches an exclusion pattern eating nested directories
ACTUAL="$(count_tree "$DEST")"
if [[ "${EXPORT_SOURCE_ALLOW_NONEMPTY:-0}" != "1" && "$ACTUAL" != "$EXPECTED" ]]; then
  fail "exported $ACTUAL files but Git lists $EXPECTED for this pathspec; refusing an incomplete tree."
fi

verify_tree "$DEST"
printf '{"commit": "%s", "dirty": %s, "mode": "%s"}\n' "$COMMIT" "$DIRTY" "$MODE" > "$DEST/.source-export.json"
echo "commit=$COMMIT dirty=$DIRTY mode=$MODE files=$ACTUAL"
