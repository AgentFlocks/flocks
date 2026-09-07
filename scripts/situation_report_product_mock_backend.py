#!/usr/bin/env python3
"""Development-only backend mock for the current-resource report contract.

``/__mock__`` endpoints are test controls and are not part of the production
integration contract.
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
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from flocks.situation_report.product.backend_sync import (
    MATERIAL_DETAIL_FIELDS,
    MaterialDetailResponse,
)


SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ResourceName = Literal["report", "template", "materials"]
RESOURCE_LIMITS = {
    "report": 10 * 1024 * 1024,
    "template": 5 * 1024 * 1024,
    "materials": 64 * 1024 * 1024,
}
MATERIAL_DETAILS_LIMIT = 128 * 1024 * 1024


class MockContractError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        request_id: str | None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id


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
    if len(content) > RESOURCE_LIMITS[resource] or (resource != "materials" and not content):
        raise ValueError(f"{resource} content is invalid or exceeds its size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{resource} snapshot must be UTF-8") from exc
    if resource in {"report", "template"}:
        if not text.strip() or "#" not in text:
            raise ValueError(f"{resource} snapshot must be non-empty Markdown")
        return
    identities: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"materials line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"materials line {line_number} is not an object")
        source_type = value.get("source_type")
        source_id = value.get("source_id")
        if source_type not in {"REPORT", "VULN", "DARKWEB", "TELEGRAM"}:
            raise ValueError(f"materials line {line_number} has an invalid source_type")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"materials line {line_number} has an invalid source_id")
        identity = f"{source_type}:{source_id}"
        if identity in identities:
            raise ValueError(f"materials line {line_number} duplicates {identity}")
        identities.add(identity)


def _material_detail_rows(content: bytes) -> list[dict[str, Any]]:
    if len(content) > MATERIAL_DETAILS_LIMIT:
        raise ValueError("material details exceed their size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("material details must be UTF-8 JSONL") from exc
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            detail = MaterialDetailResponse.model_validate_json(line)
        except ValidationError as exc:
            raise ValueError(
                f"material details line {line_number} violates the backend contract: {exc}"
            ) from exc
        identity = f"{detail.source_type}:{detail.source_id}"
        if identity in identities:
            raise ValueError(f"material details line {line_number} duplicates {identity}")
        identities.add(identity)
        rows.append(detail.model_dump(exclude_none=True))
    return rows


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
        if not SESSION_IDENTIFIER.fullmatch(session_id):
            raise HTTPException(status_code=400, detail="invalid sessionID")

    def _session_dir(self, session_id: str) -> Path:
        self.validate_session_id(session_id)
        return self.state_dir / "sessions" / session_id

    def _state_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "state.json"

    def _metadata(
        self,
        *,
        version: int,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        return {
            "exists": True,
            "version": version,
            "sizeBytes": len(content),
            "sha256": _sha256(content),
            "filename": filename,
        }

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

    def current_path(self, session_id: str, resource: ResourceName) -> Path:
        state = self.ensure_session(session_id)
        metadata = state[resource]
        if not metadata.get("exists"):
            raise HTTPException(status_code=404, detail=f"{resource} not found")
        path = self._session_dir(session_id) / "snapshots" / str(metadata["filename"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot file not found")
        content = path.read_bytes()
        if len(content) != metadata["sizeBytes"] or _sha256(content) != metadata["sha256"]:
            raise HTTPException(status_code=500, detail="stored snapshot verification failed")
        return path

    def _selected_material_ids(self, session_id: str) -> set[str]:
        path = self.current_path(session_id, "materials")
        identities: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            identities.add(f"{value['source_type']}:{str(value['source_id']).strip()}")
        return identities

    def save_material_details(
        self,
        *,
        session_id: str,
        material_version: int,
        content: bytes,
    ) -> dict[str, Any]:
        state = self.ensure_session(session_id)
        current_version = int((state.get("materials") or {}).get("version") or 0)
        if material_version != current_version:
            raise ValueError(
                f"material detail version must equal current material version {current_version}"
            )
        rows = _material_detail_rows(content)
        selected = self._selected_material_ids(session_id)
        undeclared = sorted(
            f"{row['source_type']}:{row['source_id']}"
            for row in rows
            if f"{row['source_type']}:{row['source_id']}" not in selected
        )
        if undeclared:
            raise ValueError(
                f"material details include unselected identities: {undeclared}"
            )
        destination = (
            self._session_dir(session_id)
            / "snapshots"
            / f"material-details-v{material_version}.jsonl"
        )
        if destination.exists():
            raise ValueError(
                f"immutable material details already exist for version {material_version}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return {
            "materialVersion": material_version,
            "recordCount": len(rows),
            "sizeBytes": len(content),
            "sha256": _sha256(content),
        }

    def get_material_detail(
        self,
        *,
        session_id: str,
        source_type: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        identity = f"{source_type}:{source_id}"
        if identity not in self._selected_material_ids(session_id):
            return None
        state = self.ensure_session(session_id)
        material_version = int(state["materials"]["version"])
        path = (
            self._session_dir(session_id)
            / "snapshots"
            / f"material-details-v{material_version}.jsonl"
        )
        if not path.is_file():
            return None
        for row in _material_detail_rows(path.read_bytes()):
            if row["source_type"] == source_type and row["source_id"] == source_id:
                return row
        return None

    def append_request_log(self, value: dict[str, Any]) -> None:
        path = self.state_dir / "request_log.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def create_app(*, state_dir: Path, template: Path, materials: Path, token: str) -> FastAPI:
    if not token:
        raise ValueError("SITUATION_REPORT_MOCK_TOKEN is required")
    store = MockStateStore(state_dir=state_dir, template=template, materials=materials)
    app = FastAPI(title="Situation Report Phase-One Backend Mock")

    @app.exception_handler(MockContractError)
    async def handle_contract_error(
        _request: Request,
        exc: MockContractError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "requestId": exc.request_id,
            },
            headers=(
                {"X-Request-ID": exc.request_id}
                if exc.request_id is not None
                else None
            ),
        )

    def authorize(authorization: str | None) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise MockContractError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code="FLOCKS_UNAUTHORIZED",
                message="unauthorized",
                request_id=None,
            )

    def validate_request_id(request_id: str | None) -> str:
        if request_id is None or not request_id.strip():
            raise MockContractError(
                status_code=400,
                code="INVALID_REQUEST_ID",
                message="invalid X-Request-ID",
                request_id=None,
            )
        return request_id.strip()

    def validate_contract_session_id(session_id: str, request_id: str) -> None:
        if not SESSION_IDENTIFIER.fullmatch(session_id):
            raise MockContractError(
                status_code=400,
                code="INVALID_SESSION_ID",
                message="invalid sessionID",
                request_id=request_id,
            )

    def parse_known_version(value: str | None, name: str, request_id: str) -> int:
        if value is None:
            raise MockContractError(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"{name} is required and must be an int64",
                request_id=request_id,
            )
        try:
            parsed = int(value)
        except ValueError as exc:
            raise MockContractError(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"{name} must be an int64",
                request_id=request_id,
            ) from exc
        if parsed < 0:
            raise MockContractError(
                status_code=400,
                code="INVALID_KNOWN_VERSION",
                message=f"{name} must be greater than or equal to zero",
                request_id=request_id,
            )
        return parsed

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": "development-mock"}

    @app.get("/internal/flocks/v1/report-sessions/{session_id}/state/latest")
    async def latest_state(
        session_id: str,
        response: Response,
        known_report_version_value: str | None = Query(default=None, alias="knownReportVersion"),
        known_template_version_value: str | None = Query(default=None, alias="knownTemplateVersion"),
        known_material_version_value: str | None = Query(default=None, alias="knownMaterialVersion"),
        authorization: str | None = Header(default=None),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        authorize(authorization)
        request_id = validate_request_id(request_id)
        validate_contract_session_id(session_id, request_id)
        response.headers["X-Request-ID"] = request_id
        known_report_version = parse_known_version(
            known_report_version_value,
            "knownReportVersion",
            request_id,
        )
        known_template_version = parse_known_version(
            known_template_version_value,
            "knownTemplateVersion",
            request_id,
        )
        known_material_version = parse_known_version(
            known_material_version_value,
            "knownMaterialVersion",
            request_id,
        )
        state_value = store.ensure_session(session_id)
        known = {
            "report": known_report_version,
            "template": known_template_version,
            "materials": known_material_version,
        }
        response_value: dict[str, Any] = {"sessionId": session_id}
        for resource in ("report", "template", "materials"):
            current = state_value[resource]
            if not current.get("exists"):
                response_value[resource] = {"exists": False, "version": None, "changed": False}
                continue
            changed = int(current["version"]) != known[resource]
            response_value[resource] = {
                "exists": True,
                "version": int(current["version"]),
                "changed": changed,
            }
        store.append_request_log(
            {
                "timeMs": int(time.time() * 1000),
                "sessionID": session_id,
                "requestID": request_id,
                "knownReportVersion": known_report_version,
                "knownTemplateVersion": known_template_version,
                "knownMaterialVersion": known_material_version,
                "returnedVersions": {name: value.get("version", 0) for name, value in state_value.items()},
            }
        )
        return response_value

    @app.get("/internal/flocks/v1/report-sessions/{session_id}/{resource}/download")
    async def download_current_resource(
        session_id: str,
        resource: ResourceName,
        authorization: str | None = Header(default=None),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> Response:
        authorize(authorization)
        request_id = validate_request_id(request_id)
        validate_contract_session_id(session_id, request_id)
        path = store.current_path(session_id, resource)
        metadata = store.ensure_session(session_id)[resource]
        media_type = "application/x-ndjson" if resource == "materials" else "text/markdown; charset=utf-8"
        version_header = {
            "report": "X-Report-Version",
            "template": "X-Template-Version",
            "materials": "X-Material-Version",
        }[resource]
        return Response(
            content=path.read_bytes(),
            media_type=media_type,
            headers={version_header: str(metadata["version"]), "X-Request-ID": request_id},
        )

    @app.get("/internal/flocks/v1/report-sessions/{session_id}/materials/detail")
    async def material_detail(
        session_id: str,
        response: Response,
        source_type: str | None = Query(default=None, alias="sourceType"),
        source_id: str | None = Query(default=None, alias="sourceId"),
        authorization: str | None = Header(default=None),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        authorize(authorization)
        request_id = validate_request_id(request_id)
        validate_contract_session_id(session_id, request_id)
        response.headers["X-Request-ID"] = request_id
        if source_type not in MATERIAL_DETAIL_FIELDS or source_id is None:
            raise MockContractError(
                status_code=400,
                code="INVALID_REQUEST",
                message="sourceType and sourceId are required",
                request_id=request_id,
            )
        normalized_source_id = source_id.strip()
        if not normalized_source_id:
            raise MockContractError(
                status_code=400,
                code="INVALID_MATERIAL_KEY",
                message="sourceId must be non-empty",
                request_id=request_id,
            )
        detail = store.get_material_detail(
            session_id=session_id,
            source_type=source_type,
            source_id=normalized_source_id,
        )
        if detail is None:
            raise MockContractError(
                status_code=404,
                code="REPORT_MATERIAL_NOT_FOUND",
                message="report material not found",
                request_id=request_id,
            )
        store.append_request_log(
            {
                "timeMs": int(time.time() * 1000),
                "sessionID": session_id,
                "requestID": request_id,
                "operation": "materials.detail",
                "sourceType": source_type,
                "sourceId": normalized_source_id,
            }
        )
        return detail

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

    @app.put("/__mock__/report-sessions/{session_id}/materials/details")
    async def put_mock_material_details(
        session_id: str,
        request: Request,
        material_version: int = Query(alias="materialVersion", ge=1),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            return store.save_material_details(
                session_id=session_id,
                material_version=material_version,
                content=await request.body(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
