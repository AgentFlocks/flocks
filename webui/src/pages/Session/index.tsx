import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  MessageSquare, Plus, Trash2, Wifi, WifiOff,
  ChevronDown, Sparkles, Shield, Search, AlertTriangle,
  PanelLeftClose, PanelLeft, Bot, Loader2,
  Workflow as WorkflowIcon, Settings2, CheckSquare,
  MoreHorizontal, PencilLine, Download, Users,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat, { type SSEChatEvent, type SSEConnectionStatus } from '@/components/common/SessionChat';
import { sessionApi } from '@/api/session';
import { useSessions } from '@/hooks/useSessions';
import { useAgents } from '@/hooks/useAgents';
import client from '@/api/client';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import { formatSessionDate } from '@/utils/time';

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
  // Capture params on mount only — avoids re-running when setSearchParams clears the URL.
  const initialSearchParamsRef = useRef(searchParams);
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

  // Handle ?session=<id>&message=<text> query params (e.g. from onboarding).
  // We read from the ref captured at mount time so the effect only runs once
  // and doesn't re-trigger when setSearchParams clears the URL.
  useEffect(() => {
    const params = initialSearchParamsRef.current;
    const sessionParam = params.get('session');
    const messageParam = params.get('message');
    if (sessionParam) {
      setSelectedSessionId(sessionParam);
      if (messageParam) {
        setPendingInitialMessage(messageParam);
      }
      setSearchParams({}, { replace: true });
    }
  }, []);

  // Auto select first session
  useEffect(() => {
    if (!selectedSessionId && sessions.length > 0) {
      setSelectedSessionId(sessions[0].id);
    }
  }, [sessions, selectedSessionId]);

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

  const handleCreateAndSend = useCallback(async (text: string) => {
    try {
      const response = await client.post('/api/session', { title: 'New Session' });
      const newSessionId = response.data.id;

      addSession(response.data);
      setSelectedSessionId(newSessionId);

      const payload: Record<string, unknown> = { parts: [{ type: 'text', text }] };
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
      toast.error(t('deleteFailed'), '你没有权限删除该会话');
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

  const handleShareSession = useCallback(async (sessionId: string) => {
    try {
      await sessionApi.share(sessionId);
      await refetchSessions();
    } catch (err: any) {
      toast.error(t('shareFailed', '共享失败'), err.message);
    }
  }, [refetchSessions, t, toast]);

  const handleUnshareSession = useCallback(async (sessionId: string) => {
    try {
      await sessionApi.unshare(sessionId);
      await refetchSessions();
    } catch (err: any) {
      toast.error(t('unshareFailed', '停止共享失败'), err.message);
    }
  }, [refetchSessions, t, toast]);

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
        className={`bg-white border-r border-gray-200 flex flex-col transition-all duration-300 flex-shrink-0 h-full overflow-hidden ${
          sidebarCollapsed ? 'w-0' : 'w-80'
        }`}
      >
        <div className="px-4 h-16 border-b border-gray-200 flex-shrink-0 flex items-center gap-2">
          {selectMode ? (
            <>
              <button
                onClick={handleExitSelectMode}
                className="px-3 py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
              >
                {t('cancelSelect')}
              </button>
              <button
                onClick={handleSelectAll}
                className="flex-1 text-sm text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded-lg py-2 transition-colors"
              >
                {checkedIds.size === sessions.length && sessions.length > 0 ? t('deselectAll') : t('selectAll')}
              </button>
              <button
                onClick={handleBatchDelete}
                disabled={checkedIds.size === 0 || batchDeleting}
                className="flex items-center gap-1.5 px-3 py-2 text-sm text-red-600 hover:text-red-800 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {batchDeleting
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <Trash2 className="w-4 h-4" />}
                <span>{t('deleteSelected', { count: checkedIds.size })}</span>
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleCreateSession}
                disabled={creating}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 hover:border-gray-300 transition-colors disabled:opacity-60 disabled:cursor-not-allowed shadow-sm"
              >
                {creating
                  ? <Loader2 className="w-5 h-5 animate-spin" />
                  : <Plus className="w-5 h-5" />}
                <span className="font-medium">{t('newSession')}</span>
              </button>
              <button
                onClick={handleEnterSelectMode}
                title={t('selectMode')}
                className="p-2.5 bg-white border border-gray-200 text-gray-500 rounded-xl hover:bg-gray-50 hover:border-gray-300 hover:text-gray-700 transition-colors shadow-sm"
              >
                <CheckSquare className="w-5 h-5" />
              </button>
            </>
          )}
        </div>

        <div className="flex-1 overflow-y-auto overflow-x-hidden p-4 space-y-2">
          {sessions.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <MessageSquare className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p className="text-sm">{t('noSessions')}</p>
            </div>
          ) : (
            sessions.map((session) => (
              <div
                key={session.id}
                onClick={() => selectMode ? handleToggleCheck(session.id) : setSelectedSessionId(session.id)}
                className={`group p-3 rounded-xl cursor-pointer transition-all duration-200 ${
                  !selectMode && selectedSessionId === session.id
                    ? 'bg-gray-100 border-2 border-gray-300 shadow-sm'
                    : selectMode && checkedIds.has(session.id)
                    ? 'bg-blue-50 border-2 border-blue-300 shadow-sm'
                    : 'border-2 border-transparent hover:bg-gray-50 hover:shadow-sm'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  {selectMode && (
                    <div className="flex-shrink-0 mt-0.5 pt-0.5">
                      <input
                        type="checkbox"
                        checked={checkedIds.has(session.id)}
                        onChange={() => handleToggleCheck(session.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-4 h-4 accent-blue-500 cursor-pointer rounded"
                      />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 min-w-0">
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
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              void handleSubmitRename(session.id);
                            }
                            if (e.key === 'Escape') {
                              e.preventDefault();
                              handleCancelRename();
                            }
                          }}
                          placeholder={t('renamePlaceholder')}
                          disabled={renameSubmitting}
                          className="w-full min-w-0 rounded-md border border-blue-300 bg-white px-2 py-1 text-sm font-semibold text-gray-900 outline-none ring-0 focus:border-blue-400"
                          aria-label={t('rename')}
                          data-session-rename-input
                        />
                      ) : (
                        <h3 className="font-semibold text-gray-900 truncate text-sm flex items-center gap-1.5">
                          <span className="truncate">{session.title}</span>
                          {session.visibility === 'team_shared' && (
                            <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 flex-shrink-0">
                              <Users className="w-3 h-3" />
                              Shared
                            </span>
                          )}
                        </h3>
                      )}
                    </div>
                    {session.time?.updated && (
                      <p className="text-xs text-gray-400 mt-1 truncate">
                        {formatSessionDate(session.time.updated)}
                      </p>
                    )}
                  </div>
                  {!selectMode && (
                    <div className="relative flex-shrink-0" data-session-actions>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuSessionId(prev => prev === session.id ? null : session.id);
                        }}
                        title={t('moreActions')}
                        className={`p-1.5 text-gray-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-all ${
                          openMenuSessionId === session.id ? 'opacity-100 bg-slate-100 text-slate-700' : 'opacity-0 group-hover:opacity-100'
                        }`}
                        aria-label={t('moreActions')}
                        aria-expanded={openMenuSessionId === session.id}
                      >
                        <MoreHorizontal className="w-4 h-4" />
                      </button>
                      {openMenuSessionId === session.id && (
                        <div className="absolute right-0 top-full z-20 mt-1.5 w-32 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-md">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleStartRename(session.id, session.title);
                            }}
                            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] text-gray-700 transition-colors hover:bg-gray-50"
                          >
                            <PencilLine className="w-3.5 h-3.5" />
                            <span>{t('rename')}</span>
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleDownloadSession(session.id, session.title);
                            }}
                            disabled={downloadingSessionId === session.id}
                            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Download className="w-3.5 h-3.5" />
                            <span>{t('downloadJson')}</span>
                          </button>
                          <div className="mx-2.5 my-1 border-t border-gray-100" />
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuSessionId(null);
                              if (session.visibility === 'team_shared') {
                                void handleUnshareSession(session.id);
                              } else {
                                void handleShareSession(session.id);
                              }
                            }}
                            disabled={
                              session.visibility === 'team_shared'
                                ? session.canUnshare === false
                                : session.canShare === false
                            }
                            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] text-blue-700 transition-colors hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Users className="w-3.5 h-3.5" />
                            <span>{session.visibility === 'team_shared' ? '停止共享' : '共享给本地用户'}</span>
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuSessionId(null);
                              void handleDeleteSession(session.id);
                            }}
                            disabled={session.canDelete === false}
                            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[13px] text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            <span>{t('deleteAction')}</span>
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
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
          sessionId={selectedSessionId}
          live
          display={{ compact: false, showActions: true, showTimestamp: false }}
          agentName={selectedAgent}
          className="flex-1 min-h-0"
          initialMessage={pendingInitialMessage}
          onInitialMessageConsumed={() => setPendingInitialMessage(null)}
          onSseStatusChange={selectedSessionId ? setSseStatus : undefined}
          onSSEEvent={handleSSEEvent}
          onError={handleChatError}
          onCreateAndSend={handleCreateAndSend}
          onStreamingDone={() => setPendingInitialMessage(null)}
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
      <h3 className="text-3xl font-bold text-gray-900 mb-3">{t('welcome.title')}</h3>
      <p className="text-gray-600 mb-8 text-lg">{t('welcome.description')}</p>

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
