import type { LanguageModelUsage, ProviderMetadata } from "ai"

type CacheUsageInput = {
  usage: LanguageModelUsage
  metadata?: ProviderMetadata
}

type CacheUsage = {
  read: number
  write: number
}

const CACHE_READ_USAGE_PATHS = [
  ["cachedInputTokens"],
  ["prompt_tokens_details", "cached_tokens"],
  ["input_tokens_details", "cached_tokens"],
  ["prompt_cache_hit_tokens"],
  ["usageMetadata", "cachedContentTokenCount"],
  ["cache_read_input_tokens"],
  ["cacheReadInputTokens"],
  ["input_cache_read"],
] as const

const CACHE_READ_METADATA_PATHS = [
  ["anthropic", "cacheReadInputTokens"],
  ["anthropic", "usage", "cache_read_input_tokens"],
  ["bedrock", "usage", "cacheReadInputTokens"],
  ["google", "usageMetadata", "cachedContentTokenCount"],
  ["gateway", "input_cache_read"],
  ["gateway", "usage", "input_cache_read"],
] as const

const CACHE_WRITE_USAGE_PATHS = [
  ["cacheCreationInputTokens"],
  ["cache_creation_input_tokens"],
  ["cacheWriteInputTokens"],
  ["prompt_tokens_details", "cache_write_tokens"],
  ["prompt_tokens_details", "cache_creation_tokens"],
  ["input_tokens_details", "cache_write_tokens"],
  ["input_tokens_details", "cache_creation_tokens"],
  ["input_cache_write"],
] as const

const CACHE_WRITE_METADATA_PATHS = [
  ["anthropic", "cacheCreationInputTokens"],
  ["anthropic", "usage", "cache_creation_input_tokens"],
  ["bedrock", "usage", "cacheWriteInputTokens"],
  ["gateway", "input_cache_write"],
  ["gateway", "usage", "input_cache_write"],
] as const

const PROVIDER_USAGE_CACHE_READ_PATHS = [
  ["usage", "prompt_tokens_details", "cached_tokens"],
  ["usage", "input_tokens_details", "cached_tokens"],
  ["usage", "prompt_cache_hit_tokens"],
] as const

const PROVIDER_USAGE_CACHE_WRITE_PATHS = [
  ["usage", "cacheCreationInputTokens"],
  ["usage", "cache_creation_input_tokens"],
  ["usage", "cacheWriteInputTokens"],
  ["usage", "prompt_tokens_details", "cache_write_tokens"],
  ["usage", "prompt_tokens_details", "cache_creation_tokens"],
  ["usage", "input_tokens_details", "cache_write_tokens"],
  ["usage", "input_tokens_details", "cache_creation_tokens"],
  ["usage", "input_cache_write"],
] as const

export function normalizeUsageToken(value: number | undefined | null) {
  if (typeof value !== "number") return 0
  if (!Number.isFinite(value)) return 0
  return Math.max(value, 0)
}

export function normalizeCacheUsage(input: CacheUsageInput): CacheUsage {
  const usage = input.usage as unknown
  const metadata = input.metadata as unknown

  return {
    read:
      firstPositiveNumber(usage, CACHE_READ_USAGE_PATHS) ??
      firstPositiveNumber(metadata, CACHE_READ_METADATA_PATHS) ??
      firstProviderUsageNumber(metadata, PROVIDER_USAGE_CACHE_READ_PATHS) ??
      0,
    write:
      firstPositiveNumber(usage, CACHE_WRITE_USAGE_PATHS) ??
      firstPositiveNumber(metadata, CACHE_WRITE_METADATA_PATHS) ??
      firstProviderUsageNumber(metadata, PROVIDER_USAGE_CACHE_WRITE_PATHS) ??
      0,
  }
}

function firstPositiveNumber(source: unknown, paths: readonly (readonly string[])[]) {
  let fallbackZero = false
  for (const path of paths) {
    const value = numberAt(source, path)
    if (value === undefined) continue
    if (value > 0) return value
    fallbackZero = true
  }
  return fallbackZero ? 0 : undefined
}

function numberAt(source: unknown, path: readonly string[]) {
  let current = source
  for (const key of path) {
    if (!isRecord(current)) return undefined
    current = current[key]
  }
  return normalizeNumber(current)
}

function firstProviderUsageNumber(metadata: unknown, paths: readonly (readonly string[])[]) {
  if (!isRecord(metadata)) return undefined
  let fallbackZero = false
  for (const providerMetadata of Object.values(metadata)) {
    for (const path of paths) {
      const value = numberAt(providerMetadata, path)
      if (value === undefined) continue
      if (value > 0) return value
      fallbackZero = true
    }
  }
  return fallbackZero ? 0 : undefined
}

function normalizeNumber(value: unknown) {
  if (typeof value !== "number") return undefined
  if (!Number.isFinite(value)) return 0
  return Math.max(value, 0)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
