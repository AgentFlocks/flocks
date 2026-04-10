import { useState, useEffect, useRef, useCallback } from 'react';
import { AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SessionChat, { type SSEChatEvent } from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { workflowAPI, Workflow } from '@/api/workflow';

const FALLBACK_POLL_MS = 10_000;

interface CreateChatTabProps {
  onWorkflowCreated: (workflow: Workflow) => void;
}

export default function CreateChatTab({ onWorkflowCreated }: CreateChatTabProps) {
  const { t } = useTranslation('workflow');

  const exampleQuestions = t('create.chat.exampleQuestions', { returnObjects: true }) as string[];

  const { sessionId, error, createAndSend, retry } = useSessionChat({
    title: t('create.chat.sessionTitle'),
    category: 'workflow',
    contextMessage: t('create.chat.contextMessage'),
    welcomeMessage: t('create.chat.welcomeMessage'),
  });

  const knownIdsRef = useRef<Set<string>>(new Set());
  const createdWorkflowRef = useRef<string | null>(null);
  const [snapshotReady, setSnapshotReady] = useState(false);
  const onWorkflowCreatedRef = useRef(onWorkflowCreated);
  onWorkflowCreatedRef.current = onWorkflowCreated;

  // Snapshot existing workflow IDs on mount
  useEffect(() => {
    (async () => {
      try {
        const snap = await workflowAPI.list();
        knownIdsRef.current = new Set((snap.data as Workflow[]).map((w) => w.id));
      } catch {
        knownIdsRef.current = new Set();
      }
      setSnapshotReady(true);
    })();
  }, []);

  // Check for new workflows (used by both SSE and polling)
  const detectNewWorkflow = useCallback(async () => {
    if (!snapshotReady) return;
    try {
      const res = await workflowAPI.list();
      const workflows: Workflow[] = res.data;
      const fresh = workflows.find(
        (w) =>
          !knownIdsRef.current.has(w.id) &&
          w.id !== createdWorkflowRef.current,
      );
      if (fresh) {
        createdWorkflowRef.current = fresh.id;
        onWorkflowCreatedRef.current(fresh);
      }
    } catch { /* ignore */ }
  }, [snapshotReady]);

  // SSE: react to workflow.created events immediately
  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      if (event.type === 'workflow.created' && event.properties?.id) {
        detectNewWorkflow();
      }
    },
    [detectNewWorkflow],
  );

  // Primary: check right after AI finishes streaming
  const handleStreamingDone = useCallback(() => {
    detectNewWorkflow();
  }, [detectNewWorkflow]);

  // Fallback polling for filesystem-driven creation (Rex writes directly)
  useEffect(() => {
    if (!sessionId || !snapshotReady) return;

    const timer = setInterval(detectNewWorkflow, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [sessionId, snapshotReady, detectNewWorkflow]);

  const handleCreateAndSend = useCallback(
    async (text: string) => {
      await createAndSend(text);
    },
    [createAndSend],
  );

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 p-6 text-center">
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 w-full">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
        <button
          onClick={retry}
          className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 transition-colors"
        >
          {t('common:button.retry')}
        </button>
      </div>
    );
  }

  return (
    <SessionChat
      sessionId={sessionId}
      live={!!sessionId}
      placeholder={t('create.chat.inputPlaceholder')}
      className="h-full"
      suggestions={exampleQuestions}
      onStreamingDone={handleStreamingDone}
      onSSEEvent={handleSSEEvent}
      onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
    />
  );
}

