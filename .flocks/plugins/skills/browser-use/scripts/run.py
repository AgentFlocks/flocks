# ruff: noqa: E701,E702
import os
import sys

# Windows default stdout encoding is cp1252, which can't encode the 🟢 marker
# helpers prepend to tab titles (or anything else outside Latin-1). Force UTF-8
# so `print(page_info())` doesn't UnicodeEncodeError on Windows. Issue #124(4).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .admin import (
    _version,
    ensure_daemon,
    list_cloud_profiles,
    list_local_profiles,
    print_update_banner,
    restart_daemon,
    run_doctor,
    run_setup,
    run_update,
    start_remote_daemon,
    stop_remote_daemon,
    sync_local_profile,
)
from .helpers import *

HELP = """Browser Harness

Read SKILL.md for the default workflow and examples.
Run from this skill directory (.flocks/plugins/skills/browser-use):
  uv run python -m scripts.run ...

Typical usage:
  uv run python -m scripts.run -c '
  ensure_real_tab()
  print(page_info())
  '

Helpers are pre-imported. The daemon auto-starts and connects to the running browser.

Commands:
  uv run python -m scripts.run --version        print the installed version
  uv run python -m scripts.run --doctor         diagnose install, daemon, and browser state
  uv run python -m scripts.run --setup          interactively attach to your running browser
  uv run python -m scripts.run --update [-y]    pull the latest version (agents: pass -y)
  uv run python -m scripts.run --reload         stop the daemon so next call picks up code changes
"""


def main():
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print(HELP)
        return
    if args and args[0] == "--version":
        print(_version() or "unknown")
        return
    if args and args[0] == "--doctor":
        sys.exit(run_doctor())
    if args and args[0] == "--setup":
        sys.exit(run_setup())
    if args and args[0] == "--update":
        yes = any(a in {"-y", "--yes"} for a in args[1:])
        sys.exit(run_update(yes=yes))
    if args and args[0] == "--reload":
        restart_daemon()
        print("daemon stopped — will restart fresh on next call")
        return
    if args and args[0] == "--debug-clicks":
        os.environ["BH_DEBUG_CLICKS"] = "1"
        args = args[1:]
    if not args or args[0] != "-c":
        sys.exit('Usage: uv run python -m scripts.run -c "print(page_info())"')
    if len(args) < 2:
        sys.exit('Usage: uv run python -m scripts.run -c "print(page_info())"')
    print_update_banner()
    ensure_daemon()
    exec(args[1], globals())


if __name__ == "__main__":
    main()
