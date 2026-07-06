---
name: workflow-config-guide
category: system
ui_hidden: true
description: 配置现有 Flocks 工作流的发布、集成、触发器和发布配置模板；本 skill 只定义交互协议，具体配置问题必须来自工作流目录内的 guide.md
---

# Workflow Config Guide

Use this skill when the user asks to configure, publish, integrate, deploy, or validate an existing Flocks workflow, especially when the task involves publish configuration templates, `config.json` import/fallback, API publishing, Syslog/Kafka/Webhook/Schedule triggers, file input, downstream output, sample validation, or a first-time deployment guide.

This skill is a protocol layer only. It must not be used as the source of workflow-specific configuration questions or defaults. For every existing workflow, the source of truth for configuration details is the workflow-local `guide.md` file in the same directory as `workflow.md`, `workflow.json`, and optional `config.json`.

Do not use this skill to create a brand-new workflow from scratch. Use `workflow-builder` for workflow design and generation, then return to this skill when the workflow already exists and needs runtime configuration.

## Quick Start

1. Identify the current workflow directory. Prefer the explicit path in the user request; otherwise inspect the active workflow context and project/user workflow roots.
2. Read the workflow-local `guide.md` first. If it is missing or too thin to answer the user's request, stop and use the `question` tool to ask whether to generate or repair `guide.md` from `workflow.md`, `workflow.json`, and `config.json`.
3. Read the workflow files that exist: `workflow.json`, `workflow.md`, optional legacy `config.json`, and `meta.json`. Treat `workflow_config_manage(action="get", workflow_id="<workflow_id>")` as the canonical publish-template read path. If no stored template exists, use `workflow_config_manage(action="sync", workflow_id="<workflow_id>")` to let the backend migrate the fallback `config.json`.
4. Summarize the current configurable capabilities in plain language, using `guide.md` as the source for workflow-specific modes, defaults, sample requirements, validation, and recommended question order.
5. When any user decision, missing value, preference, or confirmation is needed, call the `question` tool. Do not ask configuration questions in ordinary assistant text.
6. Before changing the publish template, use `workflow_config_manage(action="diff", workflow_id="<workflow_id>", config={...})` and show its unified diff against the canonical backend config, then call the `question` tool for explicit confirmation. That single approval authorizes applying the shown diff through `workflow_config_manage(action="put", ...)`; do not ask a second write-confirmation question for the same diff.
7. After applying changes, validate JSON syntax and run the lightest useful workflow/config smoke test available.
8. End with a concise report in chat and save a timestamped report under `~/.flocks/workspace/outputs/<today>/`, computing `<today>` at execution time.

## Workflow-local Guide Contract

Each workflow that can be configured by Rex should include:

```text
<workflow_dir>/
  workflow.md
  workflow.json
  config.json        # optional import/fallback publish template
  guide.md          # workflow-specific configuration guide
```

`guide.md` must answer these questions for this workflow, in the workflow's own domain language:

- What problem the workflow solves and which runtime paths are supported.
- What information must be collected from the user before configuration can be applied.
- Recommended defaults and safe fallback behavior.
- Which values are runtime state in Storage/SQL rather than editable template fields.
- Sample input requirements and the lightest validation method.

`guide.md` should not contain a UI button table. If a user clicks a guide shortcut, treat the shortcut label as an intent hint, read `guide.md`, semantically extract the relevant guidance, defaults, constraints, examples, and validation rules, then ask the single next useful question with the `question` tool. If no relevant guidance exists, say that the workflow guide is missing that detail and ask whether to repair `guide.md`.

## Configuration Contract

Treat the publish configuration template as a workflow runtime/publish template, not as a second copy of workflow code. The canonical template is stored in Storage/SQL and must be read or changed through the built-in `workflow_config_manage` tool. A workflow-local `config.json` is only an import/fallback artifact: when the backend has no stored template, `workflow_config_manage(action="sync", ...)` may read `config.json` once and migrate that content into Storage/SQL.

- If the stored template declares only API publishing, the publish page should expose only API publish controls.
- If the stored template declares only Syslog, Kafka, Webhook, or Schedule triggers, the publish page should expose only that trigger's start/stop or enable/disable controls.
- Do not store plaintext secrets in the template; store booleans such as `apiKeyConfigured` or secret-manager references.
- Never edit workflow-local `config.json` to apply a publish, input, or trigger configuration. It is a fallback import template only.
- Treat the template as display/intent only. Real enabled/running/stopped state must come from runtime APIs backed by Storage/SQL, never from editing a template file directly.
- Do not modify workflow node code while applying runtime configuration unless the user explicitly asks for a code change.
- Re-running with the same answers should be idempotent: no changes, or a small diff limited to comments/timestamps.
- Do not read `server_api_token` or `service_api_token`, do not call `get_secret_manager()` for backend tokens, and do not hand-roll `curl` calls to local workflow config endpoints. If `workflow_config_manage` is unavailable or fails, stop the apply path and save a draft instead of bypassing it.

## Conversation Pattern

Guide the user from "I have this workflow" to "I know what is configured and what I still need to do".

Ask decisions in the order specified by `guide.md`, using one `question` tool call per step. If `guide.md` has no order, use the clicked shortcut as the current step and ask only the single most relevant question for that shortcut. The generic categories below are only fallback headings for organizing a guide file, not universal workflow defaults:

1. **Input mode**
2. **Source system or data shape**
3. **Output destinations**
4. **Filtering or business defaults**
5. **Validation sample**
6. **Apply or draft**

### Mandatory Question Tool Rule

The `question` tool is mandatory for this skill. Any time you need the user to choose, confirm, approve a diff, provide a missing value, decide whether to change another file, or answer a follow-up, stop prose and call `question`.

