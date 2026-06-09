# Contributing Guide

Thank you for contributing to `flocks`. We welcome bug fixes, documentation improvements, tests, UX polish, new features, and other well-scoped changes that make the project better.

This guide explains how to contribute in a way that is easy to review, maintain, and merge.

## Ways to Contribute

You can contribute by:

- reporting bugs with clear reproduction steps
- proposing features or design improvements
- improving documentation, examples, and developer experience
- fixing issues and adding regression coverage
- improving the WebUI, CLI, workflows, plugins, tools, or platform integrations

If your change is large or affects architecture, public behavior, or user workflows, please open an Issue first so the direction can be discussed before implementation starts.

## Before You Start

Before writing code, please:

1. Search existing Issues and Pull Requests to avoid duplicate work.
2. Confirm the scope for larger features, refactors, or behavior changes.
3. Keep each contribution focused on one topic whenever possible.

## Development Environment

The main development stack for `flocks` currently includes:

- Python `3.12`
- `uv` for Python environment and dependency management
- Node.js `22+`
- `npm` for frontend dependencies

Recommended setup:

```bash
uv sync --group dev
cd webui && npm ci
```

If you work on browser-related features, you may also need the browser runtime dependencies described in the project README.

## Common Commands

Use `uv run` for Python-related commands whenever possible.

### Backend / Python

```bash
uv run ruff check .
uv run pytest
```

If your change is scoped to a smaller area, run the most relevant tests first:

```bash
uv run pytest tests/session
uv run pytest tests/cli/test_service_manager.py
```

### Frontend / WebUI

```bash
cd webui
npm run lint
npm run build
```

If your change touches both Python and frontend code, please run checks for both parts.

## Coding Standards

Please make sure your changes follow the repository conventions:

- Follow the Google Python Style Guide for Python code.
- Use `ruff` for linting and formatting-related checks.
- New features and bug fixes must include or update tests.
- Keep all test code under `tests/`.
- Except for the repository root `README.md`, feature guides, usage docs, and summary markdown files should go under `docs/`.
- Run Python commands with `uv run`, or from the project's active virtual environment.
- Any `.ps1` file in scripts must use **UTF-8 with BOM** encoding and **CRLF** line endings.

Please also follow these general principles:

- Keep changes focused and avoid unrelated refactors.
- Add type hints, error handling, and regression coverage where they meaningfully improve maintainability.
- Introduce new dependencies only when necessary, and explain why they are needed.
- Add brief comments for non-obvious logic, but avoid low-value commentary.

## Branching and Commits

Create your working branch from the latest `dev` branch. Do not develop directly on `main`, and do not open contribution PRs against `main` unless a maintainer explicitly asks for it.

Suggested branch naming examples:

- `feat/add-session-export`
- `fix/webui-login-redirect`
- `docs/contributing-guide`
- `refactor/mcp-client-cache`
- `test/add-workflow-route-cases`

Write commit messages in clear English. A Conventional Commits style is recommended:

```text
feat(cli): add service restart timeout option
fix(auth): preserve session after password reset
docs: add contributing guide
test(session): cover runner retry path
```

A good commit should:

- focus on one main change
- describe intent clearly in the title
- include extra context in the body when behavior, compatibility, or motivation needs explanation

## Testing Expectations

Please validate your change according to its scope:

- Documentation changes: verify links, commands, filenames, and paths.
- Python changes: run the relevant tests; for shared infrastructure changes, run broader coverage.
- Frontend changes: run at least `npm run lint` and `npm run build`.
- Cross-cutting changes: include enough automated or manual verification to show that the change works as intended.

If you are fixing a bug, prefer adding a regression test that reproduces the issue before or alongside the fix.

## Pull Request Guidelines

All contribution PRs for `flocks` should target the `dev` branch.

When opening a PR, structure the description so reviewers can quickly identify the change points, the scope of impact, and the business logic that needs careful review. A good PR description answers three core questions:

1. **What changed** — the concrete change points in this PR.
2. **What is affected** — the impact scope (users, APIs, configuration, dependencies, performance).
3. **Where to look closely** — the business logic, invariants, and edge cases that reviewers should focus on.

