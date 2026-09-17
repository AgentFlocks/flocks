import { afterEach, describe, expect, it, vi } from 'vitest';
import { INITIAL_GROUPS, MOCK_ITEMS, SCOPE_CONFIG } from './mockData';
import {
  STORAGE_KEY,
  createGroup,
  createInitialState,
  deleteGroup,
  moveItems,
  parseStoredState,
  renameGroup,
  resolveItems,
  selectItems,
  toggleFavorite,
} from './model';
import type {
  CatalogQuery,
  CatalogScope,
  OrganizationState,
  PluginItem,
} from './types';

const scopes = Object.keys(SCOPE_CONFIG) as CatalogScope[];
const query = (overrides: Partial<CatalogQuery> = {}): CatalogQuery => ({
  scope: 'workflow',
  query: '',
  collection: 'all',
  tags: [],
  facets: {},
  sort: 'name',
  ...overrides,
});
const ids = (items: PluginItem[]): string[] => items.map((item) => item.id);
const inScope = (scope: CatalogScope): PluginItem[] =>
  MOCK_ITEMS.filter((item) => item.scope === scope);
const groupFor = (scope: CatalogScope): string =>
  INITIAL_GROUPS.find((group) => group.scope === scope)!.id;
const sample = (
  id: string,
  overrides: Partial<PluginItem> = {},
): PluginItem => ({
  ...MOCK_ITEMS[0],
  id,
  name: id,
  identifier: `sample/${id}`,
  description: 'Sample only',
  scope: 'workflow',
  tags: [],
  attributes: {},
  collectionId: null,
  favorite: false,
  ...overrides,
});
const searchItems = (): PluginItem[] => [
  sample('row-1', {
    name: 'Alpha responder',
    identifier: 'evidence/triage',
    description: 'Network review',
    tags: ['urgent'],
    collectionId: 'group-a',
    favorite: true,
    attributes: { status: 'active', trigger: 'manual' },
    updatedAt: '2026-09-01T12:00:00.000Z',
  }),
  sample('row-2', {
    name: 'Beta planner',
    identifier: 'ops/triage',
    description: 'Endpoint response',
    tags: ['email'],
    attributes: { status: 'draft', trigger: 'manual' },
    updatedAt: '2026-09-02T12:00:00.000Z',
  }),
  sample('row-3', {
    name: 'Gamma reviewer',
    identifier: 'hunting/dns',
    description: 'Network review',
    tags: ['urgent', 'dns'],
    collectionId: 'group-b',
    attributes: { status: 'active', trigger: 'schedule' },
    updatedAt: '2026-09-03T12:00:00.000Z',
  }),
  sample('other-scope', {
    scope: 'agent',
    name: 'Alpha responder',
    tags: ['urgent'],
    favorite: true,
    attributes: { status: 'active', trigger: 'manual' },
  }),
];

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('deterministic catalog fixtures', () => {
  it('exports the six scopes and globally unique resource, identifier, and collection IDs', () => {
    expect(scopes).toEqual([
      'workflow',
      'agent',
      'skill',
      'tool',
      'device-template',
      'device-instance',
    ]);
    expect(MOCK_ITEMS).toHaveLength(144);
    expect(new Set(ids(MOCK_ITEMS)).size).toBe(MOCK_ITEMS.length);
    expect(new Set(MOCK_ITEMS.map((item) => item.identifier)).size).toBe(
      MOCK_ITEMS.length,
    );
    expect(new Set(INITIAL_GROUPS.map((group) => group.id)).size).toBe(24);
    expect(SCOPE_CONFIG['device-template'].type).toBe('device');
    expect(SCOPE_CONFIG['device-instance'].type).toBe('device');
  });

  it.each(scopes)(
    '%s has four business groups, diverse metadata, and 24–48 coherent items',
    (scope) => {
      const items = inScope(scope);
      const groups = INITIAL_GROUPS.filter((group) => group.scope === scope);
      expect(items.length).toBeGreaterThanOrEqual(24);
      expect(items.length).toBeLessThanOrEqual(48);
      expect(groups).toHaveLength(4);
      expect(new Set(items.map((item) => item.name)).size).toBe(items.length);
      expect(items.some((item) => item.collectionId === null)).toBe(true);
      expect(items.some((item) => item.favorite)).toBe(true);
      expect(items.some((item) => !item.favorite)).toBe(true);
      expect(items.some((item) => item.name.length > 50)).toBe(true);
      for (const group of groups)
        expect(items.some((item) => item.collectionId === group.id)).toBe(true);
      for (const item of items) {
        expect(item.tags.length).toBeGreaterThanOrEqual(3);
        expect(new Set(item.tags).size).toBe(item.tags.length);
        expect(item.description).toContain('模拟：');
        expect(item.description).toContain('项目');
        expect(item.details.length).toBeGreaterThanOrEqual(3);
        expect(Number.isFinite(Date.parse(item.updatedAt))).toBe(true);
        expect(item.updatedAt).toMatch(/^2026-09-/);
        if (item.collectionId !== null)
          expect(groups.map((group) => group.id)).toContain(item.collectionId);
      }
    },
  );

  it.each(scopes)(
    '%s facet values match localized options and exercise every option',
    (scope) => {
      const config = SCOPE_CONFIG[scope];
      const items = inScope(scope);
      expect(config.scope).toBe(scope);
      expect(config.title.zh).not.toBe('');
      expect(config.title.en).not.toBe('');
      expect(config.description.zh).not.toBe('');
      expect(config.description.en).not.toBe('');
      expect(config.facets).toHaveLength(3);
      for (const definition of config.facets) {
        expect(definition.label.zh).not.toBe('');
        expect(definition.label.en).not.toBe('');
        const options = definition.options.map((option) => option.value);
        for (const option of definition.options) {
          expect(option.label.zh).not.toBe('');
          expect(option.label.en).not.toBe('');
        }
        expect(
          new Set(items.map((item) => item.attributes[definition.key])),
        ).toEqual(new Set(options));
        for (const item of items)
          expect(options).toContain(item.attributes[definition.key]);
      }
      for (const item of items) {
        expect(Object.keys(item.attributes).sort()).toEqual(
          config.facets.map((definition) => definition.key).sort(),
        );
      }
    },
  );

  it('is unchanged by the clock or random source, including fixed timestamps', async () => {
    const before = JSON.stringify({
      items: MOCK_ITEMS,
      groups: INITIAL_GROUPS,
      config: SCOPE_CONFIG,
    });
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2040-01-01T00:00:00.000Z'));
    const random = vi.spyOn(Math, 'random').mockImplementation(() => {
      throw new Error('No random fixtures');
    });
    vi.resetModules();
    const fresh = await import('./mockData');
    expect(
      JSON.stringify({
        items: fresh.MOCK_ITEMS,
        groups: fresh.INITIAL_GROUPS,
        config: fresh.SCOPE_CONFIG,
      }),
    ).toBe(before);
    expect(MOCK_ITEMS[0].updatedAt).toBe('2026-09-16T12:00:00.000Z');
    expect(random).not.toHaveBeenCalled();
  });

  it('keeps template installation separate from instance connectivity and preserves source references', () => {
    const templates = inScope('device-template');
    const instances = inScope('device-instance');
    expect(templates.some((item) => item.attributes.state === 'broken')).toBe(
      true,
    );
    for (const template of templates) {
      expect(template.attributes).not.toHaveProperty('status');
      expect(template.attributes).not.toHaveProperty('room');
    }
    for (const instance of instances) {
      expect(instance.attributes).not.toHaveProperty('state');
      const sourceId = instance.details.find(
        (detail) => detail.label.en === 'Source template',
      )!.value;
      expect(templates.some((template) => template.id === sourceId)).toBe(true);
      const room = instance.details.find(
        (detail) => detail.label.en === 'Original room (read-only)',
      );
      expect(room?.value).toContain(instance.attributes.room);
      expect(instance.collectionId).not.toBe(instance.attributes.room);
    }
    const installedIds = new Set(
      templates
        .filter((item) => item.attributes.state === 'installed')
        .map((item) => item.id),
    );
    const installedInstances = instances.filter((instance) =>
      installedIds.has(
        instance.details.find(
          (detail) => detail.label.en === 'Source template',
        )!.value,
      ),
    );
    expect(
      installedInstances.some(
        (instance) => instance.attributes.status === 'unchecked',
      ),
    ).toBe(true);
    expect(
      installedInstances.some(
        (instance) => instance.attributes.status === 'error',
      ),
    ).toBe(true);
  });

  it.each(['workflow', 'agent', 'skill', 'tool'] as const)(
    '%s supports the Chinese demo searches for 情报 and 告警',
    (scope) => {
      for (const search of ['情报', '告警']) {
        expect(
          selectItems(MOCK_ITEMS, query({ scope, query: search })).length,
        ).toBeGreaterThan(0);
      }
      expect(
        INITIAL_GROUPS.filter((group) => group.scope === scope).every((group) =>
          /[一-鿿]/u.test(group.name),
        ),
      ).toBe(true);
    },
  );

  it('includes meaningful missing and unknown dependencies independent of enabled state', () => {
    const skills = inScope('skill');
    const missing = skills.filter(
      (item) => item.attributes.eligibility === 'missing',
    );
    const unknown = skills.filter(
      (item) => item.attributes.eligibility === 'unknown',
    );
    expect(missing.length).toBeGreaterThan(0);
    expect(unknown.length).toBeGreaterThan(0);
    expect(missing.some((item) => item.attributes.enabled === 'yes')).toBe(
      true,
    );
    expect(
      skills.some(
        (item) =>
          item.attributes.eligibility === 'ready' &&
          item.attributes.enabled === 'no',
      ),
    ).toBe(true);
    for (const item of missing)
      expect(
        item.details.some((detail) =>
          detail.value.includes('Missing dependency:'),
        ),
      ).toBe(true);
  });
});

