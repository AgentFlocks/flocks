"""Managed Session orchestration around the production report Agent."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from flocks.input.events import UserInputEvent
from flocks.server.routes.event import publish_event
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session, SessionInfo
from flocks.utils.id import Identifier

from .backend_sync import BackendReportSynchronizer, initialize_report_action
from .events import publish_report_status
from .files import async_file_lock, atomic_write_json, read_json, session_root, utc_now
from .output import publish_validated_candidate
from .policy import ReportPolicyDecision
from .workspace import file_sha256


PRODUCTION_AGENT = "situation-report-product"
MAX_AGENT_RECOVERY_TURNS = 3
ALLOWED_PRODUCT_MODELS = frozenset(
    {
        ("threatbook-cn-llm", "bailian:deepseek-v4-pro"),
        ("threatbook-cn-llm", "bailian:deepseek-v4-flash-0731"),
        ("anthropic", "claude-opus-4-6"),
        ("anthropic", "claude-opus-4-8"),
    }
)
_model_slots: Optional[asyncio.Semaphore] = None
_model_slots_limit: Optional[int] = None


class ProductAgentExecutionError(RuntimeError):
    """The production Agent stopped with an explicit persisted model error."""


def _build_agent_recovery_event(
    *,
    event: UserInputEvent,
    workspace_dir: Path,
    generation_id: str,
    recovery_turn: int,
) -> Optional[UserInputEvent]:
    """Return a bounded internal continuation when the Agent stopped too early."""

    candidate_path = workspace_dir / "work" / generation_id / "report.md"
    validation_path = workspace_dir / "runs" / generation_id / "validation.json"
    instruction: Optional[str] = None
    if not candidate_path.is_file():
        instruction = (
            "The candidate report has not been written. Continue the required Skill steps now: "
            "write the complete candidate with situation_product_report_write, then validate it."
        )
    elif not validation_path.is_file():
        instruction = (
            "The current candidate has not been validated. Call "
            "situation_product_report_validate now and continue until validation passes."
        )
    else:
        validation = read_json(validation_path)
        status = validation.get("status")
        validated_sha = str(validation.get("candidateSHA256") or "")
        current_sha = file_sha256(candidate_path)
        attempt = int(validation.get("attempt") or 0)
        if status == "passed" and validated_sha == current_sha:
            return None
        if attempt >= 3:
            return None
        if validated_sha != current_sha:
            instruction = (
                "The candidate changed after its last validation. Do not rewrite it again yet; "
                "call situation_product_report_validate for the current candidate now."
            )
        else:
            issues = json.dumps(
                validation.get("issues") or [],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            instruction = (
                "Validation returned needs_revision. In this same response, immediately repair only "
                "the listed issues with situation_product_report_write, passing expected_sha256="
                f"{current_sha}, then call situation_product_report_validate again. "
                f"Do not answer with a plan or promise. Issues: {issues}"
            )

    recovery_text = (
        "[SITUATION_REPORT_PRODUCT_RECOVERY_V1]\n"
        f"generationID: {generation_id}\n"
        f"recoveryTurn: {recovery_turn}\n"
        f"{instruction}"
    )
    return event.model_copy(
        update={
            "text": recovery_text,
            "parts": [{"type": "text", "text": recovery_text}],
            "display_text": "正在根据校验结果继续完善报告。",
            "message_id": None,
        }
    )


async def _run_agent_until_candidate_ready(
    *,
    session: SessionInfo,
    event: UserInputEvent,
    generation_id: str,
    workspace_dir: Path,
    working_directory: str,
    generic_runner: Callable[[str, SessionInfo, UserInputEvent, str], Awaitable[None]],
    on_recovery: Callable[[int], Awaitable[None]],
) -> None:
    current_event = event
    for turn_index in range(MAX_AGENT_RECOVERY_TURNS + 1):
        await generic_runner(session.id, session, current_event, working_directory)
        await _raise_persisted_agent_error(session.id)
        recovery_turn = turn_index + 1
        if recovery_turn > MAX_AGENT_RECOVERY_TURNS:
            return
        recovery_event = _build_agent_recovery_event(
            event=event,
            workspace_dir=workspace_dir,
            generation_id=generation_id,
            recovery_turn=recovery_turn,
        )
        if recovery_event is None:
            return
        await on_recovery(recovery_turn)
        current_event = recovery_event


async def _raise_persisted_agent_error(session_id: str) -> None:
    messages = await Message.list(session_id)
    for message in reversed(messages):
        if message.role != MessageRole.ASSISTANT or not message.error:
            continue
        error = message.error
        if hasattr(error, "model_dump"):
            error = error.model_dump(exclude_none=True)
        if isinstance(error, dict):
            code = str(error.get("name") or error.get("code") or "model_error")
            detail = str(error.get("message") or error.get("data") or "Agent execution failed")
            raise ProductAgentExecutionError(f"{code}: {detail}"[:2000])
        raise ProductAgentExecutionError(str(error)[:2000])


def _global_model_slots() -> asyncio.Semaphore:
    global _model_slots, _model_slots_limit
    limit = max(1, int(os.getenv("SITUATION_REPORT_MODEL_CONCURRENCY", "2")))
    if _model_slots is None or _model_slots_limit != limit:
        _model_slots = asyncio.Semaphore(limit)
        _model_slots_limit = limit
    return _model_slots


async def persist_direct_response(
    *,
    session: SessionInfo,
    event: UserInputEvent,
    decision: ReportPolicyDecision,
    expected_generation: int,
) -> None:
    """Persist and publish a policy response without calling a model or tool."""

    now_ms = int(time.time() * 1000)
    user_message_id = event.message_id or Identifier.create("message")
    user_part_id = Identifier.create("part")
    assistant_message_id = Identifier.create("message")
    assistant_part_id = Identifier.create("part")
    user_text = event.user_visible_text
    assistant_text = decision.text or ""
    await Session.run_active_write(
        session.id,
        lambda: Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content=user_text,
            id=user_message_id,
            time={"created": now_ms},
            agent=PRODUCTION_AGENT,
            ignored=True,
            part_id=user_part_id,
        ),
        expected_generation=expected_generation,
    )
    await Session.run_active_write(
        session.id,
        lambda: Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content=assistant_text,
            id=assistant_message_id,
            time={"created": now_ms, "completed": now_ms},
            parentID=user_message_id,
            modelID="policy",
            providerID="builtin",
            agent=PRODUCTION_AGENT,
            finish="stop",
            ignored=True,
            part_id=assistant_part_id,
            part_metadata=decision.metadata,
        ),
        expected_generation=expected_generation,
    )
    await publish_event(
        "message.updated",
        {
            "info": {
                "id": user_message_id,
                "sessionID": session.id,
                "role": "user",
                "time": {"created": now_ms},
                "agent": PRODUCTION_AGENT,
            }
        },
    )
    await publish_event(
        "message.part.updated",
        {
            "part": {
                "id": user_part_id,
                "messageID": user_message_id,
                "sessionID": session.id,
                "type": "text",
                "text": user_text,
                "ignored": True,
                "time": {"start": now_ms, "end": now_ms},
            }
        },
    )
    await publish_event(
        "message.updated",
        {
            "info": {
                "id": assistant_message_id,
                "sessionID": session.id,
                "role": "assistant",
                "parentID": user_message_id,
                "time": {"created": now_ms, "completed": now_ms},
                "agent": PRODUCTION_AGENT,
                "modelID": "policy",
                "providerID": "builtin",
                "finish": "stop",
            }
        },
    )
    await publish_event(
        "message.part.updated",
        {
            "part": {
                "id": assistant_part_id,
                "messageID": assistant_message_id,
                "sessionID": session.id,
                "type": "text",
                "text": assistant_text,
                "metadata": decision.metadata,
                "ignored": True,
                "time": {"start": now_ms, "end": now_ms},
            }
        },
    )


async def prepare_agent_event(
    *,
    session: SessionInfo,
    event: UserInputEvent,
    decision: ReportPolicyDecision,
    backend_synchronizer: Optional[BackendReportSynchronizer] = None,
) -> UserInputEvent:
    if decision.kind != "execute" or decision.prompt is None:
        raise ValueError("An executable report policy decision is required")
    action = decision.prompt.action
    await initialize_report_action(
        session_id=session.id,
        prompt=decision.prompt,
        synchronizer=backend_synchronizer,
    )
    task_text = (
        "[SITUATION_REPORT_PRODUCT_TASK_V1]\n"
        f"generationID: {action.generation_id}\n"
        f"operation: {action.operation}\n"
        "Load the situation-report-product Skill and complete this one restricted A1 task.\n"
        f"User instruction: {decision.prompt.text}"
    )
    return event.model_copy(
        update={
            "text": task_text,
            "parts": [{"type": "text", "text": task_text}],
            "agent": PRODUCTION_AGENT,
            "display_text": event.display_text or decision.prompt.text,
            "tools": None,
            "system": None,
            "no_reply": False,
            "mock_reply": None,
            "metadata": {
                **event.metadata,
                "situationReport": {
                    "generationID": action.generation_id,
                    "operation": action.operation,
                },
            },
        }
    )


async def run_managed_report_turn(
    *,
    session: SessionInfo,
    event: UserInputEvent,
    decision: ReportPolicyDecision,
    working_directory: str,
    generic_runner: Callable[[str, SessionInfo, UserInputEvent, str], Awaitable[None]],
    backend_synchronizer: Optional[BackendReportSynchronizer] = None,
) -> None:
    """Run one policy response or one preflighted production Agent turn."""

    expected_generation = Session.lifecycle_generation(session.id)
    workspace_dir = session_root(session.id)
    execution_lock = workspace_dir / ".locks" / "execution.lock"
    if decision.kind == "direct":
        await persist_direct_response(
            session=session,
            event=event,
            decision=decision,
            expected_generation=expected_generation,
        )
        return

    if decision.prompt is None:
        raise ValueError("Executable report decision is missing its prompt")
    action = decision.prompt.action

    async def publish_status(payload: dict) -> None:
        await publish_report_status(
            session_id=session.id,
            generation_id=action.generation_id,
            payload=payload,
        )

    current_stage = "initializing_context"
    async with async_file_lock(execution_lock):
        try:
            if action.operation == "generate":
                current_stage = "downloading_resources"
            else:
                current_stage = "checking_resources"
            await publish_status(
                {
                    "requestID": action.request_id,
                    "operation": action.operation,
                    "status": "running",
                    "stage": current_stage,
                    "progress": 5,
                    "baseBackendReportVersion": action.base_backend_report_version,
                    "error": None,
                }
            )
            prepared = await prepare_agent_event(
                session=session,
                event=event,
                decision=decision,
                backend_synchronizer=backend_synchronizer,
            )
            current_stage = "modifying" if action.operation == "modify" else "generating"

            async def publish_recovery_status(recovery_turn: int) -> None:
                nonlocal current_stage
                current_stage = "revising"
                await publish_status(
                    {
                        "requestID": action.request_id,
                        "operation": action.operation,
                        "status": "running",
                        "stage": current_stage,
                        "progress": 85,
                        "baseBackendReportVersion": action.base_backend_report_version,
                        "recoveryTurn": recovery_turn,
                        "error": None,
                    }
                )

            async with _global_model_slots():
                await _run_agent_until_candidate_ready(
                    session=session,
                    event=prepared,
                    generation_id=action.generation_id,
                    workspace_dir=workspace_dir,
                    working_directory=working_directory,
                    generic_runner=generic_runner,
                    on_recovery=publish_recovery_status,
                )
            current_stage = "validating"
            finalization = await publish_validated_candidate(
                session_id=session.id,
                generation_id=action.generation_id,
            )
            version = finalization["flocksReportVersion"]
            metadata = read_json(workspace_dir / "output" / "versions" / version / "metadata.json")
            output = dict(metadata["output"])
            output["path"] = str((workspace_dir / output["path"]).resolve())
            await publish_status(
                {
                    "requestID": action.request_id,
                    "operation": action.operation,
                    "status": "succeeded",
                    "stage": "output_ready",
                    "progress": 100,
                    "baseBackendReportVersion": action.base_backend_report_version,
                    "backendReportVersion": metadata.get("effectiveBackendReportVersion"),
                    "templateVersion": metadata.get("templateVersion"),
                    "materialVersion": metadata.get("materialVersion"),
                    "materialSnapshotID": metadata.get("materialSnapshotID"),
                    "templateSnapshotID": metadata.get("templateSnapshotID"),
                    "language": metadata.get("language"),
                    "flocksReportVersion": version,
                    "output": {
                        **output,
                        "downloadAPI": "/api/file/download",
                    },
                    "error": None,
                }
            )
        except asyncio.CancelledError:

            async def record_cancelled() -> None:
                atomic_write_json(
                    workspace_dir / "output" / "status.json",
                    {
                        "generationID": action.generation_id,
                        "operation": action.operation,
                        "status": "cancelled",
                        "stage": current_stage,
                        "progress": 100,
                        "error": None,
                        "updatedAt": utc_now(),
                    },
                )
                await publish_status(
                    {
                        "requestID": action.request_id,
                        "operation": action.operation,
                        "status": "cancelled",
                        "stage": current_stage,
                        "progress": 100,
                        "baseBackendReportVersion": action.base_backend_report_version,
                        "error": None,
                    }
                )

            await asyncio.shield(record_cancelled())
            raise
        except Exception as exc:
            atomic_write_json(
                workspace_dir / "output" / "status.json",
                {
                    "generationID": action.generation_id,
                    "operation": action.operation,
                    "status": "failed",
                    "stage": current_stage,
                    "progress": 100,
                    "error": {
                        "code": type(exc).__name__,
                        "message": str(exc),
                    },
                    "updatedAt": utc_now(),
                },
            )
            await publish_status(
                {
                    "requestID": action.request_id,
                    "operation": action.operation,
                    "status": "failed",
                    "stage": current_stage,
                    "progress": 100,
                    "baseBackendReportVersion": action.base_backend_report_version,
                    "error": {"code": type(exc).__name__, "message": str(exc)},
                }
            )
            raise
