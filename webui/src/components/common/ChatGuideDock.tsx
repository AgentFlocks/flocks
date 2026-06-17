import { useEffect, useRef, useState } from 'react';
import { ChevronUp, ChevronsLeft, ChevronsRight } from 'lucide-react';
import GuideInfoIcon from './GuideInfoIcon';

export interface ChatGuideAction {
  label: string;
  description: string;
  prompt: string;
  group?: string;
}

interface ChatGuideDockProps {
  actions: ChatGuideAction[];
  disabled?: boolean;
  collapseTitle: string;
  expandTitle: string;
  onStartPrompt: (prompt: string, label: string) => void;
}

const RAIL_ACTION_LIMIT = 5;

function groupGuideActions(actions: ChatGuideAction[]) {
  const groups: Array<{ title: string; actions: ChatGuideAction[] }> = [];

  for (const action of actions) {
    const title = action.group?.trim() || '';
    let group = groups.find((item) => item.title === title);
    if (!group) {
      group = { title, actions: [] };
      groups.push(group);
    }
    group.actions.push(action);
  }

  return groups;
}

export default function ChatGuideDock({
  actions,
  disabled,
  collapseTitle,
  expandTitle,
  onStartPrompt,
}: ChatGuideDockProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const hasOverflowActions = actions.length > RAIL_ACTION_LIMIT;
  const railActions = actions.slice(0, RAIL_ACTION_LIMIT);
  const actionGroups = groupGuideActions(actions);
  const shouldShowGroupTitle = actionGroups.some((group) => group.title);

  const handleStartPrompt = (action: ChatGuideAction) => {
    setPanelOpen(false);
    onStartPrompt(action.prompt, action.label);
  };

  useEffect(() => {
    if (!panelOpen || collapsed) return undefined;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || rootRef.current?.contains(target)) return;
      setPanelOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [collapsed, panelOpen]);

  if (actions.length === 0) return null;

  const renderActionButton = (action: ChatGuideAction, variant: 'rail' | 'panel') => (
    <div
      key={action.label}
      className={`group inline-flex h-8 max-w-full items-center rounded-lg border border-zinc-200 bg-white text-zinc-700 transition-colors hover:border-rose-200 hover:bg-rose-50/70 hover:text-rose-600 ${
        variant === 'rail' ? 'flex-shrink-0' : 'min-w-0'
      }`}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => handleStartPrompt(action)}
        className="flex h-full min-w-0 flex-1 items-center truncate whitespace-nowrap rounded-l-lg pl-2.5 pr-1 text-left text-xs font-semibold leading-none disabled:cursor-not-allowed disabled:opacity-50"
      >
        {action.label}
      </button>
      <GuideInfoIcon label={action.label} description={action.description} />
    </div>
  );

  return (
    <div ref={rootRef} className="relative flex w-full min-w-0 items-stretch gap-1.5">
      {panelOpen && !collapsed && (
        <div
          data-testid="chat-guide-expanded-panel"
          className="absolute bottom-full left-0 right-0 z-30 mb-2 h-56 max-h-[calc(100vh-12rem)] overflow-hidden rounded-xl border border-zinc-200 bg-white/95 p-2 shadow-lg backdrop-blur"
        >
          <div className="flex h-full flex-col gap-3 overflow-y-auto pr-1 [scrollbar-width:thin] [scrollbar-color:#d4d4d8_transparent] dark:[scrollbar-color:#545d68_transparent]">
            {actionGroups.map((group, groupIndex) => (
              <section key={group.title || `group-${groupIndex}`} className="min-w-0">
                {shouldShowGroupTitle && group.title && (
                  <div className="mb-1.5 px-1 text-[11px] font-semibold leading-none text-zinc-400">
                    {group.title}
                  </div>
                )}
                <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-1.5">
                  {group.actions.map((action) => renderActionButton(action, 'panel'))}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => {
          setCollapsed((value) => {
            const next = !value;
            if (next) setPanelOpen(false);
            return next;
          });
        }}
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-zinc-200 bg-white text-zinc-400 transition-colors hover:border-rose-200 hover:bg-rose-50/70 hover:text-rose-500"
        title={collapsed ? expandTitle : collapseTitle}
        aria-label={collapsed ? expandTitle : collapseTitle}
        aria-expanded={!collapsed}
      >
        {collapsed ? <ChevronsRight className="h-3.5 w-3.5" /> : <ChevronsLeft className="h-3.5 w-3.5" />}
      </button>

      <div
        className={`min-w-0 flex-1 overflow-x-auto overflow-y-hidden pr-1 transition-all duration-200 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
          collapsed ? 'basis-0 max-w-0 opacity-0 pointer-events-none' : 'basis-auto max-w-full opacity-100'
        }`}
        onWheel={(event) => {
          const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
            ? event.deltaX
            : event.deltaY;
          if (delta === 0) return;
          event.currentTarget.scrollLeft += delta;
          event.preventDefault();
        }}
      >
        <div className="flex w-max gap-1.5">
          {!collapsed && railActions.map((action) => renderActionButton(action, 'rail'))}
        </div>
      </div>

      {hasOverflowActions && !collapsed && (
        <button
          type="button"
          onClick={() => setPanelOpen((value) => !value)}
          className={`inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border bg-white transition-colors ${
            panelOpen
              ? 'border-rose-200 text-rose-500'
              : 'border-zinc-200 text-zinc-400 hover:border-rose-200 hover:bg-rose-50/70 hover:text-rose-500'
          }`}
          title={panelOpen ? collapseTitle : expandTitle}
          aria-label={panelOpen ? collapseTitle : expandTitle}
          aria-expanded={panelOpen}
        >
          <ChevronUp className={`h-3.5 w-3.5 transition-transform ${panelOpen ? 'rotate-180' : ''}`} />
        </button>
      )}
    </div>
  );
}
