import type { JSONValue, SharedV2ProviderMetadata } from "@ai-sdk/provider"
import {
  OpenAICompatibleChatLanguageModel,
  OpenAICompatibleCompletionLanguageModel,
  OpenAICompatibleEmbeddingModel,
  OpenAICompatibleImageModel,
  VERSION as OPENAI_COMPATIBLE_VERSION,
  type OpenAICompatibleProviderSettings,
} from "@ai-sdk/openai-compatible"
import { withUserAgentSuffix, withoutTrailingSlash } from "@ai-sdk/provider-utils"

const USAGE_KEYS = [
  "prompt_tokens",
  "completion_tokens",
  "total_tokens",
  "prompt_cache_hit_tokens",
  "prompt_cache_miss_tokens",
] as const

const PROMPT_TOKEN_DETAIL_KEYS = [
  "cached_tokens",
  "cache_write_tokens",
  "cache_creation_tokens",
] as const

const INPUT_TOKEN_DETAIL_KEYS = [
  "cached_tokens",
  "cache_write_tokens",
  "cache_creation_tokens",
] as const

type OpenAICompatibleUsageMetadata = Record<string, JSONValue>

export function createOpenAICompatibleUsageMetadataExtractor(providerID: string) {
  return {
    async extractMetadata({ parsedBody }: { parsedBody: unknown }) {
      return metadataFromUsage(providerID, usageFromObject(parsedBody))
    },
    createStreamExtractor() {
      let usage: Record<string, unknown> | undefined
      return {
        processChunk(parsedChunk: unknown) {
          const next = usageFromObject(parsedChunk)
          if (next) usage = next
        },
        buildMetadata() {
          return metadataFromUsage(providerID, usage)
        },
      }
    },
  }
}

export function createFlocksOpenAICompatible(options: OpenAICompatibleProviderSettings) {
  const baseURL = withoutTrailingSlash(options.baseURL)
  const providerName = options.name
  const headers = {
    ...(options.apiKey && { Authorization: `Bearer ${options.apiKey}` }),
    ...options.headers,
  }
  const getHeaders = () => withUserAgentSuffix(headers, `ai-sdk/openai-compatible/${OPENAI_COMPATIBLE_VERSION}`)
  const getCommonModelConfig = (modelType: string) => ({
    provider: `${providerName}.${modelType}`,
    url: ({ path }: { modelId: string; path: string }) => {
      const url = new URL(`${baseURL}${path}`)
      if (options.queryParams) {
        url.search = new URLSearchParams(options.queryParams).toString()
      }
      return url.toString()
    },
    headers: getHeaders,
    fetch: options.fetch,
  })

  const createChatModel = (modelId: string) =>
    new OpenAICompatibleChatLanguageModel(modelId, {
      ...getCommonModelConfig("chat"),
      includeUsage: options.includeUsage,
      supportsStructuredOutputs: options.supportsStructuredOutputs,
      metadataExtractor: createOpenAICompatibleUsageMetadataExtractor(providerName),
    })
  const createCompletionModel = (modelId: string) =>
    new OpenAICompatibleCompletionLanguageModel(modelId, {
      ...getCommonModelConfig("completion"),
      includeUsage: options.includeUsage,
    })
  const createEmbeddingModel = (modelId: string) =>
    new OpenAICompatibleEmbeddingModel(modelId, {
      ...getCommonModelConfig("embedding"),
    })
  const createImageModel = (modelId: string) => new OpenAICompatibleImageModel(modelId, getCommonModelConfig("image"))

  const provider = (modelId: string) => createChatModel(modelId)
  provider.languageModel = provider
  provider.chat = createChatModel
  provider.chatModel = createChatModel
  provider.completion = createCompletionModel
  provider.completionModel = createCompletionModel
  provider.textEmbedding = createEmbeddingModel
  provider.textEmbeddingModel = createEmbeddingModel
  provider.imageModel = createImageModel
  return provider
}

function metadataFromUsage(providerID: string, usage: Record<string, unknown> | undefined): SharedV2ProviderMetadata | undefined {
  const sanitized = sanitizeUsage(usage)
  if (!sanitized) return undefined
  return {
    [providerID]: {
      usage: sanitized,
    },
  }
}

function usageFromObject(value: unknown) {
  if (!isRecord(value)) return undefined
  const usage = value["usage"]
  if (!isRecord(usage)) return undefined
  return usage
}

function sanitizeUsage(usage: Record<string, unknown> | undefined): OpenAICompatibleUsageMetadata | undefined {
  if (!usage) return undefined

  const result: OpenAICompatibleUsageMetadata = {}
  copyNumberFields(result, usage, USAGE_KEYS)

  const promptTokensDetails = sanitizeNestedUsage(usage["prompt_tokens_details"], PROMPT_TOKEN_DETAIL_KEYS)
  if (promptTokensDetails) result["prompt_tokens_details"] = promptTokensDetails

  const inputTokensDetails = sanitizeNestedUsage(usage["input_tokens_details"], INPUT_TOKEN_DETAIL_KEYS)
  if (inputTokensDetails) result["input_tokens_details"] = inputTokensDetails

  return Object.keys(result).length > 0 ? result : undefined
}

function sanitizeNestedUsage(value: unknown, keys: readonly string[]) {
  if (!isRecord(value)) return undefined
  const result: OpenAICompatibleUsageMetadata = {}
  copyNumberFields(result, value, keys)
  return Object.keys(result).length > 0 ? result : undefined
}

function copyNumberFields(target: OpenAICompatibleUsageMetadata, source: Record<string, unknown>, keys: readonly string[]) {
  for (const key of keys) {
    const value = source[key]
    if (typeof value !== "number" || !Number.isFinite(value)) continue
    target[key] = Math.max(value, 0)
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
