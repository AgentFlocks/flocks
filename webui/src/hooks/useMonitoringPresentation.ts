import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Message, MessagePart } from '@/types';

const STEP_DELAY_MS = 450;
const MAX_PRESENTATION_LAG_MS = 2000;
const MAX_PENDING_PARTS = 32;
const keyOf = (message: Message, part: MessagePart) => `${message.id}:${part.id}`;
const urgent = (part: MessagePart) => part.state?.status === 'error'
  || part.metadata?.roundStatus === 'failed'
  || /question|request_user_input|permission/.test(part.tool || '');

type IndexedPart = { source: MessagePart; key: string; urgent: boolean };
type IndexedMessage = {
  source: Message; parts: MessagePart[]; count: number; tail?: MessagePart;
  indexed: Map<string, IndexedPart>;
};
type Location = { messageId: string; partIndex: number };

/** A view-only queue. Source messages, statuses and persisted records never wait. */
export function useMonitoringPresentation({ messages, sessionId, enabled, ready, interrupted = false, liveKeys }: {
  messages: Message[]; sessionId?: string | null; enabled: boolean; ready: boolean; interrupted?: boolean; liveKeys?: ReadonlySet<string>;
}) {
  const [revision, setRevision] = useState(0);
  const [motionReduced, setMotionReduced] = useState(false);
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
  const state = useRef({
    session: sessionId, initialized: false,
    indexed: new Map<string, IndexedMessage>(), locations: new Map<string, Location>(), order: new Map<string, number>(),
    queue: [] as string[], deadline: 0, lastShown: 0,
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const deadlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boundaryCache = useRef<{ source: Message; boundary: number; presented: Message } | null>(null);
  const clearDeadline = useCallback(() => { if (deadlineTimer.current !== null) clearTimeout(deadlineTimer.current); deadlineTimer.current = null; }, []);
  const clearTimer = useCallback(() => { if (timer.current !== null) clearTimeout(timer.current); timer.current = null; }, []);
  const flush = useCallback(() => {
    clearTimer(); clearDeadline();
    const current = state.current;
    const changed = current.queue.length > 0;
    // All nonqueued parts are visible. Flushing never revisits historical parts.
    current.queue = []; current.deadline = 0;
    if (changed) setRevision(value => value + 1);
  }, [clearTimer, clearDeadline]);

  useEffect(() => {
    if (!enabled) return;
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const updateMotion = () => { setMotionReduced(!!media?.matches); if (media?.matches) flush(); };
    const updateVisibility = () => { setVisible(document.visibilityState !== 'hidden'); flush(); };
    updateMotion();
    media?.addEventListener?.('change', updateMotion);
    document.addEventListener('visibilitychange', updateVisibility);
    return () => { media?.removeEventListener?.('change', updateMotion); document.removeEventListener('visibilitychange', updateVisibility); };
  }, [enabled, flush]);
  useEffect(() => () => { clearTimer(); clearDeadline(); }, [clearTimer, clearDeadline]);

  useLayoutEffect(() => {
    const current = state.current;
    if (!enabled) { current.initialized = false; flush(); return; }
    const hadPending = current.queue.length > 0;
    const canPace = ready && visible && !motionReduced && !interrupted;
    const reset = current.session !== sessionId || !current.initialized || !canPace;
    if (current.session !== sessionId) {
      current.indexed.clear(); current.locations.clear(); current.order.clear();
      current.lastShown = 0; boundaryCache.current = null;
    }
    if (reset) flush();
    current.session = sessionId;
    current.initialized = canPace;

    const firstRetained = messages.findIndex(message => current.indexed.has(message.id));
    const order = new Map<string, number>();
    const added: string[] = [];
    let newUrgent = false;
    messages.forEach((message, messageIndex) => {
      order.set(message.id, messageIndex);
      const previous = current.indexed.get(message.id);
      const parts = message.parts;
      // Immutable SSE updates preserve every unrelated message and its parts.
      // Keep a count/tail as well so a legacy in-place append is not lost.
      if (previous && previous.parts === parts && previous.count === parts.length && previous.tail === parts[parts.length - 1]) {
        previous.source = message;
        return;
      }
      const indexed = new Map<string, IndexedPart>();
      const prependedHistory = !previous && firstRetained >= 0 && messageIndex < firstRetained;
      parts.forEach((part, partIndex) => {
        const prior = previous?.indexed.get(part.id);
        const item = prior?.source === part ? prior : { source: part, key: keyOf(message, part), urgent: urgent(part) };
        indexed.set(part.id, item);
        current.locations.set(item.key, { messageId: message.id, partIndex });
        if (!reset && !prependedHistory && !prior && (!liveKeys || liveKeys.has(item.key))) added.push(item.key);
        if (!reset && !prependedHistory && item.urgent && !prior?.urgent) newUrgent = true;
      });
      previous?.indexed.forEach((item, partId) => { if (!indexed.has(partId)) current.locations.delete(item.key); });
      current.indexed.set(message.id, { source: message, parts, count: parts.length, tail: parts[parts.length - 1], indexed });
    });
    current.indexed.forEach((entry, messageId) => {
      if (!order.has(messageId)) {
        entry.indexed.forEach(item => current.locations.delete(item.key));
        current.indexed.delete(messageId);
      }
    });
    current.order = order;
    current.queue = current.queue.filter(key => current.locations.has(key));
    if (newUrgent || added.length + current.queue.length > MAX_PENDING_PARTS) {
      flush();
    } else if (added.length) {
      const now = Date.now();
      // The first new action is immediately visible; only rapid subsequent parts wait.
      if (!current.queue.length && now - current.lastShown >= STEP_DELAY_MS) {
        added.shift(); current.lastShown = now;
      }
      current.queue.push(...added);
      if (current.queue.length && !current.deadline) {
        current.deadline = now + MAX_PRESENTATION_LAG_MS;
        deadlineTimer.current = setTimeout(flush, MAX_PRESENTATION_LAG_MS);
      }
    }
    // Reconciliation can reorder existing queued parts as well as add new ones.
    // Sorting this bounded queue never walks the historical message contents.
    current.queue.sort((left, right) => {
      const a = current.locations.get(left)!; const b = current.locations.get(right)!;
      return order.get(a.messageId)! - order.get(b.messageId)! || a.partIndex - b.partIndex;
    });
    if (!current.queue.length) { current.deadline = 0; clearDeadline(); }
    if (hadPending || current.queue.length) setRevision(value => value + 1);
  }, [messages, sessionId, enabled, ready, visible, motionReduced, interrupted, liveKeys, flush, clearDeadline]);

  useEffect(() => {
    clearTimer();
    const current = state.current;
    if (!current.queue.length) return;
    const wait = Math.max(0, Math.min(STEP_DELAY_MS - (Date.now() - current.lastShown), current.deadline - Date.now()));
    timer.current = setTimeout(() => {
      timer.current = null;
      if (Date.now() >= current.deadline) { flush(); return; }
      current.queue.shift();
      current.lastShown = Date.now();
      if (!current.queue.length) { current.deadline = 0; clearDeadline(); }
      setRevision(value => value + 1);
    }, wait);
    return clearTimer;
  }, [revision, clearTimer, clearDeadline, flush]);

  const presentedMessages = useMemo(() => {
    const current = state.current;
    if (!enabled || !ready || interrupted || motionReduced || !visible || !current.initialized || current.session !== sessionId || !current.queue.length) return messages;
    const location = current.locations.get(current.queue[0]);
    const messageIndex = location && current.order.get(location.messageId);
    if (!location || messageIndex === undefined) return messages;
    const message = messages[messageIndex];
    // A new source array is reconciled in the layout effect before painting.
    if (!message || message.id !== location.messageId) return messages;
    const presented = messages.slice(0, messageIndex);
    if (location.partIndex > 0) {
      const cached = boundaryCache.current;
      if (cached?.source === message && cached.boundary === location.partIndex) presented.push(cached.presented);
      else {
        const partial = { ...message, parts: message.parts.slice(0, location.partIndex) };
        boundaryCache.current = { source: message, boundary: location.partIndex, presented: partial };
        presented.push(partial);
      }
    }
    return presented;
  }, [messages, sessionId, enabled, ready, interrupted, motionReduced, visible, revision]);
  return { messages: presentedMessages, pending: enabled && state.current.queue.length > 0, skip: flush };
}
