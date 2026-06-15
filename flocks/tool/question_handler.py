"""
Real question handler that communicates with frontend via API

This handler creates questions via the API routes and waits for user responses,
replacing the default auto-accept behavior.
"""

import asyncio
from typing import List, Dict, Any
from flocks.utils.log import Log
from flocks.server.routes.question import (
    store_question_request,
    get_question_request,
    get_request_answer,
    is_request_rejected,
    clear_request_state,
)
from flocks.tool.system.question import (
    QuestionRejectedError,
    get_current_call_id,
    get_current_message_id,
    normalize_question_option,
)


log = Log.create(service="question-handler")


async def api_question_handler(
    session_id: str,
    questions: List[Dict[str, Any]]
) -> List[List[str]]:
    """
    Question handler that uses API routes for user interaction
    
    This handler:
    1. Creates questions via API (stored in memory)
    2. Waits for frontend to call reply/reject endpoints
    3. Returns user's actual answers
    
    Args:
        session_id: Session ID
        questions: List of questions with format:
            {
                "question": str,  # The question text
                "header": str,    # Short header/title
                "options": List[{"label": str, "description": str}],
                "multiple": bool  # Optional, allow multiple selections
            }
        
    Returns:
        List of answers (each answer is a list of selected option labels)
        
    Raises:
        QuestionRejectedError: If user rejects the question
        TimeoutError: If user doesn't respond within timeout
    """
    if not questions:
        return []
    
    # Get message_id and call_id from context variable (set by question_tool)
    message_id = get_current_message_id()
    call_id = get_current_call_id()
    if not message_id:
        log.warn("question_handler.no_message_id", {"session": session_id})
        message_id = "msg_unknown"
    
    # Convert questions to QuestionRequest format for TUI
    # TUI expects a single QuestionRequest with multiple questions
    question_infos = []
    for q in questions:
        
        # Convert options format for QuestionInfo
        options = []
        for opt in q.get("options", []):
            option = normalize_question_option(opt)
            if option is not None:
                options.append(option)
        
        # Build QuestionInfo
        question_type = q.get("type", "choice")
        if question_type == "choice" and not options:
            question_type = "text"
        question_info = {
            "question": str(q.get("question", "")),
            "header": str(q.get("header", "")),
            "type": question_type,
            "options": options,
            "multiple": q.get("multiple", False),
            "placeholder": q.get("placeholder", ""),
            "multiline": q.get("multiline", False),
            "custom": q.get("custom", True),
        }
        # Pass optional fields only when present
        for field in ("min_value", "max_value", "step", "accept"):
            if field in q:
                question_info[field] = q[field]
        question_infos.append(question_info)
    
    # Create a single QuestionRequest for all questions
    # This matches TUI's expected format
    from flocks.utils.id import Identifier
    request_id = Identifier.ascending("question")
    
    try:
        # Build QuestionRequest for TUI
        question_request = {
            "id": request_id,
            "sessionID": session_id,
            "questions": question_infos,
        }
        
        # Add tool info if call_id is available
        if call_id:
            question_request["tool"] = {
                "messageID": message_id,
                "callID": call_id,
            }
        
        # Store the QuestionRequest
        store_question_request(request_id, question_request)
        
        # Publish question.asked event for TUI
        try:
            from flocks.server.routes.event import publish_event
            await publish_event("question.asked", question_request)
            log.info("question.event.published", {
                "request_id": request_id,
                "session": session_id,
                "count": len(question_infos)
            })
        except Exception as e:
            log.error("question.event.publish_failed", {"error": str(e)})
            # Continue even if event publish fails - request is still stored
        
    except Exception as e:
        log.error("question.create_failed", {"error": str(e)})
        clear_request_state(request_id)
        raise
    
    # Wait for user to answer (poll the API)
    timeout = 300  # 5 minutes timeout
    poll_interval = 0.5  # Poll every 500ms
    elapsed = 0
    
    answers: List[List[str]] = []
    
    try:
        while elapsed < timeout:
            # Check if rejected
            if is_request_rejected(request_id):
                log.info("question.rejected_by_user", {"request_id": request_id})
                raise QuestionRejectedError()
            
            # Check if answered
            answer = get_request_answer(request_id)
            if answer is not None:
                answers = answer
                log.info("question.all_answered", {
                    "count": len(answers),
                    "session": session_id
                })
                break
            
            # Check if request still exists
            if get_question_request(request_id) is None:
                # Request removed but no answer stored - treat as rejected
                log.warn("question.removed_without_answer", {"request_id": request_id})
                raise QuestionRejectedError()
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        
        if not answers:
            # Timeout
            log.warn("question.timeout", {
                "session": session_id,
                "elapsed": elapsed
            })
            raise TimeoutError(
                f"Question timed out after {timeout} seconds waiting for user response"
            )
        
        # Clean up state
        clear_request_state(request_id)
        
        return answers
        
    except Exception as e:
        # Clean up on any error
        clear_request_state(request_id)
        raise


# Singleton pattern - only set handler once
_handler_set = False


def setup_api_question_handler() -> None:
    """
    Setup the API question handler
    
    Call this once during application startup to replace the default
    auto-accept behavior with real user interaction.
    """
    global _handler_set
    
    if _handler_set:
        log.debug("question_handler.already_set")
        return
    
    from flocks.tool.system.question import set_question_handler
    
    set_question_handler(api_question_handler)
    _handler_set = True
    
    log.info("question_handler.configured", {
        "handler": "api_question_handler"
    })
