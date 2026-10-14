import { Grid, Database, Cloud, Code } from 'lucide-react';
import { createElement } from 'react';
import type { TabKey } from './types';

export interface TabOption {
  key: TabKey;
  labelKey: string;
  sourceFilter?: 'mcp' | 'api' | 'plugin_py' | ('mcp' | 'api' | 'plugin_py')[];
}

/** Tab keys and i18n label keys; sourceFilter for filtering. Labels resolved via t('tool.tabs.*'). */
export const TABS: TabOption[] = [
  { key: 'all', labelKey: 'tabs.all' },
  { key: 'mcp', labelKey: 'tabs.mcp', sourceFilter: 'mcp' },
  { key: 'api', labelKey: 'tabs.api', sourceFilter: 'api' },
  { key: 'local', labelKey: 'tabs.local', sourceFilter: 'plugin_py' },
];

export const TAB_ICONS = { Grid, Database, Cloud, Code };
export function getTabIcon(key: TabKey) {
  const icons = [Grid, Database, Cloud, Code];
  const i = ['all', 'mcp', 'api', 'local'].indexOf(key);
  return createElement(icons[i] ?? Grid, { className: 'w-5 h-5' });
}

/** Source badge: labelKey for i18n (tool.source.*) for builtin/custom; label for proper nouns MCP/API. */
export const SOURCE_BADGE: Record<string, { label?: string; labelKey?: string; className: string }> = {
  mcp: { label: 'MCP', className: 'bg-red-100 text-red-800' },
  api: { label: 'API', className: 'bg-purple-100 text-purple-800' },
  device: { label: 'Device', className: 'bg-amber-100 text-amber-800' },
  plugin_py: { labelKey: 'source.local', className: 'bg-blue-100 text-blue-800' },
  plugin_yaml: { label: 'API Plugin', className: 'bg-violet-100 text-violet-800' },
  builtin: { labelKey: 'source.builtin', className: 'bg-green-100 text-green-800' },
  custom: { labelKey: 'source.custom', className: 'bg-orange-100 text-orange-800' },
};

/** Category key -> i18n key (tool.category.*). */
export const CATEGORY_LABEL_KEY: Record<string, string> = {
  file: 'category.file',
  terminal: 'category.terminal',
  browser: 'category.browser',
  code: 'category.code',
  search: 'category.search',
  system: 'category.system',
  custom: 'category.custom',
};

export const SOURCE_SORT_ORDER: Record<string, number> = {
  mcp: 0,
  api: 1,
  device: 2,
  plugin_py: 3,
  plugin_yaml: 4,
  builtin: 5,
  custom: 6,
};

export function getSourceLabel(
  source: string,
  t?: (key: string, options?: Record<string, unknown>) => string,
): string {
  const badge = SOURCE_BADGE[source] ?? SOURCE_BADGE.custom;
  if (badge.labelKey && t) {
    return t(badge.labelKey);
  }
  return badge.label ?? source;
}

export const PAGE_SIZE = 20;
