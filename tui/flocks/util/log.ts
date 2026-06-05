import path from "path"
import fs from "fs/promises"
import { Global } from "../global"
import z from "zod"

export namespace Log {
  export const Level = z.enum(["DEBUG", "INFO", "WARN", "ERROR"]).meta({ ref: "LogLevel", description: "Log level" })
  export type Level = z.infer<typeof Level>

  const levelPriority: Record<Level, number> = {
    DEBUG: 0,
    INFO: 1,
    WARN: 2,
    ERROR: 3,
  }

  let level: Level = "INFO"

  function shouldLog(input: Level): boolean {
    return levelPriority[input] >= levelPriority[level]
  }

  export type Logger = {
    debug(message?: any, extra?: Record<string, any>): void
    info(message?: any, extra?: Record<string, any>): void
    error(message?: any, extra?: Record<string, any>): void
    warn(message?: any, extra?: Record<string, any>): void
    tag(key: string, value: string): Logger
    clone(): Logger
    time(
      message: string,
      extra?: Record<string, any>,
    ): {
      stop(): void
      [Symbol.dispose](): void
    }
  }

  const loggers = new Map<string, Logger>()

  export const Default = create({ service: "default" })

  export interface Options {
    print: boolean
    dev?: boolean
    level?: Level
  }

  let logpath = ""
  let errorLogpath = ""
  let logDate = ""
  export function file() {
    return logpath
  }
  let write = async (msg: any, error = false) => {
    process.stderr.write(msg)
    return msg.length
  }

  export async function init(options: Options) {
    if (options.level) level = options.level
    await cleanup(Global.Path.log)
    if (options.print) return
    await openDailyLogs()
    write = async (msg: any, error = false) => {
      await ensureCurrentDay()
      await fs.appendFile(logpath, msg)
      if (error) await fs.appendFile(errorLogpath, msg)
      return msg.length
    }
  }

  function todayString() {
    return new Date().toISOString().split("T")[0]
  }

  async function openDailyLogs() {
    logDate = todayString()
    const dir = path.join(Global.Path.log, logDate)
    await fs.mkdir(dir, { recursive: true })
    logpath = path.join(dir, "flocks.log")
    errorLogpath = path.join(dir, "errors.log")
  }

  async function ensureCurrentDay() {
    if (logDate === todayString()) return
    await openDailyLogs()
    await cleanup(Global.Path.log)
  }

  async function cleanup(dir: string) {
    const retentionDays = Number.parseInt(process.env.FLOCKS_LOG_RETENTION_DAYS || "30", 10)
    if (!Number.isFinite(retentionDays) || retentionDays <= 0) return
    const cutoff = Date.now() - retentionDays * 24 * 60 * 60 * 1000
    const cutoffDay = new Date(cutoff).toISOString().split("T")[0]
    const entries = await fs.readdir(dir, { withFileTypes: true }).catch(() => [])
    await Promise.all(
      entries.map(async (entry) => {
        const target = path.join(dir, entry.name)
        if (entry.isDirectory() && /^\d{4}-\d{2}-\d{2}$/.test(entry.name)) {
          if (entry.name < cutoffDay) {
            await fs.rm(target, { recursive: true, force: true }).catch(() => {})
          }
          return
        }
        if (entry.isFile() && /^\d{4}-\d{2}-\d{2}T\d{6}\.log(\.\d+)?$/.test(entry.name)) {
          const stamp = entry.name.split(".log")[0]
          if (new Date(stamp.replace(/T(\d{2})(\d{2})(\d{2})$/, "T$1:$2:$3")).getTime() < cutoff) {
            await fs.unlink(target).catch(() => {})
          }
        }
      }),
    )
  }

  function formatError(error: Error, depth = 0): string {
    const result = error.message
    return error.cause instanceof Error && depth < 10
      ? result + " Caused by: " + formatError(error.cause, depth + 1)
      : result
  }

  let last = Date.now()
  export function create(tags?: Record<string, any>) {
    tags = tags || {}

    const service = tags["service"]
    if (service && typeof service === "string") {
      const cached = loggers.get(service)
      if (cached) {
        return cached
      }
    }

    function build(message: any, extra?: Record<string, any>) {
      const prefix = Object.entries({
        ...tags,
        ...extra,
      })
        .filter(([_, value]) => value !== undefined && value !== null)
        .map(([key, value]) => {
          const prefix = `${key}=`
          if (value instanceof Error) return prefix + formatError(value)
          if (typeof value === "object") return prefix + JSON.stringify(value)
          return prefix + value
        })
        .join(" ")
      const next = new Date()
      const diff = next.getTime() - last
      last = next.getTime()
      return [next.toISOString().split(".")[0], "+" + diff + "ms", prefix, message].filter(Boolean).join(" ") + "\n"
    }
    const result: Logger = {
      debug(message?: any, extra?: Record<string, any>) {
        if (shouldLog("DEBUG")) {
          write("DEBUG " + build(message, extra))
        }
      },
      info(message?: any, extra?: Record<string, any>) {
        if (shouldLog("INFO")) {
          write("INFO  " + build(message, extra))
        }
      },
      error(message?: any, extra?: Record<string, any>) {
        if (shouldLog("ERROR")) {
          write("ERROR " + build(message, extra), true)
        }
      },
      warn(message?: any, extra?: Record<string, any>) {
        if (shouldLog("WARN")) {
          write("WARN  " + build(message, extra), true)
        }
      },
      tag(key: string, value: string) {
        if (tags) tags[key] = value
        return result
      },
      clone() {
        return Log.create({ ...tags })
      },
      time(message: string, extra?: Record<string, any>) {
        const now = Date.now()
        result.info(message, { status: "started", ...extra })
        function stop() {
          result.info(message, {
            status: "completed",
            duration: Date.now() - now,
            ...extra,
          })
        }
        return {
          stop,
          [Symbol.dispose]() {
            stop()
          },
        }
      },
    }

    if (service && typeof service === "string") {
      loggers.set(service, result)
    }

    return result
  }
}
