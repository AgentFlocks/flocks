import { describe, expect, test } from "bun:test"
import { Config } from "./config"

describe("permission aliases", () => {
  test("maps task to delegate_task with deny precedence", () => {
    expect(
      Config.Permission.parse({
        delegate_task: "allow",
        task: { explore: "deny" },
      }),
    ).toEqual({ delegate_task: "deny" })
  })

  test.each([
    {
      task: { explore: "allow", "legacy-only": "ask" },
      delegate_task: { explore: "ask", "canonical-only": "allow" },
    },
    {
      delegate_task: { explore: "ask", "canonical-only": "allow" },
      task: { explore: "allow", "legacy-only": "ask" },
    },
  ])("merges task into delegate_task independently of key order", (permission) => {
    expect(Config.Permission.parse(permission)).toEqual({
      delegate_task: {
        explore: "ask",
        "legacy-only": "ask",
        "canonical-only": "allow",
      },
    })
  })

  test("preserves the legacy subtask command option during migration", () => {
    expect(
      Config.Command.parse({
        template: "Review this change",
        subtask: true,
      }).subtask,
    ).toBe(true)
  })
})
