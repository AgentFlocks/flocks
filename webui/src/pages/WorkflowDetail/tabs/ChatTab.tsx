import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Plus, Clock, Sparkles, SearchCheck, FlaskConical, ChevronsLeft, ChevronsRight } from 'lucide-react';
import SessionChat, { NodeRef, type SSEChatEvent } from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import type { ImagePartData } from '@/utils/imageUpload';
import { workflowAPI, Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import { formatSessionDate } from '@/utils/time';
import client from '@/api/client';
import {
  getStoredSessions,
  pushStoredSession,
  setStoredSessions,
  type StoredSession,
} from '../sessionStorage';

const FALLBACK_POLL_MS = 30_000;
const WORKFLOW_CONFIG_SKILL_NAME = 'workflow-config-guide';

// ─────────────────────────────────────────────
// ChatTab
// ─────────────────────────────────────────────

export interface WorkflowChatLaunchRequest {
  id: number;
  prompt: string;
}

interface ChatTabProps {
  workflow: Workflow;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onWorkflowUpdated?: (updated: Workflow) => void;
  onFirstMessageSent?: () => void;
  onSessionChange?: (sessionId: string | null) => void;
  launchRequest?: WorkflowChatLaunchRequest | null;
  onLaunchRequestHandled?: (id: number) => void;
  selectedNode?: WorkflowNode | null;
  onNodeRefDismiss?: () => void;
}

export default function ChatTab({
  workflow,
  onLatestExecutionChange,
  onWorkflowUpdated,
  onFirstMessageSent,
  onSessionChange,
  launchRequest,
  onLaunchRequestHandled,
  selectedNode,
  onNodeRefDismiss,
}: ChatTabProps) {
  const { t } = useTranslation('workflow');
  const supportsVision = useDefaultModelVision();
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [initialMessage, setInitialMessage] = useState<string | null>(null);
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const hasCreatedRef = useRef(false);
  const handledLaunchRequestRef = useRef<number | null>(null);
  const lastUpdatedAtRef = useRef<number>(workflow.updatedAt);
  const workflowIdRef = useRef<string>(workflow.id);
  workflowIdRef.current = workflow.id;
  const historyBtnRef = useRef<HTMLDivElement>(null);

  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  const workflowEditDocPath = `${workflowDir}workflow.edit.md`;

  const {
    sessionId: hookSessionId,
    loading: initializing,
    error,
    create: createSession,
    createAndSend: createAndSendSession,
    reset: resetSession,
  } = useSessionChat({
    title: t('detail.chat.sessionTitle', { name: workflow.name }),
    category: 'workflow',
    contextMessage: t('detail.chat.contextMessage', {
      id: workflow.id,
      name: workflow.name,
      category: workflow.category,
      dir: workflowDir,
      editDocPath: workflowEditDocPath,
      mdPath: `${workflowDir}workflow.md`,
      jsonPath: `${workflowDir}workflow.json`,
      configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
    }),
  });

  const sessionId = activeSessionId || hookSessionId;

  useEffect(() => {
    onSessionChange?.(sessionId ?? null);
  }, [onSessionChange, sessionId]);

  // Load stored sessions and validate only the active one (lightweight check)
  useEffect(() => {
    const stored = getStoredSessions(workflow.id);
    if (stored.length === 0) {
      setSessions([]);
      return;
    }

    setSessions(stored);
    setActiveSessionId(stored[0].id);
    hasCreatedRef.current = true;

    // Validate the first session only — lazy-validate others when selected
    (async () => {
      try {
        await client.get(`/api/session/${stored[0].id}`);
      } catch {
        // First session is gone — try to find a valid one
        const valid: StoredSession[] = [];
        for (const s of stored.slice(1)) {
          try {
            await client.get(`/api/session/${s.id}`);
            valid.push(s);
            break; // found a valid one, stop
          } catch { /* continue */ }
        }
        setStoredSessions(workflow.id, valid);
        setSessions(valid);
        if (valid.length > 0) {
          setActiveSessionId(valid[0].id);
        } else {
          setActiveSessionId(null);
          hasCreatedRef.current = false;
        }
      }
    })();
  }, [workflow.id]);

  // Save newly created session to localStorage
  useEffect(() => {
    if (!hookSessionId) return;
    const newSession: StoredSession = {
      id: hookSessionId,
      title: t('detail.chat.sessionTitle', { name: workflow.name }),
      createdAt: Date.now(),
    };
    pushStoredSession(workflow.id, newSession);
    setSessions(getStoredSessions(workflow.id));
  }, [hookSessionId, workflow.id, workflow.name]);

  // Close history dropdown on outside click
  useEffect(() => {
    if (!showHistory) return;
    const handle = (e: MouseEvent) => {
      if (historyBtnRef.current && !historyBtnRef.current.contains(e.target as Node)) {
        setShowHistory(false);
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showHistory]);

  // First message — via SessionChat's onCreateAndSend callback
  const handleCreateAndSend = useCallback(
    async (text: string, imageParts?: ImagePartData[]) => {
      const hasImages = (imageParts?.length ?? 0) > 0;
      // Allow image-only messages (no text) to flow through.
      if (hasCreatedRef.current || (!text.trim() && !hasImages)) return;
      hasCreatedRef.current = true;
      onFirstMessageSent?.();

      try {
        if (hasImages) {
          // initialMessage is text-only; use createAndSend so the inline
          // image parts survive into the very first prompt instead of being
          // silently dropped (the previous bug for non-Session composers).
          await createAndSendSession({ text, imageParts });
        } else {
          setInitialMessage(text);
          await createSession();
        }
      } catch {
        hasCreatedRef.current = false;
        setInitialMessage(null);
      }
    },
    [onFirstMessageSent, createSession, createAndSendSession],
  );

  const handleNewSession = useCallback(() => {
    setShowHistory(false);
    setActiveSessionId(null);
    setInitialMessage(null);
    resetSession();
    hasCreatedRef.current = false;
  }, [resetSession]);

  useEffect(() => {
    if (!launchRequest || handledLaunchRequestRef.current === launchRequest.id) return;
    handledLaunchRequestRef.current = launchRequest.id;
    onLaunchRequestHandled?.(launchRequest.id);

    setShowHistory(false);
    setActiveSessionId(null);
    setInitialMessage(null);
    resetSession();
    hasCreatedRef.current = true;

    createAndSendSession({ text: launchRequest.prompt }).catch(() => {
      hasCreatedRef.current = false;
    });
  }, [createAndSendSession, launchRequest, onLaunchRequestHandled, resetSession]);

  const handleSelectSession = useCallback((sid: string) => {
    setInitialMessage(null);
    setActiveSessionId(sid);
    setShowHistory(false);
    hasCreatedRef.current = true;
  }, []);

  // Helper: fetch fresh workflow and notify parent if updated
  const checkWorkflowUpdate = useCallback(async () => {
    if (!onWorkflowUpdated) return;
    try {
      const res = await workflowAPI.get(workflowIdRef.current);
      const fresh = res.data;
      if (fresh.updatedAt > lastUpdatedAtRef.current) {
        lastUpdatedAtRef.current = fresh.updatedAt;
        onWorkflowUpdated(fresh);
      }
    } catch { /* ignore */ }
  }, [onWorkflowUpdated]);

  // Primary: check workflow right after AI finishes streaming
  const handleStreamingDone = useCallback(() => {
    checkWorkflowUpdate();
  }, [checkWorkflowUpdate]);

  // SSE events: react to API-driven workflow changes immediately
  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      const { type, properties } = event;
      if (
        type === 'message.part.updated'
        && properties?.part?.type === 'tool'
        && properties.part.tool === 'run_workflow'
      ) {
        const state = properties.part.state as Record<string, any> | undefined;
        const metadata = (state?.metadata ?? {}) as Record<string, any>;
        const workflowId = metadata.workflow_id;
        if (
          workflowId === workflowIdRef.current
          && metadata.workflow_execution_id
        ) {
          const status =
            state?.status === 'completed'
              ? 'success'
              : state?.status === 'error'
              ? 'error'
              : (metadata.status ?? 'running');
          onLatestExecutionChange?.({
            id: String(metadata.workflow_execution_id),
            workflowId,
            inputParams: {},
            status,
            startedAt: Number(state?.time?.start ?? Date.now()),
            executionLog: [],
            currentNodeId: metadata.current_node_id,
            currentNodeType: metadata.current_node_type,
            currentPhase: metadata.phase,
            currentStepIndex: metadata.step_index,
          });
        }
      }
      if (!onWorkflowUpdated) return;
      if (
        (type === 'workflow.updated' || type === 'workflow.created') &&
        properties?.id === workflowIdRef.current
      ) {
        checkWorkflowUpdate();
      }
    },
    [onLatestExecutionChange, onWorkflowUpdated, checkWorkflowUpdate],
  );

  // Fallback: low-frequency polling for filesystem-driven changes (Rex writes directly)
  useEffect(() => {
    if (!sessionId || !onWorkflowUpdated) return;
    lastUpdatedAtRef.current = workflow.updatedAt;

    const timer = setInterval(checkWorkflowUpdate, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [sessionId, workflow.id, workflow.updatedAt, onWorkflowUpdated, checkWorkflowUpdate]);

  const nodeRef: NodeRef | null = selectedNode
    ? { id: selectedNode.id, type: selectedNode.type, description: selectedNode.description }
    : null;

  return (
    <div className="flex flex-col h-full">
      {/* ── Session toolbar ── */}
      <div className="flex-shrink-0 flex items-center justify-end gap-0.5 px-2 py-1 border-b border-gray-100 bg-white">
        <button
          onClick={handleNewSession}
          className="flex items-center gap-1 px-1.5 py-1 rounded text-[10px] text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          title={t('detail.chat.newSession')}
        >
          <Plus className="w-3 h-3" />
          <span>{t('detail.chat.newSession')}</span>
        </button>
        {sessions.length > 0 && (
          <div className="relative" ref={historyBtnRef}>
            <button
              onClick={() => setShowHistory((v) => !v)}
              className={`flex items-center gap-1 px-1.5 py-1 rounded text-[10px] transition-colors ${
                showHistory
                  ? 'bg-gray-100 text-gray-700'
                  : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
              }`}
              title={t('detail.chat.historyLabel')}
            >
              <Clock className="w-3 h-3" />
              <span>{sessions.length}</span>
            </button>
            {showHistory && (
              <div className="absolute right-0 top-full mt-1 z-50 w-52 bg-white rounded-lg border border-gray-200 shadow-lg overflow-hidden">
                <div className="px-2.5 py-1.5 text-[9px] font-semibold text-gray-400 uppercase tracking-wider border-b border-gray-100">
                  {t('detail.chat.historyLabel')}
                </div>
                <div className="max-h-52 overflow-y-auto">
                  {sessions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => handleSelectSession(s.id)}
                      className={`w-full text-left px-2.5 py-2 flex items-center gap-2 hover:bg-gray-50 transition-colors ${
                        s.id === sessionId ? 'bg-red-50' : ''
                      }`}
                    >
                      <span
                        className={`flex-1 text-xs truncate ${
                          s.id === sessionId ? 'text-red-600 font-medium' : 'text-gray-600'
                        }`}
                      >
                        {formatSessionDate(s.createdAt)}
                      </span>
                      {s.id === sessionId && (
                        <span className="text-[9px] text-red-400 flex-shrink-0">{t('detail.chat.currentLabel')}</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── SessionChat ── */}
      <div className="flex-1 min-h-0">
        <SessionChat
          sessionId={sessionId}
          live={!!sessionId}
          placeholder={t('detail.chat.inputPlaceholder')}
          className="h-full"
          display={{ collapseIntermediateSteps: true }}
          nodeRef={nodeRef}
          onNodeRefDismiss={onNodeRefDismiss}
          onStreamingDone={handleStreamingDone}
          initialMessage={initialMessage}
          onSSEEvent={handleSSEEvent}
          supportsVision={supportsVision}
          onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
          composerTextareaMinHeight={72}
          composerTextareaMaxHeight={180}
          conversationBottomSlot={({ sendPrompt, sending }) => (
            <WorkflowGuideDock
              workflow={workflow}
              disabled={sending}
              onStartPrompt={sendPrompt}
            />
          )}
          welcomeContent={!sessionId ? (
            <WorkflowWelcome
              workflow={workflow}
              error={error}
              onRetry={() => { hasCreatedRef.current = false; resetSession(); }}
            />
          ) : undefined}
        />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Welcome content
// ─────────────────────────────────────────────

function WorkflowWelcome({
  workflow,
  error,
  onRetry,
}: {
  workflow: Workflow;
  error: string | null;
  onRetry: () => void;
}) {
  const { t } = useTranslation('workflow');

  return (
    <div className="w-full max-w-md space-y-4 text-left">
      <div className="text-xs text-gray-700 space-y-2">
        <p className="font-semibold text-gray-900">{t('detail.chat.welcome.title', { name: workflow.name })}</p>
        <p className="text-gray-500 leading-relaxed">
          {t('detail.chat.welcome.descPart1')}
          <span className="font-medium text-gray-700">{t('detail.chat.welcome.mdTabLabel')}</span>
          {t('detail.chat.welcome.descPart2')}
        </p>
      </div>

      <div className="rounded-lg border border-red-100 bg-red-50 p-3 text-xs text-red-800 space-y-1.5 leading-relaxed">
        <p className="font-medium">{t('detail.chat.welcome.canHelp')}</p>
        <ul className="space-y-1 text-red-700">
          <li>• {t('detail.chat.welcome.bullet1')}</li>
          <li>• {t('detail.chat.welcome.bullet2')}</li>
          <li>• {t('detail.chat.welcome.bullet3')}</li>
          <li>• {t('detail.chat.welcome.bullet4')}</li>
        </ul>
        <p className="pt-1 text-red-600 border-t border-red-200">
          {t('detail.chat.welcome.tipPart1')}<span className="font-medium">{t('detail.chat.welcome.mdTabLabel')}</span>
          {t('detail.chat.welcome.tipPart2')}
        </p>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={onRetry} className="underline hover:no-underline flex-shrink-0">
            {t('detail.chat.welcome.retry')}
          </button>
        </div>
      )}
    </div>
  );
}

function WorkflowGuideDock({
  workflow,
  disabled,
  onStartPrompt,
}: {
  workflow: Workflow;
  disabled?: boolean;
  onStartPrompt: (text: string) => void;
}) {
  const { t } = useTranslation('workflow');
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  const workflowEditDocPath = `${workflowDir}workflow.edit.md`;
  const promptParams = {
    id: workflow.id,
    name: workflow.name,
    dir: workflowDir,
    editDocPath: workflowEditDocPath,
    configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
  };
  const guideActions = [
    {
      label: t('detail.chat.welcome.guidePrimaryShort'),
      description: t('detail.chat.welcome.guidePrimaryDesc'),
      prompt: t('detail.chat.welcome.guidePrompt', promptParams),
      icon: Sparkles,
      className: 'border-slate-200 bg-slate-100 text-slate-700 hover:border-red-200 hover:bg-red-50 hover:text-red-600',
    },
    {
      label: t('detail.chat.welcome.guideAuditShort'),
      description: t('detail.chat.welcome.guideAuditDesc'),
      prompt: t('detail.chat.welcome.auditPrompt', promptParams),
      icon: SearchCheck,
      className: 'border-slate-200 bg-slate-100 text-slate-700 hover:border-slate-300 hover:bg-slate-200 hover:text-slate-800',
    },
    {
      label: t('detail.chat.welcome.guideSampleShort'),
      description: t('detail.chat.welcome.guideSampleDesc'),
      prompt: t('detail.chat.welcome.samplePrompt', promptParams),
      icon: FlaskConical,
      className: 'border-slate-200 bg-slate-100 text-slate-700 hover:border-slate-300 hover:bg-slate-200 hover:text-slate-800',
    },
  ];

  const handleGuideWheel = useCallback((event: WheelEvent) => {
    const el = scrollRef.current;
    if (!el) return;

    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ? event.deltaX
      : event.deltaY;
    if (delta === 0) return;

    event.preventDefault();
    event.stopPropagation();
    el.scrollLeft += delta;
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || collapsed) return undefined;

    el.addEventListener('wheel', handleGuideWheel, { passive: false });
    return () => {
      el.removeEventListener('wheel', handleGuideWheel);
    };
  }, [collapsed, handleGuideWheel]);

  return (
    <div className="flex w-full min-w-0 items-stretch gap-2">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex h-16 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-100 text-slate-500 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-200 hover:text-slate-700"
        title={collapsed ? t('detail.chat.welcome.guideExpand') : t('detail.chat.welcome.guideCollapse')}
      >
        {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
      </button>

      <div
        ref={scrollRef}
        className={`min-w-0 flex-1 overscroll-contain overflow-x-auto overflow-y-hidden transition-all duration-200 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
          collapsed ? 'basis-0 max-w-0 opacity-0 pointer-events-none' : 'basis-auto max-w-full opacity-100'
        }`}
      >
        <div className="flex w-max gap-2.5 pr-1">
          {guideActions.map((action) => {
            const Icon = action.icon;
            return (
              <button
                key={action.label}
                type="button"
                disabled={disabled}
                onClick={() => onStartPrompt(action.prompt)}
                className={`flex h-16 w-[208px] flex-shrink-0 items-center gap-2.5 rounded-lg border px-3 text-left shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${action.className}`}
                title={`${action.label} - ${action.description}`}
              >
                <span className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md bg-slate-50 text-current">
                  <Icon className="h-4 w-4 flex-shrink-0" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[13px] font-semibold">{action.label}</span>
                  <span className="mt-0.5 block truncate text-[11px] font-normal opacity-70">{action.description}</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
