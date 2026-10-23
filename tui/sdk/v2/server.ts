import { spawn } from "node:child_process"
import { type Config } from "./gen/types.gen.js"

export type ServerOptions = {
  hostname?: string
  port?: number
  signal?: AbortSignal
  timeout?: number
  config?: Config
}

export type TuiOptions = {
  project?: string
  model?: string
  session?: string
  agent?: string
  signal?: AbortSignal
  config?: Config
}

export async function createFlocksServer(options?: ServerOptions) {
  options = Object.assign(
    {
      hostname: "127.0.0.1",
      port: 8000,
      timeout: 5000,
    },
    options ?? {},
  )

  // Python CLI 的 serve 只接受 --host/--port（无 --hostname、无 --log-level）
  const args = [`serve`, `--host`, `${options.hostname}`, `--port`, `${options.port}`]

  const proc = spawn(`flocks`, args, {
    signal: options.signal,
    env: {
      ...process.env,
      FLOCKS_CONFIG_CONTENT: JSON.stringify(options.config ?? {}),
    },
  })

  const url = await new Promise<string>((resolve, reject) => {
    const deadline = Date.now() + options.timeout!
    const id = setTimeout(() => {
      reject(new Error(`Timeout waiting for server to start after ${options.timeout}ms`))
    }, options.timeout)
    let output = ""
    let polling = false
    proc.stdout?.on("data", (chunk) => {
      output += chunk.toString()
      const lines = output.split("\n")
      for (const line of lines) {
        // Python serve 打印 "Starting server on: http://host:port"（可能带 rich 颜色码）
        const match = line.match(/Starting server on:\s*(?:\x1b\[[0-9;]*m)*\s*(https?:\/\/[^\s\x1b]+)/)
        if (match && !polling) {
          // 这行在 uvicorn 绑定端口之前打印，等 /api/health 通了再返回
          polling = true
          waitReady(match[1]!, deadline, () => proc.exitCode !== null || !!options.signal?.aborted).then(
            (url) => {
              clearTimeout(id)
              resolve(url)
            },
            () => {},
          )
          return
        }
      }
    })
    proc.stderr?.on("data", (chunk) => {
      output += chunk.toString()
    })
    proc.on("exit", (code) => {
      clearTimeout(id)
      let msg = `Server exited with code ${code}`
      if (output.trim()) {
        msg += `\nServer output: ${output}`
      }
      reject(new Error(msg))
    })
    proc.on("error", (error) => {
      clearTimeout(id)
      reject(error)
    })
    if (options.signal) {
      options.signal.addEventListener("abort", () => {
        clearTimeout(id)
        reject(new Error("Aborted"))
      })
    }
  })

  return {
    url,
    close() {
      proc.kill()
    },
  }
}

/** 每 250ms 探一次 /api/health，直到就绪、超过 deadline 或 stop()（进程退出/被中止）为止 */
async function waitReady(url: string, deadline: number, stop: () => boolean): Promise<string> {
  while (Date.now() < deadline && !stop()) {
    const ok = await fetch(`${url}/api/health`)
      .then((r) => r.ok)
      .catch(() => false)
    if (ok) return url
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error("Server did not become ready")
}

export function createFlocksTui(options?: TuiOptions) {
  const args = []

  if (options?.project) {
    args.push(`--project=${options.project}`)
  }
  if (options?.model) {
    args.push(`--model=${options.model}`)
  }
  if (options?.session) {
    args.push(`--session=${options.session}`)
  }
  if (options?.agent) {
    args.push(`--agent=${options.agent}`)
  }

  const proc = spawn(`flocks`, args, {
    signal: options?.signal,
    stdio: "inherit",
    env: {
      ...process.env,
      FLOCKS_CONFIG_CONTENT: JSON.stringify(options?.config ?? {}),
    },
  })

  return {
    close() {
      proc.kill()
    },
  }
}
