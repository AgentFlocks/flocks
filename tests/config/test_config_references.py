"""Keeping {env:…}/{secret:…}/{file:…} references out of view and on disk."""

from __future__ import annotations

import copy

import json

import pytest

from flocks.config.references import (
    REDACTED,
    ChangeRefused,
    ReferencePlaces,
    StoredLayer,
    apply_changes,
    contains_reference,
    drop_unchanged,
    is_secret_key,
    leftover_masks,
    member_view,
    quote_bare_references,
    restore_hidden,
    schema_normalize,
    strip_bare_marks,
    write_unexpanded,
)


@pytest.mark.parametrize(
    "name",
    ["apiKey", "api_key", "OPENAI_API_KEY", "X-API-Key", "Authorization", "botToken", "appSecret",
     "clientSecret", "password", "encryptKey", "privateKey", "key", "apikey", "GITHUB_TOKEN", "accessKeySecret"],
)
def test_credential_keys_are_recognised(name):
    assert is_secret_key(name)


@pytest.mark.parametrize(
    "name",
    ["model", "baseURL", "maxTokens", "max_tokens", "tokenPolicyNotice", "secretId", "keyFile", "publicKey",
     "accessKeyId", "tokenUrl", "displayName", "name", 0],
)
def test_ordinary_keys_are_left_alone(name):
    assert not is_secret_key(name)


def test_contains_reference_looks_at_keys_and_values():
    assert contains_reference({"ui": {"displayName": "{secret:server_api_token}"}})
    assert contains_reference({"agent": {"rex": {"{env:HOME}": "x"}}})
    assert contains_reference(["a", {"b": ["{file:/etc/passwd}"]}])
    assert not contains_reference({"theme": "dark", "note": "{not a reference}", "n": 3})


def test_quote_bare_references_only_touches_references_outside_strings():
    text = '{"a": {env:PORT}, "b": "x {env:Y} z", // {env:IN_COMMENT}\n "c": "\\"{secret:k}\\""}'
    quoted = quote_bare_references(text)
    assert '"a": "{env:PORT}"' in quoted
    assert '"b": "x {env:Y} z"' in quoted
    assert "// {env:IN_COMMENT}" in quoted
    assert '"c": "\\"{secret:k}\\""' in quoted


def test_references_outside_strings_are_written_back_outside_strings():
    text = '{"server": {"port": {env:PORT}}, "x": "{env:PORT}", "list": [{env:N}, "{env:N}"]}'
    data, bare = strip_bare_marks(json.loads(quote_bare_references(text, mark=True)))

    assert data == {"server": {"port": "{env:PORT}"}, "x": "{env:PORT}", "list": ["{env:N}", "{env:N}"]}
    assert bare == {("server", "port"): "{env:PORT}", ("list", 0): "{env:N}"}
    written = write_unexpanded({**data, "theme": "dark"}, bare)
    assert '"port": {env:PORT}' in written
    assert '"x": "{env:PORT}"' in written
    assert json.loads(quote_bare_references(written))["list"] == ["{env:N}", "{env:N}"]
    assert "{env:N},\n" in written and '"{env:N}"' in written

    # A bare reference replaced by the save is written as the new value.
    data["server"]["port"] = 9000
    assert '"port": 9000' in write_unexpanded(data, bare)


def test_schema_normalize_spells_keys_like_a_dump_without_validating_values():
    raw = {
        "small_model": "{env:SMALL}",
        "server": {"port": "{env:PORT}"},
        "provider": {"p": {"options": {"api_key": "{env:KEY}", "base_url": "https://x"}}},
        "mcp": {"local": {"type": "local", "command": ["npx"], "env": {"A": "{env:A}"}}},
        "unknownTop": {"keep_me": 1},
    }
    normalized = schema_normalize(raw)
    assert normalized["smallModel"] == "{env:SMALL}"
    assert normalized["server"]["port"] == "{env:PORT}"
    assert normalized["provider"]["p"]["options"] == {"apiKey": "{env:KEY}", "baseURL": "https://x"}
    assert normalized["mcp"]["local"]["environment"] == {"A": "{env:A}"}
    assert normalized["unknownTop"] == {"keep_me": 1}


