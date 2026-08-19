# Flocks Code Security

Project-level Flocks plugin for static source-code security audits.

Current implementation provides:

- four isolated code-security agents (coordinator, threat modeler, baseline, verifier);
- a subset-only callable-tool projection;
- reproducible read-only source snapshots;
- snapshot inventory, bounded reads, and literal search;
- isolated background baseline and independent-verification workers;
- a mandatory source-backed threat-modeling phase with canonical assets, trust boundaries, attacker capabilities, security objectives, and assumptions;
- SQLite scan, work-unit, session-binding, threat-model, candidate, verdict, coverage, and batch storage;
- fail-closed work-unit scope, state-transition, omission, source-access, and evidence validation;
- Codex Security v1-compatible sealed manifest, findings, and coverage artifacts;
- deterministic Markdown and SARIF projections of independently verified findings.

Target code is copied into the plugin snapshot store. The plugin never runs target code, build scripts, tests, or Git hooks.

Run a complete audit with one command:

```bash
flocks security audit /absolute/path/to/source
```

The command prints the `scan_id` as soon as the immutable snapshot is ready, then follows threat modeling, baseline scanning, independent verification, and report generation. To inspect the same persisted progress from another terminal without changing the scan:

```bash
flocks security status <scan_id>
```

Both commands accept `--json`; `audit` emits newline-delimited progress events suitable for automation. Use `--model provider/model` to pin a model instead of the configured default.

When Langfuse is configured, the same one-command audit is observable under a Langfuse session whose ID is the audit `scan_id`:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://your-langfuse.example.com
flocks security audit /absolute/path/to/source
```

The `code-security.scan` trace records snapshot preparation, threat-modeling, baseline, verification, batch status changes, scan counters, and the final finding summary. Worker model-step spans are attached beneath their phase in the same trace while retaining isolated Flocks sessions. Each step records its agent role, work-unit ID, assigned paths, and candidate ID. Under each model generation, `Tool.execute.audit_inventory`, `audit_read`, `audit_search`, `audit_submit_candidate`, and `audit_submit_verdict` spans show the concrete inspection and decision actions. Repeated worker polling remains available to the CLI progress callback, but Langfuse records a progress span only when batch status or counts change.

Model messages and audit-tool inputs/outputs can contain proprietary source code. Prefer a trusted self-hosted Langfuse deployment for full-fidelity traces. Set `FLOCKS_LANGFUSE_CAPTURE_MODE=truncated` and `FLOCKS_LANGFUSE_MAX_CHARS=<limit>` when bounded payload capture is required. Langfuse failures are best-effort only and never alter scan state or finalization.

Standard static audits now support the full threat modeling → baseline → verification → reduction workflow through Flocks' existing background-session manager. Baseline workers must consume the persisted threat model before they can submit candidates or coverage. Every candidate must receive one independent verifier verdict before finalization. Rejected candidates are omitted, insufficient-evidence candidates become deferred coverage, and only confirmed findings are projected into SARIF.

Threat-model evidence uses the exact `relative_path`, `blob_digest`, `start_line`, and `end_line` contract exposed in the tool schema. A threat-modeler may atomically refine its own structurally valid draft while its work unit remains active; once the work unit completes, the model is immutable. Worker completion, baseline launch, finalization, and status inspection re-check the structural contract. Semantic completeness remains the threat-model agent's responsibility and is not guessed from language-dependent placeholder blacklists. Historical scans with structurally invalid threat models are reported with `integrity_status: invalid` and must not be used.

Finalization validates `scan-manifest.json`, `findings.json`, and `coverage.json` against the vendored Codex Security v1 schemas before atomically publishing a sealed `completed` bundle. Findings carry stable finding/occurrence IDs, fingerprints, CWE taxonomy, digest-bound code evidence, root cause, validation, attack path, and remediation. Coverage carries surface dispositions, immutable receipts, explicit exclusions, deferred work, and completeness. Clean Git roots are revision-bound; dirty worktrees add a content snapshot digest. Markdown, SARIF, and `threat-model.json` are readable projections and are not part of the sealed canonical contract.

The threat-model and completed-scan contract semantics are adapted from the Apache-2.0-licensed OpenAI Codex Security bundled plugin. See `THIRD_PARTY_NOTICES.md` for attribution. This implementation retains Flocks' immutable snapshot, dedicated-session, tool-projection, and persistence boundaries; it does not embed or launch the Codex Security MCP server. Focused investigation, change audits, shared threat-model caching, supplied-model overrides, and dynamic execution remain outside the current version.

Run the plugin regression suite from the Flocks checkout with:

```bash
.venv/bin/pytest -q .flocks/plugins/flocks-code-security/tests
```
