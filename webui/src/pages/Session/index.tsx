import { useContext, useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef } from 'react';
import {
  Plus,
  ChevronDown,
  ChevronRight,
  Sparkles,
  Shield,
  Search,
  AlertTriangle,
  Bot,
  Loader2,
  Workflow as WorkflowIcon,
  Cpu,
  Info,
  X,
  Check,
  FolderGit2,
  FolderPlus,
  BookOpen,
  Hammer,
  ClipboardList,
  Target,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useSearchParams, useParams } from 'react-router-dom';
import SessionComposerMenu, { SessionComposerMenuHeader, SessionModeOption } from './SessionComposerMenu';
import { sessionPath } from '@/utils/sessionUrl';
import { PaneActiveContext } from '@/components/layout/PaneActiveContext';
import CopyButton from '@/components/common/CopyButton';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat, { buildInstructionDisplayText, type ComposerPrefill, type PromptDisplayOptions, type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import SessionContextPanel from './SessionContextPanel';
import { getInitialSidePanelWidth, getMaxSidePanelWidth, SIDE_PANEL_MIN_WIDTH } from '@/components/common/sidePanelSizing';
import SuiteInstallProgressPanel, {
  applySuiteInstallProgressEvent,
  createSuiteInstallProgressState,
  failSuiteInstallProgress,
  type SuiteInstallProgressState,
} from '@/components/hub/SuiteInstallProgressPanel';
import { sessionApi, type SessionContextFile, type SessionContextSnapshot } from '@/api/session';
import ExtensionSlot from '@/components/extensions/ExtensionSlot';
import type { SessionStartHook } from '@/extensions/registry';
import { hubAPI, type HubInstallProgressEvent } from '@/api/hub';
import { currentReaIdentity, reaAcceptsSamples, useReaStatus } from '@/features/rea/useRea';
import { ReaShortcut } from '@/features/rea/ReaCards';
import {
  consumeReaEntryNavigation,
  reaEntryNavigationAction,
  type ReaEntryAction,
} from '@/features/rea/navigation';
import type { ReaStatus } from '@/api/rea';
import { writeChatDraft } from '@/utils/chatDraft';
import { skillAPI, type Skill } from '@/api/skill';
import { workflowAPI, type WorkflowSummary } from '@/api/workflow';
import type { Agent } from '@/api/agent';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import {
  useEnabledChatModelDefinitions,
  useResolvedDefaultModel,
} from '@/hooks/useChatModelResources';
import client from '@/api/client';
import ProjectFolderDialog from '@/components/layout/ProjectFolderDialog';
import { announceProjectsChanged, onProjectsChanged } from '@/utils/projectEvents';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type FilePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';
import { formatPricingPerMillion, isPricingFree } from '@/utils/modelPricing';
import type { Message, ModelDefinitionV2, Session } from '@/types';
import { createMessageId } from '@/utils/messageId';
import { extractErrorMessage } from '@/utils/error';
import { useAuth } from '@/contexts/AuthContext';
import {
  DEFAULT_SESSION_EXECUTION_MODE,
  promoteDraftExecutionMode,
  readSessionExecutionMode,
  resetDraftExecutionMode,
  writeSessionExecutionMode,
  type PersistentSessionExecutionMode,
  type SessionExecutionMode,
} from '@/utils/sessionExecutionMode';

const LAST_SELECTED_SESSION_STORAGE_KEY = 'flocks:last-selected-session';
const SESSION_PAGE_VISITED_STORAGE_KEY = 'flocks:sessions:visited';
const SOC_WORKSPACE_COMPONENT_ID = 'soc-workspace';
const INSTALLED_HUB_STATES = new Set(['installed', 'localOnly', 'updateAvailable']);
const SESSION_UPDATE_REFETCH_DEBOUNCE_MS = 500;
const AUTO_MODEL_KEY = '__flocks_auto__';
const TASK_SESSION_GROUP_ID = 'tasks';
const SESSION_EXECUTION_MODES: SessionExecutionMode[] = ['build', 'plan', 'goal'];
type AgentSourceFilter = 'all' | 'builtin' | 'custom';

function ExecutionModeIcon({
  mode,
  className = 'h-3 w-3',
}: {
  mode: SessionExecutionMode;
  className?: string;
}) {
  if (mode === 'plan') return <ClipboardList className={className} />;
  if (mode === 'goal') return <Target className={className} />;
  return <Hammer className={className} />;
}
type ProjectSummary = {
  id: string;
  worktree: string;
  vcs?: string | null;
  name?: string | null;
  isDefault?: boolean;
  pathStatus?: 'available' | 'missing' | 'unreadable';
  sessionCount?: number;
  matchedSessionCount?: number;
  lastActivityAt?: number | null;
  ownerUserID?: string | null;
  canWrite?: boolean;
  canDelete?: boolean;
  isShared?: boolean;
};
type ProjectSessionGroup = {
  id: string;
  label: string;
  worktree: string;
  sessions: Session[];
  sessionCount: number;
  pathStatus: 'available' | 'missing' | 'unreadable';
  canWrite: boolean;
  canDelete: boolean;
  isShared: boolean;
};

type ComposerResourceItem = {
  id: string;
  name: string;
  description?: string;
  badge?: string;
  source: 'builtin' | 'custom';
};

function ComposerResourcePicker({
  label,
  title,
  emptyText,
  icon: Icon,
  items,
  loading,
  open,
  onToggle,
  onSelect,
}: {
  label: string;
  title: string;
  emptyText: string;
  icon: React.ComponentType<{ className?: string }>;
  items: ComposerResourceItem[];
  loading: boolean;
  open: boolean;
  onToggle: () => void;
  onSelect: (item: ComposerResourceItem) => void;
}) {
  const { t } = useTranslation('session');
  const [sourceFilter, setSourceFilter] = useState<AgentSourceFilter>('all');
  const filteredItems = items.filter((item) => (
    sourceFilter === 'all' || item.source === sourceFilter
  ));

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        className="group flex h-10 w-full items-center gap-2.5 rounded-[9px] px-2 text-left text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100/90 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-white/[0.07] dark:hover:text-white"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="grid h-7 w-7 shrink-0 place-items-center text-zinc-500 transition-colors group-hover:text-zinc-800 dark:text-zinc-400 dark:group-hover:text-white">
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform ${open ? '-rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="absolute bottom-0 left-[calc(100%+0.5rem)] z-[60] w-[300px] max-w-[calc(100vw-2rem)] overflow-hidden max-md:left-0 rounded-[14px] border border-black/[0.09] bg-white/95 shadow-[0_18px_46px_rgba(22,27,34,0.14),0_2px_8px_rgba(22,27,34,0.06)] backdrop-blur-xl dark:border-white/[0.11] dark:bg-[#252c35]/95 dark:shadow-[0_20px_48px_rgba(8,10,13,0.44)]">
          <div className="flex min-h-12 items-center justify-between gap-3 border-b border-zinc-100 px-3 py-2 dark:border-white/[0.08]">
            <span className="min-w-0 truncate text-[12px] font-semibold text-zinc-800 dark:text-zinc-100">
              {title}
            </span>
            <div className="inline-flex shrink-0 items-center rounded-lg bg-zinc-100 p-0.5 text-[10px] dark:bg-black/15">
              {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setSourceFilter(filter)}
                  className={`rounded-md px-2 py-1 transition-colors ${
                    sourceFilter === filter
                      ? 'bg-white text-zinc-900 shadow-[0_1px_3px_rgba(22,27,34,0.08)] dark:bg-white/[0.12] dark:text-zinc-50'
                      : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100'
                  }`}
                >
                  {t(`agentPicker.filter.${filter}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="h-64 space-y-0.5 overflow-y-auto p-1.5">
            {loading ? (
              <div className="flex h-full items-center justify-center text-zinc-400">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
            ) : filteredItems.length > 0 ? (
              filteredItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelect(item)}
                  title={item.description}
                  className="flex w-full min-w-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-300 dark:hover:bg-white/[0.06] dark:hover:text-white"
                >
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-zinc-100 text-zinc-400 dark:bg-white/[0.05] dark:text-zinc-500">
                    <Icon className="h-3 w-3" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs font-medium">{item.name}</span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium ${
                    item.source === 'builtin'
                      ? 'bg-zinc-100 text-zinc-600 dark:bg-white/[0.08] dark:text-zinc-300'
                      : 'bg-teal-50 text-teal-600 dark:bg-teal-950/40 dark:text-teal-300'
                  }`}>
                    {t(`agentPicker.badge.${item.source}`)}
                  </span>
                  {item.badge && (
                    <span className="max-w-20 shrink-0 truncate rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] font-medium text-zinc-500 dark:bg-white/[0.07] dark:text-zinc-400">
                      {item.badge}
                    </span>
                  )}
                </button>
              ))
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-center text-xs text-zinc-400">
                {emptyText}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
const MULTI_PROJECT_SESSION_PAGE_SIZE = 6;
const SINGLE_PROJECT_SESSION_PAGE_SIZE = 20;
type ChatModelOption = {
  key: string;
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
  pricingLabel: string;
  contextLabel: string;
  contextWindowTokens: number | null;
  supportsVision: boolean | null;
};
type ChatModelProviderGroup = {
  providerID: string;
  providerName: string;
  models: ChatModelOption[];
};
type SelectorTooltip = {
  title: string;
  lines: string[];
  x: number;
  y: number;
};

function readLastSelectedSessionId(): string | null {
  try {
    return window.localStorage.getItem(LAST_SELECTED_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeLastSelectedSessionId(sessionId: string | null) {
  try {
    if (sessionId) {
      window.localStorage.setItem(LAST_SELECTED_SESSION_STORAGE_KEY, sessionId);
    } else {
      window.localStorage.removeItem(LAST_SELECTED_SESSION_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures so the main chat flow is never blocked.
  }
}

function hasVisitedSessionPage(): boolean {
  try {
    return window.sessionStorage.getItem(SESSION_PAGE_VISITED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function markSessionPageVisited() {
  try {
    window.sessionStorage.setItem(SESSION_PAGE_VISITED_STORAGE_KEY, 'true');
  } catch {
    // Ignore storage failures; this only controls best-effort restore behavior.
  }
}

function shouldSkipLastSelectedSessionRestore(state: unknown): boolean {
  return Boolean(
    state
      && typeof state === 'object'
      && 'skipLastSelectedSessionRestore' in state
      && (state as { skipLastSelectedSessionRestore?: unknown }).skipLastSelectedSessionRestore,
  );
}

function makeModelKey(providerID: string, modelID: string): string {
  return `${providerID}::${modelID}`;
}

function getPathBasename(path: string): string {
  const normalized = path.replace(/[\\/]+$/g, '');
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path || 'Project';
}

function getProjectLabel(project?: ProjectSummary, fallbackDirectory?: string): string {
  const explicitName = project?.name?.trim();
  if (explicitName) return explicitName;
  const directory = project?.worktree || fallbackDirectory || '';
  return getPathBasename(directory);
}

/**
 * Live-connection indicator for the chat header. Only the two states a user
 * can act on are shown: reconnecting (transient) and failed (retries exhausted,
 * reload to resume). ``connected`` renders nothing.
 */
function SSEStatusIndicator({ status, t }: { status: SSEConnectionStatus; t: (key: string) => string }) {
  if (status !== 'reconnecting' && status !== 'failed') return null;
  const failed = status === 'failed';
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="sse-status"
      data-sse-status={status}
      className={`flex min-w-0 items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium ${
        failed
          ? 'border-red-200 bg-red-50 text-red-600'
          : 'border-amber-200 bg-amber-50 text-amber-700'
      }`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${failed ? 'bg-red-500' : 'animate-pulse bg-amber-500'}`} />
      <span className="truncate">{failed ? t('connectionFailed') : t('reconnecting')}</span>
      {failed && (
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded px-1.5 py-0.5 text-xs font-medium underline-offset-2 hover:underline"
        >
          {t('chat.retry')}
        </button>
      )}
    </div>
  );
}

function WorkbenchRefreshStatus({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="workbench-refresh-status"
      className="ml-auto flex min-w-0 items-center gap-2 rounded-full border border-black/[0.06] bg-white/55 px-2.5 py-1 text-[11px] font-medium text-[#737980] shadow-[0_1px_2px_rgba(22,27,34,0.03)] dark:border-white/[0.08] dark:bg-white/[0.04] dark:text-[#aeb8c3]"
    >
      <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[#4f92e8]" />
      <span className="truncate">{label}</span>
      <span className="h-px w-8 shrink-0 overflow-hidden rounded-full bg-[#d9e5f4] dark:bg-[#46515e]">
        <span className="block h-full w-1/2 animate-pulse rounded-full bg-[#4f92e8]" />
      </span>
    </div>
  );
}

function SessionChatSkeleton() {
  return (
    <div
      data-testid="session-chat-skeleton"
      aria-hidden="true"
      className="flex min-h-0 flex-1 justify-center overflow-hidden px-7 pt-16"
    >
      <LoadingSpinner
        size="sm"
        delayMs={180}
        className="opacity-60 [&_svg]:text-zinc-400 dark:[&_svg]:text-zinc-500"
      />
    </div>
  );
}

function mergeSessionContext(
  current: SessionContextSnapshot | null,
  incoming: SessionContextSnapshot,
  older: boolean,
): SessionContextSnapshot {
  const newest = older && current ? current : incoming;
  const previous = older ? incoming : current;
  const incomingIDs = new Set(incoming.messageIDs);
  const incomingFiles = [...incoming.outputs, ...incoming.contextFiles];
  // A fetched page replaces descriptors for its messages, including deletions.
  // A complete head is authoritative for the whole Session, even when empty.
  const retainedFiles = current && (older || incoming.hasMore)
    ? [...current.outputs, ...current.contextFiles].filter((file) => !incomingIDs.has(file.sourceMessageID))
    : [];
  const filesByKey = new Map<string, SessionContextFile>();
  for (const file of (older ? [...retainedFiles, ...incomingFiles] : [...incomingFiles, ...retainedFiles])) {
    const key = file.fileKey || file.resourceID;
    const existing = filesByKey.get(key);
    // A page filling a gap can be newer than retained historical descriptors.
    if (!existing || (
      typeof file.createdAt === 'number' && typeof existing.createdAt === 'number'
      && file.createdAt > existing.createdAt
    )) filesByKey.set(key, file);
  }
  const files = [...filesByKey.values()];
  const outputs = files.filter((file) => file.section === 'outputs');
  const contextFiles = files.filter((file) => file.section === 'context');
  const progressSource = (newest.progressKnown ?? newest.progress.length > 0) ? newest : previous;
  const progress = progressSource?.progress ?? [];
  const progressKnown = progressSource?.progressKnown ?? progress.length > 0;
  const progressCount = Number(progress.length > 0);
  const skills = [...new Map([
    ...(previous?.skills ?? []).map((skill) => [skill.name, skill] as const),
    ...newest.skills.map((skill) => [skill.name, skill] as const),
  ]).values()];
  // Retain the old boundary only for an overlapping partial head; otherwise
  // use the fetched page's boundary to fill gaps or finish a complete refresh.
  const overlapsHead = current?.messageIDs?.some((id) => incomingIDs.has(id));
  const pagination = older || !current || !incoming.hasMore || !overlapsHead ? incoming : current;
  return {
    ...newest,
    outputs,
    contextFiles,
    progress,
    progressKnown,
    skills,
    hasMore: pagination.hasMore,
    nextBefore: pagination.nextBefore ?? null,
    counts: {
      total: outputs.length + contextFiles.length + newest.roots.length + progressCount,
      outputs: outputs.length,
      contextFiles: contextFiles.length,
      roots: newest.roots.length,
      progress: progressCount,
    },
  };
}

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
  const { user } = useAuth();
  const activeProjectStorageKey = `flocks:sessions:active-project:${user?.id ?? 'anonymous'}`;
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { sessionId: routeSessionId } = useParams();
  const selectedSessionId = routeSessionId || searchParams.get('session') || null;
  const navigationRef = useRef({ key: location.key, sessionId: selectedSessionId });
  navigationRef.current = { key: location.key, sessionId: selectedSessionId };
  const mountedRef = useRef(false);
  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      // Once this page leaves the route, its pending mutations may finish,
      // but must not navigate or overwrite the next page's selection.
      mountedRef.current = false;
    };
  }, []);
  // Inside the layout this page lives in a keep-alive pane: switching tabs
  // hides it instead of unmounting it, and its location stays pinned, so the
  // unmount / location-key guards above never fire. A hidden pane must not
  // navigate either, or a finishing create/archive would drag the whole app
  // back to the session it belongs to.
  const paneActive = useContext(PaneActiveContext);
  const paneActiveRef = useRef(paneActive);
  paneActiveRef.current = paneActive;
  const restoreAttemptRef = useRef<string | null>(null);
  const legacyNavigationRef = useRef<string | null>(null);
  const previousActionSessionRef = useRef(selectedSessionId);
  const [sessionLoadError, setSessionLoadError] = useState<{ sessionId: string; kind: 'unavailable' | 'forbidden' | 'failed' } | null>(null);
  const [sessionLoadAttempt, setSessionLoadAttempt] = useState(0);
  const [pendingInitialSessionId, setPendingInitialSessionId] = useState<string | null>(null);
  const selectSession = useCallback((id: string | null, replace = false) => {
    if (!mountedRef.current || !paneActiveRef.current) return;
    if (!id) writeLastSelectedSessionId(null);
    if (id && navigationRef.current.sessionId === id) return;
    navigate(id ? sessionPath(id) : '/sessions', {
      replace,
      state: id ? null : { skipLastSelectedSessionRestore: true },
    });
  }, [navigate]);
  const legacyNavigationPending = Boolean(selectedSessionId) && (searchParams.has('session') || searchParams.has('message') || searchParams.has('display'));
  const [pendingFocusMessageId, setPendingFocusMessageId] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState('rex');
  const [showAgentOptions, setShowAgentOptions] = useState(false);
  const [showWorkflowOptions, setShowWorkflowOptions] = useState(false);
  const [showSkillOptions, setShowSkillOptions] = useState(false);
  const [composerWorkflows, setComposerWorkflows] = useState<WorkflowSummary[]>([]);
  const [composerSkills, setComposerSkills] = useState<Skill[]>([]);
  const [loadingComposerResources, setLoadingComposerResources] = useState(false);
  const [selectedExecutionMode, setSelectedExecutionMode] = useState<SessionExecutionMode>(
    () => readSessionExecutionMode(null),
  );
  const [showExecutionModeOptions, setShowExecutionModeOptions] = useState(false);
  const executionModeSelectorRef = useRef<HTMLDivElement>(null);
  const executionModeHandoffRef = useRef<{
    sessionId: string;
    mode: SessionExecutionMode;
  } | null>(null);
  const [showProjectOptions, setShowProjectOptions] = useState(false);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [showModelOptions, setShowModelOptions] = useState(false);
  const modelSelectorRef = useRef<HTMLDivElement>(null);
  // Prepared when a first message is sent and run on its new session before
  // the prompt; extensions register them through the session.settings slot.
  const sessionStartHooksRef = useRef(new Set<SessionStartHook>());
  const registerSessionStartHook = useCallback((hook: SessionStartHook) => {
    sessionStartHooksRef.current.add(hook);
    return () => { sessionStartHooksRef.current.delete(hook); };
  }, []);
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [installingSocWorkspace, setInstallingSocWorkspace] = useState(false);
  const [startingRea, setStartingRea] = useState(false);
  const startingReaRef = useRef(false);
  // Watched: the shortcut badge and the upload rule follow installs and uninstalls done elsewhere.
  const { status: reaStatus, refresh: refreshReaStatus } = useReaStatus({ watch: true });
  // The guide the REA entry put into the message box of the conversation it was clicked in (null: a
  // new conversation); filled in, never sent.
  const [reaComposerPrefill, setReaComposerPrefill] = useState<(ComposerPrefill & { sessionId: string | null }) | null>(null);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [pendingInitialDisplayText, setPendingInitialDisplayText] = useState<string | null>(null);
  const [pendingOptimisticMessage, setPendingOptimisticMessage] = useState<Message | null>(null);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [contextPanelWidth, setContextPanelWidth] = useState(() => getInitialSidePanelWidth());
  const [contextSnapshot, setContextSnapshot] = useState<SessionContextSnapshot | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextLoadingMore, setContextLoadingMore] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextResetVersion, setContextResetVersion] = useState(0);
  const contextScope = useMemo(() => ({
    sessionId: selectedSessionId,
    open: contextPanelOpen,
    resetVersion: contextResetVersion,
    active: false,
    skillSignatures: new Map<string, string>(),
  }), [selectedSessionId, contextPanelOpen, contextResetVersion]);
  const contextScopeRef = useRef(contextScope);
  contextScopeRef.current = contextScope;
  const contextSnapshotRef = useRef<SessionContextSnapshot | null>(null);
  const contextFlightRef = useRef<{
    scope: typeof contextScope;
    controller: AbortController;
    dirty: boolean;
    promise: Promise<void>;
  } | null>(null);
  const [requestedContextResource, setRequestedContextResource] = useState<{ sessionId: string | null; resourceID: string } | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [refreshingProjects, setRefreshingProjects] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const supportsVision = useDefaultModelVision();
  const [searchQuery] = useState('');
  const [agentSourceFilter, setAgentSourceFilter] = useState<AgentSourceFilter>('all');
  const [selectedSessionFallback, setSelectedSessionFallback] = useState<Session | null>(null);
  const [selectorTooltip, setSelectorTooltip] = useState<SelectorTooltip | null>(null);
  const sessionUpdateRefetchTimerRef = useRef<number | null>(null);
  const contextRefetchTimerRef = useRef<number | null>(null);
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;
  const previousSseStatusRef = useRef<SSEConnectionStatus | null>(null);
  const projectListRequestSeqRef = useRef(0);
  const composerResourcesLoadedRef = useRef(false);
  const toast = useToast();

  const sessionProjectIds = useMemo(
    () => [TASK_SESSION_GROUP_ID, ...projects.map((project) => project.id)],
    [projects],
  );
  const sessionListPageSize = projects.length >= 1
    ? MULTI_PROJECT_SESSION_PAGE_SIZE
    : SINGLE_PROJECT_SESSION_PAGE_SIZE;
  const {
    sessions,
    loading: loadingSessions,
    refreshing: refreshingSessions = loadingSessions,
    refetch: refetchSessions,
    updateSessionTitle,
    addSession,
  } = useSessions(searchQuery, {
    projectIds: sessionProjectIds,
    pageSize: sessionListPageSize,
  });
  const { agents, loading: loadingAgents } = useAgents();
  const { providers, loading: loadingProviders } = useProviders();
  const {
    data: enabledModelDefinitions,
    loading: loadingEnabledModels,
  } = useEnabledChatModelDefinitions();
  const primaryAgents = useMemo(() => agents.filter((a) => a.mode === 'primary' && isAgentUsableInChat(a)), [agents]);
  const subAgents = useMemo(
    () => agents.filter((a) => a.mode !== 'primary' && isAgentUsableInChat(a)),
    [agents],
  );
  const chatAgents = useMemo(() => [...primaryAgents, ...subAgents], [primaryAgents, subAgents]);
  const filteredChatAgents = useMemo(
    () => chatAgents.filter((agent) => {
      if (agent.name.toLowerCase() === 'rex') return false;
      if (agentSourceFilter === 'builtin') return agent.native;
      if (agentSourceFilter === 'custom') return !agent.native;
      return true;
    }),
    [chatAgents, agentSourceFilter],
  );
  const loadComposerResources = useCallback(async () => {
    if (composerResourcesLoadedRef.current || loadingComposerResources) return;
    setLoadingComposerResources(true);
    try {
      const [workflowResult, skillResult] = await Promise.allSettled([
        workflowAPI.listSummaries({ status: 'active' }),
        skillAPI.status(),
      ]);
      if (workflowResult.status === 'fulfilled') {
        setComposerWorkflows(
          workflowResult.value.data
            .filter((workflow) => workflow.status === 'active')
            .sort((a, b) => getWorkflowDisplayName(a, i18n.language)
              .localeCompare(getWorkflowDisplayName(b, i18n.language))),
        );
      }
      if (skillResult.status === 'fulfilled') {
        setComposerSkills(
          skillResult.value.data
            .filter((skill) => (
              !skill.ui_hidden
              && !skill.disabled
              && skill.eligible !== false
              && skill.category !== 'system'
            ))
            .sort((a, b) => a.name.localeCompare(b.name)),
        );
      }
      composerResourcesLoadedRef.current = true;
    } finally {
      setLoadingComposerResources(false);
    }
  }, [i18n.language, loadingComposerResources]);
  const chatModelOptions = useMemo<ChatModelOption[]>(() => {
    const providerById = new Map(
      providers
        .filter((provider) => provider.configured)
        .map((provider) => [provider.id, provider]),
    );

    const formatPricing = (pricing: ModelDefinitionV2['pricing']): string => {
      if (!pricing) return t('modelPicker.noCost');
      if (isPricingFree(pricing)) return t('modelPicker.free');
      return formatPricingPerMillion(pricing);
    };

    const formatContextWindow = (contextWindow?: number): string => {
      if (!contextWindow) return t('modelPicker.contextUnknown');
      const value = contextWindow >= 1000000
        ? `${(contextWindow / 1000000).toFixed(0)}M`
        : `${(contextWindow / 1000).toFixed(0)}K`;
      return t('modelPicker.contextWindow', { value });
    };

    return enabledModelDefinitions.flatMap((model) => {
      if (model.model_type !== 'llm') return [];
      const provider = providerById.get(model.provider_id);
      if (!provider) return [];
      return [{
        key: makeModelKey(provider.id, model.id),
        providerID: provider.id,
        providerName: provider.name || provider.id,
        modelID: model.id,
        label: model.name || model.id,
        pricingLabel: formatPricing(model.pricing),
        contextLabel: formatContextWindow(model.limits?.context_window),
        contextWindowTokens: model.limits?.context_window ?? null,
        supportsVision: typeof model.capabilities?.supports_vision === 'boolean'
          ? model.capabilities.supports_vision
          : null,
      }];
    });
  }, [enabledModelDefinitions, providers, t]);
  const groupedChatModelOptions = useMemo<ChatModelProviderGroup[]>(() => {
    const groups = new Map<string, ChatModelProviderGroup>();

    providers.forEach((provider) => {
      if (!provider.configured) return;
      groups.set(provider.id, {
        providerID: provider.id,
        providerName: provider.name || provider.id,
        models: [],
      });
    });

    chatModelOptions.forEach((option) => {
      const group = groups.get(option.providerID);
      if (group) group.models.push(option);
    });

    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        models: [...group.models].sort((a, b) => a.label.localeCompare(b.label)),
      }))
      .filter((group) => group.models.length > 0)
      .sort((a, b) => a.providerName.localeCompare(b.providerName));
  }, [chatModelOptions, providers]);
  const listedSelectedSession = useMemo(
    () => sessions.find(s => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );
  const selectedSession = listedSelectedSession
    ?? (selectedSessionFallback?.id === selectedSessionId ? selectedSessionFallback : null);
  const activeSessionError = sessionLoadError?.sessionId === selectedSessionId ? sessionLoadError : null;
  const activeChatSessionId = selectedSession && !activeSessionError ? selectedSessionId : null;
  const resolvingSelectedSession = legacyNavigationPending || Boolean(selectedSessionId && !selectedSession && !activeSessionError);
  const pinnedModelKey = selectedSession?.model_pinned && selectedSession.provider && selectedSession.model
    ? makeModelKey(selectedSession.provider, selectedSession.model)
    : null;
  const hasPinnedModelOption = !!pinnedModelKey && chatModelOptions.some((option) => option.key === pinnedModelKey);
  const selectedModelAuto = selectedModelKey === AUTO_MODEL_KEY;
  const selectedModelOption = useMemo(
    () => chatModelOptions.find((option) => option.key === selectedModelKey) ?? (selectedModelKey ? null : chatModelOptions[0] ?? null),
    [chatModelOptions, selectedModelKey],
  );
  const {
    data: resolvedDefaultModel,
    initialized: resolvedDefaultModelInitialized,
  } = useResolvedDefaultModel(chatModelOptions.length > 0);
  const primaryModelOption = useMemo(() => {
    if (!resolvedDefaultModel) return null;
    const key = makeModelKey(resolvedDefaultModel.providerID, resolvedDefaultModel.modelID);
    return chatModelOptions.find((option) => option.key === key) ?? null;
  }, [chatModelOptions, resolvedDefaultModel]);
  const autoSelectionAllowed = !selectedSessionId || Boolean(
    selectedSession && ['user', 'entity-config', 'workflow'].includes(selectedSession.category ?? 'user'),
  );
  const canSelectAuto = Boolean(
    autoSelectionAllowed && primaryModelOption,
  );
  const effectiveModelOption = selectedModelAuto ? primaryModelOption : selectedModelOption;
  const selectedPromptModel = selectedModelAuto
    ? null
    : selectedModelOption
      ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
      : null;
  const effectiveSupportsVision = effectiveModelOption?.supportsVision ?? supportsVision;
  const autoStatusLabel = !autoSelectionAllowed
    ? t('modelPicker.autoUserSessionsOnly')
    : primaryModelOption
      ? t('modelPicker.autoHint')
      : t('modelPicker.autoUnavailable');
  const firstChatModelKey = chatModelOptions[0]?.key ?? null;
  const resolvedDefaultKey = resolvedDefaultModel
    ? makeModelKey(resolvedDefaultModel.providerID, resolvedDefaultModel.modelID)
    : null;
  const defaultSelectionKey = resolvedDefaultKey
    && chatModelOptions.some(option => option.key === resolvedDefaultKey)
    ? resolvedDefaultKey
    : firstChatModelKey;

  const projectSessionGroups = useMemo<ProjectSessionGroup[]>(() => {
    const registeredIds = new Set(projects.map((project) => project.id));
    const sessionsByProject = new Map<string, Session[]>();
    sessions.forEach((session) => {
      const responseProjectId = session.effectiveProjectID || session.projectID;
      if (!registeredIds.has(responseProjectId)) return;
      const groupSessions = sessionsByProject.get(responseProjectId) ?? [];
      groupSessions.push(session);
      sessionsByProject.set(responseProjectId, groupSessions);
    });

    return projects.map((project) => {
      const projectSessions = sessionsByProject.get(project.id) ?? [];
      return {
        id: project.id,
        label: getProjectLabel(project),
        worktree: project.worktree,
        sessions: projectSessions,
        sessionCount: searchQuery.trim()
          ? project.matchedSessionCount ?? projectSessions.length
          : project.sessionCount ?? projectSessions.length,
        pathStatus: project.pathStatus ?? 'available',
        canWrite: project.canWrite ?? true,
        canDelete: project.canDelete ?? true,
        isShared: project.isShared ?? false,
      };
    })
      .filter((group) => {
        if (!searchQuery.trim()) return true;
        const project = projects.find((item) => item.id === group.id);
        return (project?.matchedSessionCount ?? group.sessions.length) > 0 || group.id === selectedProjectId;
      })
      .sort((a, b) => {
        const aLatest = projects.find((project) => project.id === a.id)?.lastActivityAt ?? a.sessions[0]?.time?.updated ?? 0;
        const bLatest = projects.find((project) => project.id === b.id)?.lastActivityAt ?? b.sessions[0]?.time?.updated ?? 0;
        if (aLatest !== bLatest) return bLatest - aLatest;
        return a.label.localeCompare(b.label);
      });
  }, [projects, searchQuery, selectedProjectId, sessions]);

  const selectedProjectIDForCreate = selectedProjectId && selectedProjectId !== TASK_SESSION_GROUP_ID
    ? selectedProjectId
    : null;
  const selectedProjectContextLabel = selectedProjectId === TASK_SESSION_GROUP_ID
    ? t('projectPicker.none')
    : projectSessionGroups.find((group) => group.id === selectedProjectId)?.label
      ?? t('projectPicker.none');

  useEffect(() => {
    const selectableProjectIds = new Set([
      TASK_SESSION_GROUP_ID,
      ...projectSessionGroups.map((group) => group.id),
    ]);
    if (selectedProjectId && selectableProjectIds.has(selectedProjectId)) {
      return;
    }

    let storedProjectId: string | null = null;
    try {
      storedProjectId = window.localStorage.getItem(activeProjectStorageKey);
    } catch {
      storedProjectId = null;
    }
    const fallbackProjectId = storedProjectId && selectableProjectIds.has(storedProjectId)
      ? storedProjectId
      : TASK_SESSION_GROUP_ID;
    setSelectedProjectId(fallbackProjectId);
  }, [activeProjectStorageKey, projectSessionGroups, selectedProjectId]);

  useEffect(() => {
    if (
      !selectedProjectId
      || (selectedProjectId !== TASK_SESSION_GROUP_ID
        && !projects.some((project) => project.id === selectedProjectId))
    ) return;
    try {
      window.localStorage.setItem(activeProjectStorageKey, selectedProjectId);
    } catch {
      // Active project persistence must never block the session manager.
    }
  }, [activeProjectStorageKey, projects, selectedProjectId]);

  // Handle SSE events for session-level updates (title changes, etc.)
  const handleChatError = useCallback((msg: string) => {
    toast.error(t('chat.error', 'Error'), msg);
  }, [toast, t]);

  const fetchProjects = useCallback(async (ensureProject?: ProjectSummary, query = '') => {
    const requestSeq = ++projectListRequestSeqRef.current;
    setRefreshingProjects(true);
    try {
      const listResult = await client.get('/api/project', {
        params: { search: query.trim() || undefined },
      });
      if (requestSeq !== projectListRequestSeqRef.current) return;
      const nextProjects = Array.isArray(listResult.data)
        ? listResult.data.filter((project: ProjectSummary) => (
          project.id !== TASK_SESSION_GROUP_ID && !project.isDefault
        ))
        : [];
      setProjects((currentProjects) => {
        if (!ensureProject?.id || nextProjects.some((project) => project.id === ensureProject.id)) {
          return nextProjects;
        }
        const currentProject = currentProjects.find((project) => project.id === ensureProject.id);
        return [{ ...currentProject, ...ensureProject }, ...nextProjects];
      });
    } finally {
      if (requestSeq === projectListRequestSeqRef.current) {
        setRefreshingProjects(false);
      }
    }
  }, []);

  useEffect(() => onProjectsChanged('composer', () => {
    void fetchProjects(undefined, searchQuery);
  }), [fetchProjects, searchQuery]);

  const fetchSessionContext = useCallback((older = false): Promise<void> => {
    const { sessionId, open } = contextScope;
    // A stale callback must not invalidate another session, including A -> B -> A.
    if (contextScopeRef.current !== contextScope || !contextScope.active || !sessionId || !open) return Promise.resolve();
    if (!older && contextRefetchTimerRef.current !== null) {
      window.clearTimeout(contextRefetchTimerRef.current);
      contextRefetchTimerRef.current = null;
    }
    const existing = contextFlightRef.current;
    if (existing?.scope === contextScope) {
      if (!older) existing.dirty = true;
      return existing.promise;
    }
    const snapshot = contextSnapshotRef.current;
    let before = older ? snapshot?.nextBefore : undefined;
    if (older && (!snapshot?.hasMore || !before)) return Promise.resolve();
    const flight = {
      scope: contextScope,
      controller: new AbortController(),
      dirty: false,
      promise: Promise.resolve(),
    };
    const isCurrent = () => contextScopeRef.current === contextScope
      && contextFlightRef.current === flight && !flight.controller.signal.aborted;
    contextFlightRef.current = flight;
    setContextLoading(true);
    setContextLoadingMore(older);
    flight.promise = (async () => {
      try {
        do {
          flight.dirty = false;
          try {
            const page = await sessionApi.getContext(sessionId, before ? { before } : {}, flight.controller.signal);
            if (!isCurrent()) return;
            const merged = mergeSessionContext(contextSnapshotRef.current, page, Boolean(before));
            contextSnapshotRef.current = merged;
            setContextSnapshot(merged);
            setContextError(null);
          } catch (error) {
            if (!isCurrent()) return;
            setContextError(extractErrorMessage(error, 'Failed to load Session Context'));
          }
          // All refreshes received during this flight coalesce into one trailing head read.
          before = undefined;
          if (isCurrent()) setContextLoadingMore(false);
        } while (isCurrent() && flight.dirty);
      } finally {
        if (isCurrent()) {
          contextFlightRef.current = null;
          setContextLoading(false);
          setContextLoadingMore(false);
        }
      }
    })();
    return flight.promise;
  }, [contextScope]);

  const scheduleContextRefetch = useCallback(() => {
    if (contextScopeRef.current !== contextScope || !contextScope.active || !contextScope.open || !contextScope.sessionId) return;
    if (contextFlightRef.current?.scope === contextScope) {
      void fetchSessionContext();
      return;
    }
    if (contextRefetchTimerRef.current !== null) return;
    contextRefetchTimerRef.current = window.setTimeout(() => {
      contextRefetchTimerRef.current = null;
      void fetchSessionContext();
    }, 250);
  }, [contextScope, fetchSessionContext]);

  const scheduleSessionListRefetch = useCallback(() => {
    if (sessionUpdateRefetchTimerRef.current !== null) return;
    sessionUpdateRefetchTimerRef.current = window.setTimeout(() => {
      sessionUpdateRefetchTimerRef.current = null;
      void Promise.all([
        refetchSessions(),
        fetchProjects(undefined, searchQuery),
      ]);
    }, SESSION_UPDATE_REFETCH_DEBOUNCE_MS);
  }, [fetchProjects, refetchSessions, searchQuery]);

  useEffect(() => () => {
    if (sessionUpdateRefetchTimerRef.current !== null) {
      window.clearTimeout(sessionUpdateRefetchTimerRef.current);
      sessionUpdateRefetchTimerRef.current = null;
    }
    if (contextRefetchTimerRef.current !== null) {
      window.clearTimeout(contextRefetchTimerRef.current);
      contextRefetchTimerRef.current = null;
    }
  }, []);

  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    const eventSessionId = event.properties?.sessionID
      || event.properties?.part?.sessionID
      || event.properties?.info?.sessionID;
    if (
      event.type === 'session.cleared'
      && contextScopeRef.current === contextScope
      && contextScope.active
      && eventSessionId === contextScope.sessionId
    ) {
      // Clearing history invalidates every message-backed resource, even when
      // the panel is closed. Stop old reads before starting a fresh scope.
      contextScope.active = false;
      if (contextRefetchTimerRef.current !== null) {
        window.clearTimeout(contextRefetchTimerRef.current);
        contextRefetchTimerRef.current = null;
      }
      contextFlightRef.current?.controller.abort();
      contextFlightRef.current = null;
      contextScope.skillSignatures.clear();
      contextSnapshotRef.current = null;
      setContextSnapshot(null);
      setContextError(null);
      setRequestedContextResource(null);
      // Remount the panel to cancel previews/folder navigation and clear local
      // errors. The scope effect reloads retained roots if the panel is open.
      setContextResetVersion((version) => version + 1);
      return;
    }
    if (
      contextScopeRef.current === contextScope
      && contextScope.active
      && contextScope.open
      && eventSessionId === contextScope.sessionId
    ) {
      const updatedPart = event.properties?.part;
      let contextPartUpdated = event.type === 'message.part.updated' && (
        updatedPart?.type === 'file'
        || (
          updatedPart?.type === 'tool'
          && updatedPart?.tool === 'write'
          && (updatedPart?.state?.status === 'completed' || updatedPart?.state?.status === 'error')
        )
      );
      if (
        event.type === 'message.part.updated'
        && updatedPart?.type === 'tool'
        && (updatedPart.tool === 'skill_load' || updatedPart.tool === 'load_skill')
        && ['pending', 'running', 'completed', 'error'].includes(updatedPart.state?.status)
      ) {
        const { input, status, error } = updatedPart.state;
        const partID = updatedPart.id || event.properties?.partID;
        const key = JSON.stringify([updatedPart.messageID || event.properties?.messageID, partID]);
        const signature = JSON.stringify([input?.name || input?.skill || null, status, error ?? null]);
        // Only descriptor changes matter, not streamed output. Keep this cache
        // inside the open/session scope so closed, foreign, or stale events cannot seed it.
        if (!partID || contextScope.skillSignatures.get(key) !== signature) {
          if (partID) contextScope.skillSignatures.set(key, signature);
          contextPartUpdated = true;
        }
      }
      if (contextPartUpdated || event.type === 'todo.updated' || event.type === 'session.context.updated') {
        scheduleContextRefetch();
      }
    }
    if (
      event.type === 'session.execution_mode.changed'
      && event.properties?.sessionID === selectedSessionId
      && event.properties?.executionMode === 'build'
    ) {
      setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
      writeSessionExecutionMode(
        selectedSessionId,
        DEFAULT_SESSION_EXECUTION_MODE,
      );
      setShowExecutionModeOptions(false);
      return;
    }
    if (event.type === 'session.notice' && event.properties?.kind === 'directory-fallback') {
      toast.warning(
        t('projectDirectoryFallbackTitle'),
        t('projectDirectoryFallbackDescription', {
          path: event.properties.fallbackDirectory,
        }),
      );
      return;
    }
    if (event.type === 'session.updated' && event.properties?.id) {
      if (event.properties?.title) {
        // Instant local title update so the sidebar reflects the change immediately.
        updateSessionTitle(event.properties.id, event.properties.title);
      }
      // Session/title updates can arrive in bursts when several sessions or
      // background tasks finish together. Coalesce the full sidebar refresh so
      // those bursts don't turn into a request/re-render storm.
      scheduleSessionListRefetch();
    }
  }, [
    contextScope,
    scheduleContextRefetch,
    scheduleSessionListRefetch,
    selectedSessionId,
    t,
    toast,
    updateSessionTitle,
  ]);

  useEffect(() => {
    // Opening the panel must not consume the resource ID set by the message card.
    setRequestedContextResource(null);
    contextSnapshotRef.current = null;
    setContextSnapshot(null);
    setContextError(null);
  }, [selectedSessionId]);

  useEffect(() => {
    contextScope.active = true;
    setContextLoading(false);
    setContextLoadingMore(false);
    if (contextScope.open && contextScope.sessionId) void fetchSessionContext();
    return () => {
      contextScope.active = false;
      contextScope.skillSignatures.clear();
      if (contextRefetchTimerRef.current !== null) {
        window.clearTimeout(contextRefetchTimerRef.current);
        contextRefetchTimerRef.current = null;
      }
      const flight = contextFlightRef.current;
      if (flight?.scope === contextScope) {
        flight.controller.abort();
        contextFlightRef.current = null;
      }
    };
  }, [contextScope, fetchSessionContext]);

  useEffect(() => {
    const previous = previousSseStatusRef.current;
    previousSseStatusRef.current = sseStatus;
    if (
      contextPanelOpen
      && previous
      && previous !== 'connected'
      && sseStatus === 'connected'
      && selectedSessionId
    ) {
      void fetchSessionContext();
    }
  }, [contextPanelOpen, fetchSessionContext, selectedSessionId, sseStatus]);

  useEffect(() => {
    void fetchProjects(undefined, searchQuery);
  }, [fetchProjects, searchQuery]);

  // A pending action belongs only to its original session. Clear it before
  // consuming a new legacy navigation; canonicalization keeps the same ID.
  useEffect(() => {
    if (previousActionSessionRef.current === selectedSessionId) return;
    previousActionSessionRef.current = selectedSessionId;
    setPendingInitialMessage(null);
    setPendingInitialDisplayText(null);
    setPendingInitialSessionId(null);
  }, [selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId || !legacyNavigationPending || legacyNavigationRef.current === location.key) return;
    legacyNavigationRef.current = location.key;
    const legacyId = searchParams.get('session');
    const conflictingTarget = Boolean(routeSessionId && legacyId && routeSessionId !== legacyId);
    const message = searchParams.get('message');
    if (message && !conflictingTarget) {
      setPendingInitialSessionId(selectedSessionId);
      setPendingInitialMessage(message);
      const display = searchParams.get('display');
      setPendingInitialDisplayText(display ? buildInstructionDisplayText(display) : null);
    }
    if (!message || conflictingTarget) {
      setPendingInitialSessionId(null);
      setPendingInitialMessage(null);
      setPendingInitialDisplayText(null);
    }
    if (conflictingTarget) toast.error(t('linkTargetMismatch'));
    const next = new URLSearchParams(searchParams);
    next.delete('session');
    next.delete('message');
    next.delete('display');
    navigate({ pathname: sessionPath(selectedSessionId), search: next.toString(), hash: location.hash }, { replace: true });
  }, [legacyNavigationPending, location.key, location.hash, navigate, routeSessionId, searchParams, selectedSessionId, t, toast]);

  useEffect(() => {
    setPendingFocusMessageId(searchParams.get('focusMessage'));
  }, [selectedSessionId, searchParams]);

  useEffect(() => {
    // A hidden pane waits: the restore runs once the tab is back on screen.
    if (!paneActive || loadingSessions || restoreAttemptRef.current === location.key) return;
    restoreAttemptRef.current = location.key;
    const alreadyVisited = hasVisitedSessionPage();
    markSessionPageVisited();
    if (selectedSessionId || !alreadyVisited || shouldSkipLastSelectedSessionRestore(location.state)) return;
    const lastSelectedSessionId = readLastSelectedSessionId();
    if (lastSelectedSessionId) selectSession(lastSelectedSessionId, true);
  }, [loadingSessions, location.key, location.state, paneActive, selectedSessionId, selectSession]);

  useEffect(() => {
    if (!selectedSessionId || selectedSession?.id !== selectedSessionId) return;
    writeLastSelectedSessionId(selectedSessionId);
  }, [selectedSession?.id, selectedSessionId]);

  useEffect(() => {
    const handoff = executionModeHandoffRef.current;
    if (selectedSessionId && handoff?.sessionId === selectedSessionId) {
      executionModeHandoffRef.current = null;
      setSelectedExecutionMode(handoff.mode);
      return;
    }
    setSelectedExecutionMode(readSessionExecutionMode(selectedSessionId));
  }, [selectedSessionId]);

  useEffect(() => {
    // Do not reuse a list-external detail after leaving its route (it may
    // have been archived or its sharing permissions may have changed).
    setSelectedSessionFallback(current => current?.id === selectedSessionId ? current : null);
  }, [selectedSessionId]);

  useEffect(() => {
    setSessionLoadError(null);
    // Keep a newly created session cached while React commits navigation.
    // Clearing it on the old route can discard the creation handoff.
    if (!selectedSessionId) return;
    if (listedSelectedSession) {
      if (selectedSessionFallback?.id === selectedSessionId) setSelectedSessionFallback(null);
      return;
    }
    if (selectedSessionFallback?.id === selectedSessionId) return;
    // Direct links must not wait for a failed/slow sidebar request.
    let cancelled = false;
    sessionApi.get(selectedSessionId)
      .then((session) => {
        if (cancelled) return;
        if (session.id !== selectedSessionId) throw new Error('Unexpected session response');
        setSelectedSessionFallback(session as unknown as Session);
      })
      .catch((err: any) => {
        if (cancelled) return;
        const status = err?.response?.status ?? err?.status;
        setSelectedSessionFallback(null);
        setSessionLoadError({ sessionId: selectedSessionId, kind: status === 403 ? 'forbidden' : status === 404 ? 'unavailable' : 'failed' });
        setPendingInitialMessage(null);
        setPendingInitialDisplayText(null);
        if (status === 403 || status === 404) writeLastSelectedSessionId(null);
      });
    return () => { cancelled = true; };
  }, [listedSelectedSession, selectedSessionId, selectedSessionFallback?.id, sessionLoadAttempt]);

  // Close agent dropdown on outside click
  useEffect(() => {
    if (!showAgentOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-agent-selector]')) setShowAgentOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showAgentOptions]);

  useEffect(() => {
    if (!showExecutionModeOptions) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-execution-mode-selector]')) {
        setShowExecutionModeOptions(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowExecutionModeOptions(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [showExecutionModeOptions]);

  useEffect(() => {
    if (!showProjectOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-project-selector]')) setShowProjectOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showProjectOptions]);

  useEffect(() => {
    if (!showModelOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-model-selector]')) setShowModelOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showModelOptions]);

  const sessionSettingsSlotContext = useMemo(
    () => ({ sessionId: selectedSessionId, onSessionStart: registerSessionStartHook }),
    [registerSessionStartHook, selectedSessionId],
  );

  useEffect(() => {
    if (selectedSession?.model_auto) {
      setSelectedModelKey(AUTO_MODEL_KEY);
      return;
    }

    if (!firstChatModelKey) {
      setSelectedModelKey(null);
      return;
    }

    if (hasPinnedModelOption && pinnedModelKey) {
      setSelectedModelKey(pinnedModelKey);
      return;
    }

    setSelectedModelKey(null);
    if (!resolvedDefaultModelInitialized) return;
    setSelectedModelKey(defaultSelectionKey);
  }, [
    defaultSelectionKey,
    firstChatModelKey,
    hasPinnedModelOption,
    pinnedModelKey,
    resolvedDefaultModelInitialized,
    selectedSession?.model_auto,
    selectedSessionId,
  ]);

  useEffect(() => {
    if (loadingEnabledModels || chatModelOptions.length === 0 || !selectedModelKey || selectedModelAuto) return;
    if (chatModelOptions.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(chatModelOptions[0].key);
  }, [chatModelOptions, loadingEnabledModels, selectedModelAuto, selectedModelKey]);

  useEffect(() => {
    if (
      showAgentOptions
      || showExecutionModeOptions
      || showModelOptions
      || showProjectOptions
    ) return;
    setSelectorTooltip(null);
  }, [
    showAgentOptions,
    showExecutionModeOptions,
    showModelOptions,
    showProjectOptions,
  ]);

  const handleSelectExecutionMode = useCallback((mode: SessionExecutionMode) => {
    setSelectedExecutionMode(mode);
    setShowExecutionModeOptions(false);
    if (mode !== 'goal') {
      writeSessionExecutionMode(
        selectedSessionId,
        mode as PersistentSessionExecutionMode,
      );
    }
  }, [selectedSessionId]);

  const handleExecutionModeAccepted = useCallback((mode: SessionExecutionMode) => {
    if (mode !== 'goal') return;
    setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
    writeSessionExecutionMode(
      selectedSessionId,
      DEFAULT_SESSION_EXECUTION_MODE,
    );
  }, [selectedSessionId]);

  const handleOpenContextFile = useCallback((resourceId: string) => {
    setRequestedContextResource({ sessionId: selectedSessionIdRef.current, resourceID: resourceId });
    setContextPanelOpen(true);
  }, []);

  const handleResizeContextPanel = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (window.innerWidth < 1024) return;
    const startX = event.clientX;
    const startWidth = contextPanelWidth;
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const onMove = (moveEvent: PointerEvent) => {
      const next = startWidth + startX - moveEvent.clientX;
      setContextPanelWidth(Math.min(getMaxSidePanelWidth(), Math.max(SIDE_PANEL_MIN_WIDTH, next)));
    };
    const onEnd = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onEnd, { once: true });
  }, [contextPanelWidth]);

  const resetDraftState = useCallback(() => {
    setSelectedSessionFallback(null);
    setPendingInitialMessage(null);
    setPendingInitialDisplayText(null);
    setContextPanelOpen(false);
    setContextSnapshot(null);
    setRequestedContextResource(null);
    setSelectedAgent('rex');
    // Starting another blank draft keeps the session ID null, so the model
    // initialization effect will not run again. Restore the default explicitly.
    setSelectedModelKey(resolvedDefaultModelInitialized ? defaultSelectionKey : null);
    setSseStatus('disconnected');
    setShowAgentOptions(false);
    setShowModelOptions(false);
    setShowProjectOptions(false);
  }, [defaultSelectionKey, resolvedDefaultModelInitialized]);

  const handleStartNewSession = useCallback(() => {
    writeLastSelectedSessionId(null);
    selectSession(null);
    resetDraftState();
  }, [resetDraftState, selectSession]);

  // "新建会话" in the sidebar only navigates here; start the same blank draft
  // once per such navigation (the state flag marks an explicit new chat).
  const draftResetKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (selectedSessionId || !shouldSkipLastSelectedSessionRestore(location.state)) return;
    if (draftResetKeyRef.current === location.key) return;
    draftResetKeyRef.current = location.key;
    resetDraftState();
  }, [location.key, location.state, resetDraftState, selectedSessionId]);

  const handleSelectModel = useCallback(async (option: ChatModelOption) => {
    const previousModelKey = selectedModelKey;
    setSelectedModelKey(option.key);
    setShowModelOptions(false);
    if (!selectedSessionId) return;

    try {
      await sessionApi.update(selectedSessionId, {
        provider: option.providerID,
        model: option.modelID,
        model_pinned: true,
        model_auto: false,
      });
      refetchSessions();
    } catch (err: any) {
      setSelectedModelKey(previousModelKey);
      toast.error(t('chat.error', 'Error'), err.message);
    }
  }, [refetchSessions, selectedModelKey, selectedSessionId, toast, t]);

  const handleSelectAutoModel = useCallback(async () => {
    if (!canSelectAuto) return;
    const previousModelKey = selectedModelKey;
    setSelectedModelKey(AUTO_MODEL_KEY);
    setShowModelOptions(false);
    if (!selectedSessionId) return;

    try {
      await sessionApi.update(selectedSessionId, {
        model_auto: true,
        model_pinned: false,
      });
      refetchSessions();
    } catch (err: any) {
      setSelectedModelKey(previousModelKey);
      toast.error(t('chat.error', 'Error'), err.message);
    }
  }, [canSelectAuto, refetchSessions, selectedModelKey, selectedSessionId, toast, t]);

  const handleCreateAndSend = useCallback(async (
    text: string,
    fileParts?: FilePartData[],
    agentOverride?: string,
    modelOverride?: { providerID: string; modelID: string } | null,
    options?: PromptDisplayOptions,
    executionModeOverride?: SessionExecutionMode,
  ) => {
    const navigationKey = navigationRef.current.key;
    try {
      // What each extension needs done on the new session, fixed now: the
      // page may move on (and the extension go away) while it is created.
      const sessionStartWork = Array.from(sessionStartHooksRef.current)
        .map((prepare) => prepare())
        .filter((run): run is (sessionId: string) => Promise<void> => typeof run === 'function');
      const effectiveExecutionMode = executionModeOverride || selectedExecutionMode;
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(selectedProjectIDForCreate ? { projectID: selectedProjectIDForCreate } : {}),
        ...(selectedModelAuto ? { model_auto: true } : {}),
      });
      const newSessionId = response.data.id;
      const messageId = createMessageId();
      const effectiveAgent = agentOverride || selectedAgent || 'rex';
      const optimisticParts: Message['parts'] = [];
      if (text || options?.displayText) {
        optimisticParts.push({
          id: `temp-${messageId}-text`,
          type: 'text',
          text,
          ...(options?.displayText ? { metadata: { displayText: options.displayText } } : {}),
        });
      }
      fileParts?.forEach((file, index) => {
        optimisticParts.push({
          id: file.id || `temp-${messageId}-file-${index}`,
          type: 'file',
          url: file.url,
          mime: file.mime,
          filename: file.filename,
        });
      });

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, fileParts),
        messageID: messageId,
      };
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (!selectedModelAuto && modelOverride) payload.model = modelOverride;
      if (options?.displayText) payload.displayText = options.displayText;
      payload.executionMode = effectiveExecutionMode;

      // Work an extension needs done on a new session before its first
      // prompt (session.settings); a hook that throws stops the send.
      for (const run of sessionStartWork) {
        await run(newSessionId);
      }
      await client.post(`/api/session/${newSessionId}/prompt_async`, payload);

      addSession(response.data);
      void fetchProjects(undefined, searchQuery).catch(() => {});
      if (!mountedRef.current || navigationRef.current.key !== navigationKey) return;
      setSelectedSessionFallback(response.data);
      executionModeHandoffRef.current = {
        sessionId: newSessionId,
        mode: effectiveExecutionMode,
      };
      if (effectiveExecutionMode === 'goal') {
        writeSessionExecutionMode(
          newSessionId,
          DEFAULT_SESSION_EXECUTION_MODE,
        );
        resetDraftExecutionMode();
      } else {
        promoteDraftExecutionMode(
          newSessionId,
          effectiveExecutionMode as PersistentSessionExecutionMode,
        );
      }
      setSelectedModelKey(selectedModelAuto ? AUTO_MODEL_KEY : null);
      setPendingOptimisticMessage({
        id: messageId,
        sessionID: newSessionId,
        role: 'user',
        parts: optimisticParts.length > 0
          ? optimisticParts
          : [{ id: `temp-${messageId}-part`, type: 'text', text }],
        timestamp: Date.now(),
        agent: effectiveAgent,
      });
      selectSession(newSessionId);
      if (effectiveExecutionMode === 'goal') {
        setSelectedExecutionMode(DEFAULT_SESSION_EXECUTION_MODE);
        writeSessionExecutionMode(
          newSessionId,
          DEFAULT_SESSION_EXECUTION_MODE,
        );
      }
    } catch (err: any) {
      toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      throw err;
    }
  }, [
    selectSession,
    addSession,
    fetchProjects,
    searchQuery,
    selectedAgent,
    selectedExecutionMode,
    selectedModelAuto,
    selectedProjectIDForCreate,
    toast,
    t,
  ]);

  const handleSuiteInstallProgress = useCallback((progress: HubInstallProgressEvent) => {
    setSuiteInstallProgress(current => applySuiteInstallProgressEvent(current, progress));
  }, []);

  const handleAlertOperationsSetup = useCallback(async () => {
    if (installingSocWorkspace) return;

    setInstallingSocWorkspace(true);
    let startedComponentInstall = false;
    try {
      const { data } = await hubAPI.catalog({ type: 'component', q: SOC_WORKSPACE_COMPONENT_ID });
      const component = data.find((item) => item.id === SOC_WORKSPACE_COMPONENT_ID && item.type === 'component');

      if (!component) {
        toast.error(t('welcome.socComponentMissingTitle'), t('welcome.socComponentMissingDescription'));
        return;
      }

      if (!INSTALLED_HUB_STATES.has(component.state)) {
        const confirmed = window.confirm(t('welcome.socComponentInstallConfirm'));
        if (!confirmed) return;
        startedComponentInstall = true;
        setSuiteInstallProgress(createSuiteInstallProgressState(component));
        await hubAPI.installStream('component', SOC_WORKSPACE_COMPONENT_ID, handleSuiteInstallProgress);
      }

      await handleCreateAndSend(
        t('welcome.alertOperationsSuggestion'),
        [],
        undefined,
        selectedPromptModel,
        { displayText: buildInstructionDisplayText(t('welcome.alertOperations')) },
      );
    } catch (err: any) {
      const message = err?.message ?? t('welcome.socComponentInstallFailedDescription');
      if (startedComponentInstall) {
        setSuiteInstallProgress(current => failSuiteInstallProgress(current, {
          id: SOC_WORKSPACE_COMPONENT_ID,
          name: 'SOC Workspace Component',
          nameCn: 'SOC 工作区场景套件',
        }, message));
      }
      toast.error(
        t('welcome.socComponentInstallFailedTitle'),
        message,
      );
    } finally {
      setInstallingSocWorkspace(false);
    }
  }, [handleCreateAndSend, handleSuiteInstallProgress, installingSocWorkspace, selectedPromptModel, t, toast]);

  // The 「REA 逆向分析」 shortcut (and the plugin square's actions): route by the
  // live plugin state. The install or the use prompt only fills the message box
  // of a new Rex conversation, for the user to edit and send (PRD 6.1 / 6.2):
  // no message, conversation or install starts here. A failed install continues
  // in its recent install conversation, with the repair request filled in the
  // same way. Every await is checked against the account and the page the click
  // came from.
  const handleReaEntry = useCallback(async (requested?: ReaEntryAction) => {
    if (startingReaRef.current) return;
    startingReaRef.current = true;
    setStartingRea(true);
    const identity = currentReaIdentity();
    const navigationKey = navigationRef.current.key;
    const stale = () => (
      !mountedRef.current
      || currentReaIdentity() !== identity
      || navigationRef.current.key !== navigationKey
    );
    const fill = (text: string) => {
      // REA runs with Rex's tools; plan mode cannot install, so the composer shows Build.
      setSelectedAgent('rex');
      handleSelectExecutionMode('build');
      // An empty conversation that already has an id shows the shortcut too.
      setReaComposerPrefill({ id: createMessageId(), text, sessionId: selectedSessionIdRef.current ?? null });
    };
    try {
      let status: ReaStatus | null;
      try {
        status = await refreshReaStatus();
      } catch (error) {
        if (!stale()) toast.error(t('rea.entry.statusFailed', { message: error instanceof Error ? error.message : String(error) }));
        return;
      }
      if (stale() || !status) return;
      const state = status.state;
      if (state === 'uninstalling') {
        toast.info(t('rea.entry.uninstalling'));
        return;
      }
      if (state === 'uninstall_failed') {
        toast.warning(t('rea.entry.uninstallFailed', { count: status.uninstallJob?.residues?.length ?? 0 }));
        return;
      }
      if (state === 'installing' || state === 'validating') {
        if (status.installSessionId) {
          selectSession(status.installSessionId);
          return;
        }
        toast.info(t('rea.entry.installing', {
          stage: t(`rea.stage.${status.job?.stage ?? 'check'}`),
          progress: status.job?.progress ?? 0,
        }));
        return;
      }
      // Usable, or broken after it passed once: an analysis conversation, where Rex repairs first.
      if (state === 'ready' || (state === 'degraded' && requested !== 'repair')) {
        fill(t('rea.usePrompt'));
        return;
      }
      if (!status.canInstall) {
        toast.info(t(state === 'not_installed' ? 'rea.entry.memberInstall' : 'rea.entry.memberRepair'));
        return;
      }
      if (state !== 'not_installed' && status.installSessionId) {
        if (status.installSessionId === selectedSessionIdRef.current) {
          // Already in it (an emptied install conversation shows the shortcut again).
          fill(t('rea.repairPrompt'));
          return;
        }
        // Continue troubleshooting where the install ran: the request waits in its message box, in
        // Build mode (plan mode cannot install).
        writeChatDraft(status.installSessionId, t('rea.repairPrompt'));
        writeSessionExecutionMode(status.installSessionId, 'build');
        selectSession(status.installSessionId);
        return;
      }
      // A failed install starts over with the same guide (it reuses what is there).
      fill(state === 'degraded' ? t('rea.repairPrompt') : t('rea.installPrompt'));
    } finally {
      startingReaRef.current = false;
      if (mountedRef.current) setStartingRea(false);
    }
  }, [
    handleSelectExecutionMode,
    refreshReaStatus,
    selectSession,
    t,
    toast,
  ]);

  const handleReaComposerPrefillApplied = useCallback((id: string) => {
    setReaComposerPrefill((current) => (current?.id === id ? null : current));
  }, []);
  // A guide meant for one conversation is not carried into another one.
  useEffect(() => {
    setReaComposerPrefill((current) => (current && current.sessionId !== (selectedSessionId ?? null) ? null : current));
  }, [selectedSessionId]);

  // The plugin square's 「安装」「使用」「排查并修复」 land here (see the Hub page) and
  // run the shortcut's routing once per navigation, also across a page reload.
  const reaEntryNavigationRef = useRef<string | null>(null);
  useEffect(() => {
    const action = reaEntryNavigationAction(location.state);
    if (!paneActive || selectedSessionId || !action) return;
    if (reaEntryNavigationRef.current === location.key || consumeReaEntryNavigation(location.key)) return;
    reaEntryNavigationRef.current = location.key;
    void handleReaEntry(action);
  }, [handleReaEntry, location.key, location.state, paneActive, selectedSessionId]);

  const showSelectorTooltip = useCallback((target: HTMLElement, title: string, lines: string[]) => {
    const rect = target.getBoundingClientRect();
    setSelectorTooltip({
      title,
      lines,
      x: rect.left - 8,
      y: rect.top + rect.height / 2,
    });
  }, []);

  const handleOpenCreateProject = useCallback(() => {
    setCreateProjectOpen(true);
  }, []);

  const workbenchRefreshing = refreshingSessions || refreshingProjects || resolvingSelectedSession;
  const workbenchRefreshLabel = resolvingSelectedSession
    ? t('restoringTask')
    : t('refreshingWorkbench');

  return (
    <div className="flex h-full w-full overflow-hidden bg-[#fcfcfd] text-[#202328] dark:bg-[#303842] dark:text-[#d7dee8]">
      {/* ── Main area ── */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-[#fcfcfd] dark:bg-[#303842]">
        {/* Header */}
        <header className="relative flex h-[52px] flex-shrink-0 items-center gap-2 px-4 text-[13px]">
          <div className="flex min-w-0 items-center">
            <h2 className="truncate text-sm font-semibold text-[#555a61] dark:text-[#c3ccd6]">
              {selectedSession?.title || t('newSession')}
            </h2>
          </div>

          {selectedSessionId && selectedSession && !activeSessionError && (
            <CopyButton text={new URL(sessionPath(selectedSessionId), window.location.origin).href} label={t('copyLink')} labelClassName="max-md:sr-only" />
          )}

          {workbenchRefreshing && (
            <WorkbenchRefreshStatus label={workbenchRefreshLabel} />
          )}
          {activeChatSessionId && <SSEStatusIndicator status={sseStatus} t={t} />}

          <div className="ml-auto flex items-center">
            <button
              type="button"
              disabled={!activeChatSessionId}
              onClick={() => setContextPanelOpen((open) => !open)}
              className={`flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 max-md:shrink-0 max-md:whitespace-nowrap ${
                contextPanelOpen
                  ? 'bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-200'
                  : 'text-[#7b8087] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.06] dark:hover:text-white'
              }`}
              aria-label={t('context.title')}
              title={t('context.title')}
            >
              <Sparkles className="h-3.5 w-3.5" />
              <span>{t('context.title')}</span>
              {(contextSnapshot?.counts.total || 0) > 0 && (
                <span className="rounded-full bg-current/10 px-1.5 py-0.5 text-[10px]">
                  {contextSnapshot!.counts.total}
                </span>
              )}
            </button>
          </div>
        </header>

        <div className="relative flex min-h-0 flex-1 overflow-hidden">
        {/* Chat — powered by unified SessionChat */}
        {activeSessionError ? (
          <div role="alert" className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
            <p>{t(`sessionAccess.${activeSessionError.kind}`)}</p>
            <div className="flex gap-4">
              <button onClick={() => { setSessionLoadError(null); setSessionLoadAttempt(value => value + 1); }}>{t('sessionAccess.retry')}</button>
              <button onClick={() => selectSession(null)}>{t('sessionAccess.back')}</button>
            </div>
          </div>
        ) : resolvingSelectedSession ? (
          <SessionChatSkeleton />
        ) : (
          <SessionChat
            key={activeChatSessionId ?? 'empty-session'}
            sessionId={activeChatSessionId}
            live={Boolean(activeChatSessionId)}
          hideInput={selectedSession?.canWrite === false}
          display={{
            compact: false,
            pageCanvas: true,
            showActions: true,
            showTimestamp: true,
            collapseIntermediateSteps: true,
            processGroupsDefaultOpen: false,
            processGroupsOpenWhileActive: true,
          }}
          agentName={selectedAgent}
          executionMode={selectedExecutionMode}
          onExecutionModeAccepted={handleExecutionModeAccepted}
          mentionAgents={chatAgents}
          className="min-w-0 flex-1 min-h-0"
          composerTextareaMinHeight={56}
          initialMessage={pendingInitialSessionId === activeChatSessionId && selectedSession?.canWrite !== false ? pendingInitialMessage : null}
          initialDisplayText={pendingInitialSessionId === activeChatSessionId ? pendingInitialDisplayText : null}
          initialOptimisticMessage={pendingOptimisticMessage}
          focusMessageId={pendingFocusMessageId}
          onFocusMessageConsumed={() => setPendingFocusMessageId(null)}
          onOpenContextFile={handleOpenContextFile}
          onOpenContext={() => setContextPanelOpen(true)}
          onInitialMessageConsumed={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
          }}
          onInitialOptimisticMessageConsumed={(messageId) => {
            setPendingOptimisticMessage((message) => (
              message?.id === messageId ? null : message
            ));
          }}
          onSseStatusChange={activeChatSessionId ? setSseStatus : undefined}
          onSSEEvent={handleSSEEvent}
          onError={handleChatError}
          onCreateAndSend={handleCreateAndSend}
          onCreateNewSession={handleStartNewSession}
          onStreamingDone={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
          }}
          supportsVision={effectiveSupportsVision}
          // Reverse-engineering samples can be attached once REA is usable.
          allowAnyUpload={reaAcceptsSamples(reaStatus)}
          composerPrefill={reaComposerPrefill?.sessionId === (activeChatSessionId ?? null) ? reaComposerPrefill : null}
          onComposerPrefillApplied={handleReaComposerPrefillApplied}
          contextWindowTokens={effectiveModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen
              onSuggestion={setInput}
              onAlertOperationsSetup={() => void handleAlertOperationsSetup()}
              alertOperationsBusy={installingSocWorkspace}
              reaShortcut={{
                badge: Boolean(reaStatus?.badge),
                busy: startingRea,
                onClick: () => void handleReaEntry(),
              }}
            />
          )}
          onComposerAddMenuOpenChange={(open) => {
            setShowAgentOptions(false);
            setShowWorkflowOptions(false);
            setShowSkillOptions(false);
            if (!open) return;
            setShowProjectOptions(false);
            setShowModelOptions(false);
            void loadComposerResources();
          }}
          composerAddMenuSlot={({ closeMenu, insertMention, insertReference }) => (
            <>
            <div className="relative" data-agent-selector>
              <button
                type="button"
                onClick={() => {
                  setShowAgentOptions((open) => !open);
                  setShowWorkflowOptions(false);
                  setShowSkillOptions(false);
                  setShowProjectOptions(false);
                  setShowModelOptions(false);
                }}
                className="group flex h-10 w-full items-center gap-2.5 rounded-[9px] px-2 text-left text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-100/90 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-white/[0.07] dark:hover:text-white"
                title={t('agentPicker.title')}
                aria-label={t('chat.addMenu.agent')}
                aria-haspopup="menu"
                aria-expanded={showAgentOptions}
              >
                <span className="grid h-7 w-7 shrink-0 place-items-center text-zinc-500 transition-colors group-hover:text-zinc-800 dark:text-zinc-400 dark:group-hover:text-white">
                  <Bot className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 flex-1 truncate">{t('chat.addMenu.agent')}</span>
                <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform ${showAgentOptions ? '-rotate-90' : ''}`} />
              </button>
              {showAgentOptions && (
                <div className="absolute bottom-0 left-[calc(100%+0.5rem)] z-[60] w-[300px] max-w-[calc(100vw-2rem)] overflow-hidden max-md:left-0 rounded-[14px] border border-black/[0.09] bg-white/95 shadow-[0_18px_46px_rgba(22,27,34,0.14),0_2px_8px_rgba(22,27,34,0.06)] backdrop-blur-xl dark:border-white/[0.11] dark:bg-[#252c35]/95 dark:shadow-[0_20px_48px_rgba(8,10,13,0.44)]">
                  <div className="flex min-h-12 items-center justify-between gap-3 border-b border-zinc-100 px-3 py-2 dark:border-white/[0.08]">
                    <div className="min-w-0">
                      <div className="text-[12px] font-semibold text-zinc-800 dark:text-zinc-100">{t('agentPicker.title')}</div>
                      <div
                        className="truncate text-[10px] text-zinc-400 dark:text-zinc-500"
                        onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseOver={(event) => showSelectorTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                        onMouseLeave={() => setSelectorTooltip(null)}
                        onPointerLeave={() => setSelectorTooltip(null)}
                      >
                        {t('agentPicker.hint')}
                      </div>
                    </div>
                    <div className="inline-flex shrink-0 items-center rounded-lg bg-zinc-100 p-0.5 text-[10px] dark:bg-black/15">
                      {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                        <button
                          key={filter}
                          type="button"
                          onClick={() => setAgentSourceFilter(filter)}
                          className={`rounded-md px-2 py-1 transition-colors ${
                            agentSourceFilter === filter
                              ? 'bg-white text-zinc-900 shadow-[0_1px_3px_rgba(22,27,34,0.08)] dark:bg-white/[0.12] dark:text-zinc-50'
                              : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100'
                          }`}
                        >
                          {t(`agentPicker.filter.${filter}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="h-64 space-y-0.5 overflow-y-auto p-1.5">
                    {loadingAgents ? (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
                    ) : filteredChatAgents.length > 0 ? (
                      filteredChatAgents.map((agent) => {
                        const displayName = getAgentDisplayName(agent, i18n.language);
                        const primaryDesc = getAgentDisplayDescription(agent, i18n.language) || t('smartAssistant');
                        return (
                          <button
                            key={agent.name}
                            type="button"
                            onClick={() => {
                              setShowAgentOptions(false);
                              insertMention(agent.name);
                              closeMenu();
                            }}
                            className={`w-full min-w-0 rounded-lg border px-2.5 py-2 text-left transition-colors ${
                              selectedAgent === agent.name
                                ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
                                : 'border-transparent text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-white/[0.06] dark:hover:text-zinc-50'
                            }`}
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md ${
                                selectedAgent === agent.name
                                  ? 'bg-white text-zinc-700 shadow-[0_1px_3px_rgba(22,27,34,0.08)] dark:bg-white/[0.10] dark:text-white'
                                  : 'bg-zinc-100 text-zinc-400 dark:bg-white/[0.05] dark:text-zinc-500'
                              }`}>
                                <Bot className="h-3 w-3" />
                              </span>
                              <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">
                                {displayName}
                              </span>
                              <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium ${
                                agent.mode === 'primary'
                                  ? 'bg-zinc-100 text-zinc-600 dark:bg-white/[0.08] dark:text-zinc-300'
                                  : agent.native
                                    ? 'bg-zinc-100 text-zinc-600 dark:bg-white/[0.08] dark:text-zinc-300'
                                    : 'bg-teal-50 text-teal-600 dark:bg-teal-950/40 dark:text-teal-300'
                              }`}>
                                {agent.mode === 'primary'
                                  ? t('agentPicker.badge.primary')
                                  : agent.native
                                    ? t('agentPicker.badge.builtin')
                                    : t('agentPicker.badge.custom')}
                              </span>
                              {primaryDesc && (
                                <span
                                  className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-white/[0.08]"
                                  onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                  onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                  onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseOver={(event) => showSelectorTooltip(event.currentTarget, displayName, [primaryDesc])}
                                  onMouseLeave={() => setSelectorTooltip(null)}
                                  onPointerLeave={() => setSelectorTooltip(null)}
                                >
                                  <Info className="h-3 w-3 text-zinc-300 transition-colors group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-300" />
                                </span>
                              )}
                              {selectedAgent === agent.name && <Check className="h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-300" />}
                            </div>
                          </button>
                        );
                      })
                    ) : (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('noAgents')}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
            <ComposerResourcePicker
              label={t('chat.addMenu.skills')}
              title={t('chat.addMenu.selectSkill')}
              emptyText={t('chat.addMenu.noSkills')}
              icon={BookOpen}
              items={composerSkills.map((skill) => ({
                id: skill.name,
                name: skill.name,
                description: skill.description,
                badge: skill.category,
                source: skill.source === 'project' ? 'builtin' : 'custom',
              }))}
              loading={loadingComposerResources}
              open={showSkillOptions}
              onToggle={() => {
                setShowSkillOptions((open) => !open);
                setShowAgentOptions(false);
                setShowWorkflowOptions(false);
              }}
              onSelect={(skill) => {
                insertReference(skill.id, 'skill');
              }}
            />
            <ComposerResourcePicker
              label={t('chat.addMenu.workflows')}
              title={t('chat.addMenu.selectWorkflow')}
              emptyText={t('chat.addMenu.noWorkflows')}
              icon={WorkflowIcon}
              items={composerWorkflows.map((workflow) => ({
                id: workflow.id,
                name: getWorkflowDisplayName(workflow, i18n.language),
                description: workflow.description,
                source: workflow.source === 'project' ? 'builtin' : 'custom',
              }))}
              loading={loadingComposerResources}
              open={showWorkflowOptions}
              onToggle={() => {
                setShowWorkflowOptions((open) => !open);
                setShowAgentOptions(false);
                setShowSkillOptions(false);
              }}
              onSelect={(workflow) => {
                insertReference(workflow.id, 'workflow');
                setShowWorkflowOptions(false);
                closeMenu();
              }}
            />
            </>
          )}
          toolbarSlot={
            // Keep the mode switch whole when the composer narrows; the model name truncates instead.
            <div className="flex shrink-0 items-center gap-0.5">
              <div ref={executionModeSelectorRef} className="relative" data-execution-mode-selector>
                <button
                  type="button"
                  onClick={() => {
                    setShowExecutionModeOptions(!showExecutionModeOptions);
                    setShowAgentOptions(false);
                    setShowModelOptions(false);
                    setShowProjectOptions(false);
                  }}
                  className="flex h-7 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                  title={t(`executionMode.options.${selectedExecutionMode}.description`)}
                  aria-label={t('executionMode.title')}
                  aria-haspopup="menu"
                  aria-expanded={showExecutionModeOptions}
                >
                  <ExecutionModeIcon mode={selectedExecutionMode} />
                  <span className="font-medium">
                    {t(`executionMode.options.${selectedExecutionMode}.label`)}
                  </span>
                  <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showExecutionModeOptions ? 'rotate-180' : ''}`} />
                </button>
                {showExecutionModeOptions && (
                  <SessionComposerMenu
                    anchorRef={executionModeSelectorRef}
                    data-execution-mode-selector
                    role="menu"
                    aria-label={t('executionMode.title')}
                    header={(
                      <SessionComposerMenuHeader
                        title={t('executionMode.title')}
                        hint={t('executionMode.hint')}
                      />
                    )}
                    contentClassName="space-y-0.5 p-1.5"
                  >
                    {SESSION_EXECUTION_MODES.map((mode) => (
                      <SessionModeOption
                        key={mode}
                        role="menuitemradio"
                        aria-checked={selectedExecutionMode === mode}
                        selected={selectedExecutionMode === mode}
                        label={t(`executionMode.options.${mode}.label`)}
                        description={t(`executionMode.options.${mode}.description`)}
                        icon={<ExecutionModeIcon mode={mode} className="h-3.5 w-3.5 shrink-0" />}
                        onClick={() => handleSelectExecutionMode(mode)}
                      />
                    ))}
                  </SessionComposerMenu>
                )}
              </div>
              {!activeChatSessionId && (
              <div className="relative max-md:static" data-project-selector>
                <button
                  type="button"
                  onClick={() => {
                    setShowProjectOptions((open) => !open);
                    setShowAgentOptions(false);
                    setShowExecutionModeOptions(false);
                    setShowModelOptions(false);
                  }}
                  className="flex h-7 max-w-[160px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-[#5e646b] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#b8c2cc] dark:hover:bg-white/[0.06] dark:hover:text-white"
                  title={t('projectPicker.title')}
                  aria-label={t('projectPicker.title')}
                  aria-expanded={showProjectOptions}
                >
                  <FolderGit2 className="h-3 w-3 shrink-0" />
                  <span className="truncate font-medium">{selectedProjectContextLabel}</span>
                  <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showProjectOptions ? 'rotate-180' : ''}`} />
                </button>
                {showProjectOptions && (
                  <div
                    className="absolute bottom-full left-0 z-50 mb-2 grid w-64 gap-0.5 rounded-[10px] border border-black/[0.11] bg-[#fdfdfc] p-1 shadow-[0_8px_24px_rgba(22,27,34,0.10)] dark:border-white/[0.10] dark:bg-[#303842] dark:shadow-[0_12px_32px_rgba(15,18,22,0.35)]"
                    role="menu"
                    aria-label={t('projectPicker.title')}
                  >
                    {projectSessionGroups.map((group) => (
                      <button
                        key={group.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selectedProjectId === group.id}
                        onClick={() => {
                          setSelectedProjectId(group.id);
                          setShowProjectOptions(false);
                        }}
                        className={`flex h-8 w-full items-center gap-2 rounded-lg border px-2 text-left text-xs transition-colors ${
                          selectedProjectId === group.id
                            ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
                            : 'border-transparent text-[#4e5359] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white'
                        }`}
                      >
                        <FolderGit2 className={`h-3.5 w-3.5 shrink-0 ${selectedProjectId === group.id ? 'text-blue-600 dark:text-blue-300' : 'text-[#7b8087] dark:text-[#9aa7b4]'}`} />
                        <span className="min-w-0 flex-1 truncate">{group.label}</span>
                        {selectedProjectId === group.id && <Check className="h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-300" />}
                      </button>
                    ))}
                    <div className="mx-2 my-0.5 border-t border-black/[0.07] dark:border-white/[0.08]" />
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={selectedProjectId === TASK_SESSION_GROUP_ID}
                      onClick={() => {
                        setSelectedProjectId(TASK_SESSION_GROUP_ID);
                        setShowProjectOptions(false);
                      }}
                      className={`flex h-8 w-full items-center gap-2 rounded-lg border px-2 text-left text-xs transition-colors ${
                        selectedProjectId === TASK_SESSION_GROUP_ID
                          ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
                          : 'border-transparent text-[#4e5359] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white'
                      }`}
                    >
                      <X className={`h-3.5 w-3.5 shrink-0 ${selectedProjectId === TASK_SESSION_GROUP_ID ? 'text-blue-600 dark:text-blue-300' : 'text-[#7b8087] dark:text-[#9aa7b4]'}`} />
                      <span className="min-w-0 flex-1 truncate">{t('projectPicker.none')}</span>
                      {selectedProjectId === TASK_SESSION_GROUP_ID && <Check className="h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-300" />}
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setShowProjectOptions(false);
                        handleOpenCreateProject();
                      }}
                      className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                    >
                      <FolderPlus className="h-3.5 w-3.5 shrink-0 text-[#7b8087] dark:text-[#9aa7b4]" />
                      <span>{t('projectPicker.create')}</span>
                    </button>
                  </div>
                )}
              </div>
            )}
            </div>
          }
          centerToolbarSlot={
            // The model picker gives way first on a narrow composer (its name truncates);
            // on phones the permission picker can drop to the next line (longer English labels).
            <div className="flex min-w-0 items-center gap-1 max-md:flex-wrap">
              <div ref={modelSelectorRef} className="relative w-[132px] min-w-[72px] shrink" data-model-selector>
              <button
                type="button"
                onClick={() => {
                  setShowModelOptions(!showModelOptions);
                  setShowExecutionModeOptions(false);
                  setShowProjectOptions(false);
                  setShowAgentOptions(false);
                }}
                disabled={loadingProviders || loadingEnabledModels || chatModelOptions.length === 0}
                className="flex h-7 w-full min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                title={selectedModelAuto
                  ? `${t('modelPicker.auto')}: ${autoStatusLabel}`
                  : selectedModelOption
                    ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}`
                    : t('modelPicker.empty')}
              >
                {selectedModelAuto
                  ? <Sparkles className="h-3 w-3 shrink-0" />
                  : <Cpu className="h-3 w-3 shrink-0" />}
                <span className="truncate font-medium">
                  {selectedModelAuto
                    ? t('modelPicker.auto')
                    : selectedModelOption?.label ?? (loadingProviders || loadingEnabledModels ? t('loading') : t('modelPicker.empty'))}
                </span>
                <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showModelOptions ? 'rotate-180' : ''}`} />
              </button>
              {showModelOptions && (
                <SessionComposerMenu
                  anchorRef={modelSelectorRef}
                  data-model-selector
                  aria-label={t('modelPicker.title')}
                  header={(
                    <SessionComposerMenuHeader
                      title={t('modelPicker.title')}
                      hint={t('modelPicker.hint')}
                    />
                  )}
                  contentClassName="p-1.5"
                  footer={(
                    <div className="border-t border-zinc-100 p-1.5 dark:border-zinc-800">
                      <button
                        type="button"
                        onClick={() => {
                          setShowModelOptions(false);
                          setSelectorTooltip(null);
                          navigate('/models');
                        }}
                        className="flex w-full items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                      >
                        <Plus className="h-3 w-3" />
                        {t('modelPicker.addModel')}
                      </button>
                    </div>
                  )}
                >
                    {loadingProviders || loadingEnabledModels ? (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
                    ) : (
                      <>
                        <div className="mb-1 border-b border-zinc-100 pb-1.5 dark:border-zinc-800">
                          <button
                            type="button"
                            onClick={() => void handleSelectAutoModel()}
                            title={`${t('modelPicker.auto')}\n${autoStatusLabel}`}
                            disabled={!canSelectAuto}
                            className={`w-full rounded-md border px-2 py-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                              selectedModelAuto
                                ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
                                : 'border-transparent text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
                            }`}
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              <Sparkles className={`h-3 w-3 shrink-0 ${selectedModelAuto ? 'text-zinc-600 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                              <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">
                                {t('modelPicker.auto')}
                              </span>
                              <span
                                className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-700"
                                onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseOver={(event) => showSelectorTooltip(event.currentTarget, t('modelPicker.auto'), [autoStatusLabel])}
                                onMouseLeave={() => setSelectorTooltip(null)}
                                onPointerLeave={() => setSelectorTooltip(null)}
                              >
                                <Info className="h-3 w-3 text-zinc-300 transition-colors group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-300" />
                              </span>
                              <Check aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-300 ${selectedModelAuto ? '' : 'invisible'}`} />
                            </div>
                          </button>
                        </div>
                        {groupedChatModelOptions.length > 0 ? groupedChatModelOptions.map((group) => (
                        <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-white/95 px-1.5 py-1 text-[10px] font-semibold text-zinc-500 backdrop-blur dark:bg-zinc-900/95 dark:text-zinc-400">
                            <span className="min-w-0 truncate" title={group.providerName}>{group.providerName}</span>
                            <span className="shrink-0 rounded bg-zinc-50 px-1.5 py-0.5 text-[9px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                              {t('modelPicker.count', { count: group.models.length })}
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {group.models.map((option) => (
                              <button
                                key={option.key}
                                type="button"
                                onClick={() => void handleSelectModel(option)}
                                title={`${option.label}\n${option.providerName} / ${option.modelID}\n${option.pricingLabel}\n${option.contextLabel}`}
                                className={`w-full rounded-md border px-2 py-1.5 text-left transition-colors ${
                                  selectedModelOption?.key === option.key
                                    ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
                                    : 'border-transparent text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
                                }`}
                              >
                                <div className="flex min-w-0 items-center gap-2">
                                  <Cpu className={`h-3 w-3 shrink-0 ${selectedModelOption?.key === option.key ? 'text-zinc-600 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">{option.label}</span>
                                  {option.supportsVision === true && (
                                    <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                                      {t('modelPicker.vision')}
                                    </span>
                                  )}
                                  <div className="ml-auto flex shrink-0 items-center gap-1">
                                    <span
                                      className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-700"
                                      onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                      onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                      onPointerEnter={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseEnter={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseOver={(event) => showSelectorTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                      onMouseLeave={() => setSelectorTooltip(null)}
                                      onPointerLeave={() => setSelectorTooltip(null)}
                                    >
                                      <Info className="h-3 w-3 text-zinc-300 transition-colors group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-300" />
                                    </span>
                                  </div>
                                  <Check aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-300 ${selectedModelOption?.key === option.key ? '' : 'invisible'}`} />
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                        )) : (
                          <div className="p-3 text-center text-xs text-zinc-500">{t('modelPicker.empty')}</div>
                        )}
                      </>
                    )}
                </SessionComposerMenu>
              )}
              </div>
              <ExtensionSlot name="session.settings" context={sessionSettingsSlotContext} />
            </div>
          }
          />
        )}
        {contextPanelOpen && activeChatSessionId && (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              onPointerDown={handleResizeContextPanel}
              className="hidden w-1 flex-shrink-0 cursor-col-resize bg-zinc-200/70 transition-colors hover:bg-violet-300 xl:block dark:bg-zinc-700 dark:hover:bg-violet-500"
            />
            {/* A side column next to the chat, capped so messages and the composer keep their room
                (40% below xl, 45% above); phones get a closable full-screen panel. */}
            <aside
              data-testid="session-context-aside"
              className="fixed inset-0 z-50 h-full w-screen flex-shrink-0 overflow-hidden border-l border-zinc-200 bg-white sm:static sm:z-auto sm:w-[min(var(--session-context-width),40%)] xl:w-[min(var(--session-context-width),45%)] dark:border-zinc-700 dark:bg-zinc-900"
              style={{ '--session-context-width': `${contextPanelWidth}px` } as React.CSSProperties}
            >
              <SessionContextPanel
                key={`${activeChatSessionId}:${contextResetVersion}`}
                sessionId={activeChatSessionId}
                snapshot={contextSnapshot?.sessionID === activeChatSessionId ? contextSnapshot : null}
                loading={contextLoading}
                loadingMore={contextLoadingMore}
                error={contextError}
                requestedResourceID={requestedContextResource?.sessionId === activeChatSessionId ? requestedContextResource.resourceID : null}
                onRequestedResourceConsumed={() => setRequestedContextResource(null)}
                onClose={() => {
                  setContextPanelOpen(false);
                  setRequestedContextResource(null);
                }}
                onRefresh={() => fetchSessionContext()}
                onLoadMore={() => fetchSessionContext(true)}
                onFocusMessage={(messageId) => setPendingFocusMessageId(messageId)}
              />
            </aside>
          </>
        )}
        </div>
      </div>

      {selectorTooltip && (
        <div
          role="tooltip"
          className="pointer-events-none fixed z-[80] w-56 -translate-x-full -translate-y-1/2 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-zinc-700 shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:shadow-xl dark:shadow-black/30"
          style={{ left: selectorTooltip.x, top: selectorTooltip.y }}
        >
          <div className="mb-0.5 font-semibold text-zinc-800 dark:text-zinc-100">{selectorTooltip.title}</div>
          {selectorTooltip.lines.map((line, index) => (
            <div key={`${selectorTooltip.title}-${index}`} className={index === 0 ? '' : 'mt-1 break-all text-zinc-500 dark:text-zinc-400'}>
              {line}
            </div>
          ))}
          <div className="absolute left-full top-1/2 -translate-y-1/2 border-4 border-transparent border-l-zinc-200 dark:border-l-zinc-800" />
        </div>
      )}

      {createProjectOpen && (
        <ProjectFolderDialog
          mode="create"
          onClose={() => setCreateProjectOpen(false)}
          onSaved={(project) => {
            const created = { id: project.id, name: project.name ?? undefined, worktree: project.worktree } as ProjectSummary;
            setCreateProjectOpen(false);
            // List it before selecting it: the picker drops a selection that is not in the list,
            // and the refreshed list may arrive later (or not include it yet).
            setProjects((current) => (current.some((item) => item.id === created.id) ? current : [created, ...current]));
            setSelectedProjectId(project.id);
            void fetchProjects(created, searchQuery);
            announceProjectsChanged('composer');
          }}
        />
      )}

      {suiteInstallProgress && (
        <SuiteInstallProgressPanel
          progress={suiteInstallProgress}
          language={i18n.language}
          onClose={() => setSuiteInstallProgress(null)}
        />
      )}
    </div>
  );
}

// ── Welcome Screen (shown when no messages) ──

interface WelcomeReaShortcut {
  badge: boolean;
  busy: boolean;
  onClick: () => void;
}

const SHORTCUT_CLASS = 'inline-flex h-11 shrink-0 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-zinc-50 px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-white hover:text-[#111827] disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white';

function WelcomeScreen({
  onSuggestion,
  onAlertOperationsSetup,
  alertOperationsBusy,
  reaShortcut,
}: {
  onSuggestion: (text: string) => void;
  onAlertOperationsSetup: () => void;
  alertOperationsBusy: boolean;
  reaShortcut?: WelcomeReaShortcut | null;
}) {
  const { t } = useTranslation('session');
  return (
    <div className="w-full max-w-[700px] px-9 text-center max-[600px]:px-4">
      <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-slate-800 text-white shadow-[0_3px_10px_rgba(30,41,59,0.10)] dark:bg-[#46515e]">
        <Sparkles className="h-[30px] w-[30px]" strokeWidth={1.7} />
      </div>
      <h3 className="text-[22px] font-semibold tracking-[-0.01em] text-[#1f2937] dark:text-[#f0f3f7]">{t('welcome.title')}</h3>
      <p className="mt-3 text-sm leading-6 text-[#647084] dark:text-[#aeb9c5]">{t('welcome.description')}</p>

      {/* One row (REA V5): narrow screens scroll it sideways instead of wrapping; the top padding keeps the 「新」 badge visible. */}
      <div
        data-testid="welcome-shortcuts"
        className="mt-6 flex justify-center gap-2.5 max-[850px]:-mx-1 max-[850px]:justify-start max-[850px]:overflow-x-auto max-[850px]:px-1 max-[850px]:pb-1 max-[850px]:pt-3"
      >
        <button
          onClick={onAlertOperationsSetup}
          disabled={alertOperationsBusy}
          className={SHORTCUT_CLASS}
        >
          {alertOperationsBusy ? (
            <Loader2 className="h-[18px] w-[18px] animate-spin text-[#657489] dark:text-[#aeb9c5]" />
          ) : (
            <Shield className="h-[18px] w-[18px] text-[#657489] dark:text-[#aeb9c5]" />
          )}
          <span>{t('welcome.alertOperations')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.threatHuntingSuggestion'))}
          className={SHORTCUT_CLASS}
        >
          <Search className="h-[18px] w-[18px] text-[#f4511e]" />
          <span>{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className={SHORTCUT_CLASS}
        >
          <AlertTriangle className="h-[18px] w-[18px] text-amber-500" />
          <span>{t('welcome.incidentResponse')}</span>
        </button>
        {reaShortcut && (
          <ReaShortcut
            badge={reaShortcut.badge}
            busy={reaShortcut.busy}
            onClick={reaShortcut.onClick}
            className={SHORTCUT_CLASS}
          />
        )}
      </div>
    </div>
  );
}
