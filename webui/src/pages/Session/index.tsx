import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  MessageSquare, Plus, Trash2,
  ChevronDown, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Share2, Cpu, Info, X,
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
const SOC_WORKSPACE_COMPONENT_ID = 'soc-workspace';
const INSTALLED_HUB_STATES = new Set(['installed', 'localOnly', 'updateAvailable']);
type AgentSourceFilter = 'all' | 'builtin' | 'custom';
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
  const [installingSocWorkspace, setInstallingSocWorkspace] = useState(false);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [pendingInitialDisplayText, setPendingInitialDisplayText] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
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

  // 今天/昨天不限制；本周/上周/更早默认只显示 5 条
  const GROUP_DEFAULT_LIMIT: Record<string, number> = {
    today: Infinity,
    yesterday: Infinity,
    thisWeek: 5,
    lastWeek: 5,
    earlier: 5,
  };

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const toggleGroupExpand = useCallback((key: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }, []);

  const groupedSessions = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const yesterdayStart = todayStart - 86400000;
    // Week starts on Monday
    const dayOfWeek = now.getDay() === 0 ? 7 : now.getDay();
    const thisWeekStart = todayStart - (dayOfWeek - 1) * 86400000;
    const lastWeekStart = thisWeekStart - 7 * 86400000;

    const q = searchQuery.toLowerCase().trim();
    const filtered = q ? sessions.filter(s => s.title.toLowerCase().includes(q)) : sessions;

    const buckets: { key: string; labelKey: string; items: typeof sessions }[] = [
      { key: 'today', labelKey: 'groupToday', items: [] },
      { key: 'yesterday', labelKey: 'groupYesterday', items: [] },
      { key: 'thisWeek', labelKey: 'groupThisWeek', items: [] },
      { key: 'lastWeek', labelKey: 'groupLastWeek', items: [] },
      { key: 'earlier', labelKey: 'groupEarlier', items: [] },
    ];

    for (const s of filtered) {
      const ts = s.time?.updated ?? 0;
      if (ts >= todayStart) buckets[0].items.push(s);
      else if (ts >= yesterdayStart) buckets[1].items.push(s);
      else if (ts >= thisWeekStart) buckets[2].items.push(s);
      else if (ts >= lastWeekStart) buckets[3].items.push(s);
      else buckets[4].items.push(s);
    }

    return buckets.filter(b => b.items.length > 0);
  }, [sessions, searchQuery]);

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
      const response = await client.post('/api/session', { title: 'New Session' });
      addSession(response.data);
      setSelectedAgent('rex');
      setSelectedModelKey(null);
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, addSession, toast, t]);

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
      const response = await client.post('/api/session', { title: 'New Session' });
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
      if (options?.displayText) payload.displayText = options.displayText;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, selectedAgent, toast, t]);

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

  if (loadingSessions) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  return (
    <div className="flex h-full w-full overflow-hidden bg-[#f4f4f2] text-[#202328] dark:bg-[#252c35] dark:text-[#d7dee8]">
      {/* ── Sidebar ── */}
      <div
        className={`flex h-[calc(100%_-_1.5rem)] flex-shrink-0 flex-col overflow-hidden rounded-2xl border bg-[#f8f8f6] shadow-[0_3px_12px_rgba(22,27,34,0.045)] transition-[width,margin,opacity] duration-200 dark:border-white/[0.08] dark:bg-[#303842] dark:shadow-[0_8px_24px_rgba(15,18,22,0.16)] ${
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
            {!selectMode && sessions.length > 0 && (
              <button
                type="button"
                onClick={handleEnterSelectMode}
                className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg text-[#7b8087] transition-colors hover:bg-black/[0.065] hover:text-[#202328] dark:text-[#9aa7b4] dark:hover:bg-white/[0.08] dark:hover:text-white"
                title={t('selectMode')}
                aria-label={t('selectMode')}
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            )}
          </div>

          <div className="space-y-0.5">
            <div className="relative h-[34px] rounded-lg transition-colors hover:bg-black/[0.04] dark:hover:bg-white/[0.06]">
              {creating
                ? <Loader2 className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-[#7b8087]" />
                : <PencilLine className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#7b8087] dark:text-[#9aa7b4]" />}
              <button
                onClick={handleCreateSession}
                disabled={creating}
                className="h-full w-full rounded-lg border-0 bg-transparent pl-9 pr-3 text-left text-sm font-medium text-[#474b51] transition-colors hover:text-[#202328] disabled:cursor-not-allowed disabled:opacity-60 dark:text-[#c3ccd6] dark:hover:text-white"
              >
                {t('newSession')}
              </button>
            </div>

            <div className="relative h-[34px] rounded-lg transition-colors hover:bg-black/[0.04] focus-within:bg-black/[0.04] dark:hover:bg-white/[0.06] dark:focus-within:bg-white/[0.06]">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-[15px] w-[15px] -translate-y-1/2 text-[#7b8087] dark:text-[#9aa7b4]" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('filterConversations', 'Filter conversations...')}
                className="h-full w-full rounded-lg border-0 bg-transparent pl-9 pr-8 text-sm text-[#474b51] outline-none placeholder:text-[#474b51] focus:bg-transparent dark:text-[#c3ccd6] dark:placeholder:text-[#c3ccd6]"
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
        <div className="scrollbar-hide min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 pb-[18px] pt-1.5">
          {sessions.length === 0 ? (
            <div className="px-4 py-10 text-center text-[#858a91] dark:text-[#9aa7b4]">
              <MessageSquare className="mx-auto mb-2 h-9 w-9 opacity-35" />
              <p className="text-sm">{t('noSessions')}</p>
            </div>
          ) : groupedSessions.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-[#858a91] dark:text-[#9aa7b4]">
              <p className="text-sm">{t('noResults', 'No conversations found')}</p>
            </div>
          ) : (
            groupedSessions.map(({ key, labelKey, items }) => {
              const isSearching = searchQuery.trim().length > 0;
              const limit = isSearching ? Infinity : (GROUP_DEFAULT_LIMIT[key] ?? 5);
              const isExpanded = expandedGroups.has(key);
              const visibleItems = (isSearching || isExpanded || items.length <= limit)
                ? items
                : items.slice(0, limit);
              const hiddenCount = items.length - visibleItems.length;

              return (
              <div key={key} className="mt-3 first:mt-0">
                <div className="flex h-7 select-none items-center px-2 text-[11px] font-semibold uppercase tracking-[0.02em] text-[#8a8e94] dark:text-[#8f9ba8]">
                  {t(labelKey, labelKey)}
                </div>
                {visibleItems.map((session) => (
                  <div
                    key={session.id}
                    onClick={() => selectMode ? handleToggleCheck(session.id) : setSelectedSessionId(session.id)}
                    className={`group relative mb-0.5 min-h-[34px] cursor-pointer rounded-lg border px-2.5 py-1 transition-colors duration-100 ${
                      !selectMode && selectedSessionId === session.id
                        ? 'border-transparent bg-[#ececea] text-[#202328] dark:bg-[#3a434e] dark:text-white'
                        : selectMode && checkedIds.has(session.id)
                        ? 'border-blue-200 bg-blue-50 text-[#202328] dark:border-blue-400/40 dark:bg-blue-500/10 dark:text-white'
                        : 'border-transparent text-[#5b6067] hover:bg-black/[0.04] hover:text-[#202328] dark:text-[#b8c2cc] dark:hover:bg-white/[0.06] dark:hover:text-white'
                    }`}
                  >
                    {/* Title row */}
                    <div className="flex min-w-0 items-center gap-1.5 pr-6">
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
                          className="h-6 w-full min-w-0 rounded-md border border-blue-300 bg-white px-1.5 text-[13px] text-gray-900 outline-none focus:border-blue-400 dark:border-blue-500/50 dark:bg-[#252c35] dark:text-white"
                          aria-label={t('rename')}
                          data-session-rename-input
                        />
                      ) : (
                        <h3 className={`flex min-w-0 flex-1 items-center gap-1.5 truncate text-[13px] ${
                          selectedSessionId === session.id ? 'font-medium' : 'font-normal'
                        }`}>
                          <span className="truncate">{session.title}</span>
                          {session.isShared && (
                            <span className="inline-flex shrink-0 items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:border-blue-400/35 dark:bg-blue-500/10 dark:text-blue-200">
                              {t('sharedTag')}
                            </span>
                          )}
                        </h3>
                      )}
                      {session.time?.updated && renamingSessionId !== session.id && (
                        <time className="ml-1 shrink-0 text-[11px] font-normal tabular-nums text-[#858a91] transition-opacity group-hover:opacity-0 dark:text-[#8f9ba8]">
                          {formatSessionDate(session.time.updated)}
                        </time>
                      )}
                    </div>

                    {/* Three-dot menu trigger */}
                    {!selectMode && (
                      <div className="absolute right-1 top-1/2 -translate-y-1/2" data-session-actions>
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
                          className={`grid h-6 w-6 place-items-center rounded-lg text-[#989ca1] transition-all hover:bg-white/80 hover:text-[#202328] dark:text-[#8f9ba8] dark:hover:bg-white/[0.08] dark:hover:text-white ${
                            openMenuSessionId === session.id || selectedSessionId === session.id
                              ? 'bg-white/80 text-[#202328] opacity-100 dark:bg-white/[0.08] dark:text-white'
                              : 'opacity-0 group-hover:opacity-100'
                          }`}
                        >
                          <MoreHorizontal className="h-[15px] w-[15px]" />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                {/* 展开/收起按钮 */}
                {!isSearching && hiddenCount > 0 && (
                  <button
                    onClick={() => toggleGroupExpand(key)}
                    className="mx-1 mb-0.5 flex h-7 items-center gap-1 rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                  >
                    <ChevronDown className="w-3 h-3" />
                    <span>{t('showMore', { count: hiddenCount })}</span>
                  </button>
                )}
                {!isSearching && isExpanded && items.length > (GROUP_DEFAULT_LIMIT[key] ?? 5) && (
                  <button
                    onClick={() => toggleGroupExpand(key)}
                    className="mx-1 mb-0.5 flex h-7 items-center gap-1 rounded-lg px-2 text-[11px] text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
                  >
                    <ChevronDown className="w-3 h-3 rotate-180" />
                    <span>{t('showLess', 'Show less')}</span>
                  </button>
                )}
              </div>
              );
            })
          )}
          {hasMoreSessions && (
            <div className="px-1 py-1">
              <button
                onClick={() => void loadMoreSessions()}
                disabled={loadingMoreSessions}
                className="flex h-7 w-full items-center justify-center gap-1.5 rounded-lg border-0 bg-transparent px-3 text-[11px] font-medium text-[#969aa0] transition-colors hover:bg-black/[0.04] hover:text-[#474b51] disabled:cursor-not-allowed disabled:opacity-60 dark:text-[#8f9ba8] dark:hover:bg-white/[0.06] dark:hover:text-white"
              >
                {loadingMoreSessions ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ChevronDown className="h-3.5 w-3.5" />}
                <span>{loadingMoreSessions ? t('loading') : t('loadMore', 'Load more')}</span>
              </button>
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
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-[#f4f4f2] dark:bg-[#252c35]">
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
            <h2 className="truncate text-[13px] font-semibold text-[#555a61] dark:text-[#c3ccd6]">
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
          onSseStatusChange={selectedSessionId ? setSseStatus : undefined}
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
      </div>

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
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-[#f8f8f6] px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-[#fafaf8] hover:text-[#111827] disabled:cursor-not-allowed disabled:opacity-70 dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
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
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-[#f8f8f6] px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-[#fafaf8] hover:text-[#111827] dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
        >
          <Search className="h-[18px] w-[18px] text-[#f4511e]" />
          <span>{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className="inline-flex h-11 items-center gap-2.5 rounded-[10px] border border-black/[0.11] bg-[#f8f8f6] px-4 text-sm font-semibold text-[#374151] transition-colors hover:border-[#c2c9d2] hover:bg-[#fafaf8] hover:text-[#111827] dark:border-white/[0.10] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:border-white/[0.18] dark:hover:bg-[#3a434e] dark:hover:text-white"
        >
          <AlertTriangle className="h-[18px] w-[18px] text-amber-500" />
          <span>{t('welcome.incidentResponse')}</span>
        </button>
      </div>
    </div>
  );
}
