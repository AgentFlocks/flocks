"""A cursor-shaped result, so existing code does not have to be rewritten.

Several stores are written against aiosqlite's cursor:

    async with db.execute(sql, params) as cur:
        row = await cur.fetchone()
    ...
    cur = await db.execute(sql, params)
    return cur.rowcount

Rewriting every one of those by hand is a large mechanical diff, and the first
attempt at doing it with a regex broke 42 tests: the patterns vary (list
comprehensions over ``fetchall``, ``rowcount`` read after the block, results
consumed two statements later) and each variation fails differently.

So the result grows a cursor's shape instead. ``execute`` returns this, it
works as an async context manager and answers ``fetchone``/``fetchall``/
``rowcount``/``lastrowid``, and the store above keeps working on either
backend without being touched.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


class Result:
    """What a statement gives back, shaped like a DB-API cursor."""

    __slots__ = ("_rows", "_rowcount", "_lastrowid", "_offset")

    def __init__(
        self,
        rows: Sequence[Any] = (),
        *,
        rowcount: int = -1,
        lastrowid: Optional[int] = None,
    ) -> None:
        self._rows = list(rows)
        self._rowcount = rowcount
        self._lastrowid = lastrowid
        self._offset = 0

    # -- cursor surface ----------------------------------------------------

    async def fetchone(self) -> Optional[Any]:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    async def fetchall(self) -> list:
        remaining = self._rows[self._offset :]
        self._offset = len(self._rows)
        return remaining

    async def fetchmany(self, size: int = 1) -> list:
        chunk = self._rows[self._offset : self._offset + max(size, 0)]
        self._offset += len(chunk)
        return chunk

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    async def close(self) -> None:
        return None

    # -- usable as ``async with`` ------------------------------------------

    async def __aenter__(self) -> "Result":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    # -- and as ``async for`` ----------------------------------------------

    def __aiter__(self) -> "Result":
        return self

    async def __anext__(self) -> Any:
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row

    def __len__(self) -> int:
        return len(self._rows)


class Execution:
    """What ``execute`` hands back: awaitable, and usable as ``async with``.

    aiosqlite's execute can be used either way, and the stores use both forms.
    A plain coroutine only supports the first, so this wraps one and defers to
    it exactly once however it is consumed.
    """

    __slots__ = ("_coroutine", "_result")

    def __init__(self, coroutine) -> None:
        self._coroutine = coroutine
        self._result: Optional[Result] = None

    async def _run(self) -> "Result":
        if self._result is None:
            self._result = await self._coroutine
        return self._result

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> "Result":
        return await self._run()

    async def __aexit__(self, *exc_info) -> bool:
        return False


__all__ = ["Execution", "Result"]
