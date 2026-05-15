import { BusEvent } from "@/bus/bus-event"
import path from "path"
import { $ } from "bun"
import { spawn } from "child_process"
import z from "zod"
import { NamedError } from "@flocks-ai/util/error"
import { Log } from "../util/log"
import { iife } from "@/util/iife"
import { Flag } from "../flag/flag"

declare global {
  const FLOCKS_VERSION: string
  const FLOCKS_CHANNEL: string
}

export namespace Installation {
  const log = Log.create({ service: "installation" })

  export type Method = Awaited<ReturnType<typeof method>>

  export const Event = {
    Updated: BusEvent.define(
      "installation.updated",
      z.object({
        version: z.string(),
      }),
    ),
    UpdateAvailable: BusEvent.define(
      "installation.update-available",
      z.object({
        version: z.string(),
      }),
    ),
  }

  export const Info = z
    .object({
      version: z.string(),
      latest: z.string(),
    })
    .meta({
      ref: "InstallationInfo",
    })
  export type Info = z.infer<typeof Info>

  export async function info() {
    return {
      version: VERSION,
      latest: await latest(),
    }
  }

  export function isPreview() {
    return CHANNEL !== "latest"
  }

  export function isLocal() {
    return CHANNEL === "local"
  }

  export async function method() {
    if (process.execPath.includes(path.join(".flocks", "bin"))) return "curl"
    if (process.execPath.includes(path.join(".local", "bin"))) return "curl"
    const exec = process.execPath.toLowerCase()

    const checks = [
      {
        name: "npm" as const,
        command: () => $`npm list -g --depth=0`.throws(false).quiet().text(),
      },
      {
        name: "yarn" as const,
        command: () => $`yarn global list`.throws(false).quiet().text(),
      },
      {
        name: "pnpm" as const,
        command: () => $`pnpm list -g --depth=0`.throws(false).quiet().text(),
      },
      {
        name: "bun" as const,
        command: () => $`bun pm ls -g`.throws(false).quiet().text(),
      },
      {
        name: "brew" as const,
        command: () => $`brew list --formula flocks`.throws(false).quiet().text(),
      },
      {
        name: "scoop" as const,
        command: () => $`scoop list flocks`.throws(false).quiet().text(),
      },
      {
        name: "choco" as const,
        command: () => $`choco list --limit-output flocks`.throws(false).quiet().text(),
      },
    ]

    checks.sort((a, b) => {
      const aMatches = exec.includes(a.name)
      const bMatches = exec.includes(b.name)
      if (aMatches && !bMatches) return -1
      if (!aMatches && bMatches) return 1
      return 0
    })

    for (const check of checks) {
      const output = await check.command()
      const installedName =
        check.name === "brew" || check.name === "choco" || check.name === "scoop" ? "opencode" : "opencode-ai"
      if (output.includes(installedName)) {
        return check.name
      }
    }

    return "unknown"
  }

  export const UpgradeFailedError = NamedError.create(
    "UpgradeFailedError",
    z.object({
      stderr: z.string(),
    }),
  )

  const DEFAULT_NPM_REGISTRY = "https://registry.npmjs.org"

  export function normalizeNpmRegistry(registry?: string | null) {
    const value = registry?.trim() || DEFAULT_NPM_REGISTRY
    return value.endsWith("/") ? value.slice(0, -1) : value
  }

  export function resolveAgentBrowserNpmRegistry(input: {
    flocksNpmRegistry?: string | null
    npmConfigRegistry?: string | null
    npmConfigRegistryUpper?: string | null
    configuredRegistry?: string | null
  }) {
    return normalizeNpmRegistry(
      [
        input.flocksNpmRegistry,
        input.npmConfigRegistry,
        input.npmConfigRegistryUpper,
        input.configuredRegistry,
      ].find((registry) => registry?.trim()),
    )
  }

  function explicitAgentBrowserNpmRegistry() {
    const registry = [
      process.env.FLOCKS_NPM_REGISTRY,
      process.env.npm_config_registry,
      process.env.NPM_CONFIG_REGISTRY,
    ].find((registry) => registry?.trim())

    return registry ? normalizeNpmRegistry(registry) : undefined
  }

  export function updateAgentBrowserInBackground() {
    const registry = explicitAgentBrowserNpmRegistry()
    const env = registry
      ? {
          ...process.env,
          npm_config_registry: registry,
        }
      : process.env

    try {
      const npm = process.platform === "win32" ? "npm.cmd" : "npm"
      const child = spawn(npm, ["update", "-g", "agent-browser"], {
        detached: true,
        env,
        stdio: "ignore",
        windowsHide: true,
      })

      child.on("error", (error) => {
        log.warn("agent-browser background update failed to start", { error })
      })
      child.unref()
    } catch (error) {
      log.warn("agent-browser background update failed to start", { error })
      return
    }

    log.info("agent-browser background update started", {
      registry: registry ?? "npm-config",
    })
  }

