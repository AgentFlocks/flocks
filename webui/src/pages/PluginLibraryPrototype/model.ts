import { INITIAL_GROUPS, MOCK_ITEMS, SCOPE_CONFIG } from './mockData';
import type {
  CatalogQuery,
  CatalogScope,
  Collection,
  OrganizationState,
  PluginItem,
} from './types';

export const STORAGE_KEY = 'flocks:prototype:plugin-library:v1';

const itemsById = new Map(MOCK_ITEMS.map((item) => [item.id, item]));
const reservedGroupIds = new Set([
  'all',
  'favorites',
  'ungrouped',
  '__proto__',
  'constructor',
  'prototype',
]);
const nameCollator = new Intl.Collator('en', {
  sensitivity: 'base',
  numeric: true,
});
const hasOwn = (value: object, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);
const isScope = (value: unknown): value is CatalogScope =>
  typeof value === 'string' && hasOwn(SCOPE_CONFIG, value);
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isGroupId = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= 128 &&
  value.trim() === value &&
  !reservedGroupIds.has(value) &&
  Array.from(value).every(
    (character) =>
      character.charCodeAt(0) > 31 && character.charCodeAt(0) !== 127,
  );

function groupName(
  name: string,
  scope: CatalogScope,
  groups: Collection[],
  excludeId?: string,
): string {
  if (typeof name !== 'string' || !name.trim()) throw new Error('emptyName');
  const trimmed = name.trim();
  if (Array.from(trimmed).length > 32) throw new Error('longName');
  if (
    groups.some(
      (group) =>
        group.scope === scope &&
        group.id !== excludeId &&
        group.name.trim().toLowerCase() === trimmed.toLowerCase(),
    )
  ) {
    throw new Error('duplicateName');
  }
  return trimmed;
}

/** Fresh, independently mutable state; the fixture catalog is never modified. */
export function createInitialState(): OrganizationState {
  return {
    version: 1,
    groups: INITIAL_GROUPS.map((group) => ({ ...group })),
    memberships: Object.fromEntries(
      MOCK_ITEMS.map((item) => [item.id, item.collectionId]),
    ),
    favorites: MOCK_ITEMS.filter((item) => item.favorite).map(
      (item) => item.id,
    ),
  };
}

/** Organization metadata cannot change runtime attributes, including the original room. */
export function resolveItems(state: OrganizationState): PluginItem[] {
  const groups = new Map(state.groups.map((group) => [group.id, group]));
  const favorites = new Set(state.favorites);
  return MOCK_ITEMS.map((item) => {
    const membership = state.memberships[item.id];
    const collectionId =
      typeof membership === 'string' &&
      groups.get(membership)?.scope === item.scope
        ? membership
        : null;
    return {
      ...item,
      collectionId,
      favorite: favorites.has(item.id),
      tags: [...item.tags],
      attributes: { ...item.attributes },
      details: item.details.map((detail) => ({
        ...detail,
        label: { ...detail.label },
      })),
    };
  });
}

const compareId = (left: string, right: string): number =>
  left < right ? -1 : left > right ? 1 : 0;
const timestamp = (value: string): number => {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
};

/** Words AND together; tags OR together; facets OR within a key and AND across keys. */
export function selectItems(
  items: PluginItem[],
  query: CatalogQuery,
): PluginItem[] {
  const words = query.query.trim().toLowerCase().split(/\s+/u).filter(Boolean);
  const activeFacets = Object.entries(query.facets).filter(
    ([, values]) => values.length > 0,
  );
  return items
    .filter((item) => {
      if (item.scope !== query.scope) return false;
      if (query.collection === 'favorites' && !item.favorite) return false;
      if (query.collection === 'ungrouped' && item.collectionId !== null)
        return false;
      if (
        !['all', 'favorites', 'ungrouped'].includes(query.collection) &&
        item.collectionId !== query.collection
      )
        return false;
      if (
        query.tags.length > 0 &&
        !query.tags.some((tag) => item.tags.includes(tag))
      )
        return false;
      if (
        !activeFacets.every(([key, values]) =>
          values.includes(item.attributes[key]),
        )
      )
        return false;
      const searchable = [
        item.name,
        item.identifier,
        item.id,
        item.description,
        ...item.tags,
      ]
        .join(' ')
        .toLowerCase();
      return words.every((word) => searchable.includes(word));
    })
    .sort((left, right) => {
      const order =
        query.sort === 'name'
          ? nameCollator.compare(left.name, right.name)
          : timestamp(right.updatedAt) - timestamp(left.updatedAt);
      return order || compareId(left.id, right.id);
    });
}

