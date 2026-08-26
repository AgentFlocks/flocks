---
name: n8n-workflow-builder
category: automation
description: 生成、校验、发布、测试并调试 n8n 工作流。用于用户要求 Flocks 创建或修复最终在 n8n 上运行的 workflow 时。
---

# n8n Workflow Builder

Use this skill when the user wants Flocks to create, validate, publish, test, or debug an n8n workflow. Treat the final runtime as n8n, not the Flocks workflow engine.

## Required Flow

1. Clarify only business intent that is impossible to infer safely: automation goal, trigger, sample input, expected output, and high-risk side-effect boundaries. Do not ask the user how to bridge Flocks MCP/skills/tools/agents or Flocks wrapper services into n8n.
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
- Target n8n compatibility is `2.35.4` Public API v1. The API key must have the needed scopes before publish: `workflow:create`, `workflow:list`, `workflow:read`, `workflow:activate`, plus `credential:list`, `credential:read`, and `credential:create` when `credentialRequirements` is present.
- The generated n8n workflow must be runtime-independent from Flocks. Do not use Flocks MCP tools, Flocks skills, Flocks agents, Flocks tools, Flocks workflow endpoints, Flocks webhooks, a Flocks-provided public HTTP wrapper, or any local Flocks callback as a business node.
- When the user asks for a capability that exists in Flocks/MCP/skill/tool/agent, migrate the capability completely into n8n-native nodes or a direct non-Flocks external service API. For example, IOC enrichment should become an HTTP Request/API workflow in n8n, not a call back into Flocks MCP or a Flocks wrapper API.
- Do not ask the user to provide service API keys when Flocks already has the needed secret or credential reference. Use `credentialRequirements` to declare which Flocks secret should create or reuse which n8n credential.
- If the workflow cannot be safely generated because required business data, secrets, credentials, network reachability, or n8n permissions are missing, ask for the missing prerequisite and stop. If the prerequisite is still unavailable, do not publish or activate a broken workflow. Return a precise blocker such as `缺少密钥 THREATBOOK_API_KEY，请先在 Flocks 密钥配置中添加` or `n8n API key 缺少创建 credential 的权限`。
- Do not put `{secret:...}`, `{{secrets.NAME}}`, plaintext keys, or bearer tokens into n8n workflow JSON. `{secret}` is allowed only inside `credentialRequirements[].data`, where the publisher replaces it while creating n8n credentials.
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
- Kafka Trigger

For unsupported n8n nodes, either ask to reduce scope to supported primitives or produce a draft with an explicit unsupported-node warning instead of claiming it is production-ready.

## Capability Mapping Policy

Use this policy before asking any implementation-choice question:

- `kafka.consume` -> Kafka Trigger with `credentialRef` pointing to an n8n Kafka credential, plus `credentialRequirements` if Flocks has the Kafka secret material.
- `threatbook.ioc_lookup` -> HTTP Request node calling the ThreatBook HTTP API with an n8n HTTP/header/query credential, plus `credentialRequirements` that references the known Flocks secret id. Never embed the secret in workflow JSON.
- `generic.rest_api` -> HTTP Request with n8n credential or expression-safe auth.
- `format/normalize/enrich/route` -> Code, Set, and IF nodes.
- `webhook.response` -> Respond to Webhook, only for webhook-triggered workflows.

If no complete n8n-native or direct non-Flocks external API mapping exists, stop before IR generation or publish. Ask the user to confirm terminating the workflow creation because the requested Flocks-only capability cannot be migrated to an autonomous n8n runtime. Do not offer a Flocks MCP/webhook/API bridge.

## IR Pattern

Build IR like this:

```json
{
  "name": "flocks-test-alert-triage",
  "credentialRequirements": [
    {
      "name": "ThreatBook API",
      "type": "httpQueryAuth",
      "secretRef": "THREATBOOK_API_KEY",
      "data": {
        "name": "apikey",
        "value": "{secret}"
      }
    }
  ],
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
