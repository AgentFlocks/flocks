"""Secrets referenced from flocks.json stay out of sight and off disk.

arch_v2 rules on top of dev's: the generic config endpoint writes for admins
only, and nobody, admins included, reads a resolved secret from it: admins
read the references written out, members a masked view.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.config.references import REDACTED

SECRET = "sk-real-secret-value-123"
INLINE = "Bearer inline-token-456"


def _client(role: str) -> TestClient:
    from flocks.server.routes.config import router

    app = FastAPI()

    @app.middleware("http")
    async def as_user(request, call_next):
        request.state.auth_user = AuthUser(
            id=f"{role}-test", username=f"{role}-test", role=role, status="active", must_reset_password=False,
        )
        return await call_next(request)

    app.include_router(router, prefix="/api/config")
    return TestClient(app, raise_server_exceptions=True)


def _raw() -> dict:
    from flocks.config.config_writer import ConfigWriter

    return ConfigWriter._read_raw()


@pytest.fixture
def stored_secret():
    """flocks.json references a secret in .secret.json and holds one inline credential."""
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter
    from flocks.security import get_secret_manager

    get_secret_manager().set("p_llm_key", SECRET)
    ConfigWriter._write_raw({
        "provider": {"p": {"options": {"apiKey": "{secret:p_llm_key}", "baseURL": "https://gw.example.test/v1"}}},
        "mcp": {"m": {"type": "remote", "url": "https://mcp.example.test", "headers": {"Authorization": INLINE}}},
        "theme": "dark",
    })
    Config.clear_cache()
    yield
    Config.clear_cache()


def test_members_read_references_and_masks_instead_of_secrets(stored_secret):
    member = _client("member").get("/api/config")
    assert member.status_code == 200
    assert SECRET not in member.text and INLINE not in member.text
    body = member.json()
    # A value from a stored reference is masked whole; members do not need the reference name.
    assert body["provider"]["p"]["options"]["apiKey"] == REDACTED
    assert body["mcp"]["m"]["headers"]["Authorization"] == REDACTED
    assert body["provider"]["p"]["options"]["baseURL"] == "https://gw.example.test/v1"

    # Admins (and the TUI's API token) read the reference itself, never its value;
    # a value stored inline in flocks.json reads as stored.
    admin = _client("admin").get("/api/config")
    assert SECRET not in admin.text
    assert admin.json()["provider"]["p"]["options"]["apiKey"] == "{secret:p_llm_key}"
    assert admin.json()["mcp"]["m"]["headers"]["Authorization"] == INLINE


def test_saving_does_not_write_expanded_secrets_back(stored_secret):
    admin = _client("admin")
    response = admin.patch("/api/config/", json={"toolFailure": {"disableOnRepeatedFailure": False}})
    assert response.status_code == 200
    assert SECRET not in response.text
    assert _raw()["provider"]["p"]["options"]["apiKey"] == "{secret:p_llm_key}"

    # The Channel page sends back the whole config it read.
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert _raw()["provider"]["p"]["options"]["apiKey"] == "{secret:p_llm_key}"

    # Generic config writes are admin-only in v2: a member's echo is refused, nothing is written.
    member = _client("member")
    before = _raw()
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403
    raw = _raw()
    assert raw == before
    assert raw["mcp"]["m"]["headers"]["Authorization"] == INLINE
    assert REDACTED not in json.dumps(raw)


def test_admin_changing_a_secret_still_saves_it(stored_secret):
    admin = _client("admin")
    response = admin.patch("/api/config/", json={"provider": {"p": {"options": {"apiKey": "{secret:other_key}"}}}})
    assert response.status_code == 200
    assert _raw()["provider"]["p"]["options"]["apiKey"] == "{secret:other_key}"


@pytest.mark.parametrize(
    "payload",
    [
        {"theme": "{secret:p_llm_key}"},
        {"channels": {"feishu": {"appId": "{env:HOME}"}}},
        {"instructions": ["{file:/etc/hosts}"]},
        {"{secret:p_llm_key}": "x"},
    ],
)
def test_members_cannot_write_references(stored_secret, payload):
    before = _raw()
    assert _client("member").patch("/api/config/", json=payload).status_code == 403
    assert _raw() == before


def test_display_name_never_carries_a_reference(stored_secret):
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    # Members may not change the display name at all; an admin may not give it a reference.
    assert _client("member").patch("/api/config/ui", json={"displayName": "{secret:p_llm_key}"}).status_code == 403
    assert _client("admin").patch("/api/config/ui", json={"displayName": "{secret:p_llm_key}"}).status_code == 400
    assert "ui" not in _raw()

    # One stored before this check is not shown on the public login page either.
    raw = _raw()
    raw["ui"] = {"displayName": "{secret:p_llm_key}"}
    ConfigWriter._write_raw(raw)
    Config.clear_cache()
    public = _client("member").get("/api/config/ui-display")
    assert public.status_code == 200
    assert SECRET not in public.text
    assert public.json()["configuredDisplayName"] is None

    ok = _client("admin").patch("/api/config/ui", json={"displayName": "Acme SOC"})
    assert ok.status_code == 200 and ok.json()["displayName"] == "Acme SOC"


def _config_dir():
    from flocks.config.config import Config

    return Config.get_config_path()


def test_references_are_found_whatever_the_key_spelling_or_typed_neighbours(monkeypatch):
    # A typed field holding a reference (port) must not stop the others from being recognised.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_PORT", "8123")
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    ConfigWriter._write_raw({
        "server": {"port": "{env:FLOCKS_TEST_PORT}"},
        "provider": {"p": {"options": {"api_key": "{env:FLOCKS_TEST_KEY}"}}},
        "agent": {"demo": {"prompt_append": "{env:FLOCKS_TEST_KEY}"}},
    })
    Config.clear_cache()

    member = _client("member")
    read = member.get("/api/config")
    assert read.status_code == 200 and SECRET not in read.text
    # The typed port cannot be read unexpanded, so admins get the masked view too.
    admin = _client("admin")
    assert SECRET not in admin.get("/api/config").text
    assert admin.patch("/api/config/", json={"toolFailure": {"disableOnRepeatedFailure": False}}).status_code == 200
    assert SECRET not in json.dumps(_raw())


def test_list_references_survive_merging_across_layers():
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter
    from flocks.security import get_secret_manager

    get_secret_manager().set("note_secret", SECRET)
    (_config_dir() / "note.md").write_text(SECRET, encoding="utf-8")
    ConfigWriter._write_raw({"instructions": ["{file:note.md}", "plain.md"]})
    (_config_dir() / "config.json").write_text(json.dumps({"instructions": ["other.md"]}), encoding="utf-8")
    Config.clear_cache()

    assert SECRET not in _client("member").get("/api/config").text
    admin = _client("admin")
    echoed = admin.get("/api/config")
    assert SECRET not in echoed.text
    assert admin.patch("/api/config/", json=echoed.json()).status_code == 200
    assert "{file:note.md}" in _raw()["instructions"]
    assert SECRET not in json.dumps(_raw())


def test_echoing_a_higher_layer_secret_never_writes_its_plaintext(stored_secret):
    from flocks.config.config import Config
    from flocks.security import get_secret_manager

    get_secret_manager().set("p_high_key", "sk-higher-layer-value-789")
    (_config_dir() / "config.json").write_text(
        json.dumps({"provider": {"p": {"options": {"apiKey": "{secret:p_high_key}"}}}}), encoding="utf-8",
    )
    Config.clear_cache()

    member = _client("member")
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403
    admin = _client("admin")
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    on_disk = json.dumps(_raw())
    assert "sk-higher-layer-value-789" not in on_disk and SECRET not in on_disk
    # The higher layer's reference is not copied into flocks.json either.
    assert _raw()["provider"]["p"]["options"]["apiKey"] == "{secret:p_llm_key}"


def test_channel_env_reference_is_kept_through_an_echo(monkeypatch):
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_FEISHU_SECRET", SECRET)
    ConfigWriter._write_raw({"channels": {"feishu": {"enabled": False, "appId": "cli_x", "appSecret": "{env:FLOCKS_TEST_FEISHU_SECRET}"}}})
    Config.clear_cache()

    member = _client("member")
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403
    admin = _client("admin")
    assert SECRET not in admin.get("/api/config").text
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert _raw()["channels"]["feishu"]["appSecret"] == "{env:FLOCKS_TEST_FEISHU_SECRET}"


def test_mcp_credentials_in_urls_and_auth_are_masked_and_kept():
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    server = {
        "type": "remote",
        "url": f"https://mcp.example.test/sse?apikey={SECRET}",
        "auth": {"type": "bearer", "value": INLINE},
    }
    ConfigWriter._write_raw({"mcp": {"m": server}})
    Config.clear_cache()

    member = _client("member")
    read = member.get("/api/config")
    assert SECRET not in read.text and INLINE not in read.text
    assert member.patch("/api/config/", json=read.json()).status_code == 403
    admin = _client("admin")
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert _raw()["mcp"]["m"]["url"] == server["url"]
    assert _raw()["mcp"]["m"]["auth"]["value"] == INLINE


def test_display_name_from_a_reference_is_hidden_in_any_layer(stored_secret):
    from flocks.config.config import Config

    # flocks.jsonc wins for ConfigWriter but holds no UI; the reference sits in flocks.json.
    (_config_dir() / "flocks.jsonc").write_text("{}", encoding="utf-8")
    raw = json.loads((_config_dir() / "flocks.json").read_text(encoding="utf-8"))
    raw["ui"] = {"displayName": "{secret:p_llm_key}"}
    (_config_dir() / "flocks.json").write_text(json.dumps(raw), encoding="utf-8")
    Config.clear_cache()

    public = _client("member").get("/api/config/ui-display")
    assert public.status_code == 200 and SECRET not in public.text


def test_the_cached_config_is_masked_with_its_own_references(stored_secret):
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    # An admin put a reference under a key that does not look like a credential,
    # so only knowing where the value came from keeps it hidden.
    ConfigWriter._write_raw({**_raw(), "theme": "{secret:p_llm_key}"})
    Config.clear_cache()
    member = _client("member")
    assert SECRET not in member.get("/api/config").text
    # Another worker rewrites the file without the reference (this worker's cache is
    # not told); the cached config here still holds the secret.
    (_config_dir() / "flocks.json").write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert Config._cached_config is not None
    assert SECRET not in member.get("/api/config").text
    Config.clear_cache()


def test_a_stored_display_name_reference_is_hidden_even_after_normalising(monkeypatch):
    # The model strips the display name; the check must not depend on matching the value.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_NAME", f"  {SECRET}  ")
    ConfigWriter._write_raw({"ui": {"displayName": "{env:FLOCKS_TEST_NAME}"}})
    Config.clear_cache()

    public = _client("member").get("/api/config/ui-display")
    assert public.status_code == 200 and SECRET not in public.text


def test_a_literal_field_held_in_a_reference_saves_as_before(monkeypatch):
    # Nothing is put back into the payload before validation, so a Literal field
    # stored as {env:…} does not fail the save, and the reference stays.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_MODE", "primary")
    monkeypatch.setenv("FLOCKS_TEST_PORT", "8123")
    ConfigWriter._write_raw({
        "agent": {"demo": {"mode": "{env:FLOCKS_TEST_MODE}"}},
        "server": {"port": "{env:FLOCKS_TEST_PORT}"},
    })
    Config.clear_cache()

    member = _client("member")
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403
    admin = _client("admin")
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    raw = _raw()
    assert raw["agent"]["demo"]["mode"] == "{env:FLOCKS_TEST_MODE}"
    assert raw["server"]["port"] == "{env:FLOCKS_TEST_PORT}"


def test_equal_values_from_different_references_keep_their_own(monkeypatch):
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_KEY_A", SECRET)
    monkeypatch.setenv("FLOCKS_TEST_KEY_B", SECRET)
    ConfigWriter._write_raw({
        "provider": {
            "a": {"options": {"apiKey": "{env:FLOCKS_TEST_KEY_A}"}},
            "b": {"options": {"apiKey": "{env:FLOCKS_TEST_KEY_B}"}},
        },
        "theme": "dark",
    })
    Config.clear_cache()

    admin = _client("admin")
    assert admin.patch("/api/config/", json={"theme": "light"}).status_code == 200
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    raw = _raw()
    assert raw["provider"]["a"]["options"]["apiKey"] == "{env:FLOCKS_TEST_KEY_A}"
    assert raw["provider"]["b"]["options"]["apiKey"] == "{env:FLOCKS_TEST_KEY_B}"
    assert raw["theme"] == "light"


def test_a_value_mixing_a_reference_and_inline_secret_is_masked_whole(monkeypatch):
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setenv("FLOCKS_TEST_HOST", "mcp.example.test")
    url = f"https://{{env:FLOCKS_TEST_HOST}}/sse?apikey={SECRET}"
    ConfigWriter._write_raw({"mcp": {"m": {"type": "remote", "url": url}}})
    Config.clear_cache()

    member = _client("member")
    read = member.get("/api/config")
    assert SECRET not in read.text and "{env:FLOCKS_TEST_HOST}" not in read.text
    assert member.patch("/api/config/", json=read.json()).status_code == 403
    admin = _client("admin")
    # Admins read the url as stored: its reference written out, the inline key as written.
    assert admin.get("/api/config").json()["mcp"]["m"]["url"] == url
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert _raw()["mcp"]["m"]["url"] == url


def test_other_layers_are_not_copied_into_flocks_json(tmp_path, monkeypatch):
    # A relative {file:…} in a custom config file only resolves next to that file.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    custom_dir = tmp_path / "external"
    custom_dir.mkdir()
    (custom_dir / "key.txt").write_text(SECRET, encoding="utf-8")
    (custom_dir / "config.json").write_text(
        json.dumps({"provider": {"p": {"options": {"apiKey": "{file:key.txt}"}}}}), encoding="utf-8",
    )
    monkeypatch.setenv("FLOCKS_CONFIG_PATH", str(custom_dir / "config.json"))
    Config._global_config = None
    ConfigWriter._write_raw({"theme": "dark"})
    Config.clear_cache()
    assert asyncio.run(Config.get()).provider["p"].options.api_key == SECRET

    member = _client("member")
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403
    admin = _client("admin")
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    raw = _raw()
    assert "provider" not in raw, raw
    assert SECRET not in json.dumps(raw)
    Config.clear_cache()
    assert asyncio.run(Config.get()).provider["p"].options.api_key == SECRET
    Config._global_config = None


def test_unquoted_templates_do_not_switch_the_protection_off(monkeypatch):
    # The loader accepts "port": {env:PORT}; the stored file must still be read unexpanded.
    from flocks.config.config import Config

    monkeypatch.setenv("FLOCKS_TEST_PORT", "8123")
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    (_config_dir() / "flocks.json").write_text(
        '{"server": {"port": {env:FLOCKS_TEST_PORT}}, '
        '"provider": {"p": {"options": {"apiKey": "{env:FLOCKS_TEST_KEY}"}}}}',
        encoding="utf-8",
    )
    Config.clear_cache()

    member = _client("member")
    assert SECRET not in member.get("/api/config").text
    admin = _client("admin")
    assert SECRET not in admin.get("/api/config").text
    assert admin.patch("/api/config/", json={"toolFailure": {"disableOnRepeatedFailure": False}}).status_code == 200
    stored = (_config_dir() / "flocks.json").read_text(encoding="utf-8")
    assert SECRET not in stored and "{env:FLOCKS_TEST_KEY}" in stored
    # Written back the way it was stored: outside quotes, so it still expands to a number.
    assert '"port": {env:FLOCKS_TEST_PORT}' in stored
    assert asyncio.run(Config.get()).server.port == 8123


def _write_config(name: str, data: dict) -> None:
    from flocks.config.config import Config

    (_config_dir() / name).write_text(json.dumps(data), encoding="utf-8")
    Config.clear_cache()


def test_a_reference_under_the_deprecated_mode_stays_hidden_and_stored(monkeypatch):
    # The model copies mode entries into agent; the copy must be hidden and never saved in plaintext.
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    _write_config("flocks.json", {"mode": {"demo": {"prompt_append": "{env:FLOCKS_TEST_KEY}"}}, "theme": "dark"})

    member = _client("member")
    read = member.get("/api/config")
    assert read.status_code == 200 and SECRET not in read.text
    assert member.patch("/api/config/", json=member.get("/api/config").json()).status_code == 403

    admin = _client("admin")
    assert SECRET not in admin.get("/api/config").text
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert admin.patch("/api/config/", json={"agent": {"demo": {"temperature": 0.2}}}).status_code == 200
    raw = _raw()
    assert SECRET not in json.dumps(raw)
    assert raw["mode"] == {"demo": {"prompt_append": "{env:FLOCKS_TEST_KEY}"}}
    assert raw["agent"]["demo"]["prompt_append"] == "{env:FLOCKS_TEST_KEY}"
    assert raw["agent"]["demo"]["temperature"] == 0.2
    assert SECRET not in _client("member").get("/api/config").text


@pytest.mark.parametrize("alias_first", [True, False])
def test_two_spellings_of_the_display_name_never_publish_a_reference(monkeypatch, alias_first):
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    ui = {"displayName": "{env:FLOCKS_TEST_KEY}", "display_name": "Literal"}
    if not alias_first:
        ui = dict(reversed(list(ui.items())))
    _write_config("flocks.json", {"ui": ui})

    public = _client("member").get("/api/config/ui-display")
    assert public.status_code == 200 and SECRET not in public.text
    assert SECRET not in _client("member").get("/api/config").text


def test_changing_a_list_from_another_file_keeps_its_reference(monkeypatch):
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    _write_config("flocks.json", {"theme": "dark"})
    _write_config("config.json", {"mcp": {"runner": {"type": "local", "command": ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]}}})
    before = _raw()

    member = _client("member")
    body = member.get("/api/config").json()
    body["mcp"]["runner"]["command"].append("--verbose")
    assert member.patch("/api/config/", json=body).status_code == 403
    assert _raw() == before

    # Admins read the reference itself, so the changed list they send back still
    # holds the reference, not its value: it is saved as written (dev, which shows
    # admins the value, refuses this save to keep the value off disk).
    admin = _client("admin")
    body = admin.get("/api/config").json()
    body["mcp"]["runner"]["command"].append("--verbose")
    saved = admin.patch("/api/config/", json=body)
    assert saved.status_code == 200, saved.text
    assert SECRET not in saved.text
    assert _raw()["mcp"]["runner"]["command"] == ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}", "--verbose"]
    assert SECRET not in json.dumps(_raw())

    # An admin can also write the list with the reference directly.
    command = ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}", "--debug"]
    assert admin.patch("/api/config/", json={"mcp": {"runner": {"type": "local", "command": command}}}).status_code == 200
    assert _raw()["mcp"]["runner"]["command"] == command


def test_a_list_in_flocks_json_keeps_its_references_when_items_are_added(monkeypatch):
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    _write_config("flocks.json", {"mcp": {"runner": {"type": "local", "command": ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]}}})

    member = _client("member")
    body = member.get("/api/config").json()
    body["mcp"]["runner"]["command"].append("--verbose")
    assert member.patch("/api/config/", json=body).status_code == 403

    admin = _client("admin")
    body = admin.get("/api/config").json()
    assert body["mcp"]["runner"]["command"] == ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]
    body["mcp"]["runner"]["command"].append("--debug")
    assert admin.patch("/api/config/", json=body).status_code == 200
    assert _raw()["mcp"]["runner"]["command"] == ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}", "--debug"]


def test_equal_references_in_one_list_are_never_swapped(monkeypatch):
    monkeypatch.setenv("FLOCKS_TEST_KEY_A", SECRET)
    monkeypatch.setenv("FLOCKS_TEST_KEY_B", SECRET)
    command = ["runner", "--primary", "{env:FLOCKS_TEST_KEY_A}", "--secondary", "{env:FLOCKS_TEST_KEY_B}"]
    _write_config("flocks.json", {"mcp": {"runner": {"type": "local", "command": command}}, "theme": "dark"})

    admin = _client("admin")
    assert admin.patch("/api/config/", json={"theme": "admin-theme"}).status_code == 200
    assert admin.patch("/api/config/", json=admin.get("/api/config").json()).status_code == 200
    assert _raw()["mcp"]["runner"]["command"] == command

    # Admins see the references themselves, so a moved one is still told apart:
    # each keeps its own flag (dev refuses this save, having shown two equal values).
    body = admin.get("/api/config").json()
    body["mcp"]["runner"]["command"].insert(0, "--first")
    saved = admin.patch("/api/config/", json=body)
    assert saved.status_code == 200 and SECRET not in saved.text
    assert _raw()["mcp"]["runner"]["command"] == ["--first", *command]


def test_a_member_cannot_save_a_mask_where_it_was_not_shown(monkeypatch):
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    _write_config("flocks.json", {"mcp": {"runner": {"type": "local", "command": ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]}}})
    before = _raw()

    member = _client("member")
    body = member.get("/api/config").json()
    body["mcp"]["runner"]["command"].insert(0, "--first")
    response = member.patch("/api/config/", json=body)
    assert response.status_code == 403
    assert _raw() == before


def test_an_admin_cannot_save_a_mask_where_it_was_not_shown(monkeypatch):
    # A typed field holding a reference cannot be read unexpanded, so admins get
    # the masked view; a mask that moved must not be written over the value.
    monkeypatch.setenv("FLOCKS_TEST_KEY", SECRET)
    monkeypatch.setenv("FLOCKS_TEST_PORT", "8123")
    _write_config("flocks.json", {
        "server": {"port": "{env:FLOCKS_TEST_PORT}"},
        "mcp": {"runner": {"type": "local", "command": ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]}},
    })
    before = _raw()

    admin = _client("admin")
    body = admin.get("/api/config").json()
    assert body["mcp"]["runner"]["command"][2] == REDACTED and SECRET not in json.dumps(body)
    body["mcp"]["runner"]["command"].insert(0, "--first")
    response = admin.patch("/api/config/", json=body)
    assert response.status_code == 400, response.text
    assert _raw() == before

    # Sent back where it was shown, the mask keeps the stored value.
    body = admin.get("/api/config").json()
    body["theme"] = "dark"
    assert admin.patch("/api/config/", json=body).status_code == 200
    assert _raw()["mcp"]["runner"]["command"] == ["runner", "--api-key", "{env:FLOCKS_TEST_KEY}"]
    assert _raw()["theme"] == "dark"
