import { useState, useCallback, useRef, useEffect } from 'react';
import client from '@/api/client';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';

export interface UseSessionChatOptions {
  title: string;
  category?: string;
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
  displayText?: string;
}

export function useSessionChat({
  title,
  category,
  contextMessage,
  welcomeMessage,
  initialSessionId = null,
  autoCreate = false,
}: UseSessionChatOptions) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sessionIdRef = useRef<string | null>(initialSessionId);
  const createPromiseRef = useRef<Promise<string> | null>(null);
  const optionsRef = useRef({ title, category, contextMessage, welcomeMessage });
  optionsRef.current = { title, category, contextMessage, welcomeMessage };

  const create = useCallback(
    async (overrides?: Partial<UseSessionChatOptions>): Promise<string> => {
      if (sessionIdRef.current) return sessionIdRef.current;
      // Reuse in-flight creation promise to prevent duplicates (e.g. React StrictMode double-mount)
      if (createPromiseRef.current) return createPromiseRef.current;

      setError(null);
      setLoading(true);

      const opts = { ...optionsRef.current, ...overrides };

      const doCreate = async (): Promise<string> => {
        const payload: Record<string, string> = { title: opts.title };
        if (opts.category) payload.category = opts.category;

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
        const sid = await promise;
        sessionIdRef.current = sid;
        setSessionId(sid);
        return sid;
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

  useEffect(() => {
    if (initialSessionId === sessionIdRef.current) return;
    sessionIdRef.current = initialSessionId;
    createPromiseRef.current = null;
    setSessionId(initialSessionId);
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
    setLoading(false);
    setError(null);
  }, []);

  const createAndSend = useCallback(
    async ({
      text,
      imageParts,
      agent,
      model,
      displayText,
    }: CreateAndSendOptions): Promise<string> => {
      const sid = await create();
      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      if (agent) payload.agent = agent;
      if (model) payload.model = model;
      if (displayText) payload.displayText = displayText;
      client.post(`/api/session/${sid}/prompt_async`, payload).catch(() => {});
      return sid;
    },
    [create],
  );

  useEffect(() => {
    if (autoCreate) create().catch(() => {});
  }, []);

  return { sessionId, loading, error, create, createAndSend, retry, reset };
}
