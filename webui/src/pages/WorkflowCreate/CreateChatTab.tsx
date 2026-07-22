import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { AlertCircle, Bot } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SessionChat, {
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
import GuidedCreatePanel from '@/components/common/GuidedCreatePanel';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { workflowAPI, Workflow } from '@/api/workflow';
import type { ImagePartData } from '@/utils/imageUpload';

const FALLBACK_POLL_MS = 10_000;
const WORKFLOW_CHAT_AGENT_NAME = 'rex';
const WORKFLOW_CHAT_AGENT_NAMES = [WORKFLOW_CHAT_AGENT_NAME];

interface CreateChatTabProps {
  onWorkflowCreated: (workflow: Workflow) => void;
  initialSessionId?: string | null;
  creationStartedAt?: number;
  onSessionChange?: (sessionId: string | null) => void;
  launchRequest?: CreateWorkflowChatLaunchRequest | null;
  onLaunchRequestHandled?: (id: number) => void;
}

export interface CreateWorkflowChatLaunchRequest {
  id: number;
  prompt: string;
  displayLabel?: string;
}

function normalizeGuideActions(value: unknown): ChatGuideAction[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const raw = item as Record<string, unknown>;
      const label = String(raw.label ?? '').trim();
      const description = String(raw.description ?? '').trim();
      const prompt = String(raw.prompt ?? '').trim();
      const group = String(raw.group ?? '').trim();
      if (!label || !prompt) return null;
      return {
        label,
        description: description || prompt,
        prompt,
        ...(group ? { group } : {}),
      };
    })
    .filter((item): item is ChatGuideAction => Boolean(item));
}

