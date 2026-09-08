type SeverityItem = {
  label?: string;
  value?: number | null;
};

export function severityKey(value: unknown) {
  const key = String(value || '').trim().toLowerCase();
  if (key === 'critical' || key === 'severe' || key === '4') return 'critical';
  if (key === 'high' || key === '3') return 'high';
  if (key === 'medium' || key === '2') return 'medium';
  if (key === 'low' || key === '1') return 'low';
  return '';
}

export function severityRows(
  stats: { severityLevels?: SeverityItem[] },
  metricsAvailable = true,
) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const item of stats.severityLevels || []) {
    const key = severityKey(item.label);
    if (key) counts[key] += Number(item.value || 0);
  }
  return [
    { key: 'critical', label: '严重', value: metricsAvailable ? counts.critical : null, tone: 'critical' },
    { key: 'high', label: '高危', value: metricsAvailable ? counts.high : null, tone: 'high' },
    { key: 'medium', label: '中危', value: metricsAvailable ? counts.medium : null, tone: 'medium' },
    { key: 'low', label: '低危', value: metricsAvailable ? counts.low : null, tone: 'low' },
  ];
}