- Ordinary assistant text may summarize the current state, explain a proposed diff, or report results. It must not contain actionable questions such as "要不要...", "是否...", "请确认...", or numbered follow-ups like "第二个问题...".
- If `question` is not available in the tool list, say that the configuration guide cannot continue interactively until the `question` tool is available. Do not fall back to inline chat questions.
- Use one question card per turn. Do not ask several independent decisions in a single text paragraph.
- For diff approval, show the diff first, then call `question` with choices such as "应用上面的 diff", "只保存草稿", and "暂不修改". If the user chooses to apply the shown diff, immediately apply it through the backend config endpoint; do not ask an extra confirmation that only repeats the same side effect.
- For side-effect scope questions, such as "是否顺手修改 workflow.md", call `question`; do not ask in prose.

Rule anchor: never make a configuration question choice-only.

Never make a configuration question choice-only. Every Question-tool prompt used by this skill must include a way for the user to type a custom answer:

- Prefer a `type: "text"` question when the answer may be a hostname, port, topic, path, payload shape, product name, or any value not safely covered by fixed options.
- If you provide a `type: "choice"` question for recommended modes, also include a short `type: "text"` follow-up such as "Custom value or notes" with a placeholder that explains what the user can type. If the user has no custom value, allow them to enter "none".
- Do not force the user into only API/Syslog/Kafka/Webhook/Schedule choices; custom integration modes, source products, output destinations, and deployment notes must be expressible in free text.

Do not use the Question tool to collect long JSON, field lists, or credentials.

Good pattern after showing a diff:

```json
{
  "questions": [
    {
      "header": "确认应用",
      "question": "是否应用上面的发布配置 diff?",
      "type": "choice",
      "options": [
        {"label": "应用 diff", "description": "通过 workflow_config_manage 写入 Storage/SQL。"},
        {"label": "只保存草稿", "description": "不改运行配置，只写到输出目录。"},
        {"label": "暂不修改", "description": "停止本次配置变更。"}
      ]
    },
    {
      "header": "补充说明",
      "question": "如需限制范围或补充要求，请输入；没有则填 none。",
      "type": "text",
      "placeholder": "none"
    }
  ]
}
```

## Applying Publish Configuration

When the user approves an apply:

1. Read and preserve the previous canonical template with `workflow_config_manage(action="get", workflow_id="<workflow_id>")`.
2. If the response says no stored template exists, call `workflow_config_manage(action="sync", workflow_id="<workflow_id>")` so the backend migrates the fallback file or creates a generated template.
3. Deep-merge the selected values into the existing config shape where possible.
4. Use `workflow_config_manage(action="diff", workflow_id="<workflow_id>", config=<full proposed config>)` to produce the exact diff for confirmation.
5. After the user approves the shown diff, use `workflow_config_manage(action="put", workflow_id="<workflow_id>", config=<full proposed config>)` to save the full proposed config object.
6. Use the response's `config` as the saved template and `runtime` as the current effective state; do not infer runtime state from template `enabled` fields.
7. If `workflow_config_manage` is unavailable or returns an error, save a draft under `~/.flocks/workspace/outputs/<today>/` instead of changing `config.json`, and clearly state that the change was not applied, not published, and not started.
8. Validate with a JSON parser.
9. Verify `workflow_config_manage(action="get", workflow_id="<workflow_id>")` returns the saved template from Storage/SQL.
10. Run a smoke test with `metadata.sampleInputs`, `workflow.json` sample inputs, or the user's pasted sample when a safe local test is available.
11. If validation fails, restore the previous template through `workflow_config_manage(action="put", workflow_id="<workflow_id>", config=<previous config>)` and report the exact failure.

If the user says "publish as API", "Syslog input", "Kafka input", "Webhook input", or "Schedule" from the Publish page, treat it as a guided configuration intent:

- First identify whether the user wants to declare/change the template, start/stop runtime state, or both.
- For template changes, use `workflow_config_manage(action="get")` -> `workflow_config_manage(action="diff")` -> question confirmation -> `workflow_config_manage(action="put")`.
- For runtime actions, use the runtime endpoint after template confirmation, such as `/publish`, `/unpublish`, `/syslog-config`, `/kafka-config`, `/poller-config`, or `/triggers`.
- If the backend is unreachable, do not say "the user should publish later in the WebUI" as if the requested action succeeded. Save a draft and report the exact blocker.

When the user wants to start, stop, enable, disable, publish, or unpublish a capability, do not edit the template. Use the runtime endpoint for that capability, such as `/publish`, `/unpublish`, `/syslog-config`, `/kafka-config`, `/poller-config`, or `/triggers`.

If the user chooses draft mode, save the proposed config under `~/.flocks/workspace/outputs/<today>/` and list the path in the final report.

## Report Requirements

The final report must include:

- Workflow id, workflow directory, Storage/SQL config source, and optional fallback `config.json` path.
- What was configured by the guide.
- What remains for the user to do, including upstream forwarding, API key/secret setup, broker/channel details, firewall/port needs, and production validation.
- Sample validation result if a sample was provided.
- Full final config or draft path.
- Smoke test results or a clear reason the smoke test was skipped.

Do not look for skill-relative `references/` files during workflow configuration. Workflow-specific details must come from the current workflow's own `guide.md`; this prevents loading stale generic instructions or resolving a project-level skill path as a user-level path.

## Safety Rules

- Never ask the user to paste credentials in chat.
- Never enable broad/audit outputs without explicit user opt-in.
- Never clear persistent dedup/state files without explaining the consequence and getting confirmation.
- Never claim production readiness until a sample or smoke test has passed, or explicitly mark the setup as unvalidated.
- Be explicit when field mappings are inferred rather than confirmed.
