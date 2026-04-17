import { TaskStatus } from '@/api/task';
import i18n from '@/i18n';

export const STATUS_CONFIG: Record<TaskStatus, { icon: string; color: string }> = {
  pending:   { icon: '\u23f3', color: 'text-gray-500' },
  queued:    { icon: '\ud83d\udccb', color: 'text-sky-600' },
  running:   { icon: '\ud83d\udfe2', color: 'text-green-600' },
  completed: { icon: '\u2705', color: 'text-green-500' },
  failed:    { icon: '\u274c', color: 'text-red-500' },
  cancelled: { icon: '\ud83d\udeab', color: 'text-gray-400' },
};

export const PRIORITY_CONFIG: Record<string, { color: string }> = {
  urgent: { color: 'bg-amber-100 text-amber-800' },
  high:   { color: 'bg-orange-100 text-orange-700' },
  normal: { color: 'bg-slate-100 text-slate-700' },
  low:    { color: 'bg-gray-100 text-gray-600' },
};

export const SOURCE_CONFIG: Record<string, { color: string }> = {
  user_conversation: { color: 'bg-slate-100 text-slate-700' },
  scheduled_trigger: { color: 'bg-purple-50 text-purple-700' },
  system_evolution:  { color: 'bg-orange-50 text-orange-700' },
};

const t = (key: string, opts?: Record<string, unknown>) => i18n.t(`task:${key}`, opts);

export function describeCron(cron: string): string {
  if (!cron) return '';
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, month, dow] = parts;

  const isAny = (v: string) => v === '*';
  const isFixed = (v: string) => /^\d+$/.test(v);
  const isStep = (v: string) => /^\*\/\d+$/.test(v);
  const stepOf = (v: string) => parseInt(v.slice(2));
  const fmt2 = (v: string) => v.padStart(2, '0');
  const fmtTime = (h: string, m: string) => `${fmt2(h)}:${fmt2(m)}`;

  if (isAny(min) && isAny(hour) && isAny(dom) && isAny(month) && isAny(dow))
    return t('cron.everyMinute');

  if (isStep(min) && isAny(hour) && isAny(dom) && isAny(month) && isAny(dow)) {
    const n = stepOf(min);
    return n === 1 ? t('cron.everyMinute') : t('cron.everyNMinutes', { n });
  }

  if (isFixed(min) && isAny(hour) && isAny(dom) && isAny(month) && isAny(dow))
    return t('cron.atMinuteOfHour', { min });

  if (isFixed(min) && isStep(hour) && isAny(dom) && isAny(month) && isAny(dow)) {
    const n = stepOf(hour);
    return t('cron.everyNHours', { n, min });
  }

  if (isFixed(min) && isFixed(hour) && isAny(dom) && isAny(month) && isAny(dow))
    return t('cron.everyDayAt', { time: fmtTime(hour, min) });

  if (isFixed(min) && isFixed(hour) && isAny(dom) && isAny(month) && !isAny(dow)) {
    const time = fmtTime(hour, min);
    if (dow === '1-5') return t('cron.weekdaysAt', { time });
    if (dow === '6-7' || dow === '0,6' || dow === '6,0') return t('cron.weekendsAt', { time });
    const days = dow.split(/[,]/).map(d => {
      if (d.includes('-')) {
        const [a, b] = d.split('-');
        const da = t(`dow.${a}`, { defaultValue: `Day${a}` });
        const db = t(`dow.${b}`, { defaultValue: `Day${b}` });
        return `${da}～${db}`;
      }
      return t(`dow.${d}`, { defaultValue: `Day${d}` });
    }).join('、');
    return t('cron.everyDaysAt', { days, time });
  }

  if (isFixed(min) && isFixed(hour) && isFixed(dom) && isAny(month) && isAny(dow))
    return t('cron.monthlyAt', { dom, time: fmtTime(hour, min) });

  if (isFixed(min) && isFixed(hour) && isFixed(dom) && isFixed(month) && isAny(dow))
    return t('cron.yearlyAt', { month, dom, time: fmtTime(hour, min) });

  return cron;
}

export function formatRelativeTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return t('time.justNow');
  if (diff < 3600) return t('time.minutesAgo', { n: Math.floor(diff / 60) });
  if (diff < 86400) return t('time.hoursAgo', { n: Math.floor(diff / 3600) });
  return t('time.daysAgo', { n: Math.floor(diff / 86400) });
}

export function formatTime(iso: string): string {
  const locale = i18n.language ?? 'zh-CN';
  return new Date(iso).toLocaleString(locale, {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${Math.floor(secs - mins * 60)}s`;
}

export const CRON_PRESETS = [
  { key: 'everyMinute',    value: '* * * * *' },
  { key: 'every5Minutes',  value: '*/5 * * * *' },
  { key: 'every15Minutes', value: '*/15 * * * *' },
  { key: 'every30Minutes', value: '*/30 * * * *' },
  { key: 'everyHour',      value: '0 * * * *' },
  { key: 'every2Hours',    value: '0 */2 * * *' },
  { key: 'every6Hours',    value: '0 */6 * * *' },
  { key: 'every12Hours',   value: '0 */12 * * *' },
  { key: 'daily0000',      value: '0 0 * * *' },
  { key: 'daily0800',      value: '0 8 * * *' },
  { key: 'daily0900',      value: '0 9 * * *' },
  { key: 'daily1200',      value: '0 12 * * *' },
  { key: 'daily1800',      value: '0 18 * * *' },
  { key: 'weekdaysAt0900', value: '0 9 * * 1-5' },
  { key: 'mondayAt0900',   value: '0 9 * * 1' },
  { key: 'monthly1At0000', value: '0 0 1 * *' },
  { key: 'custom',         value: '__custom__' },
];

export const PAGE_SIZE = 20;
