import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ListTodo, Play, RotateCcw, XCircle, Trash2,
  ChevronLeft, ChevronRight, GripVertical, X,
} from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import SessionChat from '@/components/common/SessionChat';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useTaskExecutions } from '@/hooks/useTasks';
import { taskAPI, TaskExecution, TaskListParams } from '@/api/task';
import { StatusBadge, PriorityBadge, SourceBadge, ModeBadge, ActionButton } from './components';
import { formatTime, formatDuration, PAGE_SIZE } from './helpers';

export default function QueuedSection({ onRefreshGlobal }: { onRefreshGlobal: () => void }) {
  const { t } = useTranslation('task');
  const [filterKey, setFilterKey] = useState('all');
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTasks, setSelectedTasks] = useState<Set<string>>(new Set());
  const [detailTask, setDetailTask] = useState<TaskExecution | null>(null);
  const toast = useToast();
  const confirm = useConfirm();

  const QUEUED_FILTERS: { key: string; label: string; filter: Partial<TaskListParams> }[] = [
    { key: 'all',       label: t('queued.filterAll'),       filter: {} },
    { key: 'completed', label: t('queued.filterCompleted'), filter: { status: 'completed' } },
    { key: 'failed',    label: t('queued.filterFailed'),    filter: { status: 'failed' } },
  ];

  const currentFilter = QUEUED_FILTERS.find(f => f.key === filterKey)?.filter ?? {};
  const listParams = { ...currentFilter, offset: page * PAGE_SIZE, limit: PAGE_SIZE };

  const { tasks, total, loading, error, refetch } = useTaskExecutions(listParams, { pollInterval: 5000 });
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const refresh = useCallback(() => { refetch(); onRefreshGlobal(); }, [refetch, onRefreshGlobal]);
  const visibleSelectedIds = tasks
    .filter(task => selectedTasks.has(task.id))
    .map(task => task.id);
  const hasVisibleSelection = visibleSelectedIds.length > 0;
  const allVisibleSelected = tasks.length > 0 && tasks.every(task => selectedTasks.has(task.id));

  // Keep detailTask in sync: update from list data when available,
  // but never clear it just because the task left the current page.
  useEffect(() => {
    if (!selectedId) { setDetailTask(null); return; }
    const found = tasks.find(t => t.id === selectedId);
    if (found) setDetailTask(found);
  }, [tasks, selectedId]);

  // Selection is scoped to the current visible list so users never batch
  // operate on hidden rows from a previous page or filter.
  useEffect(() => {
    setSelectedTasks(prev => {
      let changed = false;
      const visibleIds = new Set(tasks.map(task => task.id));
      const next = new Set<string>();
      prev.forEach(id => {
        if (visibleIds.has(id)) {
          next.add(id);
        } else {
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [tasks]);

  const markViewedIfNeeded = useCallback(async (task: TaskExecution) => {
    if (task.status !== 'completed' || task.deliveryStatus !== 'unread') {
      return task;
    }
    try {
      const res = await taskAPI.markExecutionViewed(task.id);
      refresh();
      return res.data;
    } catch {
      return task;
    }
  }, [refresh]);

  // When the user selects a task, also fetch it directly to be sure
  // we have the latest data even if it's not on the current page.
  const fetchDetailTask = useCallback(async (task: TaskExecution) => {
    try {
      const res = await taskAPI.getExecution(task.id);
      const detail = await markViewedIfNeeded(res.data);
      setDetailTask(detail);
    } catch { /* ignore — list sync will cover it */ }
  }, [markViewedIfNeeded]);

  const refreshWithDetail = useCallback(() => {
    refresh();
    if (selectedId) {
      const selected = tasks.find(t => t.id === selectedId);
      if (selected) fetchDetailTask(selected);
    }
  }, [refresh, selectedId, fetchDetailTask, tasks]);

  const closeDetail = useCallback(() => {
    setSelectedId(null);
    setDetailTask(null);
  }, []);

  const openDetail = useCallback((task: TaskExecution) => {
    if (selectedId === task.id) {
      closeDetail();
      return;
    }
    setSelectedId(task.id);
    setDetailTask(task);
    fetchDetailTask(task);
  }, [selectedId, closeDetail, fetchDetailTask]);

  const handleAction = async (action: string, taskId: string) => {
    try {
      switch (action) {
        case 'cancel':
          await taskAPI.cancelExecution(taskId);
          break;
        case 'retry':  await taskAPI.retryExecution(taskId);  break;
        case 'rerun':
          await taskAPI.rerunExecution(taskId);
          break;
        case 'delete': {
          const ok = await confirm({
            description: t('queued.confirmDelete'),
            variant: 'danger',
            confirmText: t('common:button.delete'),
          });
          if (!ok) return;
          await taskAPI.deleteExecution(taskId);
          if (selectedId === taskId) closeDetail();
          break;
        }
      }
      refresh();
      if (selectedId === taskId) {
        const selected = tasks.find(t => t.id === taskId);
        if (selected) fetchDetailTask(selected);
      }
    } catch (err: unknown) {
      toast.error(t('queued.actionFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const toggleSelect = (id: string) =>
    setSelectedTasks(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; });

  const handleBatchCancel = async () => {
    if (!hasVisibleSelection) return;
    const executionIds = [...new Set(visibleSelectedIds)];
    const ok = await confirm({
      description: t('queued.confirmBatchCancel', { count: executionIds.length }),
      variant: 'warning',
      confirmText: t('queued.confirmBatchCancelBtn'),
    });
    if (!ok) return;
    try {
      await taskAPI.batchCancelExecutions(executionIds);
      setSelectedTasks(new Set());
      refresh();
    } catch (err: unknown) {
      toast.error(t('queued.actionFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const handleBatchDelete = async () => {
    if (!hasVisibleSelection) return;
    const executionIds = [...new Set(visibleSelectedIds)];
    const ok = await confirm({
      description: t('queued.confirmBatchDelete', { count: executionIds.length }),
      variant: 'danger',
      confirmText: t('common:button.delete'),
    });
    if (!ok) return;
    try {
      await taskAPI.batchDeleteExecutions(executionIds);
      setSelectedTasks(new Set());
      if (selectedId && selectedTasks.has(selectedId)) closeDetail();
      refresh();
    } catch (err: unknown) {
      toast.error(t('queued.actionFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const handleToggleSelectAll = () => {
    setSelectedTasks(prev => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        tasks.forEach(task => next.delete(task.id));
      } else {
        tasks.forEach(task => next.add(task.id));
      }
      return next;
    });
  };

  const handleFilterChange = (nextFilterKey: string) => {
    setFilterKey(nextFilterKey);
    setPage(0);
    setSelectedTasks(new Set());
    closeDetail();
  };

  const handlePageChange = (nextPage: number) => {
    setPage(nextPage);
    setSelectedTasks(new Set());
  };

  if (loading && tasks.length === 0) return <div className="flex justify-center py-12"><LoadingSpinner /></div>;
  if (error) return <div className="text-center py-12 text-red-500">{error}</div>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          {QUEUED_FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => handleFilterChange(f.key)}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                filterKey === f.key ? 'bg-white text-slate-800 shadow-sm font-medium' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        {hasVisibleSelection && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500">{t('queued.selectedCount', { count: visibleSelectedIds.length })}</span>
            <button onClick={handleBatchCancel} className="px-3 py-1.5 text-sm bg-yellow-50 text-yellow-700 rounded-md hover:bg-yellow-100">{t('queued.batchCancel')}</button>
            <button onClick={handleBatchDelete} className="px-3 py-1.5 text-sm bg-red-50 text-red-700 rounded-md hover:bg-red-100">{t('queued.batchDelete')}</button>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {tasks.length === 0 ? (
          <EmptyState
            icon={<ListTodo className="w-8 h-8" />}
            title={t('queued.emptyTitle')}
            description={t('queued.emptyDescription')}
          />
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="w-10 px-3 py-3">
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={handleToggleSelectAll}
                      className="rounded border-gray-300"
                    />
                  </th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colStatus')}</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colSource')}</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colName')}</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colMode')}</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colPriority')}</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">{t('queued.colTime')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {tasks.map(task => (
                  <tr
                    key={task.id}
                    onClick={() => openDetail(task)}
                    className={`cursor-pointer transition-colors ${selectedId === task.id ? 'bg-slate-50' : 'hover:bg-gray-50'}`}
                  >
                    <td className="px-3 py-3" onClick={e => e.stopPropagation()}>
                      <input type="checkbox" checked={selectedTasks.has(task.id)} onChange={() => toggleSelect(task.id)} className="rounded border-gray-300" />
                    </td>
                    <td className="px-3 py-3"><StatusBadge status={task.status} /></td>
                    <td className="px-3 py-3"><SourceBadge sourceType={task.source?.sourceType ?? 'user_conversation'} /></td>
                    <td className="px-3 py-3 font-medium text-gray-900 max-w-[200px] truncate">
                      {task.deliveryStatus === 'unread' && task.status === 'completed' && (
                        <span className="w-1.5 h-1.5 bg-sky-500 rounded-full inline-block mr-1.5 mb-0.5 align-middle" />
                      )}
                      {task.title}
                    </td>
                    <td className="px-3 py-3"><ModeBadge mode={task.executionMode} agent={task.agentName} /></td>
                    <td className="px-3 py-3"><PriorityBadge priority={task.priority} /></td>
                    <td className="px-3 py-3 text-gray-400 text-xs whitespace-nowrap">
                      {task.startedAt
                        ? formatTime(task.startedAt)
                        : task.queuedAt
                        ? formatTime(task.queuedAt)
                        : formatTime(task.createdAt)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
              <span className="text-sm text-gray-500">
                {t('queued.pagination', { total, page: page + 1, totalPages })}
              </span>
              <div className="flex gap-2">
                <button onClick={() => handlePageChange(Math.max(0, page - 1))} disabled={page === 0} className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed">
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button onClick={() => handlePageChange(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1} className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed">
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {detailTask && (
        <QueuedDetailPanel task={detailTask} onClose={closeDetail} onAction={handleAction} onRefresh={refreshWithDetail} />
      )}
    </div>
  );
}

function QueuedDetailPanel({ task, onClose, onAction, onRefresh }: {
  task: TaskExecution;
  onClose: () => void;
  onAction: (action: string, taskId: string) => void;
  onRefresh?: () => void;
}) {
  const DRAWER_DEFAULT_WIDTH = 640;
  const DRAWER_MIN_WIDTH = 480;
  const DRAWER_MAX_WIDTH = 960;
  const { t } = useTranslation('task');
  const sessionId = task.sessionID;
  const isActive = ['queued', 'running'].includes(task.status);
  const emptyText = ['pending', 'queued'].includes(task.status)
    ? t('queued.detailWaiting')
    : t('queued.detailNoRecord');
  const [drawerWidth, setDrawerWidth] = useState(DRAWER_DEFAULT_WIDTH);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(DRAWER_DEFAULT_WIDTH);
  const drawerWidthRef = useRef(drawerWidth);

  drawerWidthRef.current = drawerWidth;

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    dragStartX.current = e.clientX;
    dragStartWidth.current = drawerWidthRef.current;

    const handleMouseMove = (ev: MouseEvent) => {
      const delta = dragStartX.current - ev.clientX;
      setDrawerWidth(Math.min(DRAWER_MAX_WIDTH, Math.max(DRAWER_MIN_WIDTH, dragStartWidth.current + delta)));
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }, []);

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div
        className="fixed right-0 top-0 bottom-0 z-50 flex flex-col bg-white shadow-2xl transition-colors"
        style={{ width: drawerWidth, maxWidth: '100%' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className={`absolute -left-1 top-0 bottom-0 z-10 w-3 cursor-ew-resize transition-colors ${
            isDragging ? 'bg-red-500/10' : 'hover:bg-red-400/10'
          }`}
          onMouseDown={handleDragStart}
        >
          <div className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 ${
            isDragging ? 'bg-red-500' : 'bg-gray-200'
          }`} />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <GripVertical className={`w-3 h-6 ${isDragging ? 'text-red-500' : 'text-gray-300 hover:text-gray-500'}`} />
          </div>
        </div>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 flex-shrink-0">
          <h3 className="font-semibold text-gray-900 truncate pr-2">{task.title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 flex-shrink-0">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>

        <div className="px-6 py-4 border-b border-gray-100 flex-shrink-0 space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge status={task.status} />
            <PriorityBadge priority={task.priority} />
            <ModeBadge mode={task.executionMode} agent={task.agentName} />
            {task.durationMs != null && (
              <span className="text-xs text-gray-400">{formatDuration(task.durationMs)}</span>
            )}
          </div>

          {task.description && (
            <p className="text-xs text-gray-500 break-words">{task.description}</p>
          )}

          {(task.executionInputSnapshot?.tags?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-1">
              {task.executionInputSnapshot.tags.map((tag: string) => <span key={tag} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-xs">{tag}</span>)}
            </div>
          )}

          <div className="flex flex-wrap gap-1.5">
            {!['completed', 'cancelled', 'failed'].includes(task.status) && (
              <ActionButton icon={<XCircle className="w-3 h-3" />} label={t('queued.actionCancel')} onClick={() => onAction('cancel', task.id)} color="gray" />
            )}
            {task.status === 'failed' && (
              <ActionButton icon={<RotateCcw className="w-3 h-3" />} label={t('queued.actionRetry')} onClick={() => onAction('retry', task.id)} color="blue" />
            )}
            {['completed', 'cancelled', 'failed'].includes(task.status) && (
              <ActionButton icon={<Play className="w-3 h-3" />} label={t('queued.actionRerun')} onClick={() => onAction('rerun', task.id)} color="green" />
            )}
            <ActionButton icon={<Trash2 className="w-3 h-3" />} label={t('queued.actionDelete')} onClick={() => onAction('delete', task.id)} color="red" />
          </div>
        </div>

        <SessionChat
          sessionId={sessionId}
          live={isActive}
          hideInput
          emptyText={emptyText}
          className="flex-1 min-h-0"
          onSSEEvent={(event) => {
            if (event.type === 'task.updated' && event.properties?.executionID === task.id) {
              onRefresh?.();
            }
          }}
        />
      </div>
    </>
  );
}
