import { useState, type FormEvent } from 'react';
import { Search, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { TaskListParams, TaskPriority } from '@/api/task';

export type ExecutionSearchParams = Pick<TaskListParams, 'title' | 'startedAfter' | 'startedBefore' | 'priority'>;

export default function ExecutionFilters({ onSearch, onReset }: {
  onSearch: (filters: ExecutionSearchParams) => void;
  onReset: () => void;
}) {
  const { t } = useTranslation('task');
  const [title, setTitle] = useState('');
  const [startedAfter, setStartedAfter] = useState('');
  const [startedBefore, setStartedBefore] = useState('');
  const [priority, setPriority] = useState<TaskPriority | ''>('');
  const [error, setError] = useState('');

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const start = startedAfter ? new Date(startedAfter) : undefined;
    const end = startedBefore ? new Date(startedBefore) : undefined;
    // The inputs have minute precision, so include the entire final minute.
    if (end) end.setSeconds(59, 999);
    if ((start && !Number.isFinite(start.getTime())) || (end && !Number.isFinite(end.getTime()))) {
      setError(t('queued.filterInvalidTime'));
      return;
    }
    if (start && end && start > end) {
      setError(t('queued.filterInvalidRange'));
      return;
    }
    setError('');
    onSearch({
      title: title.trim() || undefined,
      startedAfter: start?.toISOString(),
      // Stored execution timestamps retain microseconds beyond JS precision.
      startedBefore: end?.toISOString().replace('.999Z', '.999999Z'),
      priority: priority || undefined,
    });
  };

  const handleReset = () => {
    setTitle('');
    setStartedAfter('');
    setStartedBefore('');
    setPriority('');
    setError('');
    onReset();
  };

  const inputClass = 'w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500';

  return (
    <form onSubmit={handleSubmit} className="space-y-2 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 flex-[1_1_180px] space-y-1 text-sm text-gray-600">
          <span>{t('queued.colName')}</span>
          <input type="search" value={title} onChange={event => setTitle(event.target.value)} placeholder={t('queued.filterNamePlaceholder')} className={inputClass} />
        </label>
        <fieldset className="min-w-0 flex-[2_1_400px]">
          <legend className="mb-1 text-sm text-gray-600">{t('queued.filterExecutionTime')}</legend>
          <div className="flex flex-wrap items-center gap-2">
            <input type="datetime-local" aria-label={t('queued.filterStartTime')} aria-describedby="execution-time-hint" value={startedAfter} onChange={event => { setStartedAfter(event.target.value); setError(''); }} className={`${inputClass} flex-[1_1_180px]`} />
            <span className="text-sm text-gray-400" aria-hidden="true">—</span>
            <input type="datetime-local" aria-label={t('queued.filterEndTime')} aria-describedby="execution-time-hint" value={startedBefore} onChange={event => { setStartedBefore(event.target.value); setError(''); }} className={`${inputClass} flex-[1_1_180px]`} />
          </div>
        </fieldset>
        <label className="min-w-[120px] flex-[0_1_140px] space-y-1 text-sm text-gray-600">
          <span>{t('queued.colPriority')}</span>
          <select value={priority} onChange={event => setPriority(event.target.value as TaskPriority | '')} className={inputClass}>
            <option value="">{t('queued.filterAllPriorities')}</option>
            {(['urgent', 'high', 'normal', 'low'] as const).map(value => (
              <option key={value} value={value}>{t(`priority.${value}`)}</option>
            ))}
          </select>
        </label>
        <div className="flex gap-2">
          <button type="submit" className="flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-sm text-white hover:bg-slate-700">
            <Search className="h-4 w-4" />{t('queued.filterSearch')}
          </button>
          <button type="button" onClick={handleReset} className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50">
            <RotateCcw className="h-4 w-4" />{t('queued.filterReset')}
          </button>
        </div>
      </div>
      <p id="execution-time-hint" className="text-xs text-gray-400">{t('queued.filterTimeHint')}</p>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    </form>
  );
}
