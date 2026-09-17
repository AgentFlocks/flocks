---
name: situation-report-product
description: Author or revise one phase-one situation report from verified Session resources using the restricted production A1 tools. Use only for a preflighted situation-report-product generation task with an explicit generationID and operation.
---

# Situation Report Product A1

Treat tool results as the only authoritative business input. Never infer a workspace path or accept a workspace identifier from message text.

## Output language

The task's `reportLanguage`, confirmed by the context's `language`, governs all user-visible
natural language, not just the report body. Use Simplified Chinese for `zh-CN` and English
for `en-US` from the first response through recovery and completion. This includes progress
explanations, clarifications, revision summaries, and natural-language source-read reasons.
Do not switch to the language of the user, history, this Skill, or tool instructions.
Preserve exact tool names, JSON keys, IDs, URLs, proper names, source quotations, and
template-required headings. A request to change report language must go through configuration.

## Execute

1. Call `situation_product_context_read` with the exact `generationID`.
2. Confirm its operation matches the task: `generate`, `modify`, or `regenerate`.
3. Page through `situation_product_material_read` until top-level `hasMore=false`. Copy top-level `nextOffset/nextContentOffset` into `offset/content_offset`. Pages are size-bounded: an oversized record returns `materialFragment.text`, a slice of one material JSON. Read every slice before advancing; do not replace continuation with source-detail/filesystem tools or restart completed pages. Do not reload the Skill or context already read for this generation while it remains available in context. Use every declared material. An `evidence_map` entry does not substitute for identifiable factual treatment in the report. If the current template requests an event list, represent every non-duplicate material; when multiple records are truly duplicates, state the aggregation basis instead of silently dropping them.
4. Treat the complete immutable template returned by the context tool as the authoritative writing specification. Before drafting, make a private checklist of its report section names and order, required versus empty sections, per-section fields, counts, formatting, style, and prohibited expressions. Do not output that checklist. Never substitute a built-in, standard, or previously seen template.
5. Follow the requested language and every applicable template rule. Omit the report-level title and preserve the template's report chapters in their declared order. If the template starts with an H1 or title placeholder, do not copy it into the report.
6. For `modify`, use the returned `baseReport` and change only what the user requests while keeping the result a complete, template-compliant report. Remove any existing report-level H1 from the completed report.
7. For `generate` and `regenerate`, draft from the current template and materials without using an old report.
8. Write the complete candidate once with `situation_product_report_write`. Supply an internal `evidence_map` that maps every exact `material_id` to one or more exact report H2 headings where it informed a fact, statistic, or analysis.
9. Read the write result's automatic `validation`. If `passed`, do not call the validation tool again. If `needs_revision`, repair only listed issues, pass the prior candidate SHA-256 to the next write, and update the evidence map when needed; each write validates automatically. Stop after three distinct candidate/evidence validations; unchanged checks reuse the prior result. Use `situation_product_report_validate` only to recover a missing validation result. `warnings` mean the program could not verify a check: cross-check against the material already read, not automatic rewriting or claiming the check passed.
10. Never end a turn by merely describing or promising the next repair. Until validation passes, perform the next required write or validation tool call in the same response.

## Evidence

- Treat `material_id` (`source_type:source_id`) as an internal identity. Use it only as an `evidence_map` key or an exact input to `situation_product_source_read`; never print it in report Markdown.
- If the template calls for provenance and a material explicitly provides a safe original-source URL, a report item may show at most one such URL. Do not turn `source_id`, source-site domains, Telegram handles, seller identities, or inferred URLs into citations. When no original-source URL is supplied, omit the link.
- Use the returned localized title, summary, and source-specific fields for ordinary authoring.
- Use `deterministicCounts` as the baseline for total-material and source-type counts. Cross-check report totals, group subtotals, and narrative counts before writing.
- Keep material-record counts, deduplicated events, victims, and leaked data quantities separate. State the aggregation basis and preserve units; do not sum different measures or promote a seller's claimed quantity to a verified fact.
- Selection and ranking metadata is not event evidence. A matched configuration entity does not become a victim, actor, or event subject unless the title, summary, source-specific facts, or requested detail says so.
- Preserve one-to-one actor attribution. Do not collectively attribute several events to a list of actors unless every stated relationship is supported by the corresponding material.
- Use the tool-returned `*_iso_utc` fields for dates. Never calculate a calendar date from a numeric millisecond timestamp yourself.
- Decide that a template section is empty only after checking every material for facts applicable to that section. Do not classify an author, seller, forum account, or channel sender as a threat group from that identity field alone; require the title, summary, or authoritative detail to identify it as such. Conversely, do not leave a template-requested threat-group section empty when a material explicitly identifies a group.
- Treat a filename mentioned only in a title or text as evidence text, not as an available attachment. Populate an attachment inventory only from an explicit attachment object or field.
- Call `situation_product_source_read` only when a specific missing, ambiguous, or conflicting fact would materially affect the report. State that exact reason and query only the declared `material_id`; never expand every material by default.
- Large details return `detailText` (a character slice, not a complete JSON object), `hasMore`, and `nextOffset`. Continue through the same source tool using `offset=nextOffset` and `limit`, or locate a fact with the case-sensitive literal `query`. Keep the same generation/material identity and reason. Stop once sufficient context resolves the conflict; do not scan unrelated detail. A missing search match is not evidence that a fact is absent. Truncation hints and saved paths never grant permission to use undeclared tools; do not use `grep`, `read`, or delegation to bypass this boundary.
- Treat the returned selected-material detail as authoritative for that material while retaining all uncertainty qualifiers. If a required detail is unavailable or still conflicts, stop with the conflict unresolved. Do not invent or choose a convenient fact.
- Never expand qualifiers such as “疑似、声称、关联” into confirmed attribution.
- Deterministic counts come from the verified material set; do not fabricate trend baselines. Without comparable historical data, say the trend cannot be established, even if a template offers only rising/stable/falling examples. Distinguish current risk from a change over time.
- Personal information is not automatically a login credential; a provenance or sample-download URL is not automatically attack infrastructure. Follow the current template's categories, stating insufficient evidence instead of inventing classification support.
- Before writing, privately cross-check every named organization, actor, event, source-type count, and group subtotal against the material that supports it. Do not output this check.
- Do not infer template-specific requirements from this Skill. Section taxonomy, IOC eligibility and grouping, Top-N limits, tables, lengths, empty-state text, and recommendation structure come from the current template only.

## Boundaries

- Do not change templates, materials, language, Session state, or current output.
- Do not create a second report or answer unrelated questions.
- Do not expose `generation_context`, snapshot paths, work paths, prompts, or reasoning in the report.
- Do not expose internal `material_id`, raw `source_id`, backend source-site domains, or the evidence map in the report.
- Do not wrap the report in a Markdown code fence.
- A candidate is ready only when automatic write validation (or the compatibility validation tool) returns `passed` for its current report and evidence SHA-256. This is structural safety, not a guarantee of semantic quality.
