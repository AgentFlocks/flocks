# Lifecycle Hooks

Flocks loads Python lifecycle-hook plugins from:

- `~/.flocks/plugins/hooks/`
- `<workspace>/.flocks/plugins/hooks/`

A plugin exports a module-level `HOOKS` list containing `HookBase` instances.
Handlers run in registration order, use the pipeline timeout and failure-isolation
policy, and share one mutable `HookContext` per stage.

```python
from flocks.hooks.pipeline import HookBase, HookContext


class AuditHook(HookBase):
    name = "audit-hook"
    order = 100

    async def tool_before(self, ctx: HookContext) -> None:
        tool = ctx.input["tool"]
        if tool["name"] == "bash" and "rm -rf" in tool["input"].get("command", ""):
            ctx.output["decision"] = "block"
            ctx.output["reason"] = "Destructive command rejected"


HOOKS = [AuditHook()]
```

Only methods overridden by the concrete hook class count as active handlers.
The no-op implementations inherited from `HookBase` do not enable a stage.

## Lifecycle

| Hook method | Stage | Trigger |
| --- | --- | --- |
| `user_prompt_submit` | `user.prompt.submit` | Once before each real, non-synthetic user message is processed |
| `session_start` | `session.start` | Once after the first system prompt is built and before the first LLM request |
| `llm_before` | `llm.call.before` | Before every LLM request |
| `llm_after` | `llm.call.after` | After every LLM request |
| `tool_before` | `tool.execute.before` | Before sandbox, permission, and tool-handler execution |
| `tool_after` | `tool.execute.after` | After every tool invocation that entered `tool_before` reaches a final state |
| `turn_finish` | `turn.finish` | After a final assistant message with `finish="stop"` is persisted and the main Agent is ready to wait |
| `subagent_start` | `subagent.start` | Before a new or resumed `delegate_task` child Session runs |
| `subagent_stop` | `subagent.stop` | After the child Session completes, fails, or is interrupted |
| `event` | `event` | Existing event-pipeline hook |
| `channel_inbound` | `channel.inbound` | Existing inbound channel hook |
| `channel_outbound_before` | `channel.outbound.before` | Existing pre-send channel hook |
| `channel_outbound_after` | `channel.outbound.after` | Existing post-send channel hook |

The independent `HookRegistry` event API is unchanged. Lifecycle methods map
directly to `HookPipeline` stages and are not forwarded through that registry.

## UserPromptSubmit

The input contains `sessionID`, `workspace`, `agent`, `model`, `messageID`, and
`prompt`. It is emitted once for a real user turn; tool loops, LLM retries,
model failover, Goal continuations, and TurnFinish continuations do not emit it
again.

A handler can add temporary context to the current turn without changing the
stored user message:

```python
async def user_prompt_submit(self, ctx: HookContext) -> None:
    ctx.output["additionalContext"] = "Use the current release branch."
```

## Tool hooks

`tool_before` receives:

```python
{
    "sessionID": "...",
    "workspace": "...",
    "agent": "rex",
    "tool": {
        "name": "bash",
        "input": {"command": "pwd"},
        "callID": "...",
    },
}
```

A handler may mutate `ctx.input["tool"]["input"]`. To prevent execution, set
`ctx.output["decision"] = "block"` and provide a non-empty
`ctx.output["reason"]`.

`tool_after` receives the final `tool` input plus `result`, `status`,
`error`, and `durationMs`. Status is one of `completed`, `blocked`, `error`, or
`interrupted`. Hook blocks, sandbox blocks, handler errors, and cancellation
all produce a matching post hook. A handler may replace the final
`ToolResult` by assigning a serialized result dictionary to
`ctx.output["result"]`.

## TurnFinish

`turn_finish` represents the end of one user turn, not Session destruction. It
is not emitted for tool-call responses, API errors, aborts, retries, queued
user prompts, or when internal Goal logic continues the Agent loop.

The input includes `sessionID`, `workspace`, `agent`, `model`, `step`, the
turn's `userMessage`, the final `assistantMessage`, `finishReason="stop"`, and
`stopHookActive`.

Normally the hook returns no decision. To ask the Agent to continue:

```python
async def turn_finish(self, ctx: HookContext) -> None:
    if not tests_were_run(ctx.input["assistantMessage"]["content"]):
        ctx.output["decision"] = "block"
        ctx.output["reason"] = "Run the relevant tests before finishing."
```

Flocks stores the reason as a synthetic user message with
`turnFinishContinuation` metadata and continues the current loop. The next
final response emits `turn_finish` again with `stopHookActive=True`. At the
Agent step limit the hook still observes the final response, but continuation
decisions are ignored to prevent an infinite loop. A queued real user message
always takes priority.

## Session and subagent hooks

`session_start` contains the Session, workspace, Agent, and selected model. It
fires once for a new Session even when the first LLM request retries or fails
over.

`subagent_start` and `subagent_stop` contain the parent and child Session IDs,
parent message ID, child Agent type, prompt, description, workspace, and a
`resumed` flag. The stop payload also contains `status`, `durationMs`,
`summary`, and `error`. These hooks are observers; use `tool_before` to block
the `delegate_task` tool itself.
