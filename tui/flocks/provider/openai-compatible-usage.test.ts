import { describe, expect, test } from "bun:test"
import type { FetchFunction } from "@ai-sdk/provider-utils"
import { createFlocksOpenAICompatible, createOpenAICompatibleUsageMetadataExtractor } from "./openai-compatible-usage"

describe("createOpenAICompatibleUsageMetadataExtractor", () => {
  test("extracts sanitized usage metadata from a response body", async () => {
    const extractor = createOpenAICompatibleUsageMetadataExtractor("deepseek")

    await expect(
      extractor.extractMetadata({
        parsedBody: {
          id: "chatcmpl-test",
          choices: [{ message: { content: "hidden" } }],
          usage: {
            prompt_tokens: 640,
            completion_tokens: 20,
            total_tokens: 660,
            prompt_cache_hit_tokens: 512,
            prompt_cache_miss_tokens: 128,
            prompt_tokens_details: {
              cached_tokens: 0,
              cache_creation_tokens: 42,
              ignored: "value",
            },
            ignored: "value",
          },
        },
      }),
    ).resolves.toEqual({
      deepseek: {
        usage: {
          prompt_tokens: 640,
          completion_tokens: 20,
          total_tokens: 660,
          prompt_cache_hit_tokens: 512,
          prompt_cache_miss_tokens: 128,
          prompt_tokens_details: {
            cached_tokens: 0,
            cache_creation_tokens: 42,
          },
        },
      },
    })
  })

  test("uses the latest streaming usage chunk", () => {
    const extractor = createOpenAICompatibleUsageMetadataExtractor("deepseek").createStreamExtractor()

    extractor.processChunk({
      choices: [{ delta: { content: "hello" } }],
    })
    extractor.processChunk({
      choices: [],
      usage: {
        prompt_tokens: 100,
        completion_tokens: 10,
        total_tokens: 110,
        prompt_cache_hit_tokens: 64,
        prompt_cache_miss_tokens: 36,
      },
    })

    expect(extractor.buildMetadata()).toEqual({
      deepseek: {
        usage: {
          prompt_tokens: 100,
          completion_tokens: 10,
          total_tokens: 110,
          prompt_cache_hit_tokens: 64,
          prompt_cache_miss_tokens: 36,
        },
      },
    })
  })

  test("returns undefined when no numeric usage metadata is available", async () => {
    const extractor = createOpenAICompatibleUsageMetadataExtractor("custom")

    await expect(extractor.extractMetadata({ parsedBody: { usage: { ignored: "value" } } })).resolves.toBeUndefined()
    expect(extractor.createStreamExtractor().buildMetadata()).toBeUndefined()
  })

  test("preserves DeepSeek raw usage metadata through the OpenAI-compatible chat model", async () => {
    const fetch = (async () =>
      new Response(
        JSON.stringify({
          id: "chatcmpl-test",
          created: 1,
          model: "deepseek-chat",
          choices: [
            {
              index: 0,
              finish_reason: "stop",
              message: { role: "assistant", content: "ok" },
            },
          ],
          usage: {
            prompt_tokens: 640,
            completion_tokens: 20,
            total_tokens: 660,
            prompt_cache_hit_tokens: 512,
            prompt_cache_miss_tokens: 128,
          },
        }),
        { headers: { "content-type": "application/json" } },
      )) as unknown as FetchFunction

    const provider = createFlocksOpenAICompatible({
      name: "deepseek",
      baseURL: "https://api.deepseek.com",
      includeUsage: true,
      fetch,
    })

    const result = await (provider.languageModel("deepseek-chat") as any).doGenerate({
      prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
      headers: {},
    })

    expect(result.usage).toEqual({
      inputTokens: 640,
      outputTokens: 20,
      totalTokens: 660,
      reasoningTokens: undefined,
      cachedInputTokens: undefined,
    })
    expect(result.providerMetadata).toEqual({
      deepseek: {
        usage: {
          prompt_tokens: 640,
          completion_tokens: 20,
          total_tokens: 660,
          prompt_cache_hit_tokens: 512,
          prompt_cache_miss_tokens: 128,
        },
      },
    })
  })

  test("preserves DeepSeek raw usage metadata through OpenAI-compatible streaming", async () => {
    const stream = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder()
        controller.enqueue(
          encoder.encode(
            [
              "data: " +
                JSON.stringify({
                  id: "chatcmpl-test",
                  created: 1,
                  model: "deepseek-chat",
                  choices: [{ index: 0, delta: { role: "assistant", content: "ok" }, finish_reason: null }],
                }),
              "",
              "data: " +
                JSON.stringify({
                  id: "chatcmpl-test",
                  created: 1,
                  model: "deepseek-chat",
                  choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
                  usage: {
                    prompt_tokens: 640,
                    completion_tokens: 20,
                    total_tokens: 660,
                    prompt_cache_hit_tokens: 512,
                    prompt_cache_miss_tokens: 128,
                  },
                }),
              "",
              "data: [DONE]",
              "",
            ].join("\n"),
          ),
        )
        controller.close()
      },
    })
    const fetch = (async () => new Response(stream, { headers: { "content-type": "text/event-stream" } })) as unknown as FetchFunction
    const provider = createFlocksOpenAICompatible({
      name: "deepseek",
      baseURL: "https://api.deepseek.com",
      includeUsage: true,
      fetch,
    })

    const result = await (provider.languageModel("deepseek-chat") as any).doStream({
      prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
      headers: {},
    })
    const events = []
    for await (const event of result.stream) {
      events.push(event)
    }

    expect(events[events.length - 1]).toEqual({
      type: "finish",
      finishReason: "stop",
      usage: {
        inputTokens: 640,
        outputTokens: 20,
        totalTokens: 660,
        reasoningTokens: undefined,
        cachedInputTokens: undefined,
      },
      providerMetadata: {
        deepseek: {
          usage: {
            prompt_tokens: 640,
            completion_tokens: 20,
            total_tokens: 660,
            prompt_cache_hit_tokens: 512,
            prompt_cache_miss_tokens: 128,
          },
        },
      },
    })
  })
})
