#!/usr/bin/env node
/**
 * Flocks npm wrapper
 *
 * Flocks is a Python package. This thin wrapper detects available Python
 * launchers and delegates to the real `flocks` CLI.
 *
 * Install preference order:
 *   1. flocks         — globally installed (symlink / wrapper → .venv)
 *   2. uvx flocks     — uv's tool runner (for PyPI-published packages)
 *   3. pipx run flocks — pipx fallback
 *
 * Usage:
 *   npx @flocks-ai/flocks [command] [options]
 *   npx @flocks-ai/flocks skill install clawhub:github
 */

import { spawnSync, execFileSync } from "node:child_process"
import { existsSync } from "node:fs"

const args = process.argv.slice(2)

function hasCommand(cmd) {
  try {
    execFileSync(cmd, ["--version"], { stdio: "ignore" })
    return true
  } catch {
    return false
  }
}

function run(launcher, launcherArgs) {
  const result = spawnSync(launcher, [...launcherArgs, ...args], {
    stdio: "inherit",
    shell: false,
  })
  process.exit(result.status ?? 1)
}

// 1. Try globally installed flocks (symlink / wrapper pointing to .venv)
if (hasCommand("flocks")) {
  run("flocks", [])
}

// 2. Try uvx (uv's tool runner — for PyPI-published packages)
if (hasCommand("uvx")) {
  run("uvx", ["flocks"])
}

// 3. Try pipx run
if (hasCommand("pipx")) {
  run("pipx", ["run", "flocks"])
}

// 4. Nothing found — guide the user
console.error(`
  Error: Flocks requires Python.

  Quick install options:
    • Run the install script (recommended):
        curl -fsSL https://raw.githubusercontent.com/AgentFlocks/Flocks/main/install.sh | bash

    • Or install uv and use npx:
        curl -LsSf https://astral.sh/uv/install.sh | sh
        npx @flocks-ai/flocks

  See: https://github.com/flocks-ai/flocks
`)
process.exit(1)
