---
name: Bug report
about: Create a report to help us improve
title: ''
labels: ''
assignees: ''

---

name: Bug Report
description: Report a bug or unexpected behavior
title: "[Bug] "
labels: ["bug", "triage"]
body:
  - type: dropdown
    id: component
    attributes:
      label: Affected Component
      options:
        - Backend (Python/FastAPI)
        - WebUI (React)
        - TUI (Solid.js)
        - CLI
        - Agent / Session
        - Workflow Engine
        - Plugin / Tool
        - MCP / LSP
        - Channel (WeCom/Lark/DingTalk)
        - Docker / Deployment
        - Other
    validations:
      required: true

  - type: textarea
    id: description
    attributes:
      label: Description
      description: What happened, and what did you expect instead?
      placeholder: |
        A clear description of the bug.

        **Expected:** ...
        **Actual:** ...
    validations:
      required: true

  - type: textarea
    id: reproduce
    attributes:
      label: Steps to Reproduce
      placeholder: |
        1. Run `flocks start`
        2. Open ...
        3. Click / execute ...
        4. See error
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Logs / Error Messages
      description: |
        Paste relevant logs or error stack traces. Log locations:
        - `~/.flocks/logs/` (Windows: `%USERPROFILE%\.flocks\logs\`)
        - `<project>/.flocks/logs/`
        ⚠️ Please redact any sensitive information (API keys, tokens, etc.) before submitting.
      render: shell

  - type: input
    id: version
    attributes:
      label: Flocks Version
      description: Run `flocks --version`
      placeholder: "v2026.x.x"
    validations:
      required: true

  - type: dropdown
    id: os
    attributes:
      label: Operating System
      options:
        - macOS
        - Linux (Ubuntu/Debian)
        - Linux (CentOS/RHEL)
        - Linux (Other)
        - Windows
        - Docker Container
    validations:
      required: true

  - type: dropdown
    id: install-method
    attributes:
      label: Installation Method
      options:
        - uv / pip install
        - Docker
        - From Source
        - Other
    validations:
      required: true

  - type: textarea
    id: additional
    attributes:
      label: Additional Context
      description: Any other details — screenshots, Python version, LLM provider, config snippets, network environment, etc.
