import type { Message } from '@/types';

/** One recorded assistant response, not every HTTP retry or child-session call. */
export interface CacheUsageCall {
  messageID: string;
  providerID?: string;
  modelID?: string;
  input: number | null;
  output: number | null;
  reasoning: number | null;
  cacheRead: number | null;
  cacheWrite: number | null;
  /** Complete prompt, with each cached token counted once. */
  promptTokens: number | null;
  reported: boolean | null;
  hitRate: number | null;
}

export interface CacheUsageSummary {
  totalCalls: number;
  reportedCalls: number;
  cacheRead: number;
  promptTokens: number;
  hitRate: number | null;
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

export function getMessageCacheUsageCall(message: Message): CacheUsageCall | null {
  if (message.role !== 'assistant' || message.finish === 'summary' || message.providerID === 'builtin') return null;
  const tokens = message.tokens;
  if (!tokens && !message.providerID && !message.modelID) return null;
  const reported = tokens?.cache?.reported ?? null;
  const input = count(tokens?.input);
  const cacheRead = count(tokens?.cache?.read);
  const cacheWrite = count(tokens?.cache?.write);
  // Historical input counters did not consistently include/exclude the cache.
  // Only the explicit reporting contract makes a hit-rate denominator reliable.
  const promptTokens = reported === true && input !== null && cacheRead !== null && cacheWrite !== null
    ? input + cacheRead + cacheWrite
    : null;
  return {
    messageID: message.id,
    providerID: message.providerID,
    modelID: message.modelID || message.model,
    input,
    output: count(tokens?.output),
    reasoning: count(tokens?.reasoning),
    cacheRead,
    cacheWrite,
    promptTokens,
    reported,
    hitRate: promptTokens !== null && promptTokens > 0 && cacheRead !== null
      ? cacheRead / promptTokens
      : null,
  };
}

export function summarizeCacheUsage(calls: readonly CacheUsageCall[]): CacheUsageSummary {
  const reported = calls.filter(call => call.reported === true && call.promptTokens !== null);
  const cacheRead = reported.reduce((total, call) => total + (call.cacheRead ?? 0), 0);
  const promptTokens = reported.reduce((total, call) => total + (call.promptTokens ?? 0), 0);
  return {
    totalCalls: calls.length,
    reportedCalls: reported.length,
    cacheRead,
    promptTokens,
    hitRate: promptTokens > 0 ? cacheRead / promptTokens : null,
  };
}

export function areCacheUsageCallsEqual(
  previous: readonly CacheUsageCall[] | undefined,
  next: readonly CacheUsageCall[] | undefined,
): boolean {
  if (previous === next) return true;
  if ((previous?.length ?? 0) !== (next?.length ?? 0)) return false;
  return (previous ?? []).every((call, index) => {
    const other = next![index];
    return call.messageID === other.messageID
      && call.providerID === other.providerID
      && call.modelID === other.modelID
      && call.input === other.input
      && call.output === other.output
      && call.reasoning === other.reasoning
      && call.cacheRead === other.cacheRead
      && call.cacheWrite === other.cacheWrite
      && call.promptTokens === other.promptTokens
      && call.reported === other.reported
      && call.hitRate === other.hitRate;
  });
}
