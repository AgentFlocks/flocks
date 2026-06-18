"""Visibility helpers for filesystem-backed workflow definitions."""

from __future__ import annotations

from typing import Any, Mapping

_TRUE_STRINGS = {"1", "true", "yes", "on"}
_HIDDEN_VISIBILITIES = {"hidden", "internal", "private", "template"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _string_value(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def is_hidden_workflow_meta(meta: Mapping[str, Any] | None) -> bool:
    """Return True when workflow metadata marks a definition as non-discoverable."""
    if not isinstance(meta, Mapping):
        return False

    flag_keys = (
        "hidden",
        "templateOnly",
        "internal",
        "excludeFromUI",
        "excludeFromPrompt",
    )
    if any(_truthy(meta.get(key)) for key in flag_keys):
        return True

    if _string_value(meta.get("visibility")) in _HIDDEN_VISIBILITIES:
        return True
    if _string_value(meta.get("status")) == "hidden":
        return True

    return False


def is_hidden_workflow(
    workflow_json: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when a workflow should be hidden from UI and prompt scans."""
    if is_hidden_workflow_meta(meta):
        return True
    if not isinstance(workflow_json, Mapping):
        return False

    if is_hidden_workflow_meta(workflow_json):
        return True

    metadata = workflow_json.get("metadata")
    return is_hidden_workflow_meta(metadata if isinstance(metadata, Mapping) else None)


def is_hidden_workflow_data(data: Mapping[str, Any] | None) -> bool:
    """Return True when data returned by read_workflow_dir() is hidden."""
    if not isinstance(data, Mapping):
        return False
    workflow_json = data.get("workflowJson")
    return is_hidden_workflow(
        workflow_json if isinstance(workflow_json, Mapping) else None,
        data,
    )
