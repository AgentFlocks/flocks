import { describe, expect, test } from "bun:test"
import type { Provider } from "@/provider/provider"
import { SystemPrompt } from "./system"
import {
  PROMPT_ANTHROPIC,
  PROMPT_ANTHROPIC_SPOOF,
  PROMPT_CODEX,
  PROMPT_FLOCKS_CONFIG_GUARD,
  PROMPT_GENERAL,
} from "./prompt-source"

function createModel(id: string): Provider.Model {
  return { api: { id } } as Provider.Model
}

describe("system prompt", () => {
  test("uses the Python anthropic prompt for Claude models", () => {
    expect(SystemPrompt.provider(createModel("claude-sonnet-4"))).toEqual([PROMPT_ANTHROPIC])
  })

  test("uses the Python general prompt as the fallback", () => {
    expect(SystemPrompt.provider(createModel("qwen-max"))).toEqual([PROMPT_GENERAL])
  })

  test("trims the Python anthropic spoof header", () => {
    expect(SystemPrompt.header("anthropic")).toEqual([
      PROMPT_ANTHROPIC_SPOOF.trim(),
      PROMPT_FLOCKS_CONFIG_GUARD.trim(),
    ])
  })

  test("adds the Flocks configuration guard for every provider", () => {
    expect(SystemPrompt.header("openai")).toEqual([PROMPT_FLOCKS_CONFIG_GUARD.trim()])
  })

  test("trims the Python codex instructions", () => {
    expect(SystemPrompt.instructions()).toBe(PROMPT_CODEX.trim())
  })
})
