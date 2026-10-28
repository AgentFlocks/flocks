"""A database backend other than SQLite replaces the runtime modules it registers, and nothing else.

The replacement has to be in place before the first import of a replaced
module, so the end-to-end cases start a fresh interpreter with a stand-in
backend distribution on its path -- entry point metadata and all -- and import
``flocks`` the way any process does.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks import db_backend

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Which backend
# ---------------------------------------------------------------------------


def test_sqlite_is_the_default(tmp_path):
    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path)})

    assert (choice.backend, choice.source, choice.is_sqlite) == ("sqlite", "default", True)


def test_the_environment_variable_comes_first(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "database.json").write_text(json.dumps({"backend": "sqlite"}), encoding="utf-8")

    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DB_BACKEND": "ExternalDB"})

    assert (choice.backend, choice.source) == ("externaldb", "FLOCKS_DB_BACKEND")


def test_a_persisted_choice_comes_before_the_connection_variables(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "database.json").write_text(
        json.dumps({"backend": "externaldb", "dsn_file": "/srv/flocks/dsn"}), encoding="utf-8",
    )

    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DB_DSN": "externaldb://other/db"})

    assert (choice.backend, choice.source, choice.dsn_file) == ("externaldb", str(config / "database.json"), "/srv/flocks/dsn")


def test_flocks_config_dir_moves_the_persisted_choice(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "database.json").write_text(json.dumps({"backend": "externaldb"}), encoding="utf-8")

    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path / "root"), "FLOCKS_CONFIG_DIR": str(elsewhere)})

    assert choice.source == str(elsewhere / "database.json")


class _Entry:
    """An installed ``flocks.db_backends`` entry point, without a distribution."""

    def __init__(self, name: str, provider: object) -> None:
        self.name, self.value, self.dist, self._provider = name, f"stand_in:{name}", None, provider

    def load(self) -> object:
        return self._provider


def _installed_backends(monkeypatch, **providers) -> None:
    entries = [_Entry(name, provider) for name, provider in providers.items()]
    real = importlib.metadata.entry_points

    def _entry_points(**params):
        if params.get("group") == db_backend.ENTRY_POINT_GROUP:
            return entries
        return real(**params)

    monkeypatch.setattr(db_backend.importlib.metadata, "entry_points", _entry_points)


@pytest.mark.parametrize("variable", ["FLOCKS_DB_DSN", "FLOCKS_DB_DSN_FILE"])
def test_a_connection_string_alone_selects_the_installed_backend(tmp_path, monkeypatch, variable):
    """Deployments configured before there was a choice keep the backend they run on."""
    _installed_backends(monkeypatch, externaldb=SimpleNamespace(modules={}))

    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path), variable: "externaldb://flocks@db/flocks"})

    assert (choice.backend, choice.source) == ("externaldb", variable)


def test_a_connection_string_with_no_backend_installed_is_not_sqlite(tmp_path, monkeypatch):
    _installed_backends(monkeypatch)

    choice = db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DB_DSN": "externaldb://flocks@db/flocks"})

    assert (choice.backend, choice.is_sqlite) == (db_backend.UNPROVIDED_BACKEND, False)


def test_of_several_backends_the_one_that_claims_a_connection_string_takes_it(tmp_path, monkeypatch):
    env = {"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DB_DSN": "externaldb://flocks@db/flocks"}
    _installed_backends(monkeypatch, other=SimpleNamespace(modules={}), externaldb=SimpleNamespace(modules={}, claims_dsn=True))
    assert db_backend.backend_choice(env).backend == "externaldb"

    _installed_backends(monkeypatch, other=SimpleNamespace(modules={}), externaldb=SimpleNamespace(modules={}))
    with pytest.raises(db_backend.BackendConfigurationError, match="more than one installed backend"):
        db_backend.backend_choice(env)


def test_a_backend_answers_to_its_aliases_under_its_installed_name(tmp_path, monkeypatch):
    _installed_backends(monkeypatch, externaldb=SimpleNamespace(modules={}, aliases=("ExternalDatabase",)))
    config = tmp_path / "config"
    config.mkdir()
    (config / "database.json").write_text(json.dumps({"backend": "externaldatabase"}), encoding="utf-8")

    assert db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DB_BACKEND": "ExternalDatabase"}).backend == "externaldb"
    assert db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path)}).backend == "externaldb"
    env = {"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DATA_DIR": str(tmp_path / "data")}
    assert db_backend.commit_backend("externaldatabase", env) == db_backend.commit_backend("externaldb", env)
    # A record that names the backend by an alias belongs to it as well.
    older = tmp_path / "older"
    older.mkdir()
    (older / db_backend.COMMITMENT_FILE_NAME).write_text(json.dumps({"version": 1, "backend": "ExternalDatabase"}))
    db_backend.commit_backend("externaldb", {"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DATA_DIR": str(older)})


@pytest.mark.parametrize("order", ["first", "second"])
def test_an_alias_two_backends_answer_to_chooses_neither(tmp_path, monkeypatch, order):
    """Whichever entry point comes first must not decide which database a deployment runs on."""
    shared = {"modules": {}, "aliases": ("shared",)}
    backends = {"backend_a": SimpleNamespace(**shared), "backend_b": SimpleNamespace(**shared)}
    if order == "second":
        backends = dict(reversed(list(backends.items())))
    _installed_backends(monkeypatch, **backends)
    config = tmp_path / "config"
    config.mkdir()
    env = {"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DATA_DIR": str(tmp_path / "data")}

    with pytest.raises(db_backend.BackendConfigurationError, match="More than one installed backend answers to 'shared'"):
        db_backend.backend_choice({**env, "FLOCKS_DB_BACKEND": "Shared"})
    (config / "database.json").write_text(json.dumps({"backend": "shared"}), encoding="utf-8")
    with pytest.raises(db_backend.BackendConfigurationError, match="answers to 'shared'"):
        db_backend.backend_choice(env)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / db_backend.COMMITMENT_FILE_NAME).write_text(json.dumps({"version": 1, "backend": "shared"}))
    with pytest.raises(db_backend.BackendConfigurationError, match="answers to 'shared'"):
        db_backend.commit_backend("backend_a", env)
    # A backend's own name still names it, whatever the others answer to.
    assert db_backend.backend_choice({**env, "FLOCKS_DB_BACKEND": "backend_b"}).backend == "backend_b"


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps(["postgres"]), json.dumps({"backend": ""}), json.dumps({"backend": "postgres", "dsn_file": 3})],
)
def test_an_unreadable_persisted_choice_stops_rather_than_guessing(tmp_path, content):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "database.json").write_text(content, encoding="utf-8")

    with pytest.raises(db_backend.BackendConfigurationError, match="database.json"):
        db_backend.backend_choice({"FLOCKS_ROOT": str(tmp_path)})


# ---------------------------------------------------------------------------
# What a backend may replace
# ---------------------------------------------------------------------------


class _Provider:
    def __init__(self, modules):
        self.modules = modules


def _everything(**overrides):
    modules = {module: "vendor." + module.removeprefix("flocks.") for module in db_backend.REPLACEABLE_MODULES}
    modules.update(overrides)
    return modules


def test_a_backend_replaces_every_listed_module():
    assert db_backend._replacements("pg", _Provider(_everything())) == _everything()


def test_a_backend_cannot_replace_a_module_outside_the_list():
    with pytest.raises(db_backend.BackendConfigurationError, match="flocks.server.auth"):
        db_backend._replacements("pg", _Provider(_everything(**{"flocks.server.auth": "elsewhere.auth"})))


def test_a_backend_may_bring_modules_the_runtime_does_not_have():
    added = {"flocks.vendor_layer": "vendor.layer", "flocks.workflow.vendor_store": "vendor.workflow_store"}

    assert db_backend._replacements("pg", _Provider(_everything(**added))) == _everything(**added)


@pytest.mark.parametrize("name", [
    "flocks.workflow",              # a package the runtime has
    "flocks.task.store.extra",      # inside a module it replaces
    "flocks.coordination.extra",
    "flocks",                       # the runtime itself
    "elsewhere.module",             # not a flocks name
    "flocks.not-an-identifier",
])
def test_an_addition_cannot_be_a_runtime_name(name):
    assert not db_backend._added_module(name)
    with pytest.raises(db_backend.BackendConfigurationError):
        db_backend._replacements("pg", _Provider(_everything(**{name: "vendor.x"})))


def test_a_backend_cannot_leave_a_listed_module_out():
    """A message store on PostgreSQL next to the single-process session lease would run, unchecked."""
    modules = _everything()
    del modules["flocks.session.ownership"]

    with pytest.raises(db_backend.BackendConfigurationError, match="flocks.session.ownership"):
        db_backend._replacements("pg", _Provider(modules))


@pytest.mark.parametrize("target", ["flocks.task.other_store", "flocks", "", None])
def test_a_backend_must_bring_its_own_module(target):
    with pytest.raises(db_backend.BackendConfigurationError, match="module of its own"):
        db_backend._replacements("pg", _Provider(_everything(**{"flocks.task.store": target})))


def test_a_backend_must_say_what_it_replaces():
    with pytest.raises(db_backend.BackendConfigurationError, match="which modules"):
        db_backend._replacements("pg", object())


def test_a_submodule_of_a_replaced_package_follows_it():
    finder = db_backend._ReplacementFinder({"flocks.db": "vendor.db", "flocks.task.store": "vendor.task_store"})

    assert finder.target("flocks.db") == "vendor.db"
    assert finder.target("flocks.db.backends.postgres") == "vendor.db.backends.postgres"
    assert finder.target("flocks.task.store") == "vendor.task_store"
    assert finder.target("flocks.dbx") is None
    assert finder.target("flocks.task.storage") is None


# ---------------------------------------------------------------------------
# End to end, in a fresh interpreter
# ---------------------------------------------------------------------------


def _stand_in_name(package: str, module: str) -> str:
    return f"{package}.mods.{module.removeprefix('flocks.').replace('.', '__')}"


#: A module the stand-in brings of its own: a database layer the runtime does not have.
ADDED = "flocks.db"


def _stand_in_modules(package: str = "stand_in_backend") -> dict:
    """Every replaceable module, plus the stand-in's own database layer."""
    return {module: _stand_in_name(package, module) for module in [*db_backend.REPLACEABLE_MODULES, ADDED]}


