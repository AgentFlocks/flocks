/**
 * EntitySheet — 统一的实体创建/编辑侧边抽屉
 *
 * 集成「表单」「Rex 对话」「测试」三种交互模式：
 * - 表单模式：传统字段填写，预填已有数据（编辑模式）
 * - Rex 对话模式：与 Rex 自然语言对话，可一键将建议提取并应用到表单
 * - 测试模式：直接向实体（如 Agent）发送消息验证效果
 *
 * 使用方式：
 * 1. 表单内组件可通过 useEntitySheet() 获取 openRex(msg?) / openTest() 方法
 * 2. 传入 onExtractFromRex 启用「从 Rex 提取配置」功能
 * 3. 传入 onRunTest 启用「测试」Tab
 */

import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react';
import {
  X,
  FileText,
  MessageSquare,
  Loader2,
  AlertCircle,
  ArrowRight,
  TestTube,
  Send,
  RotateCcw,
  GripVertical,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import client from '@/api/client';
import SessionChat, { buildInstructionDisplayText, type SessionChatDisplay } from './SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import ChatGuideDock, { type ChatGuideAction } from './ChatGuideDock';
import GuidedCreatePanel, { type GuidedCreateGroup } from './GuidedCreatePanel';
import type { Agent } from '@/api/agent';
import {
  SIDE_PANEL_MIN_WIDTH,
  getInitialSidePanelWidth,
  getMaxSidePanelWidth,
} from './sidePanelSizing';
// ─── Context ──────────────────────────────────────────────────────────────────

interface EntitySheetCtx {
  /** Switch to the Rex tab, optionally sending an initial message */
  openRex: (prefillMessage?: string) => void;
  /** Switch to the Test tab */
  openTest: () => void;
}

const EntitySheetContext = createContext<EntitySheetCtx>({ openRex: () => {}, openTest: () => {} });
const REX_WORKBENCH_DISPLAY: SessionChatDisplay = {
  collapseIntermediateSteps: true,
  processGroupsDefaultOpen: false,
};
const EXTRACT_FROM_REX_GUIDE_PROMPT = '__flocks_extract_from_rex__';
const REX_SESSION_STORAGE_PREFIX = 'flocks:entity-sheet:rex-session:v1';

function rexSessionStorageKey(key: string): string {
  return `${REX_SESSION_STORAGE_PREFIX}:${key}`;
}

function readStoredRexSessionId(key?: string | null): string | null {
  if (!key || typeof window === 'undefined') return null;

  try {
    return window.localStorage.getItem(rexSessionStorageKey(key));
  } catch {
    return null;
  }
}

function writeStoredRexSessionId(key: string | undefined, sessionId: string | null) {
  if (!key || typeof window === 'undefined') return;

  try {
    const storageKey = rexSessionStorageKey(key);
    if (sessionId) {
      window.localStorage.setItem(storageKey, sessionId);
    } else {
      window.localStorage.removeItem(storageKey);
    }
  } catch {
    // Ignore storage failures; the active in-memory chat still works.
  }
}

/** Consume inside form content to get access to openRex() / openTest() */
export function useEntitySheet() {
  return useContext(EntitySheetContext);
}

// ─── Types ────────────────────────────────────────────────────────────────────

type Tab = 'form' | 'rex' | 'test';

export interface EntitySheetProps {
  open: boolean;
  mode: 'create' | 'edit';
  /** e.g. "Agent", "任务", "工作流" */
  entityType: string;
  /** Displayed in title for edit mode, e.g. "rex" */
  entityName?: string;
  /** Small icon in the header */
  icon?: React.ReactNode;
  /** Form content. Use useEntitySheet() inside to call openRex(). */
  children: React.ReactNode;
  /** System context injected at session start (noReply) */
  rexSystemContext: string;
  /** Rex's opening message (mockReply) */
  rexWelcomeMessage: string;
  submitDisabled?: boolean;
  submitLoading?: boolean;
  /** Defaults to translated "Create" / "Save" based on mode */
  submitLabel?: string;
  /** Drawer width in pixels */
  width?: number;
  /** Minimum drawer width */
  minWidth?: number;
  /** Maximum drawer width */
  maxWidth?: number;
  onClose: () => void;
  onSubmit: () => void | Promise<void>;
  /**
   * If provided, a "从 Rex 提取配置" action appears in the Rex tab.
   * Called with current sessionId when the button is clicked.
   * Should resolve on success (EntitySheet auto-switches to form tab)
   * or throw on failure (error is shown to user).
   */
  onExtractFromRex?: (sessionId: string) => Promise<void>;
  /**
   * If provided, a "测试" tab is shown. Called with the prompt string,
   * should resolve with a sessionId to display in SessionChat.
   */
  onRunTest?: (prompt: string) => Promise<string>;
  /** Default prompt pre-filled in the test input */
  defaultTestPrompt?: string;
  /** Hide Rex tab (form-only sheet) */
  hideRex?: boolean;
  /** Hide Test tab */
  hideTest?: boolean;
  /** Hide form tab (rex-only sheet, e.g. API 创建只能从工作台开始) */
  hideForm?: boolean;
  /** Initial tab when open (e.g. "form" to show 详情 first when creating) */
  initialTab?: 'form' | 'rex';
  /** Guided actions shown in the Rex tab before and after the conversation starts. */
  rexGuideGroups?: GuidedCreateGroup[];
  rexGuidePanelTitle?: string;
  rexGuidePanelDesc?: string;
  rexGuideEmptyTitle?: string;
  rexGuideCollapseTitle?: string;
  rexGuideExpandTitle?: string;
  rexGuideIcon?: React.ReactNode;
  /** SessionChat composer options for Rex-assisted pages. */
  rexAgentName?: string;
  rexMentionAgents?: Agent[];
  rexModel?: { providerID: string; modelID: string } | null;
  rexSupportsVision?: boolean | null;
  rexContextWindowTokens?: number | null;
  /** Persist and resume the Rex conversation across refreshes when provided. */
  rexSessionStorageKey?: string;
  rexToolbarSlot?: React.ReactNode;
  rexCenterToolbarSlot?: React.ReactNode;
  rexComposerTextareaMinHeight?: number;
  rexComposerTextareaMaxHeight?: number;
  /** Optional element rendered on the left side of the form-tab footer (e.g. delete button) */
  footerLeft?: React.ReactNode;
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function EntitySheet({
  open,
  mode,
  entityType,
  entityName,
  icon,
  children,
  rexSystemContext,
  rexWelcomeMessage,
  submitDisabled,
  submitLoading,
  submitLabel,
  width: initialWidth,
  minWidth = SIDE_PANEL_MIN_WIDTH,
  maxWidth,
  onClose,
  onSubmit,
  onExtractFromRex,
  onRunTest,
  defaultTestPrompt,
  hideRex = false,
  hideTest = false,
  hideForm = false,
  initialTab,
  rexGuideGroups,
  rexGuidePanelTitle,
  rexGuidePanelDesc,
  rexGuideEmptyTitle,
  rexGuideCollapseTitle,
  rexGuideExpandTitle,
  rexGuideIcon,
  rexAgentName,
  rexMentionAgents,
  rexModel,
  rexSupportsVision,
  rexContextWindowTokens,
  rexSessionStorageKey,
  rexToolbarSlot,
  rexCenterToolbarSlot,
  rexComposerTextareaMinHeight,
  rexComposerTextareaMaxHeight,
  footerLeft,
}: EntitySheetProps) {
  const { t } = useTranslation('common');
  const supportsVision = useDefaultModelVision();
  const resolvedInitialWidth = () => initialWidth ?? getInitialSidePanelWidth();
  const resolvedMaxWidth = maxWidth ?? getMaxSidePanelWidth();
  const showTabs = !(hideRex && hideTest);
  const hasFormTab = !hideForm;
  const title =
    mode === 'create'
      ? t('entity.createTitle', { entityType })
      : entityName
        ? t('entity.editTitleWithName', { entityType, entityName })
        : t('entity.editTitle', { entityType });
  const defaultSubmitLabel = mode === 'create' ? t('entity.defaultCreate') : t('entity.defaultSave');
  const effectiveDefaultTestPrompt = defaultTestPrompt ?? t('entity.defaultTestPrompt', { defaultValue: 'Hello, please introduce yourself.' });

  const getDefaultTab = (): Tab => {
    if (!showTabs) return 'form';
    if (hideForm) return 'rex';
    if (initialTab === 'form' || initialTab === 'rex') return initialTab;
    if (mode === 'create' && !hideRex) return 'rex';
    return 'form';
  };

  const [activeTab, setActiveTab] = useState<Tab>(getDefaultTab);
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [rexInitialMessage, setRexInitialMessage] = useState<string | null>(null);
  const [storedRexSessionId, setStoredRexSessionId] = useState<string | null>(null);
  const [rexSessionHydrated, setRexSessionHydrated] = useState(() => !rexSessionStorageKey);
  const [drawerWidth, setDrawerWidth] = useState(resolvedInitialWidth);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(resolvedInitialWidth());

  // ── Rex session via unified hook ──────────────────────────────────────────
  const {
    sessionId,
    loading: sessionLoading,
    error: sessionError,
    create: createRexSession,
    createAndSend: createAndSendRex,
    retry: retryRexSession,
    reset: resetRexSession,
  } = useSessionChat({
    title: `${title} — ${t('entity.rexAssist')}`,
    category: 'entity-config',
    contextMessage: rexSystemContext,
    welcomeMessage: rexWelcomeMessage,
    initialSessionId: storedRexSessionId,
  });
  const activeRexSessionId = storedRexSessionId || sessionId;

  useEffect(() => {
    let cancelled = false;
    setRexSessionHydrated(false);

    const stored = readStoredRexSessionId(rexSessionStorageKey);
    if (!stored) {
      setStoredRexSessionId(null);
      setRexSessionHydrated(true);
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        await client.get(`/api/session/${stored}`);
        if (cancelled) return;
        setStoredRexSessionId(stored);
      } catch {
        if (cancelled) return;
        writeStoredRexSessionId(rexSessionStorageKey, null);
        setStoredRexSessionId(null);
      } finally {
        if (!cancelled) {
          setRexSessionHydrated(true);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [rexSessionStorageKey]);

  useEffect(() => {
    if (!rexSessionHydrated || !sessionId) return;
    writeStoredRexSessionId(rexSessionStorageKey, sessionId);
  }, [rexSessionHydrated, rexSessionStorageKey, sessionId]);

  // ── Test tab state ────────────────────────────────────────────────────────
  const [testPrompt, setTestPrompt] = useState(effectiveDefaultTestPrompt);
  const [testSessionId, setTestSessionId] = useState<string | null>(null);
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const testTextareaRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const effectiveRexGuideGroups = (rexGuideGroups ?? [])
    .map((group) => ({
      ...group,
      actions: group.actions.filter((action) => action.label && action.prompt),
    }))
    .filter((group) => group.actions.length > 0);
  const baseRexGuideActions: ChatGuideAction[] = effectiveRexGuideGroups.flatMap((group) => (
    group.actions.map((action) => ({
      ...action,
      group: action.group ?? group.title,
    }))
  ));
  const extractRexGuideAction: ChatGuideAction | null = onExtractFromRex && activeRexSessionId
    ? {
        label: t('entity.extractFromRex'),
        description: t('entity.extractFromRexGuideDesc'),
        prompt: EXTRACT_FROM_REX_GUIDE_PROMPT,
        group: effectiveRexGuideGroups[0]?.title,
      }
    : null;
  const rexGuideActions = extractRexGuideAction
    ? [extractRexGuideAction, ...baseRexGuideActions]
    : baseRexGuideActions;
  const rexWelcomeGuideGroups = extractRexGuideAction
    ? effectiveRexGuideGroups.length > 0
      ? [
          {
            ...effectiveRexGuideGroups[0],
            actions: [extractRexGuideAction, ...effectiveRexGuideGroups[0].actions],
          },
          ...effectiveRexGuideGroups.slice(1),
        ]
      : [{ title: '', actions: [extractRexGuideAction] }]
    : effectiveRexGuideGroups;
  const hasRexGuideActions = rexGuideActions.length > 0;
  const showRexFooter =
    activeTab === 'rex' &&
    (Boolean(extractError) ||
      Boolean(!onExtractFromRex && activeRexSessionId && hasFormTab) ||
      (mode === 'create' && !hasRexGuideActions));

  // ── Auto-resize test textarea ─────────────────────────────────────────────

  useEffect(() => {
    const el = testTextareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
    }
  }, [testPrompt]);

  // ── Reset when closed ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) {
      setActiveTab(getDefaultTab());
      if (!rexSessionStorageKey) {
        resetRexSession();
      }
      setRexInitialMessage(null);
      setExtracting(false);
      setExtractError(null);
      setTestSessionId(null);
      setTestLoading(false);
      setTestError(null);
      setTestPrompt(effectiveDefaultTestPrompt);
      setDrawerWidth(resolvedInitialWidth());
    }
  }, [open, mode, defaultTestPrompt, resetRexSession, initialWidth, showTabs, hideRex, hideForm, initialTab, rexSessionStorageKey]);

  // ── Tab handling ──────────────────────────────────────────────────────────

  const handleTabChange = useCallback(
    (tab: Tab) => {
      setActiveTab(tab);
    },
    [],
  );

  // ── Drawer width resizing ───────────────────────────────────────────────

  const drawerWidthRef = useRef(drawerWidth);
  drawerWidthRef.current = drawerWidth;

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsDragging(true);
      dragStartX.current = e.clientX;
      dragStartWidth.current = drawerWidthRef.current;

      const handleMouseMove = (ev: MouseEvent) => {
        const delta = dragStartX.current - ev.clientX;
        setDrawerWidth(Math.min(resolvedMaxWidth, Math.max(minWidth, dragStartWidth.current + delta)));
      };

      const handleMouseUp = () => {
        setIsDragging(false);
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
      };

      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    },
    [minWidth, resolvedMaxWidth],
  );

  // ── openRex (exposed via context) ─────────────────────────────────────────

  const openRex = useCallback(
    (msg?: string) => {
      setActiveTab('rex');
      if (activeRexSessionId && msg) {
        const payload: Record<string, unknown> = {
          parts: [{ type: 'text', text: msg }],
        };
        if (rexAgentName) payload.agent = rexAgentName;
        if (rexModel) payload.model = rexModel;
        client.post(`/api/session/${activeRexSessionId}/prompt_async`, payload);
      } else if (msg) {
        createAndSendRex({ text: msg, agent: rexAgentName, model: rexModel }).catch(() => {});
      }
    },
    [activeRexSessionId, createAndSendRex, rexAgentName, rexModel],
  );

  // ── openTest (exposed via context) ────────────────────────────────────────

  const openTest = useCallback(() => {
    setActiveTab('test');
  }, []);

  // ── Test session ──────────────────────────────────────────────────────────

  const handleRunTest = async () => {
    if (!onRunTest || !testPrompt.trim() || testLoading) return;
    setTestLoading(true);
    setTestError(null);
    try {
      const sid = await onRunTest(testPrompt.trim());
      setTestSessionId(sid);
    } catch (err: unknown) {
      setTestError(err instanceof Error ? err.message : t('entity.testStartFailed'));
    } finally {
      setTestLoading(false);
    }
  };

  const handleResetTest = () => {
    setTestSessionId(null);
    setTestError(null);
  };

  // ── Extract from Rex ──────────────────────────────────────────────────────

  const handleExtract = useCallback(async () => {
    if (!activeRexSessionId || !onExtractFromRex) return;
    setExtracting(true);
    setExtractError(null);
    try {
      await onExtractFromRex(activeRexSessionId);
      setActiveTab('form');
    } catch (err: unknown) {
      setExtractError(err instanceof Error ? err.message : t('entity.extractFailed'));
    } finally {
      setExtracting(false);
    }
  }, [activeRexSessionId, onExtractFromRex, t]);

  const startRexGuidePrompt = useCallback((prompt: string, label: string) => {
    if (prompt === EXTRACT_FROM_REX_GUIDE_PROMPT) {
      void handleExtract();
      return;
    }

    createAndSendRex({
      text: prompt,
      agent: rexAgentName,
      model: rexModel,
      displayText: buildInstructionDisplayText(label),
    }).catch(() => {});
  }, [createAndSendRex, handleExtract, rexAgentName, rexModel]);

  if (!open) return null;

  return (
    <EntitySheetContext.Provider value={{ openRex, openTest }}>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />

      {/* Drawer */}
      <div
        className="fixed right-0 top-0 bottom-0 z-50 flex flex-col bg-white shadow-2xl transition-colors"
        style={{ width: drawerWidth, maxWidth: '100%' }}
      >
        {/* Drag handle */}
        <div
          className={`absolute left-0 top-0 bottom-0 w-1 cursor-ew-resize hover:bg-red-400 transition-colors ${
            isDragging ? 'bg-red-500' : ''
          }`}
          onMouseDown={handleDragStart}
        >
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <GripVertical className="w-3 h-6 text-gray-300 hover:text-gray-500" />
          </div>
        </div>
        {/* ── Header ── */}
        <div className="flex-shrink-0 border-b border-gray-200">
          <div className="flex items-center gap-3 px-6 py-4">
            {icon && <div className="text-gray-500 flex-shrink-0">{icon}</div>}
            <h2 className="text-lg font-semibold text-gray-900 flex-1 min-w-0 break-words">
              {title}
            </h2>
            <button
              onClick={onClose}
              className="flex-shrink-0 p-1 rounded hover:bg-gray-100 transition-colors"
            >
              <X className="w-5 h-5 text-gray-400" />
            </button>
          </div>

          {/* Tabs */}
          {showTabs && (
            <div className="flex w-full px-6">
              {hasFormTab && (
                <SheetTab
                  active={activeTab === 'form'}
                  onClick={() => handleTabChange('form')}
                  icon={<FileText className="w-3.5 h-3.5" />}
                >
                  {t('entity.tabDetails')}
                </SheetTab>
              )}
              {!hideRex && (
                <SheetTab
                  active={activeTab === 'rex'}
                  onClick={() => handleTabChange('rex')}
                  icon={<MessageSquare className="w-3.5 h-3.5" />}
                >
                  {t('entity.tabAIEdit')}
                </SheetTab>
              )}
              {onRunTest && !hideTest && (
                <SheetTab
                  active={activeTab === 'test'}
                  onClick={() => setActiveTab('test')}
                  icon={<TestTube className="w-3.5 h-3.5" />}
                >
                  {t('entity.tabTest')}
                </SheetTab>
              )}
            </div>
          )}
        </div>

        {/* ── Body ── */}
        <div className="flex-1 min-h-0 overflow-hidden">
          {/* Form Tab */}
          {hasFormTab && (
            <div
              className={`h-full overflow-y-auto px-6 py-5 space-y-4 ${
                !showTabs || activeTab === 'form' ? '' : 'hidden'
              }`}
            >
              {children}
            </div>
          )}

          {/* Test Tab */}
          {activeTab === 'test' && (
            <div className="h-full flex flex-col">
              {testSessionId ? (
                <SessionChat
                  sessionId={testSessionId}
                  live
                  hideInput
                  className="flex-1"
                  emptyText={t('entity.rexThinking')}
                />
              ) : (
                <div className="flex-1 min-h-0 overflow-y-auto bg-gray-50 px-4 py-4 flex flex-col items-center justify-center gap-2">
                  {testError ? (
                    <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3 w-full">
                      <AlertCircle className="w-4 h-4 flex-shrink-0" />
                      {testError}
                    </div>
                  ) : (
                    <>
                      <TestTube className="w-8 h-8 text-gray-300" />
                      <p className="text-sm text-gray-400">{t('entity.editAndSend')}</p>
                    </>
                  )}
                </div>
              )}

              {/* Input bar */}
              <div className="flex-shrink-0 border-t border-gray-200 bg-white px-4 py-3">
                {testSessionId ? (
                  <button
                    onClick={handleResetTest}
                    className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    {t('entity.reTest')}
                  </button>
                ) : (
                  <div className="flex items-end gap-2">
                    <div className="flex-1 border border-gray-300 rounded-lg px-3 py-2 focus-within:border-red-500 focus-within:ring-2 focus-within:ring-red-100 transition-all bg-white">
                      <textarea
                        ref={testTextareaRef}
                        value={testPrompt}
                        onChange={(e) => setTestPrompt(e.target.value)}
                        onCompositionStart={() => { isComposingRef.current = true; }}
                        onCompositionEnd={() => { isComposingRef.current = false; }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                            e.preventDefault();
                            handleRunTest();
                          }
                        }}
                        placeholder={t('entity.testInputPlaceholder')}
                        className="w-full resize-none outline-none text-sm text-gray-900 placeholder-gray-400"
                        style={{ minHeight: '24px', maxHeight: '96px' }}
                        rows={1}
                        disabled={testLoading}
                      />
                    </div>
                    <button
                      onClick={handleRunTest}
                      disabled={testLoading || !testPrompt.trim()}
                      className="flex-shrink-0 px-3 py-2 h-[40px] bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 text-sm transition-colors"
                    >
                      {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Rex Tab */}
          {activeTab === 'rex' && (
            <div className="h-full flex flex-col">
              {sessionError && (
                <div className="flex flex-col items-center justify-center flex-1 gap-4 p-6 text-center">
                  <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3 w-full">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    {sessionError}
                  </div>
                  <button
                    onClick={retryRexSession}
                    className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 transition-colors"
                  >
                    {t('entity.rexRetry')}
                  </button>
                </div>
              )}
              {!sessionError && !rexSessionHydrated && (
                <div className="flex items-center justify-center flex-1 text-sm text-gray-400 gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('entity.startingRex')}
                </div>
              )}
              {!sessionError && rexSessionHydrated && (
                <SessionChat
                  sessionId={activeRexSessionId}
                  live={!!activeRexSessionId}
                  placeholder={t('entity.rexInputPlaceholder')}
                  className="h-full"
                  emptyText={t('entity.rexReady')}
                  display={REX_WORKBENCH_DISPLAY}
                  initialMessage={rexInitialMessage}
                  agentName={rexAgentName}
                  mentionAgents={rexMentionAgents}
                  model={rexModel}
                  supportsVision={rexSupportsVision ?? supportsVision}
                  contextWindowTokens={rexContextWindowTokens}
                  toolbarSlot={rexToolbarSlot}
                  centerToolbarSlot={rexCenterToolbarSlot}
                  composerTextareaMinHeight={rexComposerTextareaMinHeight}
                  composerTextareaMaxHeight={rexComposerTextareaMaxHeight}
                  onCreateAndSend={!activeRexSessionId ? (text, imageParts, agentOverride, modelOverride, options) => createAndSendRex({
                    text,
                    imageParts,
                    agent: agentOverride || rexAgentName,
                    model: modelOverride === undefined ? rexModel : modelOverride,
                    displayText: options?.displayText,
                  }) : undefined}
                  welcomeContent={(
                    hasRexGuideActions ? (
                      <GuidedCreatePanel
                        emptyTitle={rexGuideEmptyTitle ?? t('entity.rexReady')}
                        icon={rexGuideIcon ?? <MessageSquare className="h-5 w-5" />}
                        title={rexGuidePanelTitle ?? t('entity.rexAssist')}
                        description={rexGuidePanelDesc ?? t('entity.rexReady')}
                        groups={rexWelcomeGuideGroups}
                        onStartPrompt={startRexGuidePrompt}
                      />
                    ) : (
                      <div className="text-center max-w-md">
                        <MessageSquare className="w-10 h-10 text-red-500 mx-auto mb-3" />
                        <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('entity.rexAssist')}</h3>
                        <p className="text-sm text-gray-500">{t('entity.rexReady')}</p>
                      </div>
                    )
                  )}
                  conversationBottomSlot={({ sendPrompt, sending, streaming, hasMessages }) => (
                    hasRexGuideActions && hasMessages ? (
                      <ChatGuideDock
                        actions={rexGuideActions}
                        disabled={sending || streaming || extracting}
                        collapseTitle={rexGuideCollapseTitle ?? t('entity.guideCollapse')}
                        expandTitle={rexGuideExpandTitle ?? t('entity.guideExpand')}
                        onStartPrompt={(prompt, label) => {
                          if (prompt === EXTRACT_FROM_REX_GUIDE_PROMPT) {
                            void handleExtract();
                            return;
                          }

                          sendPrompt(prompt, {
                            displayText: buildInstructionDisplayText(label),
                          });
                        }}
                      />
                    ) : null
                  )}
                />
              )}
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        {(!showTabs || activeTab === 'form') && (
          <div className="flex-shrink-0 border-t border-gray-200 bg-white px-6 py-4">
            <div className="flex items-center justify-between gap-3">
              {/* Left: custom footer element and/or test button */}
              <div className="flex items-center gap-2">
                {footerLeft}
                {onRunTest && !hideTest && (
                  <button
                    onClick={() => setActiveTab('test')}
                    className="flex items-center gap-1.5 text-sm text-gray-500 border border-gray-300 rounded-lg px-3 py-1.5 hover:bg-gray-50 transition-colors"
                  >
                    <TestTube className="w-3.5 h-3.5" />
                    {t('entity.testButton')}
                  </button>
                )}
              </div>

              <SubmitButtons
                onClose={onClose}
                onSubmit={onSubmit}
                submitDisabled={submitDisabled}
                submitLoading={submitLoading}
                submitLabel={submitLabel ?? defaultSubmitLabel}
                cancelLabel={t('entity.cancelButton')}
              />
            </div>
          </div>
        )}

        {/* ── Rex tab footer: extract / switch actions only ── */}
        {showRexFooter && (
          <div className="flex-shrink-0 border-t border-gray-200 bg-white px-6 py-3">
            {extractError && (
              <p className="text-xs text-red-500 mb-2 flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {extractError}
              </p>
            )}
            <div className="flex items-center justify-between">
              <div>
                {!onExtractFromRex && activeRexSessionId && hasFormTab ? (
                  <button
                    onClick={() => setActiveTab('form')}
                    className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors"
                  >
                    <ArrowRight className="w-4 h-4" />
                    {t('entity.switchToForm')}
                  </button>
                ) : null}
              </div>

              {mode === 'create' && !hasRexGuideActions && (
                <SubmitButtons
                  onClose={onClose}
                  onSubmit={onSubmit}
                  submitDisabled={submitDisabled}
                  submitLoading={submitLoading}
                  submitLabel={submitLabel ?? defaultSubmitLabel}
                  cancelLabel={t('entity.cancelButton')}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </EntitySheetContext.Provider>
  );
}

// ─── Submit Buttons ───────────────────────────────────────────────────────────

function SubmitButtons({
  onClose,
  onSubmit,
  submitDisabled,
  submitLoading,
  submitLabel,
  cancelLabel,
}: {
  onClose: () => void;
  onSubmit: () => void | Promise<void>;
  submitDisabled?: boolean;
  submitLoading?: boolean;
  submitLabel: string;
  cancelLabel: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={onClose}
        disabled={submitLoading}
        className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50 transition-colors"
      >
        {cancelLabel}
      </button>
      <button
        onClick={onSubmit}
        disabled={submitDisabled || submitLoading}
        className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 transition-colors"
      >
        {submitLoading && <Loader2 className="w-4 h-4 animate-spin" />}
        {submitLabel}
      </button>
    </div>
  );
}

// ─── Tab Button ───────────────────────────────────────────────────────────────

function SheetTab({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex min-w-0 flex-1 items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-red-600 text-red-600'
          : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
      }`}
    >
      <span className="shrink-0">{icon}</span>
      <span className="truncate">{children}</span>
    </button>
  );
}