describe('catalog selection', () => {
  it.each(scopes)(
    'returns only %s without leaking across scope boundaries',
    (scope) => {
      const items = selectItems(
        resolveItems(createInitialState()),
        query({ scope }),
      );
      expect(items).toHaveLength(inScope(scope).length);
      expect(items.every((item) => item.scope === scope)).toBe(true);
    },
  );

  it.each([
    ['ALPHA', ['row-1']],
    ['evidence/triage', ['row-1']],
    ['ROW-2', ['row-2']],
    ['endpoint', ['row-2']],
    ['URGENT', ['row-1', 'row-3']],
    ['  ALPHA  evidence\tNETWORK\nurgent ROW-1  ', ['row-1']],
    ['network email', []],
    ['  \n\t ', ['row-1', 'row-2', 'row-3']],
  ])(
    'searches all specified fields with case-insensitive AND words: %s',
    (search, expected) => {
      expect(
        ids(selectItems(searchItems(), query({ query: search as string }))),
      ).toEqual(expected);
    },
  );

  it('uses OR for selected tags and does not require every selected tag', () => {
    expect(
      ids(selectItems(searchItems(), query({ tags: ['urgent', 'email'] }))),
    ).toEqual(['row-1', 'row-2', 'row-3']);
    expect(
      ids(selectItems(searchItems(), query({ tags: ['dns', 'missing'] }))),
    ).toEqual(['row-3']);
    expect(selectItems(searchItems(), query({ tags: ['missing'] }))).toEqual(
      [],
    );
  });

  it('uses OR inside each facet and AND between facets, ignoring empty selections', () => {
    expect(
      ids(
        selectItems(
          searchItems(),
          query({
            facets: { status: ['active', 'draft'], trigger: ['manual'] },
          }),
        ),
      ),
    ).toEqual(['row-1', 'row-2']);
    expect(
      ids(
        selectItems(
          searchItems(),
          query({
            facets: { status: ['active'], trigger: ['manual'], unused: [] },
          }),
        ),
      ),
    ).toEqual(['row-1']);
    expect(
      selectItems(searchItems(), query({ facets: { unknown: ['value'] } })),
    ).toEqual([]);
  });

  it.each([
    ['all', ['row-1', 'row-2', 'row-3']],
    ['favorites', ['row-1']],
    ['ungrouped', ['row-2']],
    ['group-b', ['row-3']],
    ['unknown-group', []],
  ])('handles the %s collection', (collection, expected) => {
    expect(
      ids(
        selectItems(searchItems(), query({ collection: collection as string })),
      ),
    ).toEqual(expected);
  });

  it('intersects search, collection, tags, and facets without modifying its input', () => {
    const items = searchItems();
    const before = JSON.stringify(items);
    Object.freeze(items);
    expect(
      ids(
        selectItems(
          items,
          query({
            query: 'network triage',
            collection: 'favorites',
            tags: ['urgent', 'email'],
            facets: { status: ['active', 'draft'], trigger: ['manual'] },
          }),
        ),
      ),
    ).toEqual(['row-1']);
    expect(JSON.stringify(items)).toBe(before);
  });

  it('sorts updated descending, and uses the ID to break timestamp or name ties', () => {
    expect(ids(selectItems(searchItems(), query({ sort: 'updated' })))).toEqual(
      ['row-3', 'row-2', 'row-1'],
    );
    const tied = [
      sample('z', { name: 'Alpha' }),
      sample('a', { name: 'alpha' }),
    ];
    for (const sort of ['name', 'updated'] as const) {
      expect(ids(selectItems(tied, query({ sort })))).toEqual(['a', 'z']);
      expect(ids(selectItems([...tied].reverse(), query({ sort })))).toEqual([
        'a',
        'z',
      ]);
    }
    expect(ids(tied)).toEqual(['z', 'a']);
  });

  it('sorts actual timestamps, including timezone offsets, without NaN comparator results', () => {
    const items = [
      sample('old', { updatedAt: '2026-09-16T12:00:00+02:00' }),
      sample('new', { updatedAt: '2026-09-16T11:00:00Z' }),
      sample('invalid', { updatedAt: 'not-a-date' }),
    ];
    expect(ids(selectItems(items, query({ sort: 'updated' })))).toEqual([
      'new',
      'old',
      'invalid',
    ]);
  });
});