def _install_backend(
    site: Path, name: str, modules: dict | None = None, *, dist: str = "stand_in_backend", package: str = "stand_in_backend",
) -> None:
    """A distribution on *site* registering *name* under ``flocks.db_backends``.

    By default it replaces every replaceable module with a stand-in that answers
    any name with a placeholder class, and brings a database package of its own
    (``flocks.db``); the task store and the database package say who they are.
    """
    if modules is None:
        modules = _stand_in_modules(package)
    pkg = site / package
    mods = pkg / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        f"from types import SimpleNamespace\nBACKEND = SimpleNamespace(modules={modules!r})\n", encoding="utf-8",
    )
    (mods / "__init__.py").write_text("", encoding="utf-8")
    placeholder = (
        "def __getattr__(name):\n"
        "    if name.startswith('__'):\n"
        "        raise AttributeError(name)\n"
        "    return type(name, (), {'kind': 'stand-in'})\n"
    )
    for target in modules.values():
        if not target.startswith(f"{package}.mods."):
            continue
        leaf = target.rsplit(".", 1)[1]
        if leaf in {"db", "runtime_state"}:
            (mods / leaf).mkdir(exist_ok=True)
            (mods / leaf / "__init__.py").write_text(placeholder + f"WHO = 'stand-in {leaf}'\n", encoding="utf-8")
        else:
            (mods / f"{leaf}.py").write_text(placeholder, encoding="utf-8")
    (mods / "task__store.py").write_text(
        textwrap.dedent(
            """
            from flocks.task.models import TaskStatus  # the replacement uses the runtime like any module

            class TaskStore:
                kind = "stand-in"
            """
        ),
        encoding="utf-8",
    )
    (mods / "db" / "connect.py").write_text("def open_transaction():\n    return 'stand-in transaction'\n", encoding="utf-8")
    (mods / "db" / "migrate.py").write_text(
        "import sys\nif __name__ == '__main__':\n    print('stand-in migrate', *sys.argv[1:])\n", encoding="utf-8",
    )
    info = site / f"{dist}-1.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n", encoding="utf-8")
    entry_points = info / "entry_points.txt"
    existing = entry_points.read_text(encoding="utf-8") if entry_points.exists() else "[flocks.db_backends]\n"
    entry_points.write_text(existing + f"{name} = {package}:BACKEND\n", encoding="utf-8")


