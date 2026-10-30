import { createContext, useEffect, useRef, type MutableRefObject, type ReactNode } from 'react';

interface DialogPointerTrigger {
  element: HTMLElement;
  activeElement: Element | null;
  capturedAt: number;
}

export const DialogFocusContext = createContext<MutableRefObject<DialogPointerTrigger | null> | null>(null);

/** Records pointer activation without changing the browser's normal focus behavior. */
export function DialogFocusProvider({ children }: { children: ReactNode }) {
  const triggerRef = useRef<DialogPointerTrigger | null>(null);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      triggerRef.current = null;
      if (event.button !== 0 || !(event.target instanceof Element)) return;
      const element = event.target.closest<HTMLElement>('button, a[href], input, select, textarea, [tabindex]');
      if (!element || element.tabIndex < 0 || element.matches(':disabled')
        || element.closest('[hidden], [inert], [aria-disabled="true"]')) return;
      triggerRef.current = { element, activeElement: document.activeElement, capturedAt: Date.now() };
    };
    const onKeyDown = () => { triggerRef.current = null; };
    // Mount-scoped listeners also cover portals and lazy-loaded dialogs. Nothing
    // is registered at module load, so remounts and HMR cannot accumulate them.
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
      triggerRef.current = null;
    };
  }, []);

  return <DialogFocusContext.Provider value={triggerRef}>{children}</DialogFocusContext.Provider>;
}
