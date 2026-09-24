import pytest

from flocks.db.registry import DatabaseConfigurationError, load_settings


def test_missing_pg_configuration_fails_without_creating_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DatabaseConfigurationError, match="PostgreSQL requires"):
        load_settings({})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("backend", ["sqlite", "mysql", "mariadb"])
def test_retired_backends_are_rejected(backend):
    with pytest.raises(DatabaseConfigurationError, match="Only PostgreSQL"):
        load_settings({"FLOCKS_DB_BACKEND": backend})


def test_dsn_file_and_errors_do_not_expose_credentials(tmp_path):
    path = tmp_path / "database-secret"
    path.write_text("postgresql://user:test-placeholder@localhost/db\n")
    settings = load_settings({"FLOCKS_DB_DSN_FILE": str(path)})
    assert settings.backend == "postgres" and not settings.is_sqlite
    assert "test-placeholder" not in settings.describe()
    with pytest.raises(DatabaseConfigurationError, match="Set only one"):
        load_settings({"FLOCKS_DB_DSN_FILE": str(path), "FLOCKS_DB_DSN": "postgresql://localhost/db"})


def test_sqlite_uri_cannot_be_disguised_as_pg_dsn():
    with pytest.raises(DatabaseConfigurationError, match="postgresql://"):
        load_settings({"FLOCKS_DB_DSN": "sqlite:///tmp/data.db"})
