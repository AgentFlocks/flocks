"""
Question Tool - User interaction and confirmation

Provides a way for agents to ask questions to users and receive answers.
Supports multiple choice questions with custom options.
"""

import asyncio
from contextvars import ContextVar
from typing import List, Dict, Any, Optional, Callable, Awaitable

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.utils.log import Log


log = Log.create(service="tool.question")


# Context variable to pass message_id to handler
_current_message_id: ContextVar[Optional[str]] = ContextVar('current_message_id', default=None)
_current_call_id: ContextVar[Optional[str]] = ContextVar('current_call_id', default=None)


def get_current_message_id() -> Optional[str]:
    """
    Get the current message ID from context
    
    This is used by question handlers to get the message ID associated
    with the current question tool call.
    
    Returns:
        Message ID if available, None otherwise
    """
    return _current_message_id.get()


def get_current_call_id() -> Optional[str]:
    """
    Get the current call ID from context
    
    This is used by question handlers to get the call ID associated
    with the current question tool call.
    
    Returns:
        Call ID if available, None otherwise
    """
    return _current_call_id.get()


# Question callback type - should be set by the application
QuestionCallback = Callable[[str, List[Dict[str, Any]]], Awaitable[List[List[str]]]]

# Global question handler (to be set by the application)
_question_handler: Optional[QuestionCallback] = None


def set_question_handler(handler: QuestionCallback) -> None:
    """
    Set the global question handler
    
    The handler should be an async function that:
    - Takes session_id and list of questions
    - Returns list of answers (each answer is a list of selected option labels)
    
    Args:
        handler: Question handler function
    """
    global _question_handler
    _question_handler = handler


class QuestionRejectedError(Exception):
    """Raised when user rejects/declines a question"""
    pass


DESCRIPTION = """Ask the user a question and wait for their response.

Use this tool when you need to:
- Confirm before making significant changes
- Get user preference between multiple options
- Clarify ambiguous instructions

Question format:
- Each question has a text prompt
- Optional header for context
- List of options for the user to choose from
- Options have label and optional description

The user's answers will be returned for you to continue with."""


_OPTION_LABEL_KEYS = ("label", "text", "title", "name", "value", "id", "key")
_OPTION_DESCRIPTION_KEYS = ("description", "desc", "subtitle", "detail", "details")