@pytest.mark.parametrize("alias_first", [True, False])
def test_schema_normalize_reads_two_spellings_the_way_the_model_does(alias_first):
    from flocks.config.config import ConfigInfo

    ui = {"displayName": "{env:NAME}", "display_name": "Literal"}
    agent = {"maxSteps": "{env:STEPS}", "max_steps": 5}
    mcp = {"type": "local", "command": ["x"], "environment": {"K": "{env:K}"}, "env": {"K": "other"}}
    if not alias_first:
        ui, agent, mcp = (dict(reversed(list(part.items()))) for part in (ui, agent, mcp))
    raw = {"ui": ui, "agent": {"a": agent}, "mcp": {"s": mcp}}

    normalized = schema_normalize(raw)

    # Same winners as the model: the alias (or first alias choice) is read.
    assert normalized["ui"] == {"displayName": "{env:NAME}"}
    assert normalized["agent"]["a"]["maxSteps"] == "{env:STEPS}"
    assert normalized["mcp"]["s"]["environment"] == {"K": "{env:K}"}
    # Models that keep extras keep the losing spelling under its own key.
    assert normalized["agent"]["a"]["max_steps"] == 5
    assert normalized["mcp"]["s"]["env"] == {"K": "other"}
    dumped = ConfigInfo.model_validate({
        "ui": {"displayName": "N", "display_name": "Literal"},
        "agent": {"a": {"maxSteps": 3, "max_steps": 5}},
    }).model_dump(by_alias=True, exclude_none=True)
    assert dumped["ui"] == {"displayName": "N"}
    assert dumped["agent"]["a"]["maxSteps"] == 3 and dumped["agent"]["a"]["max_steps"] == 5


def test_drop_unchanged_keeps_only_what_differs():
    current = {"theme": "dark", "provider": {"p": {"options": {"apiKey": "sk", "baseURL": "u"}}}, "plugin": ["a"]}
    incoming = {"theme": "light", "provider": {"p": {"options": {"apiKey": "sk", "baseURL": "u2"}}}, "plugin": ["a"], "new": 1}
    assert drop_unchanged(incoming, current) == {"theme": "light", "provider": {"p": {"options": {"baseURL": "u2"}}}, "new": 1}
    assert drop_unchanged(current, current) == {}


def _apply(stored, changes, seen, loaded, own_values=None, others=None):
    return apply_changes(
        stored, changes, seen, loaded,
        own_values=own_values or {},
        others=others or (lambda path: set()),
    )


def test_apply_changes_writes_the_change_into_the_stored_file_and_nothing_else():
    stored = {
        "provider": {"p": {"options": {"api_key": "{secret:k}", "baseURL": "https://old"}}},
        "ui": {"display_name": "Old"},
        "theme": "dark",
    }
    loaded = {"provider": {"p": {"options": {"apiKey": "sk-real", "baseURL": "https://old"}}},
              "ui": {"displayName": "Old"}, "theme": "dark"}
    changes = {"provider": {"p": {"options": {"baseURL": "https://new"}}}, "ui": {"displayName": "New"}}

    written = _apply(stored, changes, loaded, loaded, {"{secret:k}": "sk-real"})

    assert written == {
        "provider": {"p": {"options": {"api_key": "{secret:k}", "baseURL": "https://new"}}},
        "ui": {"display_name": "New"},  # the stored spelling is kept
        "theme": "dark",
    }


def test_apply_changes_leaves_one_spelling_of_a_replaced_field():
    stored = {"ui": {"displayName": "{env:NAME}", "display_name": "Literal"}}
    written = _apply(stored, {"ui": {"displayName": "Acme"}}, {}, {})
    assert written == {"ui": {"displayName": "Acme"}}


def test_apply_changes_keeps_untyped_spellings_that_differ_in_case_or_underscores():
    stored = {"sandbox": {"docker": {"memory_mb": 512, "image": "x"}}}
    written = _apply(stored, {"sandbox": {"docker": {"memoryMb": 1024}}}, {}, {})
    assert written == {"sandbox": {"docker": {"memory_mb": 1024, "image": "x"}}}