  async function getBrewFormula() {
    const tapFormula = await $`brew list --formula anomalyco/tap/opencode`.throws(false).quiet().text()
    if (tapFormula.includes("opencode")) return "anomalyco/tap/opencode"
    const coreFormula = await $`brew list --formula flocks`.throws(false).quiet().text()
    if (coreFormula.includes("opencode")) return "opencode"
    return "opencode"
  }

  export async function upgrade(method: Method, target: string) {
    let cmd
    switch (method) {
      case "curl":
        cmd = $`curl -fsSL https://opencode.ai/install | bash`.env({
          ...process.env,
          VERSION: target,
        })
        break
      case "npm":
        cmd = $`npm install -g flocks-ai@${target}`
        break
      case "pnpm":
        cmd = $`pnpm install -g flocks-ai@${target}`
        break
      case "bun":
        cmd = $`bun install -g flocks-ai@${target}`
        break
      case "brew": {
        const formula = await getBrewFormula()
        cmd = $`brew upgrade ${formula}`.env({
          HOMEBREW_NO_AUTO_UPDATE: "1",
          ...process.env,
        })
        break
      }
      case "choco":
        cmd = $`echo Y | choco upgrade flocks --version=${target}`
        break
      case "scoop":
        cmd = $`scoop install flocks@${target}`
        break
      default:
        throw new Error(`Unknown method: ${method}`)
    }
    const result = await cmd.quiet().throws(false)
    if (result.exitCode !== 0) {
      const stderr = method === "choco" ? "not running from an elevated command shell" : result.stderr.toString("utf8")
      throw new UpgradeFailedError({
        stderr: stderr,
      })
    }
    log.info("upgraded", {
      method,
      target,
      stdout: result.stdout.toString(),
      stderr: result.stderr.toString(),
    })
    updateAgentBrowserInBackground()
    await $`${process.execPath} --version`.nothrow().quiet().text()
  }

  export const VERSION = typeof FLOCKS_VERSION === "string" ? FLOCKS_VERSION : "local"
  export const CHANNEL = typeof FLOCKS_CHANNEL === "string" ? FLOCKS_CHANNEL : "local"
  export const USER_AGENT = `flocks/${CHANNEL}/${VERSION}/${Flag.FLOCKS_CLIENT}`

  export async function latest(installMethod?: Method) {
    const detectedMethod = installMethod || (await method())

    if (detectedMethod === "brew") {
      const formula = await getBrewFormula()
      if (formula === "opencode") {
        return fetch("https://formulae.brew.sh/api/formula/flocks.json")
          .then((res) => {
            if (!res.ok) throw new Error(res.statusText)
            return res.json()
          })
          .then((data: any) => data.versions.stable)
      }
    }

    if (detectedMethod === "npm" || detectedMethod === "bun" || detectedMethod === "pnpm") {
      const registry = await iife(async () => {
        const r = (await $`npm config get registry`.quiet().nothrow().text()).trim()
        const reg = r || "https://registry.npmjs.org"
        return reg.endsWith("/") ? reg.slice(0, -1) : reg
      })
      const channel = CHANNEL
      return fetch(`${registry}/flocks-ai/${channel}`)
        .then((res) => {
          if (!res.ok) throw new Error(res.statusText)
          return res.json()
        })
        .then((data: any) => data.version)
    }

    if (detectedMethod === "choco") {
      return fetch(
        "https://community.chocolatey.org/api/v2/Packages?$filter=Id%20eq%20%27flocks%27%20and%20IsLatestVersion&$select=Version",
        { headers: { Accept: "application/json;odata=verbose" } },
      )
        .then((res) => {
          if (!res.ok) throw new Error(res.statusText)
          return res.json()
        })
        .then((data: any) => data.d.results[0].Version)
    }

    if (detectedMethod === "scoop") {
      return fetch("https://raw.githubusercontent.com/ScoopInstaller/Main/master/bucket/flocks.json", {
        headers: { Accept: "application/json" },
      })
        .then((res) => {
          if (!res.ok) throw new Error(res.statusText)
          return res.json()
        })
        .then((data: any) => data.version)
    }

    return fetch("https://api.github.com/repos/anomalyco/flocks/releases/latest")
      .then((res) => {
        if (!res.ok) throw new Error(res.statusText)
        return res.json()
      })
      .then((data: any) => data.tag_name.replace(/^v/, ""))
  }
}
