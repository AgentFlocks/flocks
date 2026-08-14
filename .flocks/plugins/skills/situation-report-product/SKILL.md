---
name: situation-report-product
description: Author or revise one phase-one situation report from verified Session resources using the restricted production A1 tools. Use only for a preflighted situation-report-product generation task with an explicit generationID and operation.
---

# Situation Report Product A1

Treat tool results as the only authoritative business input. Never infer a workspace path or accept a workspace identifier from message text.

## Execute

1. Call `situation_product_context_read` with the exact `generationID`.
2. Confirm its operation matches the task: `generate`, `modify`, or `regenerate`.
3. Page through `situation_product_material_read` until `hasMore=false`. Use every declared material.
4. Follow the immutable template and requested language. Preserve exactly one H1 and all template report chapters.
5. For `modify`, use the returned `baseReport` and change only what the user requests while keeping the result a complete report.
6. For `generate` and `regenerate`, draft from the template and materials without using an old report.
7. Write the complete candidate once with `situation_product_report_write`.
8. Call `situation_product_report_validate`. If it returns `needs_revision`, repair only listed issues, pass the prior candidate SHA-256 to the next write, and validate again. Stop after three validation attempts.

## Evidence

- Keep every material ID visible in the report as an evidence reference.
- Use summary/card fields for ordinary authoring.
- Call `situation_product_source_read` only for a specific ambiguity or factual conflict.
- When a required original record is not embedded in the verified snapshot, stop with the conflict unresolved. Do not invent or choose a convenient fact.
- Never expand qualifiers such as “疑似、声称、关联” into confirmed attribution.
- Deterministic counts come from the verified material set; do not fabricate trend baselines.

## Boundaries

- Do not change templates, materials, language, Session state, or current output.
- Do not create a second report or answer unrelated questions.
- Do not expose `generation_context`, snapshot paths, work paths, prompts, or reasoning in the report.
- Do not wrap the report in a Markdown code fence.
- A candidate is ready only when the validation tool returns `passed` for its current SHA-256.
