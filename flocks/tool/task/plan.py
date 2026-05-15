"""
Plan Tools - Plan mode switching

Provides tools for entering and exiting plan mode:
- plan_enter: Switch to plan agent for research and planning
- plan_exit: Switch back to build agent after planning is complete
"""

from typing import Optional

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolResult, ToolContext
)
from flocks.tool.system.question import QuestionRejectedError
from flocks.utils.log import Log


log = Log.create(service="tool.plan")


# Default plan file path
DEFAULT_PLAN_FILE = "PLAN.md"


PLAN_ENTER_DESCRIPTION = """Switch to plan mode to create a detailed implementation plan.

Use this tool when:
- The task is complex and requires research first
- You need to explore the codebase before making changes
- The user asks for a plan or wants to discuss approach
- There are significant architectural decisions to make

In plan mode:
- You will operate as the "plan" agent
- Focus on research, analysis, and creating a plan document
- Changes to non-plan files are restricted
- Use plan_exit when the plan is complete and you're ready to return to build mode"""


PLAN_EXIT_DESCRIPTION = """Exit plan mode and return to build mode.

Use this tool when:
- The plan document is complete
- You're ready to start implementing

This will:
- Switch to the "rex" agent
- Allow full file editing capabilities"""


# Callback for agent switching (to be set by application)
_agent_switch_callback: Optional[callable] = None


def set_agent_switch_callback(callback: callable) -> None:
    """
    Set the callback for agent switching
    
    Args:
        callback: Function(session_id, from_agent, to_agent, message) -> None
    """
    global _agent_switch_callback
    _agent_switch_callback = callback


async def _ask_user(ctx: ToolContext, question: str, header: str, options: list) -> str:
    """
    Ask user a question using the question system
    
    Args:
        ctx: Tool context
        question: Question text
        header: Header text
        options: List of option dicts with label and description
        
    Returns:
        Selected option label
    """
    from flocks.tool.system.question import (
        _question_handler,
        default_question_handler,
        _current_call_id,
        _current_message_id,
    )
    
    handler = _question_handler or default_question_handler
    
    questions = [{
        "question": question,
        "header": header,
        "options": options
    }]
    
    # Set message_id in context for handler to use
    _current_message_id.set(ctx.message_id)
    _current_call_id.set(ctx.call_id)
    
    answers = await handler(ctx.session_id, questions)
    
    # Safe access to nested list to avoid index out of range
    if answers and len(answers) > 0 and answers[0] and len(answers[0]) > 0:
        return answers[0][0]
    return "No"


@ToolRegistry.register_function(
    name="plan_enter",
    description=PLAN_ENTER_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[]
)
async def plan_enter_tool(
    ctx: ToolContext,
) -> ToolResult:
    """
    Enter plan mode
    
    Args:
        ctx: Tool context
        
    Returns:
        ToolResult with switch status
    """
    plan_file = DEFAULT_PLAN_FILE
    
    # Ask user for confirmation
    try:
        answer = await _ask_user(
            ctx,
            question=f"Would you like to switch to the plan agent and create a plan saved to {plan_file}?",
            header="Plan Mode",
            options=[
                {"label": "Yes", "description": "Switch to plan agent for research and planning"},
                {"label": "No", "description": "Stay with build agent to continue making changes"}
            ]
        )
        
        if answer == "No":
            raise QuestionRejectedError()
            
    except QuestionRejectedError:
        return ToolResult(
            success=False,
            error="User declined to enter plan mode"
        )
    
    # Notify agent switch
    if _agent_switch_callback:
        try:
            await _agent_switch_callback(
                ctx.session_id,
                ctx.agent,
                "plan",
                "User has requested to enter plan mode. Switch to plan mode and begin planning."
            )
        except Exception as e:
            log.warn("plan_enter.callback_failed", {"error": str(e)})
    
    return ToolResult(
        success=True,
        output=f"User confirmed to switch to plan mode. A new message has been created to switch you to plan mode. The plan file will be at {plan_file}. Begin planning.",
        title="Switching to plan agent",
        metadata={}
    )


@ToolRegistry.register_function(
    name="plan_exit",
    description=PLAN_EXIT_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[]
)
async def plan_exit_tool(
    ctx: ToolContext,
) -> ToolResult:
    """
    Exit plan mode
    
    Args:
        ctx: Tool context
        
    Returns:
        ToolResult with switch status
    """
    plan_file = DEFAULT_PLAN_FILE
    
    # Notify agent switch
    if _agent_switch_callback:
        try:
            await _agent_switch_callback(
                ctx.session_id,
                ctx.agent,
                "rex",
                f"The plan at {plan_file} is complete. Switch back to build mode and execute it."
            )
        except Exception as e:
            log.warn("plan_exit.callback_failed", {"error": str(e)})
    
    return ToolResult(
        success=True,
        output="Exited plan mode and switched back to rex agent. Continue by executing the plan.",
        title="Switching to rex agent",
        metadata={}
    )
