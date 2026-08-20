# Flocks Code Security

Project-level Flocks plugin for static source-code security audits with optional local-Docker validation.

Current implementation provides:

- five isolated code-security agents (primary adjudicator, threat modeler, baseline, verifier, prober);
- a subset-only callable-tool projection;
- reproducible read-only source snapshots;
- snapshot inventory, bounded reads, and literal search;
- isolated background baseline and independent-verification workers;
- a mandatory source-backed threat-modeling phase with canonical assets, trust boundaries, attacker capabilities, security objectives, and assumptions;
- SQLite scan, work-unit, session-binding, threat-model, candidate, verdict, dynamic-run, coverage, and batch storage;
- fail-closed work-unit scope, state-transition, omission, source-access, and evidence validation;
- Codex Security v1-compatible sealed manifest, findings, and coverage artifacts;
- deterministic Markdown and SARIF projections of independently verified findings.
- final parent-Agent adjudication with at most one scope-bound targeted rescan.

Target code is copied into the plugin snapshot store. Static scans never run target code, build scripts, tests, or Git hooks.

Run a complete audit with one command:

```bash
flocks security audit /absolute/path/to/source
```

Dynamic validation is explicit opt-in:

```bash
flocks security audit /absolute/path/to/source --dynamic
```

Dynamic scans ask an isolated prober to describe a control/attack pair for each statically confirmed candidate. A trusted host runner validates the contract and builds only from an existing snapshot Dockerfile with no network, pulls, cache, or unrestricted build steps. BuildKit runs are pinned to the local default Docker builder and resource-limited; otherwise the runner forces the resource-limited legacy builder against the verified local daemon and fails preflight if neither backend can enforce CPU, memory, process, and shared-memory limits. Each script runs in a fresh local container with no network, no capabilities, a read-only root, bounded resources, and no mounts. The fail-closed Dockerfile policy accepts only an explicit instruction set and rejects parser directives, heredocs, `ADD`, `ONBUILD`, `FROM --platform`, and all `RUN` options; every external `FROM` or `COPY --from` image must already exist locally, while `scratch` needs no image inspection. The runner stores bounded facts only; the parent Agent decides whether those facts reproduce the candidate. Runnable probes require Docker CLI access and a local Docker daemon endpoint. Docker is a containment boundary for this opt-in workflow, not a general malicious-code sandbox.

The command prints the `scan_id` as soon as the immutable snapshot is ready, then follows threat modeling, baseline scanning, independent verification, parent-Agent adjudication, and report generation. This one-command path is **host-orchestrated** by `AuditOrchestrator`; the `code-security` primary Agent makes the semantic accept/reject or targeted-rescan decision, but it does not schedule the CLI's macro phases. To inspect the same persisted progress from another terminal without changing the scan:

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

The `code-security.scan` trace records snapshot preparation, threat-modeling, baseline, verification, optional dynamic validation and targeted rescan, parent adjudication, batch status changes, scan counters, and the final finding summary. Worker model-step spans are attached beneath their phase in the same trace while retaining isolated Flocks sessions. Each step records its agent role, work-unit ID, assigned paths, and candidate ID. Under each model generation, audit-tool spans show the concrete inspection and decision actions. Repeated worker polling remains available to the CLI progress callback, but Langfuse records a progress span only when batch status or counts change.

Dynamic scans add spans for Docker preflight, the bounded runner, each candidate, build, control, attack, image removal, and final cleanup. These spans use lifecycle IDs and counts plus status, exit code, duration, timeout, and truncation summaries; they never attach Docker argv, probe scripts, raw stdout/stderr, Docker endpoints, image names, or host paths.

Model messages and audit-tool inputs/outputs can contain proprietary source code. Prefer a trusted self-hosted Langfuse deployment for full-fidelity traces. Set `FLOCKS_LANGFUSE_CAPTURE_MODE=truncated` and `FLOCKS_LANGFUSE_MAX_CHARS=<limit>` when bounded payload capture is required. Langfuse failures are best-effort only and never alter scan state or finalization.

