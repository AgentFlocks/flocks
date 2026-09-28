import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Message, MessagePart } from '@/types';

const STEP_DELAY_MS = 450;
const MAX_PRESENTATION_LAG_MS = 2000;
const MAX_PENDING_PARTS = 32;
const keyOf = (message: Message, part: MessagePart) => `${message.id}:${part.id}`;
const urgent = (part: MessagePart) => part.state?.status === 'error'
  || part.metadata?.roundStatus === 'failed'
  || /question|request_user_input|permission/.test(part.tool || '');

/** A view-only queue. Source messages, statuses and persisted records never wait. */
export function useMonitoringPresentation({ messages, sessionId, enabled, ready, interrupted = false, liveKeys }: {
  messages: Message[]; sessionId?: string | null; enabled: boolean; ready: boolean; interrupted?: boolean; liveKeys?: ReadonlySet<string>;
}) {
  const [revision, setRevision] = useState(0);
  const [motionReduced, setMotionReduced] = useState(false);
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
  const state = useRef({
    session: sessionId, initialized: false, known: new Set<string>(), shown: new Set<string>(),
    urgent: new Set<string>(), queue: [] as string[], deadline: 0, lastShown: 0,
  });
  const latest = useRef(messages);
  latest.current = messages;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const deadlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearDeadline = useCallback(() => { if (deadlineTimer.current !== null) clearTimeout(deadlineTimer.current); deadlineTimer.current = null; }, []);
  const clearTimer = useCallback(() => { if (timer.current !== null) clearTimeout(timer.current); timer.current = null; }, []);
  const flush = useCallback(() => {
    clearTimer(); clearDeadline();
    const current = state.current;
    const keys = latest.current.flatMap(message => message.parts.map(part => keyOf(message, part)));
    const changed = current.queue.length > 0 || current.shown.size !== keys.length || keys.some(key => !current.shown.has(key));
    current.known = new Set(keys); current.shown = new Set(keys);
    current.urgent = new Set(latest.current.flatMap(message => message.parts.filter(urgent).map(part => keyOf(message, part))));
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
    if (!enabled) { current.initialized = false; if (current.queue.length) flush(); return; }
    const source = messages.flatMap(message => message.parts.map(part => ({ key: keyOf(message, part), part })));
    const canPace = enabled && ready && visible && !motionReduced && !interrupted;
    if (current.session !== sessionId || !current.initialized || !canPace) {
      current.session = sessionId;
      current.initialized = canPace;
      flush();
      return;
    }
    const keys = new Set(source.map(item => item.key));
    const unseen = source.filter(item => !current.known.has(item.key));
    // Fetch reconciliation may race the final SSE updates. Snapshot-only parts
    // appear immediately; already queued live parts keep their bounded cadence.
    const added = unseen.filter(item => !liveKeys || liveKeys.has(item.key));
    unseen.filter(item => liveKeys && !liveKeys.has(item.key)).forEach(item => current.shown.add(item.key));
    const newUrgent = source.some(item => urgent(item.part) && !current.urgent.has(item.key));
    current.urgent = new Set(source.filter(item => urgent(item.part)).map(item => item.key));
    current.known = keys;
    current.queue = current.queue.filter(key => keys.has(key));
    current.shown = new Set([...current.shown].filter(key => keys.has(key)));
    if (newUrgent || added.length + current.queue.length > MAX_PENDING_PARTS) { flush(); return; }
    if (!added.length) { if (unseen.length) setRevision(value => value + 1); return; }
    const now = Date.now();
    // The first new action is immediately visible; only rapid subsequent parts wait.
    if (!current.queue.length && now - current.lastShown >= STEP_DELAY_MS) {
      current.shown.add(added.shift()!.key); current.lastShown = now;
    }
    for (const { key } of added) current.queue.push(key);
    if (current.queue.length && !current.deadline) {
      current.deadline = now + MAX_PRESENTATION_LAG_MS;
      deadlineTimer.current = setTimeout(flush, MAX_PRESENTATION_LAG_MS);
    }
    setRevision(value => value + 1);
  }, [messages, sessionId, enabled, ready, visible, motionReduced, interrupted, liveKeys, flush]);

  useEffect(() => {
    clearTimer();
    const current = state.current;
    if (!current.queue.length) return;
    const wait = Math.max(0, Math.min(STEP_DELAY_MS - (Date.now() - current.lastShown), current.deadline - Date.now()));
    timer.current = setTimeout(() => {
      timer.current = null;
      if (Date.now() >= current.deadline) { flush(); return; }
      const next = current.queue.shift();
      if (next) current.shown.add(next);
      current.lastShown = Date.now();
      if (!current.queue.length) { current.deadline = 0; clearDeadline(); }
      setRevision(value => value + 1);
    }, wait);
    return clearTimer;
  }, [revision, clearTimer, clearDeadline, flush]);

  const presentedMessages = useMemo(() => {
    if (!enabled || !ready || interrupted || motionReduced || !visible || !state.current.initialized || state.current.session !== sessionId) return messages;
    const shown = state.current.shown;
    let waiting = false;
    return messages.flatMap(message => {
      if (waiting) return [];
      const boundary = message.parts.findIndex(part => !shown.has(keyOf(message, part)));
      waiting = boundary >= 0;
      // A newly fetched later result cannot overtake an earlier live step.
      const parts = waiting ? message.parts.slice(0, boundary) : message.parts;
      if (!parts.length && message.parts.length) return [];
      return parts.length === message.parts.length ? [message] : [{ ...message, parts }];
    });
  }, [messages, sessionId, enabled, ready, interrupted, motionReduced, visible, revision]);
  return { messages: presentedMessages, pending: enabled && state.current.queue.length > 0, skip: flush };
}
