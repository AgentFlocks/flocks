"""Fail-closed Dockerfile policy for local, offline dynamic validation."""

from __future__ import annotations

import re


_FROM_RE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I)
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
_PARSER_DIRECTIVE_RE = re.compile(r"^#\s*(syntax|escape|check)\s*=", re.I)


def dockerfile_base_images(contents: str) -> list[str]:
    """Return every external image used by the supported offline-safe subset."""
    logical_lines = _logical_lines(contents)
    images: list[str] = []
    stages: set[str] = set()
    saw_from = False

    for line in logical_lines:
        instruction_match = re.match(r"^([A-Za-z]+)(?:\s+(.*))?$", line)
        if instruction_match is None:
            raise ValueError("Dockerfile instruction is not safely supported")
        instruction, arguments = instruction_match.groups()
        instruction = instruction.upper()
        arguments = arguments or ""

        if instruction == "ADD":
            raise ValueError("Dockerfile ADD instructions are not supported")
        if instruction == "ONBUILD":
            raise ValueError("Dockerfile ONBUILD instructions are not supported")
        if instruction == "RUN" and arguments.startswith("--"):
            raise ValueError("Dockerfile RUN options are not supported")
        if instruction == "COPY":
            copy_source = _copy_source(arguments)
            if copy_source is not None and not _is_stage(copy_source, stages):
                images.append(copy_source)
            continue
        if instruction != "FROM":
            continue

        saw_from = True
        if arguments.lower().startswith("--platform"):
            raise ValueError("Dockerfile FROM --platform is not supported")
        match = _FROM_RE.fullmatch(line)
        if match is None:
            raise ValueError("Dockerfile FROM instruction is not safely supported")
        image, alias = match.groups()
        _require_literal_image(image, field="base image")
        if image.lower() != "scratch" and image.lower() not in stages:
            images.append(image)
        if alias:
            stages.add(alias.lower())

    if not saw_from:
        raise ValueError("Dockerfile must declare a literal base image")
    return list(dict.fromkeys(images))


def _logical_lines(contents: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if _PARSER_DIRECTIVE_RE.match(line):
                raise ValueError("Dockerfile parser directives are not supported")
            continue
        pending += line
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        lines.append(pending.strip())
        pending = ""
    if pending:
        raise ValueError("Dockerfile has an unterminated continuation")
    return lines


def _copy_source(arguments: str) -> str | None:
    source: str | None = None
    for token in arguments.split():
        if not token.startswith("--"):
            break
        if token == "--from" or token.startswith("--from="):
            if token == "--from" or source is not None:
                raise ValueError("Dockerfile COPY --from must use one literal source")
            source = token.removeprefix("--from=")
    if source is not None:
        _require_literal_image(source, field="COPY --from source")
    return source


def _is_stage(source: str, stages: set[str]) -> bool:
    return source.isdigit() or source.lower() in stages


def _require_literal_image(value: str, *, field: str) -> None:
    if (
        not value
        or "$" in value
        or value.lower().startswith(("http://", "https://"))
        or _IMAGE_RE.fullmatch(value) is None
    ):
        raise ValueError(f"Dockerfile {field} must be literal")
