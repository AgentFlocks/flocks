/**
 * Shared time formatting utilities.
 *
 * All "smart time" display logic should use these helpers to avoid
 * duplicate implementations across components.
 */

export function formatSmartTime(
  timestamp: number,
  locale = 'zh-CN',
  nowTimestamp = Date.now(),
): string {
  const d = new Date(timestamp);
  const now = new Date(nowTimestamp);
  const diffDays = localCalendarDayNumber(now) - localCalendarDayNumber(d);
  const time = formatClockTime(d, locale);

  if (diffDays === 0) {
    return time;
  }
  if (diffDays === 1) {
    return locale.toLowerCase().startsWith('zh')
      ? `昨天 ${time}`
      : `Yesterday ${time}`;
  }
  if (diffDays > 1 && diffDays < 7) {
    const weekday = new Intl.DateTimeFormat(locale, { weekday: 'long' }).format(d);
    return `${weekday} ${time}`;
  }
  if (now.getFullYear() === d.getFullYear()) {
    if (locale.toLowerCase().startsWith('zh')) {
      return `${d.getMonth() + 1}月${d.getDate()}日 ${time}`;
    }
    const date = new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric' }).format(d);
    return `${date} ${time}`;
  }
  if (locale.toLowerCase().startsWith('zh')) {
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${time}`;
  }
  const date = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(d);
  return `${date} ${time}`;
}

export function formatSessionDate(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${formatTime12h(d)}`;
}

export function formatRelativeTime(
  timestamp: number,
  locale = 'zh-CN',
  now = Date.now(),
): string {
  if (!timestamp || !Number.isFinite(timestamp)) return '';

  const diffMs = timestamp - now;
  const absDiffMs = Math.abs(diffMs);
  const isChinese = locale.toLowerCase().startsWith('zh');

  if (absDiffMs < 60_000) {
    return isChinese ? '刚刚' : 'just now';
  }

  const units: Array<{
    unit: Intl.RelativeTimeFormatUnit;
    milliseconds: number;
    upperBound: number;
  }> = [
    { unit: 'minute', milliseconds: 60_000, upperBound: 60 * 60_000 },
    { unit: 'hour', milliseconds: 60 * 60_000, upperBound: 24 * 60 * 60_000 },
    { unit: 'day', milliseconds: 24 * 60 * 60_000, upperBound: 30 * 24 * 60 * 60_000 },
    { unit: 'month', milliseconds: 30 * 24 * 60 * 60_000, upperBound: 365 * 24 * 60 * 60_000 },
    { unit: 'year', milliseconds: 365 * 24 * 60 * 60_000, upperBound: Number.POSITIVE_INFINITY },
  ];
  const selected = units.find(({ upperBound }) => absDiffMs < upperBound) || units[units.length - 1];
  const value = Math.trunc(diffMs / selected.milliseconds) || (diffMs < 0 ? -1 : 1);

  return new Intl.RelativeTimeFormat(locale, { numeric: 'always' }).format(value, selected.unit);
}

function formatTime12h(d: Date): string {
  let hours = d.getHours();
  const minutes = d.getMinutes().toString().padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  if (hours === 0) hours = 12;
  return `${hours}:${minutes} ${ampm}`;
}

function formatClockTime(d: Date, locale: string): string {
  if (locale.toLowerCase().startsWith('zh')) {
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
  }
  return new Intl.DateTimeFormat(locale, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(d);
}

function localCalendarDayNumber(d: Date): number {
  return Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86_400_000);
}