def test_a_changed_list_keeps_stored_items_wherever_they_moved():
    stored = {"mcp": {"m": {"type": "local", "command": ["runner", "--key", "{env:KEY}", "--host", "{env:HOST}"]}}}
    loaded_command = ["runner", "--key", "sk-env-key", "--host", "h.example.test"]
    loaded = {"mcp": {"m": {"type": "local", "command": loaded_command}}}
    values = {"{env:KEY}": "sk-env-key", "{env:HOST}": "h.example.test"}

    appended = _apply(stored, {"mcp": {"m": {"command": loaded_command + ["--verbose"]}}}, loaded, loaded, values)
    assert appended["mcp"]["m"]["command"] == ["runner", "--key", "{env:KEY}", "--host", "{env:HOST}", "--verbose"]

    moved = ["--verbose", "--host", "h.example.test", "runner", "--key", "sk-env-key"]
    reordered = _apply(stored, {"mcp": {"m": {"command": moved}}}, loaded, loaded, values)
    assert reordered["mcp"]["m"]["command"] == ["--verbose", "--host", "{env:HOST}", "runner", "--key", "{env:KEY}"]


def test_two_references_with_the_same_value_in_a_changed_list_refuse_the_save():
    stored = {"mcp": {"m": {"command": ["--primary", "{env:A}", "--secondary", "{env:B}"]}}}
    loaded_command = ["--primary", "same-value", "--secondary", "same-value"]
    loaded = {"mcp": {"m": {"command": loaded_command}}}
    values = {"{env:A}": "same-value", "{env:B}": "same-value"}

    # Untouched, the list is not part of the change and stays as stored.
    unchanged = _apply(stored, {"theme": "light"}, loaded, loaded, values)
    assert unchanged["mcp"]["m"]["command"] == ["--primary", "{env:A}", "--secondary", "{env:B}"]

    with pytest.raises(ChangeRefused) as refused:
        _apply(stored, {"mcp": {"m": {"command": ["--x"] + loaded_command}}}, loaded, loaded, values)
    assert refused.value.path == ("mcp", "m", "command", 2)
    assert "same-value" not in str(refused.value)


def test_a_list_item_carrying_another_files_reference_refuses_the_save():
    seen = {"mcp": {"m": {"command": ["runner", "--api-key", "sk-other-layer"]}}}
    others = {("mcp", "m", "command"): {"sk-other-layer"}}

    with pytest.raises(ChangeRefused) as refused:
        _apply({}, {"mcp": {"m": {"command": ["runner", "--api-key", "sk-other-layer", "--verbose"]}}},
               seen, {}, others=lambda path: others.get(path, set()))
    assert refused.value.path == ("mcp", "m", "command", 2)

    # Writing the reference itself is fine.
    written = _apply({}, {"mcp": {"m": {"command": ["runner", "--api-key", "{env:KEY}", "--verbose"]}}},
                     seen, {}, others=lambda path: others.get(path, set()))
    assert written["mcp"]["m"]["command"][2] == "{env:KEY}"


def test_an_edited_value_still_holding_a_reference_value_refuses_the_save():
    stored = {"mcp": {"m": {"type": "remote", "url": "https://x.test/sse?apikey={env:KEY}"}}}
    loaded = {"mcp": {"m": {"type": "remote", "url": "https://x.test/sse?apikey=sk-env-key"}}}
    values = {"{env:KEY}": "sk-env-key"}

    for url in ("https://x.test/v2/sse?apikey=sk-env-key", "https://{env:HOST}/sse?apikey=sk-env-key"):
        with pytest.raises(ChangeRefused):
            _apply(stored, {"mcp": {"m": {"url": url}}}, loaded, loaded, values)

    written = _apply(stored, {"mcp": {"m": {"url": "https://x.test/v2/sse?apikey={env:KEY}"}}}, loaded, loaded, values)
    assert written["mcp"]["m"]["url"] == "https://x.test/v2/sse?apikey={env:KEY}"


