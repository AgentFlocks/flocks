#!/usr/bin/env bash
# The open-edition archive the transition package (old format zip: this archive + the old wheel) is built from on Console.
# - A built interface and no webui/package.json, package-lock.json: an old updater then neither looks for npm nor builds (updater.py in
#   every old version only builds where webui/package.json exists).
# - Its own build backend in place of hatchling and the lock's index configured: an old updater's `uv sync` then needs no network
#   on a machine that has the locked packages, and that backend starts the watchdog that puts the old version back if the old
#   updater fails (flocks_transition_build.py).
# usage: make_core.sh <core-commit> <core-version> <checkout> <out-dir>
set -euo pipefail
core=$1 version=$2 checkout=$3 out=$4
here="$(cd "$(dirname "$0")" && pwd)"
work=$(mktemp -d "${TMPDIR:-/tmp}/transition-core.XXXXXX")
trap 'chmod -R u+w "$work" 2>/dev/null; rm -r "$work"' EXIT
git -C "$checkout" archive --prefix=flocks/ "$core" | tar -x -C "$work"
( cd "$work/flocks/webui"
  npm_config_registry=https://registry.npmmirror.com npm ci --no-audit --no-fund
  npm run build
  rm -r node_modules package.json package-lock.json
)
test -f "$work/flocks/webui/dist/index.html"
cp "$here/flocks_transition_build.py" "$here/flocks_transition_watchdog.py" "$work/flocks/"
python3 - "$work/flocks/pyproject.toml" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
new, count = re.subn(
    r'\[build-system\]\nrequires = \["hatchling"\]\nbuild-backend = "hatchling.build"\n',
    '[build-system]\nrequires = []\nbuild-backend = "flocks_transition_build"\nbackend-path = ["."]\n',
    text,
)
if count != 1:
    sys.exit("pyproject.toml: the build system is not the expected hatchling one")
# uv.lock names this index. A machine's own mirror (uv.toml, or the old updater's --default-index) would otherwise
# make the lock out of date, and uv would ask that mirror for all of it: with this index configured too, the lock
# stands and nothing is asked. Machines since 2026.7.23 have every locked package already.
if "[tool.uv]" in new or "[[tool.uv.index]]" in new:
    sys.exit("pyproject.toml: it has uv settings now, look at this again")
new = new.rstrip("\n") + '\n\n[[tool.uv.index]]\nname = "locked"\nurl = "https://pypi.org/simple"\n'
open(path, "w", encoding="utf-8").write(new)
lock = open(path.replace("pyproject.toml", "uv.lock"), encoding="utf-8").read()
if set(re.findall(r'registry = "([^"]+)"', lock)) != {"https://pypi.org/simple"}:
    sys.exit("uv.lock: it names another index than the one configured here")
PY
find "$work/flocks" -name '.DS_Store' -delete
mkdir -p "$out"
COPYFILE_DISABLE=1 tar -C "$work" --no-xattrs -czf "$out/flocks-core-$version.tar.gz" flocks
( cd "$out" && shasum -a 256 "flocks-core-$version.tar.gz" | tee "flocks-core-$version.tar.gz.sha256" )
ls -la "$out/flocks-core-$version.tar.gz"
git -C "$checkout" rev-parse "$core" > "$out/flocks-core-$version.commit"
