import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconCalendar, IconClock, IconTrash, IconToggleLeft, IconToggleRight } from '@tabler/icons-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useTaskSchedulers } from '@/hooks/useTasks';
import { taskAPI, TaskScheduler } from '@/api/task';
import { PriorityBadge, ModeBadge } from './components';
import { describeCron, formatTime } from './helpers';
import TaskSheet from './TaskSheet';
import { extractErrorMessage } from '@/utils/error';

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
      toast.error(t('scheduled.actionFailed'), extractErrorMessage(err));
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
      toast.error(t('scheduled.deleteFailed'), extractErrorMessage(err));
    }
  };

  if (loading && tasks.length === 0) return <div className="flex justify-center py-12"><LoadingSpinner delayMs={180} /></div>;
  if (error) return <div className="text-center py-12 text-danger">{error}</div>;

  return (
    <>
      <div className="bg-surface rounded-card border border-line overflow-hidden">
        {tasks.length === 0 ? (
          <div className="p-8">
            <EmptyState
              icon={<IconCalendar aria-hidden="true" className="w-8 h-8" />}
              title={t('scheduled.emptyTitle')}
              description={t('scheduled.emptyDescription')}
            />
          </div>
        ) : (
          <>
            {/* 窄屏（375px）下表格比容器宽，overflow-hidden 会把后几列裁掉且无法滚动；改为容器内横向滚动 */}
            <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-subtle border-b border-line">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary w-24">{t('scheduled.colStatus')}</th>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colName')}</th>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colFrequency')}</th>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colNextRun')}</th>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colMode')}</th>
                  <th className="text-left px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colPriority')}</th>
                  <th className="text-right px-4 py-3 font-medium text-fg-secondary">{t('scheduled.colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
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
                    <tr key={task.id} onClick={() => setEditingTask(task)} className="hover:bg-hover transition-colors cursor-pointer">
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${enabled ? 'text-success' : 'text-fg-muted'}`}>
                          <span className={`w-2 h-2 rounded-pill flex-shrink-0 ${enabled ? 'bg-success' : 'bg-selected'}`} />
                          {enabled ? t('scheduled.statusEnabled') : t('scheduled.statusDisabled')}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-fg">{task.title}</div>
                        {task.description && (
                          <div className="text-xs text-fg-muted truncate max-w-[220px]" title={task.description}>
                            {task.description}
                          </div>
                        )}
                        {task.tags.length > 0 && (
                          <div className="flex gap-1 mt-1 flex-wrap">
                            {task.tags.map(tag => (
                              <span key={tag} className="px-1.5 py-0.5 bg-subtle text-fg-muted rounded-tag text-xs">{tag}</span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 font-medium whitespace-nowrap">
                        {runOnce ? (
                          <span className="inline-flex items-center gap-1 text-fg-muted">
                            <IconClock aria-hidden="true" className="w-4 h-4" /> {t('scheduled.runOnce')}
                          </span>
                        ) : (
                          <span className="text-ai">{cronDesc}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-fg-muted text-xs whitespace-nowrap">
                        {nextRun ? (
                          <span className={enabled ? 'text-fg-secondary' : 'text-fg-muted'}>{formatTime(nextRun)}</span>
                        ) : (
                          <span className="text-fg-placeholder">&mdash;</span>
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
                            className={`ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control transition-colors ${
                              enabled
                                ? 'text-success hover:bg-success-soft'
                                : 'text-fg-muted hover:bg-hover'
                            }`}
                          >
                            {enabled
                              ? <IconToggleRight aria-hidden="true" className="w-5 h-5" />
                              : <IconToggleLeft aria-hidden="true" className="w-5 h-5" />
                            }
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDelete(task.id); }}
                            title={t('common:button.delete')}
                            className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-placeholder hover:text-fg-secondary hover:bg-hover rounded-control transition-colors"
                          >
                            <IconTrash aria-hidden="true" className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            <div className="px-4 py-3 border-t border-line bg-subtle text-sm text-fg-muted">
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
