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
  type ChatModelOption,
} from '@/components/common/ChatPromptSelectors';
import ChatGuideDock, { type ChatGuideAction } from '@/components/common/ChatGuideDock';
import GuidedCreatePanel from '@/components/common/GuidedCreatePanel';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { workflowAPI, Workflow } from '@/api/workflow';
import client from '@/api/client';
import type { ImagePartData } from '@/utils/imageUpload';
import {
  addWorkflowLinks,
  pickLinkedWorkflow,
  workflowLinksFromMessages,
  workflowLinksFromToolPart,
  type WorkflowLinkKind,
} from './workflowSessionLinks';

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
    effectiveModelOption,
    modelPickerAutoOption,
    selectModelKey,
    selectedModelAuto,
    selectedModelOption,
    selectedPromptModel,
    setSelectedModelAuto,
  } = useChatModelOptions({ enableAuto: true });
  const supportsVision = effectiveModelOption?.supportsVision ?? defaultSupportsVision;
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

  const {
    sessionId,
    error,
    pendingOptimisticMessage,
    createAndSend,
    consumePendingOptimisticMessage,
    retry,
  } = useSessionChat({
    title: t('create.chat.sessionTitle'),
    category: 'workflow',
    modelAuto: selectedModelAuto,
    contextMessage: t('create.chat.contextMessage'),
    welcomeMessage: t('create.chat.welcomeMessage'),
    initialSessionId,
  });

  useEffect(() => {
    setSelectedModelAuto(false);
    if (!initialSessionId) return;
    let cancelled = false;
    client.get(`/api/session/${initialSessionId}`)
      .then((response) => {
        if (cancelled) return;
        setSelectedModelAuto(Boolean(response.data?.model_auto));
      })
      .catch(() => {
        if (cancelled) return;
        setSelectedModelAuto(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialSessionId, setSelectedModelAuto]);

  const handleSelectModel = useCallback((option: ChatModelOption) => {
    selectModelKey(option.key);
    if (!sessionId) return;
    client.patch(`/api/session/${sessionId}`, {
      provider: option.providerID,
      model: option.modelID,
      model_pinned: true,
      model_auto: false,
    }).catch((err) => {
      console.warn('[WorkflowCreate] failed to pin workflow chat model', err);
    });
  }, [selectModelKey, sessionId]);

  const handleSelectAuto = useCallback(() => {
    if (!modelPickerAutoOption || modelPickerAutoOption.disabled) return;
    modelPickerAutoOption.onSelect();
    if (!sessionId) return;
    client.patch(`/api/session/${sessionId}`, {
      model_auto: true,
      model_pinned: false,
    }).catch((err) => {
      console.warn('[WorkflowCreate] failed to enable Auto for workflow chat', err);
    });
  }, [modelPickerAutoOption, sessionId]);

  const knownIdsRef = useRef<Set<string>>(new Set());
  const snapshotStartedAtRef = useRef<number | null>(null);
  const createdWorkflowRef = useRef<string | null>(null);
  const pendingWorkflowEventIdsRef = useRef<Set<string>>(new Set());
  const pendingDetectionRef = useRef(false);
  const [snapshotReady, setSnapshotReady] = useState(false);
  const onWorkflowCreatedRef = useRef(onWorkflowCreated);
  onWorkflowCreatedRef.current = onWorkflowCreated;
  // Workflows this chat's own tool calls wrote. Only these can be attached:
  // other users' workflows show up in the same global list and may announce
  // themselves through workflow.created as well.
  const workflowLinksRef = useRef<Map<string, WorkflowLinkKind>>(new Map());
  // Candidates already checked against the stored conversation. A tool part is
  // stored with its input before the tool runs, so once a candidate exists one
  // successful read settles it; later calls arrive as live tool events.
  const checkedCandidateIdsRef = useRef<Set<string>>(new Set());
  const linkSessionIdRef = useRef<string | null>(sessionId ?? null);
  const linkBackfillRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    onSessionChange?.(sessionId ?? null);
  }, [onSessionChange, sessionId]);

  useEffect(() => {
    linkSessionIdRef.current = sessionId ?? null;
    workflowLinksRef.current = new Map();
    checkedCandidateIdsRef.current = new Set();
    linkBackfillRef.current = null;
  }, [sessionId]);

  // Read the stored conversation for candidates it has not been checked
  // against yet: the page may have been reloaded mid-creation or missed SSE.
  const backfillWorkflowLinks = useCallback(async (candidateIds: string[]) => {
    if (!sessionId) return;
    const uncheckedIds = () => candidateIds.filter((id) => (
      !workflowLinksRef.current.has(id) && !checkedCandidateIdsRef.current.has(id)
    ));
    if (uncheckedIds().length === 0) return;
    // A read already in flight may predate these candidates: wait for it,
    // then read again only for what it did not settle.
    if (linkBackfillRef.current) await linkBackfillRef.current;
    const unchecked = uncheckedIds();
    if (unchecked.length === 0) return;
    if (linkBackfillRef.current) {
      // Another caller started a read after the one awaited above, so it
      // already covers these candidates' tool calls; use its result.
      await linkBackfillRef.current;
      return;
    }
    const backfill = client.get(`/api/session/${sessionId}/message`, {
      params: { include_archived: true },
    })
      .then((response) => {
        if (linkSessionIdRef.current !== sessionId) return;
        addWorkflowLinks(workflowLinksRef.current, workflowLinksFromMessages(response.data));
        unchecked.forEach((id) => checkedCandidateIdsRef.current.add(id));
      })
      .catch(() => {
        // Left unchecked so the next poll retries.
      })
      .finally(() => {
        if (linkBackfillRef.current === backfill) linkBackfillRef.current = null;
      });
    linkBackfillRef.current = backfill;
    await backfill;
  }, [sessionId]);

  const checkWorkflowLinked = useCallback(async (workflowId: string) => {
    await backfillWorkflowLinks([workflowId]);
    return workflowLinksRef.current.has(workflowId);
  }, [backfillWorkflowLinks]);

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
    if (!(await checkWorkflowLinked(workflowId))) return;
    try {
      const res = await workflowAPI.get(workflowId);
      attachCreatedWorkflow(res.data);
    } catch {
      // The workflow file may still be settling; polling can recover it.
    }
  }, [attachCreatedWorkflow, checkWorkflowLinked, snapshotReady]);

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
      if (freshCandidates.length === 0) return;
      await backfillWorkflowLinks(freshCandidates.map((w) => w.id));
      const linked = pickLinkedWorkflow(freshCandidates, workflowLinksRef.current);
      if (linked) {
        await attachCreatedWorkflowById(linked.id);
      }
    } catch { /* ignore */ }
  }, [attachCreatedWorkflowById, backfillWorkflowLinks, creationStartedAt, snapshotReady]);

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

  // SSE: learn which workflows this chat's tool calls write, and react to
  // workflow.created immediately when it names one of them.
  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      if (event.type === 'message.part.updated') {
        const part = event.properties?.part;
        if (part?.sessionID === sessionId) {
          addWorkflowLinks(workflowLinksRef.current, workflowLinksFromToolPart(part));
        }
        return;
      }
      if (event.type === 'workflow.created' && event.properties?.id) {
        void attachCreatedWorkflowById(String(event.properties.id));
      }
    },
    [attachCreatedWorkflowById, sessionId],
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
        modelAuto: selectedModelAuto,
        displayText: options?.displayText,
      });
    },
    [createAndSend, selectedModelAuto, selectedPromptModel],
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
          <AlertCircle aria-hidden="true" className="w-4 h-4 flex-shrink-0" />
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
      contextWindowTokens={effectiveModelOption?.contextWindowTokens ?? null}
      model={selectedPromptModel}
      modelAuto={selectedModelAuto}
      initialOptimisticMessage={pendingOptimisticMessage}
      onInitialOptimisticMessageConsumed={consumePendingOptimisticMessage}
      onStreamingDone={handleStreamingDone}
      onSSEEvent={handleSSEEvent}
      onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
      welcomeContent={!sessionId ? (
        <GuidedCreatePanel
          emptyTitle={t('create.chat.emptyStateTitle')}
          icon={<Bot aria-hidden="true" className="h-5 w-5" />}
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
          onSelectModel={handleSelectModel}
          autoOption={modelPickerAutoOption
            ? { ...modelPickerAutoOption, onSelect: handleSelectAuto }
            : undefined}
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
