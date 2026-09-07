#!/usr/bin/env python3
"""Freeze one real backend report Session for repeatable WebUI debug runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from flocks.situation_report.product.backend_sync import MaterialDetailResponse
from flocks.situation_report.product.contracts import SAFE_IDENTIFIER


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _request_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _headers(token: str, request_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": request_id,
    }


def _require_echo(response: httpx.Response, request_id: str) -> None:
    response.raise_for_status()
    if response.headers.get("X-Request-ID") != request_id:
        raise RuntimeError("Backend response did not echo X-Request-ID")


def _state(
    client: httpx.Client,
    *,
    endpoint: str,
    token: str,
    session_id: str,
    report_version: int,
    template_version: int,
    material_version: int,
) -> dict[str, Any]:
    request_id = _request_id(
        "freeze_state",
        f"{session_id}:{report_version}:{template_version}:{material_version}",
    )
    response = client.get(
        f"{endpoint}/state/latest",
        params={
            "knownReportVersion": report_version,
            "knownTemplateVersion": template_version,
            "knownMaterialVersion": material_version,
        },
        headers=_headers(token, request_id),
    )
    _require_echo(response, request_id)
    value = response.json()
    if value.get("sessionId") != session_id:
        raise RuntimeError("Backend state response returned another sessionId")
    return value


def _download(
    client: httpx.Client,
    *,
    endpoint: str,
    token: str,
    session_id: str,
    resource: str,
) -> tuple[bytes, int]:
    request_id = _request_id("freeze_download", f"{session_id}:{resource}")
    response = client.get(
        f"{endpoint}/{resource}/download",
        headers=_headers(token, request_id),
    )
    _require_echo(response, request_id)
    version_header = {
        "template": "X-Template-Version",
        "materials": "X-Material-Version",
    }[resource]
    version = int(response.headers.get(version_header) or "0")
    if version < 1:
        raise RuntimeError(f"Backend {resource} download omitted {version_header}")
    return response.content, version


def _material_rows(content: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    for line_number, line in enumerate(content.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        source_type = value.get("source_type")
        source_id = str(value.get("source_id") or "").strip()
        identity = f"{source_type}:{source_id}"
        if source_type not in {"REPORT", "VULN", "DARKWEB", "TELEGRAM"} or not source_id:
            raise RuntimeError(f"Material line {line_number} has an invalid identity")
        if identity in identities:
            raise RuntimeError(f"Material line {line_number} duplicates {identity}")
        identities.add(identity)
        rows.append(value)
    if not rows:
        raise RuntimeError("A frozen debug dataset must contain at least one material")
    return rows


def export_dataset(args: argparse.Namespace) -> Path:
    if not SAFE_IDENTIFIER.fullmatch(args.session_id):
        raise ValueError("session-id is invalid")
    if not SAFE_IDENTIFIER.fullmatch(args.dataset_id):
        raise ValueError("dataset-id is invalid")
    token = os.getenv("SITUATION_REPORT_BACKEND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SITUATION_REPORT_BACKEND_TOKEN is not configured")
    base_url = args.base_url.strip().rstrip("/")
    endpoint = (
        f"{base_url}/internal/flocks/v1/report-sessions/{args.session_id}"
    )
    client_kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(args.timeout, connect=10.0),
        "follow_redirects": False,
    }
    if args.proxy:
        client_kwargs["proxy"] = args.proxy

    with httpx.Client(**client_kwargs) as client:
        initial = _state(
            client,
            endpoint=endpoint,
            token=token,
            session_id=args.session_id,
            report_version=0,
            template_version=0,
            material_version=0,
        )
        if not (initial.get("template") or {}).get("exists"):
            raise RuntimeError("Backend Session has no template")
        if not (initial.get("materials") or {}).get("exists"):
            raise RuntimeError("Backend Session has no materials")

        template, template_version = _download(
            client,
            endpoint=endpoint,
            token=token,
            session_id=args.session_id,
            resource="template",
        )
        materials, material_version = _download(
            client,
            endpoint=endpoint,
            token=token,
            session_id=args.session_id,
            resource="materials",
        )
        material_rows = _material_rows(materials)
        detail_rows: list[dict[str, Any]] = []
        for material in material_rows:
            source_type = str(material["source_type"])
            source_id = str(material["source_id"]).strip()
            request_id = _request_id(
                "freeze_detail",
                f"{args.session_id}:{source_type}:{source_id}",
            )
            response = client.get(
                f"{endpoint}/materials/detail",
                params={"sourceType": source_type, "sourceId": source_id},
                headers=_headers(token, request_id),
            )
            _require_echo(response, request_id)
            detail = MaterialDetailResponse.model_validate(response.json())
            if detail.source_type != source_type or detail.source_id != source_id:
                raise RuntimeError(
                    f"Backend detail identity mismatch for {source_type}:{source_id}"
                )
            detail_rows.append(detail.model_dump(exclude_none=True))

        stable = _state(
            client,
            endpoint=endpoint,
            token=token,
            session_id=args.session_id,
            report_version=int((initial.get("report") or {}).get("version") or 0),
            template_version=template_version,
            material_version=material_version,
        )
        if stable["template"]["changed"] or stable["materials"]["changed"]:
            raise RuntimeError("Backend template or materials changed during dataset export")

    details = "".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        for value in detail_rows
    ).encode("utf-8")
    counts = dict(Counter(str(row["source_type"]) for row in material_rows))
    manifest = {
        "schemaVersion": 1,
        "datasetID": args.dataset_id,
        "name": args.name,
        "description": args.description,
        "language": args.language,
        "sourceSessionID": args.session_id,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "templateSHA256": _sha256(template),
        "materialsSHA256": _sha256(materials),
        "materialDetailsSHA256": _sha256(details),
        "materialCount": len(material_rows),
        "materialDetailCount": len(detail_rows),
        "sourceCounts": counts,
    }

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / args.dataset_id
    if destination.exists():
        raise FileExistsError(f"Dataset destination already exists: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{args.dataset_id}.", dir=output_root))
    try:
        (temporary / "template.md").write_bytes(template)
        (temporary / "materials.jsonl").write_bytes(materials)
        (temporary / "material-details.jsonl").write_bytes(details)
        (temporary / "dataset.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for path in temporary.iterdir():
            os.chmod(path, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o700)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--language", choices=("zh-CN", "en-US"), default="zh-CN")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--proxy")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    destination = export_dataset(args)
    print(destination)


if __name__ == "__main__":
    main()
