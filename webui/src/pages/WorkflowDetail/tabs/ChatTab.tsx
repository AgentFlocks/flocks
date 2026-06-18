import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Bot, Clock, Plus } from 'lucide-react';
import SessionChat, {
  NodeRef,
  buildInstructionDisplayText,
  type PromptDisplayOptions,
  type SSEChatEvent,
} from '@/components/common/SessionChat';
import {
  ChatAgentDisplay,
  ChatModelPicker,
  useChatAgentOptions,
  useChatModelOptions,
} from '@/components/common/ChatPromptSelectors';
import ChatGuideDock, { type ChatGuideAction } from '@/components/common/ChatGuideDock';
import GuideInfoIcon from '@/components/common/GuideInfoIcon';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import type { ImagePartData } from '@/utils/imageUpload';
import { workflowAPI, workflowAPIEndpoints, Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import { formatSessionDate } from '@/utils/time';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';
import client from '@/api/client';
import {
  getStoredSessions,
  pushStoredSession,
  setStoredSessions,
  type StoredSession,
} from '../sessionStorage';

const FALLBACK_POLL_MS = 30_000;
const WORKFLOW_CONFIG_SKILL_NAME = 'workflow-config-guide';
const WORKFLOW_CHAT_AGENT_NAME = 'rex';
const WORKFLOW_CHAT_AGENT_NAMES = [WORKFLOW_CHAT_AGENT_NAME];
const WORKFLOW_GUIDE_FILE_NAME = 'guide.md';

function formatWorkflowAPIEndpoints(id: string): string {
  return JSON.stringify(workflowAPIEndpoints(id), null, 2);
}

type TranslateFn = (key: string, params?: Record<string, unknown>) => string;

function workflowRevisionKey(workflow: Workflow): string {
  return [
    workflow.updatedAt,
    workflow.markdownContent ?? workflow.editMarkdownContent ?? '',
    JSON.stringify(workflow.workflowJson),
  ].join('\u0000');
}

type WorkflowPromptParams = Record<string, unknown> & {
  backendConfigAccessGuide: string;
};

function withBackendConfigAccessGuide(
  t: TranslateFn,
  params: Record<string, unknown>,
): WorkflowPromptParams {
  return {
    ...params,
    backendConfigAccessGuide: t('detail.chat.backendConfigAccessGuide', params),
  };
}

// ─────────────────────────────────────────────
// ChatTab
// ─────────────────────────────────────────────

export interface WorkflowChatLaunchRequest {
  id: number;
  prompt: string;
  displayLabel?: string;
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
  const { t, i18n } = useTranslation('workflow');
  const workflowDisplayName = getWorkflowDisplayName(workflow, i18n?.language);
  const defaultSupportsVision = useDefaultModelVision();
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [initialMessage, setInitialMessage] = useState<string | null>(null);
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [sessionsHydrated, setSessionsHydrated] = useState(false);
  const hasCreatedRef = useRef(false);
  const workflowRevisionRef = useRef<string>(workflowRevisionKey(workflow));
  const workflowIdRef = useRef<string>(workflow.id);
  workflowIdRef.current = workflow.id;
  const historyBtnRef = useRef<HTMLDivElement>(null);
  const { agents: workflowChatAgents } = useChatAgentOptions({
    allowedAgentNames: WORKFLOW_CHAT_AGENT_NAMES,
  });
  const {
    groupedOptions: groupedChatModelOptions,
    loading: loadingChatModels,
    selectedModelOption,
    selectedPromptModel,
    setSelectedModelKey,
  } = useChatModelOptions();
  const effectiveSupportsVision = selectedModelOption?.supportsVision ?? defaultSupportsVision;

  useEffect(() => {
    workflowRevisionRef.current = workflowRevisionKey(workflow);
  }, [workflow]);

  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  const workflowMdPath = `${workflowDir}workflow.md`;
  const workflowGuidePath = `${workflowDir}${WORKFLOW_GUIDE_FILE_NAME}`;
  const endpoints = workflowAPIEndpoints(workflow.id);
  const workflowConfigEndpoint = endpoints.config.read.replace(/^GET /, '');
  const workflowChatPromptParams = withBackendConfigAccessGuide(t, {
    id: workflow.id,
    name: workflowDisplayName,
    category: workflow.category,
    dir: workflowDir,
    mdPath: workflowMdPath,
    jsonPath: `${workflowDir}workflow.json`,
    guidePath: workflowGuidePath,
    configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
    configEndpoint: workflowConfigEndpoint,
    configSyncEndpoint: endpoints.config.syncFallback.replace(/^POST /, ''),
    publishEndpoint: endpoints.apiService.publish.replace(/^POST /, ''),
    unpublishEndpoint: endpoints.apiService.unpublish.replace(/^POST /, ''),
    triggersEndpoint: endpoints.triggers.list.replace(/^GET /, ''),
    apiEndpoints: formatWorkflowAPIEndpoints(workflow.id),
  });

  const {
    sessionId: hookSessionId,
    loading: initializing,
    error,
    create: createSession,
    createAndSend: createAndSendSession,
    reset: resetSession,
  } = useSessionChat({
    title: t('detail.chat.sessionTitle', { name: workflowDisplayName }),
    category: 'workflow',
    contextMessage: [
      t('detail.chat.contextMessage', workflowChatPromptParams),
      workflowChatPromptParams.backendConfigAccessGuide,
    ].join('\n\n'),
  });

  const sessionId = activeSessionId || hookSessionId;

  useEffect(() => {
    onSessionChange?.(sessionId ?? null);
  }, [onSessionChange, sessionId]);

  // Load stored sessions and validate only the active one (lightweight check)
  useEffect(() => {
    let cancelled = false;
    setSessionsHydrated(false);
    const stored = getStoredSessions(workflow.id);
    if (stored.length === 0) {
      setSessions([]);
      setActiveSessionId(null);
      hasCreatedRef.current = false;
      setSessionsHydrated(true);
      return;
    }

    setSessions(stored);
    setActiveSessionId(stored[0].id);
    hasCreatedRef.current = true;

    // Validate the first session only — lazy-validate others when selected
    (async () => {
      try {
        await client.get(`/api/session/${stored[0].id}`);
        if (cancelled) return;
      } catch {
        if (cancelled) return;
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
      } finally {
        if (!cancelled) {
          setSessionsHydrated(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workflow.id]);

  // Save newly created session to localStorage
  useEffect(() => {
    if (!hookSessionId) return;
    const newSession: StoredSession = {
      id: hookSessionId,
      title: t('detail.chat.sessionTitle', { name: workflowDisplayName }),
      createdAt: Date.now(),
    };
    pushStoredSession(workflow.id, newSession);
    setSessions(getStoredSessions(workflow.id));
  }, [hookSessionId, workflow.id, workflowDisplayName, t]);

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
    async (
      text: string,
      imageParts?: ImagePartData[],
      agentOverride?: string,
      modelOverride?: { providerID: string; modelID: string } | null,
      options?: PromptDisplayOptions,
    ) => {
      const hasImages = (imageParts?.length ?? 0) > 0;
      // Allow image-only messages (no text) to flow through.
      if (hasCreatedRef.current || (!text.trim() && !hasImages)) return;
      hasCreatedRef.current = true;
      onFirstMessageSent?.();
      const effectiveAgent = agentOverride || WORKFLOW_CHAT_AGENT_NAME;
      const effectiveModel = modelOverride === undefined ? selectedPromptModel : modelOverride;
      const effectiveDisplayText = options?.displayText;

      try {
        if (hasImages || effectiveDisplayText) {
          // initialMessage is text-only; use createAndSend so the inline
          // image parts survive into the very first prompt instead of being
          // silently dropped (the previous bug for non-Session composers).
          await createAndSendSession({
            text,
            imageParts,
            agent: effectiveAgent,
            model: effectiveModel,
            displayText: effectiveDisplayText,
          });
        } else {
          setInitialMessage(text);
          await createSession();
        }
      } catch {
        hasCreatedRef.current = false;
        setInitialMessage(null);
      }
    },
    [onFirstMessageSent, selectedPromptModel, createSession, createAndSendSession],
  );

  const handleWelcomeGuidePrompt = useCallback(
    (prompt: string, label: string) => {
      void handleCreateAndSend(prompt, [], undefined, undefined, {
        displayText: buildInstructionDisplayText(label),
      });
    },
    [handleCreateAndSend],
  );

  const handleNewSession = useCallback(() => {
    setShowHistory(false);
    setActiveSessionId(null);
    setInitialMessage(null);
    resetSession();
    hasCreatedRef.current = false;
  }, [resetSession]);

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
      const nextRevision = workflowRevisionKey(fresh);
      if (nextRevision !== workflowRevisionRef.current) {
        workflowRevisionRef.current = nextRevision;
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
      const toolPart = (
        type === 'message.part.updated' && properties?.part?.type === 'tool'
      ) ? properties.part : null;
      if (
        toolPart
        && toolPart.tool === 'run_workflow'
      ) {
        const state = toolPart.state as Record<string, any> | undefined;
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
            stepCount: metadata.step_count,
            loopProgress: metadata.loop_progress,
          });
        }
      }
      if (toolPart) {
        const state = toolPart.state as Record<string, any> | undefined;
        if (state?.status === 'completed' || state?.status === 'error') {
          void checkWorkflowUpdate();
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

    const timer = setInterval(checkWorkflowUpdate, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [sessionId, workflow.id, onWorkflowUpdated, checkWorkflowUpdate]);

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
          display={{ collapseIntermediateSteps: true, processGroupsDefaultOpen: false }}
          agentName={WORKFLOW_CHAT_AGENT_NAME}
          mentionAgents={workflowChatAgents}
          nodeRef={nodeRef}
          onNodeRefDismiss={onNodeRefDismiss}
          onStreamingDone={handleStreamingDone}
          initialMessage={initialMessage}
          onSSEEvent={handleSSEEvent}
          supportsVision={effectiveSupportsVision}
          contextWindowTokens={selectedModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
          composerTextareaMinHeight={48}
          composerTextareaMaxHeight={120}
          toolbarSlot={
            <ChatAgentDisplay
              agents={workflowChatAgents}
              selectedAgent={WORKFLOW_CHAT_AGENT_NAME}
            />
          }
          centerToolbarSlot={
            <ChatModelPicker
              groupedOptions={groupedChatModelOptions}
              loading={loadingChatModels}
              selectedModelOption={selectedModelOption}
              onSelectModel={(option) => setSelectedModelKey(option.key)}
            />
          }
          conversationBottomSlot={({ sendPrompt, sending, streaming }) => (
            <>
              <WorkflowLaunchRequestRunner
                launchRequest={launchRequest}
                enabled={sessionsHydrated}
                onLaunchRequestHandled={onLaunchRequestHandled}
                onStartPrompt={(prompt, label) => sendPrompt(prompt, {
                  displayText: label ? buildInstructionDisplayText(label) : undefined,
                })}
              />
              {sessionId || sending || streaming ? (
                <WorkflowGuideDock
                  workflow={workflow}
                  disabled={sending || streaming}
                  onStartPrompt={(prompt, label) => sendPrompt(prompt, {
                    displayText: buildInstructionDisplayText(label),
                  })}
                />
              ) : null}
            </>
          )}
          welcomeContent={!sessionId ? (
            <WorkflowWelcome
              workflow={workflow}
              error={error}
              onRetry={() => { hasCreatedRef.current = false; resetSession(); }}
              onStartPrompt={handleWelcomeGuidePrompt}
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
  onStartPrompt,
}: {
  workflow: Workflow;
  error: string | null;
  onRetry: () => void;
  onStartPrompt: (prompt: string, label: string) => void;
}) {
  const { t, i18n } = useTranslation('workflow');
  const workflowDisplayName = getWorkflowDisplayName(workflow, i18n?.language);
  const guideGroups = buildWorkflowGuideGroups(t, workflow);

  return (
    <div className="flex min-h-[420px] w-full flex-col items-center justify-center px-5 py-8">
      <p className="mb-8 text-center text-sm font-medium text-gray-400">
        {t('detail.run.noHistory')}
      </p>
      <div className="flex max-h-[min(560px,calc(100vh-260px))] w-full max-w-[420px] flex-col overflow-hidden rounded-xl border border-gray-200 bg-white px-5 py-5 text-center shadow-sm">
        <div className="flex-shrink-0">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-red-100 bg-red-50 text-red-500">
            <Bot className="h-5 w-5" />
          </div>
          <h3 className="mt-4 text-sm font-semibold text-gray-900">
            {t('detail.chat.welcome.editPanelTitle')}
          </h3>
          <p className="mx-auto mt-2 max-w-[320px] text-xs leading-relaxed text-gray-500">
            {t('detail.chat.welcome.editPanelDesc', { name: workflowDisplayName })}
          </p>
        </div>
        <div
          data-testid="workflow-edit-guide-scroll"
          className="mt-4 min-h-0 space-y-4 overflow-y-auto pr-1 text-left [scrollbar-width:thin] [scrollbar-color:#e4e4e7_transparent]"
        >
          <WorkflowGuideSection
            title={t('detail.chat.welcome.editSectionTitle')}
            actions={guideGroups.editActions}
            onStartPrompt={onStartPrompt}
          />
          <WorkflowGuideSection
            title={t('detail.chat.welcome.configSectionTitle')}
            actions={guideGroups.configActions}
            onStartPrompt={onStartPrompt}
          />
          <WorkflowGuideSection
            title={t('detail.chat.welcome.publishSectionTitle')}
            actions={guideGroups.publishActions}
            onStartPrompt={onStartPrompt}
          />
        </div>
      </div>

      {error && (
        <div className="mt-4 flex w-full max-w-[420px] items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
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

function WorkflowGuideSection({
  title,
  actions,
  onStartPrompt,
}: {
  title: string;
  actions: ChatGuideAction[];
  onStartPrompt: (prompt: string, label: string) => void;
}) {
  if (actions.length === 0) return null;

  return (
    <section>
      <h4 className="mb-2 text-[11px] font-semibold text-gray-400">{title}</h4>
      <div className="flex flex-col gap-1.5">
        {actions.map((action) => (
          <div
            key={action.label}
            className="group flex h-8 w-full items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-3 text-left text-xs font-semibold text-gray-700 transition-colors hover:border-rose-200 hover:bg-rose-50/70 hover:text-rose-600"
          >
            <button
              type="button"
              onClick={() => onStartPrompt(action.prompt, action.label)}
              className="min-w-0 flex-1 truncate text-left"
            >
              {action.label}
            </button>
            <GuideInfoIcon
              label={action.label}
              description={action.description}
              className="group-hover:text-rose-400"
            />
          </div>
        ))}
      </div>
    </section>
  );
}

function WorkflowLaunchRequestRunner({
  launchRequest,
  enabled,
  onLaunchRequestHandled,
  onStartPrompt,
}: {
  launchRequest?: WorkflowChatLaunchRequest | null;
  enabled: boolean;
  onLaunchRequestHandled?: (id: number) => void;
  onStartPrompt: (text: string, label?: string) => void;
}) {
  const handledLaunchRequestRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || !launchRequest || handledLaunchRequestRef.current === launchRequest.id) return;
    handledLaunchRequestRef.current = launchRequest.id;
    onStartPrompt(launchRequest.prompt, launchRequest.displayLabel);
    onLaunchRequestHandled?.(launchRequest.id);
  }, [enabled, launchRequest, onLaunchRequestHandled, onStartPrompt]);

  return null;
}

function buildWorkflowPromptParams(workflow: Workflow) {
  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  const workflowMdPath = `${workflowDir}workflow.md`;
  const workflowGuidePath = `${workflowDir}${WORKFLOW_GUIDE_FILE_NAME}`;
  const endpoints = workflowAPIEndpoints(workflow.id);
  return {
    id: workflow.id,
    name: workflow.name,
    dir: workflowDir,
    mdPath: workflowMdPath,
    guidePath: workflowGuidePath,
    configEndpoint: endpoints.config.read.replace(/^GET /, ''),
    configSyncEndpoint: endpoints.config.syncFallback.replace(/^POST /, ''),
    publishEndpoint: endpoints.apiService.publish.replace(/^POST /, ''),
    unpublishEndpoint: endpoints.apiService.unpublish.replace(/^POST /, ''),
    triggersEndpoint: endpoints.triggers.list.replace(/^GET /, ''),
    apiEndpoints: formatWorkflowAPIEndpoints(workflow.id),
    configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
  };
}

function buildWorkflowGuideQuestionPrompt(
  t: TranslateFn,
  workflow: Workflow,
  focus: string,
  instruction: string,
): string {
  const promptParams = withBackendConfigAccessGuide(t, buildWorkflowPromptParams(workflow));
  return [
    t(
      'detail.chat.welcome.guideQuestionPrompt',
      {
        ...promptParams,
        focus,
        instruction,
      },
    ),
    promptParams.backendConfigAccessGuide,
  ].join('\n\n');
}

function buildWorkflowEditActions(t: TranslateFn, workflow: Workflow): ChatGuideAction[] {
  const promptParams = buildWorkflowPromptParams(workflow);
  const group = t('detail.chat.welcome.editSectionTitle');
  return [
    {
      label: t('detail.chat.welcome.editRequirementShort'),
      description: t('detail.chat.welcome.editRequirementDesc'),
      prompt: t('detail.chat.welcome.editRequirementPrompt', promptParams),
      group,
    },
    {
      label: t('detail.chat.welcome.editNodeFunctionShort'),
      description: t('detail.chat.welcome.editNodeFunctionDesc'),
      prompt: t('detail.chat.welcome.editNodeFunctionPrompt', promptParams),
      group,
    },
    {
      label: t('detail.chat.welcome.editNodeShort'),
      description: t('detail.chat.welcome.editNodeDesc'),
      prompt: t('detail.chat.welcome.editNodePrompt', promptParams),
      group,
    },
    {
      label: t('detail.chat.welcome.editFlowShort'),
      description: t('detail.chat.welcome.editFlowDesc'),
      prompt: t('detail.chat.welcome.editFlowPrompt', promptParams),
      group,
    },
    {
      label: t('detail.chat.welcome.editRegenerateShort'),
      description: t('detail.chat.welcome.editRegenerateDesc'),
      prompt: t('detail.chat.welcome.editRegeneratePrompt', promptParams),
      group,
    },
  ];
}

function buildWorkflowConfigActions(t: TranslateFn, workflow: Workflow): ChatGuideAction[] {
  const promptParams = withBackendConfigAccessGuide(t, buildWorkflowPromptParams(workflow));
  const group = t('detail.chat.welcome.configSectionTitle');
  const buildQuestionPrompt = (focus: string, instruction: string) => (
    buildWorkflowGuideQuestionPrompt(t, workflow, focus, instruction)
  );
  return [
    {
      label: t('detail.chat.welcome.guidePrimaryShort'),
      description: t('detail.chat.welcome.guidePrimaryDesc'),
      prompt: [
        t('detail.chat.welcome.guidePrompt', promptParams),
        promptParams.backendConfigAccessGuide,
      ].join('\n\n'),
      group,
    },
    {
      label: t('detail.chat.welcome.guideInputModeShort'),
      description: t('detail.chat.welcome.guideInputModeDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideInputModeShort'),
        t('detail.chat.welcome.guideInputModeInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideSourceShapeShort'),
      description: t('detail.chat.welcome.guideSourceShapeDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideSourceShapeShort'),
        t('detail.chat.welcome.guideSourceShapeInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideOutputShort'),
      description: t('detail.chat.welcome.guideOutputDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideOutputShort'),
        t('detail.chat.welcome.guideOutputInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideFilterShort'),
      description: t('detail.chat.welcome.guideFilterDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideFilterShort'),
        t('detail.chat.welcome.guideFilterInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideSampleShort'),
      description: t('detail.chat.welcome.guideSampleDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideSampleShort'),
        t('detail.chat.welcome.guideSampleInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideApplyShort'),
      description: t('detail.chat.welcome.guideApplyDesc'),
      prompt: buildQuestionPrompt(
        t('detail.chat.welcome.guideApplyShort'),
        t('detail.chat.welcome.guideApplyInstruction'),
      ),
      group,
    },
    {
      label: t('detail.chat.welcome.guideAuditShort'),
      description: t('detail.chat.welcome.guideAuditDesc'),
      prompt: t('detail.chat.welcome.auditPrompt', promptParams),
      group,
    },
  ];
}

function buildWorkflowPublishActions(t: TranslateFn, workflow: Workflow): ChatGuideAction[] {
  const group = t('detail.chat.welcome.publishSectionTitle');
  const buildQuestionPrompt = (focus: string, instruction: string) => (
    buildWorkflowGuideQuestionPrompt(t, workflow, focus, instruction)
  );
  return [
    {
      label: t('detail.run.guideApiShort'),
      description: t('detail.run.guideApiDesc'),
      prompt: buildQuestionPrompt(
        t('detail.run.guideApiShort'),
        t('detail.run.guideApiInstruction'),
      ),
      group,
    },
    {
      label: t('detail.run.guideSyslogShort'),
      description: t('detail.run.guideSyslogDesc'),
      prompt: buildQuestionPrompt(
        t('detail.run.guideSyslogShort'),
        t('detail.run.guideSyslogInstruction'),
      ),
      group,
    },
    {
      label: t('detail.run.guideKafkaShort'),
      description: t('detail.run.guideKafkaDesc'),
      prompt: buildQuestionPrompt(
        t('detail.run.guideKafkaShort'),
        t('detail.run.guideKafkaInstruction'),
      ),
      group,
    },
    {
      label: t('detail.run.guideWebhookShort'),
      description: t('detail.run.guideWebhookDesc'),
      prompt: buildQuestionPrompt(
        t('detail.run.guideWebhookShort'),
        t('detail.run.guideWebhookInstruction'),
      ),
      group,
    },
    {
      label: t('detail.run.guideScheduleShort'),
      description: t('detail.run.guideScheduleDesc'),
      prompt: buildQuestionPrompt(
        t('detail.run.guideScheduleShort'),
        t('detail.run.guideScheduleInstruction'),
      ),
      group,
    },
  ];
}

function buildWorkflowGuideGroups(t: TranslateFn, workflow: Workflow) {
  const editActions = buildWorkflowEditActions(t, workflow);
  const configActions = buildWorkflowConfigActions(t, workflow);
  const publishActions = buildWorkflowPublishActions(t, workflow);
  return {
    editActions,
    configActions,
    publishActions,
    allActions: [...editActions, ...configActions, ...publishActions],
  };
}

function WorkflowGuideDock({
  workflow,
  disabled,
  onStartPrompt,
}: {
  workflow: Workflow;
  disabled?: boolean;
  onStartPrompt: (text: string, label: string) => void;
}) {
  const { t } = useTranslation('workflow');
  const guideActions = buildWorkflowGuideGroups(t, workflow).allActions;

  return (
    <ChatGuideDock
      actions={guideActions}
      disabled={disabled}
      collapseTitle={t('detail.chat.welcome.guideCollapse')}
      expandTitle={t('detail.chat.welcome.guideExpand')}
      onStartPrompt={onStartPrompt}
    />
  );
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
