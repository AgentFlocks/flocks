export * from "./gen/types.gen.js"

import { readFileSync, statSync } from "fs"
import os from "os"
import path from "path"
import { createClient } from "./gen/client/client.gen.js"
import { type Config } from "./gen/client/types.gen.js"
import { FlocksClient } from "./gen/sdk.gen.js"
export { type Config as FlocksClientConfig, FlocksClient }

const API_TOKEN_SECRET_ID = "server_api_token"

let tokenCache: { path: string; mtimeMs: number; value: string | undefined } | undefined

function getStoredApiToken(): string | undefined {
  if (typeof process === "undefined") return undefined
  const configDir = process.env.FLOCKS_CONFIG_DIR || path.join(os.homedir(), ".flocks", "config")
  const secretFile = path.join(configDir, ".secret.json")

  try {
    const stat = statSync(secretFile)
    if (
      tokenCache &&
      tokenCache.path === secretFile &&
      tokenCache.mtimeMs === stat.mtimeMs
    ) {
      return tokenCache.value
    }
    const parsed = JSON.parse(readFileSync(secretFile, "utf-8")) as Record<string, unknown>
    const raw = parsed[API_TOKEN_SECRET_ID]
    const value = typeof raw === "string" && raw.trim() ? raw.trim() : undefined
    tokenCache = { path: secretFile, mtimeMs: stat.mtimeMs, value }
    return value
  } catch {
    tokenCache = undefined
    return undefined
  }
}

function hasAuthHeader(headers: Headers) {
  return headers.has("authorization") || headers.has("x-flocks-api-token")
}

function withAuthHeaders(config?: Config & { directory?: string }) {
  const headers = new Headers(config?.headers as HeadersInit | undefined)
  const apiToken = getStoredApiToken()
  const hasAuth = hasAuthHeader(headers)
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

  const callerHasAuth = hasAuthHeader(new Headers(config?.headers as HeadersInit | undefined))
  const headers = withAuthHeaders(config)

  if (config?.directory) {
    headers.set("x-flocks-directory", config.directory)
  }
  config = { ...config, headers: Object.fromEntries(headers.entries()) }

  const client = createClient(config)
  if (!callerHasAuth) {
    // token 轮换（.secret.json 被改写、mtime 变化）后新请求要带新 token：
    // 创建 client 时只读一次会让运行中的 TUI 一直用旧 token 报 401，直到重启
    client.interceptors.request.use((request) => {
      const apiToken = getStoredApiToken()
      if (apiToken) request.headers.set("Authorization", `Bearer ${apiToken}`)
      return request
    })
  }
  return new FlocksClient({ client })
}
