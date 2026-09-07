"""Small, shared helpers for persisted PoC file payloads."""

from __future__ import annotations

import base64
import binascii
from typing import Any


POC_FILE_ENCODINGS = frozenset({"utf8", "hex", "base64"})


def decode_poc_bytes(data: Any, encoding: Any) -> bytes:
    """Decode one persisted PoC file without executing or materializing it."""
    if not isinstance(data, str) or encoding not in POC_FILE_ENCODINGS:
        raise ValueError("PoC file requires supported encoding and string data")
    try:
        if encoding == "utf8":
            return data.encode("utf-8")
        if encoding == "hex":
            return bytes.fromhex(data)
        return base64.b64decode(data.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValueError("PoC file data does not match its encoding") from exc


def resolve_cybergym_input(bundle: Any) -> tuple[bytes, str]:
    """Resolve the one literal PoC file accepted by CyberGym's raw-input runner."""
    if not isinstance(bundle, dict):
        raise ValueError("Generic PoC bundle is invalid")
    artifact_type = bundle.get("artifact_type")
    if artifact_type == "source_harness":
        raise ValueError("CyberGym cannot execute a source_harness bundle; submit literal raw input")
    if artifact_type not in {"raw_input", "request", "bundle"}:
        raise ValueError("Generic PoC artifact_type is not executable by CyberGym")
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Generic PoC bundle has no files")
    delivery = bundle.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("input_kind") != "literal":
        raise ValueError("CyberGym requires delivery.input_kind=literal")
    selector = delivery.get("input_path") if isinstance(delivery, dict) else None
    if not isinstance(selector, str) or not selector.strip():
        selector = bundle.get("entrypoint")
    matches = [
        item
        for item in files
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and (not selector or item["path"] == selector)
    ]
    if selector and len(matches) != 1:
        raise ValueError("Generic PoC must identify exactly one CyberGym input file")
    if not selector:
        if len(files) != 1 or not isinstance(files[0], dict):
            raise ValueError("Generic PoC must identify exactly one CyberGym input file")
        matches = [files[0]]
    item = matches[0]
    path = item.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Generic PoC input file is invalid")
    raw = decode_poc_bytes(item.get("data"), item.get("encoding", "utf8"))
    if not raw:
        raise ValueError("Generic PoC input file must be non-empty")
    return raw, path