Standard static audits use the flow threat modeling → baseline → verification → parent adjudication → deterministic reduction. Dynamic audits insert probing and Docker execution after verification. The parent may instead direct one targeted rescan, followed by verification, dynamic processing of only new confirmed candidates, and a mandatory second/final adjudication. Baseline workers must consume the persisted threat model before they can submit candidates or coverage. Every candidate must receive one independent verifier verdict and be classified by the parent before finalization. Parent-rejected candidates are omitted, insufficient-evidence candidates remain deferred coverage, and only independently confirmed candidates accepted by the parent are projected into SARIF.

The public `code-security` Agent remains the interactive audit entry point. In an interactive audit it may drive the audit tools directly. In the one-command CLI path it is invoked only at the adjudication boundary, where the session callable-tool set exposes `audit_adjudication_context` and `audit_submit_adjudication`; the host resumes control after the decision.

The five Agent definitions are declarative and live in `src/flocks_code_security/agents/<agent-name>/agent.yaml`, with each prompt in the adjacent `prompt.md`. Tools, skills, model settings, and isolation policy can therefore be reviewed and changed independently for each Agent while remaining owned and packaged by this plugin.

All five code-security Agents combine a dedicated session with the `isolated` prompt profile. Their model input contains the Agent prompt, execution-mode rules, core configuration guard and tool protocol, minimal runtime environment, and only host-selected Agent skills; it excludes the Rex/Flocks provider identity, workspace instruction files, memory, optional `UserPromptBefore` context, and unrelated runtime metadata. Trusted `LLM_BEFORE`/`LLM_AFTER` hooks remain part of the host policy and redaction boundary. Their callable application tools never exceed the names declared in `AGENT_TOOLS`; phase projection may reduce that set, globally auto-loaded tools are not added, and host runtime controls such as `plan_exit` remain available when their mode requires them.

Threat-model evidence uses the exact `relative_path`, `blob_digest`, `start_line`, and `end_line` contract exposed in the tool schema. A threat-modeler may atomically refine its own structurally valid draft while its work unit remains active; once the work unit completes, the model is immutable. Worker completion, baseline launch, finalization, and status inspection re-check the structural contract. Semantic completeness remains the threat-model agent's responsibility and is not guessed from language-dependent placeholder blacklists. Historical scans with structurally invalid threat models are reported with `integrity_status: invalid` and must not be used.

Finalization validates `scan-manifest.json`, `findings.json`, and `coverage.json` against the vendored Codex Security v1 schemas before atomically publishing a sealed `completed` bundle. Findings carry stable finding/occurrence IDs, fingerprints, CWE taxonomy, digest-bound code evidence, root cause, validation, attack path, and remediation. Coverage carries surface dispositions, immutable receipts, explicit exclusions, deferred work, and completeness. `adjudication.json` is a sealed supplemental artifact containing the parent decisions and rejection reasons. Dynamic bundles also seal `dynamic-validation.json`; PoC files are published only for candidates that are parent-accepted, statically confirmed, and dynamically reproduced. Clean Git roots are revision-bound; dirty worktrees add a content snapshot digest. Markdown, SARIF, and `threat-model.json` remain readable projections outside the canonical contract.

Normal completion, errors, and cancellation remove the named containers, final images, and `~/.flocks/workspace/code-security/runtime/docker/<scan_id>/` files created by the scan. A hard host-process or daemon crash can leave labeled resources. Inspect and remove only resources carrying `flocks.code_security.scan_id=<scan_id>`; do not use a broad Docker prune. Shared Docker build cache is daemon-managed and is not part of the exact cleanup guarantee.

Coverage completeness describes static source coverage, not the absence of environmental uncertainty. Inventoried zero-byte files are counted as `notApplicable`; unread or failed non-empty files, missing receipts, incomplete work units, snapshot omissions, unverified candidates, and explicitly blocking coverage questions still produce `partial`. External-service, deployment, runtime, and unresolved-hypothesis questions are retained as non-blocking validation limitations.

The threat-model and completed-scan contract semantics are adapted from the Apache-2.0-licensed OpenAI Codex Security bundled plugin. See `THIRD_PARTY_NOTICES.md` for attribution. This implementation retains Flocks' immutable snapshot, dedicated-session, tool-projection, and persistence boundaries; it does not embed or launch the Codex Security MCP server. Change audits, shared threat-model caching, supplied-model overrides, remote Docker, external-network probes, and crash recovery remain outside the current version.

Run the plugin regression suite from the Flocks checkout with:

```bash
.venv/bin/pytest -q .flocks/plugins/flocks-code-security/tests
```