def test_a_partly_changed_object_in_a_list_keeps_its_references():
    stored = {"fallback_providers": [{"provider_id": "{env:PROVIDER}", "model_id": "m1"}]}
    loaded = {"fallback_providers": [{"provider_id": "p-from-env", "model_id": "m1"}]}
    changes = {"fallback_providers": [{"provider_id": "p-from-env", "model_id": "m2"}]}

    written = _apply(stored, changes, loaded, loaded, {"{env:PROVIDER}": "p-from-env"})

    assert written["fallback_providers"] == [{"provider_id": "{env:PROVIDER}", "model_id": "m2"}]


def test_removing_an_object_from_a_list_does_not_carry_its_fields_to_the_next():
    # The second field must not become the first one's secret field.
    first = {"key": "api_key", "storage": "secret", "secret_id": "custom_api_api_key", "sensitive": True}
    second = {"key": "base_url"}
    stored = {"api_services": {"custom_api": {"credential_fields": [first, second]}}}
    loaded = copy.deepcopy(stored)

    written = _apply(stored, {"api_services": {"custom_api": {"credential_fields": [second]}}}, loaded, loaded)

    assert written["api_services"]["custom_api"]["credential_fields"] == [second]


def test_reordered_objects_keep_their_own_references():
    stored = {"fallback_providers": [
        {"provider_id": "{env:PRIMARY}", "model_id": "m1"},
        {"provider_id": "openai", "model_id": "m2"},
    ]}
    loaded = {"fallback_providers": [
        {"provider_id": "openai", "model_id": "m1"},
        {"provider_id": "openai", "model_id": "m2"},
    ]}
    values = {"{env:PRIMARY}": "openai"}

    # Sent back as an admin reads them (references written), in the other order.
    swapped = [{"provider_id": "openai", "model_id": "m2"}, {"provider_id": "{env:PRIMARY}", "model_id": "m1"}]
    written = _apply(stored, {"fallback_providers": swapped}, loaded, loaded, values)

    assert written["fallback_providers"] == swapped


def test_an_edited_object_that_moved_is_written_as_given():
    first = {"key": "api_key", "storage": "secret", "secret_id": "custom_api_api_key", "sensitive": True}
    second = {"key": "base_url"}
    stored = {"api_services": {"custom_api": {"credential_fields": [first, second]}}}
    loaded = copy.deepcopy(stored)

    # The second moved up and was edited; the one that stood there moved down unchanged.
    edited = [{"key": "base_url", "label": "Base URL"}, first]
    written = _apply(stored, {"api_services": {"custom_api": {"credential_fields": edited}}}, loaded, loaded)

    assert written["api_services"]["custom_api"]["credential_fields"] == edited


def test_swapped_objects_both_edited_do_not_trade_fields():
    first = {"key": "api_key", "storage": "secret", "secret_id": "custom_api_api_key", "sensitive": True, "label": "Key"}
    second = {"key": "base_url", "label": "URL"}
    stored = {"api_services": {"custom_api": {"credential_fields": [first, second]}}}
    loaded = copy.deepcopy(stored)

    # Same length, nothing left as it was: neither place holds its old object.
    edited = [{**second, "label": "Base URL"}, {**first, "label": "API key"}]
    written = _apply(stored, {"api_services": {"custom_api": {"credential_fields": edited}}}, loaded, loaded)

    assert written["api_services"]["custom_api"]["credential_fields"] == edited


def test_an_object_put_in_place_of_a_removed_one_does_not_take_its_fields():
    first = {"key": "api_key", "storage": "secret", "secret_id": "custom_api_api_key", "sensitive": True}
    second = {"key": "base_url"}
    stored = {"api_services": {"custom_api": {"credential_fields": [first, second]}}}
    loaded = copy.deepcopy(stored)

    # The first removed and another field added: the list keeps its length.
    replaced = [{"key": "username"}, second]
    written = _apply(stored, {"api_services": {"custom_api": {"credential_fields": replaced}}}, loaded, loaded)

    assert written["api_services"]["custom_api"]["credential_fields"] == replaced