export function createGroup(
  state: OrganizationState,
  scope: CatalogScope,
  name: string,
  id: string,
): OrganizationState {
  if (
    !isScope(scope) ||
    !isGroupId(id) ||
    state.groups.some((group) => group.id === id)
  ) {
    throw new Error('invalidGroup');
  }
  const group: Collection = {
    id,
    scope,
    name: groupName(name, scope, state.groups),
  };
  return { ...state, groups: [...state.groups, group] };
}

export function renameGroup(
  state: OrganizationState,
  id: string,
  name: string,
): OrganizationState {
  const existing = state.groups.find((group) => group.id === id);
  if (!existing) throw new Error('invalidGroup');
  const normalized = groupName(name, existing.scope, state.groups, id);
  return {
    ...state,
    groups: state.groups.map((group) =>
      group.id === id ? { ...group, name: normalized } : group,
    ),
  };
}

/** Deleting a collection only unassigns members; resources and favorites survive. */
export function deleteGroup(
  state: OrganizationState,
  id: string,
): OrganizationState {
  if (!state.groups.some((group) => group.id === id))
    throw new Error('invalidGroup');
  return {
    ...state,
    groups: state.groups.filter((group) => group.id !== id),
    memberships: Object.fromEntries(
      Object.entries(state.memberships).map(([itemId, groupId]) => [
        itemId,
        groupId === id ? null : groupId,
      ]),
    ),
  };
}

/** Validate the complete batch before applying it; hidden cross-scope IDs are rejected. */
export function moveItems(
  state: OrganizationState,
  scope: CatalogScope,
  ids: string[],
  targetId: string | null,
): OrganizationState {
  if (
    !isScope(scope) ||
    (targetId !== null &&
      !state.groups.some(
        (group) => group.id === targetId && group.scope === scope,
      ))
  ) {
    throw new Error('invalidGroup');
  }
  if (ids.some((id) => itemsById.get(id)?.scope !== scope))
    throw new Error('invalidResource');
  const memberships = { ...state.memberships };
  for (const id of ids) memberships[id] = targetId;
  return { ...state, memberships };
}

export function toggleFavorite(
  state: OrganizationState,
  id: string,
): OrganizationState {
  if (!itemsById.has(id)) throw new Error('invalidResource');
  return {
    ...state,
    favorites: state.favorites.includes(id)
      ? state.favorites.filter((favorite) => favorite !== id)
      : [...state.favorites, id],
  };
}

/**
 * Parse only the supplied string; persistence is the caller's responsibility.
 * Invalid envelopes reset to defaults. Invalid entries are removed, missing/orphaned
 * memberships become null, and every repair is surfaced through recovered.
 */
export function parseStoredState(raw: string | null): {
  state: OrganizationState;
  recovered: boolean;
} {
  if (raw === null) return { state: createInitialState(), recovered: false };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { state: createInitialState(), recovered: true };
  }
  if (
    !isRecord(parsed) ||
    parsed.version !== 1 ||
    !Array.isArray(parsed.groups) ||
    !isRecord(parsed.memberships) ||
    !Array.isArray(parsed.favorites)
  ) {
    return { state: createInitialState(), recovered: true };
  }

  let recovered = false;
  const groups: Collection[] = [];
  const groupIds = new Set<string>();
  for (const candidate of parsed.groups) {
    if (
      !isRecord(candidate) ||
      !isGroupId(candidate.id) ||
      !isScope(candidate.scope) ||
      typeof candidate.name !== 'string' ||
      groupIds.has(candidate.id)
    ) {
      recovered = true;
      continue;
    }
    try {
      const name = groupName(candidate.name, candidate.scope, groups);
      if (name !== candidate.name) recovered = true;
      groups.push({ id: candidate.id, scope: candidate.scope, name });
      groupIds.add(candidate.id);
    } catch {
      recovered = true;
    }
  }

  const groupsById = new Map(groups.map((group) => [group.id, group]));
  const memberships: Record<string, string | null> = {};
  for (const item of MOCK_ITEMS) {
    const membership = hasOwn(parsed.memberships, item.id)
      ? parsed.memberships[item.id]
      : undefined;
    if (membership === null) {
      memberships[item.id] = null;
    } else if (
      typeof membership === 'string' &&
      groupsById.get(membership)?.scope === item.scope
    ) {
      memberships[item.id] = membership;
    } else {
      memberships[item.id] = null;
      recovered = true;
    }
  }
  if (Object.keys(parsed.memberships).some((id) => !itemsById.has(id)))
    recovered = true;

  const favorites: string[] = [];
  const favoriteIds = new Set<string>();
  for (const candidate of parsed.favorites) {
    if (
      typeof candidate !== 'string' ||
      !itemsById.has(candidate) ||
      favoriteIds.has(candidate)
    ) {
      recovered = true;
      continue;
    }
    favorites.push(candidate);
    favoriteIds.add(candidate);
  }
  return { state: { version: 1, groups, memberships, favorites }, recovered };
}
