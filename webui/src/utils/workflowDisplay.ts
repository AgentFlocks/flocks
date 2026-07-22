import type { Workflow, WorkflowSummary } from '@/api/workflow';

type LocalizedNameSource = Record<string, unknown> | null | undefined;
type WorkflowDisplaySource = Workflow | WorkflowSummary;

function isChineseLocale(language?: string): boolean {
  if (!language) return false;
  return language.toLowerCase().replace('_', '-').startsWith('zh');
}

function localizedNamesFrom(value: LocalizedNameSource): Record<string, string> {
  if (!value || typeof value !== 'object') return {};

  const names: Record<string, string> = {};
  Object.entries(value).forEach(([key, item]) => {
    const locale = key.trim();
    const name = typeof item === 'string' ? item.trim() : '';
    if (locale && name) {
      names[locale] = name;
    }
  });
  return names;
}

function collectLocalizedNames(workflow: WorkflowDisplaySource | null | undefined): Record<string, string> {
  if (!workflow) return {};
  const raw = workflow as WorkflowDisplaySource & Record<string, unknown>;
  const workflowJson = raw.workflowJson as Record<string, unknown> | undefined;
  const metadata = workflowJson?.metadata as Record<string, unknown> | undefined;
  const names: Record<string, string> = {};

  [
    raw.nameI18n,
    raw.names,
    raw.localizedNames,
    raw.displayNames,
    workflowJson?.nameI18n,
    workflowJson?.names,
    workflowJson?.localizedNames,
    workflowJson?.displayNames,
    metadata?.nameI18n,
    metadata?.names,
    metadata?.localizedNames,
    metadata?.displayNames,
  ].forEach((source) => {
    Object.assign(names, localizedNamesFrom(source as LocalizedNameSource));
  });

  const directAliases: Record<string, string[]> = {
    'zh-CN': ['nameZh', 'nameCn', 'zhName', 'cnName'],
    'en-US': ['nameEn', 'enName'],
  };
  Object.entries(directAliases).forEach(([locale, aliases]) => {
    for (const alias of aliases) {
      const value = raw[alias] ?? workflowJson?.[alias] ?? metadata?.[alias];
      if (typeof value === 'string' && value.trim()) {
        names[locale] = names[locale] || value.trim();
        break;
      }
    }
  });

  return names;
}

function pickLocalizedName(names: Record<string, string>, language?: string): string {
  if (isChineseLocale(language)) {
    return names['zh-CN'] || names['zh_CN'] || names.zh || names.cn || '';
  }
  return names['en-US'] || names.en_US || names.en || '';
}

export function getWorkflowDisplayName(
  workflow: WorkflowDisplaySource | null | undefined,
  language?: string,
): string {
  if (!workflow) return '';
  const localized = pickLocalizedName(collectLocalizedNames(workflow), language);
  return localized || workflow.name?.trim() || workflow.id;
}