def _run(tmp_path: Path, site: Path, env: dict, code: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FLOCKS_")}
    clean.update({"HOME": str(home), "FLOCKS_ROOT": str(tmp_path / "root"), "PYTHONPATH": os.pathsep.join([str(site), str(REPO_ROOT)])})
    clean.update(env)
    return subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        capture_output=True, text=True, env=clean, cwd=tmp_path, timeout=120,
    )


PROBE = """
    import json, sys
    import flocks
    from flocks.task.store import TaskStore
    import flocks.task.store as store
    try:
        import flocks.db.connect as connect
        connected = connect.open_transaction() if connect.__name__.startswith("stand_in") else connect.__name__
    except ImportError as exc:
        connected = repr(exc)
    from flocks import db_backend
    print(json.dumps({
        "kind": getattr(TaskStore, "kind", "runtime"),
        "same_object": store is sys.modules.get("stand_in_backend.mods.task__store"),
        "spec": store.__spec__.name,
        "connect": connected,
        "attribute": flocks.task.store is store,
        "describe": db_backend.describe(),
    }))
"""


def test_the_registered_backend_replaces_what_it_names(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, PROBE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["kind"] == "stand-in"
    assert seen["same_object"] is True
    assert seen["spec"] == "stand_in_backend.mods.task__store"
    assert seen["connect"] == "stand-in transaction"
    assert seen["attribute"] is True
    assert seen["describe"] == {
        "backend": "standin",
        "source": "FLOCKS_DB_BACKEND",
        "provider": "stand_in_backend==1.0",
        "replaced": sorted([*db_backend.REPLACEABLE_MODULES, ADDED]),
    }


def test_sqlite_replaces_nothing_even_with_a_backend_installed(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")

    result = _run(tmp_path, site, {}, PROBE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["kind"] == "runtime"
    assert seen["spec"] == "flocks.task.store"
    assert seen["describe"]["replaced"] == []


def test_a_persisted_choice_selects_the_backend(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    (tmp_path / "root" / "config").mkdir(parents=True)
    (tmp_path / "root" / "config" / "database.json").write_text(json.dumps({"backend": "standin"}), encoding="utf-8")

    result = _run(tmp_path, site, {}, PROBE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["kind"] == "stand-in"
    assert seen["describe"]["source"].endswith("database.json")


def test_a_backend_reaching_outside_the_list_stops_the_process(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin", {**_stand_in_modules(), "flocks.server.auth": "stand_in_backend.mods.task__store"})

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, "import flocks")

    assert result.returncode != 0
    assert "BackendConfigurationError" in result.stderr
    assert "flocks.server.auth" in result.stderr


def test_two_distributions_claiming_one_backend_stop_the_process(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    _install_backend(site, "standin", dist="other_backend", package="other_backend")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, "import flocks")

    assert result.returncode != 0
    assert "More than one installed backend" in result.stderr


def test_a_backend_that_fails_to_load_stops_the_process(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    (site / "stand_in_backend" / "__init__.py").write_text("raise ImportError('licence check failed')\n", encoding="utf-8")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, "import flocks")

    assert result.returncode != 0
    assert "failed to load" in result.stderr
    assert "licence check failed" in result.stderr


def test_a_backend_nobody_provides_leaves_the_runtime_modules_in_place(tmp_path):
    site = tmp_path / "site"
    site.mkdir()

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, PROBE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["kind"] == "runtime"
    assert seen["describe"]["provider"] is None


@pytest.mark.parametrize("explicit_sqlite", [False, True])
def test_a_committed_backend_refuses_an_older_sqlite_view_before_imports(tmp_path, explicit_sqlite):
    site = tmp_path / "site"
    site.mkdir()
    data = tmp_path / "data-kept-separately"
    data.mkdir()
    marker = data / db_backend.COMMITMENT_FILE_NAME
    marker.write_text(json.dumps({"backend": "postgres", "version": 1}))
    old = data / "flocks.db"
    old.write_bytes(b"old database must remain untouched")
    env = {"FLOCKS_DATA_DIR": str(data)}
    if explicit_sqlite:
        env["FLOCKS_DB_BACKEND"] = "sqlite"

    result = _run(tmp_path, site, env, "import flocks")

    assert result.returncode != 0
    assert "refusing to open an older database" in result.stderr
    assert old.read_bytes() == b"old database must remain untouched"
    assert json.loads(marker.read_text())["backend"] == "postgres"


def test_a_backend_can_validate_even_when_sqlite_was_selected(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    module = site / "stand_in_backend" / "__init__.py"
    module.write_text(module.read_text() + "\ndef validate(choice, environ=None):\n"
                      "    if choice.backend == 'sqlite':\n"
                      "        raise RuntimeError('the migrated data cannot use sqlite')\n"
                      "BACKEND.validate_choice = validate\n")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "sqlite"}, "import flocks")

    assert result.returncode != 0
    assert "the migrated data cannot use sqlite" in result.stderr


def test_backend_commitment_is_private_idempotent_and_never_overwrites_another_choice(tmp_path):
    env = {"FLOCKS_ROOT": str(tmp_path), "FLOCKS_DATA_DIR": str(tmp_path / "actual-data")}
    path = db_backend.commit_backend("ExternalDB", env)
    before = path.read_bytes()
    assert path == tmp_path / "actual-data" / db_backend.COMMITMENT_FILE_NAME
    assert path.stat().st_mode & 0o077 == 0
    assert db_backend.commit_backend("externaldb", env) == path
    assert path.read_bytes() == before
    with pytest.raises(db_backend.BackendConfigurationError, match="belongs to"):
        db_backend.commit_backend("sqlite", env)
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", ["{broken", "null", "{}", '{"backend": 1}'])
def test_corrupt_commitment_does_not_become_permission_to_open_sqlite(tmp_path, contents):
    site = tmp_path / "site"
    site.mkdir()
    data = tmp_path / "root" / "data"
    data.mkdir(parents=True)
    (data / db_backend.COMMITMENT_FILE_NAME).write_text(contents)
    result = _run(tmp_path, site, {}, "import flocks")
    assert result.returncode != 0
    assert "storage backend record" in result.stderr
    assert not (data / "flocks.db").exists()


@pytest.mark.parametrize("variable", ["FLOCKS_DATA_DIR", "XDG_DATA_HOME", "FLOCKS_ROOT"])
@pytest.mark.parametrize("value", ["~/existing-data", " /existing-data", "/existing-data "])
def test_ambiguous_storage_directory_is_refused_without_selecting_another_root(tmp_path, monkeypatch, variable, value):
    from flocks.config.config import Config
    for name in ("FLOCKS_DATA_DIR", "XDG_DATA_HOME", "FLOCKS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(Config, "_global_config", None)
    with pytest.raises(db_backend.BackendConfigurationError, match="directory"):
        db_backend.data_dir()
    with pytest.raises((db_backend.BackendConfigurationError, ValueError), match="directory"):
        Config.get_data_path()


@pytest.mark.parametrize("variable", ["FLOCKS_DATA_DIR", "XDG_DATA_HOME", "FLOCKS_ROOT"])
def test_commitment_and_storage_share_the_exact_directory_rule(tmp_path, monkeypatch, variable):
    from flocks.config.config import Config
    for name in ("FLOCKS_DATA_DIR", "XDG_DATA_HOME", "FLOCKS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, str(tmp_path / "existing data"))
    monkeypatch.setattr(Config, "_global_config", None)
    assert db_backend.data_dir() == Config.get_data_path()


@pytest.mark.parametrize("contents", ['{"version":2,"backend":"sqlite"}', '{"backend":"sqlite"}', 'x' * 65537])
def test_invalid_commitment_version_and_size_are_not_sqlite_permission(tmp_path, contents):
    data = tmp_path / "data"
    data.mkdir()
    (data / db_backend.COMMITMENT_FILE_NAME).write_text(contents)
    with pytest.raises(db_backend.BackendConfigurationError, match="storage backend record"):
        db_backend._validate_commitment(db_backend.BackendChoice("sqlite", "test"), {"FLOCKS_DATA_DIR": str(data)})


def test_dangling_commitment_is_not_treated_as_a_missing_record(tmp_path):
    marker = tmp_path / db_backend.COMMITMENT_FILE_NAME
    marker.symlink_to(tmp_path / "missing-retained-record")
    with pytest.raises(db_backend.BackendConfigurationError, match="storage backend record"):
        db_backend._validate_commitment(db_backend.BackendChoice("sqlite", "test"), {"FLOCKS_DATA_DIR": str(tmp_path)})


def test_removed_package_bytecode_caches_are_not_a_runtime_module(tmp_path, monkeypatch):
    package = tmp_path / "flocks"
    cache = package / "db" / "backends" / "__pycache__" / "postgres.cpython-312.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"retained cache")
    monkeypatch.setattr(db_backend, "_PACKAGE_DIR", package)
    assert db_backend._added_module("flocks.db")
    assert cache.read_bytes() == b"retained cache"
    (package / "db" / "__init__.py").write_text("")
    assert not db_backend._added_module("flocks.db")


def test_every_replaceable_module_is_a_runtime_module():
    import importlib.util

    missing = {name for name in db_backend.REPLACEABLE_MODULES if importlib.util.find_spec(name) is None}

    assert missing == set()


REFUSE_TO_OPEN_SQLITE = """
    import importlib.metadata

    _installed_entry_points = importlib.metadata.entry_points

    def _no_backend_installed(**params):
        # Whatever this interpreter has installed, nothing provides a backend here.
        if params.get("group") == "flocks.db_backends":
            return importlib.metadata.EntryPoints(())
        return _installed_entry_points(**params)

    importlib.metadata.entry_points = _no_backend_installed

    import asyncio, json
    import flocks
    from flocks import db_backend
    from flocks.storage.storage import Storage

    async def main():
        try:
            await Storage.init()
        except db_backend.BackendConfigurationError as exc:
            return {"refused": str(exc)}
        return {"refused": None}

    seen = asyncio.run(main())
    seen["sqlite_file"] = Storage.get_db_path().exists()
    print(json.dumps(seen))
"""


@pytest.mark.parametrize("configured", ["environment", "persisted", "connection string"])
def test_storage_refuses_sqlite_when_another_backend_was_chosen_but_nothing_provides_it(tmp_path, configured):
    """A deployment set to another database must not start on a new, empty SQLite database."""
    site = tmp_path / "site"
    site.mkdir()
    env = {"FLOCKS_DB_BACKEND": "externaldb"}
    expected = "'externaldb'"
    if configured == "persisted":
        env = {}
        (tmp_path / "root" / "config").mkdir(parents=True)
        (tmp_path / "root" / "config" / "database.json").write_text(
            json.dumps({"backend": "externaldb", "dsn_file": str(tmp_path / "dsn")}), encoding="utf-8",
        )
    if configured == "connection string":
        env = {"FLOCKS_DB_DSN": "externaldb://flocks@db/flocks"}
        expected = f"'{db_backend.UNPROVIDED_BACKEND}'"

    result = _run(tmp_path, site, env, REFUSE_TO_OPEN_SQLITE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["refused"] and expected in seen["refused"] and "FLOCKS_DB_BACKEND=sqlite" in seen["refused"]
    assert seen["sqlite_file"] is False


def test_storage_opens_sqlite_when_sqlite_is_chosen(tmp_path):
    site = tmp_path / "site"
    site.mkdir()

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "sqlite"}, REFUSE_TO_OPEN_SQLITE)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen == {"refused": None, "sqlite_file": True}


def test_the_dotenv_beside_the_source_tree_comes_first(tmp_path):
    from flocks import startup_env

    package = tmp_path / "release" / "flocks"
    package.mkdir(parents=True)
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    assert startup_env.dotenv_path(package) == tmp_path / ".env"
    (package / ".env").write_text("A=2\n", encoding="utf-8")
    assert startup_env.dotenv_path(package) == package / ".env"
    (tmp_path / "release" / ".env").write_text("A=3\n", encoding="utf-8")
    assert startup_env.dotenv_path(package) == tmp_path / "release" / ".env"


def test_loading_the_dotenv_keeps_what_the_environment_has(tmp_path, monkeypatch):
    """As load_dotenv(override=False): present-but-empty counts as set, and ${...} reads the environment first."""
    from flocks import startup_env

    package = tmp_path / "flocks"
    package.mkdir()
    (tmp_path / ".env").write_text(
        "FLOCKS_ROOT=/review/file\nFLOCKS_CONFIG_DIR=${FLOCKS_ROOT}/config\nFLOCKS_DB_BACKEND=postgres\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOCKS_ROOT", "/review/live")
    monkeypatch.setenv("FLOCKS_DB_BACKEND", "")
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(startup_env, "_loaded", False)
    monkeypatch.setattr(startup_env, "_path", None)
    monkeypatch.setattr(startup_env, "dotenv_path", lambda package=None: tmp_path / ".env")

    assert startup_env.load() == tmp_path / ".env"

    assert os.environ["FLOCKS_ROOT"] == "/review/live"
    assert os.environ["FLOCKS_CONFIG_DIR"] == "/review/live/config"
    assert os.environ["FLOCKS_DB_BACKEND"] == ""
    assert startup_env.load() == tmp_path / ".env"


def test_a_backend_chosen_in_the_dotenv_is_the_one_installed(tmp_path):
    """flocks loads the .env beside its source tree before choosing, as the CLI and the server would see it."""
    import shutil

    site = tmp_path / "site"
    _install_backend(site, "standin")
    release = tmp_path / "release"
    shutil.copytree(REPO_ROOT / "flocks", release / "flocks", ignore=shutil.ignore_patterns("__pycache__"))
    (release / ".env").write_text("FLOCKS_DB_BACKEND=standin\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FLOCKS_")}
    env.update({"HOME": str(home), "FLOCKS_ROOT": str(tmp_path / "root"), "PYTHONPATH": os.pathsep.join([str(site), str(release)])})

    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(PROBE)], capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["kind"] == "stand-in"
    assert seen["describe"]["source"] == "FLOCKS_DB_BACKEND"


def test_a_dotenv_in_the_working_directory_is_not_read(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    (tmp_path / ".env").write_text("FLOCKS_DB_BACKEND=standin\n", encoding="utf-8")

    result = _run(tmp_path, site, {}, PROBE)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])["kind"] == "runtime"


def test_a_replacing_package_may_import_flocks_while_it_loads(tmp_path):
    """Its parent package runs when the targets are checked; what it imports from flocks is already replaced."""
    site = tmp_path / "site"
    _install_backend(site, "standin")
    (site / "stand_in_backend" / "mods" / "__init__.py").write_text("import flocks.coordination  # noqa: F401\n", encoding="utf-8")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, PROBE + "    import flocks.coordination as c\n    print(c.__name__)\n")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert json.loads(lines[-2])["kind"] == "stand-in"
    assert lines[-1] == "stand_in_backend.mods.coordination"


def test_python_dash_m_runs_the_replacing_module(tmp_path):
    """The Linux installer runs `python -m flocks.db.migrate`; with the backend in place that is its migrate."""
    site = tmp_path / "site"
    _install_backend(site, "standin")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("FLOCKS_")}
    env.update({
        "HOME": str(home), "FLOCKS_ROOT": str(tmp_path / "root"), "FLOCKS_DB_BACKEND": "standin",
        "PYTHONPATH": os.pathsep.join([str(site), str(REPO_ROOT)]),
    })

    result = subprocess.run(
        [sys.executable, "-B", "-m", "flocks.db.migrate", "--help"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "stand-in migrate --help"


def test_a_backend_naming_a_module_it_does_not_ship_stops_the_process(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")
    (site / "stand_in_backend" / "mods" / "task__store.py").unlink()

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, "import flocks")

    assert result.returncode != 0
    assert "not installed" in result.stderr
    assert "flocks.task.store" in result.stderr


def test_a_missing_submodule_of_a_replaced_package_is_simply_missing(tmp_path):
    site = tmp_path / "site"
    _install_backend(site, "standin")

    result = _run(tmp_path, site, {"FLOCKS_DB_BACKEND": "standin"}, """
        import importlib.util, flocks
        print(importlib.util.find_spec("flocks.db.connect") is not None, importlib.util.find_spec("flocks.db.nowhere"))
    """)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True None"


def _skip_if_coordination_is_replaced():
    """These check the runtime's own single-process answers; a configured backend brings its own."""
    from flocks import coordination

    if coordination.__name__ != "flocks.coordination":
        pytest.skip(f"flocks.coordination is replaced by {coordination.__name__}")


def test_coordination_answers_for_one_process():
    _skip_if_coordination_is_replaced()
    import asyncio

    from flocks import coordination

    assert asyncio.run(coordination.sessions_running_elsewhere()) == []
    assert coordination.page_api_database() is None
    assert coordination.workflow_services(lambda: False) == {}


def test_coordination_recognises_a_duplicate_key_on_either_database():
    import sqlite3

    from flocks import coordination

    class PgUnique(Exception):
        sqlstate = "23505"

    assert coordination.is_unique_violation(sqlite3.IntegrityError("UNIQUE constraint failed: groups.name"))
    assert coordination.is_unique_violation(PgUnique())
    assert not coordination.is_unique_violation(sqlite3.IntegrityError("NOT NULL constraint failed: groups.name"))
    assert not coordination.is_unique_violation(ValueError("UNIQUE"))


async def test_shared_setdefault_keeps_the_first_value(monkeypatch):
    _skip_if_coordination_is_replaced()
    from flocks import coordination
    from flocks.storage import storage as storage_mod

    stored: dict = {}

    async def fake_get(key, model=None):
        return stored.get(key)

    async def fake_set(key, value, value_type="json"):
        stored[key] = value

    monkeypatch.setattr(storage_mod.Storage, "get", fake_get)
    monkeypatch.setattr(storage_mod.Storage, "set", fake_set)
    monkeypatch.setattr(coordination, "_settled", {})

    assert await coordination.shared_setdefault("rea:salt", "first") == "first"
    assert await coordination.shared_setdefault("rea:salt", "second") == "first"


async def test_shared_setdefault_gives_concurrent_callers_one_value(monkeypatch):
    _skip_if_coordination_is_replaced()
    import asyncio

    from flocks import coordination
    from flocks.storage import storage as storage_mod

    stored: dict = {}

    async def slow_get(key, model=None):
        found = stored.get(key)  # what the store holds when asked; the answer arrives later
        await asyncio.sleep(0.01)
        return found

    async def fake_set(key, value, value_type="json"):
        stored[key] = value

    monkeypatch.setattr(storage_mod.Storage, "get", slow_get)
    monkeypatch.setattr(storage_mod.Storage, "set", fake_set)
    monkeypatch.setattr(coordination, "_settled", {})

    first, second = await asyncio.gather(
        coordination.shared_setdefault("rea:salt", "first"), coordination.shared_setdefault("rea:salt", "second"),
    )

    assert first == second == stored["rea:salt"]


def test_shared_setdefault_gives_callers_on_different_event_loops_one_value(monkeypatch):
    _skip_if_coordination_is_replaced()
    import asyncio
    import threading

    from flocks import coordination
    from flocks.storage import storage as storage_mod

    stored: dict = {}
    both_read = threading.Barrier(2)

    async def slow_get(key, model=None):
        found = stored.get(key)
        await asyncio.to_thread(both_read.wait)  # both threads have read before either stores
        return found

    async def fake_set(key, value, value_type="json"):
        stored[key] = value

    monkeypatch.setattr(storage_mod.Storage, "get", slow_get)
    monkeypatch.setattr(storage_mod.Storage, "set", fake_set)
    monkeypatch.setattr(coordination, "_settled", {})
    results: dict = {}

    def call(value):
        results[value] = asyncio.run(coordination.shared_setdefault("rea:salt:threads", value))

    threads = [threading.Thread(target=call, args=(value,)) for value in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results["first"] == results["second"] == stored["rea:salt:threads"]

