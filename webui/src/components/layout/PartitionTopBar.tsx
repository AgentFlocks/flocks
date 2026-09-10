import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import type { NavPartitionId } from '@/utils/navPartitions';

export interface PartitionTopBarItem {
  id: NavPartitionId;
  name: string;
  icon: LucideIcon;
}

export interface PartitionTopBarScene {
  id: string;
  name: string;
}

interface PartitionTopBarProps {
  items: PartitionTopBarItem[];
  activeId: NavPartitionId;
  onSelect: (id: NavPartitionId) => void;
  /** Scenes of the active partition; only rendered when there is more than one. */
  scenes?: PartitionTopBarScene[];
  activeSceneId?: string | null;
  onSelectScene?: (id: string) => void;
  /** Optional right-aligned entry for the active partition (suite manager). */
  action?: { href: string; name: string; icon: LucideIcon; active?: boolean };
}

/**
 * The three fixed partitions above the content. Switching a partition swaps the
 * sidebar menu and lands on the page that partition was left on; the panes of
 * the other partitions stay alive underneath.
 */
export default function PartitionTopBar({
  items,
  activeId,
  onSelect,
  scenes,
  activeSceneId,
  onSelectScene,
  action,
}: PartitionTopBarProps) {
  const { t } = useTranslation('nav');
  // One scene needs no switcher: the sidebar already shows only its pages.
  const showScenes = Boolean(scenes && scenes.length > 1 && onSelectScene);

  return (
    <div className="relative z-20 flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-b border-zinc-200 bg-zinc-100 pl-20 pr-3 lg:pl-3 [scrollbar-width:none] dark:border-zinc-800 dark:bg-zinc-900 [&::-webkit-scrollbar]:hidden">
      <div
        role="tablist"
        aria-label={t('partitions')}
        className="flex shrink-0 items-center gap-1"
      >
        {items.map((item) => {
          const active = item.id === activeId;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-current={active ? 'page' : undefined}
              onClick={() => onSelect(item.id)}
              title={item.name}
              className={`flex h-8 shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-semibold transition-colors ${
                active
                  ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                  : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100'
              }`}
            >
              <Icon className={`h-4 w-4 shrink-0 ${active ? 'text-zinc-600 dark:text-zinc-300' : 'text-zinc-400 dark:text-zinc-500'}`} />
              <span className="min-w-0 truncate">{item.name}</span>
            </button>
          );
        })}
      </div>
      {showScenes && (
        <div
          role="tablist"
          aria-label={t('scenes')}
          className="ml-4 flex shrink-0 items-center gap-1 rounded-lg bg-zinc-200/60 p-0.5 dark:bg-zinc-800/60"
        >
          {scenes?.map((scene) => {
            const active = scene.id === activeSceneId;
            return (
              <button
                key={scene.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onSelectScene?.(scene.id)}
                title={scene.name}
                className={`flex h-7 shrink-0 items-center rounded-md px-2.5 text-xs font-semibold transition-colors ${
                  active
                    ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-zinc-50'
                    : 'text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100'
                }`}
              >
                <span className="min-w-0 truncate">{scene.name}</span>
              </button>
            );
          })}
        </div>
      )}
      {action && (
        <Link
          to={action.href}
          title={action.name}
          aria-current={action.active ? 'page' : undefined}
          className={`ml-auto inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium transition-colors ${
            action.active
              ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
              : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100'
          }`}
        >
          <action.icon className="h-4 w-4 shrink-0" />
          <span className="hidden sm:inline">{action.name}</span>
        </Link>
      )}
    </div>
  );
}
