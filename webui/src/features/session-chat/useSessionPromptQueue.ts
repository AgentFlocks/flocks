import { useCallback, useLayoutEffect, useRef, useState } from 'react';

import { sessionApi, type QueuedPrompt } from '@/api/session';

export interface EnqueuePromptPayload {
  parts: Array<Record<string, unknown>>;
  agent?: string;
  model?: Record<string, unknown>;
  variant?: string;
  displayText?: string;
}

export function useSessionPromptQueue(sessionId?: string | null) {
  const [items, setItems] = useState<QueuedPrompt[]>([]);
  const [expanded, setExpanded] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState('');
  const [actionId, setActionId] = useState<string | null>(null);
  const requestSeqRef = useRef(0);

  const reset = useCallback(() => {
    requestSeqRef.current += 1;
    setItems([]);
    setEditingId(null);
    setEditingText('');
    setActionId(null);
  }, []);

  useLayoutEffect(() => {
    reset();
  }, [reset, sessionId]);

  const refresh = useCallback(async () => {
    const requestSeq = ++requestSeqRef.current;
    if (!sessionId) {
      setItems([]);
      return [];
    }

    try {
      const response = await sessionApi.listPromptQueue(sessionId);
      const nextItems = response.items ?? [];
      if (requestSeq === requestSeqRef.current) {
        setItems(nextItems);
      }
      return nextItems;
    } catch (err) {
      if (requestSeq === requestSeqRef.current) {
        console.warn('[SessionChat] Failed to fetch prompt queue:', err);
      }
      return [];
    }
  }, [sessionId]);

  const applyItems = useCallback((nextItems: QueuedPrompt[]) => {
    requestSeqRef.current += 1;
    setItems(nextItems);
    if (nextItems.length > 0) setExpanded(true);
  }, []);

  const enqueue = useCallback(async (payload: EnqueuePromptPayload) => {
    if (!sessionId) return;
    await sessionApi.enqueuePrompt(sessionId, payload);
    await refresh();
    setExpanded(true);
  }, [refresh, sessionId]);

  const startEdit = useCallback((item: QueuedPrompt) => {
    setEditingId(item.id);
    setEditingText(getQueuedPromptText(item));
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditingText('');
  }, []);

  const saveEdit = useCallback(async (item: QueuedPrompt) => {
    if (!sessionId) return false;
    const text = editingText.trim();
    if (!text) return false;
    setActionId(item.id);
    try {
      await sessionApi.updateQueuedPrompt(sessionId, item.id, text);
      cancelEdit();
      await refresh();
      return true;
    } finally {
      setActionId(null);
    }
  }, [cancelEdit, editingText, refresh, sessionId]);

  const remove = useCallback(async (item: QueuedPrompt) => {
    if (!sessionId) return false;
    setActionId(item.id);
    try {
      await sessionApi.removeQueuedPrompt(sessionId, item.id);
      if (editingId === item.id) cancelEdit();
      await refresh();
      return true;
    } finally {
      setActionId(null);
    }
  }, [cancelEdit, editingId, refresh, sessionId]);

  const runNow = useCallback(async (item: QueuedPrompt) => {
    if (!sessionId) return false;
    setActionId(item.id);
    try {
      await sessionApi.runQueuedPromptNow(sessionId, item.id);
      if (editingId === item.id) cancelEdit();
      await refresh();
      return true;
    } finally {
      setActionId(null);
    }
  }, [cancelEdit, editingId, refresh, sessionId]);

  return {
    items,
    expanded,
    setExpanded,
    editingId,
    editingText,
    setEditingText,
    actionId,
    refresh,
    applyItems,
    enqueue,
    startEdit,
    cancelEdit,
    saveEdit,
    remove,
    runNow,
    reset,
  } as const;
}

export function getQueuedPromptText(item: QueuedPrompt): string {
  const displayText = item.displayText ?? item.display_text;
  if (typeof displayText === 'string' && displayText.trim()) {
    return displayText;
  }
  const firstText = item.parts
    .map((part) => typeof part.text === 'string' ? part.text : '')
    .find((text) => text.trim());
  return firstText || '';
}
