import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  MessageSquare, Plus, Trash2,
  ChevronDown, ChevronRight, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Share2, Cpu, Info,
  FolderGit2, FolderPlus,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat, { type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import { sessionApi } from '@/api/session';
import type { Agent } from '@/api/agent';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import client from '@/api/client';
import { defaultModelAPI, modelV2API } from '@/api/provider';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { formatSessionDate } from '@/utils/time';
import type { ModelDefinitionV2, Session } from '@/types';

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
type AgentSourceFilter = 'all' | 'builtin' | 'custom';
type ProjectSummary = {
  id: string;
  worktree: string;
  name?: string | null;
};
type ProjectDialogMode = 'create' | 'rename';
type ProjectSessionGroup = {
  id: string;
  label: string;
  worktree: string;
  sessions: Session[];
};
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

function normalizeProjectPath(path?: string): string {
  return (path || '').replace(/[\\/]+$/g, '');
}

function isLegacyDefaultProjectId(projectId?: string): boolean {
  return !projectId || projectId === 'default' || projectId === 'global';
}

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState('rex');
  const [showAgentOptions, setShowAgentOptions] = useState(false);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [showModelOptions, setShowModelOptions] = useState(false);
  const [enabledModelDefinitions, setEnabledModelDefinitions] = useState<ModelDefinitionV2[]>([]);
  const [loadingEnabledModels, setLoadingEnabledModels] = useState(true);
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [creating, setCreating] = useState(false);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [collapsedProjectIds, setCollapsedProjectIds] = useState<Set<string>>(new Set());
  const [projectDialogMode, setProjectDialogMode] = useState<ProjectDialogMode | null>(null);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [projectNameValue, setProjectNameValue] = useState('');
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
  const toast = useToast();

  const {
    sessions,
    loading: loadingSessions,
    refetch: refetchSessions,
    updateSessionTitle,
    removeSession,
    removeSessions,
    addSession,
    hasMore: hasMoreSessions,
    loadingMore: loadingMoreSessions,
    loadMore: loadMoreSessions,
  } = useSessions(searchQuery);
  const { agents, loading: loadingAgents } = useAgents();
  const { providers, loading: loadingProviders } = useProviders();
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
  const selectedModelOption = useMemo(
    () => chatModelOptions.find((option) => option.key === selectedModelKey) ?? (selectedModelKey ? null : chatModelOptions[0] ?? null),
    [chatModelOptions, selectedModelKey],
  );
  const selectedPromptModel = selectedModelOption
    ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
    : null;
  const effectiveSupportsVision = selectedModelOption?.supportsVision ?? supportsVision;
  const listedSelectedSession = useMemo(
    () => sessions.find(s => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );
  const selectedSession = listedSelectedSession
    ?? (selectedSessionFallback?.id === selectedSessionId ? selectedSessionFallback : null);

  const toggleProjectCollapsed = useCallback((projectId: string) => {
    setCollapsedProjectIds(prev => {
      const next = new Set(prev);
      next.has(projectId) ? next.delete(projectId) : next.add(projectId);
      return next;
    });
  }, []);

  const projectSessionGroups = useMemo<ProjectSessionGroup[]>(() => {
    const projectMap = new Map<string, ProjectSessionGroup>();
    const currentProject = currentProjectId
      ? projects.find((project) => project.id === currentProjectId)
      : undefined;
    const currentProjectWorktree = normalizeProjectPath(currentProject?.worktree);

    projects.forEach((project) => {
      const isCurrentProject = currentProjectId === project.id;
      const hasExplicitName = Boolean(project.name?.trim());
      projectMap.set(project.id, {
        id: project.id,
        label: isCurrentProject && !hasExplicitName ? t('defaultProjectName') : getProjectLabel(project),
        worktree: project.worktree,
        sessions: [],
      });
    });

    sessions.forEach((session) => {
      const sessionDirectory = normalizeProjectPath(session.directory);
      const projectId = isLegacyDefaultProjectId(session.projectID) && currentProjectId && sessionDirectory === currentProjectWorktree
        ? currentProjectId
        : session.projectID || session.directory || 'global';
      const existing = projectMap.get(projectId);
      if (existing) {
        existing.sessions.push(session);
        return;
      }

      projectMap.set(projectId, {
        id: projectId,
        label: getProjectLabel(undefined, session.directory),
        worktree: session.directory,
        sessions: [session],
      });
    });

    return Array.from(projectMap.values())
      .filter((group) => group.sessions.length > 0 || !searchQuery.trim())
      .sort((a, b) => {
        const aLatest = a.sessions[0]?.time?.updated ?? 0;
        const bLatest = b.sessions[0]?.time?.updated ?? 0;
        if (aLatest !== bLatest) return bLatest - aLatest;
        return a.label.localeCompare(b.label);
      });
  }, [currentProjectId, projects, searchQuery, sessions, t]);

  const selectedProjectIDForCreate = selectedProjectId ?? currentProjectId ?? projectSessionGroups[0]?.id ?? null;

  useEffect(() => {
    if (projectSessionGroups.length === 0) {
      setSelectedProjectId(null);
      return;
    }

    if (selectedProjectId && projectSessionGroups.some((group) => group.id === selectedProjectId)) {
      return;
    }

    const fallbackProjectId = currentProjectId && projectSessionGroups.some((group) => group.id === currentProjectId)
      ? currentProjectId
      : projectSessionGroups[0].id;
    setSelectedProjectId(fallbackProjectId);
  }, [currentProjectId, projectSessionGroups, selectedProjectId]);

  // Handle SSE events for session-level updates (title changes, etc.)
  const handleChatError = useCallback((msg: string) => {
    toast.error(t('chat.error', 'Error'), msg);
  }, [toast, t]);

  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    if (event.type === 'session.updated' && event.properties?.id) {
      if (event.properties?.title) {
        // Instant local title update so the sidebar reflects the change immediately.
        updateSessionTitle(event.properties.id, event.properties.title);
      }
      // Always do a silent background sync: session.updated also changes
      // time.updated (affects ordering) and potentially other metadata.
      // refetchSessions() is safe here — it never shows a loading spinner
      // after the initial load (see initializedRef in useSessions).
      refetchSessions();
    }
  }, [updateSessionTitle, refetchSessions]);

  const fetchProjects = useCallback(async () => {
    const [listResult, currentResult] = await Promise.allSettled([
      client.get('/api/project'),
      client.get('/api/project/current'),
    ]);

    const nextProjects = listResult.status === 'fulfilled' && Array.isArray(listResult.value.data)
      ? listResult.value.data
      : [];
    const nextCurrentProjectId = currentResult.status === 'fulfilled'
      ? currentResult.value.data?.id ?? null
      : null;

    setProjects(nextProjects);
    setCurrentProjectId(nextCurrentProjectId);
    setSelectedProjectId((current) => current ?? nextCurrentProjectId ?? nextProjects[0]?.id ?? null);
  }, []);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  // Keep the selected session in sync with URL query params (e.g. onboarding
  // or other in-app navigation to `/sessions?session=...`). Clear the params
  // after consuming them so refreshes don't re-send the initial message.
  useEffect(() => {
    const sessionParam = searchParams.get('session');
    const messageParam = searchParams.get('message');
    if (!sessionParam) return;

    if (sessionParam !== selectedSessionId) {
      setSelectedSessionId(sessionParam);
    }
    if (sessionParam) {
      if (messageParam) {
        setPendingInitialMessage(messageParam);
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
    if (!selectedSessionId) return;
    writeLastSelectedSessionId(selectedSessionId);
  }, [selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setSelectedSessionFallback(null);
      return;
    }
    if (listedSelectedSession) {
      setSelectedSessionFallback(null);
      return;
    }
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
          writeLastSelectedSessionId(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [listedSelectedSession, loadingSessions, selectedSessionId]);

  useEffect(() => {
    let cancelled = false;
    setLoadingEnabledModels(true);
    modelV2API.listDefinitions({ enabled_only: true })
      .then((response) => {
        if (!cancelled) setEnabledModelDefinitions(response.data.models ?? []);
      })
      .catch(() => {
        if (!cancelled) setEnabledModelDefinitions([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingEnabledModels(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

    const pinnedKey = selectedSession?.model_pinned && selectedSession.provider && selectedSession.model
      ? makeModelKey(selectedSession.provider, selectedSession.model)
      : null;
    if (pinnedKey && chatModelOptions.some((option) => option.key === pinnedKey)) {
      setSelectedModelKey(pinnedKey);
      return;
    }

    let cancelled = false;
    setSelectedModelKey(null);
    defaultModelAPI.getResolved()
      .then((response) => {
        if (cancelled) return;
        const { provider_id: providerID, model_id: modelID } = response.data;
        const defaultKey = makeModelKey(providerID, modelID);
        const fallbackKey = chatModelOptions[0]?.key ?? null;
        setSelectedModelKey(chatModelOptions.some((option) => option.key === defaultKey) ? defaultKey : fallbackKey);
      })
      .catch(() => {
        if (!cancelled) setSelectedModelKey(chatModelOptions[0]?.key ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [
    chatModelOptions,
    selectedSession?.model,
    selectedSession?.model_pinned,
    selectedSession?.provider,
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

  const handleCreateSession = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(selectedProjectIDForCreate ? { projectID: selectedProjectIDForCreate } : {}),
      });
      addSession(response.data);
      setSelectedAgent('rex');
      setSelectedModelKey(null);
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, selectedProjectIDForCreate, addSession, toast, t]);

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
  ) => {
    try {
      const response = await client.post('/api/session', {
        title: 'New Session',
        ...(selectedProjectIDForCreate ? { projectID: selectedProjectIDForCreate } : {}),
      });
      const newSessionId = response.data.id;

      addSession(response.data);
      setSelectedModelKey(null);
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      const effectiveAgent = agentOverride || selectedAgent || 'rex';
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (modelOverride) payload.model = modelOverride;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, selectedAgent, selectedProjectIDForCreate, toast, t]);

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
    } catch (err: any) {
      toast.error(t('deleteFailed'), err.message);
    }
  }, [selectedSessionId, removeSession, toast, t]);

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
    setProjectNameValue('');
  }, []);

  const handleOpenRenameProject = useCallback((project: ProjectSessionGroup) => {
    setProjectDialogMode('rename');
    setEditingProjectId(project.id);
    setProjectNameValue(project.label);
  }, []);

  const handleCloseProjectDialog = useCallback(() => {
    if (projectSubmitting) return;
    setProjectDialogMode(null);
    setEditingProjectId(null);
    setProjectNameValue('');
  }, [projectSubmitting]);

  const handleSubmitProject = useCallback(async () => {
    if (!projectDialogMode || projectSubmitting) return;
    const nextName = projectNameValue.trim();
    if (!nextName) {
      toast.error(t('projectDialog.saveFailed'), t('projectDialog.nameEmpty'));
      return;
    }

    setProjectSubmitting(true);
    try {
      if (projectDialogMode === 'create') {
        const response = await client.post('/api/project', { name: nextName });
        setSelectedProjectId(response.data.id);
        setCollapsedProjectIds(prev => {
          const next = new Set(prev);
          next.delete(response.data.id);
          return next;
        });
      } else if (editingProjectId) {
        await client.patch(`/api/project/${editingProjectId}`, { name: nextName });
      }
      await fetchProjects();
      setProjectDialogMode(null);
      setEditingProjectId(null);
      setProjectNameValue('');
    } catch (err: any) {
      toast.error(t('projectDialog.saveFailed'), err.message);
    } finally {
      setProjectSubmitting(false);
    }
  }, [editingProjectId, fetchProjects, projectDialogMode, projectNameValue, projectSubmitting, t, toast]);

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
  }, [checkedIds, batchDeleting, removeSessions, selectedSessionId, toast, t]);

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
        <LoadingSpinner />
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
              onClick={handleCreateSession}
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
              <div className="flex items-center justify-between px-4 pt-4 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide select-none dark:text-zinc-600">
                <span>{t('projectsSection')}</span>
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
              <div className="space-y-1">
                {projectSessionGroups.map((group) => {
                  const collapsed = collapsedProjectIds.has(group.id);
                  const isCurrentProject = currentProjectId === group.id;
                  const isSelectedProject = selectedProjectId === group.id;
                  return (
                    <div key={group.id} className="group/project">
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
                          onClick={(event) => {
                            event.stopPropagation();
                            toggleProjectCollapsed(group.id);
                          }}
                          className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                          aria-label={t('toggleProject', { project: group.label })}
                        >
                          {collapsed ? <ChevronRight className="h-3.5 w-3.5 shrink-0" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0" />}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedProjectId(group.id);
                            setCollapsedProjectIds(prev => {
                              const next = new Set(prev);
                              next.delete(group.id);
                              return next;
                            });
                          }}
                          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-0.5 text-left"
                          aria-label={t('selectProject', { project: group.label })}
                        >
                          <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-gray-500 dark:text-zinc-500" />
                          <span className="min-w-0 flex-1 truncate font-medium">{group.label}</span>
                          {isCurrentProject && (
                            <span
                              className="h-2 w-2 shrink-0 rounded-full bg-blue-400 shadow-[0_0_0_2px_rgba(96,165,250,0.18)]"
                              title={t('defaultProject')}
                            />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleOpenRenameProject(group)}
                          className="rounded p-1 text-gray-400 opacity-0 transition-all hover:bg-gray-200 hover:text-gray-700 group-hover/project:opacity-100 dark:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                          title={t('projectDialog.renameTitle')}
                          aria-label={t('projectDialog.renameTitle')}
                        >
                          <PencilLine className="h-3 w-3" />
                        </button>
                        <span className="w-5 shrink-0 text-right text-xs tabular-nums text-gray-400 dark:text-zinc-600">
                          {group.sessions.length}
                        </span>
                      </div>
                      {!collapsed && (
                        <div className="mt-1">
                          {group.sessions.length > 0 ? (
                            group.sessions.map(renderSessionListItem)
                          ) : (
                            <div className="mx-4 py-1 text-xs text-gray-400 dark:text-zinc-600">
                              {t('noProjectSessions')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {hasMoreSessions && (
            <div className="px-4 py-3">
              <button
                onClick={() => void loadMoreSessions()}
                disabled={loadingMoreSessions}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400 dark:hover:bg-zinc-900"
              >
                {loadingMoreSessions ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ChevronDown className="h-3.5 w-3.5" />}
                <span>{loadingMoreSessions ? t('loading') : t('loadMore', 'Load more')}</span>
              </button>
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
        <SessionChat
          key={selectedSessionId ?? 'empty-session'}
          sessionId={selectedSessionId}
          live={Boolean(selectedSessionId)}
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
          onInitialMessageConsumed={() => setPendingInitialMessage(null)}
          onSseStatusChange={selectedSessionId ? setSseStatus : undefined}
          onSSEEvent={handleSSEEvent}
          onError={handleChatError}
          onCreateAndSend={handleCreateAndSend}
          onCreateNewSession={handleCreateSession}
          onStreamingDone={() => setPendingInitialMessage(null)}
          supportsVision={effectiveSupportsVision}
          contextWindowTokens={selectedModelOption?.contextWindowTokens ?? null}
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen onSuggestion={setInput} />
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
      </div>

      {projectDialogMode && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 px-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
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
                onChange={(event) => setProjectNameValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
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

      {/* Three-dot dropdown — rendered outside sidebar to avoid overflow:hidden clipping */}
      {openMenuSessionId && menuAnchor && (() => {
        const sid = openMenuSessionId;
        const session = sessions.find(s => s.id === sid);
        if (!session) return null;
        return (
          <div
            className="fixed z-50 w-36 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
            style={{ top: menuAnchor.top, right: menuAnchor.right }}
            data-session-menu-portal
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <PencilLine className="w-3.5 h-3.5" />
              <span>{t('rename')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              disabled={downloadingSessionId === session.id}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Download className="w-3.5 h-3.5" />
              <span>{t('downloadJson')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleShareSession(session.id, !session.isShared); }}
              disabled={session.canWrite === false}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              <Share2 className="w-3.5 h-3.5" />
              <span>{session.isShared ? t('unshareAction') : t('shareAction')}</span>
            </button>
            <div className="mx-2.5 my-1 border-t border-gray-100 dark:border-zinc-800" />
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleDeleteSession(session.id); }}
              disabled={session.canDelete === false}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-red-600 hover:bg-red-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-300 dark:hover:bg-red-950/40"
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

function WelcomeScreen({ onSuggestion }: { onSuggestion: (text: string) => void }) {
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
          onClick={() => onSuggestion(t('welcome.alertTriageSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-slate-400 hover:bg-slate-50 transition-all duration-200 shadow-sm hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-slate-500/70 dark:hover:bg-zinc-800 dark:hover:shadow-none"
        >
          <Shield className="w-5 h-5 text-slate-600" />
          <span className="font-medium text-gray-700 dark:text-zinc-200">{t('welcome.alertTriage')}</span>
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
