"""
Python fallback searches, kept off the event loop.

Without ripgrep, glob and grep walk the tree in Python. The walk is plain
blocking file system work: run on the event loop it holds up the whole
server -- every request, every session -- until it ends, which for a home
directory is minutes. Here it runs in a worker thread, for a limited time,
and stops once nobody waits for it any more.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# How long a fallback walk may run (seconds) before it stops with what it
# has found so far.
TIME_LIMIT = 30.0


class Stop:
    """Whether a walk should stop: its time is up, or the search was given up.

    The walk calls it as it goes, from its thread; ``stopped`` tells
    afterwards whether it was cut short. Nothing interrupts the walk between
    two calls, so one long step -- listing a huge directory, reading a very
    long line -- runs to its end first.
    """

    def __init__(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds
        self._given_up = threading.Event()
        self.stopped = False

    def give_up(self) -> None:
        self._given_up.set()

    def __call__(self) -> bool:
        if not self.stopped and (self._given_up.is_set() or time.monotonic() >= self._deadline):
            self.stopped = True
        return self.stopped


async def run_in_thread(walk: Callable[[Stop], T]) -> tuple[T, bool]:
    """``walk(stop)`` in a worker thread: what it returned, and whether it was cut short."""
    stop = Stop(TIME_LIMIT)
    try:
        found = await asyncio.to_thread(walk, stop)
    except asyncio.CancelledError:
        # A thread cannot be cancelled: the walk ends at its next check.
        stop.give_up()
        raise
    return found, stop.stopped


def stopped_note() -> str:
    """What a search cut short at the time limit says under its results."""
    return (
        f"(Search stopped after {TIME_LIMIT:g} seconds: ripgrep is not installed and the "
        "Python fallback did not finish walking the directory, so results are incomplete. "
        "Search a more specific path, or install ripgrep.)"
    )
