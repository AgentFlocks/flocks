import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  MessageSquare, Plus, Trash2,
  ChevronDown, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Share2, Cpu,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useSearchParams } from 'react-router-dom';
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
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import { formatSessionDate } from '@/utils/time';
import type { ModelDefinitionV2 } from '@/types';

function sanitizeSessionExportName(value: string) {
  const trimmed = value.trim();
  return trimmed
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'session';
}

const LAST_SELECTED_SESSION_STORAGE_KEY = 'flocks:last-selected-session';
type AgentSourceFilter = 'all' | 'builtin' | 'custom';
type ChatModelOption = {
  key: string;
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
  supportsVision: boolean | null;
};
type ChatModelProviderGroup = {
  providerID: string;
  providerName: string;
  models: ChatModelOption[];
};

function formatAgentName(name: string): string {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}

function getAgentSecondaryDescription(agent: Agent, language: string): string {
  const isZh = language.toLowerCase().replace('_', '-').startsWith('zh');
  const primary = (isZh ? agent.descriptionCn : agent.description)?.trim();
  const secondary = (isZh ? agent.description : agent.descriptionCn)?.trim();
  if (primary && secondary && primary !== secondary) return secondary;
  return '';
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

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
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
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSubmitInFlightRef = useRef(false);
  const toast = useToast();

  const { sessions, loading: loadingSessions, refetch: refetchSessions, updateSessionTitle, removeSession, removeSessions, addSession } = useSessions();
  const { agents, loading: loadingAgents } = useAgents();
  const { providers, loading: loadingProviders } = useProviders();
  const primaryAgents = useMemo(() => agents.filter((a) => a.mode === 'primary'), [agents]);
  const subAgents = useMemo(
    () => agents.filter((a) => a.mode !== 'primary' && !(a.tags ?? []).includes('system')),
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

    return enabledModelDefinitions.flatMap((model) => {
      const provider = providerById.get(model.provider_id);
      if (!provider) return [];
      return [{
        key: `${provider.id}::${model.id}`,
        providerID: provider.id,
        providerName: provider.name || provider.id,
        modelID: model.id,
        label: model.name || model.id,
        supportsVision: typeof model.capabilities?.supports_vision === 'boolean'
          ? model.capabilities.supports_vision
          : null,
      }];
    });
  }, [enabledModelDefinitions, providers]);
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
    () => chatModelOptions.find((option) => option.key === selectedModelKey) ?? chatModelOptions[0] ?? null,
    [chatModelOptions, selectedModelKey],
  );
  const selectedPromptModel = selectedModelOption
    ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
    : null;
  const effectiveSupportsVision = selectedModelOption?.supportsVision ?? supportsVision;
  const selectedSession = useMemo(
    () => sessions.find(s => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

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
    if (!selectedSessionId) return;
    writeLastSelectedSessionId(selectedSessionId);
  }, [selectedSessionId]);

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
    if (selectedModelKey || chatModelOptions.length === 0) return;
    let cancelled = false;
    defaultModelAPI.getResolved()
      .then((response) => {
        if (cancelled) return;
        const { provider_id: providerID, model_id: modelID } = response.data;
        const defaultKey = `${providerID}::${modelID}`;
        const fallbackKey = chatModelOptions[0]?.key ?? null;
        setSelectedModelKey(chatModelOptions.some((option) => option.key === defaultKey) ? defaultKey : fallbackKey);
      })
      .catch(() => {
        if (!cancelled) setSelectedModelKey(chatModelOptions[0]?.key ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [chatModelOptions, selectedModelKey]);

  useEffect(() => {
    if (loadingEnabledModels || chatModelOptions.length === 0 || !selectedModelKey) return;
    if (chatModelOptions.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(chatModelOptions[0].key);
  }, [chatModelOptions, loadingEnabledModels, selectedModelKey]);

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
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, addSession, toast, t]);

  useEffect(() => {
    if (loadingSessions) return;
    if (searchParams.get('session')) return;

    if (selectedSessionId && sessions.some((session) => session.id === selectedSessionId)) {
      return;
    }

    const lastSelectedSessionId = readLastSelectedSessionId();
    const fallbackSession = lastSelectedSessionId
      ? sessions.find((session) => session.id === lastSelectedSessionId)
      : undefined;

    if (fallbackSession && fallbackSession.id !== selectedSessionId) {
      setSelectedSessionId(fallbackSession.id);
      return;
    }

    if (!fallbackSession && selectedSessionId) {
      setSelectedSessionId(null);
    }
  }, [
    loadingSessions,
    searchParams,
    selectedSessionId,
    sessions,
  ]);

  const handleCreateAndSend = useCallback(async (
    text: string,
    imageParts?: ImagePartData[],
    agentOverride?: string,
    modelOverride?: { providerID: string; modelID: string } | null,
  ) => {
    try {
      const response = await client.post('/api/session', { title: 'New Session' });
      const newSessionId = response.data.id;

      addSession(response.data);
      setSelectedAgent('rex');
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      const effectiveAgent = agentOverride || 'rex';
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (modelOverride) payload.model = modelOverride;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, toast, t]);

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
    <div className="h-full w-full flex overflow-hidden">
      {/* ── Sidebar ── */}
      <div
        className={`bg-white border-r border-gray-100 flex flex-col transition-all duration-300 flex-shrink-0 h-full overflow-hidden ${
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
              className="w-full pl-8 pr-3 py-2 text-left bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 hover:border-gray-300 shadow-sm hover:shadow transition-all disabled:opacity-60 disabled:cursor-not-allowed text-sm font-medium"
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
              className="w-full pl-8 pr-3 py-1.5 text-sm bg-gray-100 rounded-lg border-0 outline-none focus:bg-gray-200 transition-colors placeholder:text-gray-400 text-gray-700"
            />
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-hide pb-2">
          {sessions.length === 0 ? (
            <div className="text-center py-10 px-4 text-gray-400">
              <MessageSquare className="w-10 h-10 mx-auto mb-2 opacity-40" />
              <p className="text-sm">{t('noSessions')}</p>
            </div>
          ) : groupedSessions.length === 0 ? (
            <div className="text-center py-8 px-4 text-gray-400">
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
              <div key={key}>
                <div className="px-4 pt-4 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide select-none">
                  {t(labelKey, labelKey)}
                </div>
                {visibleItems.map((session) => (
                  <div
                    key={session.id}
                    onClick={() => selectMode ? handleToggleCheck(session.id) : setSelectedSessionId(session.id)}
                    className={`group relative mx-2 mb-1 px-3 py-2.5 rounded-xl border cursor-pointer transition-all duration-150 ${
                      !selectMode && selectedSessionId === session.id
                        ? 'bg-gray-100 border-gray-300 shadow-sm'
                        : selectMode && checkedIds.has(session.id)
                        ? 'bg-blue-50 border-blue-200'
                        : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50 hover:shadow-sm'
                    }`}
                  >
                    {/* Title row */}
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
                          className="w-full min-w-0 rounded border border-blue-300 bg-white px-1.5 py-0.5 text-sm text-gray-900 outline-none focus:border-blue-400"
                          aria-label={t('rename')}
                          data-session-rename-input
                        />
                      ) : (
                        <h3 className="font-semibold text-gray-900 truncate text-sm flex items-center gap-1.5">
                          <span className="truncate">{session.title}</span>
                          {session.isShared && (
                            <span className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
                              {t('sharedTag')}
                            </span>
                          )}
                        </h3>
                      )}
                    </div>
                    {/* Timestamp row */}
                    {session.time?.updated && renamingSessionId !== session.id && (
                      <p className="mt-1 text-xs text-gray-400 truncate pl-0.5">
                        {formatSessionDate(session.time.updated)}
                      </p>
                    )}

                    {/* Three-dot menu trigger */}
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
                          className={`p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all ${
                            openMenuSessionId === session.id ? 'opacity-100 text-gray-600 bg-gray-200' : 'opacity-0 group-hover:opacity-100'
                          }`}
                        >
                          <MoreHorizontal className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                {/* 展开/收起按钮 */}
                {!isSearching && hiddenCount > 0 && (
                  <button
                    onClick={() => toggleGroupExpand(key)}
                    className="mx-4 mb-1 flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors py-1"
                  >
                    <ChevronDown className="w-3 h-3" />
                    <span>{t('showMore', { count: hiddenCount })}</span>
                  </button>
                )}
                {!isSearching && isExpanded && items.length > (GROUP_DEFAULT_LIMIT[key] ?? 5) && (
                  <button
                    onClick={() => toggleGroupExpand(key)}
                    className="mx-4 mb-1 flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors py-1"
                  >
                    <ChevronDown className="w-3 h-3 rotate-180" />
                    <span>{t('showLess', 'Show less')}</span>
                  </button>
                )}
              </div>
              );
            })
          )}
        </div>

        {/* Bottom：批量操作栏 / 批量选择入口 */}
        {sessions.length > 0 && (
          <div className="border-t border-gray-100 px-3 pt-3 pb-4 flex-shrink-0">
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
        <div className="px-6 h-12 border-b border-gray-200 bg-white flex items-center justify-between flex-shrink-0 relative">
          <div className="absolute left-4 top-1/2 -translate-y-1/2">
            <button
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
              className="p-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 shadow-sm hover:shadow-md transition-all duration-200"
              title={sidebarCollapsed ? t('showHistory') : t('hideHistory')}
            >
              {sidebarCollapsed ? <PanelLeft className="w-5 h-5" /> : <PanelLeftClose className="w-5 h-5" />}
            </button>
          </div>

          <div className="flex items-center gap-3 ml-14">
            <h2 className="text-base font-semibold text-gray-900">
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
          display={{ compact: false, showActions: true, showTimestamp: true }}
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
          model={selectedPromptModel}
          welcomeContent={(setInput) => (
            <WelcomeScreen onSuggestion={setInput} />
          )}
          toolbarSlot={
            <div className="relative" data-agent-selector>
              <button
                onClick={() => setShowAgentOptions(!showAgentOptions)}
                className="flex items-center gap-1.5 h-8 max-w-[220px] px-2 rounded-lg text-zinc-600 hover:text-zinc-900 hover:bg-zinc-200/60 transition-colors text-sm"
              >
                <Bot className="w-4 h-4 flex-shrink-0" />
                <span className="font-medium truncate">
                  {formatAgentName(selectedAgentInfo?.name ?? selectedAgent)}
                </span>
                <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAgentOptions ? 'rotate-180' : ''}`} />
              </button>
              {showAgentOptions && (
                <div className="absolute left-0 bottom-full mb-2 w-[34rem] max-w-[calc(100vw-2rem)] bg-white border border-zinc-200 rounded-xl shadow-lg z-50 overflow-hidden">
                  <div className="flex items-center justify-between gap-3 border-b border-zinc-100 px-3 py-2">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-zinc-700">{t('agentPicker.title')}</div>
                      <div className="text-[11px] text-zinc-400">{t('agentPicker.hint')}</div>
                    </div>
                    <div className="inline-flex shrink-0 items-center rounded-lg border border-zinc-200 bg-zinc-50 p-0.5 text-[11px]">
                      {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                        <button
                          key={filter}
                          type="button"
                          onClick={() => setAgentSourceFilter(filter)}
                          className={`rounded-md px-2 py-1 transition-colors ${
                            agentSourceFilter === filter
                              ? 'bg-zinc-800 text-white'
                              : 'text-zinc-500 hover:bg-white hover:text-zinc-800'
                          }`}
                        >
                          {t(`agentPicker.filter.${filter}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="p-1.5 space-y-0.5 max-h-80 overflow-y-auto">
                    {loadingAgents ? (
                      <div className="p-4 text-center text-sm text-zinc-500">{t('loading')}</div>
                    ) : filteredChatAgents.length > 0 ? (
                      filteredChatAgents.map((agent) => {
                        const primaryDesc = getAgentDisplayDescription(agent, i18n.language) || t('smartAssistant');
                        const secondaryDesc = getAgentSecondaryDescription(agent, i18n.language);
                        return (
                        <button
                          key={agent.name}
                          onClick={() => { setSelectedAgent(agent.name); setShowAgentOptions(false); }}
                          className={`w-full min-w-0 text-left px-3 py-2 rounded-lg transition-colors ${
                            selectedAgent === agent.name
                              ? 'bg-zinc-100 text-zinc-900'
                              : 'hover:bg-zinc-50 text-zinc-700'
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <Bot className="w-4 h-4 text-zinc-500 shrink-0" />
                            <span className="w-32 shrink-0 truncate text-sm font-semibold text-zinc-900">
                              {formatAgentName(agent.name)}
                            </span>
                            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                              agent.mode === 'primary'
                                ? 'bg-slate-100 text-slate-600'
                                : agent.native
                                  ? 'bg-blue-50 text-blue-600'
                                  : 'bg-teal-50 text-teal-600'
                            }`}>
                              {agent.mode === 'primary'
                                ? t('agentPicker.badge.primary')
                                : agent.native
                                  ? t('agentPicker.badge.builtin')
                                  : t('agentPicker.badge.custom')}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-xs text-zinc-500">
                              {primaryDesc}
                              {secondaryDesc ? <span className="text-zinc-400"> / {secondaryDesc}</span> : null}
                            </span>
                          </div>
                        </button>
                        );
                      })
                    ) : (
                      <div className="p-4 text-center text-sm text-zinc-500">{t('noAgents')}</div>
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
                className="flex h-8 max-w-[260px] items-center gap-1.5 rounded-lg px-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50"
                title={selectedModelOption ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}` : t('modelPicker.empty')}
              >
                <Cpu className="h-4 w-4 shrink-0" />
                <span className="truncate font-medium">
                  {selectedModelOption?.label ?? (loadingProviders || loadingEnabledModels ? t('loading') : t('modelPicker.empty'))}
                </span>
                <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${showModelOptions ? 'rotate-180' : ''}`} />
              </button>
              {showModelOptions && (
                <div className="absolute left-0 bottom-full z-50 mb-2 w-[32rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-lg">
                  <div className="border-b border-zinc-100 px-3 py-2">
                    <div className="text-xs font-semibold text-zinc-700">{t('modelPicker.title')}</div>
                    <div className="text-[11px] text-zinc-400">{t('modelPicker.hint')}</div>
                  </div>
                  <div className="max-h-80 overflow-y-auto p-1.5">
                    {loadingProviders || loadingEnabledModels ? (
                      <div className="p-4 text-center text-sm text-zinc-500">{t('loading')}</div>
                    ) : groupedChatModelOptions.length > 0 ? (
                      groupedChatModelOptions.map((group) => (
                        <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                          <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-white/95 px-2 py-1.5 text-[11px] font-semibold text-zinc-500 backdrop-blur">
                            <span className="truncate">{group.providerName}</span>
                            <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500">
                              {t('modelPicker.count', { count: group.models.length })}
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {group.models.map((option) => (
                              <button
                                key={option.key}
                                type="button"
                                onClick={() => {
                                  setSelectedModelKey(option.key);
                                  setShowModelOptions(false);
                                }}
                                className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                                  selectedModelOption?.key === option.key
                                    ? 'bg-zinc-100 text-zinc-900'
                                    : 'text-zinc-700 hover:bg-zinc-50'
                                }`}
                              >
                                <div className="flex min-w-0 items-center gap-2">
                                  <Cpu className="h-4 w-4 shrink-0 text-zinc-500" />
                                  <span className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-900">{option.label}</span>
                                  {option.supportsVision === true && (
                                    <span className="shrink-0 rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-600">
                                      {t('modelPicker.vision')}
                                    </span>
                                  )}
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="p-4 text-center text-sm text-zinc-500">{t('modelPicker.empty')}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          }
        />
      </div>

      {/* Three-dot dropdown — rendered outside sidebar to avoid overflow:hidden clipping */}
      {openMenuSessionId && menuAnchor && (() => {
        const sid = openMenuSessionId;
        const session = sessions.find(s => s.id === sid);
        if (!session) return null;
        return (
          <div
            className="fixed z-50 w-36 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
            style={{ top: menuAnchor.top, right: menuAnchor.right }}
            data-session-menu-portal
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
            >
              <PencilLine className="w-3.5 h-3.5" />
              <span>{t('rename')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); setOpenMenuSessionId(null); setMenuAnchor(null); }}
              disabled={downloadingSessionId === session.id}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Download className="w-3.5 h-3.5" />
              <span>{t('downloadJson')}</span>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleShareSession(session.id, !session.isShared); }}
              disabled={session.canWrite === false}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Share2 className="w-3.5 h-3.5" />
              <span>{session.isShared ? t('unshareAction') : t('shareAction')}</span>
            </button>
            <div className="mx-2.5 my-1 border-t border-gray-100" />
            <button
              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); setMenuAnchor(null); void handleDeleteSession(session.id); }}
              disabled={session.canDelete === false}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-red-600 hover:bg-red-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
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
      <h3 className="text-xl font-bold text-gray-900 mb-3">{t('welcome.title')}</h3>
      <p className="text-sm text-gray-600 mb-8">{t('welcome.description')}</p>

      <div className="flex flex-wrap gap-3 justify-center">
        <button
          onClick={() => onSuggestion(t('welcome.alertTriageSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-slate-400 hover:bg-slate-50 transition-all duration-200 shadow-sm hover:shadow-md"
        >
          <Shield className="w-5 h-5 text-slate-600" />
          <span className="font-medium text-gray-700">{t('welcome.alertTriage')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.threatHuntingSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-orange-400 hover:bg-orange-50 transition-all duration-200 shadow-sm hover:shadow-md"
        >
          <Search className="w-5 h-5 text-orange-600" />
          <span className="font-medium text-gray-700">{t('welcome.threatHunting')}</span>
        </button>
        <button
          onClick={() => onSuggestion(t('welcome.incidentResponseSuggestion'))}
          className="flex items-center gap-2 px-5 py-3 bg-white border-2 border-gray-200 rounded-xl hover:border-amber-400 hover:bg-amber-50 transition-all duration-200 shadow-sm hover:shadow-md"
        >
          <AlertTriangle className="w-5 h-5 text-amber-600" />
          <span className="font-medium text-gray-700">{t('welcome.incidentResponse')}</span>
        </button>
      </div>
    </div>
  );
}
