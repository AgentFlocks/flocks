---
name: n8n-workflow-builder
category: automation
description: 生成、校验、发布、测试并调试 n8n 工作流。用于用户要求 Flocks 创建或修复最终在 n8n 上运行的 workflow 时。
---

# n8n Workflow Builder

Use this skill when the user wants Flocks to create, validate, publish, test, or debug an n8n workflow. Treat the final runtime as n8n, not the Flocks workflow engine.

## Required Flow

1. Clarify the automation goal, trigger, sample input, expected output, and side-effect boundaries.
2. Build a stable n8n IR first. Do not ask the model to directly author native n8n JSON.
3. Call `n8n_workflow_render` to render native n8n workflow JSON.
4. Call `n8n_workflow_lint` before any publish call.
5. Publish only a test workflow by default. Use a `flocks-test-` or otherwise clearly test-scoped name unless the user explicitly requests production.
6. Activate the test workflow only when a test must run.
7. Run webhook tests through `n8n_test_run` or `n8n_webhook_call`.
8. On failure, collect lint issues, API errors, webhook response, and execution details. Use `n8n_repair_context` before passing context into an LLM repair step.
9. Iterate at most 8 times by default.
10. Write generated JSON, iteration JSONL, and reports under `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`.

## Safety Rules

- Never write API keys, tokens, passwords, cookies, or Authorization headers into workflow files, reports, prompts, or generated n8n JSON.
- Prefer `api_key_secret_ref` such as `N8N_API_KEY`; direct `api_key` inputs are transient only.
- Do not modify or activate production workflows unless the user explicitly confirms that scope.
- Do not remove core business steps just to pass one sample test.
- Do not hard-code a test fixture response unless the user's requested workflow is intentionally constant.
- Use mock endpoints or dry-run behavior for Slack, email, databases, ticketing, and other high-side-effect systems unless the user opts into real effects.

## Supported MVP Nodes

The stable renderer/linter currently supports:

- Webhook
- Code
- Set
- IF
- HTTP Request
- Respond to Webhook
- NoOp

For unsupported n8n nodes, either ask to reduce scope to supported primitives or produce a draft with an explicit unsupported-node warning instead of claiming it is production-ready.

## IR Pattern

Build IR like this:

```json
{
  "name": "flocks-test-alert-triage",
  "trigger": {
    "type": "webhook",
    "method": "POST",
    "path": "flocks-test-alert-triage"
  },
  "steps": [
    {
      "id": "normalize",
      "kind": "code",
      "js_code": "const body = $input.first().json.body || {}; return [{ json: { alert: body, source: 'n8n' } }];"
    },
    {
      "id": "respond",
      "kind": "respond_to_webhook",
      "response_body": "={{ $json }}"
    }
  ],
  "tests": [
    {
      "name": "normal input",
      "input": { "name": "Alice" },
      "expect": {
        "status": 200,
        "jsonContains": { "source": "n8n" }
      }
    }
  ]
}
```

## Completion Criteria

Only call the workflow ready when:

- Native n8n JSON renders successfully.
- Static lint has no errors.
- n8n API create/update and activate succeeded for a test workflow.
- Runtime tests passed, or the user explicitly accepts a draft.
- The final response includes workflow id, editor URL, webhook URL if applicable, generated JSON path, and test report path.

