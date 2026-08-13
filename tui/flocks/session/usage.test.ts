import { describe, expect, test } from "bun:test"
import type { LanguageModelUsage, ProviderMetadata } from "ai"
import { normalizeCacheUsage, normalizeUsageToken } from "./usage"

function usage(input: Record<string, unknown>): LanguageModelUsage {
  return input as LanguageModelUsage
}

function metadata(input: Record<string, unknown>): ProviderMetadata {
  return input as ProviderMetadata
}

describe("normalizeCacheUsage", () => {
  const cases: Array<{
    name: string
    usage: LanguageModelUsage
    metadata?: ProviderMetadata
    expected: { read: number; write: number }
  }> = [
    {
      name: "AI SDK cached input tokens",
      usage: usage({ cachedInputTokens: 12 }),
      expected: { read: 12, write: 0 },
    },
    {
      name: "OpenAI chat cached tokens",
      usage: usage({ prompt_tokens_details: { cached_tokens: 128 } }),
      expected: { read: 128, write: 0 },
    },
    {
      name: "OpenAI responses cached tokens",
      usage: usage({ input_tokens_details: { cached_tokens: 256 } }),
      expected: { read: 256, write: 0 },
    },
    {
      name: "DeepSeek prompt cache hit tokens",
      usage: usage({ prompt_cache_hit_tokens: 512, prompt_cache_miss_tokens: 64 }),
      expected: { read: 512, write: 0 },
    },
    {
      name: "Anthropic raw usage tokens",
      usage: usage({ cache_read_input_tokens: 32, cache_creation_input_tokens: 16 }),
      expected: { read: 32, write: 16 },
    },
    {
      name: "Anthropic provider metadata tokens",
      usage: usage({}),
      metadata: metadata({ anthropic: { cacheReadInputTokens: 24, cacheCreationInputTokens: 8 } }),
      expected: { read: 24, write: 8 },
    },
    {
      name: "Bedrock provider metadata tokens",
      usage: usage({}),
      metadata: metadata({ bedrock: { usage: { cacheReadInputTokens: 48, cacheWriteInputTokens: 12 } } }),
      expected: { read: 48, write: 12 },
    },
    {
      name: "Google cached content tokens",
      usage: usage({ usageMetadata: { cachedContentTokenCount: 96 } }),
      expected: { read: 96, write: 0 },
    },
    {
      name: "Gateway cache tokens",
      usage: usage({ input_cache_read: 144, input_cache_write: 36 }),
      expected: { read: 144, write: 36 },
    },
    {
      name: "OpenAI-compatible cache write tokens",
      usage: usage({ prompt_tokens_details: { cache_write_tokens: 72 } }),
      expected: { read: 0, write: 72 },
    },
    {
      name: "positive raw fallback when SDK standard field is zero",
      usage: usage({ cachedInputTokens: 0, prompt_cache_hit_tokens: 80 }),
      expected: { read: 80, write: 0 },
    },
    {
      name: "OpenAI-compatible provider metadata raw DeepSeek tokens",
      usage: usage({ inputTokens: 640 }),
      metadata: metadata({ deepseek: { usage: { prompt_cache_hit_tokens: 512, prompt_cache_miss_tokens: 128 } } }),
      expected: { read: 512, write: 0 },
    },
    {
      name: "OpenAI-compatible provider metadata raw cache write tokens",
      usage: usage({}),
      metadata: metadata({ custom: { usage: { prompt_tokens_details: { cache_creation_tokens: 44 } } } }),
      expected: { read: 0, write: 44 },
    },
  ]

  for (const item of cases) {
    test(item.name, () => {
      expect(normalizeCacheUsage({ usage: item.usage, metadata: item.metadata })).toEqual(item.expected)
    })
  }

  test("ignores cache miss tokens for cache write", () => {
    expect(normalizeCacheUsage({ usage: usage({ prompt_cache_miss_tokens: 100 }) })).toEqual({
      read: 0,
      write: 0,
    })
  })

  test("normalizes invalid and negative values to zero", () => {
    expect(
      normalizeCacheUsage({
        usage: usage({
          cachedInputTokens: Number.NaN,
          cacheCreationInputTokens: -10,
        }),
      }),
    ).toEqual({ read: 0, write: 0 })
    expect(normalizeUsageToken(Number.POSITIVE_INFINITY)).toBe(0)
    expect(normalizeUsageToken(-1)).toBe(0)
  })
})