def test_a_reference_swapped_for_another_with_the_same_value_is_saved():
    stored = {"mcp": {"m": {"type": "local", "command": ["runner", "--key", "{env:KEY_A}"]}}}
    loaded = {"mcp": {"m": {"type": "local", "command": ["runner", "--key", "same-value"]}}}
    values = {"{env:KEY_A}": "same-value", "{env:KEY_B}": "same-value"}

    changed = ["runner", "--key", "{env:KEY_B}"]
    written = _apply(stored, {"mcp": {"m": {"command": changed}}}, loaded, loaded, values)

    assert written["mcp"]["m"]["command"] == changed


def test_references_swapped_within_one_value_are_saved_as_written():
    stored = {"mcp": {"m": {"type": "local", "command": ["runner", "--primary-token={env:A} --backup-token={env:B}"]}}}
    loaded = {"mcp": {"m": {"type": "local", "command": ["runner", "--primary-token=same --backup-token=same"]}}}
    values = {"{env:A}": "same", "{env:B}": "same"}

    swapped = ["runner", "--primary-token={env:B} --backup-token={env:A}"]
    written = _apply(stored, {"mcp": {"m": {"command": swapped}}}, loaded, loaded, values)

    assert written["mcp"]["m"]["command"] == swapped


def test_an_object_keeping_only_a_minor_field_is_not_merged_into_the_old_one():
    # Name and connection replaced, only "enabled" the same: another object, whose
    # connection must not inherit the old one's key.
    old = {"name": "primary", "enabled": True, "connection": {"url": "https://a.example.test", "api_key": "{env:GW_KEY}"}}
    stored = {"api_services": {"gateway": {"profiles": [old]}}}
    loaded = {"api_services": {"gateway": {"profiles": [
        {"name": "primary", "enabled": True, "connection": {"url": "https://a.example.test", "api_key": "sk-gateway-key"}},
    ]}}}
    replaced = [{"name": "secondary", "enabled": True, "connection": {"url": "https://b.example.test"}}]

    written = _apply(stored, {"api_services": {"gateway": {"profiles": replaced}}}, loaded, loaded, {"{env:GW_KEY}": "sk-gateway-key"})

    assert written["api_services"]["gateway"]["profiles"] == replaced


def test_an_object_that_dropped_a_nested_field_is_not_merged_into_the_old_one():
    # Half the fields kept (enabled, type), but the connection no longer has a key:
    # merged into the old profile, it would take the old key to the new address.
    old = {"name": "primary", "enabled": True, "type": "http", "connection": {"url": "https://a.example.test", "api_key": "{env:GW_KEY}"}}
    stored = {"api_services": {"gateway": {"profiles": [old]}}}
    loaded = {"api_services": {"gateway": {"profiles": [
        {**old, "connection": {"url": "https://a.example.test", "api_key": "sk-gateway-key"}},
    ]}}}
    replaced = [{"name": "secondary", "enabled": True, "type": "http", "connection": {"url": "https://b.example.test"}}]

    written = _apply(stored, {"api_services": {"gateway": {"profiles": replaced}}}, loaded, loaded, {"{env:GW_KEY}": "sk-gateway-key"})

    assert written["api_services"]["gateway"]["profiles"] == replaced


def test_most_fields_of_an_object_changed_keeps_the_reference_left_as_it_was():
    # A client sending values as they expand: the key it left alone stays a reference.
    stored = {"fallback_providers": [{"provider_id": "openai", "model_id": "m1", "api_key": "{env:FALLBACK_KEY}"}]}
    loaded = {"fallback_providers": [{"provider_id": "openai", "model_id": "m1", "api_key": "sk-fallback"}]}
    changes = {"fallback_providers": [{"provider_id": "anthropic", "model_id": "m2", "api_key": "sk-fallback"}]}

    written = _apply(stored, changes, loaded, loaded, {"{env:FALLBACK_KEY}": "sk-fallback"})

    assert written["fallback_providers"] == [{"provider_id": "anthropic", "model_id": "m2", "api_key": "{env:FALLBACK_KEY}"}]


