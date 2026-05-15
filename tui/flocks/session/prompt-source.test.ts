import { describe, expect, test } from "bun:test"
import {
  BUILD_SWITCH,
  MAX_STEPS,
  PROMPT_ANTHROPIC,
  PROMPT_ANTHROPIC_SPOOF,
  PROMPT_BEAST,
  PROMPT_CODEX,
  PROMPT_GENERAL,
  PROMPT_GEMINI,
  PROMPT_PLAN,
  resolvePythonSessionPromptUrl,
} from "./prompt-source"

describe("prompt-source", () => {
  test("loads TUI session prompts from the Python prompt directory", async () => {
    const prompts = [
      ["anthropic.txt", PROMPT_ANTHROPIC],
      ["anthropic_spoof.txt", PROMPT_ANTHROPIC_SPOOF],
      ["beast.txt", PROMPT_BEAST],
      ["build-switch.txt", BUILD_SWITCH],
      ["codex_header.txt", PROMPT_CODEX],
      ["general.txt", PROMPT_GENERAL],
      ["gemini.txt", PROMPT_GEMINI],
      ["max-steps.txt", MAX_STEPS],
      ["plan.txt", PROMPT_PLAN],
    ] as const

    await Promise.all(
      prompts.map(async ([filename, content]) => {
        const expected = await Bun.file(resolvePythonSessionPromptUrl(filename)).text()
        expect(content).toBe(expected)
      }),
    )
  })
})
