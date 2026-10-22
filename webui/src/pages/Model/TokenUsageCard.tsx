import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Brain, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UsageStats } from '@/types';
import CacheUsagePanel from './CacheUsagePanel';
import { formatTokenMillions } from './usageDisplay';

export default function TokenUsageCard(props: {
  usageStats: UsageStats | null;
  loading: boolean;
  error: boolean;
  onRefresh: () => void;
}) {
  const { t } = useTranslation('model');
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [position, setPosition] = useState<{
    left: number; top?: number; bottom?: number; width: number; maxHeight: number;
  } | null>(null);
  const total = props.usageStats?.summary.total_tokens ?? 0;

  const cancelClose = () => clearTimeout(closeTimer.current);
  const close = () => { cancelClose(); setPosition(null); };
  const contains = (target: EventTarget | null) => target instanceof Node
    && (trigger.current?.contains(target) || panel.current?.contains(target));
  const show = () => {
    cancelClose();
    if (!trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const width = Math.min(640, window.innerWidth - 24);
    const below = window.innerHeight - rect.bottom - 20;
    const above = rect.top - 20;
    const useBelow = below >= Math.min(480, above);
    setPosition({
      left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)),
      width,
      ...(useBelow ? { top: rect.bottom + 8 } : { bottom: window.innerHeight - rect.top + 8 }),
      maxHeight: Math.max(0, useBelow ? below : above),
    });
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (!contains(document.activeElement)) setPosition(null);
    }, 160);
  };
  const onBlur = (event: React.FocusEvent) => {
    if (!contains(event.relatedTarget)) close();
  };
  const escape = (event: React.KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    if (panel.current?.contains(document.activeElement)) trigger.current?.focus();
    close();
  };

  useEffect(() => () => clearTimeout(closeTimer.current), []);
  useEffect(() => {
    if (!position) return;
    const outside = (event: PointerEvent) => { if (!contains(event.target)) close(); };
    const scroll = (event: Event) => { if (!(event.target instanceof Node && panel.current?.contains(event.target))) close(); };
    document.addEventListener('pointerdown', outside);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', scroll, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', scroll, true);
    };
  }, [position]);

  return <>
    <button
      ref={trigger}
      type="button"
      data-testid="model-total-tokens"
      aria-haspopup="dialog"
      aria-expanded={Boolean(position)}
      aria-controls={position ? id : undefined}
      onPointerEnter={show}
      onPointerLeave={scheduleClose}
      onFocus={show}
      onBlur={onBlur}
      onClick={show}
      onKeyDown={event => {
        escape(event);
        if (position && (event.key === 'ArrowDown' || (event.key === 'Tab' && !event.shiftKey))) {
          event.preventDefault();
          (panel.current?.querySelector<HTMLButtonElement>('button:not(:disabled)') ?? panel.current)?.focus();
        }
      }}
      className="rounded-lg border border-sky-200 bg-sky-50 p-3 text-left text-sky-800 hover:bg-sky-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2"
    >
      <span className="mb-1 flex items-center gap-2 text-xs font-medium opacity-70"><Brain className="h-5 w-5" aria-hidden="true" />{t('dashboard.totalTokens')}</span>
      <span className="block break-words text-lg font-bold">{props.loading && !props.usageStats ? '…' : props.error && !props.usageStats ? '—' : total > 0 ? formatTokenMillions(total) : t('dashboard.noUsage')}</span>
      <span className="mt-0.5 flex items-center gap-1 text-xs opacity-70"><Info className="h-3 w-3" aria-hidden="true" />{t('cacheUsage.details')}</span>
    </button>
    {position && createPortal(
      <div
        ref={panel}
        id={id}
        role="dialog"
        aria-label={t('cacheUsage.details')}
        tabIndex={-1}
        style={position}
        onPointerEnter={cancelClose}
        onPointerLeave={scheduleClose}
        onBlur={onBlur}
        onKeyDown={event => {
          escape(event);
          if (event.key === 'Tab') {
            event.preventDefault();
            if (event.shiftKey) {
              trigger.current?.focus();
            } else {
              // Continue after the trigger in page order, rather than at the
              // end of document.body where the portal happens to be mounted.
              const next = [...document.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
                .find(element => !panel.current?.contains(element) && trigger.current
                  && Boolean(trigger.current.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
                  && element.getClientRects().length > 0);
              (next ?? trigger.current)?.focus();
              close();
            }
          }
        }}
        className="fixed z-[1000] overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-xl outline-none dark:border-zinc-700 dark:bg-zinc-900"
      >
        <CacheUsagePanel {...props} />
      </div>, document.body,
    )}
  </>;
}
