import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ListTodo, Play, Pause, RotateCcw, XCircle, Trash2,
  ChevronLeft, ChevronRight, GripVertical, X,
} from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import SessionChat from '@/components/common/SessionChat';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useQueueItems } from '@/hooks/useTasks';
import { taskAPI, Task, TaskListParams } from '@/api/task';
import { StatusBadge, PriorityBadge, SourceBadge, ModeBadge, ActionButton } from './components';
import { formatTime, formatDuration, PAGE_SIZE } from './helpers';

export default function QueuedSection({ onRefreshGlobal }: { onRefreshGlobal: () => void }) {
  const { t } = useTranslation('task');
  const [filterKey, setFilterKey] = useState('all');
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTasks, setSelectedTasks] = useState<Set<string>>(new Set());
  const [detailTask, setDetailTask] = useState<Task | null>(null);
  const toast = useToast();
  const confirm = useConfirm();

  const QUEUED_FILTERS: { key: string; label: string; filter: Partial<TaskListParams> }[] = [
    { key: 'all',       label: t('queued.filterAll'),       filter: {} },
    { key: 'completed', label: t('queued.filterCompleted'), filter: { status: 'completed' } },
    { key: 'failed',    label: t('queued.filterFailed'),    filter: { status: 'failed' } },
  ];

  const currentFilter = QUEUED_FILTERS.find(f => f.key === filterKey)?.filter ?? {};
  const listParams = { ...currentFilter, offset: page * PAGE_SIZE, limit: PAGE_SIZE };

  const { tasks, total, loading, error, refetch } = useQueueItems(listParams, { pollInterval: 5000 });
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Keep detailTask in sync: update from list data when available,
  // but never clear it just because the task left the current page.
  useEffect(() => {
    if (!selectedId) { setDetailTask(null); return; }
    const found = tasks.find(t => t.id === selectedId);
    if (found) setDetailTask(found);
  }, [tasks, selectedId]);

  // When the user selects a task, also fetch it directly to be sure
  // we have the latest data even if it's not on the current page.
  const fetchDetailTask = useCallback(async (task: Task) => {
    if (task.taskID) {
      setDetailTask(task);
      return;
    }
    try {
      const res = await taskAPI.get(task.id);
      setDetailTask(res.data);
    } catch { /* ignore — list sync will cover it */ }
  }, []);

  const refresh = useCallback(() => { refetch(); onRefreshGlobal(); }, [refetch, onRefreshGlobal]);

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

  const openDetail = useCallback((task: Task) => {
    if (selectedId === task.id) {
      closeDetail();
      return;
    }
    setSelectedId(task.id);
    setDetailTask(task);
    fetchDetailTask(task);
  }, [selectedId, closeDetail, fetchDetailTask]);

  const isScheduledExecutionItem = useCallback(
    (task?: Task | null) => task?.source?.sourceType === 'scheduled_trigger' && !!task?.taskID,
    [],
  );

  const handleAction = async (action: string, taskId: string) => {
    try {
      const item = tasks.find(t => t.id === taskId);
      const isScheduledExecution = isScheduledExecutionItem(item);
      const targetId = item?.taskID ?? taskId;
      switch (action) {
        case 'cancel':
          if (isScheduledExecution) await taskAPI.cancelQueueItem(taskId);
          else await taskAPI.cancel(targetId);
          break;
        case 'pause':  await taskAPI.pause(targetId);  break;
        case 'resume': await taskAPI.resume(targetId); break;
        case 'retry':  await taskAPI.retry(targetId);  break;
        case 'rerun':
          if (isScheduledExecution) await taskAPI.rerunQueueItem(taskId);
          else await taskAPI.rerun(targetId);
          break;
        case 'delete': {
          const ok = await confirm({
            description: t('queued.confirmDelete'),
            variant: 'danger',
            confirmText: t('common:button.delete'),
          });
          if (!ok) return;
          if (isScheduledExecution) await taskAPI.deleteQueueItem(taskId);
          else await taskAPI.delete(targetId);
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
    if (!selectedTasks.size) return;
    const ok = await confirm({
      description: t('queued.confirmBatchCancel', { count: selectedTasks.size }),
      variant: 'warning',
      confirmText: t('queued.confirmBatchCancelBtn'),
    });
    if (!ok) return;
    const selectedItems = [...selectedTasks]
      .map(id => tasks.find(t => t.id === id))
      .filter((task): task is Task => !!task);
    const scheduledItemIDs = selectedItems
      .filter(task => isScheduledExecutionItem(task))
      .map(task => task.id);
    const manualTaskIDs = selectedItems
      .filter(task => !isScheduledExecutionItem(task))
      .map(task => task.id);

    await Promise.all([
      ...(manualTaskIDs.length > 0 ? [taskAPI.batchCancel([...new Set(manualTaskIDs)])] : []),
      ...scheduledItemIDs.map(itemId => taskAPI.cancelQueueItem(itemId)),
    ]);
    setSelectedTasks(new Set());
    refresh();
  };

  const handleBatchDelete = async () => {
    if (!selectedTasks.size) return;
    const ok = await confirm({
      description: t('queued.confirmBatchDelete', { count: selectedTasks.size }),
      variant: 'danger',
      confirmText: t('common:button.delete'),
    });
    if (!ok) return;
    const selectedItems = [...selectedTasks]
      .map(id => tasks.find(t => t.id === id))
      .filter((task): task is Task => !!task);
    const scheduledItemIDs = selectedItems
      .filter(task => isScheduledExecutionItem(task))
      .map(task => task.id);
    const manualTaskIDs = selectedItems
      .filter(task => !isScheduledExecutionItem(task))
      .map(task => task.id);

    await Promise.all([
      ...(manualTaskIDs.length > 0 ? [taskAPI.batchDelete([...new Set(manualTaskIDs)])] : []),
      ...scheduledItemIDs.map(itemId => taskAPI.deleteQueueItem(itemId)),
    ]);
    setSelectedTasks(new Set());
    if (selectedId && selectedTasks.has(selectedId)) closeDetail();
    refresh();
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
              onClick={() => { setFilterKey(f.key); setPage(0); closeDetail(); }}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                filterKey === f.key ? 'bg-white text-slate-800 shadow-sm font-medium' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        {selectedTasks.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500">{t('queued.selectedCount', { count: selectedTasks.size })}</span>
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
                      checked={tasks.length > 0 && selectedTasks.size === tasks.length}
                      onChange={() => selectedTasks.size === tasks.length ? setSelectedTasks(new Set()) : setSelectedTasks(new Set(tasks.map(t => t.id)))}
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
                      {task.execution?.startedAt
                        ? formatTime(task.execution.startedAt)
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
                <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0} className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed">
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1} className="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed">
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
  task: Task;
  onClose: () => void;
  onAction: (action: string, taskId: string) => void;
  onRefresh?: () => void;
}) {
  const DRAWER_DEFAULT_WIDTH = 640;
  const DRAWER_MIN_WIDTH = 480;
  const DRAWER_MAX_WIDTH = 960;
  const { t } = useTranslation('task');
  const sessionId = task.execution?.sessionID;
  const isActive = ['queued', 'running'].includes(task.status);
  const isScheduledExecution = task.source?.sourceType === 'scheduled_trigger' && !!task.taskID;
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
            {task.execution?.durationMs != null && (
              <span className="text-xs text-gray-400">{formatDuration(task.execution.durationMs)}</span>
            )}
          </div>

          {task.description && (
            <p className="text-xs text-gray-500 break-words">{task.description}</p>
          )}

          {task.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {task.tags.map(tag => <span key={tag} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-xs">{tag}</span>)}
            </div>
          )}

          <div className="flex flex-wrap gap-1.5">
            {!isScheduledExecution && (task.status === 'running' || task.status === 'queued') && (
              <ActionButton icon={<Pause className="w-3 h-3" />} label={t('queued.actionPause')} onClick={() => onAction('pause', task.id)} color="yellow" />
            )}
            {!isScheduledExecution && task.status === 'paused' && (
              <ActionButton icon={<Play className="w-3 h-3" />} label={t('queued.actionResume')} onClick={() => onAction('resume', task.id)} color="green" />
            )}
            {!['completed', 'cancelled', 'failed'].includes(task.status) && (
              <ActionButton icon={<XCircle className="w-3 h-3" />} label={t('queued.actionCancel')} onClick={() => onAction('cancel', task.id)} color="gray" />
            )}
            {!isScheduledExecution && task.status === 'failed' && (
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
            // task.updated fires when session_id is linked (backend emits it immediately).
            // Trigger a task-list refresh so the new sessionId propagates to this panel
            // without waiting for the next polling cycle.
            if (event.type === 'task.updated' && event.properties?.taskID === (task.taskID ?? task.id)) {
              onRefresh?.();
            }
          }}
        />
      </div>
    </>
  );
}
