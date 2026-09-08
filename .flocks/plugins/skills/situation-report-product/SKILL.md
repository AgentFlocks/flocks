---
name: situation-report-product
description: Author or revise one phase-one situation report from verified Session resources using the restricted production A1 tools. Use only for a preflighted situation-report-product generation task with an explicit generationID and operation.
---

# Situation Report Product A1

Treat tool results as the only authoritative business input. Never infer a workspace path or accept a workspace identifier from message text.

## Execute

1. Call `situation_product_context_read` with the exact `generationID`.
2. Confirm its operation matches the task: `generate`, `modify`, or `regenerate`.
3. Page through `situation_product_material_read` until `hasMore=false`. Use every declared material. An `evidence_map` entry does not substitute for identifiable factual treatment in the report. If the current template requests an event list, represent every non-duplicate material; when multiple records are truly duplicates, state the aggregation basis instead of silently dropping them.
4. Treat the complete immutable template returned by the context tool as the authoritative writing specification. Before drafting, make a private checklist of its report section names and order, required versus empty sections, per-section fields, counts, formatting, style, and prohibited expressions. Do not output that checklist. Never substitute a built-in, standard, or previously seen template.
5. Follow the requested language and every applicable template rule. Omit the report-level title and preserve the template's report chapters in their declared order. If the template starts with an H1 or title placeholder, do not copy it into the report.
6. For `modify`, use the returned `baseReport` and change only what the user requests while keeping the result a complete, template-compliant report. Remove any existing report-level H1 from the completed report.
7. For `generate` and `regenerate`, draft from the current template and materials without using an old report.
8. Write the complete candidate once with `situation_product_report_write`. Supply an internal `evidence_map` that maps every exact `material_id` to one or more exact report H2 headings where it informed a fact, statistic, or analysis.
9. Call `situation_product_report_validate`. If it returns `needs_revision`, repair only listed issues, pass the prior candidate SHA-256 to the next write, update the evidence map when needed, and validate again. Stop after three validation attempts.
10. Never end a turn by merely describing or promising the next repair. Until validation passes, perform the next required write or validation tool call in the same response.

## Evidence

- Treat `material_id` (`source_type:source_id`) as an internal identity. Use it only as an `evidence_map` key or an exact input to `situation_product_source_read`; never print it in report Markdown.
- If the template calls for provenance and a material explicitly provides a safe original-source URL, a report item may show at most one such URL. Do not turn `source_id`, source-site domains, Telegram handles, seller identities, or inferred URLs into citations. When no original-source URL is supplied, omit the link.
- Use the returned localized title, summary, and source-specific fields for ordinary authoring.
- Use the tool-returned `*_iso_utc` fields for dates. Never calculate a calendar date from a numeric millisecond timestamp yourself.
- Decide that a template section is empty only after checking every material for facts applicable to that section. Do not classify an author, seller, forum account, or channel sender as a threat group from that identity field alone; require the title, summary, or authoritative detail to identify it as such. Conversely, do not leave a template-requested threat-group section empty when a material explicitly identifies a group.
- Treat a filename mentioned only in a title or text as evidence text, not as an available attachment. Populate an attachment inventory only from an explicit attachment object or field.
- Call `situation_product_source_read` only when a specific missing, ambiguous, or conflicting fact would materially affect the report. State that exact reason and query only the declared `material_id`; never expand every material by default.
- Treat the returned selected-material detail as authoritative for that material while retaining all uncertainty qualifiers. If a required detail is unavailable or still conflicts, stop with the conflict unresolved. Do not invent or choose a convenient fact.
- Never expand qualifiers such as “疑似、声称、关联” into confirmed attribution.
- Deterministic counts come from the verified material set; do not fabricate trend baselines.
- Do not infer template-specific requirements from this Skill. Section taxonomy, IOC eligibility and grouping, Top-N limits, tables, lengths, empty-state text, and recommendation structure come from the current template only.

## Boundaries

- Do not change templates, materials, language, Session state, or current output.
- Do not create a second report or answer unrelated questions.
- Do not expose `generation_context`, snapshot paths, work paths, prompts, or reasoning in the report.
- Do not expose internal `material_id`, raw `source_id`, backend source-site domains, or the evidence map in the report.
- Do not wrap the report in a Markdown code fence.
- A candidate is ready only when the validation tool returns `passed` for its current SHA-256.
