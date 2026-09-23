# Flocks Code Security

Project-level Flocks plugin for static source-code security audits with mandatory PoC generation.

Current implementation provides:

- seven isolated code-security agents (primary adjudicator, threat modeler, repository-wide baseline, focused investigator, verifier, PoC generator, result assistant);
- a subset-only callable-tool projection;
- digest-bound source views backed by reproducible read-only copies by default;
- standard file operations, shell commands, regex search, web research, and task tracking;
- isolated repository-wide baseline, optional focused-investigation, and independent-verification workers;
- a mandatory source-backed threat-modeling phase with canonical assets, trust boundaries, attacker capabilities, security objectives, and assumptions;
- SQLite scan, work-unit, session-binding, threat-model, candidate, verdict, dynamic-run, coverage, and batch storage;
- fail-closed work-unit scope, state-transition, omission, source-access, and evidence validation;
- Codex Security v1-compatible sealed manifest, findings, and coverage artifacts;
- deterministic Markdown and SARIF projections of independently verified findings.
- final parent-Agent adjudication with at most one scope-bound targeted rescan.

Target code is copied into the plugin snapshot store by default. Agents receive file, shell, and network tools for supporting work. Prompts instruct them to keep the canonical snapshot unchanged and use scratch copies for modifications or experiments; tool visibility is no longer a read-only execution boundary. Every audit assigns one repository-wide baseline work unit over `.` with a 200-step ceiling; it is never split by file count or byte size. If the baseline returns valid blocking questions with exact related paths, the host may launch at most one focused investigator over their deduplicated union before verification.


All seven agents receive these standard tools by default, including during parent adjudication:
`read`, `write`, `edit`, `apply_patch`, `glob`, `delete`, `move`, `copy`, `mkdir`,
`bash`, `grep`, `webfetch`, `websearch`, and `todo`.
The duplicate `audit_inventory`, `audit_read`, and `audit_search` tools have been
removed; use `glob`, `read`, and `grep` instead. Audit-specific context, candidate,
coverage, verdict, PoC, query, and lifecycle tools remain stage-specific.

Workers receive the absolute snapshot root and assigned paths in their work message.
Standard tools retain their original behavior: `read` returns file contents and does
not write audit records or add audit metadata. Before destructive context compression,
at worker recovery, and at coverage, verifier-verdict, and PoC submission, the audit
lifecycle derives deduplicated source receipts from completed
`read`, `glob`, and `grep` ToolParts already stored by the session loop (including
archived messages). Processed ToolPart IDs are committed atomically with receipts,
so repeated submissions skip previously validated outputs. Ordinary sessions bypass
the audit database during compaction. The audit layer shares native argument/path
normalization and maps native read line numbers to snapshot evidence ranges.
It verifies snapshot digests and counts only fully
returned, unchanged source lines. Failed calls, user text, truncated lines, shell
output, and web output cannot establish complete source coverage. Evidence digests
come from existing candidate context or SHA-256 computed against the snapshot.
The fourteen standard tools remain visible at every stage. Existing audit tools
retain their original stage, role, subject, and assigned-path restrictions.
For long lines, use read columnOffset/columnLimit with a zero-based line offset,
following next_column until has_more is false. Verified slices survive compaction
and count as a read only after covering the complete line. Verifier reads may span
multiple calls without weakening independent-read requirements.
Normal host permissions govern standard tools; audit-specific evidence validation
and independent-verifier read requirements remain enforced.

Run a complete audit with one command:

```bash
flocks security audit /absolute/path/to/source
```

Standard tools are available at every stage in both single audits and batch runs;
the model chooses which tools to call. No per-scan tool switches are required.
When migrating commands from the experimental CLI branch, remove `--bash` and
`--web-search` from `flocks security audit` and `flocks security batch run`.
PoC generation always runs after static adjudication in single audits and batch
runs. Remove `--poc` / `--generate-poc` from existing commands; these switches are
no longer accepted.

To skip the source copy and audit the source directory directly:

```bash
flocks security audit /absolute/path/to/source --no-copy
```

Direct mode still records the initial inventory and file digests. Audit reads fail closed if an included source file changes, and deleting the scan never deletes the source directory.

Snapshots include up to 4 GiB by default, allowing large source trees such as
Wireshark with built static libraries. The total is checked before content hashing.
Use `--max-snapshot-bytes` to change this limit. Source files are not automatically
discarded to fit the budget. `--no-copy` still hashes included files.

Optional scope controls are `--include PATH` and `--exclude GLOB` (both repeatable),
and `--max-file-bytes`. For example, `--exclude '*.a' --exclude '*.o'` explicitly
omits build products from the requested scope. Files exceeding a per-file cap are
recorded as omissions; this does not establish complete repository coverage.
The service/API equivalent of the total cap is `max_total_bytes` / `maxTotalBytes`.