def test_changing_an_agent_copied_from_mode_starts_from_the_stored_mode_entry():
    stored = {"mode": {"demo": {"prompt_append": "{env:KEY}"}}}
    copied = {"promptAppend": "sk-env-key", "mode": "primary", "options": {}}
    loaded = {"mode": {"demo": dict(copied)}, "agent": {"demo": dict(copied)}}

    written = _apply(stored, {"agent": {"demo": {"temperature": 0.2}}}, loaded, loaded, {"{env:KEY}": "sk-env-key"})

    assert written["mode"] == {"demo": {"prompt_append": "{env:KEY}"}}
    assert written["agent"] == {"demo": {"prompt_append": "{env:KEY}", "mode": "primary", "temperature": 0.2}}


def test_member_view_masks_every_place_a_file_stores_a_reference():
    places = ReferencePlaces([
        {"provider": {"p": {"options": {"apiKey": "{secret:k}"}}}, "server": {"port": "{env:PORT}"},
         "instructions": ["{file:a.md}"]},
    ])
    expanded = {
        "provider": {"p": {"options": {"apiKey": "sk-real", "baseURL": "https://gw"}}},
        "server": {"port": 8123},
        "instructions": ["plain.md", "file body"],
        "mcp": {"m": {"type": "remote", "url": "https://mcp/sse?apikey=url-key", "headers": {"Authorization": "Bearer t"}}},
        "theme": "dark",
    }

    view, hidden = member_view(expanded, places)

    assert view["provider"]["p"]["options"]["apiKey"] == REDACTED
    assert view["server"]["port"] == REDACTED
    assert view["instructions"] == [REDACTED, REDACTED]
    for secret in ("sk-real", "url-key", "Bearer t", "file body"):
        assert secret not in json.dumps(view)
    assert view["provider"]["p"]["options"]["baseURL"] == "https://gw"
    assert view["theme"] == "dark"
    assert hidden[("server", "port")] == 8123
    assert expanded["provider"]["p"]["options"]["apiKey"] == "sk-real"


def test_member_view_masks_copies_of_a_reference_value_anywhere():
    places = ReferencePlaces([StoredLayer(None, {"mode": {"demo": {"promptAppend": "{env:KEY}"}}}, {"{env:KEY}": "sk-env-key"})])
    expanded = {
        "mode": {"demo": {"promptAppend": "sk-env-key"}},
        "agent": {"demo": {"promptAppend": "sk-env-key", "description": "uses sk-env-key inline"}},
        "custom": {"sk-env-key": "a reference written as a key"},
        "theme": "dark",
    }

    view, hidden = member_view(expanded, places)

    assert "sk-env-key" not in json.dumps(view)
    assert view["agent"]["demo"] == {"promptAppend": REDACTED, "description": REDACTED}
    assert view["custom"] == {}
    assert view["theme"] == "dark"
    assert hidden[("agent", "demo", "promptAppend")] == "sk-env-key"
    assert places.reveals("sk-env-key") and not places.reveals("dark")


def test_short_reference_values_only_count_as_a_whole_value():
    places = ReferencePlaces([StoredLayer(None, {"x": "{env:N}"}, {"{env:N}": "42"})])
    view, _ = member_view({"x": "42", "y": "port 4242", "z": "42"}, places)
    assert view == {"x": REDACTED, "y": "port 4242", "z": REDACTED}


def test_an_unreadable_stored_file_masks_everything():
    view, _ = member_view({"theme": "dark", "n": 1}, ReferencePlaces([None]))
    assert view == {"theme": REDACTED, "n": REDACTED}


