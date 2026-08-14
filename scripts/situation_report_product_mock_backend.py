#!/usr/bin/env python3
"""Development-only backend mock for the phase-one situation-report flow.

This process implements only the backend-owned latest-state and snapshot-download
contract.  ``/__mock__`` endpoints are test controls and are not part of the
production integration contract.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status


SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ResourceName = Literal["report", "template", "materials"]
RESOURCE_LIMITS = {
    "report": 10 * 1024 * 1024,
    "template": 5 * 1024 * 1024,
    "materials": 64 * 1024 * 1024,
}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_content(resource: ResourceName, content: bytes) -> None:
    if not content or len(content) > RESOURCE_LIMITS[resource]:
        raise ValueError(f"{resource} snapshot is empty or exceeds its size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{resource} snapshot must be UTF-8") from exc
    if resource in {"report", "template"}:
        if not text.strip() or "#" not in text:
            raise ValueError(f"{resource} snapshot must be non-empty Markdown")
        return
    count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"materials line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"materials line {line_number} is not an object")
        count += 1
    if count == 0:
        raise ValueError("materials snapshot has no records")


class MockStateStore:
    """Small persistent store whose resource versions are immutable once written."""

    def __init__(self, *, state_dir: Path, template: Path, materials: Path) -> None:
        self.state_dir = state_dir.resolve()
        self.template = template.resolve()
        self.materials = materials.resolve()
        for resource, path in (("template", self.template), ("materials", self.materials)):
            content = path.read_bytes()
            _validate_content(resource, content)  # type: ignore[arg-type]
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_session_id(session_id: str) -> None:
        if not SAFE_IDENTIFIER.fullmatch(session_id):
            raise HTTPException(status_code=400, detail="invalid sessionID")

    def _session_dir(self, session_id: str) -> Path:
        self.validate_session_id(session_id)
        return self.state_dir / "sessions" / session_id

    def _state_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "state.json"

    def _metadata(
        self,
        *,
        resource: ResourceName,
        version: int,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "exists": True,
            "version": version,
            "sizeBytes": len(content),
            "sha256": _sha256(content),
            "filename": filename,
        }
        if resource == "template":
            value.update(templateSnapshotID=f"mock-template-v{version}", format="markdown")
        elif resource == "materials":
            value.update(materialSnapshotID=f"mock-materials-v{version}", format="jsonl")
        else:
            value["source"] = "mock-event-import"
        return value

    def _write_resource(
        self,
        *,
        session_id: str,
        resource: ResourceName,
        version: int,
        content: bytes,
    ) -> dict[str, Any]:
        suffix = {"report": "md", "template": "md", "materials": "jsonl"}[resource]
        filename = f"{resource}-v{version}.{suffix}"
        destination = self._session_dir(session_id) / "snapshots" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError(f"immutable snapshot already exists: {filename}")
        destination.write_bytes(content)
        return self._metadata(
            resource=resource,
            version=version,
            content=content,
            filename=filename,
        )

    def ensure_session(self, session_id: str) -> dict[str, Any]:
        state_path = self._state_path(session_id)
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        template = self.template.read_bytes()
        materials = self.materials.read_bytes()
        state = {
            "report": {"exists": False},
            "template": self._write_resource(
                session_id=session_id,
                resource="template",
                version=1,
                content=template,
            ),
            "materials": self._write_resource(
                session_id=session_id,
                resource="materials",
                version=1,
                content=materials,
            ),
        }
        _atomic_json(state_path, state)
        return state

    def save_resource(
        self,
        *,
        session_id: str,
        resource: ResourceName,
        version: int,
        content: bytes,
    ) -> dict[str, Any]:
        _validate_content(resource, content)
        state = self.ensure_session(session_id)
        current = state[resource]
        current_version = int(current.get("version") or 0) if current.get("exists") else 0
        if version <= current_version:
            raise ValueError(f"{resource} version must be greater than {current_version}")
        metadata = self._write_resource(
            session_id=session_id,
            resource=resource,
            version=version,
            content=content,
        )
        state[resource] = metadata
        _atomic_json(self._state_path(session_id), state)
        return metadata

    def snapshot_path(self, session_id: str, resource: ResourceName, version: int) -> Path:
        state = self.ensure_session(session_id)
        metadata = state[resource]
        if not metadata.get("exists") or int(metadata["version"]) != version:
            raise HTTPException(status_code=404, detail="snapshot not found")
        path = self._session_dir(session_id) / "snapshots" / str(metadata["filename"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot file not found")
        content = path.read_bytes()
        if len(content) != metadata["sizeBytes"] or _sha256(content) != metadata["sha256"]:
            raise HTTPException(status_code=500, detail="stored snapshot verification failed")
        return path

    def append_request_log(self, value: dict[str, Any]) -> None:
        path = self.state_dir / "request_log.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def create_app(*, state_dir: Path, template: Path, materials: Path, token: str) -> FastAPI:
    if not token:
        raise ValueError("SITUATION_REPORT_MOCK_TOKEN is required")
    store = MockStateStore(state_dir=state_dir, template=template, materials=materials)
    app = FastAPI(title="Situation Report Phase-One Backend Mock")

    def authorize(authorization: str | None) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": "development-mock"}

    @app.get("/internal/flocks/v1/report-sessions/{session_id}/state/latest")
    async def latest_state(
        session_id: str,
        known_report_version: int = Query(alias="knownReportVersion", ge=0),
        known_template_version: int = Query(alias="knownTemplateVersion", ge=0),
        known_material_version: int = Query(alias="knownMaterialVersion", ge=0),
        authorization: str | None = Header(default=None),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        authorize(authorization)
        if request_id is None or not SAFE_IDENTIFIER.fullmatch(request_id):
            raise HTTPException(status_code=400, detail="invalid X-Request-ID")
        state_value = store.ensure_session(session_id)
        known = {
            "report": known_report_version,
            "template": known_template_version,
            "materials": known_material_version,
        }
        response: dict[str, Any] = {}
        expires_at = int(time.time() * 1000) + 15 * 60 * 1000
        for resource in ("report", "template", "materials"):
            current = state_value[resource]
            if not current.get("exists"):
                response[resource] = {"exists": False, "changed": False}
                continue
            changed = int(current["version"]) != known[resource]
            public = {
                key: value
                for key, value in current.items()
                if key not in {"filename", "sizeBytes"}
            }
            public["changed"] = changed
            if changed:
                public["sizeBytes"] = current["sizeBytes"]
                public["download"] = {
                    "url": (
                        f"/__mock__/downloads/{session_id}/{resource}/"
                        f"{current['version']}"
                    ),
                    "expiresAt": expires_at,
                }
            response[resource] = public
        store.append_request_log(
            {
                "timeMs": int(time.time() * 1000),
                "sessionID": session_id,
                "requestID": request_id,
                "knownReportVersion": known_report_version,
                "knownTemplateVersion": known_template_version,
                "knownMaterialVersion": known_material_version,
                "returnedVersions": {
                    name: value.get("version", 0) for name, value in state_value.items()
                },
            }
        )
        return response

    @app.get("/__mock__/downloads/{session_id}/{resource}/{version}")
    async def download_snapshot(
        session_id: str,
        resource: ResourceName,
        version: int,
        authorization: str | None = Header(default=None),
    ) -> Response:
        authorize(authorization)
        path = store.snapshot_path(session_id, resource, version)
        media_type = "application/x-ndjson" if resource == "materials" else "text/markdown; charset=utf-8"
        return Response(content=path.read_bytes(), media_type=media_type)

    @app.get("/__mock__/report-sessions/{session_id}/state")
    async def inspect_mock_state(
        session_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return store.ensure_session(session_id)

    @app.put("/__mock__/report-sessions/{session_id}/resources/{resource}")
    async def put_mock_resource(
        session_id: str,
        resource: ResourceName,
        request: Request,
        version: int = Query(ge=1),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            metadata = store.save_resource(
                session_id=session_id,
                resource=resource,
                version=version,
                content=await request.body(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {key: value for key, value in metadata.items() if key != "filename"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18090, type=int)
    args = parser.parse_args()
    token = os.getenv("SITUATION_REPORT_MOCK_TOKEN", "").strip()
    uvicorn.run(
        create_app(
            state_dir=args.state_dir,
            template=args.template,
            materials=args.materials,
            token=token,
        ),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
