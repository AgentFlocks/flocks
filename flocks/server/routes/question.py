"""
Question routes for UI clients.

Provides /question endpoints for handling agent questions to user.

- POST /question/{id}/reply   - Reply to a question
- POST /question/{id}/reject  - Reject a question
"""

from typing import Dict, List, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="question-routes")


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_question_requests: Dict[str, dict] = {}
_request_answers: Dict[str, List[List[str]]] = {}
_request_rejected: set = set()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class QuestionOption(BaseModel):
    id: str = ""
    label: str


class QuestionRequestReply(BaseModel):
    """Batch reply: one answer-list per question."""
    answers: List[List[str]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def store_question_request(request_id: str, question_request: dict) -> None:
    _question_requests[request_id] = question_request


def get_question_request(request_id: str) -> Optional[dict]:
    return _question_requests.get(request_id)


def list_question_requests(session_id: Optional[str] = None) -> List[dict]:
    """List pending question requests, optionally filtered by session."""
    requests = list(_question_requests.values())
    if session_id is None:
        return requests
    return [req for req in requests if req.get("sessionID") == session_id]


def get_request_answer(request_id: str) -> Optional[List[List[str]]]:
    return _request_answers.get(request_id)


def is_request_rejected(request_id: str) -> bool:
    return request_id in _request_rejected


def clear_request_state(request_id: str) -> None:
    _question_requests.pop(request_id, None)
    _request_answers.pop(request_id, None)
    _request_rejected.discard(request_id)


async def reject_session_questions(session_id: str) -> int:
    """Reject all pending question requests for a session.

    Called from abort_session so that the question_handler polling loop
    detects the rejection and unblocks cleanly instead of timing out.
    Also publishes question.rejected SSE events so that frontends (WebUI /
    TUI) can clear their pending question UI without waiting for a timeout.

    Returns the number of requests rejected.
    """
    from flocks.server.routes.event import publish_event

    rejected = 0
    for request_id, req in list(_question_requests.items()):
        if req.get("sessionID") == session_id:
            _request_rejected.add(request_id)
            del _question_requests[request_id]
            rejected += 1
            log.info("question.request.auto_rejected", {
                "request_id": request_id,
                "session_id": session_id,
                "reason": "session_aborted",
            })
            try:
                await publish_event("question.rejected", {
                    "sessionID": session_id,
                    "requestID": request_id,
                })
            except Exception as e:
                log.error("question.auto_rejected.event.failed", {"error": str(e)})
    return rejected


def has_pending_questions(session_id: str) -> bool:
    """Return True if the session has any pending question requests."""
    return any(
        req.get("sessionID") == session_id
        for req in _question_requests.values()
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "/session/{session_id}/pending",
    summary="List pending questions for a session",
    description="Return all unanswered Question requests for the given session",
)
async def get_pending_question_requests(session_id: str) -> List[Dict[str, Any]]:
    return list_question_requests(session_id)


@router.post(
    "/{request_id}/reply",
    summary="Reply to question request",
    description="Reply to a QuestionRequest with answers for all questions",
)
async def reply_question_request(
    request_id: str,
    request: QuestionRequestReply,
) -> Dict[str, Any]:
    if request_id not in _question_requests:
        raise HTTPException(status_code=404, detail="Question request not found")

    log.info("question.request.reply", {
        "request_id": request_id,
        "answer_count": len(request.answers),
    })
    _request_answers[request_id] = request.answers

    try:
        from flocks.server.routes.event import publish_event
        question_request = _question_requests[request_id]
        await publish_event("question.replied", {
            "sessionID": question_request.get("sessionID", ""),
            "requestID": request_id,
            "answers": request.answers,
        })
    except Exception as e:
        log.error("question.replied.event.failed", {"error": str(e)})

    del _question_requests[request_id]
    return {"success": True}


@router.post(
    "/{request_id}/reject",
    summary="Reject question request",
    description="Reject a QuestionRequest (user doesn't want to answer)",
)
async def reject_question_request(request_id: str) -> Dict[str, bool]:
    if request_id not in _question_requests:
        raise HTTPException(status_code=404, detail="Question request not found")

    log.info("question.request.reject", {"request_id": request_id})
    _request_rejected.add(request_id)

    try:
        from flocks.server.routes.event import publish_event
        question_request = _question_requests[request_id]
        await publish_event("question.rejected", {
            "sessionID": question_request.get("sessionID", ""),
            "requestID": request_id,
        })
    except Exception as e:
        log.error("question.rejected.event.failed", {"error": str(e)})

    del _question_requests[request_id]
    return {"success": True}


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "router",
    "store_question_request",
    "get_question_request",
    "list_question_requests",
    "get_request_answer",
    "is_request_rejected",
    "clear_request_state",
    "reject_session_questions",
    "has_pending_questions",
    "QuestionOption",
    "QuestionRequestReply",
]
