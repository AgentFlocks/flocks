from __future__ import annotations

from flocks.security.canonical import canonical_hash, canonicalize_command, canonicalize_json, canonicalize_path
from flocks.tool.registry import _build_canonical_payload


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
    assert value is None


def test_non_json_value_is_uncertain():
    result = canonicalize_json({"value": object()})

    assert result.status == "uncertain"
    assert result.hash is None


def test_command_uncertainty_propagates_to_top_level():
    result = _build_canonical_payload(tool_name="bash", tool_input={"command": "'"})

    assert result["command_status"] == "uncertain"
    assert result["status"] == "uncertain"
    assert result["hash"] is None


def test_canonical_payload_binds_resolved_cwd_resource_and_execution_domain(tmp_path):
    workdir = tmp_path / "child" / ".."
    resource = {"type": "command", "id": "bash"}

    result = _build_canonical_payload(
        tool_name="bash",
        tool_input={"command": "pwd", "workdir": str(workdir)},
        resource=resource,
        execution_domain="production",
    )

    assert result["status"] == "ok"
    assert result["cwd"] == {"path": str(tmp_path.resolve())}
    assert result["resource"] == resource
    assert result["execution_domain"] == "production"
    assert result["hash"] is not None
