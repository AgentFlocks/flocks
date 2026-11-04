import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

const STORAGE_KEY = 'flocks_layout_sidebar_width';
const DEFAULT_WIDTH = 192;
const MIN_WIDTH = 192;
const MAX_WIDTH = 480;

function readWidth(): number {
  try {
    const value = Number(localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(value) && value > 0) return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, value));
  } catch {
    // Width memory is optional when browser storage is unavailable.
  }
  return DEFAULT_WIDTH;
}

function maximumWidth(): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(window.innerWidth * 0.4)));
}

/** Keep the expanded width across routes, collapse/expand and browser reloads. */
export function useSidebarResize(enabled: boolean) {
  const [requestedWidth, setRequestedWidth] = useState(readWidth);
  const [maximum, setMaximum] = useState(maximumWidth);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  const width = Math.min(requestedWidth, maximum);

  useEffect(() => {
    const measure = () => setMaximum(maximumWidth());
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  useEffect(() => {
    if (!enabled) {
      dragRef.current = null;
      setDragging(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!dragging) return;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const stop = () => { dragRef.current = null; setDragging(false); };
    window.addEventListener('blur', stop);
    return () => {
      document.body.style.cursor = cursor;
      document.body.style.userSelect = userSelect;
      window.removeEventListener('blur', stop);
    };
  }, [dragging]);

  const resize = (next: number) => {
    const clamped = Math.round(Math.max(MIN_WIDTH, Math.min(maximumWidth(), next)));
    setRequestedWidth(clamped);
    try {
      localStorage.setItem(STORAGE_KEY, String(clamped));
    } catch {
      // Resizing still works without persistence.
    }
  };

  const finishDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerId !== dragRef.current?.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return {
    width,
    minimum: MIN_WIDTH,
    maximum,
    dragging,
    handleProps: {
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        if (!enabled || event.button !== 0) return;
        event.preventDefault();
        // Pointer dragging must not leave the keyboard focus ring on the full-height handle.
        event.currentTarget.blur();
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width };
        setDragging(true);
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        const drag = dragRef.current;
        if (drag?.pointerId === event.pointerId) resize(drag.startWidth + event.clientX - drag.startX);
      },
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onLostPointerCapture: () => { dragRef.current = null; setDragging(false); },
      onDoubleClick: () => resize(DEFAULT_WIDTH),
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'ArrowLeft') resize(width - 16);
        else if (event.key === 'ArrowRight') resize(width + 16);
        else if (event.key === 'Home') resize(MIN_WIDTH);
        else if (event.key === 'End') resize(maximum);
        else if (event.key === 'Enter') resize(DEFAULT_WIDTH);
        else return;
        event.preventDefault();
      },
    },
  };
}
