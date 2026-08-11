import { useState, useCallback, useRef, useEffect } from 'react';
import client from '@/api/client';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';
import { createMessageId } from '@/utils/messageId';
import type { Message } from '@/types';
import type { SessionExecutionMode } from '@/utils/sessionExecutionMode';

export interface UseSessionChatOptions {
  title: string;
  category?: string;
  /** Enable runtime model failover when creating a new session. */
  modelAuto?: boolean;
  /** Context injected via noReply (not visible as user message) */
  contextMessage?: string;
  /** Mock welcome message from assistant */
  welcomeMessage?: string;
  /** Existing session to resume instead of creating a new one */
  initialSessionId?: string | null;
  /** Auto-create session when hook mounts */
  autoCreate?: boolean;
}

/** Options accepted by {@link useSessionChat} `createAndSend`. */
export interface CreateAndSendOptions {
  text: string;
  imageParts?: ImagePartData[];
  agent?: string;
  model?: { providerID: string; modelID: string } | null;
  /** Override Auto mode for the new session created by this send. */
  modelAuto?: boolean;
  displayText?: string;
  executionMode?: SessionExecutionMode;
}

export function useSessionChat({
  title,
  category,
  modelAuto,
  contextMessage,
  welcomeMessage,
  initialSessionId = null,
  autoCreate = false,
}: UseSessionChatOptions) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingOptimisticMessage, setPendingOptimisticMessage] = useState<Message | null>(null);

  const sessionIdRef = useRef<string | null>(initialSessionId);
  const createPromiseRef = useRef<Promise<string> | null>(null);
  const optionsRef = useRef({ title, category, modelAuto, contextMessage, welcomeMessage });
  optionsRef.current = { title, category, modelAuto, contextMessage, welcomeMessage };

  const ensureSession = useCallback(
    async (overrides?: Partial<UseSessionChatOptions>): Promise<string> => {
      if (sessionIdRef.current) return sessionIdRef.current;
      // Reuse in-flight creation promise to prevent duplicates (e.g. React StrictMode double-mount)
      if (createPromiseRef.current) return createPromiseRef.current;

      setError(null);
      setLoading(true);

      const opts = { ...optionsRef.current, ...overrides };

      const doCreate = async (): Promise<string> => {
        const payload: Record<string, string | boolean> = { title: opts.title };
        if (opts.category) payload.category = opts.category;
        if (typeof opts.modelAuto === 'boolean') payload.model_auto = opts.modelAuto;

        const res = await client.post('/api/session', payload);
        const sid: string = res.data.id;

        if (opts.contextMessage || opts.welcomeMessage) {
          const msgPayload: Record<string, unknown> = {
            parts: [{ type: 'text', text: opts.contextMessage || '' }],
          };
          if (opts.contextMessage) msgPayload.noReply = true;
          if (opts.welcomeMessage) msgPayload.mockReply = opts.welcomeMessage;
          await client.post(`/api/session/${sid}/message`, msgPayload);
        }

        return sid;
      };

      const promise = doCreate();
      createPromiseRef.current = promise;

      try {
        return await promise;
      } catch (err: unknown) {
        createPromiseRef.current = null;
        setError(
          err instanceof Error ? err.message : '创建会话失败',
        );
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const create = useCallback(
    async (overrides?: Partial<UseSessionChatOptions>): Promise<string> => {
      const sid = await ensureSession(overrides);
      sessionIdRef.current = sid;
      setSessionId(sid);
      return sid;
    },
    [ensureSession],
  );

  useEffect(() => {
    if (initialSessionId === sessionIdRef.current) return;
    sessionIdRef.current = initialSessionId;
    createPromiseRef.current = null;
    setSessionId(initialSessionId);
    setPendingOptimisticMessage(null);
    setLoading(false);
    setError(null);
  }, [initialSessionId]);

  const retry = useCallback(() => {
    setError(null);
    create().catch(() => {});
  }, [create]);

  const reset = useCallback(() => {
    sessionIdRef.current = null;
    createPromiseRef.current = null;
    setSessionId(null);
    setPendingOptimisticMessage(null);
    setLoading(false);
    setError(null);
  }, []);

  const createAndSend = useCallback(
    async ({
      text,
      imageParts,
      agent,
      model,
      modelAuto: createModelAuto,
      displayText,
      executionMode = 'build',
    }: CreateAndSendOptions): Promise<string> => {
      const resumedExistingSession = Boolean(sessionIdRef.current);
      const effectiveModelAuto = typeof createModelAuto === 'boolean'
        ? createModelAuto
        : optionsRef.current.modelAuto;
      const sid = await ensureSession(
        typeof createModelAuto === 'boolean' ? { modelAuto: createModelAuto } : undefined,
      );
      if (resumedExistingSession && effectiveModelAuto) {
        await client.patch(`/api/session/${sid}`, {
          model_auto: true,
          model_pinned: false,
        });
      }
      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      const messageId = createMessageId();
      payload.messageID = messageId;
      if (agent) payload.agent = agent;
      if (model) payload.model = model;
      if (displayText) payload.displayText = displayText;
      payload.executionMode = executionMode;
      await client.post(`/api/session/${sid}/prompt_async`, payload);

      if (!resumedExistingSession) {
        const optimisticParts: Message['parts'] = [];
        if (text || displayText) {
          optimisticParts.push({
            id: `temp-${messageId}-text`,
            type: 'text',
            text,
            ...(displayText ? { metadata: { displayText } } : {}),
          });
        }
        imageParts?.forEach((image, index) => {
          optimisticParts.push({
            id: `temp-${messageId}-img-${index}`,
            type: 'file',
            url: image.url,
            mime: image.mime,
            filename: image.filename,
          });
        });
        setPendingOptimisticMessage({
          id: messageId,
          sessionID: sid,
          role: 'user',
          parts: optimisticParts.length > 0
            ? optimisticParts
            : [{ id: `temp-${messageId}-part`, type: 'text', text }],
          timestamp: Date.now(),
          agent,
        });
        sessionIdRef.current = sid;
        setSessionId(sid);
      }
      return sid;
    },
    [ensureSession],
  );

  const consumePendingOptimisticMessage = useCallback((messageId: string) => {
    setPendingOptimisticMessage((message) => (
      message?.id === messageId ? null : message
    ));
  }, []);

  useEffect(() => {
    if (autoCreate) create().catch(() => {});
  }, []);

  return {
    sessionId,
    loading,
    error,
    pendingOptimisticMessage,
    create,
    createAndSend,
    consumePendingOptimisticMessage,
    retry,
    reset,
  };
}
