---
name: workflow-config-guide
category: system
ui_hidden: true
description: 配置现有 Flocks 工作流的发布、集成、触发器和发布配置模板，引导用户完成 API/Syslog/Kafka/Webhook/Schedule/File 输入、下游输出、样例验证、差异确认和最终报告
---

# Workflow Config Guide

Use this skill when the user asks to configure, publish, integrate, deploy, or validate an existing Flocks workflow, especially when the task involves publish configuration templates, `config.json` import/fallback, API publishing, Syslog/Kafka/Webhook/Schedule triggers, file input, downstream output, sample validation, or a first-time deployment guide.

Do not use this skill to create a brand-new workflow from scratch. Use `workflow-builder` for workflow design and generation, then return to this skill when the workflow already exists and needs runtime configuration.

## Quick Start

1. Identify the current workflow directory. Prefer the explicit path in the user request; otherwise inspect the active workflow context and project/user workflow roots.
2. Read the workflow files that exist: `workflow.json`, `workflow.md`, `workflow.edit.md`, optional legacy `config.json`, and `meta.json`. Treat the backend `/api/workflow/<workflow_id>/config` response as the canonical publish template.
3. Summarize the current configurable capabilities in plain language: publish mode, triggers, inputs, outputs, sample inputs, existing secrets references, and missing items.
4. Ask one decision question at a time. Always offer a default and avoid exposing technical knobs unless the user asks for details.
5. Before changing the publish template, show a unified diff of the proposed JSON and ask for explicit confirmation.
6. After applying changes, validate JSON syntax and run the lightest useful workflow/config smoke test available.
7. End with a concise report in chat and save a timestamped report under `~/.flocks/workspace/outputs/<today>/`, computing `<today>` at execution time.

## Configuration Contract

Treat the publish configuration template as a workflow runtime/publish template, not as a second copy of workflow code. The canonical template is stored in Storage/SQL under the backend workflow config endpoint. A workflow-local `config.json` is only an import/fallback artifact: when the backend has no stored template, it may read `config.json` once and migrate that content into Storage/SQL.

- If the stored template declares only API publishing, the publish page should expose only API publish controls.
- If the stored template declares only Syslog, Kafka, Webhook, or Schedule triggers, the publish page should expose only that trigger's start/stop or enable/disable controls.
- Do not store plaintext secrets in the template; store booleans such as `apiKeyConfigured` or secret-manager references.
- Treat the template as display/intent only. Real enabled/running/stopped state must come from runtime APIs backed by Storage/SQL, never from editing a template file directly.
- Do not modify workflow node code while applying runtime configuration unless the user explicitly asks for a code change.
- Re-running with the same answers should be idempotent: no changes, or a small diff limited to comments/timestamps.

## Conversation Pattern

Guide the user from "I have this workflow" to "I know what is configured and what I still need to do".

Ask these decisions in order when relevant:

1. **Input mode**: API, Syslog, Kafka, Webhook, Schedule, File, or manual test input.
2. **Source system or data shape**: product/source name, expected payload format, and whether a sample exists.
3. **Output destinations**: local files, API response, Kafka, IM/channel push, another workflow, or custom downstream.
4. **Filtering or business defaults**: keep the user-facing behavior simple; hide low-level thresholds and field lists behind "use default / show details".
5. **Validation sample**: ask for one representative payload when available; if unavailable, mark the configuration as unvalidated rather than blocking forever.
6. **Apply or draft**: show the publish-template JSON diff, then ask whether to apply through the backend endpoint or save as draft.

Use the Question tool when available for 2-4 clear options, but never make a configuration question choice-only. Every Question-tool prompt used by this skill must include a way for the user to type a custom answer:

- Prefer a `type: "text"` question when the answer may be a hostname, port, topic, path, payload shape, product name, or any value not safely covered by fixed options.
- If you provide a `type: "choice"` question for recommended modes, also include a short `type: "text"` follow-up such as "Custom value or notes" with a placeholder that explains what the user can type. If the user has no custom value, allow them to enter "none".
- Do not force the user into only API/Syslog/Kafka/Webhook/Schedule choices; custom integration modes, source products, output destinations, and deployment notes must be expressible in free text.

Do not use the Question tool to collect long JSON, field lists, or credentials.

## Applying Publish Configuration

When the user approves an apply:

1. Read and preserve the previous canonical template from `GET /api/workflow/<workflow_id>/config`.
2. Deep-merge the selected values into the existing config shape where possible.
3. Prefer the backend template endpoint: `PUT /api/workflow/<workflow_id>/config` with the full proposed config object as the JSON body.
4. Use the response's `config` as the saved template and `runtime` as the current effective state; do not infer runtime state from template `enabled` fields.
5. If the endpoint is unavailable, save a draft under `~/.flocks/workspace/outputs/<today>/` instead of directly changing runtime state.
6. Validate with a JSON parser.
7. Verify the publish page or config endpoint returns the saved template from Storage/SQL.
8. Run a smoke test with `metadata.sampleInputs`, `workflow.json` sample inputs, or the user's pasted sample when a safe local test is available.
9. If validation fails, restore the previous template through `PUT /api/workflow/<workflow_id>/config` and report the exact failure.

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

## References

- For `stream_alert_denoise`, `stream_alert_dedup`, or similar streaming alert deduplication workflows, read `references/stream-alert-dedup-integration-guide.md` and follow its scenario-specific defaults.
- For other workflows, use this `SKILL.md` as the source of truth and derive details from the workflow's own files instead of blindly applying the alert-dedup reference.

## Safety Rules

- Never ask the user to paste credentials in chat.
- Never enable broad/audit outputs without explicit user opt-in.
- Never clear persistent dedup/state files without explaining the consequence and getting confirmation.
- Never claim production readiness until a sample or smoke test has passed, or explicitly mark the setup as unvalidated.
- Be explicit when field mappings are inferred rather than confirmed.
