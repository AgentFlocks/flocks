import { memo, useState, useEffect, useMemo, useCallback, useRef, type RefObject } from 'react';
import {
  MessageSquare, Plus, Trash2,
  ChevronDown, ChevronRight, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Share2, Cpu, Info,
  FolderGit2, FolderPlus, FolderOpen, Copy, ArrowUp, HardDrive,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat, { buildInstructionDisplayText, type PromptDisplayOptions, type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
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
import { useEnabledChatModelDefinitions, useResolvedDefaultModel } from '@/hooks/useChatModelResources';
import client from '@/api/client';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { formatSessionDate } from '@/utils/time';
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
  isDefault: boolean;
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
  renaming: boolean;
  renameValue: string;
  renameSubmitting: boolean;
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
  renaming,
  renameValue,
  renameSubmitting,
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
      className={`group relative mb-1 cursor-pointer border px-3 transition-all duration-150 ${
        nested ? 'ml-7 mr-2 rounded-lg py-2' : 'mx-2 rounded-xl py-2.5'
      } ${
        !selectMode && selected
          ? 'border-gray-300 bg-gray-100 shadow-sm dark:border-zinc-700 dark:bg-zinc-900 dark:shadow-none'
          : selectMode && checked
          ? 'bg-blue-50 border-blue-200 dark:border-blue-500/40 dark:bg-blue-950/30'
          : nested
            ? 'border-gray-100 hover:border-gray-200 hover:bg-gray-50 dark:border-zinc-800 dark:hover:bg-zinc-900'
            : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50 hover:shadow-sm dark:border-transparent dark:hover:border-zinc-800 dark:hover:bg-zinc-900 dark:hover:shadow-none'
      }`}
    >
      <div className="flex items-center gap-1.5 min-w-0 pr-7">
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
            <WorkflowIcon className="w-3 h-3 text-orange-400" />
          </span>
        )}
        {session.category === 'entity-config' && (
          <span title={t('configSession')} className="flex-shrink-0">
            <Settings2 className="w-3 h-3 text-purple-400" />
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
      {session.time?.updated && !renaming && (
        <p className="mt-1 text-xs text-gray-400 truncate pl-0.5 dark:text-zinc-500">
          {formatSessionDate(session.time.updated)}
        </p>
      )}
      {!selectMode && (
        <div className="absolute right-1.5 top-2" data-session-actions>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleMenu(session.id, e.currentTarget);
            }}
            title={t('moreActions')}
            aria-label={t('moreActions')}
            aria-expanded={menuOpen}
            className={`p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all dark:text-zinc-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 ${
              menuOpen ? 'opacity-100 text-gray-600 bg-gray-200 dark:bg-zinc-800 dark:text-zinc-200' : 'opacity-0 group-hover:opacity-100'
            }`}
          >
            <MoreHorizontal className="w-3.5 h-3.5" />
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
  prev.renaming === next.renaming &&
  prev.renameValue === next.renameValue &&
  prev.renameSubmitting === next.renameSubmitting &&
  prev.t === next.t
));

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
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [showModelOptions, setShowModelOptions] = useState(false);
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [creating, setCreating] = useState(false);
  const [installingSocWorkspace, setInstallingSocWorkspace] = useState(false);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [pendingInitialDisplayText, setPendingInitialDisplayText] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(collapsedProjectsStorageKey) ?? '[]');
      return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === 'string') : []);
    } catch {
      return new Set();
    }
  });
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
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSubmitInFlightRef = useRef(false);
  const projectSubmitInFlightRef = useRef(false);
  const folderBrowserRequestIdRef = useRef(0);
  const folderBrowserInputPathRef = useRef<string | null>(null);
  const sessionUpdateRefetchTimerRef = useRef<number | null>(null);
  const projectListRequestSeqRef = useRef(0);
  const toast = useToast();

  const sessionProjectIds = useMemo(
    () => projects.map((project) => project.id),
    [projects],
  );
  const {
    sessions,
    loading: loadingSessions,
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
    pageSize: projects.length >= 2
      ? MULTI_PROJECT_SESSION_PAGE_SIZE
      : SINGLE_PROJECT_SESSION_PAGE_SIZE,
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
  const selectedModelOption = useMemo(
    () => chatModelOptions.find((option) => option.key === selectedModelKey) ?? (selectedModelKey ? null : chatModelOptions[0] ?? null),
    [chatModelOptions, selectedModelKey],
  );
  const selectedPromptModel = selectedModelOption
    ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
    : null;
  const {
    data: resolvedDefaultModel,
    initialized: resolvedDefaultModelInitialized,
  } = useResolvedDefaultModel(chatModelOptions.length > 0 && !hasPinnedModelOption);
  const effectiveSupportsVision = selectedModelOption?.supportsVision ?? supportsVision;

  const toggleProjectCollapsed = useCallback((projectId: string) => {
    setCollapsedProjectIds(prev => {
      const next = new Set(prev);
      next.has(projectId) ? next.delete(projectId) : next.add(projectId);
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

  const defaultProjectId = useMemo(
    () => projects.find((project) => project.isDefault)?.id ?? projects[0]?.id ?? null,
    [projects],
  );

  const projectSessionGroups = useMemo<ProjectSessionGroup[]>(() => {
    const registeredIds = new Set(projects.map((project) => project.id));
    const sessionsByProject = new Map<string, Session[]>();
    sessions.forEach((session) => {
      const responseProjectId = session.effectiveProjectID || session.projectID;
      const projectId = registeredIds.has(responseProjectId)
        ? responseProjectId
        : defaultProjectId;
      if (!projectId) return;
      const groupSessions = sessionsByProject.get(projectId) ?? [];
      groupSessions.push(session);
      sessionsByProject.set(projectId, groupSessions);
    });

    return projects.map((project) => {
      const isDefault = project.isDefault ?? project.id === defaultProjectId;
      const projectSessions = sessionsByProject.get(project.id) ?? [];
      return {
        id: project.id,
        label: isDefault ? t('defaultProjectName') : getProjectLabel(project),
        worktree: project.worktree,
        sessions: projectSessions,
        sessionCount: searchQuery.trim()
          ? project.matchedSessionCount ?? projectSessions.length
          : project.sessionCount ?? projectSessions.length,
        isDefault,
        pathStatus: project.pathStatus ?? 'available',
        canWrite: project.canWrite ?? true,
        canDelete: project.canDelete ?? true,
        isShared: project.isShared ?? false,
      };
    })
      .filter((group) => {
        if (!searchQuery.trim()) return true;
        if (group.isDefault) return true;
        const project = projects.find((item) => item.id === group.id);
        return (project?.matchedSessionCount ?? group.sessions.length) > 0 || group.id === selectedProjectId;
      })
      .sort((a, b) => {
        if (a.isDefault !== b.isDefault) return a.isDefault ? -1 : 1;
        const aLatest = projects.find((project) => project.id === a.id)?.lastActivityAt ?? a.sessions[0]?.time?.updated ?? 0;
        const bLatest = projects.find((project) => project.id === b.id)?.lastActivityAt ?? b.sessions[0]?.time?.updated ?? 0;
        if (aLatest !== bLatest) return bLatest - aLatest;
        return a.label.localeCompare(b.label);
      });
  }, [defaultProjectId, projects, searchQuery, selectedProjectId, sessions, t]);

  const managedProjectSessionGroups = useMemo(
    () => projectSessionGroups.filter((group) => !group.isDefault),
    [projectSessionGroups],
  );
  const taskSessionGroup = useMemo(
    () => projectSessionGroups.find((group) => group.isDefault) ?? null,
    [projectSessionGroups],
  );
  const taskGroupCollapsed = taskSessionGroup
    ? collapsedProjectIds.has(taskSessionGroup.id)
    : false;
  const taskGroupSelected = taskSessionGroup?.id === selectedProjectId;
  const hasMoreTaskSessions = taskSessionGroup
    ? projects.length >= 2
      ? taskSessionGroup.sessions.length < taskSessionGroup.sessionCount
      : hasMoreByProject[taskSessionGroup.id]
    : false;

  const selectedProjectIDForCreate = selectedProjectId ?? defaultProjectId ?? projectSessionGroups[0]?.id ?? null;

  useEffect(() => {
    if (projectSessionGroups.length === 0) {
      setSelectedProjectId(null);
      return;
    }

    if (selectedProjectId && projectSessionGroups.some((group) => group.id === selectedProjectId)) {
      return;
    }

    let storedProjectId: string | null = null;
    try {
      storedProjectId = window.localStorage.getItem(activeProjectStorageKey);
    } catch {
      storedProjectId = null;
    }
    const fallbackProjectId = storedProjectId && projectSessionGroups.some((group) => group.id === storedProjectId)
      ? storedProjectId
      : defaultProjectId && projectSessionGroups.some((group) => group.id === defaultProjectId)
        ? defaultProjectId
        : projectSessionGroups[0].id;
    setSelectedProjectId(fallbackProjectId);
  }, [activeProjectStorageKey, defaultProjectId, projectSessionGroups, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId || !projects.some((project) => project.id === selectedProjectId)) return;
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
    const listResult = await client.get('/api/project', {
      params: { search: query.trim() || undefined },
    });
    if (requestSeq !== projectListRequestSeqRef.current) return;
    const nextProjects = Array.isArray(listResult.data) ? listResult.data : [];
    setProjects((currentProjects) => {
      if (!ensureProject?.id || nextProjects.some((project) => project.id === ensureProject.id)) {
        return nextProjects;
      }
      const currentProject = currentProjects.find((project) => project.id === ensureProject.id);
      return [{ ...currentProject, ...ensureProject }, ...nextProjects];
    });
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
    if (!showModelOptions) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-model-selector]')) setShowModelOptions(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [showModelOptions]);

  useEffect(() => {
    if (chatModelOptions.length === 0) {
      setSelectedModelKey(null);
      return;
    }

    if (hasPinnedModelOption && pinnedModelKey) {
      setSelectedModelKey(pinnedModelKey);
      return;
    }

    setSelectedModelKey(null);
    if (!resolvedDefaultModelInitialized) return;
    const defaultKey = resolvedDefaultModel
      ? makeModelKey(resolvedDefaultModel.providerID, resolvedDefaultModel.modelID)
      : null;
    const fallbackKey = chatModelOptions[0]?.key ?? null;
    setSelectedModelKey(defaultKey && chatModelOptions.some((option) => option.key === defaultKey) ? defaultKey : fallbackKey);
  }, [
    chatModelOptions,
    hasPinnedModelOption,
    pinnedModelKey,
    resolvedDefaultModel,
    resolvedDefaultModelInitialized,
    selectedSessionId,
  ]);

  useEffect(() => {
    if (loadingEnabledModels || chatModelOptions.length === 0 || !selectedModelKey) return;
    if (chatModelOptions.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(chatModelOptions[0].key);
  }, [chatModelOptions, loadingEnabledModels, selectedModelKey]);

  useEffect(() => {
    if (showAgentOptions || showModelOptions) return;
    setSelectorTooltip(null);
  }, [showAgentOptions, showModelOptions]);

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

  const handleCreateSession = useCallback(async (projectIdOverride?: string) => {
    if (creating) return;
    const projectID = projectIdOverride ?? selectedProjectIDForCreate;
    setCreating(true);
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(projectID ? { projectID } : {}),
      });
      addSession(response.data);
      await fetchProjects(undefined, searchQuery);
      setSelectedSessionFallback(response.data);
      if (projectID) {
        setSelectedProjectId(projectID);
        setCollapsedProjectIds(prev => {
          const next = new Set(prev);
          next.delete(projectID);
          return next;
        });
      }
      setSelectedAgent('rex');
      setSelectedModelKey(null);
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, selectedProjectIDForCreate, addSession, fetchProjects, searchQuery, toast, t]);

  const handleCreateSessionInProject = useCallback((projectId: string) => {
    void handleCreateSession(projectId);
  }, [handleCreateSession]);

  const handleSelectModel = useCallback(async (option: ChatModelOption) => {
    setSelectedModelKey(option.key);
    setShowModelOptions(false);
    if (!selectedSessionId) return;

    try {
      await sessionApi.update(selectedSessionId, {
        provider: option.providerID,
        model: option.modelID,
        model_pinned: true,
      });
      refetchSessions();
    } catch (err: any) {
      toast.error(t('chat.error', 'Error'), err.message);
    }
  }, [refetchSessions, selectedSessionId, toast, t]);

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
      });
      const newSessionId = response.data.id;

      addSession(response.data);
      await fetchProjects(undefined, searchQuery);
      setSelectedSessionFallback(response.data);
      setSelectedModelKey(null);
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      const effectiveAgent = agentOverride || selectedAgent || 'rex';
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (modelOverride) payload.model = modelOverride;
      if (options?.displayText) payload.displayText = options.displayText;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, fetchProjects, searchQuery, selectedAgent, selectedProjectIDForCreate, toast, t]);

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
        current === projectPendingDelete.id ? defaultProjectId : current
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
  }, [defaultProjectId, fetchProjects, projectDeleting, projectPendingDelete, refetchSessions, searchQuery, t, toast]);

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

  if (loadingSessions) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  return (
    <div className="h-full w-full flex overflow-hidden bg-gray-50 dark:bg-zinc-950">
      {/* ── Sidebar ── */}
      <div
        className={`bg-white border-r border-gray-100 flex flex-col transition-all duration-300 flex-shrink-0 h-full overflow-hidden dark:border-zinc-800 dark:bg-zinc-950 ${
          sidebarCollapsed ? 'w-0' : 'w-64'
        }`}
      >
        {/* Header：始终显示新建 + 搜索 */}
        <div className="px-3 pt-3 pb-2 flex-shrink-0 space-y-2">
          <div className="relative">
            {creating
              ? <Loader2 className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 animate-spin text-gray-400 pointer-events-none" />
              : <Plus className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none" />}
            <button
              onClick={() => void handleCreateSession()}
              disabled={creating}
              className="w-full pl-8 pr-3 py-2 text-left bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 hover:border-gray-300 shadow-sm hover:shadow transition-all disabled:opacity-60 disabled:cursor-not-allowed text-sm font-medium dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:border-zinc-700 dark:hover:bg-zinc-800"
            >
              {t('newSession')}
            </button>
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('filterConversations', 'Filter conversations...')}
              className="w-full pl-8 pr-3 py-1.5 text-sm bg-gray-100 rounded-lg border-0 outline-none focus:bg-gray-200 transition-colors placeholder:text-gray-400 text-gray-700 dark:bg-zinc-900 dark:text-zinc-200 dark:placeholder:text-zinc-600 dark:focus:bg-zinc-800"
            />
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-hide pb-2">
          {sessions.length === 0 && projectSessionGroups.length === 0 ? (
            <div className="text-center py-10 px-4 text-gray-400">
              <MessageSquare className="w-10 h-10 mx-auto mb-2 opacity-40" />
              <p className="text-sm">{t('noSessions')}</p>
            </div>
          ) : projectSessionGroups.length === 0 ? (
            <div className="text-center py-8 px-4 text-gray-400">
              <p className="text-sm">{t('noResults', 'No conversations found')}</p>
            </div>
          ) : (
            <div>
              <section className="group/projects-section">
                <div className="flex items-center justify-between px-4 pt-4 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide select-none dark:text-zinc-600">
                  <button
                    type="button"
                    onClick={() => setProjectsSectionCollapsed((collapsed) => !collapsed)}
                    className="flex items-center gap-1 rounded text-left transition-colors hover:text-gray-700 dark:hover:text-zinc-300"
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
                    className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-zinc-600 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
                    title={t('projectDialog.createTitle')}
                    aria-label={t('projectDialog.createTitle')}
                  >
                    <FolderPlus className="h-3.5 w-3.5" />
                  </button>
                </div>
                {!projectsSectionCollapsed && (
                  <div className="space-y-2">
                {managedProjectSessionGroups.map((group) => {
                  const collapsed = collapsedProjectIds.has(group.id);
                  const isDefaultProject = group.isDefault;
                  const isSelectedProject = selectedProjectId === group.id;
                  const hasMoreProjectSessions = projects.length >= 2
                    ? group.sessions.length < group.sessionCount
                    : hasMoreByProject[group.id];
                  const persistedProject = projects.find((project) => project.id === group.id);
                  return (
                    <div key={group.id} className="group/project relative">
                      <div
                        className={`mx-2 flex items-center gap-1 rounded-lg px-1.5 py-1 text-sm transition-colors ${
                          isSelectedProject
                            ? 'bg-gray-100 text-gray-900 dark:bg-zinc-900 dark:text-zinc-100'
                            : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100'
                        }`}
                        title={group.worktree}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedProjectId(group.id);
                            toggleProjectCollapsed(group.id);
                          }}
                          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-0.5 text-left"
                          aria-label={t('selectProject', { project: group.label })}
                          aria-expanded={!collapsed}
                        >
                          <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-gray-500 dark:text-zinc-500" />
                          <span className="min-w-0 flex-1 truncate font-medium">{group.label}</span>
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
                          className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
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
                            className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-700 dark:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                            title={t('projectActions')}
                            aria-label={t('projectActions')}
                            aria-expanded={openProjectMenuId === group.id}
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <span className="inline-flex min-w-5 shrink-0 items-center justify-center rounded-full bg-white/80 px-1.5 py-0.5 text-[10px] tabular-nums text-gray-400 dark:bg-zinc-800 dark:text-zinc-500">
                          {group.sessionCount}
                        </span>
                      </div>
                      {persistedProject && openProjectMenuId === group.id && (
                        <div
                          className="absolute right-8 top-8 z-30 min-w-28 rounded-md border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
                          role="menu"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => void handleCopyProjectPath(group)}
                            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                          >
                            <Copy className="h-3.5 w-3.5" />
                            {t('projectDialog.copyPathAction')}
                          </button>
                          {!isDefaultProject && group.canWrite && (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => void handleShareProject(group, !group.isShared)}
                            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                          >
                            <Share2 className="h-3.5 w-3.5" />
                            {t(group.isShared ? 'unshareAction' : 'shareAction')}
                          </button>
                          )}
                          {!isDefaultProject && group.canWrite && (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => handleOpenRenameProject(group)}
                            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                          >
                            <PencilLine className="h-3.5 w-3.5" />
                            {t('projectDialog.renameAction')}
                          </button>
                          )}
                          {!isDefaultProject && group.canDelete && (
                          <div className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />
                          )}
                          {!isDefaultProject && group.canDelete && (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => handleOpenDeleteProject(group)}
                            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 dark:text-red-300 dark:hover:bg-red-950/40"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {t('projectDialog.deleteAction')}
                          </button>
                          )}
                        </div>
                      )}
                      {!collapsed && (
                        <div className="mt-1.5">
                          {group.sessions.length > 0 ? (
                            group.sessions.map((session) => (
                              <SessionSidebarItem
                                key={session.id}
                                session={session}
                                nested
                                selected={selectedSessionId === session.id}
                                selectMode={selectMode}
                                checked={checkedIds.has(session.id)}
                                menuOpen={openMenuSessionId === session.id}
                                renaming={renamingSessionId === session.id}
                                renameValue={renameValue}
                                renameSubmitting={renameSubmitting}
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
                            <div className="ml-7 mr-2 rounded-lg px-3 py-2 text-xs text-gray-400 dark:text-zinc-600">
                              {t('noProjectSessions')}
                            </div>
                          )}
                          {hasMoreProjectSessions && (
                            <div className="ml-7 mr-2 py-1">
                              <button
                                type="button"
                                onClick={() => void loadMoreSessions(group.id)}
                                disabled={loadingMoreProjectIds.has(group.id)}
                                className="flex w-full items-center justify-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-50 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
                              >
                                {loadingMoreProjectIds.has(group.id)
                                  ? <Loader2 className="h-3 w-3 animate-spin" />
                                  : <ChevronDown className="h-3 w-3" />}
                                {t('loadMore', 'Load more')}
                              </button>
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
                  <div className="flex items-center gap-1 px-4 pb-1 pt-2 text-xs font-semibold uppercase tracking-wide text-gray-400 select-none dark:text-zinc-600">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedProjectId(taskSessionGroup.id);
                        toggleProjectCollapsed(taskSessionGroup.id);
                      }}
                      className="min-w-0 truncate rounded text-left transition-colors hover:text-gray-700 dark:hover:text-zinc-300"
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
                      className="mr-auto rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-zinc-600 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
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
                        handleCreateSessionInProject(taskSessionGroup.id);
                      }}
                      disabled={creating || taskSessionGroup.pathStatus !== 'available'}
                      className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-600 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
                      title={t('createTaskSession')}
                      aria-label={t('createTaskSession')}
                    >
                      {creating && taskGroupSelected
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <Plus className="h-3 w-3" />}
                    </button>
                    <span className="w-5 shrink-0 text-right tabular-nums">
                      {taskSessionGroup.sessionCount}
                    </span>
                  </div>
                  {!taskGroupCollapsed && (
                    <div className="mt-1 space-y-1">
                      {taskSessionGroup.sessions.map((session) => (
                        <SessionSidebarItem
                          key={session.id}
                          session={session}
                          selected={selectedSessionId === session.id}
                          selectMode={selectMode}
                          checked={checkedIds.has(session.id)}
                          menuOpen={openMenuSessionId === session.id}
                          renaming={renamingSessionId === session.id}
                          renameValue={renameValue}
                          renameSubmitting={renameSubmitting}
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
                      {hasMoreTaskSessions && (
                        <div className="mx-4 py-1">
                          <button
                            type="button"
                            onClick={() => void loadMoreSessions(taskSessionGroup.id)}
                            disabled={loadingMoreProjectIds.has(taskSessionGroup.id)}
                            className="flex w-full items-center justify-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-50 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
                          >
                            {loadingMoreProjectIds.has(taskSessionGroup.id)
                              ? <Loader2 className="h-3 w-3 animate-spin" />
                              : <ChevronDown className="h-3 w-3" />}
                            {t('loadMore', 'Load more')}
                          </button>
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
          <div className="border-t border-gray-100 px-3 pt-3 pb-4 flex-shrink-0 dark:border-zinc-800">
            {selectMode ? (
              <div className="grid grid-cols-3 gap-1.5">
                <button
                  onClick={handleSelectAll}
                  className="flex items-center justify-center py-2 text-sm text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors"
                >
                  {checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
                </button>
                <button
                  onClick={handleExitSelectMode}
                  className="flex items-center justify-center py-2 text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
                >
                  {t('cancelSelect')}
                </button>
                <button
                  onClick={handleBatchDelete}
                  disabled={checkedIds.size === 0 || batchDeleting}
                  className="flex items-center justify-center py-2 text-sm text-red-600 bg-red-50 hover:bg-red-100 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  title={t('deleteSelected', { count: checkedIds.size })}
                >
                  {batchDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                </button>
              </div>
            ) : (
              <button
                onClick={handleEnterSelectMode}
                className="w-full flex items-center justify-center gap-1.5 py-1.5 text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-50 rounded-lg transition-colors"
              >
                <CheckSquare className="w-3.5 h-3.5" />
                <span>{t('selectMode')}</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col overflow-hidden h-full min-w-0">
        {/* Header */}
        <div className="px-6 h-12 border-b border-gray-200 bg-white flex items-center justify-between flex-shrink-0 relative dark:border-zinc-800 dark:bg-zinc-950/95">
          <div className="absolute left-4 top-1/2 -translate-y-1/2">
            <button
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
              className="p-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 shadow-sm hover:shadow-md transition-all duration-200 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:shadow-none"
              title={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
            >
              {sidebarCollapsed ? <PanelLeft className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
            </button>
          </div>

          <div className="flex items-center gap-3 ml-14">
            <h2 className="text-base font-semibold text-gray-900 dark:text-zinc-100">
              {selectedSession?.title || t('newSession')}
            </h2>
          </div>

        </div>

        {/* Chat — powered by unified SessionChat */}
        {resolvingSelectedSession ? (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <LoadingSpinner delayMs={180} />
          </div>
        ) : (
          <SessionChat
            key={activeChatSessionId ?? 'empty-session'}
            sessionId={activeChatSessionId}
            live={Boolean(activeChatSessionId)}
          hideInput={selectedSession?.canWrite === false}
          display={{
            compact: false,
            showActions: true,
            showTimestamp: true,
            collapseIntermediateSteps: true,
            processGroupsDefaultOpen: false,
            processGroupsOpenWhileActive: true,
          }}
          agentName={selectedAgent}
          mentionAgents={chatAgents}
          className="flex-1 min-h-0"
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
          onCreateNewSession={handleCreateSession}
          onStreamingDone={() => {
            setPendingInitialMessage(null);
            setPendingInitialDisplayText(null);
          }}
          supportsVision={effectiveSupportsVision}
          contextWindowTokens={selectedModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen
              onSuggestion={setInput}
              onAlertOperationsSetup={() => void handleAlertOperationsSetup()}
              alertOperationsBusy={installingSocWorkspace}
            />
          )}
          toolbarSlot={
            <div className="relative" data-agent-selector>
              <button
                type="button"
                onClick={() => setShowAgentOptions(!showAgentOptions)}
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
          }
          centerToolbarSlot={
            <div className="relative" data-model-selector>
              <button
                type="button"
                onClick={() => setShowModelOptions(!showModelOptions)}
                disabled={loadingProviders || loadingEnabledModels || chatModelOptions.length === 0}
                className="flex h-7 w-[132px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                title={selectedModelOption ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}` : t('modelPicker.empty')}
              >
                <Cpu className="h-3 w-3 shrink-0" />
                <span className="truncate font-medium">
                  {selectedModelOption?.label ?? (loadingProviders || loadingEnabledModels ? t('loading') : t('modelPicker.empty'))}
                </span>
                <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${showModelOptions ? 'rotate-180' : ''}`} />
              </button>
              {showModelOptions && (
                <div className="absolute right-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
                  <div className="border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
                    <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('modelPicker.title')}</div>
                    <div className="truncate text-[10px] text-zinc-400 dark:text-zinc-500">{t('modelPicker.hint')}</div>
                  </div>
                  <div className="h-[13.5rem] overflow-y-auto p-1.5">
                    {loadingProviders || loadingEnabledModels ? (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
                    ) : groupedChatModelOptions.length > 0 ? (
                      groupedChatModelOptions.map((group) => (
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
                      ))
                    ) : (
                      <div className="p-3 text-center text-xs text-zinc-500">{t('modelPicker.empty')}</div>
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
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
            <div className="border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {projectDialogMode === 'create' ? t('projectDialog.createTitle') : t('projectDialog.renameTitle')}
              </h3>
            </div>
            <div className="space-y-3 px-4 py-4">
              <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400" htmlFor="session-project-name">
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
                className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-600 dark:focus:border-blue-500"
                autoFocus
              />
              {projectDialogMode === 'create' && (
                <>
                  <label className="block pt-1 text-xs font-medium text-zinc-500 dark:text-zinc-400" htmlFor="session-project-worktree">
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
                      className="min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-3 py-2 font-mono text-xs text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-blue-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-600 dark:focus:border-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => void loadFolderBrowser(projectWorktreeValue)}
                      disabled={projectSubmitting || folderBrowserLoading}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 text-xs font-medium text-zinc-600 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-900"
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
            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
              <button
                type="button"
                onClick={handleCloseProjectDialog}
                disabled={projectSubmitting}
                className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleSubmitProject()}
                disabled={projectSubmitting}
                className="inline-flex min-w-16 items-center justify-center rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-300"
              >
                {projectSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : t('save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {projectPendingDelete && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
            <div className="border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {t('projectDialog.deleteTitle')}
              </h3>
            </div>
            <div className="px-4 py-4 text-sm leading-6 text-zinc-600 dark:text-zinc-300">
              {t('projectDialog.deleteDescription', { project: projectPendingDelete.label })}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
              <button
                type="button"
                onClick={() => setProjectPendingDelete(null)}
                disabled={projectDeleting}
                className="rounded-lg px-3 py-1.5 text-sm text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteProject()}
                disabled={projectDeleting}
                className="inline-flex min-w-16 items-center justify-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
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
            className="fixed z-50 min-w-28 rounded-md border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
            style={{ top: menuAnchor.top, right: menuAnchor.right }}
            data-session-menu-portal
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <PencilLine className="w-3.5 h-3.5" />
              <span>{t('rename')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              disabled={downloadingSessionId === session.id}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Download className="w-3.5 h-3.5" />
              <span>{t('downloadJson')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleShareSession(session.id, !session.isShared); }}
              disabled={session.canWrite === false}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Share2 className="w-3.5 h-3.5" />
              <span>{session.isShared ? t('unshareAction') : t('shareAction')}</span>
            </button>
            <div className="mx-2 my-1 border-t border-zinc-100 dark:border-zinc-800" />
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleDeleteSession(session.id); }}
              disabled={session.canDelete === false}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-300 dark:hover:bg-red-950/40"
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
    <div className="text-center max-w-2xl px-8">
      <div className="w-20 h-20 mx-auto mb-6 rounded-full bg-gradient-to-br from-slate-700 to-slate-900 flex items-center justify-center shadow-lg">
        <Sparkles className="w-10 h-10 text-white" />
      </div>
      <h3 className="text-xl font-bold text-gray-900 mb-3 dark:text-zinc-50">{t('welcome.title')}</h3>
      <p className="text-sm text-gray-600 mb-8 dark:text-zinc-400">{t('welcome.description')}</p>

      <div className="flex flex-wrap gap-3 justify-center">
        <button
          onClick={onAlertOperationsSetup}
          disabled={alertOperationsBusy}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-slate-400 hover:bg-slate-50 transition-all duration-200 shadow-sm hover:shadow-md disabled:cursor-not-allowed disabled:opacity-70 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-slate-500/70 dark:hover:bg-zinc-800 dark:hover:shadow-none"
        >
          {alertOperationsBusy ? (
            <Loader2 className="w-5 h-5 animate-spin text-slate-600" />
          ) : (
            <Shield className="w-5 h-5 text-slate-600" />
          )}
          <span className="font-medium text-gray-700 dark:text-zinc-200">{t('welcome.alertOperations')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.threatHuntingSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-orange-400 hover:bg-orange-50 transition-all duration-200 shadow-sm hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-orange-500/70 dark:hover:bg-orange-950/30 dark:hover:shadow-none"
        >
          <Search className="w-5 h-5 text-orange-600" />
          <span className="font-medium text-gray-700 dark:text-zinc-200">{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-amber-400 hover:bg-amber-50 transition-all duration-200 shadow-sm hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-amber-500/70 dark:hover:bg-amber-950/30 dark:hover:shadow-none"
        >
          <AlertTriangle className="w-5 h-5 text-amber-600" />
          <span className="font-medium text-gray-700 dark:text-zinc-200">{t('welcome.incidentResponse')}</span>
        </button>
      </div>
    </div>
  );
}
