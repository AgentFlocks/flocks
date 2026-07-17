import { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import type { Session, Message } from '@/types';

const VISIBLE_CATEGORIES = new Set(['user', 'workflow', 'entity-config']);
const ABORTED_TOOL_ERROR = 'Tool execution was interrupted';
const SESSION_LIST_PAGE_SIZE = 100;
const MESSAGE_PAGE_SIZE = 50;

function finalizeStoppedMessageParts(parts: Message['parts'], stoppedAt = Date.now()): Message['parts'] {
  return parts.map((part) => {
    if (
      (part.type !== 'tool' && part.type !== 'toolCall')
      || part.state?.status !== 'running'
    ) {
      return part;
    }

    const nextTime = part.state.time
      ? { ...part.state.time, end: part.state.time.end ?? stoppedAt }
      : undefined;

    return {
      ...part,
      state: {
        ...part.state,
        status: 'error',
        error: part.state.error || ABORTED_TOOL_ERROR,
        ...(nextTime ? { time: nextTime } : {}),
      },
    };
  });
}

function normalizeMessageOrder(messages: Message[]): Message[] {
  const messageIds = new Set(messages.map((message) => message.id));
  const assistantChildrenByParent = new Map<string, Message[]>();
  const childIds = new Set<string>();

  messages.forEach((message) => {
    if (message.role !== 'assistant' || !message.parentID || !messageIds.has(message.parentID)) {
      return;
    }
    childIds.add(message.id);
    const siblings = assistantChildrenByParent.get(message.parentID) ?? [];
    siblings.push(message);
    assistantChildrenByParent.set(message.parentID, siblings);
  });

  const ordered: Message[] = [];
  const pushed = new Set<string>();

  messages.forEach((message) => {
    if (childIds.has(message.id)) return;
    if (!pushed.has(message.id)) {
      ordered.push(message);
      pushed.add(message.id);
    }

    const children = assistantChildrenByParent.get(message.id) ?? [];
    children.forEach((child) => {
      if (pushed.has(child.id)) return;
      ordered.push(child);
      pushed.add(child.id);
    });
  });

  return ordered;
}

function mergeFetchedMessages(prev: Message[], fetched: Message[]): Message[] {
  const previousById = new Map(prev.map((message) => [message.id, message]));

  return normalizeMessageOrder(fetched.map((message) => {
    const existing = previousById.get(message.id);
    if (!existing) return message;

    // Aborted assistant replies may never be fully persisted by the backend.
    // Keep the richer local snapshot so partial streamed text/tool state doesn't
    // disappear or regress on a later refetch.
    if (existing.finish === 'stop' && !message.finish) {
      return {
        ...message,
        parts: existing.parts,
        finish: existing.finish,
        compacted: message.compacted ?? existing.compacted,
      };
    }

    return message;
  }));
}

function mergeLatestFetchedMessages(prev: Message[], fetched: Message[]): Message[] {
  if (prev.length === 0) return normalizeMessageOrder(fetched);
  const fetchedIds = new Set(fetched.map((message) => message.id));
  const mergedFetched = mergeFetchedMessages(prev, fetched);
  const firstFetchedTimestamp = mergedFetched[0]?.timestamp ?? Number.POSITIVE_INFINITY;
  const retainedOlder = prev.filter(
    (message) => !fetchedIds.has(message.id) && message.timestamp <= firstFetchedTimestamp,
  );
  const retainedNewer = prev.filter(
    (message) => !fetchedIds.has(message.id) && message.timestamp > firstFetchedTimestamp,
  );
  return normalizeMessageOrder([...retainedOlder, ...mergedFetched, ...retainedNewer]);
}

function prependOlderMessages(prev: Message[], older: Message[]): Message[] {
  const existingIds = new Set(prev.map((message) => message.id));
  return normalizeMessageOrder([...older.filter((message) => !existingIds.has(message.id)), ...prev]);
}

function transformMessageResponse(data: any): {
  messages: Message[];
  hasMore: boolean;
  nextBefore: string | null;
} {
  const items = Array.isArray(data) ? data : (data?.items ?? []);
  return {
    messages: items.map((msg: any) => ({
      id: msg.info.id,
      sessionID: msg.info.sessionID,
      role: msg.info.role,
      parts: msg.parts || [],
      parentID: msg.info.parentID,
      agent: msg.info.agent,
      model: msg.info.model,
      modelID: msg.info.modelID,
      providerID: msg.info.providerID,
      cost: msg.info.cost,
      tokens: msg.info.tokens,
      timestamp: msg.info.time?.created || Date.now(),
      finish: msg.info.finish || null,
      error: msg.info.error || null,
      compacted: msg.info.compacted || null,
    })),
    hasMore: Array.isArray(data) ? false : Boolean(data?.hasMore),
    nextBefore: Array.isArray(data) ? null : (data?.nextBefore ?? null),
  };
}

function markMeasure(name: string, startMark: string) {
  if (typeof performance === 'undefined') return;
  try {
    performance.measure(name, startMark);
  } catch {
    // Ignore environments where the mark was cleared or performance is mocked.
  }
}

function mergeSessionListWithOptimistic(fetched: Session[], optimistic: Map<string, Session>): Session[] {
  if (optimistic.size === 0) return fetched;
  const fetchedIds = new Set(fetched.map(session => session.id));
  const optimisticRows = Array.from(optimistic.values()).filter(session => !fetchedIds.has(session.id));
  return [...optimisticRows, ...fetched];
}

function appendSessionList(prev: Session[], fetched: Session[]): Session[] {
  const existingIds = new Set(prev.map(session => session.id));
  return [...prev, ...fetched.filter(session => !existingIds.has(session.id))];
}

/**
 * Pure reducer for updating a message part in the messages list.
 * Exported for unit testing.
 */
export function applyMessagePartUpdate(
  prev: Message[],
  partInfo: any,
  delta?: string,
): Message[] {
  const messageIndex = prev.findIndex(m => m.id === partInfo.messageID);

  if (messageIndex < 0) {
    // Message metadata can arrive after part updates over SSE. Keep the part
    // attached to its own messageID instead of borrowing a nearby assistant,
    // otherwise chunks from a new turn can render inside the previous reply.
    return [...prev, {
      id: partInfo.messageID,
      sessionID: partInfo.sessionID,
      role: 'assistant' as const,
      parts: [partInfo],
      timestamp: Date.now(),
    }];
  }

  // Message exists — update its parts
  const updated = [...prev];
  const message = { ...updated[messageIndex] };
  const parts = [...(message.parts || [])];

  const partIndex = parts.findIndex((p: any) => p.id === partInfo.id);

  if (partIndex < 0) {
    for (let j = parts.length - 1; j >= 0; j--) {
      if (String(parts[j].id).startsWith('temp-')) {
        parts.splice(j, 1);
      }
    }
    parts.push(partInfo);
  } else {
    if (delta && (partInfo.type === 'text' || partInfo.type === 'reasoning' || partInfo.type === 'thinking')) {
      const existingPart = parts[partIndex];
      parts[partIndex] = {
        ...existingPart,
        ...partInfo,
        text: partInfo.text,
      };
    } else {
      parts[partIndex] = partInfo;
    }
  }

  message.parts = parts;
  updated[messageIndex] = message;
  return updated;
}

type UseSessionsOptions = {
  projectIds?: string[];
  pageSize?: number;
};

function sessionEffectiveProjectId(session: Session): string {
  return session.effectiveProjectID || session.projectID;
}

export function useSessions(search = '', options?: UseSessionsOptions) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [hasMoreByProject, setHasMoreByProject] = useState<Record<string, boolean>>({});
  const [loadingMoreProjectIds, setLoadingMoreProjectIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Track whether the initial fetch has completed — refetches should be silent
  const initializedRef = useRef(false);
  const sessionsRef = useRef<Session[]>([]);
  const hasLoadedOnceRef = useRef(false);
  const requestSeqRef = useRef(0);
  const optimisticSessionsRef = useRef<Map<string, Session>>(new Map());
  const projectIdsKey = options?.projectIds === undefined
    ? null
    : [...options.projectIds].sort().join('\u0000');
  const pageSize = options?.pageSize ?? SESSION_LIST_PAGE_SIZE;
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  const fetchSessions = useCallback(async (fetchOptions?: { append?: boolean; projectId?: string }) => {
    const append = Boolean(fetchOptions?.append);
    const requestedProjectIds = projectIdsKey === null
      ? null
      : projectIdsKey.split('\u0000').filter(Boolean);
    const targetProjectIds = requestedProjectIds === null
      ? null
      : fetchOptions?.projectId
        ? [fetchOptions.projectId]
        : requestedProjectIds;
    const requestSeq = ++requestSeqRef.current;
    try {
      // Only show the full-page loading state on the very first fetch.
      // Subsequent refetches (triggered by SSE events) update data silently
      // to avoid unmounting SessionChat and disrupting the active conversation.
      if (append) {
        setLoadingMore(true);
        if (fetchOptions?.projectId) {
          setLoadingMoreProjectIds((current) => new Set(current).add(fetchOptions.projectId as string));
        }
      } else if (!initializedRef.current) {
        setLoading(true);
      }
      setError(null);
      if (targetProjectIds?.length === 0) {
        setSessions([]);
        setHasMore(false);
        setHasMoreByProject({});
        hasLoadedOnceRef.current = true;
        return;
      }
      // Fetch only root sessions: child sessions are internal and never shown
      // in the sidebar, so excluding them avoids extra payload and filtering.
      const startMark = append ? 'sessions:list:older-start' : 'sessions:list:first-start';
      if (typeof performance !== 'undefined') performance.mark(startMark);
      const projectTargets = targetProjectIds ?? [undefined];
      const responses = await Promise.all(projectTargets.map(async (projectId) => {
        const offset = append
          ? sessionsRef.current.filter((session) => (
            !optimisticSessionsRef.current.has(session.id)
            && (projectId === undefined || sessionEffectiveProjectId(session) === projectId)
          )).length
          : 0;
        const response = await sessionApi.list({
          view: 'list',
          manager: true,
          roots: true,
          limit: pageSize,
          offset,
          search: search.trim() || undefined,
          projectID: projectId,
        });
        return { projectId, response };
      }));
      if (requestSeq !== requestSeqRef.current) return;
      markMeasure(append ? 'sessions:list:older-page' : 'sessions:list:first-render', startMark);
      const nextSessions = responses.flatMap(({ response }) => (
        Array.isArray(response) ? response.filter(
          (s: any) => (!s.category || VISIBLE_CATEGORIES.has(s.category)) && !s.parentID,
        ) : []
      ));
      nextSessions.forEach((session: Session) => optimisticSessionsRef.current.delete(session.id));
      const nextHasMoreByProject = Object.fromEntries(
        responses
          .filter(({ projectId }) => projectId !== undefined)
          .map(({ projectId, response }) => [
            projectId as string,
            Array.isArray(response) && response.length >= pageSize,
          ]),
      );
      setHasMoreByProject((current) => append
        ? { ...current, ...nextHasMoreByProject }
        : nextHasMoreByProject);
      setSessions((previous) => {
        if (append) return appendSessionList(previous, nextSessions);
        if (targetProjectIds === null) {
          return mergeSessionListWithOptimistic(nextSessions, optimisticSessionsRef.current);
        }
        const optimistic = new Map(
          Array.from(optimisticSessionsRef.current.entries()).filter(([, session]) => (
            targetProjectIds.includes(sessionEffectiveProjectId(session))
          )),
        );
        return mergeSessionListWithOptimistic(nextSessions, optimistic);
      });
      setHasMore(targetProjectIds === null
        ? responses.some(({ response }) => Array.isArray(response) && response.length >= pageSize)
        : Object.values(nextHasMoreByProject).some(Boolean));
      hasLoadedOnceRef.current = true;
    } catch (err: any) {
      if (requestSeq !== requestSeqRef.current) return;
      setError(err.message || 'Failed to fetch sessions');
      if (!append && !hasLoadedOnceRef.current) setSessions([]);
    } finally {
      if (requestSeq === requestSeqRef.current) {
        setLoading(false);
        setLoadingMore(false);
        if (fetchOptions?.projectId) {
          setLoadingMoreProjectIds((current) => {
            const next = new Set(current);
            next.delete(fetchOptions.projectId as string);
            return next;
          });
        }
        initializedRef.current = true;
      }
    }
  }, [pageSize, projectIdsKey, search]);

  const updateSessionTitle = useCallback((sessionId: string, title: string) => {
    const optimistic = optimisticSessionsRef.current.get(sessionId);
    if (optimistic) {
      optimisticSessionsRef.current.set(sessionId, { ...optimistic, title });
    }
    setSessions(prev =>
      prev.map(session =>
        session.id === sessionId ? { ...session, title } : session,
      )
    );
  }, []);

  useEffect(() => {
    if (!hasLoadedOnceRef.current) {
      initializedRef.current = false;
    }
    fetchSessions();
  }, [fetchSessions]);

  const removeSession = useCallback((sessionId: string) => {
    optimisticSessionsRef.current.delete(sessionId);
    setSessions(prev => prev.filter(s => s.id !== sessionId));
  }, []);

  const removeSessions = useCallback((sessionIds: string[]) => {
    const idSet = new Set(sessionIds);
    sessionIds.forEach(sessionId => optimisticSessionsRef.current.delete(sessionId));
    setSessions(prev => prev.filter(s => !idSet.has(s.id)));
  }, []);

  /** Optimistically prepend a newly created session without a full refetch. */
  const addSession = useCallback((session: Session) => {
    optimisticSessionsRef.current.set(session.id, session);
    setSessions(prev => {
      if (prev.some(s => s.id === session.id)) return prev;
      return [session, ...prev];
    });
  }, []);

  return {
    sessions,
    loading,
    error,
    refetch: fetchSessions,
    updateSessionTitle,
    removeSession,
    removeSessions,
    addSession,
    hasMore,
    hasMoreByProject,
    loadingMore,
    loadingMoreProjectIds,
    loadMore: (projectId?: string) => fetchSessions({ append: true, projectId }),
  };
}

export function useSessionMessages(sessionId?: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activeSessionIdRef = useRef(sessionId);
  const firstPageRequestIdRef = useRef(0);
  const firstPageInFlightRequestIdRef = useRef<number | null>(null);
  const olderPageRequestIdRef = useRef(0);
  const olderPageInFlightRequestIdRef = useRef<number | null>(null);

  const fetchMessages = useCallback(async () => {
    if (!sessionId || activeSessionIdRef.current !== sessionId) return;

    const requestSessionId = sessionId;
    const requestId = ++firstPageRequestIdRef.current;
    firstPageInFlightRequestIdRef.current = requestId;
    // A fresh first page invalidates pagination based on an older snapshot.
    olderPageRequestIdRef.current += 1;
    olderPageInFlightRequestIdRef.current = null;
    setLoadingOlder(false);
    const isCurrentRequest = () => (
      activeSessionIdRef.current === requestSessionId
      && firstPageRequestIdRef.current === requestId
    );
    
    try {
      setLoading(true);
      setError(null);
      const startMark = 'session:messages:first-page-start';
      if (typeof performance !== 'undefined') performance.mark(startMark);
      const response = await client.get(`/api/session/${sessionId}/message`, {
        params: { page: true, limit: MESSAGE_PAGE_SIZE, include_archived: true },
      });
      markMeasure('session:messages:first-page', startMark);
      if (!isCurrentRequest()) return;
      const { messages: messagesData, hasMore, nextBefore } = transformMessageResponse(response.data);
      setMessages(prev => mergeLatestFetchedMessages(prev, messagesData));
      setHasMore(hasMore);
      setNextBefore(nextBefore);
    } catch (err: any) {
      if (isCurrentRequest()) {
        setError(err.message || 'Failed to fetch messages');
      }
    } finally {
      if (firstPageInFlightRequestIdRef.current === requestId) {
        firstPageInFlightRequestIdRef.current = null;
      }
      if (isCurrentRequest()) {
        setLoading(false);
      }
    }
  }, [sessionId]);

  const loadOlder = useCallback(async () => {
    if (
      !sessionId
      || activeSessionIdRef.current !== sessionId
      || firstPageInFlightRequestIdRef.current !== null
      || olderPageInFlightRequestIdRef.current !== null
      || !hasMore
      || !nextBefore
    ) return;

    const requestSessionId = sessionId;
    const requestId = ++olderPageRequestIdRef.current;
    olderPageInFlightRequestIdRef.current = requestId;
    const firstPageRequestId = firstPageRequestIdRef.current;
    const isCurrentRequest = () => (
      activeSessionIdRef.current === requestSessionId
      && olderPageRequestIdRef.current === requestId
      && firstPageRequestIdRef.current === firstPageRequestId
    );

    try {
      setLoadingOlder(true);
      setError(null);
      const startMark = 'session:messages:older-page-start';
      if (typeof performance !== 'undefined') performance.mark(startMark);
      const response = await client.get(`/api/session/${sessionId}/message`, {
        params: {
          page: true,
          limit: MESSAGE_PAGE_SIZE,
          before: nextBefore,
          include_archived: true,
        },
      });
      markMeasure('session:messages:older-page', startMark);
      if (!isCurrentRequest()) return;
      const page = transformMessageResponse(response.data);
      setMessages(prev => prependOlderMessages(prev, page.messages));
      setHasMore(page.hasMore);
      setNextBefore(page.nextBefore);
    } catch (err: any) {
      if (isCurrentRequest()) {
        setError(err.message || 'Failed to fetch older messages');
      }
    } finally {
      if (olderPageInFlightRequestIdRef.current === requestId) {
        olderPageInFlightRequestIdRef.current = null;
      }
      if (isCurrentRequest()) {
        setLoadingOlder(false);
      }
    }
  }, [hasMore, nextBefore, sessionId]);

  // Reset state synchronously before paint when session changes
  // to prevent flash of welcome screen (useEffect runs AFTER paint)
  useLayoutEffect(() => {
    activeSessionIdRef.current = sessionId;
    setMessages([]);
    setError(null);
    setHasMore(false);
    setNextBefore(null);
    olderPageInFlightRequestIdRef.current = null;
    setLoadingOlder(false);
    if (sessionId) {
      setLoading(true);
    } else {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    fetchMessages();
  }, [fetchMessages]);

  return {
    messages,
    loading,
    loadingOlder,
    hasMore,
    error,
    refetch: fetchMessages,
    loadOlder,
    addMessage: (message: Message) => {
      setMessages(prev => [...prev, message]);
    },
    updateMessage: (messageInfo: any) => {
      setMessages(prev => {
        const existingIndex = prev.findIndex(m => m.id === messageInfo.id);
        if (existingIndex >= 0) {
          const existing = prev[existingIndex];
          const updated = [...prev];
          updated[existingIndex] = {
            ...existing,
            ...messageInfo,
            parentID: messageInfo.parentID ?? existing.parentID,
            timestamp: messageInfo.time?.created || existing.timestamp,
            // Preserve compacted/finish from the authoritative refetch data —
            // SSE events never carry these fields, so a naive spread would
            // overwrite them with undefined.
            compacted: messageInfo.compacted ?? existing.compacted,
            finish: messageInfo.finish ?? existing.finish,
            tokens: messageInfo.tokens ?? existing.tokens,
            modelID: messageInfo.modelID ?? existing.modelID,
            providerID: messageInfo.providerID ?? existing.providerID,
            cost: messageInfo.cost ?? existing.cost,
          };
          return updated;
        }

        // If a user SSE message arrives and there's a temp placeholder, replace it
        // instead of appending (temp placeholder has parts=[] so no text duplication).
        if (messageInfo.role === 'user') {
          const tempIndex = prev.reduceRight(
            (found, m, i) =>
              found >= 0 ? found : m.role === 'user' && String(m.id).startsWith('temp-') ? i : -1,
            -1
          );
          if (tempIndex >= 0) {
            const updated = [...prev];
            const nextUser = {
              id: messageInfo.id,
              sessionID: messageInfo.sessionID,
              role: 'user' as const,
              parts: updated[tempIndex].parts,
              agent: messageInfo.agent,
              model: messageInfo.model,
              modelID: messageInfo.modelID,
              providerID: messageInfo.providerID,
              cost: messageInfo.cost,
              tokens: messageInfo.tokens,
              timestamp: messageInfo.time?.created || updated[tempIndex].timestamp,
            };
            updated[tempIndex] = nextUser;
            return normalizeMessageOrder(updated);
          }
        }

        // Add new message
        const nextMessage = {
          id: messageInfo.id,
          sessionID: messageInfo.sessionID,
          role: messageInfo.role,
          parts: [],
          parentID: messageInfo.parentID,
          agent: messageInfo.agent,
          model: messageInfo.model,
          modelID: messageInfo.modelID,
          providerID: messageInfo.providerID,
          cost: messageInfo.cost,
          tokens: messageInfo.tokens,
          timestamp: messageInfo.time?.created || Date.now(),
        };

        return normalizeMessageOrder([...prev, {
          ...nextMessage,
        }]);
      });
    },
    /**
     * Incrementally update a message part for streaming rendering.
     * @param partInfo - Part object containing id, messageID, sessionID, type, text, etc.
     * @param delta - Optional text delta for this update.
     *
     * Every SSE update enters message state immediately so a following finish
     * event cannot overtake the final delta. Display-layer smoothing owns the
     * frame-level typing cadence and Markdown parse budget.
     */
    updateMessagePart: (partInfo: any, delta?: string) => {
      setMessages(prev => applyMessagePartUpdate(prev, partInfo, delta));
    },
    replaceMessageText: (messageId: string, partId: string, text: string) => {
      setMessages(prev => prev.map((message) => {
        if (message.id !== messageId) return message;

        const parts = [...(message.parts || [])];
        const targetPartIndex = parts.findIndex((part) => part.id === partId && part.type === 'text');
        if (targetPartIndex < 0) {
          return message;
        }
        parts[targetPartIndex] = {
          ...parts[targetPartIndex],
          text,
        };

        return {
          ...message,
          parts,
        };
      }));
    },
    markMessageStopped: (messageId: string) => {
      setMessages(prev => prev.map((message) => {
        if (message.id !== messageId) return message;
        if (message.finish === 'stop') return message;

        return {
          ...message,
          finish: 'stop',
          parts: finalizeStoppedMessageParts(message.parts),
        };
      }));
    },
    truncateAfterMessage: (messageId: string, options?: { includeTarget?: boolean }) => {
      setMessages(prev => {
        const targetIndex = prev.findIndex((message) => message.id === messageId);
        if (targetIndex < 0) return prev;
        return prev.slice(0, options?.includeTarget ? targetIndex : targetIndex + 1);
      });
    },
  };
}
