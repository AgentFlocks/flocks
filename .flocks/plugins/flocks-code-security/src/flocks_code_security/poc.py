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


def require_cybergym_submission_input(
    bundle: Any,
    *,
    max_bytes: int,
) -> tuple[bytes, str]:
    """Validate a PoC that can be sent to CyberGym without materialization.

    CyberGym's official runner mounts one input file.  Keep generic bundles
    flexible for normal audits, but make the producer of a CyberGym PoC commit
    to that exact byte sequence before the dynamic phase starts.
    """
    if not isinstance(bundle, dict) or bundle.get("artifact_type") != "raw_input":
        raise ValueError("CyberGym PoC must use artifact_type=raw_input")
    files = bundle.get("files")
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("CyberGym PoC must contain exactly one raw input file")
    delivery = bundle.get("delivery")
    if not isinstance(delivery, dict) or delivery.get("input_kind") != "literal":
        raise ValueError("CyberGym PoC requires delivery.input_kind=literal")
    entrypoint = bundle.get("entrypoint")
    input_path = delivery.get("input_path")
    if not isinstance(entrypoint, str) or input_path != entrypoint:
        raise ValueError("CyberGym PoC delivery.input_path must equal entrypoint")
    raw, path = resolve_cybergym_input(bundle)
    if path != entrypoint:
        raise ValueError("CyberGym PoC entrypoint must identify its raw input")
    if type(max_bytes) is not int or not 1 <= len(raw) <= max_bytes:
        raise ValueError("CyberGym PoC input exceeds the trusted task byte limit")
    return raw, path


def materialize_bit_recipe(raw: bytes, recipe: Any, *, max_bytes: int) -> bytes:
    """Apply bounded, deterministic MSB-first bit edits to an existing seed.

    Offsets are relative to the bytes actually consumed by the harness. No
    protocol, shell, filesystem path or network transport is inferred here.
    """
    if not isinstance(recipe, dict) or set(recipe) - {"size_bytes", "edits"}:
        raise ValueError("Recipe supports size_bytes and edits only")
    size = recipe.get("size_bytes", len(raw))
    if type(size) is not int or not 1 <= size <= max_bytes:
        raise ValueError("Recipe size exceeds input budget")
    edits = recipe.get("edits", [])
    if not isinstance(edits, list) or len(edits) > 256:
        raise ValueError("Recipe permits at most 256 bit edits")
    result = bytearray(raw[:size])
    result.extend(b"\0" * (size - len(result)))
    occupied = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"offset_bits", "width_bits", "value"}:
            raise ValueError("Bit edit requires offset_bits, width_bits, value")
        offset, width, value = edit["offset_bits"], edit["width_bits"], edit["value"]
        if (any(type(item) is not int for item in (offset, width, value))
                or not 1 <= width <= 64 or offset < 0 or offset + width > size * 8
                or not 0 <= value < (1 << width)):
            raise ValueError("Bit edit is outside its field bounds")
        for index in range(width):
            bit = offset + index
            if bit in occupied:
                raise ValueError("Recipe bit edits must not overlap")
            occupied.add(bit)
            mask = 1 << (7 - bit % 8)
            result[bit // 8] = (result[bit // 8] & ~mask) | (((value >> (width - 1 - index)) & 1) * mask)
    return bytes(result)