describe('immutable organization model', () => {
  it('returns independent initial state and resolved nested data', () => {
    const state = createInitialState();
    const pristine = createInitialState();
    const resolved = resolveItems(state);
    expect(resolved).toEqual(MOCK_ITEMS);
    state.groups[0].name = 'Changed';
    state.memberships[MOCK_ITEMS[0].id] = null;
    state.favorites.length = 0;
    resolved[0].tags.push('changed');
    resolved[0].attributes.status = 'changed';
    resolved[0].details[0].label.en = 'Changed';
    expect(createInitialState()).toEqual(pristine);
    expect(MOCK_ITEMS[0].tags).not.toContain('changed');
    expect(MOCK_ITEMS[0].attributes.status).not.toBe('changed');
    expect(MOCK_ITEMS[0].details[0].label.en).not.toBe('Changed');
  });

  it('resolves orphaned, missing, and cross-scope memberships as ungrouped', () => {
    const state = createInitialState();
    const [first, second, third] = inScope('workflow');
    state.memberships[first.id] = 'deleted-group';
    state.memberships[second.id] = groupFor('agent');
    delete state.memberships[third.id];
    const resolved = resolveItems(state);
    for (const item of [first, second, third])
      expect(
        resolved.find((entry) => entry.id === item.id)?.collectionId,
      ).toBeNull();
    expect(resolved).toHaveLength(MOCK_ITEMS.length);
  });

  it.each(scopes)(
    '%s CRUD and moving stay isolated; deletion preserves resources and favorites',
    (scope) => {
      const initial = createInitialState();
      const before = JSON.stringify(initial);
      const [first, second] = inScope(scope);
      const customId = `custom-${scope}`;
      const created = createGroup(
        initial,
        scope,
        '  Service owners  ',
        customId,
      );
      expect(created.groups.find((group) => group.id === customId)?.name).toBe(
        'Service owners',
      );
      const renamed = renameGroup(created, customId, '  Critical services  ');
      expect(renamed.groups.find((group) => group.id === customId)).toEqual({
        id: customId,
        scope,
        name: 'Critical services',
      });
      const moved = moveItems(renamed, scope, [first.id, second.id], customId);
      expect(moved.memberships[first.id]).toBe(customId);
      expect(moved.memberships[second.id]).toBe(customId);
      const deleted = deleteGroup(moved, customId);
      expect(deleted.memberships[first.id]).toBeNull();
      expect(deleted.memberships[second.id]).toBeNull();
      expect(deleted.favorites).toEqual(initial.favorites);
      expect(resolveItems(deleted)).toHaveLength(MOCK_ITEMS.length);
      expect(ids(resolveItems(deleted))).toEqual(ids(MOCK_ITEMS));
      expect(
        resolveItems(deleted).filter((item) => item.scope !== scope),
      ).toEqual(MOCK_ITEMS.filter((item) => item.scope !== scope));
      expect(JSON.stringify(initial)).toBe(before);
    },
  );

  it('trims names, rejects duplicate names in one scope, and permits the same name in another scope', () => {
    const initial = createInitialState();
    const created = createGroup(
      initial,
      'workflow',
      '  Review desk ',
      'custom-review',
    );
    expect(() =>
      createGroup(created, 'workflow', 'REVIEW DESK', 'duplicate'),
    ).toThrowError('duplicateName');
    expect(() =>
      renameGroup(created, groupFor('workflow'), ' review DESK '),
    ).toThrowError('duplicateName');
    expect(
      createGroup(created, 'agent', 'Review desk', 'agent-review').groups.slice(
        -1,
      )[0]?.scope,
    ).toBe('agent');
    expect(
      renameGroup(created, 'custom-review', 'review desk').groups.find(
        (group) => group.id === 'custom-review',
      )?.name,
    ).toBe('review desk');
  });

  it('validates empty and long names, counting Unicode code points rather than UTF-16 units', () => {
    const initial = createInitialState();
    expect(() =>
      createGroup(initial, 'workflow', ' \n\t ', 'empty'),
    ).toThrowError('emptyName');
    expect(() =>
      createGroup(initial, 'workflow', 'x'.repeat(33), 'long'),
    ).toThrowError('longName');
    expect(() => renameGroup(initial, groupFor('workflow'), '')).toThrowError(
      'emptyName',
    );
    expect(() =>
      renameGroup(initial, groupFor('workflow'), 'x'.repeat(33)),
    ).toThrowError('longName');
    const supplementaryCharacter = String.fromCodePoint(0x20000);
    expect(
      createGroup(
        initial,
        'workflow',
        supplementaryCharacter.repeat(32),
        'unicode',
      ).groups.slice(-1)[0]?.name,
    ).toBe(supplementaryCharacter.repeat(32));
  });

  it.each([
    '',
    ' ',
    'all',
    'favorites',
    'ungrouped',
    '__proto__',
    'constructor',
    ' bad',
    'bad\nline',
    'x'.repeat(129),
  ])('rejects invalid or reserved group ID %j', (id) => {
    expect(() =>
      createGroup(createInitialState(), 'workflow', 'Valid name', id),
    ).toThrowError('invalidGroup');
  });

  it('rejects duplicate IDs, invalid scopes, and operations on unknown groups', () => {
    const state = createInitialState();
    expect(() =>
      createGroup(state, 'workflow', 'Valid name', groupFor('agent')),
    ).toThrowError('invalidGroup');
    expect(() =>
      createGroup(state, 'invalid' as CatalogScope, 'Valid name', 'custom'),
    ).toThrowError('invalidGroup');
    expect(() => renameGroup(state, 'unknown', 'Valid name')).toThrowError(
      'invalidGroup',
    );
    expect(() => deleteGroup(state, 'unknown')).toThrowError('invalidGroup');
  });

  it('rejects invalid targets and complete mixed-scope batches atomically', () => {
    const state = createInitialState();
    const before = JSON.stringify(state);
    const workflowId = inScope('workflow')[0].id;
    const agentId = inScope('agent')[0].id;
    for (const target of ['unknown', 'favorites', groupFor('agent')]) {
      expect(() =>
        moveItems(state, 'workflow', [workflowId], target),
      ).toThrowError('invalidGroup');
    }
    for (const target of [null, groupFor('workflow')]) {
      expect(() =>
        moveItems(state, 'workflow', [workflowId, agentId], target),
      ).toThrowError('invalidResource');
      expect(() =>
        moveItems(state, 'workflow', [workflowId, 'unknown'], target),
      ).toThrowError('invalidResource');
    }
    expect(() =>
      moveItems(state, 'invalid' as CatalogScope, [], null),
    ).toThrowError('invalidGroup');
    expect(JSON.stringify(state)).toBe(before);
  });

  it('supports ungrouping and duplicate/empty selection without mutating other members', () => {
    const state = createInitialState();
    const id = inScope('workflow')[0].id;
    expect(moveItems(state, 'workflow', [], null)).toEqual(state);
    const moved = moveItems(state, 'workflow', [id, id], null);
    expect(moved.memberships[id]).toBeNull();
    expect(Object.keys(moved.memberships)).toHaveLength(MOCK_ITEMS.length);
    for (const item of MOCK_ITEMS.filter((item) => item.id !== id))
      expect(moved.memberships[item.id]).toBe(state.memberships[item.id]);
  });

  it('moves device instances without changing original room, connection, template, or status', () => {
    const state = createInitialState();
    const original = inScope('device-instance')[0];
    const target = INITIAL_GROUPS.find(
      (group) =>
        group.scope === original.scope && group.id !== original.collectionId,
    )!;
    const resolved = resolveItems(
      moveItems(state, original.scope, [original.id], target.id),
    );
    const moved = resolved.find((item) => item.id === original.id)!;
    expect(moved.collectionId).toBe(target.id);
    expect(moved.attributes).toEqual(original.attributes);
    expect(moved.details).toEqual(original.details);
    expect(moved.attributes.status).toBe('unchecked');
  });

  it('toggles only known favorites and keeps them through deletion of a seeded collection', () => {
    const initial = createInitialState();
    const item = inScope('workflow')[0];
    const toggled = toggleFavorite(initial, item.id);
    expect(toggled.favorites.includes(item.id)).toBe(
      !initial.favorites.includes(item.id),
    );
    const restored = toggleFavorite(toggled, item.id);
    expect(new Set(restored.favorites)).toEqual(new Set(initial.favorites));
    const deleted = deleteGroup(restored, item.collectionId!);
    expect(deleted.favorites).toEqual(restored.favorites);
    expect(
      resolveItems(deleted).find((entry) => entry.id === item.id)?.favorite,
    ).toBe(item.favorite);
    expect(() => toggleFavorite(initial, 'unknown')).toThrowError(
      'invalidResource',
    );
  });
});

