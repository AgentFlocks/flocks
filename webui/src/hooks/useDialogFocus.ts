import { useContext, useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { DialogFocusContext } from '@/contexts/DialogFocusContext';

const modalStack: HTMLElement[] = [];
let savedBodyOverflow = '';
// Allow a lazy-loaded dialog to mount without reusing an old click when a
// notification or other automatic modal appears later.
const POINTER_TRIGGER_MAX_AGE_MS = 5000;

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => {
    if (element.tabIndex < 0 || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    for (let ancestor: HTMLElement | null = element; ancestor && ancestor !== container; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  });
}

/** Shared modal keyboard behavior. Passing no close callback keeps a busy dialog open. */
export function useDialogFocus<T extends HTMLElement>(ref: RefObject<T | null>, open: boolean, onClose?: () => void) {
  const closeRef = useRef(onClose);
  const pointerTriggerRef = useContext(DialogFocusContext);
  useLayoutEffect(() => { closeRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const container = ref.current;
    if (!open || !container) return;
    const activeElement = document.activeElement;
    const pointerTrigger = pointerTriggerRef?.current;
    if (pointerTriggerRef) pointerTriggerRef.current = null;
    // macOS WebKit may leave BODY (or an unrelated input) focused when a
    // button is clicked. Prefer the captured activation unless focus has
    // deliberately moved elsewhere since then. Auto-focus inside a newly
    // mounted dialog should not replace its external return target.
    const restorePointerTrigger = pointerTrigger?.element.isConnected
      && Date.now() - pointerTrigger.capturedAt <= POINTER_TRIGGER_MAX_AGE_MS
      && !container.contains(pointerTrigger.element)
      && (activeElement === document.body || activeElement === pointerTrigger.element
        || activeElement === pointerTrigger.activeElement || container.contains(activeElement));
    const previousFocus = restorePointerTrigger
      ? pointerTrigger.element
      : activeElement instanceof HTMLElement && !container.contains(activeElement) ? activeElement : null;
    if (modalStack.length === 0) {
      savedBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    modalStack.push(container);
    const isTopmost = () => modalStack[modalStack.length - 1] === container
      && !Array.from(document.querySelectorAll('dialog[open]')).some((dialog) => !container.contains(dialog));
    const focusFirst = () => (focusableElements(container)[0] ?? container).focus();
    if (!container.contains(document.activeElement)) focusFirst();
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopmost() || event.defaultPrevented) return;
      if (event.key === 'Escape' && closeRef.current) {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      } else if (event.key === 'Tab') {
        const targets = focusableElements(container);
        const first = targets[0];
        const last = targets[targets.length - 1];
        if (!first) {
          event.preventDefault();
          container.focus();
        } else if (event.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (isTopmost() && event.target instanceof Node && !container.contains(event.target)) focusFirst();
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('focusin', onFocusIn);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('focusin', onFocusIn);
      const index = modalStack.lastIndexOf(container);
      const wasTopmost = index === modalStack.length - 1;
      if (index >= 0) modalStack.splice(index, 1);
      if (modalStack.length === 0) document.body.style.overflow = savedBodyOverflow;
      if (wasTopmost && previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open, ref, pointerTriggerRef]);
}
