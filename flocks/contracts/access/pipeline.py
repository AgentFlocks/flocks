"""Shared overlay, idempotency, and mutation pipelines."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from flocks.contracts.access.models import InternalDataRow, MutationPlan, RuntimeContext, ContractRuntimeError


@dataclass
class OverlayEntry:
    version: int
    fields: dict[str, Any]


class OverlayStore:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str, str], OverlayEntry] = {}

    def merge(self, rows: list[InternalDataRow], context: RuntimeContext) -> list[InternalDataRow]:
        merged: list[InternalDataRow] = []
        for row in rows:
            entity_type = row.identity.get("entityType")
            entity_id = row.identity.get("entityId")
            if not isinstance(entity_type, str) or not isinstance(entity_id, str):
                merged.append(row)
                continue
            entry = self._entries.get(
                (
                    context.page_id,
                    context.contract_id,
                    context.contract_version,
                    entity_type,
                    entity_id,
                )
            )
            if entry is None:
                merged.append(row)
                continue
            merged.append(
                InternalDataRow(
                    raw={
                        **row.raw,
                        **entry.fields,
                        "_overlay_version": entry.version,
                    },
                    identity=row.identity,
                )
            )
        return merged

    def transaction(self, plan: MutationPlan) -> OverlayEntry:
        key = (
            plan.context.page_id,
            plan.context.contract_id,
            plan.context.contract_version,
            plan.entity_type,
            plan.entity_id,
        )
        current = self._entries.get(key)
        current_version = current.version if current else 0
        expected = plan.expected_overlay_version
        if current_version == 0:
            if expected not in (None, 0):
                raise ContractRuntimeError("conflict", status_code=409, user_message="Overlay version conflict.")
        elif expected != current_version:
            raise ContractRuntimeError("conflict", status_code=409, user_message="Overlay version conflict.")

        fields = dict(current.fields) if current else {}
        fields.update(
            {
                key: value
                for key, value in plan.params.items()
                if key not in {"entityType", "entityId"}
            }
        )
        entry = OverlayEntry(version=current_version + 1, fields=fields)
        self._entries[key] = entry
        return entry


class IdempotencyService:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], tuple[str, dict[str, Any]]] = {}

    def replay_or_conflict(self, scope: tuple[str, ...], request_hash: str) -> dict[str, Any] | None:
        entry = self._entries.get(scope)
        if entry is None:
            return None
        stored_hash, response = entry
        if stored_hash != request_hash:
            raise ContractRuntimeError(
                "conflict",
                status_code=409,
                user_message="idempotencyKey was already used with different content.",
            )
        return response

    def store(self, scope: tuple[str, ...], request_hash: str, response: dict[str, Any]) -> None:
        self._entries[scope] = (request_hash, response)


class MutationPipeline:
    def __init__(
        self,
        overlay_store: OverlayStore | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self._overlay_store = overlay_store or OverlayStore()
        self._idempotency_service = idempotency_service or IdempotencyService()

    def run(self, plan: MutationPlan) -> dict[str, Any]:
        request_hash = hashlib.sha256(
            json.dumps(plan.params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        scope = (
            plan.context.page_id,
            plan.context.contract_id,
            plan.context.contract_version,
            plan.entity_type,
            plan.entity_id,
            plan.idempotency_key,
        )
        replay = self._idempotency_service.replay_or_conflict(scope, request_hash)
        if replay is not None:
            return replay

        entry = self._overlay_store.transaction(plan)
        response = {
            "ok": True,
            "entityType": plan.entity_type,
            "entityId": plan.entity_id,
            "overlayVersion": entry.version,
            "writeThrough": {
                "enabled": plan.write_through_enabled,
                "status": "not_configured",
            },
        }
        self._idempotency_service.store(scope, request_hash, response)
        return response
