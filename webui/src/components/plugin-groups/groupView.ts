import type { TFunction } from 'i18next';
import { extractErrorMessage } from '@/utils/error';

/** Presentation rows only. Keys are the current page's native item identities. */
export interface GroupNavItem {
  key: string;
  name: string;
  group?: string | null;
  readOnlyReason?: string;
}

export interface GroupNavEntry {
  name: string;
  count: number;
  readOnlyReason?: string;
}

/** null is All; the empty string is Ungrouped. Neither is a group name. */
export type GroupSelection = string | null;
export const groupItemLabel = (item: GroupNavItem): string => item.name === item.key ? item.name : `${item.name} (${item.key})`;
export const groupName = (value?: string | null): string => value?.trim() ?? '';

/** Localize known default names for display only; native values stay unchanged. */
export function groupLabel(name: string, t: TFunction): string {
  const labels = t('pluginGroups:defaultNames', { returnObjects: true });
  if (labels && typeof labels === 'object' && Object.prototype.hasOwnProperty.call(labels, name)) {
    const label = (labels as Record<string, unknown>)[name];
    if (typeof label === 'string') return label;
  }
  return name;
}
export const matchesGroup = (value: string | null | undefined, selection: GroupSelection): boolean => (
  selection === null || groupName(value) === selection
);

export function deriveGroupNav(items: readonly GroupNavItem[]) {
  const entries = new Map<string, GroupNavEntry>();
  let ungroupedCount = 0;
  for (const item of items) {
    const name = groupName(item.group);
    if (!name) { ungroupedCount += 1; continue; }
    const entry = entries.get(name) ?? { name, count: 0 };
    entry.count += 1;
    if (item.readOnlyReason) entry.readOnlyReason = item.readOnlyReason;
    entries.set(name, entry);
  }
  return { groups: [...entries.values()], total: items.length, ungroupedCount };
}

/** Apply a personal ordering to *currently derived* names, never create empty groups. */
export function orderGroupNames(names: string[], saved: unknown): string[] {
  const order = Array.isArray(saved) ? saved.filter((name): name is string => typeof name === 'string' && names.includes(name)) : [];
  return [...new Set([...order, ...names])];
}

/**
 * Bounded native saves, not a group persistence layer. The page supplies its
 * complete visible operation scope and its own native save/reload callbacks.
 * Precheck every member before writing; never fake an atomic rollback.
 */
export class GroupActionCancelled extends Error {}

export function assertGroupItemsEditable(items: readonly GroupNavItem[], t: TFunction): void {
  const readOnly = items.filter((item) => item.readOnlyReason);
  if (readOnly.length) {
    throw new Error(t('pluginGroups:errors.readOnlyMembers', {
      items: readOnly.map(groupItemLabel).join(', '),
    }));
  }
}

export async function saveGroupItems(
  items: readonly GroupNavItem[],
  group: string | null,
  save: (key: string, group: string | null) => Promise<unknown>,
  reload: () => Promise<unknown>,
  t: TFunction,
  confirmScope = false,
): Promise<void> {
  if (!items.length) throw new Error(t('pluginGroups:errors.missing'));
  assertGroupItemsEditable(items, t);
  if (confirmScope && !window.confirm(t('pluginGroups:confirmScope', {
    count: items.length,
    group: group === null ? t('pluginGroups:ungrouped') : groupLabel(group, t),
    items: items.map(groupItemLabel).join(', '),
  }))) throw new GroupActionCancelled();

  const failures: string[] = [];
  const succeeded: string[] = [];
  for (const item of items) {
    try {
      await save(item.key, group);
      succeeded.push(groupItemLabel(item));
    } catch (error) {
      failures.push(`${groupItemLabel(item)}: ${extractErrorMessage(error, t('pluginGroups:errors.write'))}`);
    }
  }
  let refreshError = '';
  try { await reload(); } catch (error) {
    refreshError = `${t('pluginGroups:errors.refresh')} ${extractErrorMessage(error, '')}`;
  }
  if (failures.length || refreshError) {
    throw new Error([
      t('pluginGroups:errors.partial', { succeeded: succeeded.length, failed: failures.length }),
      succeeded.length ? `${t('pluginGroups:errors.succeededItems')} ${succeeded.join(', ')}` : '',
      ...(failures.length ? [t('pluginGroups:errors.failedItems'), ...failures] : []),
      refreshError,
    ].filter(Boolean).join('\n'));
  }
}
