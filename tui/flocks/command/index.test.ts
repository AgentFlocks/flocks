import { describe, expect, test } from "bun:test"
import { Command } from "."

describe("delegated commands", () => {
  test("delegates commands that explicitly request a subtask", () => {
    expect(Command.shouldDelegate({ subtask: true }, "primary")).toBe(true)
  })

  test("delegates subagent commands unless explicitly disabled", () => {
    expect(Command.shouldDelegate({}, "subagent")).toBe(true)
    expect(Command.shouldDelegate({ subtask: false }, "subagent")).toBe(false)
  })
})
