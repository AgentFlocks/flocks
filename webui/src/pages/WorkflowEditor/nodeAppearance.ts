// Resource categories are independent of execution status. Labels and icons identify each node type.
const neutral = { bg: 'bg-surface', border: 'border-line-control', text: 'text-fg-secondary', handleColor: '!bg-fg-muted', accentBg: 'bg-subtle', dot: 'bg-fg-muted' };
const ai = { bg: 'bg-surface', border: 'border-ai', text: 'text-ai', handleColor: '!bg-ai', accentBg: 'bg-ai-soft', dot: 'bg-ai' };
const data = { bg: 'bg-surface', border: 'border-data', text: 'text-data', handleColor: '!bg-data', accentBg: 'bg-data-soft', dot: 'bg-data' };

export const workflowNodeAppearance: Record<string, typeof neutral> = {
  python: neutral,
  logic: neutral,
  branch: neutral,
  loop: neutral,
  tool: data,
  llm: ai,
  http_request: data,
  subworkflow: ai,
  schedule: data,
  webhook: data,
  custom_webhook: data,
  kafka: data,
  syslog: data,
  internal_event: data,
  custom_adapter: data,
  manual: neutral,
  plugin: data,
};

export function workflowNodeColor(type: string): string {
  const appearance = workflowNodeAppearance[type] ?? neutral;
  if (appearance === ai) return 'var(--accent-ai)';
  if (appearance === data) return 'var(--accent-data)';
  return 'var(--text-muted)';
}