For a guided source audit, attach one small UTF-8 vulnerability description:

```bash
flocks security audit /absolute/path/to/source \
  --knowledge-base /absolute/path/to/description.txt
```

The description is captured before the scan starts, stored as immutable scan-bound data, and exposed only to the threat modeler, static workers, independent verifiers, and final adjudicator. It is always treated as an untrusted hypothesis rather than source evidence. The sealed report records only its file name, byte length, and SHA-256 digest.

The command prints the `scan_id` as soon as the digest-bound source view is ready, then follows threat modeling, repository-wide baseline scanning, optional focused investigation, independent verification, parent-Agent adjudication, and report generation. This one-command path is **host-orchestrated** by `AuditOrchestrator`; the `code-security` primary Agent makes the semantic accept/reject or targeted-rescan decision, but it does not schedule the CLI's macro phases. To inspect the same persisted progress from another terminal without changing the scan:

```bash
flocks security status <scan_id>
```

Audits retain execution history by default. Opt into cleanup for an individual run:

```bash
flocks security audit /absolute/path/to/source --cleanup-intermediates
```

The equivalent parameter is `cleanup_intermediates: true` in the Python service and
`code_security_audit` / `audit_prepare` tools, or `cleanupIntermediates: true` in the
HTTP create request. Set it to `false` (CLI: `--no-cleanup-intermediates`) to retain
execution data. The choice is persisted per scan, including across process restarts.

Cleanup runs after completion, failure, cancellation, or recovery of an interrupted
scan. It removes execution events, phase/retry/access history, unused fuzz inputs,
audit-owned worker sessions, copied source snapshots, and Docker scratch directories.
It retains the sealed report bundle, final coverage, threat model, findings, verdicts,
PoCs, validation/submission results, selected input ancestry, and the minimal database
records needed to preserve their references. Caller source directories, shared source
snapshots, and the calling conversation of a tool-started audit are preserved. External
code projections supplied as an audit target are caller-owned and are never removed.

Scan detail exposes `scan.cleanup` with the cleanup outcome and deletion counts.
Unfinished or failed cleanup is retried at plugin startup once its owning process
has stopped; scans still owned by a live audit process are skipped. Worker ownership
is persisted when its session is created, so launch failures before an attempt is
recorded can also be cleaned. Cleanup failures do not overwrite the audit result; successful reports must pass
integrity checks before cleanup. One terminal cleanup event replaces the execution
history. Deleted SQLite pages become reusable; cleanup does not run a database-wide
`VACUUM` or remove the database file.

Both commands accept `--json`; `audit` emits newline-delimited progress events suitable for automation. Use `--model provider/model` to pin a model instead of the configured default.

