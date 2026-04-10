# @flocks-ai/flocks

npm wrapper for [Flocks](https://github.com/flocks-ai/flocks) — AI-Native SecOps platform.

Flocks is a Python package. This wrapper detects a globally installed `flocks` binary, `uvx`, or `pipx` and delegates to it.

## Quick start

```bash
# Zero-install (requires uv or pipx)
npx @flocks-ai/flocks

# Install skill from clawhub
npx @flocks-ai/flocks skill install clawhub:github

# Install skill from GitHub
npx @flocks-ai/flocks skill install github:owner/repo
```

## Prerequisites

Install `uv` (recommended):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or install Flocks directly via the install script:

```bash
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh | bash
```

## Skill registry

Skills can be installed from:

| Source | Example |
|--------|---------|
| clawhub.com | `flocks skill install clawhub:github` |
| GitHub URL | `flocks skill install github:owner/repo` |
| Direct URL | `flocks skill install https://...` |
| Local path | `flocks skill install ./my-skill` |
| SafeSkill (future) | `flocks skill install safeskill:ioc-lookup` |
