"""CLI entrypoint for ``flocks browser``."""

from __future__ import annotations

import os
import sys

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
from .helpers import *  # noqa: F403


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


HELP = """Flocks Browser

Read the browser-use skill for the default workflow and examples.

Typical usage:
  flocks browser -c '
  ensure_real_tab()
  print(page_info())
  '

Helpers are pre-imported. The daemon auto-starts and connects to the running browser.

Commands:
  flocks browser --version        print the current Flocks version
  flocks browser --doctor         diagnose install, daemon, and browser state
  flocks browser --setup          interactively attach to your running browser
  flocks browser --update [-y]    update the current Flocks install if possible
  flocks browser --reload         stop the daemon so the next call starts fresh
"""


def main(argv: list[str] | None = None) -> None:
    """Run the raw browser command interface."""
    args = list(sys.argv[1:] if argv is None else argv)
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
        yes = any(arg in {"-y", "--yes"} for arg in args[1:])
        sys.exit(run_update(yes=yes))
    if args and args[0] == "--reload":
        restart_daemon()
        print("daemon stopped; it will restart fresh on the next call")
        return
    if args and args[0] == "--debug-clicks":
        os.environ["BH_DEBUG_CLICKS"] = "1"
        args = args[1:]
    if not args or args[0] != "-c":
        sys.exit('Usage: flocks browser -c "print(page_info())"')
    if len(args) < 2:
        sys.exit('Usage: flocks browser -c "print(page_info())"')
    print_update_banner()
    ensure_daemon()
    exec(args[1], globals())  # noqa: S102


if __name__ == "__main__":
    main()
