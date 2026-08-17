import { describe, expect, test } from "bun:test"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { Agent } from "../agent/agent"
import { Instance } from "../project/instance"
import { Command } from "./index"

describe("built-in commands", () => {
  test("review uses an available primary agent", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "flocks-review-command-"))
    try {
      await Instance.provide({
        directory,
        fn: async () => {
          const command = await Command.get(Command.Default.REVIEW)
          const agentName = command.agent ?? (await Agent.defaultAgent())

          expect(command.agent).toBeUndefined()
          expect(await Agent.get(agentName)).toBeDefined()
        },
      })
    } finally {
      await Instance.disposeAll()
      await rm(directory, { recursive: true, force: true })
    }
  })
})