def _first_non_empty_string(data: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_question_option(opt: Any) -> Optional[Dict[str, str]]:
    """Normalize LLM-produced choice options into the UI's label/description shape."""
    if isinstance(opt, str):
        label = opt.strip()
        return {"label": label, "description": ""} if label else None

    if not isinstance(opt, dict):
        label = str(opt).strip() if opt is not None else ""
        return {"label": label, "description": ""} if label else None

    label = _first_non_empty_string(opt, _OPTION_LABEL_KEYS)
    description = _first_non_empty_string(opt, _OPTION_DESCRIPTION_KEYS)
    if not label and description:
        label, description = description, ""
    if not label:
        return None
    return {"label": label, "description": description}


def _format_channel_question_text(questions: List[Dict[str, Any]]) -> str:
    """Render normalized questions as plain text for IM channels."""
    blocks: list[str] = []
    for idx, q in enumerate(questions, start=1):
        header = str(q.get("header") or "").strip()
        question = str(q.get("question") or "").strip()
        qtype = str(q.get("type") or "choice")
        options = q.get("options") or []

        lines: list[str] = []
        if header:
            lines.append(header)
        prefix = f"{idx}. " if len(questions) > 1 else ""
        lines.append(f"{prefix}{question}")

        if qtype in {"choice", "confirm"} and options:
            for opt_idx, opt in enumerate(options, start=1):
                label = str(opt.get("label", "")).strip()
                description = str(opt.get("description", "") or "").strip()
                if description:
                    lines.append(f"{opt_idx}. {label} - {description}")
                else:
                    lines.append(f"{opt_idx}. {label}")
            lines.append("请回复选项序号、选项文本，或直接补充你的答案。")
        else:
            lines.append("请直接回复你的答案。")

        blocks.append("\n".join(line for line in lines if line))

    return "\n\n".join(blocks)


async def _send_channel_question_if_applicable(
    ctx: ToolContext,
    questions: List[Dict[str, Any]],
) -> ToolResult | None:
    """Send the question as a plain text IM message for channel sessions.

    Channel sessions do not have the Web UI question-answer transport. Sending
    a text prompt and returning immediately avoids waiting until timeout.
    """
    try:
        from flocks.channel.inbound.session_binding import SessionBindingService
        from flocks.channel.outbound.deliver import OutboundDelivery
        from flocks.channel.base import OutboundContext

        svc = SessionBindingService()
        bindings = await svc.get_bindings_by_session(ctx.session_id)
        if not bindings:
            return None

        text = _format_channel_question_text(questions)
        for binding in bindings:
            await OutboundDelivery.deliver(
                OutboundContext(
                    channel_id=binding.channel_id,
                    account_id=binding.account_id,
                    to=binding.chat_id,
                    text=text,
                    thread_id=binding.thread_id,
                ),
                session_id=binding.session_id,
            )

        return ToolResult(
            success=True,
            output=(
                "Question sent to the IM channel as plain text. "
                "Do not continue the dependent action until the user replies in a new message."
            ),
            title="Question sent to channel",
            metadata={
                "deferred": True,
                "channel_session": True,
                "bindings": [
                    {
                        "channel_id": b.channel_id,
                        "chat_type": b.chat_type.value if b.chat_type else None,
                        "chat_id": b.chat_id,
                        "session_id": b.session_id,
                    }
                    for b in bindings
                ],
            },
        )
    except Exception as e:
        log.warning("question.channel_send_failed", {
            "session_id": ctx.session_id,
            "error": str(e),
        })
        return ToolResult(
            success=False,
            error=f"Failed to send question to channel: {e}",
        )


async def default_question_handler(
    session_id: str,
    questions: List[Dict[str, Any]]
) -> List[List[str]]:
    """
    Default question handler that auto-accepts
    
    In production, this would be replaced with actual user interaction.
    
    Args:
        session_id: Session ID
        questions: List of questions
        
    Returns:
        List of answers (first option selected for each)
    """
    answers = []
    for q in questions:
        options = q.get("options", [])
        if options:
            # Auto-select first option
            answers.append([options[0].get("label", "Yes")])
        else:
            answers.append(["Yes"])
    return answers


@ToolRegistry.register_function(
    name="question",
    description=DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="questions",
            type=ParameterType.ARRAY,
            description="Array of questions to ask the user",
            required=True,
            json_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Question text prompt",
                        },
                        "header": {
                            "type": "string",
                            "description": "Optional header/context for the question",
                        },
                        "type": {
                            "type": "string",
                            "description": (
                                "Input type for the question. "
                                "'choice' (default): select from options (single or multiple); "
                                "'text': free-form text input (single or multi-line); "
                                "'number': numeric input with optional range; "
                                "'file': file upload (content returned to agent); "
                                "'confirm': yes/no confirmation buttons; "
                                "'password': masked text input for sensitive data."
                            ),
                            "enum": ["choice", "text", "number", "file", "confirm", "password"],
                        },
                        "options": {
                            "type": "array",
                            "description": "Options for 'choice' type questions",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "required": ["label"],
                                        "additionalProperties": False,
                                    },
                                ],
                            },
                        },
                        "multiple": {
                            "type": "boolean",
                            "description": "For 'choice' type: allow selecting multiple options",
                        },
                        "custom": {
                            "type": "boolean",
                            "description": (
                                "For 'choice' type: allow a custom Other answer option. "
                                "Defaults to true."
                            ),
                        },
                        "placeholder": {
                            "type": "string",
                            "description": "Placeholder/hint text for text, number, password, file inputs",
                        },
                        "multiline": {
                            "type": "boolean",
                            "description": "For 'text' type: use textarea (multi-line input)",
                        },
                        "min_value": {
                            "type": "number",
                            "description": "For 'number' type: minimum allowed value",
                        },
                        "max_value": {
                            "type": "number",
                            "description": "For 'number' type: maximum allowed value",
                        },
                        "step": {
                            "type": "number",
                            "description": "For 'number' type: step increment",
                        },
                        "accept": {
                            "type": "string",
                            "description": "For 'file' type: accepted file extensions, e.g. '.txt,.log,.csv'",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": True,
                },
            },
        ),
    ]
)
async def question_tool(
    ctx: ToolContext,
    questions: List[Dict[str, Any]],
) -> ToolResult:
    """
    Ask questions to the user
    
    Args:
        ctx: Tool context
        questions: List of question objects with question, header, options fields
        
    Returns:
        ToolResult with user's answers
    """
    if not questions:
        return ToolResult(
            success=False,
            error="At least one question is required"
        )
    
    # Normalize questions
    normalized_questions = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        
        normalized = {
            "question": str(q.get("question", "")),
            "header": q.get("header", ""),
            "type": q.get("type", "choice"),
            "options": [],
            "multiple": q.get("multiple", False),
            "custom": q.get("custom", True),
            "placeholder": q.get("placeholder", ""),
            "multiline": q.get("multiline", False),
        }
        # Optional numeric range fields
        if "min_value" in q:
            normalized["min_value"] = q["min_value"]
        if "max_value" in q:
            normalized["max_value"] = q["max_value"]
        if "step" in q:
            normalized["step"] = q["step"]
        if "accept" in q:
            normalized["accept"] = q["accept"]

        options = q.get("options", [])
        for opt in options:
            option = normalize_question_option(opt)
            if option is not None:
                normalized["options"].append(option)

        if normalized["type"] == "choice" and not normalized["options"]:
            normalized["type"] = "text"
        
        normalized_questions.append(normalized)
    
    if not normalized_questions:
        return ToolResult(
            success=False,
            error="No valid questions provided"
        )

    channel_result = await _send_channel_question_if_applicable(ctx, normalized_questions)
    if channel_result is not None:
        return channel_result
    
    # Get handler
    handler = _question_handler or default_question_handler
    
    try:
        # Set message_id and call_id in context for handler to use
        _current_message_id.set(ctx.message_id)
        _current_call_id.set(ctx.call_id)
        
        # Ask questions
        answers = await handler(ctx.session_id, normalized_questions)
        
        # Format output
        def format_answer(answer: Optional[List[str]]) -> str:
            if not answer:
                return "Unanswered"
            return ", ".join(answer)
        
        formatted = ", ".join([
            f'"{q["question"]}"="{format_answer(answers[i] if i < len(answers) else None)}"'
            for i, q in enumerate(normalized_questions)
        ])
        
        output = f"User has answered your questions: {formatted}. You can now continue with the user's answers in mind."
        try:
            from flocks.session.goal import GoalManager

            await GoalManager.record_initial_clarification(
                ctx.session_id,
                normalized_questions,
                answers,
                message_id=ctx.message_id,
                call_id=ctx.call_id,
            )
        except Exception as e:
            log.warn("question.goal_clarification_record_failed", {
                "session_id": ctx.session_id,
                "error": str(e),
            })
        
        return ToolResult(
            success=True,
            output=output,
            title=f"Asked {len(normalized_questions)} question{'s' if len(normalized_questions) > 1 else ''}",
            metadata={
                "answers": answers
            }
        )
        
    except QuestionRejectedError:
        return ToolResult(
            success=False,
            error="User rejected the question"
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to get answers: {str(e)}"
        )
