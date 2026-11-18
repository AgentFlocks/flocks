import type { TFunction } from 'i18next';
import { requestConfirm } from '@/components/common/ConfirmDialog';
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
/** What a page groups; messages name it ("拖动智能体到分组") instead of the generic "plugin". */
export type GroupItemKind = 'plugin' | 'agent' | 'skill' | 'workflow' | 'tool' | 'mcp' | 'api' | 'device';
/** `{{noun}}` / `{{nouns}}` for pluginGroups messages that name the grouped items. */
export const groupNoun = (t: TFunction, kind: GroupItemKind = 'plugin') => ({
  noun: t(`pluginGroups:noun.${kind}`),
  nouns: t(`pluginGroups:nouns.${kind}`),
});
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

/** A batch that did not fully succeed: `message` is for people, `details` keeps the server's own words. */
export class GroupSaveError extends Error {
  constructor(message: string, readonly details = '') {
    super(message);
    this.name = 'GroupSaveError';
  }
}

/** Server failures said in the user's words; anything else is shown as the server said it. */
function groupFailureReason(raw: string, t: TFunction, kind: GroupItemKind): string {
  // flocks.tool.nofollow: the file (or a link on its way) is no longer the one checked before writing.
  if (/changed after it was checked/i.test(raw)) return t('pluginGroups:errors.changedAfterCheck');
  return raw || t('pluginGroups:errors.write', groupNoun(t, kind));
}

export function assertGroupItemsEditable(items: readonly GroupNavItem[], t: TFunction, kind: GroupItemKind = 'plugin'): void {
  const readOnly = items.filter((item) => item.readOnlyReason);
  if (readOnly.length) {
    throw new Error(t('pluginGroups:errors.readOnlyMembers', {
      ...groupNoun(t, kind),
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
  kind: GroupItemKind = 'plugin',
): Promise<void> {
  const nouns = groupNoun(t, kind);
  if (!items.length) throw new Error(t('pluginGroups:errors.missing', nouns));
  assertGroupItemsEditable(items, t, kind);
  if (confirmScope) {
    const target = group === null ? t('pluginGroups:ungrouped') : groupLabel(group, t);
    const confirmed = await requestConfirm({
      title: t('pluginGroups:confirmScopeTitle', { group: target }),
      description: t('pluginGroups:confirmScope', {
        ...nouns,
        count: items.length,
        group: target,
        items: items.map(groupItemLabel).join(', '),
      }),
      confirmText: t('pluginGroups:confirmScopeAction'),
    });
    if (!confirmed) throw new GroupActionCancelled();
  }

  const failures: string[] = [];
  // The server's own words (with paths) for the technical details, only where they were reworded.
  const details: string[] = [];
  let succeeded = 0;
  for (const item of items) {
    try {
      await save(item.key, group);
      succeeded += 1;
    } catch (error) {
      const raw = extractErrorMessage(error, '');
      const reason = groupFailureReason(raw, t, kind);
      failures.push(t('pluginGroups:errors.failedItem', { item: groupItemLabel(item), reason }));
      if (raw && raw !== reason) details.push(`${groupItemLabel(item)}: ${raw}`);
    }
  }
  let refreshError = '';
  try { await reload(); } catch (error) {
    refreshError = `${t('pluginGroups:errors.refresh')} ${extractErrorMessage(error, '')}`;
  }
  if (failures.length || refreshError) {
    const summary = t('pluginGroups:errors.partial', { succeeded, failed: failures.length });
    const body = [...failures, refreshError].filter(Boolean).join('\n');
    throw new GroupSaveError(body ? `${summary}\n\n${body}` : summary, details.join('\n'));
  }
}
