import { Fragment, useEffect, useId, useState } from 'react';
import type { DragEvent } from 'react';
import {
  ArrowRightLeft,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  GripVertical,
  Pencil,
  Trash2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type {
  Collection,
  FacetDefinition,
  PluginItem,
  ScopeConfig,
} from './types';
import {
  attributeLabel,
  buttonClass,
  localized,
  TYPE_ICONS,
} from './presentation';

export interface PluginSection {
  id: string;
  name: string;
  group?: Collection;
  items: PluginItem[];
  totalCount: number;
  page: number;
  pages: number;
}

interface CatalogItemsProps {
  sections: PluginSection[];
  config: ScopeConfig;
  layout: 'cards' | 'list';
  selected: Set<string>;
  collapsed: Set<string>;
  dragging: boolean;
  onCollapse: (id: string) => void;
  onSelect: (id: string) => void;
  onDetail: (id: string) => void;
  onMove: (id: string) => void;
  onRename: (group: Collection) => void;
  onDelete: (group: Collection) => void;
  onDragStart: (event: DragEvent<HTMLElement>, id: string) => void;
  onDragEnd: () => void;
  onDrop: (groupId: string | null) => void;
  onPageChange: (groupId: string, page: number) => void;
}

const iconButtonClass =
  'inline-flex shrink-0 items-center justify-center rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500';
const titleButtonClass =
  'block max-w-full truncate rounded text-left text-sm font-medium text-gray-900 hover:text-red-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500';

function FacetBadge({
  item,
  config,
  facet,
}: {
  item: PluginItem;
  config: ScopeConfig;
  facet: FacetDefinition;
}) {
  const { i18n } = useTranslation('pluginLibrary');
  const value = item.attributes[facet.key];
  const label = value
    ? attributeLabel(config, facet.key, value, i18n.language)
    : '—';
  const color = ['error', 'broken', 'missing'].includes(value)
    ? 'bg-amber-50 text-amber-700 ring-amber-200'
    : ['active', 'ready', 'connected', 'installed'].includes(value) ||
        (facet.key === 'enabled' && value === 'yes')
      ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
      : 'bg-gray-50 text-gray-600 ring-gray-200';

  return (
    <span
      title={`${localized(facet.label, i18n.language)}: ${label}`}
      className={`inline-flex max-w-full items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${color}`}
    >
      <span className="truncate">{label}</span>
    </span>
  );
}

export default function CatalogItems({
  sections,
  config,
  layout,
  selected,
  collapsed,
  dragging,
  onCollapse,
  onSelect,
  onDetail,
  onMove,
  onRename,
  onDelete,
  onDragStart,
  onDragEnd,
  onDrop,
  onPageChange,
}: CatalogItemsProps) {
  const { t, i18n } = useTranslation('pluginLibrary');
  const listId = useId();
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const Icon = TYPE_ICONS[config.type];
  const contentId = (section: PluginSection) =>
    `${listId}-${section.id}-content`;
  const headingId = (section: PluginSection) =>
    `${listId}-${section.id}-heading`;

  useEffect(() => {
    if (!dragging) setDropTarget(null);
  }, [dragging]);

  const finishDrag = () => {
    setDropTarget(null);
    // Native dragend fires for both a completed drop and a cancelled drag.
    onDragEnd();
  };

  const itemDragProps = (item: PluginItem) => ({
    draggable: true,
    'data-plugin-id': item.id,
    onDragStart: (event: DragEvent<HTMLElement>) => onDragStart(event, item.id),
    onDragEnd: finishDrag,
  });

  const selection = (item: PluginItem) => (
    <div className="flex shrink-0 items-center gap-2">
      <span
        role="img"
        aria-label={t('dragItem', { name: item.name })}
        title={t('dragItem', { name: item.name })}
        className="cursor-grab text-gray-300 active:cursor-grabbing"
      >
        <GripVertical className="h-4 w-4" aria-hidden="true" />
      </span>
      <input
        type="checkbox"
        checked={selected.has(item.id)}
        onChange={() => onSelect(item.id)}
        aria-label={t('selectItem', { name: item.name })}
        className="h-4 w-4 shrink-0 cursor-pointer rounded border-gray-300 accent-red-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500"
      />
    </div>
  );

  const itemActions = (item: PluginItem) => (
    <div className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        onClick={() => onDetail(item.id)}
        aria-label={t('previewItem', { name: item.name })}
        title={t('previewItem', { name: item.name })}
        className={iconButtonClass}
      >
        <Eye className="h-4 w-4" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={() => onMove(item.id)}
        aria-label={t('moveItem', { name: item.name })}
        title={t('moveItem', { name: item.name })}
        className={iconButtonClass}
      >
        <ArrowRightLeft className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );

  const groupHeader = (section: PluginSection) => {
    const group = section.group;
    const targeted = dragging && dropTarget === section.id;
    return (
      <div
        data-group-id={section.id}
        onDragOver={(event) => {
          if (!dragging) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          setDropTarget(section.id);
        }}
        onDragLeave={(event) => {
          if (
            event.relatedTarget instanceof Node &&
            event.currentTarget.contains(event.relatedTarget)
          )
            return;
          setDropTarget((current) => (current === section.id ? null : current));
        }}
        onDrop={(event) => {
          if (!dragging) return;
          event.preventDefault();
          setDropTarget(null);
          onDrop(group?.id || null);
        }}
        className={`flex min-w-0 items-center gap-2 rounded-md px-2 transition-colors ${targeted ? 'bg-red-50 ring-1 ring-inset ring-red-200' : 'hover:bg-gray-50'}`}
      >
        <h3 className="min-w-0 flex-1">
          <button
            id={headingId(section)}
            type="button"
            onClick={() => onCollapse(section.id)}
            aria-expanded={!collapsed.has(section.id)}
            aria-controls={contentId(section)}
            className="flex w-full items-center gap-2 rounded py-2 text-left text-sm font-medium text-gray-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500"
          >
            {collapsed.has(section.id) ? (
              <ChevronRight
                className="h-4 w-4 shrink-0 text-gray-400"
                aria-hidden="true"
              />
            ) : (
              <ChevronDown
                className="h-4 w-4 shrink-0 text-gray-400"
                aria-hidden="true"
              />
            )}
            <span className="truncate" title={section.name}>
              {section.name}
            </span>
            <span className="shrink-0 text-xs font-normal tabular-nums text-gray-400">
              {section.totalCount}
            </span>
          </button>
        </h3>
        {targeted && (
          <span className="shrink-0 text-xs text-red-600">{t('dropHere')}</span>
        )}
        {group && (
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={() => onRename(group)}
              aria-label={t('renameGroup', { name: group.name })}
              title={t('grouping.rename')}
              className={iconButtonClass}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => onDelete(group)}
              aria-label={t('deleteGroup', { name: group.name })}
              title={t('deleteGroup', { name: group.name })}
              className={iconButtonClass}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
    );
  };

  const pagination = (section: PluginSection) =>
    section.pages > 1 ? (
      <div className="flex flex-wrap items-center justify-end gap-2 py-2 text-xs text-gray-500">
        <span className="tabular-nums">
          {t('groupPage', {
            page: section.page,
            pages: section.pages,
            count: section.totalCount,
          })}
        </span>
        <button
          type="button"
          className={buttonClass}
          aria-label={t('previousGroupPage', { name: section.name })}
          disabled={section.page <= 1}
          onClick={() => onPageChange(section.id, section.page - 1)}
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          className={buttonClass}
          aria-label={t('nextGroupPage', { name: section.name })}
          disabled={section.page >= section.pages}
          onClick={() => onPageChange(section.id, section.page + 1)}
        >
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
    ) : null;

  if (layout === 'list') {
    const facetKeys =
      config.type === 'skill'
        ? ['source', 'eligibility', 'enabled']
        : ['source', 'enabled', 'confirmation'];
    const facets = facetKeys
      .map((key) => config.facets.find((facet) => facet.key === key))
      .filter((facet): facet is FacetDefinition => Boolean(facet));
    const columns = facets.length + 3;

    return (
      <div className="relative overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="w-full min-w-[760px] text-left text-sm">
          <caption className="sr-only">{t('resourceList')}</caption>
          <thead className="border-b border-gray-200 bg-gray-50 text-xs text-gray-500">
            <tr>
              <th scope="col" className="w-20 px-4 py-3 font-medium">
                <span className="sr-only">{t('selection')}</span>
              </th>
              <th scope="col" className="px-2 py-3 font-medium">
                {t('name')}
              </th>
              {facets.map((facet) => (
                <th
                  key={facet.key}
                  scope="col"
                  className="px-3 py-3 font-medium"
                >
                  {localized(facet.label, i18n.language)}
                </th>
              ))}
              <th scope="col" className="px-3 py-3 font-medium">
                <span className="sr-only">{t('actions')}</span>
              </th>
            </tr>
          </thead>
          {sections.map((section) => (
            <Fragment key={section.id}>
              <tbody>
                <tr className="border-y border-gray-100">
                  <td colSpan={columns} className="px-2 py-1">
                    {groupHeader(section)}
                  </td>
                </tr>
              </tbody>
              <tbody
                id={contentId(section)}
                aria-labelledby={headingId(section)}
                hidden={collapsed.has(section.id)}
                className="divide-y divide-gray-100"
              >
                {section.items.map((item) => (
                  <tr
                    key={item.id}
                    {...itemDragProps(item)}
                    className={
                      selected.has(item.id)
                        ? 'bg-red-50/50'
                        : 'hover:bg-gray-50/70'
                    }
                  >
                    <td className="px-4 py-3">{selection(item)}</td>
                    <td className="max-w-[340px] px-2 py-3">
                      <button
                        type="button"
                        onClick={() => onDetail(item.id)}
                        className={titleButtonClass}
                        title={item.name}
                      >
                        {item.name}
                      </button>
                      <p
                        className="mt-0.5 truncate text-xs text-gray-500"
                        title={item.description}
                      >
                        {item.description}
                      </p>
                      <p
                        className="mt-1 truncate font-mono text-[10px] text-gray-400"
                        title={item.identifier}
                      >
                        {item.identifier}
                      </p>
                    </td>
                    {facets.map((facet) => (
                      <td key={facet.key} className="px-3 py-3">
                        <FacetBadge item={item} config={config} facet={facet} />
                      </td>
                    ))}
                    <td className="w-20 px-3 py-3">{itemActions(item)}</td>
                  </tr>
                ))}
                {section.items.length === 0 && (
                  <tr>
                    <td
                      colSpan={columns}
                      className="px-4 py-5 text-xs text-gray-400"
                    >
                      {t('emptyGroupInline')}
                    </td>
                  </tr>
                )}
                {section.pages > 1 && (
                  <tr>
                    <td colSpan={columns} className="px-3">
                      {pagination(section)}
                    </td>
                  </tr>
                )}
              </tbody>
            </Fragment>
          ))}
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {sections.map((section) => (
        <section key={section.id} aria-labelledby={headingId(section)}>
          {groupHeader(section)}
          <div id={contentId(section)} hidden={collapsed.has(section.id)}>
            {section.items.length === 0 ? (
              <p className="px-2 py-5 text-xs text-gray-400">
                {t('emptyGroupInline')}
              </p>
            ) : (
              <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {section.items.map((item) => {
                  const summary =
                    config.type === 'workflow'
                      ? item.details.find((detail) =>
                          /node|节点/i.test(
                            `${detail.label.en} ${detail.label.zh}`,
                          ),
                        )
                      : item.details[0];
                  return (
                    <article
                      key={item.id}
                      {...itemDragProps(item)}
                      className={`flex h-[212px] min-w-0 flex-col rounded-xl border bg-white p-3.5 transition-colors hover:border-gray-300 ${selected.has(item.id) ? 'border-red-300 ring-1 ring-red-100' : 'border-gray-200'}`}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-500">
                          <Icon className="h-4 w-4" aria-hidden="true" />
                        </div>
                        {config.facets[0] && (
                          <div className="min-w-0">
                            <FacetBadge
                              item={item}
                              config={config}
                              facet={config.facets[0]}
                            />
                          </div>
                        )}
                        <div className="ml-auto">{selection(item)}</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => onDetail(item.id)}
                        className={`${titleButtonClass} mt-2 shrink-0`}
                        title={item.name}
                      >
                        {item.name}
                      </button>
                      <p
                        className="mt-0.5 shrink-0 truncate font-mono text-[10px] leading-3 text-gray-400"
                        title={item.identifier}
                      >
                        {item.identifier}
                      </p>
                      <p
                        className="mt-1 line-clamp-2 min-h-8 text-xs leading-4 text-gray-500"
                        title={item.description}
                      >
                        {item.description}
                      </p>
                      {summary && (
                        <p
                          className="mt-1 truncate text-[10px] leading-4 text-gray-400"
                          title={`${localized(summary.label, i18n.language)}: ${summary.value}`}
                        >
                          {localized(summary.label, i18n.language)} ·{' '}
                          {summary.value}
                        </p>
                      )}
                      <div className="mt-auto flex items-center justify-between gap-1 pt-1">
                        <div className="flex min-w-0 items-center gap-1">
                          {config.facets.slice(1).map((facet) => (
                            <FacetBadge
                              key={facet.key}
                              item={item}
                              config={config}
                              facet={facet}
                            />
                          ))}
                        </div>
                        {itemActions(item)}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
            {pagination(section)}
          </div>
        </section>
      ))}
    </div>
  );
}
