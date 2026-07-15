import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, Clock, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useTaskSchedulers } from '@/hooks/useTasks';
import { taskAPI, TaskScheduler } from '@/api/task';
import { PriorityBadge, ModeBadge } from './components';
import { describeCron, formatTime } from './helpers';
import TaskSheet from './TaskSheet';

export default function ScheduledSection({ onRefreshGlobal }: { onRefreshGlobal: () => void }) {
  const { t } = useTranslation('task');
  const [editingTask, setEditingTask] = useState<TaskScheduler | null>(null);
  const { tasks, loading, error, refetch } = useTaskSchedulers(
    { limit: 100, scheduledOnly: true },
    { pollInterval: 10000 },
  );
  const toast = useToast();
  const confirm = useConfirm();

  const refresh = () => { refetch(); onRefreshGlobal(); };

  const handleToggle = async (task: TaskScheduler) => {
    try {
      if (task.status === 'active') await taskAPI.disableScheduler(task.id);
      else await taskAPI.enableScheduler(task.id);
      refresh();
    } catch (err: unknown) {
      toast.error(t('scheduled.actionFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async (taskId: string) => {
    const ok = await confirm({
      description: t('scheduled.confirmDelete'),
      variant: 'danger',
      confirmText: t('common:button.delete'),
    });
    if (!ok) return;
    try {
      await taskAPI.deleteScheduler(taskId);
      refresh();
    } catch (err: unknown) {
      toast.error(t('scheduled.deleteFailed'), err instanceof Error ? err.message : String(err));
    }
  };

  if (loading && tasks.length === 0) return <div className="flex justify-center py-12"><LoadingSpinner /></div>;
  if (error) return <div className="text-center py-12 text-red-500">{error}</div>;

  return (
    <>
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {tasks.length === 0 ? (
          <div className="p-8">
            <EmptyState
              icon={<Calendar className="w-8 h-8" />}
              title={t('scheduled.emptyTitle')}
              description={t('scheduled.emptyDescription')}
            />
          </div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 w-24">{t('scheduled.colStatus')}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t('scheduled.colName')}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t('scheduled.colFrequency')}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t('scheduled.colNextRun')}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t('scheduled.colMode')}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t('scheduled.colPriority')}</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">{t('scheduled.colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {[...tasks].sort((a, b) => {
                  const ae = a.status === 'active' ? 1 : 0;
                  const be = b.status === 'active' ? 1 : 0;
                  return be - ae;
                }).map(task => {
                  const enabled = task.status === 'active';
                  const runOnce = task.mode === 'once';
                  const cron = task.trigger?.cron ?? '';
                  const cronDesc = runOnce
                    ? t('scheduled.runOnce')
                    : (task.trigger?.cronDescription || describeCron(cron));
                  const nextRun = task.trigger?.nextRun ?? (runOnce ? task.trigger?.runAt : undefined);

                  return (
                    <tr key={task.id} onClick={() => setEditingTask(task)} className="hover:bg-gray-50 transition-colors cursor-pointer">
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${enabled ? 'text-green-700' : 'text-gray-400'}`}>
                          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                          {enabled ? t('scheduled.statusEnabled') : t('scheduled.statusDisabled')}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">{task.title}</div>
                        {task.description && (
                          <div className="text-xs text-gray-400 truncate max-w-[220px]" title={task.description}>
                            {task.description}
                          </div>
                        )}
                        {task.tags.length > 0 && (
                          <div className="flex gap-1 mt-1 flex-wrap">
                            {task.tags.map(tag => (
                              <span key={tag} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-xs">{tag}</span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 font-medium whitespace-nowrap">
                        {runOnce ? (
                          <span className="inline-flex items-center gap-1 text-gray-500">
                            <Clock className="w-3.5 h-3.5" /> {t('scheduled.runOnce')}
                          </span>
                        ) : (
                          <span className="text-purple-700">{cronDesc}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {nextRun ? (
                          <span className={enabled ? 'text-gray-700' : 'text-gray-400'}>{formatTime(nextRun)}</span>
                        ) : (
                          <span className="text-gray-300">&mdash;</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <ModeBadge mode={task.executionMode} agent={task.agentName} />
                      </td>
                      <td className="px-4 py-3">
                        <PriorityBadge priority={task.priority} />
                      </td>
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={(e) => { e.stopPropagation(); handleToggle(task); }}
                            title={enabled ? t('scheduled.toggleDisable') : t('scheduled.toggleEnable')}
                            className={`p-1.5 rounded transition-colors ${
                              enabled
                                ? 'text-green-600 hover:bg-green-50'
                                : 'text-gray-400 hover:bg-gray-100'
                            }`}
                          >
                            {enabled
                              ? <ToggleRight className="w-5 h-5" />
                              : <ToggleLeft className="w-5 h-5" />
                            }
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDelete(task.id); }}
                            title={t('common:button.delete')}
                            className="p-1.5 text-gray-300 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="px-4 py-3 border-t border-gray-200 bg-gray-50 text-sm text-gray-500">
              {t('scheduled.totalCount', { count: tasks.length })}
            </div>
          </>
        )}
      </div>

      {editingTask && (
        <TaskSheet
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onSaved={() => { setEditingTask(null); refresh(); }}
        />
      )}
    </>
  );
}
