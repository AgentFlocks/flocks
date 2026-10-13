import { useMemo, useRef } from 'react';
import type { Message } from '@/types';

export type MonitoringMergedMessage = Message & { _merged?: boolean; _sourceMessageIds?: string[] };
type MessageGroup = { sources: Message[]; kind: 'assistant' | 'summary' | 'other' };
type CachedGroup = MessageGroup & { merged: MonitoringMergedMessage };

/** Reuse complete historical display groups while only rebuilding the live group. */
export function useMonitoringMessageMerge(messages: Message[], enabled = true): MonitoringMergedMessage[] {
  const cache = useRef(new Map<string, CachedGroup>());
  const previousResult = useRef<MonitoringMergedMessage[]>([]);

  return useMemo(() => {
    if (!enabled) {
      cache.current.clear();
      previousResult.current = messages;
      return messages;
    }
    const groups: MessageGroup[] = [];
    for (const message of messages) {
      const kind = message.finish === 'summary' ? 'summary' : message.role === 'assistant' ? 'assistant' : 'other';
      const last = groups[groups.length - 1];
      if (kind === 'assistant' && last?.kind === 'assistant' && !!last.sources[0].compacted === !!message.compacted) {
        last.sources.push(message);
      } else {
        groups.push({ kind, sources: [message] });
      }
    }

    const nextCache = new Map<string, CachedGroup>();
    const result = groups.map(group => {
      const first = group.sources[0];
      const cached = cache.current.get(first.id);
      if (cached?.kind === group.kind && cached.sources.length === group.sources.length
        && group.sources.every((source, index) => cached.sources[index] === source)) {
        nextCache.set(first.id, cached);
        return cached.merged;
      }
      let merged: MonitoringMergedMessage;
      if (group.kind === 'other') merged = first;
      else if (group.kind === 'summary') merged = { ...first, parts: [...first.parts], _merged: false };
      else {
        merged = {
          ...first,
          // Build a changed group once, rather than repeatedly concatenating its
          // growing prefix. Historical groups never read or copy their parts.
          parts: group.sources.flatMap(source => source.parts),
          _merged: true,
          _sourceMessageIds: group.sources.map(source => source.id),
          finish: group.sources[group.sources.length - 1].finish,
        };
      }
      nextCache.set(first.id, { ...group, merged });
      return merged;
    });
    cache.current = nextCache;
    if (result.length === previousResult.current.length && result.every((item, index) => previousResult.current[index] === item)) {
      return previousResult.current;
    }
    previousResult.current = result;
    return result;
  }, [messages, enabled]);
}
