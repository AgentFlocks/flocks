import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  ChevronsDownUp,
  ChevronsUpDown,
  Info,
  Layers3,
  RotateCcw,
  SearchX,
  X,
} from 'lucide-react';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { SCOPE_CONFIG } from './mockData';
import {
  createGroup,
  createInitialState,
  deleteGroup,
  moveItems,
  renameGroup,
  resolveItems,
  selectItems,
} from './model';
import type { CatalogScope, PluginType } from './types';
import { usePrototypeState } from './usePrototypeState';
import { buttonClass, PLUGIN_TYPES, TYPE_ICONS } from './presentation';
import CatalogItems, { type PluginSection } from './CatalogItems';
import FilterToolbar from './FilterToolbar';
import GroupNavigation from './GroupNavigation';
import OrganizationDialog, {
  type OrganizationAction,
} from './OrganizationDialog';
import DetailDrawer from './DetailDrawer';

const GROUP_PAGE_SIZE = 6;

export default function PluginLibraryPrototype({
  pluginType,
}: {
  pluginType?: PluginType;
}) {
  const { t } = useTranslation('pluginLibrary');
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const requestedType = params.get('type');
  const type: PluginType =
    pluginType ||
    (PLUGIN_TYPES.includes(requestedType as PluginType)
      ? (requestedType as PluginType)
      : 'workflow');
  const scope: CatalogScope =
    type === 'device'
      ? params.get('deviceView') === 'templates'
        ? 'device-template'
        : 'device-instance'
      : type;
  const config = SCOPE_CONFIG[scope];
  const Icon = TYPE_ICONS[type];
  const layout = type === 'skill' || type === 'tool' ? 'list' : 'cards';
  const { state, setState, storageUnavailable, recovered, dismissRecovery } =
    usePrototypeState();
  const [collection, setCollection] = useState('all');
  const [query, setQuery] = useState('');
  const [facets, setFacets] = useState<Record<string, string[]>>({});
  const [sort, setSort] = useState<'updated' | 'name'>('updated');
  const [groupPages, setGroupPages] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [draggedIds, setDraggedIds] = useState<string[]>([]);
  const [action, setAction] = useState<OrganizationAction | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() => resolveItems(state), [state]);
  const scopeItems = useMemo(
    () => items.filter((item) => item.scope === scope),
    [items, scope],
  );
  const groups = state.groups.filter((group) => group.scope === scope);
  const results = useMemo(
    () =>
      selectItems(items, { scope, query, collection, facets, tags: [], sort }),
    [items, scope, query, collection, facets, sort],
  );
  const hasFilters =
    !!query.trim() || Object.values(facets).some((values) => values.length > 0);
  const sections: PluginSection[] = [
    ...groups.map((group) => ({ id: group.id, name: group.name, group })),
    { id: 'ungrouped', name: t('ungrouped'), group: undefined },
  ]
    .filter((group) => collection === 'all' || collection === group.id)
    .map((group) => {
      const groupItems = results.filter(
        (item) => (item.collectionId || 'ungrouped') === group.id,
      );
      const pages = Math.max(1, Math.ceil(groupItems.length / GROUP_PAGE_SIZE));
      const page = Math.max(1, Math.min(groupPages[group.id] || 1, pages));
      return {
        ...group,
        totalCount: groupItems.length,
        page,
        pages,
        items: groupItems.slice(
          (page - 1) * GROUP_PAGE_SIZE,
          page * GROUP_PAGE_SIZE,
        ),
      };
    })
    .filter((group) => !hasFilters || group.totalCount > 0);
  const visibleItems = sections.flatMap((group) =>
    collapsed.has(group.id) ? [] : group.items,
  );
  const selectedIds = visibleItems
    .filter((item) => selected.has(item.id))
    .map((item) => item.id);
  const detail = scopeItems.find((item) => item.id === detailId);
  const allCollapsed =
    sections.length > 0 && sections.every((group) => collapsed.has(group.id));

  useEffect(() => {
    setCollection('all');
    setQuery('');
    setFacets({});
    setGroupPages({});
    setCollapsed(new Set());
    setSelected(new Set());
    setDraggedIds([]);
    setDetailId(null);
    setAction(null);
  }, [scope]);
  useEffect(() => {
    setSelected(new Set());
    setDraggedIds([]);
  }, [query, collection, facets, sort, groupPages, collapsed]);
  useEffect(() => {
    if (selectAllRef.current)
      selectAllRef.current.indeterminate =
        selectedIds.length > 0 && selectedIds.length < visibleItems.length;
  }, [selectedIds.length, visibleItems.length]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        event.key !== '/' ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.isComposing ||
        document.querySelector('dialog[open]')
      )
        return;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      )
        return;
      event.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  const showMatches = () => {
    setGroupPages({});
    setCollapsed(new Set());
  };
  const clearFilters = () => {
    setQuery('');
    setFacets({});
    showMatches();
  };
  const changeCollection = (value: string) => {
    setCollection(value);
    showMatches();
  };
  const toggleFacet = (key: string, value: string) => {
    setFacets((current) => {
      const values = current[key] || [];
      return {
        ...current,
        [key]: values.includes(value)
          ? values.filter((entry) => entry !== value)
          : [...values, value],
      };
    });
    showMatches();
  };
  const expandGroup = (id: string | null) =>
    setCollapsed((current) => {
      const next = new Set(current);
      next.delete(id || 'ungrouped');
      return next;
    });
  const commitAction = (value: string) => {
    if (!action) return;
    if (action.kind === 'create') {
      const id = `collection-${crypto.randomUUID()}`;
      setState(createGroup(state, scope, value, id));
      setCollection('all');
      setQuery('');
      setFacets({});
      expandGroup(id);
    } else if (action.kind === 'rename') {
      setState(renameGroup(state, action.group.id, value));
    } else if (action.kind === 'delete') {
      setState(deleteGroup(state, action.group.id));
      if (collection === action.group.id) setCollection('ungrouped');
      expandGroup(null);
    } else if (action.kind === 'move') {
      setState(moveItems(state, scope, action.ids, value || null));
      expandGroup(value || null);
    } else {
      setState(createInitialState());
      setCollection('all');
      setQuery('');
      setFacets({});
      setGroupPages({});
      setCollapsed(new Set());
      setDetailId(null);
      dismissRecovery();
    }
    setSelected(new Set());
    setAction(null);
    toast.success(t(`feedback.${action.kind}`), t('feedback.localOnly'));
  };
  const startDrag = (event: DragEvent<HTMLElement>, id: string) => {
    const ids = selectedIds.includes(id) ? selectedIds : [id];
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', ids.join(','));
    setDraggedIds(ids);
  };
  const dropIntoGroup = (targetId: string | null) => {
    if (!draggedIds.length) return;
    try {
      setState(moveItems(state, scope, draggedIds, targetId));
      expandGroup(targetId);
      setSelected(new Set());
      toast.success(
        t('feedback.move'),
        t('movedTo', {
          count: draggedIds.length,
          name:
            groups.find((group) => group.id === targetId)?.name ||
            t('ungrouped'),
        }),
      );
    } catch {
      toast.error(t('errors.invalidGroup'));
    } finally {
      setDraggedIds([]);
    }
  };

  return (
    <div className="mx-auto max-w-[1500px] text-gray-900">
      <header
        className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2"
        data-testid="compact-page-header"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <Icon className="h-5 w-5 shrink-0 text-red-600" />
          <h1
            className="text-xl font-semibold tracking-tight"
            title={t(`pages.${type}.description`)}
          >
            {t(`pages.${type}.title`)}
          </h1>
          <span
            className="text-sm tabular-nums text-gray-400"
            aria-label={t('pageCount', { count: scopeItems.length })}
          >
            {scopeItems.length}
          </span>
          <span
            className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700"
            title={t('inlineNotice')}
          >
            Mock
          </span>
          <button
            type="button"
            onClick={() => setAction({ kind: 'reset' })}
            aria-label={t('reset')}
            title={t('reset')}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
        {type === 'device' && (
          <div className="inline-flex shrink-0 items-center rounded-lg border border-gray-200 bg-white p-0.5">
            {(['device-instance', 'device-template'] as const).map((entry) => (
              <button
                type="button"
                key={entry}
                aria-pressed={scope === entry}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set(
                    'deviceView',
                    entry === 'device-instance' ? 'instances' : 'templates',
                  );
                  setParams(next);
                }}
                className={`rounded-md px-2 py-1 text-xs font-medium ${scope === entry ? 'bg-slate-100 text-slate-900' : 'text-gray-400 hover:text-gray-700'}`}
              >
                {t(
                  entry === 'device-template'
                    ? 'deviceTemplates'
                    : 'deviceInstances',
                )}
                <span className="ml-1.5 text-[10px] text-gray-400">
                  {items.filter((item) => item.scope === entry).length}
                </span>
              </button>
            ))}
          </div>
        )}
        <div className="flex w-full min-w-0 items-center sm:ml-auto sm:w-auto">
          <FilterToolbar
            config={config}
            query={query}
            facets={facets}
            searchRef={searchRef}
            onQuery={(value) => {
              setQuery(value);
              showMatches();
            }}
            onFacet={toggleFacet}
            onClear={clearFilters}
          />
        </div>
      </header>
      {(storageUnavailable || recovered) && (
        <div
          role="status"
          className="mb-3 flex items-center gap-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800"
        >
          <Info className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            {t(storageUnavailable ? 'storageUnavailable' : 'storageRecovered')}
          </span>
          {!storageUnavailable && (
            <button
              type="button"
              onClick={dismissRecovery}
              aria-label={t('close')}
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      )}

      <div className="flex min-w-0 flex-col gap-3 xl:flex-row xl:gap-4">
        <div className="min-w-0 xl:sticky xl:top-20 xl:max-h-[calc(100dvh-112px)] xl:w-40 xl:shrink-0 xl:self-start xl:overflow-y-auto">
          <GroupNavigation
            groups={groups}
            items={scopeItems}
            active={collection}
            onSelect={changeCollection}
            onCreate={() => setAction({ kind: 'create' })}
            dragging={draggedIds.length > 0}
            onDrop={dropIntoGroup}
          />
        </div>
        <section aria-label={t('resourceList')} className="min-w-0 flex-1">
          <div
            className="mb-1 flex min-h-8 flex-wrap items-center justify-between gap-2"
            data-testid="list-toolbar"
          >
            <div className="flex min-w-0 flex-wrap items-center gap-2.5">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-500">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  disabled={!visibleItems.length}
                  checked={
                    visibleItems.length > 0 &&
                    selectedIds.length === visibleItems.length
                  }
                  onChange={() =>
                    setSelected(
                      selectedIds.length === visibleItems.length
                        ? new Set()
                        : new Set(visibleItems.map((item) => item.id)),
                    )
                  }
                  className="h-3.5 w-3.5 accent-slate-800"
                />
                <span className="sr-only sm:not-sr-only">
                  {t('selectVisible')}
                </span>
              </label>
              {selectedIds.length > 0 ? (
                <div
                  role="region"
                  aria-label={t('batchActions')}
                  className="flex flex-wrap items-center gap-2.5 text-xs"
                >
                  <span className="font-medium text-slate-700">
                    {t('selectedCount', { count: selectedIds.length })}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setAction({ kind: 'move', ids: selectedIds })
                    }
                    className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-red-600"
                  >
                    <Layers3 className="h-3.5 w-3.5" />
                    {t('moveTo')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    className="text-gray-400 hover:text-gray-700"
                  >
                    {t('cancelSelection')}
                  </button>
                </div>
              ) : (
                <span aria-live="polite" className="text-xs text-gray-400">
                  {t(hasFilters ? 'matchedCount' : 'compactCount', {
                    count: results.length,
                  })}
                </span>
              )}
            </div>
            <div
              className={`${selectedIds.length > 0 ? 'hidden sm:flex' : 'flex'} shrink-0 items-center gap-2 text-xs text-gray-500`}
            >
              <select
                aria-label={t('sort')}
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as 'updated' | 'name');
                  setGroupPages({});
                }}
                className="max-w-28 border-0 bg-transparent py-1 text-xs outline-none focus:ring-1 focus:ring-gray-300"
              >
                <option value="updated">{t('sortUpdated')}</option>
                <option value="name">{t('sortName')}</option>
              </select>
              <button
                type="button"
                onClick={() =>
                  setCollapsed(
                    allCollapsed
                      ? new Set()
                      : new Set(sections.map((group) => group.id)),
                  )
                }
                disabled={!sections.length}
                className="inline-flex items-center gap-1 rounded p-1 hover:bg-gray-100 hover:text-gray-900 disabled:opacity-40"
              >
                {allCollapsed ? (
                  <ChevronsUpDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronsDownUp className="h-3.5 w-3.5" />
                )}
                {t(allCollapsed ? 'expandAll' : 'collapseAll')}
              </button>
            </div>
          </div>
          {hasFilters && results.length === 0 ? (
            <div className="rounded-xl border border-dashed border-gray-200 bg-white py-6">
              <EmptyState
                icon={<SearchX className="h-7 w-7" />}
                title={t('noResults')}
                description={t('noResultsDescription')}
                action={
                  <button
                    type="button"
                    onClick={clearFilters}
                    className={buttonClass}
                  >
                    {t('clearFilters')}
                  </button>
                }
              />
            </div>
          ) : (
            <CatalogItems
              sections={sections}
              config={config}
              layout={layout}
              selected={selected}
              collapsed={collapsed}
              dragging={draggedIds.length > 0}
              onCollapse={(id) =>
                setCollapsed((current) => {
                  const next = new Set(current);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                })
              }
              onSelect={(id) =>
                setSelected((current) => {
                  const next = new Set(current);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                })
              }
              onDetail={setDetailId}
              onMove={(id) => setAction({ kind: 'move', ids: [id] })}
              onRename={(group) => setAction({ kind: 'rename', group })}
              onDelete={(group) =>
                setAction({
                  kind: 'delete',
                  group,
                  count: scopeItems.filter(
                    (item) => item.collectionId === group.id,
                  ).length,
                })
              }
              onDragStart={startDrag}
              onDragEnd={() => setDraggedIds([])}
              onDrop={dropIntoGroup}
              onPageChange={(id, page) =>
                setGroupPages((current) => ({ ...current, [id]: page }))
              }
            />
          )}
          <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-5 text-gray-400">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {t('compactDragHint')}
          </p>
        </section>
      </div>
      {action && (
        <OrganizationDialog
          key={action.kind + ('group' in action ? action.group.id : '')}
          action={action}
          groups={groups}
          onClose={() => setAction(null)}
          onCommit={commitAction}
        />
      )}
      {detail && !action && (
        <DetailDrawer
          item={detail}
          config={config}
          groups={groups}
          onClose={() => setDetailId(null)}
          onMove={() => {
            setDetailId(null);
            setAction({ kind: 'move', ids: [detail.id] });
          }}
        />
      )}
    </div>
  );
}
