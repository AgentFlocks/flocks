import { describe, expect, test } from "bun:test"
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "fs"
import { tmpdir } from "os"
import path from "path"
import { createFlocksServer } from "./server"

describe("createFlocksServer", () => {
  test("spawns `flocks serve --host --port`, parses the Python startup line and waits for /api/ready", async () => {
    const bin = mkdtempSync(path.join(tmpdir(), "fake-flocks-"))
    const fixture = path.join(import.meta.dir, "__fixtures__", "fake-flocks.ts")
    writeFileSync(path.join(bin, "flocks"), `#!/bin/sh\nexec "${process.execPath}" "${fixture}" "$@"\n`)
    chmodSync(path.join(bin, "flocks"), 0o755)
    const prevPath = process.env.PATH
    process.env.PATH = `${bin}:${prevPath}`
    try {
      const server = await createFlocksServer({ port: 25278, timeout: 15000 })
      expect(server.url).toBe("http://127.0.0.1:25278")
      const ready = await fetch(`${server.url}/api/ready`).then((r) => r.status)
      expect(ready).toBe(200)
      server.close()
    } finally {
      process.env.PATH = prevPath
      rmSync(bin, { recursive: true, force: true })
    }
  })
})
