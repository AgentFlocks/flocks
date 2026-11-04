/**
 * Flocks TUI Entry Point
 * 
 * Terminal User Interface for Flocks AI-Native SecOps Platform.
 */

import yargs from "yargs"
import { hideBin } from "yargs/helpers"
import { AttachCommand } from "@tui/attach"
import { Log } from "@/util/log"
import { UI } from "@/cli/ui"
import { Installation } from "@/installation"
import { NamedError } from "@flocks-ai/util/error"
import { FormatError } from "@/cli/error"
import { EOL } from "os"

// Set up error handlers
process.on("unhandledRejection", (e) => {
  Log.Default.error("rejection", {
    e: e instanceof Error ? e.message : e,
  })
})

process.on("uncaughtException", (e) => {
  Log.Default.error("exception", {
    e: e instanceof Error ? e.message : e,
  })
})

// Custom logo for Flocks
function flocksLogo(): string {
  return `
    ███████╗██╗      ██████╗  ██████╗██╗  ██╗███████╗
    ██╔════╝██║     ██╔═══██╗██╔════╝██║ ██╔╝██╔════╝
    █████╗  ██║     ██║   ██║██║     █████╔╝ ███████╗
    ██╔══╝  ██║     ██║   ██║██║     ██╔═██╗ ╚════██║
    ██║     ███████╗╚██████╔╝╚██████╗██║  ██╗███████║
    ╚═╝     ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝
                        TUI
  `
}

const cli = yargs(hideBin(process.argv))
  .parserConfiguration({ "populate--": true })
  .scriptName("flocks-tui")
  .wrap(100)
  .help("help", "show help")
  .alias("help", "h")
  .version("version", "show version number", "1.0.0")
  .alias("version", "v")
  .option("print-logs", {
    describe: "print logs to stderr",
    type: "boolean",
  })
  .option("log-level", {
    describe: "log level",
    type: "string",
    choices: ["DEBUG", "INFO", "WARN", "ERROR"],
  })
  .middleware(async (opts) => {
    await Log.init({
      print: process.argv.includes("--print-logs"),
      dev: false,
      level: opts.logLevel as Log.Level || "INFO",
    })

    process.env.AGENT = "1"
    process.env.FLOCKS = "1"

    Log.Default.info("flocks-tui", {
      version: "1.0.0",
      args: process.argv.slice(2),
    })
  })
  .usage("\n" + flocksLogo())
  .command(AttachCommand)
  .fail((msg, err) => {
    if (
      msg?.startsWith("Unknown argument") ||
      msg?.startsWith("Not enough non-option arguments") ||
      msg?.startsWith("Invalid values:")
    ) {
      if (err) throw err
      // 自定义 fail 后 yargs 不再自行输出校验信息，先把原因打出来再给帮助
      console.error(msg + EOL)
      cli.showHelp("log")
    }
    if (err) throw err
    process.exit(1)
  })
  .strict()

try {
  await cli.parse()
} catch (e) {
  let data: Record<string, any> = {}
  if (e instanceof NamedError) {
    const obj = e.toObject()
    Object.assign(data, {
      ...obj.data,
    })
  }

  if (e instanceof Error) {
    Object.assign(data, {
      name: e.name,
      message: e.message,
      cause: e.cause?.toString(),
      stack: e.stack,
    })
  }

  Log.Default.error("fatal", data)
  const formatted = FormatError(e)
  if (formatted) UI.error(formatted)
  if (formatted === undefined) {
    UI.error("Unexpected error, check log file at " + Log.file() + " for more details" + EOL)
    console.error(e instanceof Error ? e.message : String(e))
  }
  process.exitCode = 1
} finally {
  process.exit()
}
