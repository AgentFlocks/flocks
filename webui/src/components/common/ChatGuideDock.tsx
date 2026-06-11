import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronsLeft, ChevronsRight } from 'lucide-react';
import GuideInfoIcon from './GuideInfoIcon';

export interface ChatGuideAction {
  label: string;
  description: string;
  prompt: string;
}

interface ChatGuideDockProps {
  actions: ChatGuideAction[];
  disabled?: boolean;
  collapseTitle: string;
  expandTitle: string;
  onStartPrompt: (prompt: string, label: string) => void;
}

export default function ChatGuideDock({
  actions,
  disabled,
  collapseTitle,
  expandTitle,
  onStartPrompt,
}: ChatGuideDockProps) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleGuideWheel = useCallback((event: WheelEvent) => {
    const el = scrollRef.current;
    if (!el) return;

    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ? event.deltaX
      : event.deltaY;
    if (delta === 0) return;

    event.preventDefault();
    event.stopPropagation();
    el.scrollLeft += delta;
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || collapsed) return undefined;

    el.addEventListener('wheel', handleGuideWheel, { passive: false });
    return () => {
      el.removeEventListener('wheel', handleGuideWheel);
    };
  }, [collapsed, handleGuideWheel]);

  if (actions.length === 0) return null;

  return (
    <div className="flex w-full min-w-0 items-stretch gap-1.5">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-zinc-200 bg-white text-zinc-400 transition-colors hover:border-rose-200 hover:bg-rose-50/80 hover:text-rose-500"
        title={collapsed ? expandTitle : collapseTitle}
      >
        {collapsed ? <ChevronsRight className="h-3.5 w-3.5" /> : <ChevronsLeft className="h-3.5 w-3.5" />}
      </button>

      <div
        ref={scrollRef}
        className={`min-w-0 flex-1 overscroll-contain overflow-x-auto overflow-y-hidden transition-all duration-200 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
          collapsed ? 'basis-0 max-w-0 opacity-0 pointer-events-none' : 'basis-auto max-w-full opacity-100'
        }`}
      >
        <div className="flex w-max gap-1.5 pr-1">
          {actions.map((action) => (
            <div
              key={action.label}
              className="group inline-flex h-8 flex-shrink-0 items-center rounded-lg border border-zinc-200 bg-white text-zinc-700 transition-colors hover:border-rose-200 hover:bg-rose-50/80 hover:text-rose-600"
            >
              <button
                type="button"
                disabled={disabled}
                onClick={() => onStartPrompt(action.prompt, action.label)}
                className="flex h-full items-center whitespace-nowrap rounded-l-lg pl-2.5 pr-1 text-left text-xs font-semibold leading-none disabled:cursor-not-allowed disabled:opacity-50"
              >
                {action.label}
              </button>
              <GuideInfoIcon label={action.label} description={action.description} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
