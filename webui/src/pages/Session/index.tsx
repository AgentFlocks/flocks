import { memo, useState, useEffect, useMemo, useCallback, useRef, type RefObject } from 'react';
import {
  Plus, Trash2,
  ChevronDown, ChevronRight, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Share2, Cpu, Info, X, Check,
  FolderGit2, FolderPlus, FolderOpen, Copy, ArrowUp, HardDrive,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { getAnchoredMenuLeftOffset } from '@/components/common/ChatPromptSelectors';
import { useToast } from '@/components/common/Toast';
import SessionChat, { buildInstructionDisplayText, type PromptDisplayOptions, type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import { useSSE } from '@/hooks/useSSE';
import SuiteInstallProgressPanel, {
  applySuiteInstallProgressEvent,
  createSuiteInstallProgressState,
  failSuiteInstallProgress,
  type SuiteInstallProgressState,
} from '@/components/hub/SuiteInstallProgressPanel';
import { sessionApi } from '@/api/session';
import { hubAPI, type HubInstallProgressEvent } from '@/api/hub';
import type { Agent } from '@/api/agent';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import {
  useEnabledChatModelDefinitions,
  useResolvedDefaultModel,
} from '@/hooks/useChatModelResources';
import client, { getApiBase } from '@/api/client';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { formatRelativeTime, formatSessionDate } from '@/utils/time';
import type { ModelDefinitionV2, Session } from '@/types';
import { useAuth } from '@/contexts/AuthContext';

function sanitizeSessionExportName(value: string) {
  const trimmed = value.trim();
  return trimmed
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'session';
}

const LAST_SELECTED_SESSION_STORAGE_KEY = 'flocks:last-selected-session';
const SESSION_PAGE_VISITED_STORAGE_KEY = 'flocks:sessions:visited';
const SOC_WORKSPACE_COMPONENT_ID = 'soc-workspace';
const INSTALLED_HUB_STATES = new Set(['installed', 'localOnly', 'updateAvailable']);
const SESSION_UPDATE_REFETCH_DEBOUNCE_MS = 500;
const AUTO_MODEL_KEY = '__flocks_auto__';
const TASK_SESSION_GROUP_ID = 'tasks';
type AgentSourceFilter = 'all' | 'builtin' | 'custom';
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
type ProjectDialogMode = 'create' | 'rename';
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
type FolderEntry = { name: string; path: string };
type FolderBrowserResponse = {
  path: string;
  parent?: string | null;
  roots: FolderEntry[];
  entries: FolderEntry[];
};
const MULTI_PROJECT_SESSION_PAGE_SIZE = 6;
const SINGLE_PROJECT_SESSION_PAGE_SIZE = 20;

function readSessionStatusType(status: unknown): string | undefined {
  if (typeof status === 'string') return status;
  if (!status || typeof status !== 'object' || !('type' in status)) return undefined;
  return typeof status.type === 'string' ? status.type : undefined;
}

function isRunningSessionStatus(status: unknown): boolean {
  const statusType = readSessionStatusType(status);
  return statusType === 'busy' || statusType === 'compacting' || statusType === 'retry';
}

function readRunningSessionIds(statuses: unknown): Set<string> {
  if (!statuses || typeof statuses !== 'object' || Array.isArray(statuses)) return new Set();
  return new Set(
    Object.entries(statuses)
      .filter(([, status]) => isRunningSessionStatus(status))
      .map(([sessionId]) => sessionId),
  );
}
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

function formatAgentName(name: string): string {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}

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

interface SessionSidebarItemProps {
  session: Session;
  nested?: boolean;
  selected: boolean;
  selectMode: boolean;
  checked: boolean;
  menuOpen: boolean;
  running: boolean;
  renaming: boolean;
  renameValue: string;
  renameSubmitting: boolean;
  language: string;
  relativeTimeClock: number;
  t: (key: string, options?: Record<string, unknown>) => string;
  renameInputRef: RefObject<HTMLInputElement | null>;
  onSelect: (sessionId: string) => void;
  onToggleCheck: (sessionId: string) => void;
  onRenameValueChange: (value: string) => void;
  onSubmitRename: (sessionId: string) => void | Promise<void>;
  onCancelRename: () => void;
  onToggleMenu: (sessionId: string, trigger: HTMLElement) => void;
}

function SessionSidebarItemInner({
  session,
  nested = false,
  selected,
  selectMode,
  checked,
  menuOpen,
  running,
  renaming,
  renameValue,
  renameSubmitting,
  language,
  t,
  renameInputRef,
  onSelect,
  onToggleCheck,
  onRenameValueChange,
  onSubmitRename,
  onCancelRename,
  onToggleMenu,
}: SessionSidebarItemProps) {
  return (
    <div
      onClick={() => onSelect(session.id)}
      className={`group relative mb-0.5 min-h-[34px] cursor-pointer rounded-lg border px-3 py-1 transition-colors duration-100 ${
        nested ? 'ml-5' : ''
      } ${
        !selectMode && selected
          ? 'border-transparent bg-zinc-200/70 text-[#202328] dark:bg-[#3a434e] dark:text-white'
          : selectMode && checked
          ? 'border-blue-200 bg-blue-50 text-[#202328] dark:border-blue-400/40 dark:bg-blue-500/10 dark:text-white'
          : 'border-transparent text-[#5b6067] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#b8c2cc] dark:hover:bg-white/[0.06] dark:hover:text-white'
      }`}
    >
      <div className="flex min-w-0 items-center gap-1.5 pr-6">
        {selectMode && (
          <input
            type="checkbox"
            checked={checked}
            onChange={() => onToggleCheck(session.id)}
            onClick={(e) => e.stopPropagation()}
            className="flex-shrink-0 w-3.5 h-3.5 accent-blue-500 cursor-pointer rounded"
          />
        )}
        {session.category === 'workflow' && (
          <span title={t('workflowSession')} className="flex-shrink-0">
            <WorkflowIcon className="h-3.5 w-3.5 text-orange-400" />
          </span>
        )}
        {session.category === 'entity-config' && (
          <span title={t('configSession')} className="flex-shrink-0">
            <Settings2 className="h-3.5 w-3.5 text-purple-400" />
          </span>
        )}
        {renaming ? (
          <input
            ref={renameInputRef}
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => void onSubmitRename(session.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); void onSubmitRename(session.id); }
              if (e.key === 'Escape') { e.preventDefault(); onCancelRename(); }
            }}
            placeholder={t('renamePlaceholder')}
            disabled={renameSubmitting}
            className="h-6 w-full min-w-0 rounded-md border border-blue-300 bg-white px-1.5 text-sm text-gray-900 outline-none focus:border-blue-400 dark:border-blue-500/50 dark:bg-[#252c35] dark:text-white"
            aria-label={t('rename')}
            data-session-rename-input
          />
        ) : (
          <h3 className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-sm font-medium">
            <span className="truncate">{session.title}</span>
            {session.isShared && (
              <span className="inline-flex shrink-0 items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:border-blue-400/35 dark:bg-blue-500/10 dark:text-blue-200">
                {t('sharedTag')}
              </span>
            )}
          </h3>
        )}
        {session.time?.updated && !renaming && (
          <time
            dateTime={new Date(session.time.updated).toISOString()}
            title={formatSessionDate(session.time.updated)}
            className="ml-1 shrink-0 whitespace-nowrap text-[11px] font-normal tabular-nums text-zinc-500 dark:text-[#8f9ba8]"
          >
            {formatRelativeTime(session.time.updated, language)}
          </time>
        )}
      </div>
      {!selectMode && (
        <div className="absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2" data-session-actions>
          {running && (
            <span
              role="status"
              aria-label={t('chat.tool.running')}
              title={t('chat.tool.running')}
              data-session-running={session.id}
              className={`pointer-events-none absolute inset-0 grid place-items-center text-[#5f8fcb] transition-opacity dark:text-[#8ab4e8] ${
                menuOpen
                  ? 'opacity-0'
                  : 'opacity-100 group-hover:opacity-0 group-focus-within:opacity-0'
              }`}
            >
              <Loader2 className="h-[14px] w-[14px] animate-spin" />
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleMenu(session.id, e.currentTarget);
            }}
            title={t('moreActions')}
            aria-label={t('moreActions')}
            aria-expanded={menuOpen}
            className={`absolute inset-0 grid h-6 w-6 place-items-center rounded-lg text-[#989ca1] transition-[color,opacity,background-color] hover:bg-white hover:text-[#202328] dark:text-[#8f9ba8] dark:hover:bg-white/[0.08] dark:hover:text-white ${
              menuOpen || (selected && !running)
                ? 'text-[#202328] opacity-100 dark:text-white'
                : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <MoreHorizontal className="h-[15px] w-[15px]" />
          </button>
        </div>
      )}
    </div>
  );
}

const SessionSidebarItem = memo(SessionSidebarItemInner, (prev, next) => (
  prev.session.id === next.session.id &&
  prev.nested === next.nested &&
  prev.session.title === next.session.title &&
  prev.session.category === next.session.category &&
  prev.session.isShared === next.session.isShared &&
  prev.session.time?.updated === next.session.time?.updated &&
  prev.selected === next.selected &&
  prev.selectMode === next.selectMode &&
  prev.checked === next.checked &&
  prev.menuOpen === next.menuOpen &&
  prev.running === next.running &&
  prev.renaming === next.renaming &&
  prev.renameValue === next.renameValue &&
  prev.renameSubmitting === next.renameSubmitting &&
  prev.language === next.language &&
  prev.relativeTimeClock === next.relativeTimeClock &&
  prev.t === next.t
));

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

function SessionListSkeleton() {
  return (
    <div
      data-testid="session-list-skeleton"
      aria-hidden="true"
      className="animate-pulse px-2 pt-1"
    >
      <div className="mb-2 h-3 w-16 rounded-full bg-black/[0.07] dark:bg-white/[0.08]" />
      <div className="space-y-1.5">
        {[72, 88, 64].map((width, index) => (
          <div
            key={`${width}-${index}`}
            className="flex h-[34px] items-center gap-2 rounded-lg px-2"
          >
            <div className="h-3.5 w-3.5 shrink-0 rounded bg-black/[0.06] dark:bg-white/[0.07]" />
            <div
              className="h-3 rounded-full bg-black/[0.06] dark:bg-white/[0.07]"
              style={{ width: `${width}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mb-2 mt-5 h-3 w-12 rounded-full bg-black/[0.07] dark:bg-white/[0.08]" />
      <div className="space-y-1.5">
        {[82, 68, 76, 58].map((width, index) => (
          <div
            key={`${width}-${index}`}
            className="flex h-[34px] items-center gap-2 rounded-lg px-2"
          >
            <div className="h-3.5 w-3.5 shrink-0 rounded-full bg-black/[0.06] dark:bg-white/[0.07]" />
            <div
              className="h-3 rounded-full bg-black/[0.06] dark:bg-white/[0.07]"
              style={{ width: `${width}%` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function SessionChatSkeleton() {
  return (
    <div
      data-testid="session-chat-skeleton"
      aria-hidden="true"
      className="flex min-h-0 flex-1 justify-center overflow-hidden px-7 py-6"
    >
      <div className="w-full max-w-[760px] animate-pulse space-y-7">
        <div className="ml-auto h-16 w-[42%] rounded-2xl bg-black/[0.045] dark:bg-white/[0.055]" />
        <div className="space-y-3">
          <div className="h-3 w-24 rounded-full bg-black/[0.06] dark:bg-white/[0.07]" />
          <div className="h-4 w-[86%] rounded-full bg-black/[0.055] dark:bg-white/[0.065]" />
          <div className="h-4 w-[68%] rounded-full bg-black/[0.045] dark:bg-white/[0.055]" />
          <div className="h-20 w-full rounded-2xl bg-black/[0.035] dark:bg-white/[0.045]" />
        </div>
        <div className="ml-auto h-12 w-[34%] rounded-2xl bg-black/[0.04] dark:bg-white/[0.05]" />
      </div>
    </div>
  );
}

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
  const { user } = useAuth();
  const activeProjectStorageKey = `flocks:sessions:active-project:${user?.id ?? 'anonymous'}`;
  const collapsedProjectsStorageKey = `flocks:sessions:collapsed-projects:${user?.id ?? 'anonymous'}`;
  const projectsSectionCollapsedStorageKey = `flocks:sessions:projects-section-collapsed:${user?.id ?? 'anonymous'}`;
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState('rex');
  const [showAgentOptions, setShowAgentOptions] = useState(false);
  const [showProjectOptions, setShowProjectOptions] = useState(false);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [showModelOptions, setShowModelOptions] = useState(false);
  const modelSelectorRef = useRef<HTMLDivElement>(null);
  const [modelMenuLeftOffset, setModelMenuLeftOffset] = useState(0);
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [runningSessionIds, setRunningSessionIds] = useState<Set<string>>(new Set());
  const [relativeTimeClock, setRelativeTimeClock] = useState(0);
  const [creating, setCreating] = useState(false);
  const [installingSocWorkspace, setInstallingSocWorkspace] = useState(false);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [pendingInitialDisplayText, setPendingInitialDisplayText] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [refreshingProjects, setRefreshingProjects] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(collapsedProjectsStorageKey) ?? '[]');
      return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === 'string') : []);
    } catch {
      return new Set();
    }
  });
  const [collapsedLoadedSessionGroupIds, setCollapsedLoadedSessionGroupIds] = useState<Set<string>>(new Set());
  const [projectsSectionCollapsed, setProjectsSectionCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(projectsSectionCollapsedStorageKey) === 'true';
    } catch {
      return false;
    }
  });
  const [projectDialogMode, setProjectDialogMode] = useState<ProjectDialogMode | null>(null);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<string | null>(null);
  const [projectPendingDelete, setProjectPendingDelete] = useState<ProjectSessionGroup | null>(null);
  const [projectDeleting, setProjectDeleting] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [projectNameValue, setProjectNameValue] = useState('');
  const [projectWorktreeValue, setProjectWorktreeValue] = useState('');
  const [projectNameManuallyEdited, setProjectNameManuallyEdited] = useState(false);
  const [folderBrowser, setFolderBrowser] = useState<FolderBrowserResponse | null>(null);
  const [folderBrowserLoading, setFolderBrowserLoading] = useState(false);
  const [projectSubmitting, setProjectSubmitting] = useState(false);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [openMenuSessionId, setOpenMenuSessionId] = useState<string | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<{ top: number; right: number } | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameSubmitting, setRenameSubmitting] = useState(false);
  const [downloadingSessionId, setDownloadingSessionId] = useState<string | null>(null);
  const supportsVision = useDefaultModelVision();
  const [searchQuery, setSearchQuery] = useState('');
  const [agentSourceFilter, setAgentSourceFilter] = useState<AgentSourceFilter>('all');
  const [selectedSessionFallback, setSelectedSessionFallback] = useState<Session | null>(null);
  const [selectorTooltip, setSelectorTooltip] = useState<SelectorTooltip | null>(null);
  const updateModelMenuLeftOffset = useCallback(() => {
    const selector = modelSelectorRef.current;
    if (!selector) return;
    setModelMenuLeftOffset(getAnchoredMenuLeftOffset(
      selector.getBoundingClientRect().left,
      window.innerWidth,
    ));
  }, []);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSubmitInFlightRef = useRef(false);
  const projectSubmitInFlightRef = useRef(false);
  const folderBrowserRequestIdRef = useRef(0);
  const folderBrowserInputPathRef = useRef<string | null>(null);
  const sessionUpdateRefetchTimerRef = useRef<number | null>(null);
  const sessionStatusEventVersionRef = useRef(0);
  const projectListRequestSeqRef = useRef(0);
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
    removeSession,
    removeSessions,
    addSession,
    hasMoreByProject = {},
    loadingMoreProjectIds = new Set<string>(),
    loadMore: loadMoreSessions,
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
      if (agentSourceFilter === 'builtin') return agent.native;
      if (agentSourceFilter === 'custom') return !agent.native;
      return true;
    }),
    [chatAgents, agentSourceFilter],
  );
  const selectedAgentInfo = useMemo(
    () => chatAgents.find((agent) => agent.name === selectedAgent),
    [chatAgents, selectedAgent],
  );
  const chatModelOptions = useMemo<ChatModelOption[]>(() => {
    const providerById = new Map(
      providers
        .filter((provider) => provider.configured)
        .map((provider) => [provider.id, provider]),
    );

    const formatPricing = (pricing: ModelDefinitionV2['pricing']): string => {
      if (!pricing) return t('modelPicker.noCost');
      if (pricing.input === 0 && pricing.output === 0) return t('modelPicker.free');
      const currencySymbol = pricing.currency === 'CNY' ? '¥' : '$';
      return `${currencySymbol}${pricing.input}/${currencySymbol}${pricing.output}/M`;
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
  const activeChatSessionId = selectedSession ? selectedSessionId : null;
  const resolvingSelectedSession = Boolean(selectedSessionId && !selectedSession);
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

  const toggleProjectCollapsed = useCallback((projectId: string) => {
    setCollapsedProjectIds(prev => {
      const next = new Set(prev);
      next.has(projectId) ? next.delete(projectId) : next.add(projectId);
      return next;
    });
  }, []);

  const setLoadedSessionGroupCollapsed = useCallback((groupId: string, collapsed: boolean) => {
    setCollapsedLoadedSessionGroupIds((current) => {
      const next = new Set(current);
      if (collapsed) next.add(groupId);
      else next.delete(groupId);
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        collapsedProjectsStorageKey,
        JSON.stringify(Array.from(collapsedProjectIds)),
      );
    } catch {
      // Project collapse persistence must never block the session manager.
    }
  }, [collapsedProjectIds, collapsedProjectsStorageKey]);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        projectsSectionCollapsedStorageKey,
        String(projectsSectionCollapsed),
      );
    } catch {
      // Section collapse persistence must never block the session manager.
    }
  }, [projectsSectionCollapsed, projectsSectionCollapsedStorageKey]);

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

  const managedProjectSessionGroups = projectSessionGroups;
  const taskSessionGroup = useMemo(
    () => {
      const registeredIds = new Set(projects.map((project) => project.id));
      const taskSessions = sessions.filter((session) => (
        !registeredIds.has(session.effectiveProjectID || session.projectID)
      ));
      return {
        id: TASK_SESSION_GROUP_ID,
        label: t('tasksSection'),
        worktree: '',
        sessions: taskSessions,
        sessionCount: taskSessions.length,
        pathStatus: 'available' as const,
      };
    },
    [projects, sessions, t],
  );
  const taskGroupCollapsed = collapsedProjectIds.has(TASK_SESSION_GROUP_ID);
  const taskGroupSelected = selectedProjectId === TASK_SESSION_GROUP_ID;
  const taskSessionsCollapsedToFirstPage = collapsedLoadedSessionGroupIds.has(TASK_SESSION_GROUP_ID);
  const visibleTaskSessions = taskSessionsCollapsedToFirstPage
    ? taskSessionGroup.sessions.slice(0, sessionListPageSize)
    : taskSessionGroup.sessions;
  const hasMoreRemoteTaskSessions = hasMoreByProject[TASK_SESSION_GROUP_ID] ?? false;
  const canShowMoreTaskSessions = taskSessionsCollapsedToFirstPage || hasMoreRemoteTaskSessions;
  const canCollapseTaskSessions = !taskSessionsCollapsedToFirstPage
    && taskSessionGroup.sessions.length > sessionListPageSize;

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
  }, []);

  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
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
  }, [scheduleSessionListRefetch, t, toast, updateSessionTitle]);

  useEffect(() => {
    void fetchProjects(undefined, searchQuery);
  }, [fetchProjects, searchQuery]);

  const setSessionRunning = useCallback((sessionId: string, running: boolean) => {
    setRunningSessionIds((current) => {
      if (current.has(sessionId) === running) return current;
      const next = new Set(current);
      if (running) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }, []);

  const handleSessionStatusEvent = useCallback((event: SSEChatEvent) => {
    if (event.type !== 'session.status') return;
    const sessionId = event.properties?.sessionID;
    if (typeof sessionId !== 'string') return;
    sessionStatusEventVersionRef.current += 1;
    setSessionRunning(sessionId, isRunningSessionStatus(event.properties?.status));
  }, [setSessionRunning]);

  const refreshRunningSessionIds = useCallback(async () => {
    const eventVersion = sessionStatusEventVersionRef.current;
    try {
      const response = await client.get('/api/session/status');
      if (sessionStatusEventVersionRef.current !== eventVersion) return;
      setRunningSessionIds(readRunningSessionIds(response.data));
    } catch {
      // The sidebar status is progressive enhancement; chat remains usable
      // when status recovery is temporarily unavailable.
    }
  }, []);

  useSSE({
    url: `${getApiBase()}/api/event`,
    onEvent: handleSessionStatusEvent,
    onReconnect: refreshRunningSessionIds,
    enabled: true,
    reconnect: { enabled: true, maxRetries: 5, initialDelay: 1000, maxDelay: 10000 },
  });

  useEffect(() => {
    void refreshRunningSessionIds();
  }, [refreshRunningSessionIds]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setRelativeTimeClock((tick) => tick + 1);
    }, 60_000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!openProjectMenuId) return;
    const closeMenu = () => setOpenProjectMenuId(null);
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  }, [openProjectMenuId]);

  // Keep the selected session in sync with URL query params (e.g. onboarding
  // or other in-app navigation to `/sessions?session=...`). Clear the params
  // after consuming them so refreshes don't re-send the initial message.
  useEffect(() => {
    const sessionParam = searchParams.get('session');
    const messageParam = searchParams.get('message');
    const displayParam = searchParams.get('display');
    if (!sessionParam) return;

    if (sessionParam !== selectedSessionId) {
      setSelectedSessionId(sessionParam);
    }
    if (sessionParam) {
      if (messageParam) {
        setPendingInitialMessage(messageParam);
        setPendingInitialDisplayText(displayParam ? buildInstructionDisplayText(displayParam) : null);
      } else {
        setPendingInitialMessage(null);
        setPendingInitialDisplayText(null);
      }
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, selectedSessionId, setSearchParams]);

  useEffect(() => {
    if (loadingSessions) return;

    const alreadyVisited = hasVisitedSessionPage();
    markSessionPageVisited();

    if (selectedSessionId) return;
    if (searchParams.get('session')) return;
    if (!alreadyVisited) return;
    if (shouldSkipLastSelectedSessionRestore(location.state)) return;

    const lastSelectedSessionId = readLastSelectedSessionId();
    if (lastSelectedSessionId) {
      setSelectedSessionId(lastSelectedSessionId);
    }
  }, [loadingSessions, location.state, searchParams, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId || selectedSession?.id !== selectedSessionId) return;
    writeLastSelectedSessionId(selectedSessionId);
  }, [selectedSession?.id, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setSelectedSessionFallback(null);
      return;
    }
    if (listedSelectedSession) {
      setSelectedSessionFallback(null);
      return;
    }
    if (selectedSessionFallback?.id === selectedSessionId) return;
    if (loadingSessions) return;

    let cancelled = false;
    sessionApi.get(selectedSessionId)
      .then((session) => {
        if (cancelled) return;
        setSelectedSessionFallback(session as unknown as Session);
      })
      .catch((err: any) => {
        if (cancelled) return;
        const statusCode = err?.response?.status ?? err?.status;
        if (statusCode === 403 || statusCode === 404) {
          setSelectedSessionId((current) => (current === selectedSessionId ? null : current));
          setSelectedSessionFallback(null);
          setPendingInitialMessage(null);
          setPendingInitialDisplayText(null);
          writeLastSelectedSessionId(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [listedSelectedSession, loadingSessions, selectedSessionFallback?.id, selectedSessionId]);

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
    updateModelMenuLeftOffset();
    window.addEventListener('resize', updateModelMenuLeftOffset);
    return () => window.removeEventListener('resize', updateModelMenuLeftOffset);
  }, [showModelOptions, updateModelMenuLeftOffset]);

  useEffect(() => {
    if (!showModelOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-model-selector]')) setShowModelOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showModelOptions]);

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
    if (showAgentOptions || showModelOptions || showProjectOptions) return;
    setSelectorTooltip(null);
  }, [showAgentOptions, showModelOptions, showProjectOptions]);

  useEffect(() => {
    if (!openMenuSessionId) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-session-actions]') && !target.closest('[data-session-menu-portal]')) {
        setOpenMenuSessionId(null);
        setMenuAnchor(null);
      }
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [openMenuSessionId]);

  useEffect(() => {
    if (!renamingSessionId) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
  }, [renamingSessionId]);

  useEffect(() => {
    if (!selectMode) return;
    setOpenMenuSessionId(null);
    setRenamingSessionId(null);
    setRenameValue('');
  }, [selectMode]);

  const handleStartNewSession = useCallback(() => {
    writeLastSelectedSessionId(null);
    setSelectedSessionId(null);
    setSelectedSessionFallback(null);
    setPendingInitialMessage(null);
    setPendingInitialDisplayText(null);
    setSelectedAgent('rex');
    setSelectedModelKey(null);
    setSseStatus('disconnected');
    setShowAgentOptions(false);
    setShowModelOptions(false);
    setShowProjectOptions(false);
  }, []);

  const handleCreateSession = useCallback(async (projectIdOverride?: string) => {
    if (creating) return;
    const targetGroupId = projectIdOverride ?? selectedProjectId ?? TASK_SESSION_GROUP_ID;
    const projectID = targetGroupId === TASK_SESSION_GROUP_ID ? null : targetGroupId;
    const carryAutoSelection = !selectedSessionId && selectedModelAuto;
    setCreating(true);
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(projectID ? { projectID } : {}),
        ...(carryAutoSelection ? { model_auto: true } : {}),
      });
      addSession(response.data);
      await fetchProjects(undefined, searchQuery);
      setSelectedSessionFallback(response.data);
      setSelectedProjectId(targetGroupId);
      setCollapsedProjectIds(prev => {
        const next = new Set(prev);
        next.delete(targetGroupId);
        return next;
      });
      setSelectedAgent('rex');
      setSelectedModelKey(carryAutoSelection ? AUTO_MODEL_KEY : null);
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, selectedProjectId, selectedSessionId, selectedModelAuto, addSession, fetchProjects, searchQuery, toast, t]);

  const handleCreateSessionInProject = useCallback((projectId: string) => {
    void handleCreateSession(projectId);
  }, [handleCreateSession]);

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
    imageParts?: ImagePartData[],
    agentOverride?: string,
    modelOverride?: { providerID: string; modelID: string } | null,
    options?: PromptDisplayOptions,
  ) => {
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(selectedProjectIDForCreate ? { projectID: selectedProjectIDForCreate } : {}),
        ...(selectedModelAuto ? { model_auto: true } : {}),
      });
      const newSessionId = response.data.id;

      addSession(response.data);
      await fetchProjects(undefined, searchQuery);
      setSelectedSessionFallback(response.data);
      setSelectedModelKey(selectedModelAuto ? AUTO_MODEL_KEY : null);
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      const effectiveAgent = agentOverride || selectedAgent || 'rex';
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (!selectedModelAuto && modelOverride) payload.model = modelOverride;
      if (options?.displayText) payload.displayText = options.displayText;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, fetchProjects, searchQuery, selectedAgent, selectedModelAuto, selectedProjectIDForCreate, toast, t]);

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

  const showSelectorTooltip = useCallback((target: HTMLElement, title: string, lines: string[]) => {
    const rect = target.getBoundingClientRect();
    setSelectorTooltip({
      title,
      lines,
      x: rect.left - 8,
      y: rect.top + rect.height / 2,
    });
  }, []);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    const target = sessions.find((s) => s.id === sessionId);
    if (target?.canDelete === false) {
      toast.error(t('deleteFailed'), i18n.t('auth:error.noPermissionToDeleteSession') as string);
      return;
    }
    if (!confirm(t('confirmDelete'))) return;
    try {
      await sessionApi.delete(sessionId);
      // Remove from local state first so auto-select won't pick the deleted session.
      // No need to refetchSessions — removeSession already keeps the list accurate.
      if (selectedSessionId === sessionId) setSelectedSessionId(null);
      removeSession(sessionId);
      await fetchProjects(undefined, searchQuery);
    } catch (err: any) {
      toast.error(t('deleteFailed'), err.message);
    }
  }, [fetchProjects, removeSession, searchQuery, selectedSessionId, toast, t]);

  const handleStartRename = useCallback((sessionId: string, currentTitle: string) => {
    setOpenMenuSessionId(null);
    setRenamingSessionId(sessionId);
    setRenameValue(currentTitle);
  }, []);

  const handleCancelRename = useCallback(() => {
    if (renameSubmitting) return;
    renameSubmitInFlightRef.current = false;
    setRenamingSessionId(null);
    setRenameValue('');
  }, [renameSubmitting]);

  const handleSubmitRename = useCallback(async (sessionId: string) => {
    if (renameSubmitInFlightRef.current) return;
    const nextTitle = renameValue.trim();
    if (!nextTitle) {
      toast.error(t('renameFailed'), t('renameEmpty'));
      return;
    }
    const currentSession = sessions.find(session => session.id === sessionId);
    if (currentSession?.title === nextTitle) {
      setRenamingSessionId(null);
      setRenameValue('');
      return;
    }

    renameSubmitInFlightRef.current = true;
    setRenameSubmitting(true);
    try {
      const updatedSession = await sessionApi.update(sessionId, { title: nextTitle });
      updateSessionTitle(sessionId, updatedSession.title ?? nextTitle);
      setRenamingSessionId(null);
      setRenameValue('');
    } catch (err: any) {
      toast.error(t('renameFailed'), err.message);
    } finally {
      renameSubmitInFlightRef.current = false;
      setRenameSubmitting(false);
    }
  }, [renameValue, sessions, t, toast, updateSessionTitle]);

  const handleOpenCreateProject = useCallback(() => {
    setProjectDialogMode('create');
    setEditingProjectId(null);
    setProjectWorktreeValue('');
    setProjectNameValue('');
    setProjectNameManuallyEdited(false);
    setFolderBrowser(null);
  }, []);

  const handleOpenRenameProject = useCallback((project: ProjectSessionGroup) => {
    const persistedName = projects.find((item) => item.id === project.id)?.name?.trim();
    setOpenProjectMenuId(null);
    setProjectDialogMode('rename');
    setEditingProjectId(project.id);
    setProjectNameValue(persistedName && persistedName !== getPathBasename(project.worktree)
      ? persistedName
      : project.label);
    setProjectWorktreeValue(project.worktree);
    setProjectNameManuallyEdited(true);
    setFolderBrowser(null);
  }, [projects]);

  const handleCloseProjectDialog = useCallback(() => {
    if (projectSubmitting) return;
    setProjectDialogMode(null);
    setEditingProjectId(null);
    setProjectNameValue('');
    setProjectWorktreeValue('');
    setProjectNameManuallyEdited(false);
    setFolderBrowser(null);
  }, [projectSubmitting]);

  const loadFolderBrowser = useCallback(async (
    path?: string,
    options?: { preserveInput?: boolean; silent?: boolean },
  ) => {
    const requestId = ++folderBrowserRequestIdRef.current;
    setFolderBrowserLoading(true);
    try {
      const response = await client.get('/api/project/folders', {
        params: { path: path?.trim() || undefined },
      });
      if (requestId !== folderBrowserRequestIdRef.current) return;
      const browser = response.data as FolderBrowserResponse;
      setFolderBrowser(browser);
      if (options?.preserveInput) {
        folderBrowserInputPathRef.current = path?.trim() || browser.path;
      } else {
        folderBrowserInputPathRef.current = browser.path;
        setProjectWorktreeValue(browser.path);
      }
    } catch (err: any) {
      if (requestId === folderBrowserRequestIdRef.current && !options?.silent) {
        toast.error(t('projectDialog.folderBrowseFailed'), err.message);
      }
    } finally {
      if (requestId === folderBrowserRequestIdRef.current) {
        setFolderBrowserLoading(false);
      }
    }
  }, [t, toast]);

  useEffect(() => {
    if (!folderBrowser) return undefined;
    const path = projectWorktreeValue.trim();
    if (
      !path
      || path === folderBrowser.path
      || path === folderBrowserInputPathRef.current
    ) return undefined;

    const timer = window.setTimeout(() => {
      void loadFolderBrowser(path, { preserveInput: true, silent: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [folderBrowser, loadFolderBrowser, projectWorktreeValue]);

  const handleSelectProjectFolder = useCallback((path: string) => {
    setProjectWorktreeValue(path);
    if (!projectNameManuallyEdited) {
      setProjectNameValue(getPathBasename(path));
    }
    setFolderBrowser(null);
  }, [projectNameManuallyEdited]);

  const handleCopyProjectPath = useCallback(async (project: ProjectSessionGroup) => {
    setOpenProjectMenuId(null);
    try {
      await navigator.clipboard.writeText(project.worktree);
      toast.success(t('projectDialog.pathCopied'));
    } catch (err: any) {
      toast.error(t('projectDialog.copyPathFailed'), err.message);
    }
  }, [t, toast]);

  const handleShareProject = useCallback(async (
    project: ProjectSessionGroup,
    nextShared: boolean,
  ) => {
    setOpenProjectMenuId(null);
    try {
      const action = nextShared ? 'share-local' : 'unshare-local';
      await client.post(`/api/project/${project.id}/${action}`);
      toast.success(t(nextShared ? 'shareEnabled' : 'shareDisabled'));
      await Promise.all([
        fetchProjects(undefined, searchQuery),
        refetchSessions(),
      ]);
    } catch (err: any) {
      toast.error(t('shareUpdateFailed'), err.message);
    }
  }, [fetchProjects, refetchSessions, searchQuery, t, toast]);

  const handleOpenDeleteProject = useCallback((project: ProjectSessionGroup) => {
    setOpenProjectMenuId(null);
    setProjectPendingDelete(project);
  }, []);

  const handleDeleteProject = useCallback(async () => {
    if (!projectPendingDelete || projectDeleting) return;

    setProjectDeleting(true);
    try {
      await client.delete(`/api/project/${projectPendingDelete.id}`);
      setProjects((current) => current.filter((project) => project.id !== projectPendingDelete.id));
      setCollapsedProjectIds((current) => {
        const next = new Set(current);
        next.delete(projectPendingDelete.id);
        return next;
      });
      setSelectedProjectId((current) => (
        current === projectPendingDelete.id ? TASK_SESSION_GROUP_ID : current
      ));
      setProjectPendingDelete(null);
      await Promise.all([
        fetchProjects(undefined, searchQuery),
        refetchSessions(),
      ]);
      toast.success(t('projectDialog.deleteSuccess'));
    } catch (err: any) {
      toast.error(t('projectDialog.deleteFailed'), err.message);
    } finally {
      setProjectDeleting(false);
    }
  }, [fetchProjects, projectDeleting, projectPendingDelete, refetchSessions, searchQuery, t, toast]);

  const handleSubmitProject = useCallback(async () => {
    if (!projectDialogMode || projectSubmitInFlightRef.current) return;
    const nextWorktree = projectWorktreeValue.trim() || folderBrowser?.path.trim() || '';
    const nextName = projectNameValue.trim() || (
      projectDialogMode === 'create' && !projectNameManuallyEdited && nextWorktree
        ? getPathBasename(nextWorktree)
        : ''
    );
    if (!nextName) {
      toast.error(t('projectDialog.saveFailed'), t('projectDialog.nameEmpty'));
      return;
    }
    if (projectDialogMode === 'create' && !nextWorktree) {
      toast.error(t('projectDialog.saveFailed'), t('projectDialog.folderEmpty'));
      return;
    }

    projectSubmitInFlightRef.current = true;
    setProjectSubmitting(true);
    try {
      if (projectDialogMode === 'create') {
        const response = await client.post('/api/project', {
          name: nextName,
          worktree: nextWorktree,
        });
        const createdProject = response.data as ProjectSummary;
        setProjects(prev => (
          prev.some((project) => project.id === createdProject.id)
            ? prev.map((project) => (project.id === createdProject.id ? createdProject : project))
            : [createdProject, ...prev]
        ));
        setSelectedProjectId(createdProject.id);
        setCollapsedProjectIds(prev => {
          const next = new Set(prev);
          next.delete(createdProject.id);
          return next;
        });
        await fetchProjects(createdProject);
      } else if (editingProjectId) {
        const response = await client.patch(`/api/project/${editingProjectId}`, { name: nextName });
        await fetchProjects(response.data as ProjectSummary);
      }
      setProjectDialogMode(null);
      setEditingProjectId(null);
      setProjectNameValue('');
      setProjectWorktreeValue('');
      setProjectNameManuallyEdited(false);
      setFolderBrowser(null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(
        t('projectDialog.saveFailed'),
        typeof detail === 'string' ? detail : err?.message,
      );
    } finally {
      projectSubmitInFlightRef.current = false;
      setProjectSubmitting(false);
    }
  }, [editingProjectId, fetchProjects, folderBrowser?.path, projectDialogMode, projectNameManuallyEdited, projectNameValue, projectWorktreeValue, t, toast]);

  const handleDownloadSession = useCallback(async (sessionId: string, title: string) => {
    setOpenMenuSessionId(null);
    setDownloadingSessionId(sessionId);
    try {
      const [sessionInfo, messages] = await Promise.all([
        sessionApi.get(sessionId),
        sessionApi.getMessages(sessionId),
      ]);
      const exportPayload = {
        info: sessionInfo,
        messages,
      };
      const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `session-${sanitizeSessionExportName(title || sessionId)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      toast.error(t('downloadFailed'), err.message);
    } finally {
      setDownloadingSessionId(null);
    }
  }, [t, toast]);

  const handleShareSession = useCallback(async (sessionId: string, nextShared: boolean) => {
    try {
      if (nextShared) {
        await sessionApi.shareLocal(sessionId);
        toast.success(t('shareEnabled'));
      } else {
        await sessionApi.unshareLocal(sessionId);
        toast.success(t('shareDisabled'));
      }
      await refetchSessions();
    } catch (err: any) {
      toast.error(t('shareUpdateFailed'), err.message);
    }
  }, [refetchSessions, t, toast]);

  const handleEnterSelectMode = useCallback(() => {
    setSelectMode(true);
    setCheckedIds(new Set());
  }, []);

  const handleExitSelectMode = useCallback(() => {
    setSelectMode(false);
    setCheckedIds(new Set());
  }, []);

  const handleToggleCheck = useCallback((sessionId: string) => {
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  }, []);
  const handleSelectSessionRow = useCallback((sessionId: string) => {
    if (selectMode) {
      handleToggleCheck(sessionId);
    } else {
      setSelectedSessionId(sessionId);
    }
  }, [handleToggleCheck, selectMode]);
  const handleToggleSessionMenu = useCallback((sessionId: string, trigger: HTMLElement) => {
    setOpenMenuSessionId((current) => {
      if (current === sessionId) {
        setMenuAnchor(null);
        return null;
      }
      const rect = trigger.getBoundingClientRect();
      setMenuAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
      return sessionId;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    if (checkedIds.size === sessions.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(sessions.map(s => s.id)));
    }
  }, [checkedIds.size, sessions]);

  const handleBatchDelete = useCallback(async () => {
    if (checkedIds.size === 0 || batchDeleting) return;
    if (!confirm(t('confirmBatchDelete', { count: checkedIds.size }))) return;
    setBatchDeleting(true);
    const ids = Array.from(checkedIds);
    const succeeded: string[] = [];
    const failed: string[] = [];
    await Promise.all(ids.map(async (id) => {
      try {
        await client.delete(`/api/session/${id}`);
        succeeded.push(id);
      } catch {
        failed.push(id);
      }
    }));
    if (succeeded.length > 0) {
      removeSessions(succeeded);
      await fetchProjects(undefined, searchQuery);
      if (selectedSessionId && succeeded.includes(selectedSessionId)) {
        setSelectedSessionId(null);
      }
    }
    if (failed.length > 0) {
      setCheckedIds(new Set(failed));
      toast.error(t('batchDeleteFailed', { count: failed.length }));
    } else {
      setCheckedIds(new Set());
      setSelectMode(false);
    }
    setBatchDeleting(false);
  }, [batchDeleting, checkedIds, fetchProjects, removeSessions, searchQuery, selectedSessionId, toast, t]);

  const renderSessionListItem = (session: Session) => (
    <div
      key={session.id}
      onClick={() => selectMode ? handleToggleCheck(session.id) : setSelectedSessionId(session.id)}
      className={`group relative mx-2 mb-1 px-3 py-2.5 rounded-xl border cursor-pointer transition-all duration-150 ${
        !selectMode && selectedSessionId === session.id
          ? 'bg-gray-100 border-gray-300 shadow-sm dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-none'
          : selectMode && checkedIds.has(session.id)
          ? 'bg-blue-50 border-blue-200 dark:border-blue-500/40 dark:bg-blue-950/30'
          : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50 hover:shadow-sm dark:border-transparent dark:hover:border-zinc-800 dark:hover:bg-zinc-900 dark:hover:shadow-none'
      }`}
    >
      <div className="flex items-center gap-1.5 min-w-0 pr-7">
        {selectMode && (
          <input
            type="checkbox"
            checked={checkedIds.has(session.id)}
            onChange={() => handleToggleCheck(session.id)}
            onClick={(e) => e.stopPropagation()}
            className="flex-shrink-0 w-3.5 h-3.5 accent-blue-500 cursor-pointer rounded"
          />
        )}
        {session.category === 'workflow' && (
          <span title={t('workflowSession')} className="flex-shrink-0">
            <WorkflowIcon className="w-3 h-3 text-orange-400" />
          </span>
        )}
        {session.category === 'entity-config' && (
          <span title={t('configSession')} className="flex-shrink-0">
            <Settings2 className="w-3 h-3 text-purple-400" />
          </span>
        )}
        {renamingSessionId === session.id ? (
          <input
            ref={renameInputRef}
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => void handleSubmitRename(session.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); void handleSubmitRename(session.id); }
              if (e.key === 'Escape') { e.preventDefault(); handleCancelRename(); }
            }}
            placeholder={t('renamePlaceholder')}
            disabled={renameSubmitting}
            className="w-full min-w-0 rounded border border-blue-300 bg-white px-1.5 py-0.5 text-sm text-gray-900 outline-none focus:border-blue-400 dark:border-blue-500/50 dark:bg-zinc-950 dark:text-zinc-100"
            aria-label={t('rename')}
            data-session-rename-input
          />
        ) : (
          <h3 className="font-semibold text-gray-900 truncate text-sm flex items-center gap-1.5 dark:text-zinc-100">
            <span className="truncate">{session.title}</span>
            {session.isShared && (
              <span className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
                {t('sharedTag')}
              </span>
            )}
          </h3>
        )}
      </div>
      {session.time?.updated && renamingSessionId !== session.id && (
        <p className="mt-1 text-xs text-gray-400 truncate pl-0.5 dark:text-zinc-500">
          {formatSessionDate(session.time.updated)}
        </p>
      )}

      {!selectMode && (
        <div className="absolute right-1.5 top-2" data-session-actions>
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (openMenuSessionId === session.id) {
                setOpenMenuSessionId(null);
                setMenuAnchor(null);
              } else {
                const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setMenuAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
                setOpenMenuSessionId(session.id);
              }
            }}
            title={t('moreActions')}
            aria-label={t('moreActions')}
            aria-expanded={openMenuSessionId === session.id}
            className={`p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all dark:text-zinc-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 ${
              openMenuSessionId === session.id ? 'opacity-100 text-gray-600 bg-gray-200 dark:bg-zinc-800 dark:text-zinc-200' : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <MoreHorizontal className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );

  const workbenchRefreshing = refreshingSessions || refreshingProjects || resolvingSelectedSession;
  const workbenchRefreshLabel = resolvingSelectedSession
    ? t('restoringTask')
    : t('refreshingWorkbench');
  const showSessionListSkeleton = loadingSessions && sessions.length === 0;

  return (
    <div className="flex h-full w-full overflow-hidden bg-gray-50 text-[#202328] dark:bg-[#252c35] dark:text-[#d7dee8]">
      {/* ── Sidebar ── */}
      <div
        className={`flex h-[calc(100%_-_1.5rem)] flex-shrink-0 flex-col overflow-hidden rounded-2xl border bg-white shadow-[0_3px_12px_rgba(22,27,34,0.045)] transition-[width,margin,opacity] duration-200 dark:border-white/[0.08] dark:bg-[#303842] dark:shadow-[0_8px_24px_rgba(15,18,22,0.16)] ${
          sidebarCollapsed
            ? 'my-3 w-0 border-transparent opacity-0'
            : 'm-3 w-[282px] border-black/[0.07] opacity-100'
        }`}
        aria-label={t('managementTitle')}
      >
        {/* Header：始终显示标题、新建与搜索 */}
        <div className="flex-shrink-0 px-3 pb-2 pt-3.5">
          <div className="mb-2 flex h-8 items-center justify-between px-1">
            <div className="min-w-0">
              <strong className="text-sm font-semibold text-[#202328] dark:text-[#f0f3f7]">
                {t('managementTitle')}
              </strong>
              <span className="ml-2 text-[11px] text-[#7b8087] dark:text-[#9aa7b4]">
                {t('sessionCount', { count: sessions.length })}
              </span>
            </div>
          </div>

          <div className="space-y-0.5">
            <div className="relative h-[34px] rounded-lg transition-colors hover:bg-black/[0.04] dark:hover:bg-white/[0.06]">
              {creating
                ? <Loader2 className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-[#7b8087]" />
                : <PencilLine className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#7b8087] dark:text-[#9aa7b4]" />}
              <button
                onClick={handleStartNewSession}
                disabled={creating}
                className="h-full w-full rounded-lg border-0 bg-transparent pl-9 pr-3 text-left text-sm font-medium text-[#474b51] transition-colors hover:text-[#202328] disabled:cursor-not-allowed disabled:opacity-60 dark:text-[#c3ccd6] dark:hover:text-white"
              >
                {t('newSession')}
              </button>
            </div>

            <div className="relative h-[34px] rounded-lg transition-colors hover:bg-black/[0.04] focus-within:bg-black/[0.04] dark:hover:bg-white/[0.06] dark:focus-within:bg-white/[0.06]">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#7b8087] dark:text-[#9aa7b4]" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('filterConversations', 'Search tasks')}
                className="h-full w-full rounded-lg border-0 bg-transparent pl-9 pr-8 text-sm font-medium text-[#474b51] outline-none placeholder:text-[#474b51] focus:bg-transparent dark:text-[#c3ccd6] dark:placeholder:text-[#c3ccd6]"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="absolute right-1.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-md text-[#7b8087] transition-colors hover:bg-black/[0.065] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.08] dark:hover:text-white"
                  title={t('clearSearch')}
                  aria-label={t('clearSearch')}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Session list */}
        <div
          data-testid="session-list-scroll"
          className="session-sidebar-scrollbar min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 pb-[18px] pt-1.5"
        >
          {showSessionListSkeleton ? (
            <SessionListSkeleton />
          ) : (
          <div>
            <section className="group/projects-section">
                <div className="flex h-7 select-none items-center gap-1.5 px-2 text-xs font-semibold uppercase tracking-[0.02em] text-zinc-500 dark:text-[#8f9ba8]">
                  <button
                    type="button"
                    onClick={() => setProjectsSectionCollapsed((collapsed) => !collapsed)}
                    className="flex h-6 items-center gap-1 rounded-lg px-1 text-left transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:hover:bg-white/[0.06] dark:hover:text-white"
                    aria-label={t('toggleProjects')}
                  >
                    <span>{t('projectsSection')}</span>
                    {projectsSectionCollapsed
                      ? <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                      : <ChevronDown className="h-3.5 w-3.5 shrink-0" />}
                  </button>
                  <button
                    type="button"
                    onClick={handleOpenCreateProject}
                    className="ml-auto grid h-6 w-6 place-items-center rounded-lg text-[#8a8e94] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                    title={t('projectDialog.createTitle')}
                    aria-label={t('projectDialog.createTitle')}
                  >
                    <FolderPlus className="h-3.5 w-3.5" />
                  </button>
                </div>
                {!projectsSectionCollapsed && (
                  <div>
                {managedProjectSessionGroups.map((group) => {
                  const collapsed = collapsedProjectIds.has(group.id);
                  const isSelectedProject = selectedProjectId === group.id;
                  const sessionsCollapsedToFirstPage = collapsedLoadedSessionGroupIds.has(group.id);
                  const visibleProjectSessions = sessionsCollapsedToFirstPage
                    ? group.sessions.slice(0, sessionListPageSize)
                    : group.sessions;
                  const hasMoreRemoteProjectSessions = (
                    group.sessions.length < group.sessionCount
                    || hasMoreByProject[group.id]
                  );
                  const canShowMoreProjectSessions = sessionsCollapsedToFirstPage
                    || hasMoreRemoteProjectSessions;
                  const canCollapseProjectSessions = !sessionsCollapsedToFirstPage
                    && group.sessions.length > sessionListPageSize;
                  const persistedProject = projects.find((project) => project.id === group.id);
                  return (
                    <div key={group.id} className="group/project relative">
                      <div
                        className={`flex h-[34px] items-center gap-1 rounded-lg px-3 text-sm transition-colors ${
                          isSelectedProject
                            ? 'font-semibold text-[#202328] dark:text-white'
                            : 'font-medium text-[#474b51] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white'
                        }`}
                        title={group.worktree}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedProjectId(group.id);
                            toggleProjectCollapsed(group.id);
                          }}
                          className="flex h-full min-w-0 flex-1 items-center gap-2 rounded-lg text-left"
                          aria-label={t('selectProject', { project: group.label })}
                          aria-expanded={!collapsed}
                        >
                          <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-[#6f757c] dark:text-[#9aa7b4]" />
                          <span className="min-w-0 flex-1 truncate">{group.label}</span>
                          {group.isShared && (
                            <span className="inline-flex shrink-0 items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:border-blue-500/30 dark:bg-blue-950/30 dark:text-blue-300">
                              {t('sharedTag')}
                            </span>
                          )}
                          {group.pathStatus !== 'available' && (
                            <AlertTriangle
                              className="h-3.5 w-3.5 shrink-0 text-amber-500"
                              aria-label={t('projectPathUnavailable')}
                            />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleCreateSessionInProject(group.id);
                          }}
                          disabled={creating || !group.canWrite || group.pathStatus !== 'available'}
                          className={`grid h-[26px] w-[26px] place-items-center rounded-lg text-[#7b8087] transition-all hover:bg-black/[0.065] hover:text-[#202328] disabled:cursor-not-allowed disabled:opacity-40 dark:text-[#9aa7b4] dark:hover:bg-white/[0.08] dark:hover:text-white ${
                            isSelectedProject ? 'opacity-100' : 'opacity-0 group-hover/project:opacity-100 group-focus-within/project:opacity-100'
                          }`}
                          title={t('createSessionInProject', { project: group.label })}
                          aria-label={t('createSessionInProject', { project: group.label })}
                        >
                          {creating && isSelectedProject ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                        </button>
                        {persistedProject && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setOpenProjectMenuId((current) => current === group.id ? null : group.id);
                            }}
                            className={`grid h-[26px] w-[26px] place-items-center rounded-lg text-[#7b8087] transition-all hover:bg-black/[0.065] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.08] dark:hover:text-white ${
                              openProjectMenuId === group.id || isSelectedProject
                                ? 'opacity-100'
                                : 'opacity-0 group-hover/project:opacity-100 group-focus-within/project:opacity-100'
                            }`}
                            title={t('projectActions')}
                            aria-label={t('projectActions')}
                            aria-expanded={openProjectMenuId === group.id}
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <span className="min-w-[18px] shrink-0 text-center text-[12px] font-normal tabular-nums text-[#858a91] dark:text-[#8f9ba8]">
                          {group.sessionCount}
                        </span>
                      </div>
                      {persistedProject && openProjectMenuId === group.id && (
                        <div
                          className="absolute right-7 top-8 z-30 grid w-[140px] gap-0.5 rounded-[10px] border border-black/[0.11] bg-[#fdfdfc] p-1 shadow-[0_8px_24px_rgba(22,27,34,0.10)] dark:border-white/[0.10] dark:bg-[#303842] dark:shadow-[0_12px_32px_rgba(15,18,22,0.35)]"
                          role="menu"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => void handleCopyProjectPath(group)}
                            className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                          >
                            <Copy className="h-3.5 w-3.5" />
                            {t('projectDialog.copyPathAction')}
                          </button>
                          {group.canWrite && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => void handleShareProject(group, !group.isShared)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                            >
                              <Share2 className="h-3.5 w-3.5" />
                              {t(group.isShared ? 'unshareAction' : 'shareAction')}
                            </button>
                          )}
                          {group.canWrite && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => handleOpenRenameProject(group)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                            >
                              <PencilLine className="h-3.5 w-3.5" />
                              {t('projectDialog.renameAction')}
                            </button>
                          )}
                          {group.canDelete && (
                            <div className="mx-2 my-0.5 border-t border-black/[0.07] dark:border-white/[0.08]" />
                          )}
                          {group.canDelete && (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => handleOpenDeleteProject(group)}
                              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#c33c36] transition-colors hover:bg-[#fff0ef] hover:text-[#a92520] disabled:cursor-not-allowed disabled:opacity-40 dark:text-red-300 dark:hover:bg-red-500/10 dark:hover:text-red-200"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                              {t('projectDialog.deleteAction')}
                            </button>
                          )}
                        </div>
                      )}
                      {!collapsed && (
                        <div className="mb-1 mt-0.5">
                          {group.sessions.length > 0 ? (
                            visibleProjectSessions.map((session) => (
                              <SessionSidebarItem
                                key={session.id}
                                session={session}
                                nested
                                selected={selectedSessionId === session.id}
                                selectMode={selectMode}
                                checked={checkedIds.has(session.id)}
                                menuOpen={openMenuSessionId === session.id}
                                running={runningSessionIds.has(session.id)}
                                renaming={renamingSessionId === session.id}
                                renameValue={renameValue}
                                renameSubmitting={renameSubmitting}
                                language={i18n.language}
                                relativeTimeClock={relativeTimeClock}
                                t={t}
                                renameInputRef={renameInputRef}
                                onSelect={handleSelectSessionRow}
                                onToggleCheck={handleToggleCheck}
                                onRenameValueChange={setRenameValue}
                                onSubmitRename={handleSubmitRename}
                                onCancelRename={handleCancelRename}
                                onToggleMenu={handleToggleSessionMenu}
                              />
                            ))
                          ) : (
                            <div className="ml-[19px] px-2.5 py-2 text-[11px] text-[#a0a4aa] dark:text-[#8f9ba8]">
                              {t('noProjectSessions')}
                            </div>
                          )}
                          {(canShowMoreProjectSessions || canCollapseProjectSessions) && (
                            <div className="ml-[19px] flex h-7 items-center justify-center gap-2 py-0.5">
                              {canShowMoreProjectSessions && (
                              <button
                                type="button"
                                onClick={() => {
                                  if (sessionsCollapsedToFirstPage) {
                                    setLoadedSessionGroupCollapsed(group.id, false);
                                    return;
                                  }
                                  void loadMoreSessions(group.id);
                                }}
                                disabled={loadingMoreProjectIds.has(group.id)}
                                className="flex h-7 items-center justify-center gap-1 rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                              >
                                {loadingMoreProjectIds.has(group.id) && (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                )}
                                {t('loadMore')}
                              </button>
                              )}
                              {canCollapseProjectSessions && (
                                <button
                                  type="button"
                                  onClick={() => setLoadedSessionGroupCollapsed(group.id, true)}
                                  className="flex h-7 items-center justify-center rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                                >
                                  {t('collapseLoaded')}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
                  </div>
                )}
              </section>
              {taskSessionGroup && (
                <section className="group/tasks-section mt-3">
                  <div className="flex h-7 select-none items-center gap-1 px-2 text-xs font-semibold uppercase tracking-[0.02em] text-zinc-500 dark:text-[#8f9ba8]">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedProjectId(taskSessionGroup.id);
                        toggleProjectCollapsed(taskSessionGroup.id);
                      }}
                      className="flex h-6 min-w-0 items-center truncate rounded-lg px-1 text-left transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:hover:bg-white/[0.06] dark:hover:text-white"
                      aria-label={t('selectTasks')}
                    >
                      {t('tasksSection')}
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        toggleProjectCollapsed(taskSessionGroup.id);
                      }}
                      className="mr-auto grid h-6 w-6 place-items-center rounded-lg text-[#8a8e94] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                      aria-label={t('toggleTasks')}
                    >
                      {taskGroupCollapsed
                        ? <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                        : <ChevronDown className="h-3.5 w-3.5 shrink-0" />}
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleCreateSession(TASK_SESSION_GROUP_ID);
                      }}
                      disabled={creating || taskSessionGroup.pathStatus !== 'available'}
                      className="grid h-6 w-6 place-items-center rounded-lg text-[#8a8e94] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                      title={t('createTaskSession')}
                      aria-label={t('createTaskSession')}
                    >
                      {creating && taskGroupSelected
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <Plus className="h-3 w-3" />}
                    </button>
                    <span className="min-w-[18px] shrink-0 text-center text-[12px] font-normal tabular-nums text-[#858a91] dark:text-[#8f9ba8]">
                      {taskSessionGroup.sessionCount}
                    </span>
                  </div>
                  {!taskGroupCollapsed && (
                    <div className="mt-0.5">
                      {visibleTaskSessions.map((session) => (
                        <SessionSidebarItem
                          key={session.id}
                          session={session}
                          selected={selectedSessionId === session.id}
                          selectMode={selectMode}
                          checked={checkedIds.has(session.id)}
                          menuOpen={openMenuSessionId === session.id}
                          running={runningSessionIds.has(session.id)}
                          renaming={renamingSessionId === session.id}
                          renameValue={renameValue}
                          renameSubmitting={renameSubmitting}
                          language={i18n.language}
                          relativeTimeClock={relativeTimeClock}
                          t={t}
                          renameInputRef={renameInputRef}
                          onSelect={handleSelectSessionRow}
                          onToggleCheck={handleToggleCheck}
                          onRenameValueChange={setRenameValue}
                          onSubmitRename={handleSubmitRename}
                          onCancelRename={handleCancelRename}
                          onToggleMenu={handleToggleSessionMenu}
                        />
                      ))}
                      {(canShowMoreTaskSessions || canCollapseTaskSessions) && (
                        <div className="flex h-7 items-center justify-center gap-2 py-0.5">
                          {canShowMoreTaskSessions && (
                          <button
                            type="button"
                            onClick={() => {
                              if (taskSessionsCollapsedToFirstPage) {
                                setLoadedSessionGroupCollapsed(taskSessionGroup.id, false);
                                return;
                              }
                              void loadMoreSessions(taskSessionGroup.id);
                            }}
                            disabled={loadingMoreProjectIds.has(taskSessionGroup.id)}
                            className="flex h-7 items-center justify-center gap-1 rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                          >
                            {loadingMoreProjectIds.has(taskSessionGroup.id) && (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            )}
                            {t('loadMore')}
                          </button>
                          )}
                          {canCollapseTaskSessions && (
                            <button
                              type="button"
                              onClick={() => setLoadedSessionGroupCollapsed(taskSessionGroup.id, true)}
                              className="flex h-7 items-center justify-center rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                            >
                              {t('collapseLoaded')}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </section>
              )}
          </div>
          )}
        </div>

        {/* Bottom：批量操作栏 / 批量选择入口 */}
        {sessions.length > 0 && (
          <div className="flex min-h-11 flex-shrink-0 items-center justify-center border-t border-black/[0.07] px-3 dark:border-white/[0.08]">
            {selectMode ? (
              <div className="grid grid-cols-3 gap-1.5">
                <button
                  onClick={handleSelectAll}
                  className="grid h-[30px] w-[30px] place-items-center rounded-lg text-blue-600 transition-colors hover:bg-blue-50 dark:text-blue-300 dark:hover:bg-blue-500/10"
                  title={checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
                  aria-label={checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
                >
                  <CheckSquare className="h-[15px] w-[15px]" />
                </button>
                <button
                  onClick={handleExitSelectMode}
                  className="grid h-[30px] w-[30px] place-items-center rounded-lg text-[#60656b] transition-colors hover:bg-black/[0.04] dark:text-[#b8c2cc] dark:hover:bg-white/[0.06]"
                  title={t('cancelSelect')}
                  aria-label={t('cancelSelect')}
                >
                  <X className="h-[15px] w-[15px]" />
                </button>
                <button
                  onClick={handleBatchDelete}
                  disabled={checkedIds.size === 0 || batchDeleting}
                  className="grid h-[30px] w-[30px] place-items-center rounded-lg text-red-600 transition-colors hover:bg-red-50 disabled:cursor-default disabled:opacity-35 dark:text-red-300 dark:hover:bg-red-500/10"
                  title={t('deleteSelected', { count: checkedIds.size })}
                  aria-label={t('deleteSelected', { count: checkedIds.size })}
                >
                  {batchDeleting ? <Loader2 className="h-[15px] w-[15px] animate-spin" /> : <Trash2 className="h-[15px] w-[15px]" />}
                </button>
              </div>
            ) : (
              <button
                onClick={handleEnterSelectMode}
                className="grid h-[30px] w-[30px] place-items-center rounded-lg text-[#7b8087] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.06] dark:hover:text-white"
                title={t('selectMode')}
                aria-label={t('selectMode')}
              >
                <CheckSquare className="h-[15px] w-[15px]" />
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Main area ── */}
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-gray-50 dark:bg-[#252c35]">
        {/* Header */}
        <div className="relative flex h-[52px] flex-shrink-0 items-center gap-2 px-4 text-[13px]">
          <div className="shrink-0">
            <button
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
              className="grid h-[30px] w-[30px] place-items-center rounded-lg border-0 bg-transparent text-[#7b8087] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.06] dark:hover:text-white"
              title={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
              aria-label={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
            >
              {sidebarCollapsed ? <PanelLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
          </div>

          <div className="flex min-w-0 items-center">
            <h2 className="truncate text-sm font-semibold text-[#555a61] dark:text-[#c3ccd6]">
              {selectedSession?.title || t('newSession')}
            </h2>
          </div>

          {workbenchRefreshing && (
            <WorkbenchRefreshStatus label={workbenchRefreshLabel} />
          )}
        </div>

        {/* Chat — powered by unified SessionChat */}
        {resolvingSelectedSession ? (
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
          mentionAgents={chatAgents}
          className="flex-1 min-h-0"
          composerTextareaMinHeight={56}
          initialMessage={pendingInitialMessage}
          initialDisplayText={pendingInitialDisplayText}
          onInitialMessageConsumed={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
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
          contextWindowTokens={effectiveModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen
              onSuggestion={setInput}
              onAlertOperationsSetup={() => void handleAlertOperationsSetup()}
              alertOperationsBusy={installingSocWorkspace}
            />
          )}
          toolbarSlot={
            <>
            {!activeChatSessionId && (
              <div className="relative" data-project-selector>
                <button
                  type="button"
                  onClick={() => {
                    setShowProjectOptions((open) => !open);
                    setShowAgentOptions(false);
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
                        className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                      >
                        <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-[#7b8087] dark:text-[#9aa7b4]" />
                        <span className="min-w-0 flex-1 truncate">{group.label}</span>
                        {selectedProjectId === group.id && <Check className="h-3.5 w-3.5 shrink-0 text-[#4f92e8]" />}
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
                      className="flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
                    >
                      <X className="h-3.5 w-3.5 shrink-0 text-[#7b8087] dark:text-[#9aa7b4]" />
                      <span className="min-w-0 flex-1 truncate">{t('projectPicker.none')}</span>
                      {selectedProjectId === TASK_SESSION_GROUP_ID && <Check className="h-3.5 w-3.5 shrink-0 text-[#4f92e8]" />}
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
            <div className="relative" data-agent-selector>
              <button
                type="button"
                onClick={() => {
                  setShowAgentOptions(!showAgentOptions);
                  setShowProjectOptions(false);
                  setShowModelOptions(false);
                }}
                className="flex h-7 w-auto max-w-[150px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                title={t('agentPicker.title')}
              >
                <Bot className="h-3 w-3 shrink-0" />
                <span className="truncate font-medium">
                  {selectedAgentInfo ? getAgentDisplayName(selectedAgentInfo, i18n.language) : formatAgentName(selectedAgent)}
                </span>
                <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showAgentOptions ? 'rotate-180' : ''}`} />
              </button>
              {showAgentOptions && (
                <div className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
                  <div className="flex items-center justify-between gap-2 border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('agentPicker.title')}</div>
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
                    <div className="inline-flex shrink-0 items-center rounded-md border border-zinc-200 bg-white p-0.5 text-[10px] dark:border-zinc-800 dark:bg-zinc-950">
                      {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                        <button
                          key={filter}
                          type="button"
                          onClick={() => setAgentSourceFilter(filter)}
                          className={`rounded px-1.5 py-0.5 transition-colors ${
                            agentSourceFilter === filter
                              ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
                              : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
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
                          onClick={() => { setSelectedAgent(agent.name); setShowAgentOptions(false); }}
                          className={`w-full min-w-0 rounded-md px-2 py-1.5 text-left transition-colors ${
                            selectedAgent === agent.name
                              ? 'bg-zinc-50 text-zinc-900 shadow-[inset_2px_0_0_#a1a1aa] dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-[inset_2px_0_0_#539bf5]'
                              : 'hover:bg-zinc-50 text-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <Bot className={`h-3 w-3 shrink-0 ${selectedAgent === agent.name ? 'text-zinc-600 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                            <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">
                              {displayName}
                            </span>
                            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium ${
                              agent.mode === 'primary'
                                ? 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                                : agent.native
                                  ? 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                                  : 'bg-teal-50 text-teal-600 dark:bg-teal-950/40 dark:text-teal-300'
                            }`}>
                              {agent.mode === 'primary'
                                ? t('agentPicker.badge.primary')
                                : agent.native
                                  ? t('agentPicker.badge.builtin')
                                  : t('agentPicker.badge.custom')}
                            </span>
                            <div className="ml-auto flex shrink-0 items-center gap-1">
                              {primaryDesc && (
                                <span
                                  className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-700"
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
                            </div>
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
            </>
          }
          centerToolbarSlot={
            <div ref={modelSelectorRef} className="relative" data-model-selector>
              <button
                type="button"
                onClick={() => {
                  if (!showModelOptions) updateModelMenuLeftOffset();
                  setShowModelOptions(!showModelOptions);
                  setShowProjectOptions(false);
                  setShowAgentOptions(false);
                }}
                disabled={loadingProviders || loadingEnabledModels || chatModelOptions.length === 0}
                className="flex h-7 w-[132px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
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
                <div
                  className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30"
                  style={{ transform: `translateX(${modelMenuLeftOffset}px)` }}
                >
                  <div className="border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
                    <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('modelPicker.title')}</div>
                    <div className="truncate text-[10px] text-zinc-400 dark:text-zinc-500">{t('modelPicker.hint')}</div>
                  </div>
                  <div className="h-[15.5rem] overflow-y-auto p-1.5">
                    {loadingProviders || loadingEnabledModels ? (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
                    ) : (
                      <>
                        <div className="mb-1 border-b border-zinc-100 pb-1.5 dark:border-zinc-800">
                          <button
                            type="button"
                            onClick={() => void handleSelectAutoModel()}
                            disabled={!canSelectAuto}
                            className={`w-full rounded-md px-2 py-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                              selectedModelAuto
                                ? 'bg-zinc-50 text-zinc-900 shadow-[inset_2px_0_0_#a1a1aa] dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-[inset_2px_0_0_#539bf5]'
                                : 'text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
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
                            </div>
                          </button>
                        </div>
                        {groupedChatModelOptions.length > 0 ? groupedChatModelOptions.map((group) => (
                        <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-white/95 px-1.5 py-1 text-[10px] font-semibold text-zinc-500 backdrop-blur dark:bg-zinc-900/95 dark:text-zinc-400">
                            <span className="truncate">{group.providerName}</span>
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
                                className={`w-full rounded-md px-2 py-1.5 text-left transition-colors ${
                                  selectedModelOption?.key === option.key
                                    ? 'bg-zinc-50 text-zinc-900 shadow-[inset_2px_0_0_#a1a1aa] dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-[inset_2px_0_0_#539bf5]'
                                    : 'text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
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
                  </div>
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
                </div>
              )}
            </div>
          }
          />
        )}
      </div>

      {projectDialogMode && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[#1e23283d] px-6 backdrop-blur-[2px]">
          <div className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-black/[0.11] bg-[#fdfdfc] shadow-[0_8px_24px_rgba(22,27,34,0.10)] dark:border-white/[0.10] dark:bg-[#303842] dark:shadow-[0_12px_32px_rgba(15,18,22,0.35)]">
            <div className="border-b border-black/[0.07] px-4 py-[13px] dark:border-white/[0.08]">
              <h3 className="text-sm font-semibold text-[#202328] dark:text-[#f0f3f7]">
                {projectDialogMode === 'create' ? t('projectDialog.createTitle') : t('projectDialog.renameTitle')}
              </h3>
            </div>
            <div className="space-y-3 px-4 py-4 text-[13px] text-[#676c73] dark:text-[#b8c2cc]">
              <label className="block text-[11px] font-semibold text-[#777c82] dark:text-[#9aa7b4]" htmlFor="session-project-name">
                {t('projectDialog.nameLabel')}
              </label>
              <input
                id="session-project-name"
                value={projectNameValue}
                onChange={(event) => {
                  setProjectNameValue(event.target.value);
                  setProjectNameManuallyEdited(true);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    if (
                      event.nativeEvent.isComposing
                      || (projectDialogMode === 'create'
                        && !projectWorktreeValue.trim()
                        && !folderBrowser?.path.trim())
                    ) {
                      return;
                    }
                    void handleSubmitProject();
                  }
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    handleCloseProjectDialog();
                  }
                }}
                disabled={projectSubmitting}
                placeholder={t('projectDialog.namePlaceholder')}
                className="h-9 w-full rounded-[10px] border border-black/[0.11] bg-white px-2.5 text-[13px] text-[#202328] outline-none transition-colors placeholder:text-[#969aa0] focus:border-[#4f92e8] dark:border-white/[0.10] dark:bg-[#252c35] dark:text-white dark:placeholder:text-[#8f9ba8]"
                autoFocus
              />
              {projectDialogMode === 'create' && (
                <>
                  <label className="block pt-1 text-[11px] font-semibold text-[#777c82] dark:text-[#9aa7b4]" htmlFor="session-project-worktree">
                    {t('projectDialog.folderLabel')}
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="session-project-worktree"
                      value={projectWorktreeValue}
                      onChange={(event) => {
                        folderBrowserRequestIdRef.current += 1;
                        folderBrowserInputPathRef.current = null;
                        setFolderBrowserLoading(false);
                        setProjectWorktreeValue(event.target.value);
                        if (!projectNameManuallyEdited) {
                          setProjectNameValue(getPathBasename(event.target.value));
                        }
                      }}
                      disabled={projectSubmitting}
                      placeholder={t('projectDialog.folderPlaceholder')}
                      className="h-9 min-w-0 flex-1 rounded-[10px] border border-black/[0.11] bg-white px-2.5 font-mono text-xs text-[#202328] outline-none transition-colors placeholder:text-[#969aa0] focus:border-[#4f92e8] dark:border-white/[0.10] dark:bg-[#252c35] dark:text-white dark:placeholder:text-[#8f9ba8]"
                    />
                    <button
                      type="button"
                      onClick={() => void loadFolderBrowser(projectWorktreeValue)}
                      disabled={projectSubmitting || folderBrowserLoading}
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-[10px] border border-black/[0.11] px-3 text-xs font-medium text-[#60656b] transition-colors hover:bg-black/[0.04] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/[0.10] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06]"
                    >
                      {folderBrowserLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen className="h-3.5 w-3.5" />}
                      {t('projectDialog.chooseFolder')}
                    </button>
                  </div>
                  {folderBrowser && (
                    <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                      <div className="flex items-center gap-1 border-b border-zinc-100 bg-zinc-50 px-2 py-1.5 dark:border-zinc-800 dark:bg-zinc-900">
                        <button
                          type="button"
                          onClick={() => folderBrowser.parent && void loadFolderBrowser(folderBrowser.parent)}
                          disabled={!folderBrowser.parent || folderBrowserLoading}
                          className="rounded p-1 text-zinc-500 hover:bg-zinc-200 disabled:opacity-30 dark:hover:bg-zinc-800"
                          title={t('projectDialog.parentFolder')}
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </button>
                        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500" title={folderBrowser.path}>
                          {folderBrowser.path}
                        </span>
                        <button
                          type="button"
                          onClick={() => handleSelectProjectFolder(folderBrowser.path)}
                          className="rounded bg-zinc-900 px-2 py-1 text-[11px] font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-300"
                        >
                          {t('projectDialog.selectCurrentFolder')}
                        </button>
                      </div>
                      <div className="flex gap-1 overflow-x-auto border-b border-zinc-100 px-2 py-1 dark:border-zinc-800">
                        {folderBrowser.roots.map((root) => (
                          <button
                            key={root.path}
                            type="button"
                            onClick={() => void loadFolderBrowser(root.path)}
                            className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 text-[11px] text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
                            title={root.path}
                          >
                            <HardDrive className="h-3 w-3" />
                            {root.name}
                          </button>
                        ))}
                      </div>
                      <div className="max-h-48 overflow-y-auto p-1">
                        {folderBrowser.entries.length > 0 ? folderBrowser.entries.map((entry) => (
                          <button
                            key={entry.path}
                            type="button"
                            onClick={() => void loadFolderBrowser(entry.path)}
                            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
                            title={entry.path}
                          >
                            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                            <span className="truncate">{entry.name}</span>
                          </button>
                        )) : (
                          <div className="px-2 py-5 text-center text-xs text-zinc-400">
                            {t('projectDialog.noSubfolders')}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-black/[0.07] px-4 py-[13px] dark:border-white/[0.08]">
              <button
                type="button"
                onClick={handleCloseProjectDialog}
                disabled={projectSubmitting}
                className="h-8 min-w-[66px] rounded-[10px] bg-[#f1f1ef] px-3 text-xs text-[#5d6269] transition-colors hover:bg-[#e8e8e5] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/[0.07] dark:text-[#c3ccd6] dark:hover:bg-white/[0.10]"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleSubmitProject()}
                disabled={projectSubmitting}
                className="inline-flex h-8 min-w-[66px] items-center justify-center rounded-[10px] bg-[#24272b] px-3 text-xs font-medium text-white transition-colors hover:bg-[#35393e] disabled:cursor-not-allowed disabled:opacity-60 dark:bg-[#eef2f6] dark:text-[#252c35] dark:hover:bg-white"
              >
                {projectSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : t('save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {projectPendingDelete && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[#1e23283d] px-6 backdrop-blur-[2px]">
          <div className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-black/[0.11] bg-[#fdfdfc] shadow-[0_8px_24px_rgba(22,27,34,0.10)] dark:border-white/[0.10] dark:bg-[#303842] dark:shadow-[0_12px_32px_rgba(15,18,22,0.35)]">
            <div className="border-b border-black/[0.07] px-4 py-[13px] dark:border-white/[0.08]">
              <h3 className="text-sm font-semibold text-[#202328] dark:text-[#f0f3f7]">
                {t('projectDialog.deleteTitle')}
              </h3>
            </div>
            <div className="px-4 py-4 text-[13px] leading-6 text-[#676c73] dark:text-[#b8c2cc]">
              {t('projectDialog.deleteDescription', { project: projectPendingDelete.label })}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-black/[0.07] px-4 py-[13px] dark:border-white/[0.08]">
              <button
                type="button"
                onClick={() => setProjectPendingDelete(null)}
                disabled={projectDeleting}
                className="h-8 min-w-[66px] rounded-[10px] bg-[#f1f1ef] px-3 text-xs text-[#5d6269] transition-colors hover:bg-[#e8e8e5] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/[0.07] dark:text-[#c3ccd6] dark:hover:bg-white/[0.10]"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteProject()}
                disabled={projectDeleting}
                className="inline-flex h-8 min-w-[66px] items-center justify-center gap-1.5 rounded-[10px] bg-[#dc3f39] px-3 text-xs font-medium text-white transition-colors hover:bg-[#c9342f] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {projectDeleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                {t('projectDialog.confirmDelete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {selectorTooltip && (
        <div
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

      {suiteInstallProgress && (
        <SuiteInstallProgressPanel
          progress={suiteInstallProgress}
          language={i18n.language}
          onClose={() => setSuiteInstallProgress(null)}
        />
      )}

      {/* Three-dot dropdown — rendered outside sidebar to avoid overflow:hidden clipping */}
      {openMenuSessionId && menuAnchor && (() => {
        const sid = openMenuSessionId;
        const session = sessions.find(s => s.id === sid);
        if (!session) return null;
        return (
          <div
            className="fixed z-50 grid w-[132px] gap-0.5 overflow-hidden rounded-[10px] border border-black/[0.11] bg-[#fdfdfc] p-1 shadow-[0_8px_24px_rgba(22,27,34,0.10)] dark:border-white/[0.10] dark:bg-[#303842] dark:shadow-[0_12px_32px_rgba(15,18,22,0.35)]"
            style={{ top: menuAnchor.top, right: menuAnchor.right }}
            data-session-menu-portal
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
            >
              <PencilLine className="w-3.5 h-3.5" />
              <span>{t('rename')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              disabled={downloadingSessionId === session.id}
              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
            >
              <Download className="w-3.5 h-3.5" />
              <span>{t('downloadJson')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleShareSession(session.id, !session.isShared); }}
              disabled={session.canWrite === false}
              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#4e5359] transition-colors hover:bg-black/[0.04] hover:text-[#202328] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#c3ccd6] dark:hover:bg-white/[0.06] dark:hover:text-white"
            >
              <Share2 className="w-3.5 h-3.5" />
              <span>{session.isShared ? t('unshareAction') : t('shareAction')}</span>
            </button>
            <div className="mx-2 my-0.5 border-t border-black/[0.07] dark:border-white/[0.08]" />
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleDeleteSession(session.id); }}
              disabled={session.canDelete === false}
              className="flex h-[30px] w-full items-center gap-2 rounded-lg px-2 text-left text-xs text-[#c33c36] transition-colors hover:bg-[#fff0ef] hover:text-[#a92520] disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-300 dark:hover:bg-red-500/10 dark:hover:text-red-200"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>{t('deleteAction')}</span>
            </button>
          </div>
        );
      })()}
    </div>
  );
}

// ── Welcome Screen (shown when no messages) ──

function WelcomeScreen({
  onSuggestion,
  onAlertOperationsSetup,
  alertOperationsBusy,
}: {
  onSuggestion: (text: string) => void;
  onAlertOperationsSetup: () => void;
  alertOperationsBusy: boolean;
}) {
  const { t } = useTranslation('session');
  return (
    <div className="w-full max-w-[660px] px-9 text-center">
      <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-slate-800 text-white shadow-[0_3px_10px_rgba(30,41,59,0.10)] dark:bg-[#46515e]">
        <Sparkles className="h-[30px] w-[30px]" strokeWidth={1.7} />
      </div>
      <h3 className="text-[22px] font-semibold tracking-[-0.01em] text-[#1f2937] dark:text-[#f0f3f7]">{t('welcome.title')}</h3>
      <p className="mt-3 text-sm leading-6 text-[#647084] dark:text-[#aeb9c5]">{t('welcome.description')}</p>

      <div className="mt-6 flex flex-wrap justify-center gap-2.5">
        <button
          onClick={onAlertOperationsSetup}
          disabled={alertOperationsBusy}
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-zinc-50 px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-white hover:text-[#111827] disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
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
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-zinc-50 px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-white hover:text-[#111827] dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
        >
          <Search className="h-[18px] w-[18px] text-[#f4511e]" />
          <span>{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-zinc-50 px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-white hover:text-[#111827] dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
        >
          <AlertTriangle className="h-[18px] w-[18px] text-amber-500" />
          <span>{t('welcome.incidentResponse')}</span>
        </button>
      </div>
    </div>
  );
}