def test_values_at_reads_other_files_and_every_value_of_an_unreadable_one():
    places = ReferencePlaces([
        StoredLayer("/cfg/flocks.json", {"mcp": {"m": {"command": ["{env:OWN}"]}}}, {"{env:OWN}": "own-value"}),
        StoredLayer("/cfg/config.json", {"mcp": {"m": {"command": ["--k", "{env:OTHER}"]}}}, {"{env:OTHER}": "other-value"}),
    ])
    assert places.values_at(("mcp", "m", "command"), exclude="/cfg/flocks.json") == {"other-value"}
    assert places.values_at(("mcp", "m", "command")) == {"own-value", "other-value"}
    assert places.values_at(("theme",)) == set()

    unreadable = ReferencePlaces([StoredLayer("/cfg/config.json", None, {"{env:X}": "x-value"})])
    assert unreadable.values_at(("theme",)) == {"x-value"}


def test_restore_hidden_maps_an_unchanged_copy_back_and_keeps_edits():
    places = ReferencePlaces([{"provider": {"p": {"options": {"apiKey": "{secret:k}"}}}}])
    expanded = {"provider": {"p": {"options": {"apiKey": "sk-real"}}}, "channels": {"feishu": {"appSecret": "old"}}}
    view, hidden = member_view(expanded, places)
    sent = json.loads(json.dumps(view))
    sent["channels"]["feishu"]["appSecret"] = "typed-new-secret"
    sent["channels"]["feishu"]["appId"] = REDACTED  # a mask moved elsewhere stays a mask

    restored = restore_hidden(sent, view, hidden)

    assert restored["provider"]["p"]["options"]["apiKey"] == "sk-real"
    assert restored["channels"]["feishu"]["appSecret"] == "typed-new-secret"
    assert restored["channels"]["feishu"]["appId"] == REDACTED
    assert leftover_masks(drop_unchanged(restored, expanded), view, hidden) == [("channels", "feishu", "appId")]


def test_leftover_masks_finds_shifted_list_masks_and_edited_mcp_urls():
    places = ReferencePlaces([{"mcp": {"m": {"command": ["{env:KEY}"]}}}])
    expanded = {"mcp": {"m": {"type": "local", "command": ["runner", "sk-env-key"]},
                        "r": {"type": "remote", "url": "https://x.test/sse?apikey=url-key"}}}
    view, hidden = member_view(expanded, places)
    assert view["mcp"]["r"]["url"] == "https://x.test/sse?apikey=***"

    sent = json.loads(json.dumps(view))
    sent["mcp"]["m"]["command"] = ["--first"] + sent["mcp"]["m"]["command"]
    sent["mcp"]["r"]["url"] = "https://x.test/v2/sse?apikey=***"
    restored = restore_hidden(sent, view, hidden)

    assert sorted(leftover_masks(drop_unchanged(restored, expanded), view, hidden)) == [
        ("mcp", "m", "command", 2),
        ("mcp", "r", "url"),
    ]
    assert leftover_masks(drop_unchanged(restore_hidden(view, view, hidden), expanded), view, hidden) == []


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point every flocks path at tmp_path so nothing touches the runner's ~/.flocks."""
    from flocks.config.config import Config

    root = tmp_path / ".flocks"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(root / "config"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(root / "logs"))
    monkeypatch.delenv("FLOCKS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("FLOCKS_CONFIG_CONTENT", raising=False)
    Config._global_config = None
    Config.clear_cache()
    yield root / "config"
    Config._global_config = None
    Config.clear_cache()


async def test_loading_collects_the_stored_layer_and_caches_it_with_the_config(isolated_config_dir, monkeypatch):
    from flocks.config.config import Config

    config_file = Config.get_config_file()
    assert config_file.parent == isolated_config_dir
    monkeypatch.setenv("FLOCKS_TEST_KEY", "k-from-env")
    config_file.write_text(
        json.dumps({"provider": {"p": {"options": {"api_key": "{env:FLOCKS_TEST_KEY}"}}}}), encoding="utf-8"
    )

    config, places = await Config.get_with_reference_places()

    assert config.provider["p"].options.api_key == "k-from-env"
    assert places.covers(("provider", "p", "options", "apiKey"))
    assert not places.covers(("provider", "p", "options", "baseURL"))
    assert places.values == {"k-from-env"}
    assert places.layers[0].values == {"{env:FLOCKS_TEST_KEY}": "k-from-env"}
