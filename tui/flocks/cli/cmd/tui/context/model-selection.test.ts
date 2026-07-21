import { describe, expect, test } from "bun:test"
import { selectFallbackModel } from "./model-selection"

describe("selectFallbackModel", () => {
  test("skips an empty provider when a later provider has a model", () => {
    expect(
      selectFallbackModel(
        [
          { id: "openai", models: {} },
          {
            id: "threatbook-cn-llm",
            models: {
              "minimax-m3": { id: "minimax-m3" },
            },
          },
        ],
        { "threatbook-cn-llm": "minimax-m3" },
      ),
    ).toEqual({
      providerID: "threatbook-cn-llm",
      modelID: "minimax-m3",
    })
  })

  test("uses the provider default when it is available", () => {
    expect(
      selectFallbackModel(
        [
          {
            id: "openai",
            models: {
              "gpt-4.1": { id: "gpt-4.1" },
              "gpt-5": { id: "gpt-5" },
            },
          },
        ],
        { openai: "gpt-5" },
      ),
    ).toEqual({ providerID: "openai", modelID: "gpt-5" })
  })

  test("uses the first model when the provider default is unavailable", () => {
    expect(
      selectFallbackModel(
        [
          {
            id: "openai",
            models: {
              "gpt-4.1": { id: "gpt-4.1" },
            },
          },
        ],
        { openai: "removed-model" },
      ),
    ).toEqual({ providerID: "openai", modelID: "gpt-4.1" })
  })

  test("returns undefined when no provider has a model", () => {
    expect(selectFallbackModel([{ id: "openai", models: {} }], {})).toBeUndefined()
  })
})