When Langfuse is configured, the same one-command audit is observable under a Langfuse session whose ID is the audit `scan_id`:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://your-langfuse.example.com
flocks security audit /absolute/path/to/source
```

The `code-security.scan` trace records snapshot preparation, threat-modeling, baseline, optional investigation, verification, PoC generation and optional targeted rescan, parent adjudication, batch status changes, scan counters, and the final finding summary. Worker model-step spans are attached beneath their phase in the same trace while retaining isolated Flocks sessions. Each step records its agent role, work-unit ID, assigned paths, and candidate ID. Under each model generation, audit-tool spans show the concrete inspection and decision actions. Repeated worker polling remains available to the CLI progress callback, but Langfuse records a progress span only when batch status or counts change.

For local batch diagnostics, run `flocks security batch status <run-dir>` and
inspect each task's `runtime`. Workers refresh `tasks/<task-id>/runtime.json`
approximately every five seconds; orchestration events wake the same monitor
and are coalesced into its next update. The snapshot includes the service's
current phase (including cleanup), coordinator and active/pending work units, session
and attempt IDs, logical `step`, cumulative `trace_step`, model state
(`waiting_model` / `streaming_model`), active tool names and counts, retry or
compaction status, and the last observed model/tool activity time. Tool arguments,
model output, and provider error text are excluded.

Before timeout cancellation, the worker freezes this snapshot into `result.json`.
If the worker is unresponsive, the scheduler retains the last persisted snapshot
and its age. Both JSON files survive normal cleanup. `observed_at` is the sampling
time; `workers_observed_at` is the last successful work-unit lookup. Activity and
polling do not prove useful audit progress. A missing snapshot is reported as an
unknown phase; these diagnostics cannot reconstruct runs made before this change.
Slow lookups keep a single outstanding query. Diagnostic write failures are
reported separately and do not prevent audit result saving or resource cleanup.

Dynamic scans add spans for Docker preflight, the bounded runner, each candidate, build, control, attack, image removal, and final cleanup. These spans use lifecycle IDs and counts plus status, exit code, duration, timeout, and truncation summaries; they never attach Docker argv, probe scripts, raw stdout/stderr, Docker endpoints, image names, or host paths.

Model messages and audit-tool inputs/outputs can contain proprietary source code. Prefer a trusted self-hosted Langfuse deployment for full-fidelity traces. Set `FLOCKS_LANGFUSE_CAPTURE_MODE=truncated` and `FLOCKS_LANGFUSE_MAX_CHARS=<limit>` when bounded payload capture is required. Langfuse failures are best-effort only and never alter scan state or finalization.

Standard static audits use the flow threat modeling → repository-wide baseline → optional focused investigation → verification → parent adjudication → PoC generation → deterministic reduction. The parent may instead direct one targeted rescan, followed by verification and a mandatory second/final adjudication. Baseline and investigator workers must consume the persisted threat model before they can submit candidates or coverage. Every candidate must receive one independent verifier verdict and be classified by the parent before finalization. Parent-rejected candidates are omitted, insufficient-evidence candidates remain deferred coverage, and only independently confirmed candidates accepted by the parent are projected into SARIF.

The public `code-security` Agent remains the interactive audit entry point. In an interactive audit it may drive the audit tools directly. In the one-command CLI path it is invoked only at the adjudication boundary, where the session callable-tool set exposes the common standard tools plus `audit_knowledge_base`, `audit_adjudication_context`, and `audit_submit_adjudication`; the host resumes control after the decision.

The seven Agent definitions are declarative and live in `src/flocks_code_security/agents/<agent-name>/agent.yaml`, with each prompt in the adjacent `prompt.md`. Tools, skills, model settings, and isolation policy can therefore be reviewed and changed independently for each Agent while remaining owned and packaged by this plugin.

All seven code-security Agents combine a dedicated session with the `isolated` prompt profile. Their model input contains the Agent prompt, execution-mode rules, core configuration guard and tool protocol, minimal runtime environment, and only host-selected Agent skills; it excludes the Rex/Flocks provider identity, workspace instruction files, memory, optional `UserPromptBefore` context, and unrelated runtime metadata. Trusted `LLM_BEFORE`/`LLM_AFTER` hooks remain part of the host policy and redaction boundary. Their callable application tools never exceed the names declared in `AGENT_TOOLS`; phase projection may reduce that set, globally auto-loaded tools are not added, and host runtime controls such as `plan_exit` remain available when their mode requires them.

Threat-model evidence uses the exact `relative_path`, `blob_digest`, `start_line`, and `end_line` contract exposed in the tool schema. A threat-modeler may atomically refine its own structurally valid draft while its work unit remains active; once the work unit completes, the model is immutable. Worker completion, baseline launch, finalization, and status inspection re-check the structural contract. Semantic completeness remains the threat-model agent's responsibility and is not guessed from language-dependent placeholder blacklists. Historical scans with structurally invalid threat models are reported with `integrity_status: invalid` and must not be used.

Finalization validates `scan-manifest.json`, `findings.json`, and `coverage.json` against the vendored Codex Security v1 schemas before atomically publishing a sealed `completed` bundle. Findings carry stable finding/occurrence IDs, fingerprints, CWE taxonomy, digest-bound code evidence, root cause, validation, attack path, and remediation. Coverage carries surface dispositions, immutable receipts, explicit exclusions, deferred work, and completeness. `adjudication.json` is a sealed supplemental artifact containing the parent decisions and rejection reasons. PoC files are generated for parent-accepted, statically confirmed candidates. Historical dynamic-validation artifacts remain readable. Clean Git roots are revision-bound; dirty worktrees add a content snapshot digest. Markdown, SARIF, and `threat-model.json` remain readable projections outside the canonical contract.

Coverage completeness describes static source coverage, not the absence of environmental uncertainty. Inventoried zero-byte files are counted as `notApplicable`; later, stronger analysis receipts replace weaker baseline states deterministically. Missing receipts may receive one same-session coverage-only recovery when analysis progress already exists. After that bounded recovery, unread or failed non-empty files, snapshot omissions, or explicitly blocking questions fail finalization with `coverage_blocked` while preserving the recorded database facts. External-service, deployment, runtime, and unresolved-hypothesis questions are retained as non-blocking validation limitations.

The threat-model and completed-scan contract semantics are adapted from the Apache-2.0-licensed OpenAI Codex Security bundled plugin. See `THIRD_PARTY_NOTICES.md` for attribution. This implementation retains Flocks' digest-bound source view, dedicated-session, tool-projection, and persistence boundaries; it does not embed or launch the Codex Security MCP server. Change audits, shared threat-model caching, supplied-model overrides, remote Docker, external-network probes, and crash recovery remain outside the current version.

Run the plugin regression suite from the Flocks checkout with:

```bash
.venv/bin/pytest -q .flocks/plugins/flocks-code-security/tests
```
