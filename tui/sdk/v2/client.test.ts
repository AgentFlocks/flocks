import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdtempSync, rmSync, utimesSync, writeFileSync } from "fs"
import { tmpdir } from "os"
import path from "path"

let dir: string
const prevConfigDir = process.env.FLOCKS_CONFIG_DIR

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), "flocks-sdk-token-"))
  process.env.FLOCKS_CONFIG_DIR = dir
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  if (prevConfigDir === undefined) delete process.env.FLOCKS_CONFIG_DIR
  else process.env.FLOCKS_CONFIG_DIR = prevConfigDir
})

function writeSecret(token: string, mtimeSeconds: number) {
  const file = path.join(dir, ".secret.json")
  writeFileSync(file, JSON.stringify({ server_api_token: token }))
  utimesSync(file, mtimeSeconds, mtimeSeconds)
}

describe("createFlocksClient token handling", () => {
  test("re-reads a rotated server_api_token on the next request (mtime cache)", async () => {
    const { createFlocksClient } = await import("./client.ts")
    writeSecret("token-old", 1_700_000_000)
    const seen: string[] = []
    const fakeFetch: any = async (req: Request) => {
      seen.push(req.headers.get("authorization") ?? "")
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
    }
    const client = createFlocksClient({ baseUrl: "http://127.0.0.1:9", fetch: fakeFetch })
    await client.config.get()
    writeSecret("token-new", 1_700_000_100)
    await client.config.get()
    expect(seen).toEqual(["Bearer token-old", "Bearer token-new"])
  })

  test("does not override an auth header supplied by the caller", async () => {
    const { createFlocksClient } = await import("./client.ts")
    writeSecret("token-old", 1_700_000_000)
    const seen: Record<string, string | null>[] = []
    const fakeFetch: any = async (req: Request) => {
      seen.push({ authorization: req.headers.get("authorization"), alt: req.headers.get("x-flocks-api-token") })
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
    }
    const client = createFlocksClient({ baseUrl: "http://127.0.0.1:9", fetch: fakeFetch, headers: { "x-flocks-api-token": "abc" } })
    await client.config.get()
    expect(seen).toEqual([{ authorization: null, alt: "abc" }])
  })
})
