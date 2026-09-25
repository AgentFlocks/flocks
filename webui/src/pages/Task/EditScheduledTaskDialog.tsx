import { pageActionLabel } from '../pageActionLabel';
import PageDialog from '../PageDialog';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconX, IconCalendar, IconClock, IconLoader2 } from '@tabler/icons-react';
import { useToast } from '@/components/common/Toast';
import { taskAPI, TaskScheduler, TaskPriority, ExecutionMode } from '@/api/task';
import { describeCron, CRON_PRESETS } from './helpers';
import { extractErrorMessage } from '@/utils/error';

export default function EditScheduledTaskDialog({ task, onClose, onSaved }: {
  task: TaskScheduler;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isRunOnce = task.mode === 'once';
  const { t } = useTranslation('task');
  const toast = useToast();

  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [cronPreset, setCronPreset] = useState<string>(() => {
    const preset = CRON_PRESETS.find(p => p.value === task.trigger?.cron);
    return preset ? preset.value : '__custom__';
  });
  const [cronCustom, setCronCustom] = useState(task.trigger?.cron ?? '');
  const [cronDescription, setCronDescription] = useState(task.trigger?.cronDescription ?? '');
  const [runAt, setRunAt] = useState<string>(() => {
    const src = task.trigger?.runAt ?? task.trigger?.nextRun;
    if (!src) return '';
    const d = new Date(src);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  });
  const [timezone, setTimezone] = useState(task.trigger?.timezone ?? 'Asia/Shanghai');
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(task.executionMode);
  const [agentName, setAgentName] = useState(task.agentName);
  const [workflowID, setWorkflowID] = useState(task.workflowID ?? '');
  const [userPrompt, setUserPrompt] = useState(task.source?.userPrompt ?? '');
  const [submitting, setSubmitting] = useState(false);

  const effectiveCron = cronPreset === '__custom__' ? cronCustom : cronPreset;
  const showCustomInput = cronPreset === '__custom__';
  const canSubmit = title.trim() && (isRunOnce ? runAt : effectiveCron.trim());

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      if (isRunOnce) {
        await taskAPI.updateScheduler(task.id, {
          title: title.trim(),
          description: description.trim(),
          priority,
          runOnce: true,
          runAt: runAt ? new Date(runAt).toISOString() : undefined,
          executionMode,
          agentName: executionMode === 'agent' ? agentName : undefined,
          workflowID: executionMode === 'workflow' ? workflowID : undefined,
          userPrompt: userPrompt || undefined,
        });
      } else {
        await taskAPI.updateScheduler(task.id, {
          title: title.trim(),
          description: description.trim(),
          priority,
          cron: effectiveCron.trim(),
          cronDescription: cronDescription.trim() || undefined,
          timezone,
          executionMode,
          agentName: executionMode === 'agent' ? agentName : undefined,
          workflowID: executionMode === 'workflow' ? workflowID : undefined,
          userPrompt: userPrompt || undefined,
        });
      }
      onSaved();
    } catch (err: unknown) {
      toast.error(t('taskSheet.saveFailed'), extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageDialog onClose={submitting ? undefined : onClose} className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-backdrop">
      <div className="bg-surface rounded-card shadow-xl w-full max-w-modal mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <h2 className="text-title-section font-semibold text-fg">{t('form.editTitle')}</h2>
          <button aria-label={pageActionLabel('close')} onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center p-1 rounded-control hover:bg-hover">
            <IconX aria-hidden="true" className="w-5 h-5 text-fg-muted" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-5">
          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('form.titleLabel')}</label>
            <input
              type="text" value={title} onChange={e => setTitle(e.target.value)} required
              className="ui-input w-full px-3  border border-line-control rounded-control"
              placeholder={t('form.titlePlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('form.descLabel')}</label>
            <textarea
              value={description} onChange={e => setDescription(e.target.value)}
              className="ui-textarea w-full px-3  border border-line-control rounded-control resize-none"
              rows={2} placeholder={t('form.descPlaceholder')}
            />
          </div>

          <div className={`rounded-card p-4 space-y-3 border ${isRunOnce ? 'bg-warning-soft border-warning' : 'bg-ai-soft border-ai'}`}>
            <p className={`text-sm font-semibold flex items-center gap-1.5 ${isRunOnce ? 'text-warning' : 'text-ai'}`}>
              <IconCalendar aria-hidden="true" className="w-4 h-4" /> {t('form.scheduleConfig')}
            </p>

            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">{t('form.freqRecurringLabel')}</label>
              {isRunOnce ? (
                <div className="flex items-center gap-2 px-3 py-2 bg-surface border border-warning rounded-control text-sm text-warning font-medium">
                  <IconClock aria-hidden="true" className="w-4 h-4" /> {t('form.runOnce')}
                </div>
              ) : (
                <select
                  value={cronPreset}
                  onChange={e => {
                    setCronPreset(e.target.value);
                    if (e.target.value !== '__custom__') setCronCustom(e.target.value);
                  }}
                  className="ui-select w-full px-3  border border-line-control rounded-control text-sm bg-surface"
                >
                  {CRON_PRESETS.map(p => (
                    <option key={p.value} value={p.value}>{t(`cronPresets.${p.key}`)}</option>
                  ))}
                </select>
              )}
            </div>

            {isRunOnce && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1">{t('form.execTimeLabel')}</label>
                <input
                  type="datetime-local" value={runAt} onChange={e => setRunAt(e.target.value)}
                  className="ui-input w-full px-3  border border-line-control rounded-control text-sm bg-surface"
                  required
                />
              </div>
            )}

            {!isRunOnce && showCustomInput && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1">{t('form.customCronLabel')}</label>
                <input
                  type="text" value={cronCustom} onChange={e => setCronCustom(e.target.value)}
                  className="ui-input w-full px-3  border border-line-control rounded-control font-mono text-sm"
                  placeholder="48 11 * * *"
                />
              </div>
            )}

            {!isRunOnce && effectiveCron && (
              <div className="flex items-center gap-2 text-xs">
                <code className="bg-surface border border-ai text-ai px-2 py-0.5 rounded-tag font-mono">{effectiveCron}</code>
                {cronDescription && cronDescription !== effectiveCron && (
                  <span className="text-ai font-medium">{cronDescription}</span>
                )}
              </div>
            )}

            {!isRunOnce && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1">{t('form.timezoneLabel')}</label>
                <select
                  value={timezone} onChange={e => setTimezone(e.target.value)}
                  className="ui-select w-full px-3  border border-line-control rounded-control text-sm bg-surface"
                >
                  <option value="Asia/Shanghai">{t('form.timezoneShanghai')}</option>
                  <option value="UTC">UTC</option>
                  <option value="America/New_York">America/New_York（UTC-5/4）</option>
                  <option value="Europe/London">Europe/London（UTC+0/1）</option>
                  <option value="Asia/Tokyo">Asia/Tokyo（UTC+9）</option>
                </select>
              </div>
            )}

            {!isRunOnce && (
              <div>
                <label className="block text-xs font-medium text-fg-secondary mb-1">
                  {t('form.cronDescLabel')}
                  <span className="text-fg-muted font-normal ml-1">{t('form.cronDescHint')}</span>
                </label>
                <input
                  type="text" value={cronDescription} onChange={e => setCronDescription(e.target.value)}
                  placeholder={effectiveCron ? describeCron(effectiveCron) : t('form.cronDescPlaceholder')}
                  className="ui-input w-full px-3  border border-line-control rounded-control text-sm bg-surface"
                />
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('form.priorityLabel')}</label>
            <select
              value={priority} onChange={e => setPriority(e.target.value as TaskPriority)}
              className="ui-select w-full px-3  border border-line-control rounded-control"
            >
              <option value="urgent">{t('form.urgentLabel')}</option>
              <option value="high">{t('form.highLabel')}</option>
              <option value="normal">{t('form.normalLabel')}</option>
              <option value="low">{t('form.lowLabel')}</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1">{t('form.modeLabel')}</label>
              <select
                value={executionMode} onChange={e => setExecutionMode(e.target.value as ExecutionMode)}
                className="ui-select w-full px-3  border border-line-control rounded-control"
              >
                <option value="agent">Agent</option>
                <option value="workflow">Workflow</option>
              </select>
            </div>
            {executionMode === 'agent' ? (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1">{t('form.agentName')}</label>
                <input
                  type="text" value={agentName} onChange={e => setAgentName(e.target.value)}
                  className="ui-input w-full px-3  border border-line-control rounded-control"
                  placeholder="rex"
                />
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1">Workflow ID</label>
                <input
                  type="text" value={workflowID} onChange={e => setWorkflowID(e.target.value)}
                  className="ui-input w-full px-3  border border-line-control rounded-control font-mono text-sm"
                  placeholder={t('form.workflowIdPlaceholder')}
                />
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              {t('form.additionalInfoLabel')}
              <span className="text-fg-muted font-normal text-xs ml-1">{t('form.additionalInfoHint')}</span>
            </label>
            <textarea
              value={userPrompt} onChange={e => setUserPrompt(e.target.value)}
              className="ui-textarea w-full px-3  border border-line-control rounded-control resize-none"
              rows={3} placeholder={t('form.additionalInfoPlaceholder')}
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-fg-secondary bg-subtle rounded-control hover:bg-hover">
              {t('common:button.cancel')}
            </button>
            <button type="submit" disabled={submitting || !canSubmit}
              className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed flex items-center gap-2">
              {submitting && <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />}
              {t('common:button.save')}
            </button>
          </div>
        </form>
      </div>
    </PageDialog>
  );
}
