export * from "./gen/types.gen.js"

import { readFileSync } from "fs"
import os from "os"
import path from "path"
import { createClient } from "./gen/client/client.gen.js"
import { type Config } from "./gen/client/types.gen.js"
import { FlocksClient } from "./gen/sdk.gen.js"
export { type Config as FlocksClientConfig, FlocksClient }

const API_TOKEN_SECRET_ID = "server_api_token"

function getStoredApiToken(): string | undefined {
  if (typeof process === "undefined") return undefined
  const configDir = process.env.FLOCKS_CONFIG_DIR || path.join(os.homedir(), ".flocks", "config")
  const secretFile = path.join(configDir, ".secret.json")
  try {
    const parsed = JSON.parse(readFileSync(secretFile, "utf-8")) as Record<string, unknown>
    const value = parsed[API_TOKEN_SECRET_ID]
    if (typeof value !== "string") return undefined
    return value.trim() || undefined
  } catch {
    return undefined
  }
}

function withAuthHeaders(config?: Config & { directory?: string }) {
  const headers = new Headers(config?.headers as HeadersInit | undefined)
  const apiToken = getStoredApiToken()
  const hasAuth = headers.has("authorization") || headers.has("x-flocks-api-token")
  if (apiToken && !hasAuth) {
    headers.set("Authorization", `Bearer ${apiToken}`)
  }
  return headers
}

export function createFlocksClient(config?: Config & { directory?: string }) {
  if (!config?.fetch) {
    const customFetch: any = (req: any) => {
      // @ts-ignore
      req.timeout = false
      return fetch(req)
    }
    config = {
      ...config,
      fetch: customFetch,
    }
  }

  const headers = withAuthHeaders(config)

  if (config?.directory) {
    headers.set("x-flocks-directory", config.directory)
  }
  config = { ...config, headers: Object.fromEntries(headers.entries()) }

  const client = createClient(config)
  return new FlocksClient({ client })
}
