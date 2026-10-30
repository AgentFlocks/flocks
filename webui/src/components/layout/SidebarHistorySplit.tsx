import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

const HANDLE_HEIGHT = 8;
const MIN_NAV_HEIGHT = 64;

interface SidebarHistorySplitProps {
  enabled: boolean;
  navigation: ReactNode;
  history: ReactNode;
}

/** The version footer is outside this region, so history always gets half its usable height. */
export default function SidebarHistorySplit({ enabled, navigation, history }: SidebarHistorySplitProps) {
  const { t } = useTranslation('nav');
  const navigationId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const navigationRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; target: HTMLDivElement; startY: number; startHeight: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  // Null leaves the navigation at its content height; a drag overrides it until reset.
  const [requestedHeight, setRequestedHeight] = useState<number | null>(null);
  const [measurements, setMeasurements] = useState({ available: 0, navigation: 0 });

  const stopDrag = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (drag?.target.hasPointerCapture(drag.pointerId)) drag.target.releasePointerCapture(drag.pointerId);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    window.addEventListener('blur', stopDrag);
    return () => window.removeEventListener('blur', stopDrag);
  }, [dragging, stopDrag]);

  useLayoutEffect(() => {
    if (!enabled) {
      stopDrag();
      return undefined;
    }
    const measure = () => {
      const available = rootRef.current?.clientHeight ?? 0;
      const navigation = Math.round(navigationRef.current?.getBoundingClientRect().height ?? 0);
      setMeasurements((previous) => previous.available === available && previous.navigation === navigation
        ? previous : { available, navigation });
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (rootRef.current) observer.observe(rootRef.current);
    if (navigationRef.current) observer.observe(navigationRef.current);
    return () => observer.disconnect();
  }, [enabled, stopDrag]);

  const maximum = Math.max(0, measurements.available / 2 - HANDLE_HEIGHT);
  const minimum = Math.min(MIN_NAV_HEIGHT, maximum);
  const resize = (height: number) => {
    // Read the live size as well, in case the window resized during a drag.
    const max = Math.max(0, (rootRef.current?.clientHeight ?? 0) / 2 - HANDLE_HEIGHT);
    setRequestedHeight(Math.max(Math.min(MIN_NAV_HEIGHT, max), Math.min(height, max)));
  };
  const finishDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerId !== dragRef.current?.pointerId) return;
    stopDrag();
  };

  return (
    <div ref={rootRef} className={`flex min-h-0 flex-1 flex-col ${dragging ? 'cursor-row-resize select-none' : ''}`}>
      <div
        ref={navigationRef}
        id={navigationId}
        className={`flex min-h-0 flex-col overflow-hidden ${enabled ? 'shrink-0' : 'flex-1'}`}
        // CSS enforces the limit before the observer fires and whenever the window shrinks.
        style={enabled ? { height: requestedHeight ?? undefined, maxHeight: `calc(50% - ${HANDLE_HEIGHT}px)` } : undefined}
      >
        {navigation}
      </div>
      {enabled && (
        <div
          role="separator"
          tabIndex={0}
          aria-label={t('history.resizeHeight')}
          aria-orientation="horizontal"
          aria-controls={navigationId}
          aria-valuemin={Math.round(minimum)}
          aria-valuemax={Math.round(maximum)}
          aria-valuenow={Math.round(Math.min(maximum, Math.max(minimum, measurements.navigation)))}
          className="flex h-2 shrink-0 touch-none cursor-row-resize items-center outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-zinc-400"
          onDoubleClick={() => setRequestedHeight(null)}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.blur();
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current = {
              pointerId: event.pointerId,
              target: event.currentTarget,
              startY: event.clientY,
              startHeight: navigationRef.current?.getBoundingClientRect().height ?? 0,
            };
            setDragging(true);
          }}
          onPointerMove={(event) => {
            const drag = dragRef.current;
            if (drag?.pointerId !== event.pointerId) return;
            // Recover if the release happened outside the window and pointerup was missed.
            if (event.buttons === 0) {
              stopDrag();
              return;
            }
            resize(drag.startHeight + event.clientY - drag.startY);
          }}
          onPointerUp={finishDrag}
          onPointerCancel={finishDrag}
          onLostPointerCapture={finishDrag}
          onKeyDown={(event) => {
            const current = navigationRef.current?.getBoundingClientRect().height ?? 0;
            if (event.key === 'ArrowUp') resize(current - 16);
            else if (event.key === 'ArrowDown') resize(current + 16);
            else if (event.key === 'Home') resize(minimum);
            else if (event.key === 'End') resize(maximum);
            else if (event.key === 'Enter') setRequestedHeight(null);
            else return;
            event.preventDefault();
          }}
        >
          <span aria-hidden="true" className="h-px w-full bg-zinc-200 dark:bg-zinc-800" />
        </div>
      )}
      {history}
    </div>
  );
}
