"""Building a WHERE clause without counting placeholders by hand.

SQLite and MySQL take positional markers that all look the same (``?``,
``%s``), so code that builds a clause conditionally can append markers in any
order and stay correct. PostgreSQL numbers them, and the number is the
parameter's position in the value tuple. The moment a filter becomes optional,
keeping those two in step by hand is a bug waiting to happen -- and it is a
silent one, because ``$2`` still runs, it just reads the wrong value.

``Params`` removes the bookkeeping: ask it for a marker, give it the value,
and it hands back whatever this backend expects while keeping the tuple in the
matching order.

    params = Params(handle.dialect)
    clause = f"WHERE session_id = {params.add(session_id)}"
    if start:
        clause += f" AND created_at >= {params.add(start)}"
    rows = await handle.fetch_all_mappings(f"SELECT ... {clause}", params.values)
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from flocks.db.dialect import Dialect


class Params:
    """Collects bind values and emits correctly numbered placeholders."""

    __slots__ = ("_dialect", "_values")

    def __init__(self, dialect: Dialect) -> None:
        self._dialect = dialect
        self._values: list[Any] = []

    def add(self, value: Any) -> str:
        """Record *value* and return the marker that refers to it."""
        self._values.append(value)
        return self._dialect.placeholder(len(self._values))

    def add_many(self, values: Iterable[Any]) -> str:
        """Record several values and return their markers, comma separated.

        For ``IN (...)`` lists, where the number of markers is only known at
        call time.
        """
        markers = [self.add(value) for value in values]
        return ", ".join(markers)

    @property
    def values(self) -> tuple:
        """The bind values, in the order their markers were issued."""
        return tuple(self._values)

    def __len__(self) -> int:
        return len(self._values)


def in_clause(params: Params, column: str, values: Sequence[Any]) -> str:
    """``column IN (...)``, or a predicate that is false when *values* is empty.

    An empty IN list is a syntax error on every backend, and the intent in
    that case is "match nothing" rather than "match everything", which is what
    dropping the clause would do.
    """
    if not values:
        return "1 = 0"
    return f"{column} IN ({params.add_many(values)})"


__all__ = ["Params", "in_clause"]
