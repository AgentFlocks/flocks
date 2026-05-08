import { createFlocksClient, getFlocksAuthHeaders, type Event } from "@flocks-ai/sdk/v2"
import { createSimpleContext } from "./helper"
import { createGlobalEmitter } from "@solid-primitives/event-bus"
import { batch, onCleanup, onMount } from "solid-js"

export type EventSource = {
  on: (handler: (event: Event) => void) => () => void
}

export const { use: useSDK, provider: SDKProvider } = createSimpleContext({
  name: "SDK",
  init: (props: { url: string; directory?: string; fetch?: typeof fetch; events?: EventSource }) => {
    const abort = new AbortController()
    const sdk = createFlocksClient({
      baseUrl: props.url,
      signal: abort.signal,
      directory: props.directory,
      fetch: props.fetch,
    })

    const authenticatedFetch: typeof fetch = (input, init = {}) => {
      const headers = getFlocksAuthHeaders(init.headers)
      return (props.fetch ?? fetch)(input, { ...init, headers })
    }

    const emitter = createGlobalEmitter<{
      [key in Event["type"]]: Extract<Event, { type: key }>
    }>()

    let queue: Event[] = []
    let timer: Timer | undefined
    let last = 0

    const flush = () => {
      if (queue.length === 0) return
      const events = queue
      queue = []
      timer = undefined
      last = Date.now()
      // Batch all event emissions so all store updates result in a single render
      batch(() => {
        for (const event of events) {
          emitter.emit(event.type, event)
        }
      })
    }

    const handleEvent = (event: Event) => {
      queue.push(event)
      const elapsed = Date.now() - last

      if (timer) return
      // Optimized batching strategy:
      // - If we just flushed very recently (< 8ms), batch for up to 8ms to reduce render thrashing
      // - Otherwise, process immediately to minimize perceived latency
      // This balances responsiveness with performance better than the previous 16ms threshold
      if (elapsed < 8) {
        timer = setTimeout(flush, 8)
        return
      }
      flush()
    }

    onMount(async () => {
      // If an event source is provided, use it instead of SSE
      if (props.events) {
        const unsub = props.events.on(handleEvent)
        onCleanup(unsub)
        return
      }

      // Fall back to SSE
      while (true) {
        if (abort.signal.aborted) break
        const events = await sdk.event.subscribe(
          {},
          {
            signal: abort.signal,
          },
        )

        for await (const event of events.stream) {
          handleEvent(event)
        }

        // Flush any remaining events
        if (timer) clearTimeout(timer)
        if (queue.length > 0) {
          flush()
        }
      }
    })

    onCleanup(() => {
      abort.abort()
      if (timer) clearTimeout(timer)
    })

    return { client: sdk, event: emitter, url: props.url, fetch: authenticatedFetch }
  },
})
