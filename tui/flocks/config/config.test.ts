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
})
