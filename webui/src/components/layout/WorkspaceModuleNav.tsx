import { useEffect, useId, useRef, useState, type HTMLAttributes } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Layers, type LucideIcon } from 'lucide-react';
import { isSceneSuitesPath, SCENE_SUITES_PATH, type NavWorkspaceId } from '@/utils/navPartitions';
import { applyOrderByKey, navItemOrderStorageKey, readNavItemOrder, saveNavItemOrder } from '@/utils/workspaceNavOrder';

export interface HeaderWorkspace {
  id: NavWorkspaceId;
  name: string;
  icon: LucideIcon;
  separated?: boolean;
}

interface WorkspaceModuleNavProps {
  workspaces: HeaderWorkspace[];
  currentWorkspace: NavWorkspaceId | null;
  onSelectWorkspace: (id: NavWorkspaceId) => void;
  hasUnseenSuites: boolean;
  onOpenSceneManager: () => void;
}

const ORDER_SCOPE = 'header-modules';
const SCENES_ID = 'scene-management';
const DRAG_TYPE = 'application/x-flocks-header-module';

export default function WorkspaceModuleNav({
  workspaces, currentWorkspace, onSelectWorkspace, hasUnseenSuites, onOpenSceneManager,
}: WorkspaceModuleNavProps) {
  const { t } = useTranslation('nav');
  const location = useLocation();
  const hintId = useId();
  const [order, setOrder] = useState(() => readNavItemOrder(ORDER_SCOPE));
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: string; after: boolean } | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const activeDrag = useRef<string | null>(null);
  const suppressClick = useRef(false);
  const viewingScenes = isSceneSuitesPath(location.pathname);
  const entries = applyOrderByKey<HeaderWorkspace>([
    ...workspaces,
    { id: SCENES_ID, name: t('sceneManagement'), icon: Layers, separated: true },
  ], (entry) => entry.id, order);

  useEffect(() => {
    const syncOrder = (event: StorageEvent) => {
      if (event.key === null || event.key === navItemOrderStorageKey(ORDER_SCOPE)) {
        setOrder(readNavItemOrder(ORDER_SCOPE));
      }
    };
    window.addEventListener('storage', syncOrder);
    return () => window.removeEventListener('storage', syncOrder);
  }, []);

  const resetDrag = () => {
    activeDrag.current = null;
    setDraggedId(null);
    setDropTarget(null);
  };

  const reorder = (sourceId: string, targetId: string, after: boolean) => {
    if (sourceId === targetId || !entries.some((entry) => entry.id === sourceId)) return;
    const remaining = entries.filter((entry) => entry.id !== sourceId);
    const targetIndex = remaining.findIndex((entry) => entry.id === targetId);
    const source = entries.find((entry) => entry.id === sourceId);
    if (targetIndex < 0 || !source) return;
    remaining.splice(targetIndex + (after ? 1 : 0), 0, source);
    if (remaining.every((entry, index) => entry.id === entries[index].id)) return;

    // Keep temporarily hidden suites in their saved slots, including while
    // navigation data is loading. Re-enabling a suite restores its position.
    const visibleIds = new Set(entries.map((entry) => entry.id));
    const savedIds = [...new Set([...order, ...entries.map((entry) => entry.id)])];
    let index = 0;
    const nextOrder = savedIds.map((id) => visibleIds.has(id) ? remaining[index++].id : id);
    setOrder(nextOrder);
    saveNavItemOrder(ORDER_SCOPE, nextOrder);
    setAnnouncement(t('moduleMoved', { name: source.name, position: remaining.findIndex((entry) => entry.id === sourceId) + 1, count: entries.length }));
  };

  const dragProps = (id: string): HTMLAttributes<HTMLElement> => ({
    draggable: true,
    title: t('reorderModulesHint'),
    'aria-describedby': hintId,
    'aria-keyshortcuts': 'Alt+ArrowLeft Alt+ArrowRight',
    onPointerDown: () => { suppressClick.current = false; },
    onClickCapture: (event) => {
      if (!suppressClick.current) return;
      event.preventDefault();
      event.stopPropagation();
      suppressClick.current = false;
    },
    onDragStart: (event) => {
      activeDrag.current = id;
      suppressClick.current = true;
      setDraggedId(id);
      event.dataTransfer.setData(DRAG_TYPE, id);
      event.dataTransfer.effectAllowed = 'move';
    },
    onDragOver: (event) => {
      if (!activeDrag.current || !entries.some((entry) => entry.id === activeDrag.current)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      const rect = event.currentTarget.getBoundingClientRect();
      setDropTarget({ id, after: event.clientX > rect.left + rect.width / 2 });
    },
    onDragLeave: (event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropTarget(null);
    },
    onDrop: (event) => {
      const sourceId = activeDrag.current;
      if (!sourceId) return;
      event.preventDefault();
      if (event.dataTransfer.getData(DRAG_TYPE) === sourceId) {
        const rect = event.currentTarget.getBoundingClientRect();
        reorder(sourceId, id, event.clientX > rect.left + rect.width / 2);
      }
      resetDrag();
    },
    onDragEnd: resetDrag,
    onKeyDown: (event) => {
      suppressClick.current = false;
      if (event.key === 'Escape') resetDrag();
      if (!event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
      event.preventDefault();
      const after = event.key === 'ArrowRight';
      const target = entries[entries.findIndex((entry) => entry.id === id) + (after ? 1 : -1)];
      if (target) reorder(id, target.id, after);
    },
  });

  return (
    <nav aria-label={t('modules')} className="flex h-full min-w-0 flex-1 items-stretch overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <span id={hintId} className="sr-only">{t('reorderModulesHint')}</span>
      <span role="status" className="sr-only">{announcement}</span>
      {entries.map((entry, index) => {
        const isScenes = entry.id === SCENES_ID;
        const selected = isScenes ? viewingScenes : entry.id === currentWorkspace;
        const Icon = entry.icon;
        const className = `relative flex shrink-0 cursor-grab select-none items-center gap-1 border-b-2 px-3 text-sm transition-colors active:cursor-grabbing focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus ${entry.separated && index > 0 ? 'ml-3 before:absolute before:-left-1.5 before:top-4 before:h-6 before:border-l before:border-ui-border' : ''} ${selected ? 'border-red-600 font-medium text-ui-text' : 'border-transparent font-normal text-ui-text hover:bg-ui-hover hover:text-ui-text'} ${draggedId === entry.id ? 'opacity-40' : ''}`;
        const content = <>
          <Icon className="hidden h-4 w-4 shrink-0 stroke-[1.75] xl:block" />
          <span className="whitespace-nowrap">{entry.name}</span>
          {isScenes && hasUnseenSuites && <>
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" aria-hidden="true" />
            <span className="sr-only">{t('newBadge')}</span>
          </>}
          {dropTarget?.id === entry.id && draggedId !== entry.id && <span aria-hidden="true" className={`pointer-events-none absolute inset-y-2 w-0.5 rounded bg-red-600 ${dropTarget.after ? 'right-0' : 'left-0'}`} />}
        </>;
        const props = {
          ...dragProps(entry.id),
          'data-header-module': entry.id,
          'aria-current': selected ? 'page' as const : undefined,
          className,
        };
        return isScenes ? (
          <Link key={entry.id} {...props} to={SCENE_SUITES_PATH} onClick={onOpenSceneManager} aria-label={entry.name}>{content}</Link>
        ) : (
          <button key={entry.id} {...props} type="button" onClick={() => onSelectWorkspace(entry.id)}>{content}</button>
        );
      })}
    </nav>
  );
}
