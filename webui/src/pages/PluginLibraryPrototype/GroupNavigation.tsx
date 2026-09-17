import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Collection, PluginItem } from './types';

export default function GroupNavigation({
  groups,
  items,
  active,
  onSelect,
  onCreate,
  dragging,
  onDrop,
}: {
  groups: Collection[];
  items: PluginItem[];
  active: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  dragging: boolean;
  onDrop: (id: string | null) => void;
}) {
  const { t } = useTranslation('pluginLibrary');
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const counts = new Map<string, number>();
  for (const item of items) {
    const id = item.collectionId || 'ungrouped';
    counts.set(id, (counts.get(id) || 0) + 1);
  }

  useEffect(() => {
    if (!dragging) setDropTarget(null);
  }, [dragging]);

  const groupButton = (id: string, name: string, count: number) => {
    const targeted = dragging && dropTarget === id;
    return (
      <button
        key={id}
        type="button"
        data-group-nav-id={id}
        aria-label={t('navigateGroup', { name })}
        aria-current={active === id ? 'page' : undefined}
        title={name}
        onClick={() => onSelect(id)}
        onDragOver={(event) => {
          if (!dragging || id === 'all') return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          setDropTarget(id);
        }}
        onDragLeave={(event) => {
          if (
            event.relatedTarget instanceof Node &&
            event.currentTarget.contains(event.relatedTarget)
          )
            return;
          setDropTarget((current) => (current === id ? null : current));
        }}
        onDrop={(event) => {
          if (!dragging || id === 'all') return;
          event.preventDefault();
          setDropTarget(null);
          onDrop(id === 'ungrouped' ? null : id);
        }}
        className={`flex h-8 shrink-0 items-center gap-2 rounded-md px-2.5 text-left text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500 xl:w-full ${id === 'ungrouped' ? 'xl:mt-2' : ''} ${targeted ? 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200' : active === id ? 'bg-slate-100 font-medium text-slate-900' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'}`}
      >
        <span className="max-w-48 truncate xl:max-w-none xl:flex-1">
          {name}
        </span>
        <span
          title={t('groupCountHint')}
          className="ml-auto shrink-0 text-[11px] font-normal tabular-nums text-slate-500"
        >
          {count}
        </span>
      </button>
    );
  };

  return (
    <nav
      aria-label={t('groupNavigation')}
      className="flex min-w-0 items-start gap-1 xl:block"
    >
      <div className="order-last flex h-8 shrink-0 items-center justify-between xl:mb-1">
        <span
          title={t('groupCountHint')}
          className="hidden px-2.5 text-xs font-medium text-slate-500 xl:block"
        >
          {t('groupFilter')}
        </span>
        <button
          type="button"
          onClick={onCreate}
          aria-label={t('createGroup')}
          title={t('createGroup')}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-w-0 flex-1 overflow-x-auto xl:overflow-visible">
        <div className="flex w-max items-center gap-1 xl:w-full xl:flex-col xl:items-stretch xl:gap-0.5">
          {groupButton('all', t('allGroups'), items.length)}
          {groups.map((group) =>
            groupButton(group.id, group.name, counts.get(group.id) || 0),
          )}
          {groupButton(
            'ungrouped',
            t('ungrouped'),
            counts.get('ungrouped') || 0,
          )}
        </div>
      </div>
    </nav>
  );
}
