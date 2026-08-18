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
- deterministic JSON, Markdown, and SARIF report generation.

Target code is copied into the plugin snapshot store. The plugin never runs target code, build scripts, tests, or Git hooks.

Standard static audits now support the full threat modeling → baseline → verification → reduction workflow through Flocks' existing background-session manager. Baseline workers must consume the persisted threat model before they can submit candidates or coverage. Finalization writes the canonical model to `threat-model.json` and projects it into `scan-manifest.json` and `report.md`.

The threat-model semantics are adapted from the Apache-2.0-licensed OpenAI Codex Security bundled plugin, specifically `skills/threat-model/SKILL.md` and `references/threat-model.md`. This implementation retains Flocks' immutable snapshot, dedicated-session, tool-projection, and persistence boundaries; it does not embed or launch the Codex Security MCP server. Focused investigation, change audits, shared threat-model caching, supplied-model overrides, and dynamic execution remain outside the current version.

Run the plugin regression suite from the Flocks checkout with:

```bash
.venv/bin/pytest -q .flocks/plugins/flocks-code-security/tests
```
