import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { AlertCircle, Bot, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
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
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { workflowAPI, Workflow } from '@/api/workflow';
import type { ImagePartData } from '@/utils/imageUpload';

const FALLBACK_POLL_MS = 10_000;
const WORKFLOW_CHAT_AGENT_NAME = 'rex';
const WORKFLOW_CHAT_AGENT_NAMES = [WORKFLOW_CHAT_AGENT_NAME];

interface CreateChatTabProps {
  onWorkflowCreated: (workflow: Workflow) => void;
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
      if (!label || !prompt) return null;
      return {
        label,
        description: description || prompt,
        prompt,
      };
    })
    .filter((item): item is ChatGuideAction => Boolean(item));
}

export default function CreateChatTab({
  onWorkflowCreated,
  launchRequest,
  onLaunchRequestHandled,
}: CreateChatTabProps) {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const defaultSupportsVision = useDefaultModelVision();

  const guideActions = useMemo(() => (
    normalizeGuideActions(t('create.chat.guideActions', { returnObjects: true }))
  ), [t]);
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
    }))
  ), [exampleQuestionLabels, exampleQuestions]);
  const quickActions = useMemo(() => (
    [...guideActions, ...exampleActions]
  ), [exampleActions, guideActions]);

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
      display={{ collapseIntermediateSteps: true }}
      agentName={WORKFLOW_CHAT_AGENT_NAME}
      mentionAgents={workflowChatAgents}
      supportsVision={supportsVision}
      contextWindowTokens={selectedModelOption?.contextWindowTokens ?? null}
      model={selectedPromptModel}
      onStreamingDone={handleStreamingDone}
      onSSEEvent={handleSSEEvent}
      onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
      welcomeContent={!sessionId ? (
        <CreateWorkflowGuidePanel
          guideActions={guideActions}
          caseActions={exampleActions}
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
          onAddModel={() => navigate('/models')}
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

function CreateWorkflowGuidePanel({
  guideActions,
  caseActions,
  onStartPrompt,
}: {
  guideActions: ChatGuideAction[];
  caseActions: ChatGuideAction[];
  onStartPrompt: (prompt: string, label: string) => void;
}) {
  const { t } = useTranslation('workflow');

  return (
    <div className="flex min-h-[420px] w-full flex-col items-center justify-center px-5 py-8">
      <p className="mb-8 text-center text-sm font-medium text-gray-400">
        {t('create.chat.emptyStateTitle')}
      </p>
      <div className="flex max-h-[min(560px,calc(100vh-260px))] w-full max-w-[420px] flex-col overflow-hidden rounded-xl border border-gray-200 bg-white px-5 py-5 text-center shadow-sm">
        <div className="flex-shrink-0">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-red-100 bg-red-50 text-red-500">
            <Bot className="h-5 w-5" />
          </div>
          <h3 className="mt-4 text-sm font-semibold text-gray-900">
            {t('create.chat.guidePanelTitle')}
          </h3>
          <p className="mx-auto mt-2 max-w-[300px] text-xs leading-relaxed text-gray-500">
            {t('create.chat.guidePanelDesc')}
          </p>
        </div>
        <div
          data-testid="create-workflow-guide-scroll"
          className="mt-4 min-h-0 space-y-4 overflow-y-auto pr-1 text-left [scrollbar-width:thin] [scrollbar-color:#e4e4e7_transparent]"
        >
          <CreateGuideSection
            title={t('create.chat.guideSectionTitle')}
            actions={guideActions}
            onStartPrompt={onStartPrompt}
          />
          <CreateGuideSection
            title={t('create.chat.caseSectionTitle')}
            actions={caseActions}
            onStartPrompt={onStartPrompt}
          />
        </div>
      </div>
    </div>
  );
}

function CreateGuideSection({
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
          <button
            key={action.label}
            type="button"
            onClick={() => onStartPrompt(action.prompt, action.label)}
            className="group flex h-8 w-full items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-3 text-left text-xs font-semibold text-gray-700 transition-colors hover:border-rose-200 hover:bg-rose-50/70 hover:text-rose-600"
            title={action.description}
          >
            <span className="truncate">{action.label}</span>
            <span
              className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md text-gray-300 transition-colors group-hover:text-rose-400"
              title={action.description}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
            >
              <Info className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
