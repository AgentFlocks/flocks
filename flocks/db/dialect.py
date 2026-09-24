"""PostgreSQL SQL generation and the stable dialect contract for extensions."""

from __future__ import annotations


from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Dialect(Protocol):
    """What a store needs to know about the database under it."""

    name: str

    def placeholder(self, position: int) -> str:
        """Bind marker for the *position*-th parameter, counting from 1.

        SQLite and MySQL ignore the position; PostgreSQL needs it, which is
        why callers must pass it rather than concatenating a constant.
        """
        ...

    def quote(self, identifier: str) -> str:
        """Quote a table or column name."""
        ...

    def upsert(
        self,
        table: str,
        columns: Sequence[str],
        *,
        key_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> str:
        """Insert, or update the row that already owns *key_columns*.

        Every backend spells this differently and two of the spellings are
        not merely syntax: MySQL keys the update on any unique index rather
        than the columns named here.
        """
        ...

    def now(self) -> str:
        """Expression for the current timestamp."""
        ...

    def now_text(self) -> str:
        """The current UTC timestamp as text, in SQLite's CURRENT_TIMESTAMP shape.

        Several tables store timestamps in a TEXT column and default it to
        CURRENT_TIMESTAMP. That works on SQLite because its value already is
        text; on a server backend it is a timestamp type, and assigning one to
        a text column in a DEFAULT is rejected outright. Same instant, same
        ``YYYY-MM-DD HH:MM:SS`` in UTC, spelled per backend.
        """
        ...

    def json_value(self, column: str, path: str) -> str:
        """Read a scalar out of a JSON column, given a ``$.a.b`` style path."""
        ...

    def text_match(self, column: str, placeholder: str) -> str:
        """Full-text predicate. See ``supports_full_text`` before using it."""
        ...

    # -- DDL types ---------------------------------------------------------
    # Table definitions differ by type name, not just by syntax. A module that
    # writes "INTEGER PRIMARY KEY AUTOINCREMENT" has chosen SQLite forever;
    # asking the dialect keeps one CREATE TABLE serving every backend.

    def insert_ignore(self, table: str, columns: Sequence[str]) -> str:
        """INSERT that silently skips a row violating any unique constraint.

        SQLite spells it ``INSERT OR IGNORE``. The conflict target is left
        unnamed on purpose: the constraint being avoided here is a *partial*
        unique index, and naming one as an arbiter means repeating its
        predicate in a form only PostgreSQL accepts.
        """
        ...

    def autoincrement_pk(self) -> str:
        """Column definition for a surrogate auto-incrementing primary key."""
        ...

    def text_type(self) -> str:
        ...

    def integer_type(self) -> str:
        """Whole-number column wide enough for what this codebase stores in one.

        SQLite's INTEGER holds 64 bits, and the code was written against that:
        timestamps are milliseconds since the epoch, which passed the 32-bit
        range in 1970 + 24 days. A backend whose INTEGER is 32 bits therefore
        needs its 64-bit type here, or every such write fails at runtime.
        """
        ...

    def json_type(self) -> str:
        ...

    def timestamp_type(self) -> str:
        ...

    def boolean_type(self) -> str:
        ...

    def blob_type(self) -> str:
        ...

    def double_type(self) -> str:
        """Floating point money/latency columns."""
        ...

    def date_of(self, column: str) -> str:
        """Truncate a timestamp column to a date, for grouping."""
        ...

    def columns_query(self) -> str:
        """Statement returning one row per column of a table, given its name.

        SQLite answers with PRAGMA, which takes the table name inline; the
        others read information_schema with a bound parameter. Callers use
        :meth:`columns_query_params` so they do not have to know which.
        """
        ...

    def columns_query_params(self, table: str) -> tuple:
        ...

    def column_name_index(self) -> int:
        """Which column of ``columns_query``'s result holds the column name."""
        ...

    @property
    def supports_returning(self) -> bool:
        ...

    @property
    def supports_full_text(self) -> bool:
        ...


def _split_path(path: str) -> list[str]:
    """``$.a.b`` -> ``['a', 'b']``. Bare names are accepted as single steps."""
    cleaned = path[2:] if path.startswith("$.") else path.lstrip("$").lstrip(".")
    return [step for step in cleaned.split(".") if step]




class PostgresDialect:
    """The target backend."""

    name = "postgres"

    def placeholder(self, position: int) -> str:
        return f"${position}"

    def quote(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def upsert(
        self,
        table: str,
        columns: Sequence[str],
        *,
        key_columns: Sequence[str],
        update_columns: Sequence[str],
    ) -> str:
        names = ", ".join(self.quote(column) for column in columns)
        values = ", ".join(self.placeholder(index) for index in range(1, len(columns) + 1))
        keys = ", ".join(self.quote(column) for column in key_columns)
        head = f"INSERT INTO {self.quote(table)} ({names}) VALUES ({values})"
        if not update_columns:
            return f"{head} ON CONFLICT ({keys}) DO NOTHING"
        assignments = ", ".join(
            f"{self.quote(column)} = EXCLUDED.{self.quote(column)}" for column in update_columns
        )
        return f"{head} ON CONFLICT ({keys}) DO UPDATE SET {assignments}"

    def now(self) -> str:
        return "NOW()"

    def now_text(self) -> str:
        return "to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"

    def json_value(self, column: str, path: str) -> str:
        steps = _split_path(path)
        if not steps:
            return column
        # ``->`` keeps JSON for intermediate steps; the last one uses ``->>``
        # so the result is text, matching what json_extract returns elsewhere.
        walk = "".join(f"->'{step}'" for step in steps[:-1])
        return f"{column}{walk}->>'{steps[-1]}'"

    def text_match(self, column: str, placeholder: str) -> str:
        return f"{column} @@ plainto_tsquery('simple', {placeholder})"

    def insert_ignore(self, table: str, columns: Sequence[str]) -> str:
        names = ", ".join(self.quote(column) for column in columns)
        markers = ", ".join(self.placeholder(index) for index in range(1, len(columns) + 1))
        return (
            f"INSERT INTO {self.quote(table)} ({names}) VALUES ({markers}) "
            "ON CONFLICT DO NOTHING"
        )

    def autoincrement_pk(self) -> str:
        raise ValueError("Generate primary keys in the application; sequences are not used")

    def text_type(self) -> str:
        return "TEXT"

    def integer_type(self) -> str:
        # BIGINT, not INTEGER: PostgreSQL's INTEGER is 32 bits and the values
        # this maps are SQLite INTEGERs, which are 64.
        return "BIGINT"

    def json_type(self) -> str:
        return "JSONB"

    def timestamp_type(self) -> str:
        return "BIGINT"

    def boolean_type(self) -> str:
        return "BOOLEAN"

    def blob_type(self) -> str:
        return "BYTEA"

    def double_type(self) -> str:
        return "DOUBLE PRECISION"

    def date_of(self, column: str) -> str:
        # PostgreSQL has no DATE() function; the cast is the idiom.
        return f"({column})::date"

    def columns_query(self) -> str:
        return (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1 "
            "ORDER BY ordinal_position"
        )

    def columns_query_params(self, table: str) -> tuple:
        return (table,)

    def column_name_index(self) -> int:
        return 0

    @property
    def supports_returning(self) -> bool:
        return True

    @property
    def supports_full_text(self) -> bool:
        return True




_DIALECTS: dict[str, Dialect] = {
    "postgres": PostgresDialect(),
    "postgresql": PostgresDialect(),
}


def get_dialect(name: str) -> Dialect:
    """Look up a dialect by backend name, case-insensitively."""
    try:
        return _DIALECTS[name.strip().lower()]
    except KeyError:
        supported = ", ".join(sorted({dialect.name for dialect in _DIALECTS.values()}))
        raise ValueError(f"unknown database backend {name!r}; supported: {supported}") from None


__all__ = [
    "Dialect",
    "PostgresDialect",
    "get_dialect",
]
