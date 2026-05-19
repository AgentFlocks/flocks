/**
 * Shared time formatting utilities.
 *
 * All "smart time" display logic should use these helpers to avoid
 * duplicate implementations across components.
 */

export function formatSmartTime(timestamp: number): string {
  const d = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    // 今天：只显示时间
    return formatTime12h(d);
  }
  if (diffDays === 1) {
    // 昨天
    return `昨天 ${formatTime12h(d)}`;
  }
  if (now.getFullYear() === d.getFullYear()) {
    // 今年：不显示年
    return `${d.getMonth() + 1}/${d.getDate()} ${formatTime12h(d)}`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${formatTime12h(d)}`;
}

export function formatSessionDate(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${formatTime12h(d)}`;
}

function formatTime12h(d: Date): string {
  let hours = d.getHours();
  const minutes = d.getMinutes().toString().padStart(2, '0');
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  if (hours === 0) hours = 12;
  return `${hours}:${minutes} ${ampm}`;
}