export default function CreateChatTab({
  onWorkflowCreated,
  initialSessionId = null,
  creationStartedAt,
  onSessionChange,
  launchRequest,
  onLaunchRequestHandled,
}: CreateChatTabProps) {
  const { t } = useTranslation('workflow');
  const defaultSupportsVision = useDefaultModelVision();
  const guideSectionTitle = t('create.chat.guideSectionTitle');
  const caseSectionTitle = t('create.chat.caseSectionTitle');

  const guideActions = useMemo(() => (
    normalizeGuideActions(t('create.chat.guideActions', { returnObjects: true }))
      .map((action) => ({ ...action, group: guideSectionTitle }))
  ), [guideSectionTitle, t]);
  const exampleQuestions = t('create.chat.exampleQuestions', { returnObjects: true }) as string[];
  const exampleQuestionLabels = t('create.chat.exampleQuestionLabels', { returnObjects: true }) as string[];
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
  const supportsVision = selectedModelOption?.supportsVision ?? defaultSupportsVision;
  const exampleActions = useMemo(() => (
    (Array.isArray(exampleQuestions) ? exampleQuestions : []).map((question, index) => ({
      label: Array.isArray(exampleQuestionLabels) && exampleQuestionLabels[index]
        ? exampleQuestionLabels[index]
        : question,
      description: question,
      prompt: question,
      group: caseSectionTitle,
    }))
  ), [caseSectionTitle, exampleQuestionLabels, exampleQuestions]);
  const quickActions = useMemo(() => (
    [...guideActions, ...exampleActions]
  ), [exampleActions, guideActions]);

  const { sessionId, error, createAndSend, retry } = useSessionChat({
    title: t('create.chat.sessionTitle'),
    category: 'workflow',
    contextMessage: t('create.chat.contextMessage'),
    welcomeMessage: t('create.chat.welcomeMessage'),
    initialSessionId,
  });

  const knownIdsRef = useRef<Set<string>>(new Set());
  const snapshotStartedAtRef = useRef<number | null>(null);
  const createdWorkflowRef = useRef<string | null>(null);
  const pendingWorkflowEventIdsRef = useRef<Set<string>>(new Set());
  const pendingDetectionRef = useRef(false);
  const [snapshotReady, setSnapshotReady] = useState(false);
  const onWorkflowCreatedRef = useRef(onWorkflowCreated);
  onWorkflowCreatedRef.current = onWorkflowCreated;

  useEffect(() => {
    onSessionChange?.(sessionId ?? null);
  }, [onSessionChange, sessionId]);

  // Snapshot existing workflow IDs on mount
  useEffect(() => {
    (async () => {
      setSnapshotReady(false);
      const snapshotStartedAt = Date.now();
      const freshBoundary = Math.max(creationStartedAt ?? 0, snapshotStartedAt) - 500;
      snapshotStartedAtRef.current = snapshotStartedAt;
      try {
        const snap = await workflowAPI.listSummaries();
        knownIdsRef.current = new Set(snap.data
          .filter((w) => {
            const createdAt = Number(w.createdAt ?? 0);
            return !(createdAt > 0 && createdAt >= freshBoundary);
          })
          .map((w) => w.id));
      } catch {
        knownIdsRef.current = new Set();
      }
      setSnapshotReady(true);
    })();
  }, [creationStartedAt]);

  const attachCreatedWorkflow = useCallback((workflow?: Workflow | null): boolean => {
    if (!workflow?.id || !snapshotReady) return false;
    if (knownIdsRef.current.has(workflow.id) || workflow.id === createdWorkflowRef.current) {
      return false;
    }
    const createdAt = Number(workflow.createdAt ?? 0);
    const startedAt = Math.max(creationStartedAt ?? 0, snapshotStartedAtRef.current ?? 0);
    if (startedAt > 0 && createdAt > 0 && createdAt < startedAt - 500) {
      return false;
    }
    createdWorkflowRef.current = workflow.id;
    onWorkflowCreatedRef.current(workflow);
    return true;
  }, [creationStartedAt, snapshotReady]);

  const attachCreatedWorkflowById = useCallback(async (workflowId: string) => {
    if (!workflowId) return;
    if (!snapshotReady) {
      pendingWorkflowEventIdsRef.current.add(workflowId);
      return;
    }
    try {
      const res = await workflowAPI.get(workflowId);
      attachCreatedWorkflow(res.data);
    } catch {
      // The workflow file may still be settling; polling can recover it.
    }
  }, [attachCreatedWorkflow, snapshotReady]);

  // Check for new workflows (used by fallback polling and post-stream refresh)
  const detectNewWorkflow = useCallback(async () => {
    if (!snapshotReady) {
      pendingDetectionRef.current = true;
      return;
    }
    try {
      const res = await workflowAPI.listSummaries();
      const workflows = res.data;
      const sortedWorkflows = [...workflows].sort((a, b) => Number(b.createdAt ?? 0) - Number(a.createdAt ?? 0));
      const startedAt = Math.max(creationStartedAt ?? 0, snapshotStartedAtRef.current ?? 0);
      const freshCandidates = sortedWorkflows.filter((w) => {
        if (knownIdsRef.current.has(w.id) || w.id === createdWorkflowRef.current) return false;
        const createdAt = Number(w.createdAt ?? 0);
        return !(startedAt > 0 && createdAt > 0 && createdAt < startedAt - 500);
      });
      if (freshCandidates.length === 1) {
        await attachCreatedWorkflowById(freshCandidates[0].id);
      }
    } catch { /* ignore */ }
  }, [attachCreatedWorkflowById, creationStartedAt, snapshotReady]);

  useEffect(() => {
    if (!snapshotReady) return;

    if (pendingWorkflowEventIdsRef.current.size > 0) {
      const ids = Array.from(pendingWorkflowEventIdsRef.current);
      pendingWorkflowEventIdsRef.current.clear();
      ids.forEach((id) => {
        void attachCreatedWorkflowById(id);
      });
    }

    if (pendingDetectionRef.current) {
      pendingDetectionRef.current = false;
      void detectNewWorkflow();
    }
  }, [attachCreatedWorkflowById, detectNewWorkflow, snapshotReady]);

  // SSE: react to workflow.created events immediately
  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      if (event.type === 'workflow.created' && event.properties?.id) {
        void attachCreatedWorkflowById(String(event.properties.id));
      }
    },
    [attachCreatedWorkflowById],
  );

  // Primary: check right after AI finishes streaming
  const handleStreamingDone = useCallback(() => {
    void detectNewWorkflow();
  }, [detectNewWorkflow]);

  // Fallback polling for filesystem-driven creation (Rex writes directly)
  useEffect(() => {
    if (!sessionId || !snapshotReady) return;

    const timer = setInterval(detectNewWorkflow, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [sessionId, snapshotReady, detectNewWorkflow]);

  const handleCreateAndSend = useCallback(
    async (
      text: string,
      imageParts?: ImagePartData[],
      agentOverride?: string,
      modelOverride?: { providerID: string; modelID: string } | null,
      options?: PromptDisplayOptions,
    ) => {
      await createAndSend({
        text,
        imageParts,
        agent: agentOverride || WORKFLOW_CHAT_AGENT_NAME,
        model: modelOverride === undefined ? selectedPromptModel : modelOverride,
        displayText: options?.displayText,
      });
    },
    [createAndSend, selectedPromptModel],
  );

  const handleWelcomeGuidePrompt = useCallback(
    (prompt: string, label: string) => {
      void handleCreateAndSend(prompt, [], undefined, undefined, {
        displayText: buildInstructionDisplayText(label),
      });
    },
    [handleCreateAndSend],
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
      display={{ collapseIntermediateSteps: true, processGroupsDefaultOpen: false }}
      agentName={WORKFLOW_CHAT_AGENT_NAME}
      mentionAgents={workflowChatAgents}
      supportsVision={supportsVision}
      contextWindowTokens={selectedModelOption?.contextWindowTokens ?? null}
      model={selectedPromptModel}
      onStreamingDone={handleStreamingDone}
      onSSEEvent={handleSSEEvent}
      onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
      welcomeContent={!sessionId ? (
        <GuidedCreatePanel
          emptyTitle={t('create.chat.emptyStateTitle')}
          icon={<Bot className="h-5 w-5" />}
          title={t('create.chat.guidePanelTitle')}
          description={t('create.chat.guidePanelDesc')}
          groups={[
            { title: t('create.chat.guideSectionTitle'), actions: guideActions },
            { title: t('create.chat.caseSectionTitle'), actions: exampleActions },
          ]}
          scrollTestId="create-workflow-guide-scroll"
          onStartPrompt={handleWelcomeGuidePrompt}
        />
      ) : undefined}
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
          <CreateWorkflowLaunchRequestRunner
            launchRequest={launchRequest}
            onLaunchRequestHandled={onLaunchRequestHandled}
            onStartPrompt={(prompt, label) => sendPrompt(prompt, {
              displayText: label ? buildInstructionDisplayText(label) : undefined,
            })}
          />
          {sessionId || sending || streaming ? (
            <ChatGuideDock
              actions={quickActions}
              disabled={sending || streaming}
              collapseTitle={t('detail.chat.welcome.guideCollapse')}
              expandTitle={t('detail.chat.welcome.guideExpand')}
              onStartPrompt={(prompt, label) => sendPrompt(prompt, {
                displayText: buildInstructionDisplayText(label),
              })}
            />
          ) : null}
        </>
      )}
    />
  );
}

function CreateWorkflowLaunchRequestRunner({
  launchRequest,
  onLaunchRequestHandled,
  onStartPrompt,
}: {
  launchRequest?: CreateWorkflowChatLaunchRequest | null;
  onLaunchRequestHandled?: (id: number) => void;
  onStartPrompt: (text: string, label?: string) => void;
}) {
  const handledLaunchRequestRef = useRef<number | null>(null);

  useEffect(() => {
    if (!launchRequest || handledLaunchRequestRef.current === launchRequest.id) return;
    handledLaunchRequestRef.current = launchRequest.id;
    onStartPrompt(launchRequest.prompt, launchRequest.displayLabel);
    onLaunchRequestHandled?.(launchRequest.id);
  }, [launchRequest, onLaunchRequestHandled, onStartPrompt]);

  return null;
}
