"""CLI entrypoint for ``flocks browser``."""

from __future__ import annotations

import json
import os
import sys

from .admin import (
    _version,
    ensure_daemon,
    print_update_banner,
    restart_daemon,
    run_doctor,
    run_setup,
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
  flocks browser --reload         stop the daemon so the next call starts fresh
  flocks browser state save PATH [--url URL]
  flocks browser state load PATH [--url URL] [--no-reload]
  flocks browser state show PATH
"""


def _require_value(args: list[str], index: int, option: str) -> str:
    try:
        return args[index + 1]
    except IndexError as error:
        raise SystemExit(f"{option} requires a value") from error


def _parse_state_options(args: list[str], *, allow_no_reload: bool = False) -> tuple[str | None, bool]:
    url = None
    no_reload = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--url":
            url = _require_value(args, i, "--url")
            i += 2
            continue
        if allow_no_reload and arg == "--no-reload":
            no_reload = True
            i += 1
            continue
        raise SystemExit(f"unknown state option: {arg}")
    return url, no_reload


def _run_state_command(args: list[str]) -> None:
    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  flocks browser state save PATH [--url URL]\n"
            "  flocks browser state load PATH [--url URL] [--no-reload]\n"
            "  flocks browser state show PATH"
        )
        return

    action = args[0]
    if action not in {"save", "load", "show"}:
        raise SystemExit(f"unknown state action: {action}")
    if len(args) < 2:
        raise SystemExit(f"state {action} requires a file path")

    path = args[1]
    rest = args[2:]

    if action == "show":
        if rest:
            raise SystemExit(f"unexpected arguments for state show: {' '.join(rest)}")
        print(json.dumps(summarize_state(path), ensure_ascii=False, indent=2))
        return

    url, no_reload = _parse_state_options(rest, allow_no_reload=action == "load")

    print_update_banner()
    ensure_daemon()
    if action == "save":
        result = save_state(path, url=url)
    else:
        result = load_state(path, url=url, reload=not no_reload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


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
    if args and args[0] == "--reload":
        restart_daemon()
        print("daemon stopped; it will restart fresh on the next call")
        return
    if args and args[0] == "state":
        _run_state_command(args[1:])
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
