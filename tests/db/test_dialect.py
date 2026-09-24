"""PostgreSQL SQL generation and rejection of removed runtime backends."""
from __future__ import annotations

import pytest

from flocks.db import registry
from flocks.db.dialect import PostgresDialect, get_dialect


def test_postgres_markers_quotes_json_and_types():
    dialect = PostgresDialect()
    assert [dialect.placeholder(i) for i in (1, 2)] == ["$1", "$2"]
    assert dialect.quote('a"b') == '"a""b"'
    assert dialect.json_value("doc", "$.a.b") == "doc->'a'->>'b'"
    assert dialect.json_value("doc", "$.a") == "doc->>'a'"
    assert dialect.json_type() == "JSONB"
    assert dialect.boolean_type() == "BOOLEAN"
    assert dialect.integer_type() == "BIGINT"
    assert dialect.blob_type() == "BYTEA"
    assert dialect.supports_returning and dialect.supports_full_text
    assert dialect.text_match("tsv", "$1") == "tsv @@ plainto_tsquery('simple', $1)"
    with pytest.raises(ValueError, match="primary keys"):
        dialect.autoincrement_pk()


def test_postgres_upsert_numbers_its_parameters():
    sql = PostgresDialect().upsert("state", ("key", "value"), key_columns=("key",), update_columns=("value",))
    assert sql == 'INSERT INTO "state" ("key", "value") VALUES ($1, $2) ON CONFLICT ("key") DO UPDATE SET "value" = EXCLUDED."value"'
    assert "DO NOTHING" in PostgresDialect().upsert("t", ("a",), key_columns=("a",), update_columns=())


@pytest.mark.parametrize("backend", ["sqlite", "mysql", "oracle"])
def test_removed_dialects_are_rejected(backend):
    with pytest.raises(ValueError):
        get_dialect(backend)
    with pytest.raises(registry.DatabaseConfigurationError):
        registry.load_settings({registry.BACKEND_ENV: backend})


def test_postgres_alias_and_required_dsn():
    assert get_dialect(" PostgreSQL ").name == "postgres"
    with pytest.raises(registry.DatabaseConfigurationError):
        registry.load_settings({})
    configured = registry.load_settings({registry.DSN_ENV: "postgresql://host/database"})
    assert configured.backend == "postgres" and not configured.is_sqlite


def test_describe_does_not_include_connection_credentials():
    settings = registry.load_settings({registry.DSN_ENV: "postgresql://example:fixture-secret@host/database"})
    assert settings.describe() == "postgres (dsn configured)"
    assert "fixture-secret" not in settings.describe()


def test_configure_is_process_wide_and_resettable():
    original = registry.current()
    registry.configure(registry.DatabaseSettings(backend="postgres", dsn="postgresql://host/database"))
    assert registry.current().dsn == "postgresql://host/database"
    registry.reset()
    assert registry.current().dsn == original.dsn


@pytest.mark.asyncio
async def test_insert_ignore_skips_a_duplicate(postgres_handle):
    await postgres_handle.execute("CREATE TABLE dialect_probe (id TEXT PRIMARY KEY, body TEXT)")
    sql = postgres_handle.dialect.insert_ignore("dialect_probe", ("id", "body"))
    await postgres_handle.execute(sql, ("one", "original"))
    await postgres_handle.execute(sql, ("one", "duplicate"))
    assert await postgres_handle.fetch_one("SELECT body FROM dialect_probe WHERE id = ?", ("one",)) == ("original",)