### 1. Key Changes (改动点)

List the concrete changes grouped by area (backend, frontend, docs, tests, etc.). Each bullet should describe one reviewable change, not a vague summary.

- What was added, modified, or removed.
- Which modules, files, or APIs are touched.
- Any new public interfaces, configuration options, environment variables, or CLI flags.

### 2. Impact Scope (影响范围)

Explain who and what is affected. Cover every category that applies, and state explicitly when a category has no impact (e.g. "No public API change.").

- **User-visible behavior**: UX, UI, CLI, API responses, log/event output, or output format changes. Include screenshots or recordings for UI changes.
- **Compatibility**: backward-compatibility impact, deprecations, default-value changes, or required migrations.
- **Configuration & environment**: new required settings, environment variables, secrets, or deployment changes.
- **Dependencies**: any newly added or upgraded third-party packages, and why they are needed.
- **Performance & resources**: expected impact on latency, memory, CPU, network, or storage.
- **Security & permissions**: authentication, authorization, data-privacy, or secret-handling touch points.

### 3. Business Logic to Focus On During Review (需重点 Review 的业务逻辑)

Call out the parts of the change that deserve extra reviewer attention. This section is the most valuable part of the PR description — it shortens review time and reduces back-and-forth.

- The specific functions, classes, endpoints, or flows that implement the core logic.
- Assumptions, preconditions, or invariants the code relies on, and why they hold.
- Edge cases and error paths that are easy to miss (empty input, partial failure, retry, timeout, concurrency, ordering).
- Any cross-module contract (e.g. how this change interacts with existing APIs, plugins, or workflows).
- Anything you are uncertain about and would like a second opinion on — say so explicitly.

### 4. Why This Approach

Briefly justify the chosen approach over reasonable alternatives, especially for non-trivial changes.

### 5. Test Plan & Validation

- Which tests you added or updated, and which suites you ran.
- Any manual verification, reproduction scripts, or staging checks.
- For UI changes, attach before/after screenshots or short recordings.

### 6. Compatibility, Migration & Rollback

- Any breaking changes and the migration path.
- New or changed configuration, environment variables, or feature flags.
- Rollback strategy if the change is risky.

---

Recommended PR description template:

```markdown
## Summary
- One or two sentences stating the goal of this PR.

## Key Changes
- ...

## Impact Scope
- User-visible behavior:
- Compatibility / migration:
- Configuration / environment:
- Dependencies:
- Performance / resources:
- Security / permissions:

## Business Logic to Review
- ...
- ...

## Why This Approach
- ...

## Test Plan
- [x] uv run pytest ...
- [x] npm run lint
- [ ] Manual verification

## Compatibility, Migration & Rollback
- ...
```

Please keep PRs as small and focused as practical. Multiple reviewable PRs are usually easier to merge than one large mixed change.

## Issue Reporting

This repository already provides GitHub Issue templates. Please choose the most appropriate template and include enough detail to make triage efficient:

- Bug reports: reproduction steps, expected behavior, actual behavior, logs, and version information
- Feature requests: motivation, proposed solution, alternatives considered, and expected impact
- Plugin / tool requests: target use case, inputs, outputs, and relevant constraints

High-quality Issues significantly improve response time and implementation quality.

## Security Issues

If you discover a security vulnerability or any issue that could expose users or deployments to risk, please do not disclose sensitive details in a public Issue. Contact the maintainers through an approved private channel first, then coordinate on disclosure after a fix is available.

## Communication

Please keep communication respectful, specific, and constructive:

- discuss the problem, not the person
- provide evidence and context, not just conclusions
- stay open to review feedback, and split changes if needed

We strongly prefer incremental, testable, reviewable contributions over large rewrites.

## Pre-PR Checklist

Before opening a PR, please confirm:

- [ ] the change is focused and does not include unrelated edits
- [ ] code, naming, and documentation style match the repository
- [ ] new features or bug fixes include appropriate tests
- [ ] relevant local checks have passed
- [ ] the PR clearly explains background, approach, and validation
- [ ] the PR targets `dev`
- [ ] any new markdown documentation has been added under `docs/` when applicable

Thank you for helping improve `flocks`.
