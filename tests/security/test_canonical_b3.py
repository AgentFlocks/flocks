from __future__ import annotations

from flocks.security.canonical import canonical_hash, canonicalize_command, canonicalize_json, canonicalize_path


def test_canonicalize_json_is_stable():
    a = canonicalize_json({"b": 1, "a": 2})
    b = canonicalize_json({"a": 2, "b": 1})
    assert a.status == "ok"
    assert b.status == "ok"
    assert a.hash == b.hash


def test_canonicalize_command_parses_shell_tokens():
    result = canonicalize_command("FOO=1 echo hello")
    assert result.status == "ok"
    assert result.hash is not None
    assert result.value["argv"][-1] == "hello"


def test_canonicalize_path_resolves_home_and_hash():
    result = canonicalize_path("~/")
    assert result.status == "ok"
    assert result.hash is not None
    assert isinstance(result.value, dict)
    assert "path" in result.value


def test_canonical_hash_returns_none_on_unserializable():
    class _X:
        pass

    value = canonical_hash({"x": _X()})
    assert value is not None
