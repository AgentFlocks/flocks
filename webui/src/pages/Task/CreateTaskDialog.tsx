import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Calendar, Loader2 } from 'lucide-react';
import { useToast } from '@/components/common/Toast';
import {
  taskAPI,
  TaskType,
  TaskPriority,
  TaskCreateParams,
  ExecutionMode,
} from '@/api/task';
import { describeCron, CRON_PRESETS } from './helpers';

export default function CreateTaskDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<TaskCreateParams>({
    title: '', description: '', type: 'queued', priority: 'normal',
    executionMode: 'agent', agentName: 'rex',
  });
  const [scheduleMode, setScheduleMode] = useState<'recurring' | 'once'>('recurring');
  const [runAt, setRunAt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { t } = useTranslation('task');
  const toast = useToast();

  const isScheduled = form.type === 'scheduled';
  const isOnce = isScheduled && scheduleMode === 'once';

  const canSubmit = form.title.trim() && (
    !isScheduled ||
    (isOnce ? runAt : !!form.cron?.trim())
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const params: TaskCreateParams = { ...form };
      if (isOnce) {
        params.runOnce = true;
        params.runAt = runAt ? new Date(runAt).toISOString() : undefined;
        params.cron = undefined;
      }
      await taskAPI.createScheduler(params);
      onCreated();
    } catch (err: unknown) {
      toast.error(t('taskSheet.createFailed'), err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const update = (fields: Partial<TaskCreateParams>) => setForm(prev => ({ ...prev, ...fields }));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">{t('form.createTitle')}</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100"><X className="w-5 h-5 text-gray-400" /></button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.titleLabel')}</label>
            <input type="text" value={form.title} onChange={e => update({ title: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none"
              placeholder={t('form.titlePlaceholder')} required />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.descLabel')}</label>
            <textarea value={form.description} onChange={e => update({ description: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none resize-none"
              rows={2} placeholder={t('form.descPlaceholder')} />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.typeLabel')}</label>
              <select
                value={form.type}
                onChange={e => {
                  update({ type: e.target.value as TaskType, cron: undefined });
                  setScheduleMode('recurring');
                  setRunAt('');
                }}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none"
              >
                <option value="queued">{t('form.queueOption')}</option>
                <option value="scheduled">{t('form.scheduledOption')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.priorityLabel')}</label>
              <select value={form.priority} onChange={e => update({ priority: e.target.value as TaskPriority })} className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none">
                <option value="urgent">{t('form.urgentLabel')}</option>
                <option value="high">{t('form.highLabel')}</option>
                <option value="normal">{t('form.normalLabel')}</option>
                <option value="low">{t('form.lowLabel')}</option>
              </select>
            </div>
          </div>

          {isScheduled && (
            <div className={`rounded-xl p-4 space-y-3 border ${isOnce ? 'bg-orange-50/60 border-orange-100' : 'bg-purple-50/60 border-purple-100'}`}>
              <p className={`text-sm font-semibold flex items-center gap-1.5 ${isOnce ? 'text-orange-800' : 'text-purple-800'}`}>
                <Calendar className="w-4 h-4" /> {t('form.scheduleConfig')}
              </p>

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">{t('form.frequencyLabel')}</label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => { setScheduleMode('recurring'); update({ cron: undefined }); }}
                    className={`flex-1 py-2 text-sm rounded-lg border transition-colors font-medium ${
                      scheduleMode === 'recurring'
                        ? 'bg-purple-600 text-white border-purple-600'
                        : 'bg-white text-gray-600 border-gray-300 hover:border-purple-400'
                    }`}
                  >
                    {t('form.recurring')}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setScheduleMode('once'); update({ cron: undefined }); }}
                    className={`flex-1 py-2 text-sm rounded-lg border transition-colors font-medium ${
                      scheduleMode === 'once'
                        ? 'bg-orange-500 text-white border-orange-500'
                        : 'bg-white text-gray-600 border-gray-300 hover:border-orange-400'
                    }`}
                  >
                    {t('form.runOnce')}
                  </button>
                </div>
              </div>

              {isOnce && (
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('form.execTimeLabel')}</label>
                  <input
                    type="datetime-local" value={runAt} onChange={e => setRunAt(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-orange-400 outline-none text-sm bg-white"
                    required
                  />
                </div>
              )}

              {!isOnce && (
                <>
                  <div>
                    <select
                      value={CRON_PRESETS.find(p => p.value === form.cron) ? form.cron : (form.cron ? '__custom__' : '')}
                      onChange={e => {
                        if (e.target.value !== '__custom__') update({ cron: e.target.value });
                        else update({ cron: '' });
                      }}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none text-sm bg-white focus:ring-2 focus:ring-purple-500"
                    >
                      <option value="">{t('form.selectFrequency')}</option>
                      {CRON_PRESETS.filter(p => p.value !== '__custom__').map(p => (
                        <option key={p.value} value={p.value}>{t(`cronPresets.${p.key}`)}</option>
                      ))}
                      <option value="__custom__">{t('form.customOption')}</option>
                    </select>
                  </div>
                  {form.cron !== undefined && !CRON_PRESETS.find(p => p.value === form.cron && p.value !== '__custom__') && (
                    <input
                      type="text" value={form.cron ?? ''} onChange={e => update({ cron: e.target.value })}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 outline-none font-mono text-sm"
                      placeholder="0 9 * * 1-5"
                    />
                  )}
                  {form.cron && (
                    <p className="text-xs text-purple-600 font-medium">{describeCron(form.cron)}</p>
                  )}
                </>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.modeLabel')}</label>
              <select value={form.executionMode} onChange={e => update({ executionMode: e.target.value as ExecutionMode })} className="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none">
                <option value="agent">Agent</option>
                <option value="workflow">Workflow</option>
              </select>
            </div>
            {form.executionMode === 'agent' ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.agentName')}</label>
                <input type="text" value={form.agentName ?? 'rex'} onChange={e => update({ agentName: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none"
                  placeholder="rex" />
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Workflow ID</label>
                <input type="text" value={form.workflowID ?? ''} onChange={e => update({ workflowID: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none font-mono text-sm"
                  placeholder={t('form.workflowIdPlaceholder')} />
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('form.additionalInfoLabel')}
              <span className="text-gray-400 font-normal text-xs ml-1">{t('form.additionalInfoHint')}</span>
            </label>
            <textarea value={form.userPrompt ?? ''} onChange={e => update({ userPrompt: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-slate-400 outline-none resize-none"
              rows={3} placeholder={t('form.additionalInfoPlaceholder')} />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200">{t('common:button.cancel')}</button>
            <button type="submit" disabled={submitting || !canSubmit}
              className={`px-4 py-2 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 ${
                isOnce ? 'bg-orange-500 hover:bg-orange-600' : 'bg-slate-700 hover:bg-slate-800'
              }`}>
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('common:button.create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
