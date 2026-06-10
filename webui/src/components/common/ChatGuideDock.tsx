import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronsLeft, ChevronsRight, Info } from 'lucide-react';

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
  const [guideTooltip, setGuideTooltip] = useState<{
    title: string;
    description: string;
    x: number;
    y: number;
  } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const showGuideTooltip = useCallback((target: HTMLElement, title: string, description: string) => {
    const rect = target.getBoundingClientRect();
    setGuideTooltip({
      title,
      description,
      x: rect.left + rect.width / 2,
      y: rect.top - 8,
    });
  }, []);

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
            <button
              key={action.label}
              type="button"
              disabled={disabled}
              onClick={() => onStartPrompt(action.prompt, action.label)}
              className="inline-flex h-8 flex-shrink-0 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 text-left text-zinc-700 transition-colors hover:border-rose-200 hover:bg-rose-50/80 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
              title={action.label}
            >
              <span className="whitespace-nowrap text-xs font-semibold leading-none">{action.label}</span>
              <span
                className="group/info inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-white/80 hover:text-rose-500"
                title={action.description}
                onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                onPointerEnter={(event) => showGuideTooltip(event.currentTarget, action.label, action.description)}
                onMouseEnter={(event) => showGuideTooltip(event.currentTarget, action.label, action.description)}
                onMouseOver={(event) => showGuideTooltip(event.currentTarget, action.label, action.description)}
                onMouseLeave={() => setGuideTooltip(null)}
                onPointerLeave={() => setGuideTooltip(null)}
              >
                <Info className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
            </button>
          ))}
        </div>
      </div>
      {guideTooltip && (
        <div
          className="pointer-events-none fixed z-[80] w-48 -translate-x-1/2 -translate-y-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-zinc-600 shadow-md"
          style={{ left: guideTooltip.x, top: guideTooltip.y }}
        >
          <div className="mb-0.5 font-semibold text-zinc-800">{guideTooltip.title}</div>
          <div>{guideTooltip.description}</div>
          <div className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-zinc-200" />
        </div>
      )}
    </div>
  );
}
