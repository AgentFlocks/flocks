# Flocks Code Security

Project-level Flocks plugin for static source-code security audits.

Current implementation provides:

- four isolated code-security agents;
- a subset-only callable-tool projection;
- reproducible read-only source snapshots;
- snapshot inventory, bounded reads, and literal search;
- isolated background baseline and independent-verification workers;
- SQLite scan, work-unit, session-binding, candidate, verdict, coverage, and batch storage;
- fail-closed work-unit scope, state-transition, omission, and evidence validation;
- deterministic JSON, Markdown, and SARIF report generation.

Target code is copied into the plugin snapshot store. The plugin never runs target code, build scripts, tests, or Git hooks.

Standard static audits now support the full baseline → verification → reduction workflow through Flocks' existing background-session manager. Focused investigation, change audits, and dynamic execution remain outside the current version.

Run the plugin regression suite from the Flocks checkout with:

```bash
.venv/bin/pytest -q .flocks/plugins/flocks-code-security/tests
```
