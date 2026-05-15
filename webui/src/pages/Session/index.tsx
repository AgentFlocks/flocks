import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  MessageSquare, Plus, Trash2, Wifi, WifiOff,
  ChevronDown, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { useSearchParams } from 'react-router-dom';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat, { type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import { sessionApi } from '@/api/session';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import client from '@/api/client';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { buildPromptParts, type ImagePartData } from '@/utils/imageUpload';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';

function sanitizeSessionExportName(value: string) {
  const trimmed = value.trim();
  return trimmed
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'session';
}

export default function SessionPage() {
  const { t, i18n } = useTranslation('session');
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState('rex');
  const [showAgentOptions, setShowAgentOptions] = useState(false);
  const [sseStatus, setSseStatus] = useState<SSEConnectionStatus>('disconnected');
  const [creating, setCreating] = useState(false);
  const [pendingInitialMessage, setPendingInitialMessage] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [openMenuSessionId, setOpenMenuSessionId] = useState<string | null>(null);
  const [renamingSessionId, setRenamingSessionId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameSubmitting, setRenameSubmitting] = useState(false);
  const [downloadingSessionId, setDownloadingSessionId] = useState<string | null>(null);
  const supportsVision = useDefaultModelVision();
  const [searchQuery, setSearchQuery] = useState('');
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSubmitInFlightRef = useRef(false);
  const toast = useToast();

  const { sessions, loading: loadingSessions, refetch: refetchSessions, updateSessionTitle, removeSession, removeSessions, addSession } = useSessions();
  const { agents, loading: loadingAgents } = useAgents();
  const rexAgents = useMemo(() => agents.filter(a => a.name.toLowerCase() === 'rex'), [agents]);
  const selectedSession = useMemo(
    () => sessions.find(s => s.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

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
    if (!openMenuSessionId) return;
    const handle = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-session-actions]')) {
        setOpenMenuSessionId(null);
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
      setSelectedSessionId(response.data.id);
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    } finally {
      setCreating(false);
    }
  }, [creating, addSession, toast, t]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && e.key === 'n') {
        e.preventDefault();
        void handleCreateSession();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleCreateSession]);

  const handleCreateAndSend = useCallback(async (
    text: string,
    imageParts?: ImagePartData[],
  ) => {
    try {
      const response = await client.post('/api/session', { title: 'New Session' });
      const newSessionId = response.data.id;

      addSession(response.data);
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      if (selectedAgent) payload.agent = selectedAgent;
      client.post(`/api/session/${newSessionId}/prompt_async`, payload).catch((err: any) => {
        toast.error(t('chat.sendFailed', 'Send failed'), err.message);
      });
    } catch (err: any) {
      toast.error(t('createFailed'), err.message);
    }
  }, [addSession, selectedAgent, toast, t]);

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
        {/* Header */}
        {selectMode ? (
          <div className="px-3 h-14 flex items-center gap-1.5 border-b border-gray-100 flex-shrink-0">
            <button
              onClick={handleExitSelectMode}
              className="px-2.5 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            >
              {t('cancelSelect')}
            </button>
            <button
              onClick={handleSelectAll}
              className="flex-1 text-sm text-blue-600 hover:bg-blue-50 rounded-lg py-1.5 transition-colors"
            >
              {checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
            </button>
            <button
              onClick={handleBatchDelete}
              disabled={checkedIds.size === 0 || batchDeleting}
              className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title={t('deleteSelected', { count: checkedIds.size })}
            >
              {batchDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            </button>
          </div>
        ) : (
          <div className="px-3 pt-3 pb-2 flex-shrink-0 space-y-2">
            <button
              onClick={handleCreateSession}
              disabled={creating}
              className="w-full flex items-center justify-between px-3 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-60 disabled:cursor-not-allowed text-sm font-medium"
            >
              <div className="flex items-center gap-2">
                {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                <span>{t('newSession')}</span>
              </div>
              <kbd className="text-xs text-gray-500 bg-gray-800 border border-gray-700 px-1.5 py-0.5 rounded font-mono leading-none">
                Alt+N
              </kbd>
            </button>
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
        )}

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
            groupedSessions.map(({ key, labelKey, items }) => (
              <div key={key}>
                <div className="px-4 pt-4 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wide select-none">
                  {t(labelKey, labelKey)}
                </div>
                {items.map((session) => (
                  <div
                    key={session.id}
                    onClick={() => selectMode ? handleToggleCheck(session.id) : setSelectedSessionId(session.id)}
                    className={`group relative mx-2 px-2 py-2 rounded-lg cursor-pointer transition-colors ${
                      !selectMode && selectedSessionId === session.id
                        ? 'bg-gray-100'
                        : selectMode && checkedIds.has(session.id)
                        ? 'bg-blue-50'
                        : 'hover:bg-gray-100'
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
                          className="w-full min-w-0 rounded border border-blue-300 bg-white px-1.5 py-0.5 text-sm text-gray-900 outline-none focus:border-blue-400"
                          aria-label={t('rename')}
                          data-session-rename-input
                        />
                      ) : (
                        <span className="truncate text-sm text-gray-800">{session.title}</span>
                      )}
                    </div>

                    {/* Three-dot menu */}
                    {!selectMode && (
                      <div className="absolute right-1.5 top-1/2 -translate-y-1/2" data-session-actions>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenMenuSessionId(prev => prev === session.id ? null : session.id);
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
                        {openMenuSessionId === session.id && (
                          <div className="absolute right-0 top-full z-20 mt-1 w-32 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-md">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleStartRename(session.id, session.title); }}
                              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                            >
                              <PencilLine className="w-3.5 h-3.5" />
                              <span>{t('rename')}</span>
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); void handleDownloadSession(session.id, session.title); }}
                              disabled={downloadingSessionId === session.id}
                              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <Download className="w-3.5 h-3.5" />
                              <span>{t('downloadJson')}</span>
                            </button>
                            <div className="mx-2.5 my-1 border-t border-gray-100" />
                            <button
                              onClick={(e) => { e.stopPropagation(); setOpenMenuSessionId(null); void handleDeleteSession(session.id); }}
                              disabled={session.canDelete === false}
                              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-red-600 hover:bg-red-50 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                              <span>{t('deleteAction')}</span>
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))
          )}
        </div>

        {/* Bottom: manage / select mode entry */}
        {!selectMode && sessions.length > 0 && (
          <div className="border-t border-gray-100 px-3 py-2 flex-shrink-0">
            <button
              onClick={handleEnterSelectMode}
              className="w-full flex items-center justify-center gap-1.5 py-1.5 text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-50 rounded-lg transition-colors"
            >
              <CheckSquare className="w-3.5 h-3.5" />
              <span>{t('selectMode')}</span>
            </button>
          </div>
        )}
      </div>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col overflow-hidden h-full min-w-0">
        {/* Header */}
        <div className="px-6 h-16 border-b border-gray-200 bg-white flex items-center justify-between flex-shrink-0 relative">
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
            <h2 className="text-lg font-semibold text-gray-900">
              {selectedSession?.title || t('newSession')}
            </h2>
            {selectedSessionId && (
              <span className="inline-flex items-center">
                {sseStatus === 'connected' ? (
                  <span title={t('realTimeOk')}><Wifi className="w-4 h-4 text-green-500" /></span>
                ) : sseStatus === 'reconnecting' ? (
                  <span title={t('reconnecting')}><WifiOff className="w-4 h-4 text-yellow-500 animate-pulse" /></span>
                ) : sseStatus === 'failed' ? (
                  <span title={t('connectionFailed')}><WifiOff className="w-4 h-4 text-red-500" /></span>
                ) : (
                  <span title={t('notConnected')}><WifiOff className="w-4 h-4 text-gray-400" /></span>
                )}
              </span>
            )}
          </div>

          {/* Agent Selector */}
          <div className="relative" data-agent-selector>
            <button
              onClick={() => setShowAgentOptions(!showAgentOptions)}
              className="flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900 transition-colors"
            >
              <Bot className="w-4 h-4 text-purple-600" />
              <span className="font-medium text-purple-600">
                {selectedAgent.charAt(0).toUpperCase() + selectedAgent.slice(1)}
              </span>
              <ChevronDown className={`w-4 h-4 transition-transform ${showAgentOptions ? 'rotate-180' : ''}`} />
            </button>

            {showAgentOptions && (
              <div className="absolute right-0 top-full mt-2 w-80 bg-white border border-gray-200 rounded-xl shadow-lg z-50 overflow-hidden">
                <div className="p-2 space-y-1 max-h-80 overflow-y-auto">
                  {loadingAgents ? (
                    <div className="p-4 text-center text-sm text-gray-500">{t('loading')}</div>
                  ) : rexAgents.length > 0 ? (
                    rexAgents.map((agent) => (
                      <button
                        key={agent.name}
                        onClick={() => { setSelectedAgent(agent.name); setShowAgentOptions(false); }}
                        className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                          selectedAgent === agent.name
                            ? 'bg-purple-50 text-purple-900 border border-purple-200'
                            : 'hover:bg-gray-50'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <Bot className="w-4 h-4 text-purple-600" />
                          <div className="flex-1">
                            <div className="font-medium text-sm">
                              {agent.name.charAt(0).toUpperCase() + agent.name.slice(1)}
                            </div>
                            <div className="text-xs text-gray-500 mt-0.5">
                              {getAgentDisplayDescription(agent, i18n.language) || t('smartAssistant')}
                            </div>
                          </div>
                        </div>
                      </button>
                    ))
                  ) : (
                    <div className="p-4 text-center text-sm text-gray-500">{t('noAgents')}</div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Chat — powered by unified SessionChat */}
        <SessionChat
          key={selectedSessionId ?? 'empty-session'}
          sessionId={selectedSessionId}
          live={Boolean(selectedSessionId)}
          display={{ compact: false, showActions: true, showTimestamp: false }}
          agentName={selectedAgent}
          className="flex-1 min-h-0"
          initialMessage={pendingInitialMessage}
          onInitialMessageConsumed={() => setPendingInitialMessage(null)}
          onSseStatusChange={selectedSessionId ? setSseStatus : undefined}
          onSSEEvent={handleSSEEvent}
          onError={handleChatError}
          onCreateAndSend={handleCreateAndSend}
          onCreateNewSession={handleCreateSession}
          onStreamingDone={() => setPendingInitialMessage(null)}
          supportsVision={supportsVision}
          welcomeContent={(setInput) => (
            <WelcomeScreen onSuggestion={setInput} />
          )}
        />
      </div>
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
