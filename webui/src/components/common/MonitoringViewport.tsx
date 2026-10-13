import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';

export const MonitoringViewportContext = createContext<{
  root: RefObject<HTMLDivElement | null>;
  active: boolean;
  focusMessageId?: string | null;
} | null>(null);

type Disclosure = { open: boolean; touched: boolean };
const DisclosureContext = createContext<Map<string, Disclosure> | null>(null);

/** Keep user expansion choices when a message leaves and re-enters the viewport. */
export function useMonitoringDisclosure(key: string, defaultOpen: boolean) {
  const cache = useContext(DisclosureContext);
  const [state, setState] = useState<Disclosure>(() => cache?.get(key) || { open: defaultOpen, touched: false });
  useEffect(() => {
    setState(previous => previous.touched || previous.open === defaultOpen ? previous : { open: defaultOpen, touched: false });
  }, [defaultOpen]);
  useEffect(() => { cache?.set(key, state); }, [cache, key, state]);
  return [state.open, (open: boolean) => {
    const next = { open, touched: true };
    cache?.set(key, next);
    setState(next);
  }] as const;
}

/** Lightweight anchor shells remain mounted; expensive message bodies only exist near the reader. */
export function MonitoringMessageViewport({ id, sourceIds, active, children }: {
  id: string; sourceIds?: string[]; active: boolean; children: ReactNode;
}) {
  const viewport = useContext(MonitoringViewportContext);
  const node = useRef<HTMLDivElement>(null);
  const height = useRef(240);
  const disclosures = useRef(new Map<string, Disclosure>());
  const [visible, setVisible] = useState(() => !viewport || typeof IntersectionObserver === 'undefined');
  const forced = active || !!(viewport?.focusMessageId && (id === viewport.focusMessageId || sourceIds?.includes(viewport.focusMessageId)));
  const mounted = !viewport || visible || forced;

  useEffect(() => {
    if (!viewport?.active || typeof IntersectionObserver === 'undefined' || !node.current) return;
    const observer = new IntersectionObserver(entries => {
      const entry = entries[entries.length - 1];
      if (entry) setVisible(entry.isIntersecting);
    }, { root: viewport.root.current, rootMargin: '1200px 0px' });
    observer.observe(node.current);
    return () => observer.disconnect();
  }, [viewport?.root, viewport?.active]);

  useLayoutEffect(() => {
    if (!viewport?.active || !mounted || !node.current) return;
    const measure = () => {
      const measured = node.current?.getBoundingClientRect().height || 0;
      if (measured > 0) height.current = measured;
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(node.current);
    return () => observer.disconnect();
  }, [mounted, viewport?.active]);

  return <div ref={node} data-message-id={id} data-monitor-placeholder={mounted ? undefined : ''}
    className="rounded-md transition-shadow duration-300" style={mounted ? undefined : { height: height.current }}>
    <DisclosureContext.Provider value={disclosures.current}>{mounted ? children : null}</DisclosureContext.Provider>
  </div>;
}

/** renderBody is deliberately a function: unopened payloads must not be serialized. */
export function MonitoringDeferredDetails({ summary, renderBody, cacheKey, className, testId }: {
  summary: ReactNode; renderBody: () => ReactNode; cacheKey: string; className?: string; testId?: string;
}) {
  const enabled = !!useContext(MonitoringViewportContext);
  const [open, setOpen] = useMonitoringDisclosure(cacheKey, false);
  return <details className={className} data-testid={testId} open={enabled ? open : undefined}>
    <summary className="cursor-pointer px-3 py-2.5 text-sm font-medium text-slate-800 dark:text-slate-200"
      onClick={enabled ? event => { event.preventDefault(); setOpen(!open); } : undefined}>{summary}</summary>
    {(!enabled || open) && renderBody()}
  </details>;
}
