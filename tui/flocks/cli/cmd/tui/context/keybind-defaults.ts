import { Config } from "@/config/config"

/**
 * 后端 /config 只回传它认识的键位子集（Python KeybindsConfig 仅 15 项），
 * 缺失的键位按 TUI 自身 Config.Keybinds 的默认值补齐；非字符串值忽略。
 */
export function resolveKeybinds(server: Record<string, unknown> | null | undefined): Record<string, string> {
  const custom = Object.fromEntries(
    Object.entries(server ?? {}).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  )
  return { ...Config.Keybinds.parse({}), ...custom }
}
