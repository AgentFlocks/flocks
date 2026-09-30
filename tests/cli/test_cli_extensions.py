"""Installed packages may add CLI command groups through an entry point."""

from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from flocks.cli import main as cli_main


class _EntryPoint(SimpleNamespace):
    def load(self):
        return self.target


def _app_with(monkeypatch, *entry_points):
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda group: list(entry_points) if group == cli_main.CLI_EXTENSION_ENTRY_POINT_GROUP else [],
    )
    app = typer.Typer()

    @app.command(name="builtin")
    def builtin() -> None:
        typer.echo("builtin")

    @app.command(name="other")
    def other() -> None:
        typer.echo("other")

    cli_main._register_extension_commands(app)
    return app


def _extension_app(message: str) -> typer.Typer:
    extension = typer.Typer()

    @extension.command(name="hello")
    def hello() -> None:
        typer.echo(message)

    return extension


def test_extension_group_is_added(monkeypatch):
    app = _app_with(monkeypatch, _EntryPoint(name="extra", value="pkg:app", target=_extension_app("hi")))

    result = CliRunner().invoke(app, ["extra", "hello"])

    assert result.exit_code == 0, result.output
    assert "hi" in result.output


def test_factory_returning_typer_is_accepted(monkeypatch):
    app = _app_with(monkeypatch, _EntryPoint(name="extra", value="pkg:make", target=lambda: _extension_app("made")))

    result = CliRunner().invoke(app, ["extra", "hello"])

    assert result.exit_code == 0, result.output
    assert "made" in result.output


def test_extension_cannot_shadow_builtin_command(monkeypatch):
    app = _app_with(monkeypatch, _EntryPoint(name="builtin", value="pkg:app", target=_extension_app("shadow")))

    result = CliRunner().invoke(app, ["builtin"])

    assert result.exit_code == 0, result.output
    assert "builtin" in result.output
    assert "shadow" not in result.output


def test_broken_extension_is_skipped(monkeypatch, capsys):
    def boom():
        raise RuntimeError("cannot import")

    app = _app_with(
        monkeypatch,
        _EntryPoint(name="broken", value="pkg:boom", target=boom),
        _EntryPoint(name="extra", value="pkg:app", target=_extension_app("still works")),
    )

    assert "skipped CLI extension 'broken'" in capsys.readouterr().err
    result = CliRunner().invoke(app, ["extra", "hello"])
    assert "still works" in result.output


def test_should_load_only_for_relevant_invocations(monkeypatch):
    app = typer.Typer()

    # `@app.command()` with no explicit name: Typer derives "status" / the
    # hyphenated "service-daemon" from the function, which the check must match.
    @app.command()
    def status() -> None:
        pass

    @app.command()
    def service_daemon() -> None:
        pass

    for argv, expected in [
        (["flocks", "status"], False),
        (["flocks", "status", "--json"], False),
        (["flocks", "service-daemon"], False),
        (["flocks", "--log-level", "DEBUG", "status"], False),
        (["flocks", "--log-level=DEBUG", "status"], False),
        (["flocks", "security", "audit"], True),
        (["flocks", "--log-level", "DEBUG", "security"], True),
        (["flocks", "--help"], True),
        (["flocks"], True),
    ]:
        monkeypatch.setattr(cli_main.sys, "argv", argv)
        assert cli_main._should_load_cli_extensions(app) is expected, argv


def test_should_load_for_shell_completion(monkeypatch):
    app = typer.Typer()

    @app.command(name="status")
    def status() -> None:
        pass

    monkeypatch.setattr(cli_main.sys, "argv", ["flocks", "status"])
    monkeypatch.setenv("_FLOCKS_COMPLETE", "complete_zsh")
    assert cli_main._should_load_cli_extensions(app) is True