describe('storage validation and recovery', () => {
  it('uses the exact versioned key and treats absent storage as a clean initial state', () => {
    expect(STORAGE_KEY).toBe('flocks:prototype:plugin-library:v1');
    expect(parseStoredState(null)).toEqual({
      state: createInitialState(),
      recovered: false,
    });
  });

  it('round-trips valid initial and edited states, including intentionally empty groups', () => {
    const initial = createInitialState();
    const item = inScope('tool')[0];
    const custom = moveItems(
      createGroup(initial, 'tool', 'My tools', 'custom-tools'),
      'tool',
      [item.id],
      'custom-tools',
    );
    const edited = toggleFavorite(custom, item.id);
    const empty: OrganizationState = {
      version: 1,
      groups: [],
      memberships: Object.fromEntries(
        MOCK_ITEMS.map((entry) => [entry.id, null]),
      ),
      favorites: [],
    };
    for (const state of [initial, edited, empty])
      expect(parseStoredState(JSON.stringify(state))).toEqual({
        state,
        recovered: false,
      });
  });

  it.each([
    '',
    '{',
    'null',
    '[]',
    '1',
    '"text"',
    '{}',
    '{"version":2,"groups":[],"memberships":{},"favorites":[]}',
    '{"version":"1","groups":[],"memberships":{},"favorites":[]}',
    '{"version":1,"groups":{},"memberships":{},"favorites":[]}',
    '{"version":1,"groups":[],"memberships":null,"favorites":[]}',
    '{"version":1,"groups":[],"memberships":[],"favorites":[]}',
    '{"version":1,"groups":[],"memberships":{},"favorites":{}}',
  ])('resets an invalid JSON envelope without throwing: %s', (raw) => {
    expect(parseStoredState(raw)).toEqual({
      state: createInitialState(),
      recovered: true,
    });
  });

  it('repairs malformed groups, duplicate IDs/names, reserved IDs, scopes, and name lengths', () => {
    const initial = createInitialState();
    const badGroups = [
      null,
      [],
      {},
      { id: 'missing-name', scope: 'workflow' },
      { id: 7, scope: 'workflow', name: 'Bad ID' },
      { id: 'invalid-scope', scope: 'device', name: 'Unknown scope' },
      { id: 'empty-name', scope: 'workflow', name: '  ' },
      { id: 'long-name', scope: 'workflow', name: 'x'.repeat(33) },
      { id: 'all', scope: 'workflow', name: 'Reserved ID' },
      { id: 'x'.repeat(129), scope: 'workflow', name: 'Overlong ID' },
      { ...initial.groups[0], name: 'Duplicate ID' },
      {
        id: 'duplicate-name',
        scope: 'workflow',
        name: ` ${initial.groups[0].name} `,
      },
      { id: 'trimmed', scope: 'workflow', name: '  Review desk  ' },
      { id: 'other-scope', scope: 'agent', name: 'Review desk' },
    ];
    const parsed = parseStoredState(
      JSON.stringify({ ...initial, groups: [...initial.groups, ...badGroups] }),
    );
    expect(parsed.recovered).toBe(true);
    expect(parsed.state.groups).toEqual([
      ...initial.groups,
      { id: 'trimmed', scope: 'workflow', name: 'Review desk' },
      { id: 'other-scope', scope: 'agent', name: 'Review desk' },
    ]);
    expect(parsed.state.memberships).toEqual(initial.memberships);
  });

  it('repairs orphan, cross-scope, invalid, and missing memberships and prunes unknown resources', () => {
    const initial = createInitialState();
    const [orphan, crossScope, malformed, missing] = inScope('workflow');
    const memberships: Record<string, unknown> = {
      ...initial.memberships,
      [orphan.id]: 'deleted-group',
      [crossScope.id]: groupFor('agent'),
      [malformed.id]: { id: groupFor('workflow') },
      'removed-resource': groupFor('workflow'),
    };
    delete memberships[missing.id];
    const parsed = parseStoredState(
      JSON.stringify({ ...initial, memberships }),
    );
    expect(parsed.recovered).toBe(true);
    for (const item of [orphan, crossScope, malformed, missing])
      expect(parsed.state.memberships[item.id]).toBeNull();
    expect(parsed.state.memberships).not.toHaveProperty('removed-resource');
    expect(Object.keys(parsed.state.memberships)).toHaveLength(
      MOCK_ITEMS.length,
    );
    expect(parsed.state.favorites).toEqual(initial.favorites);
    expect(parseStoredState(JSON.stringify(parsed.state)).recovered).toBe(
      false,
    );
  });

  it('ungroups members of rejected groups, while preserving all valid favorites', () => {
    const initial = createInitialState();
    const removed = initial.groups[0];
    const groups = initial.groups.map((group) =>
      group.id === removed.id ? { ...group, name: '' } : group,
    );
    const parsed = parseStoredState(JSON.stringify({ ...initial, groups }));
    expect(parsed.recovered).toBe(true);
    for (const item of MOCK_ITEMS.filter(
      (entry) => entry.collectionId === removed.id,
    ))
      expect(parsed.state.memberships[item.id]).toBeNull();
    expect(parsed.state.favorites).toEqual(initial.favorites);
    expect(resolveItems(parsed.state)).toHaveLength(MOCK_ITEMS.length);
  });

  it('deduplicates favorites and rejects malformed, unknown, and prototype-like IDs safely', () => {
    const initial = createInitialState();
    const id = MOCK_ITEMS[0].id;
    const raw = JSON.stringify({
      ...initial,
      memberships: {
        ...initial.memberships,
        ...JSON.parse('{"__proto__":{"polluted":true},"constructor":"bad"}'),
      },
      favorites: [id, id, 'unknown', null, 7, {}, '__proto__', 'constructor'],
      groups: [
        ...initial.groups,
        { id: '__proto__', scope: 'workflow', name: 'Bad group' },
      ],
    });
    const parsed = parseStoredState(raw);
    expect(parsed.recovered).toBe(true);
    expect(parsed.state.favorites).toEqual([id]);
    expect(parsed.state.memberships).toEqual(initial.memberships);
    expect(parsed.state.groups).toEqual(initial.groups);
    expect(Object.prototype).not.toHaveProperty('polluted');
  });

  it('imports and runs without browser globals, persistence calls, or network calls', async () => {
    const forbidden = vi.fn(() => {
      throw new Error('Browser API must not be called');
    });
    try {
      vi.stubGlobal('window', undefined);
      vi.stubGlobal('document', undefined);
      vi.stubGlobal('localStorage', {
        getItem: forbidden,
        setItem: forbidden,
        removeItem: forbidden,
      });
      vi.stubGlobal('sessionStorage', {
        getItem: forbidden,
        setItem: forbidden,
        removeItem: forbidden,
      });
      vi.stubGlobal('fetch', forbidden);
      vi.stubGlobal('WebSocket', forbidden);
      vi.resetModules();
      const pure = await import('./model');
      let state = pure.createInitialState();
      state = pure.createGroup(
        state,
        'workflow',
        'Offline collection',
        'offline',
      );
      state = pure.renameGroup(state, 'offline', 'Offline review');
      state = pure.moveItems(state, 'workflow', [MOCK_ITEMS[0].id], 'offline');
      state = pure.toggleFavorite(state, MOCK_ITEMS[0].id);
      state = pure.deleteGroup(state, 'offline');
      const stored = pure.parseStoredState(JSON.stringify(state));
      expect(stored.recovered).toBe(false);
      expect(
        pure.selectItems(pure.resolveItems(stored.state), query()),
      ).toHaveLength(24);
      expect(forbidden).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
