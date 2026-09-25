import { useId, useLayoutEffect, useRef, type HTMLAttributes } from 'react';
import { useDialogFocus } from '@/hooks/useDialogFocus';

/** Keyboard and backdrop behavior shared by page-owned modal layouts. */
export default function PageDialog({
  onClose,
  children,
  onClick,
  variant = 'dialog',
  ...props
}: HTMLAttributes<HTMLDivElement> & { onClose?: () => void; variant?: 'dialog' | 'panels' }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(ref, true, onClose);
  useLayoutEffect(() => {
    const container = ref.current;
    if (!container || props['aria-label'] || props['aria-labelledby']) return;
    const currentTitle = document.getElementById(container.getAttribute('aria-labelledby') || '');
    if (currentTitle && container.contains(currentTitle)) return;
    const title = container.querySelector('h1, h2, h3, h4');
    if (title) {
      if (!title.id) title.id = titleId;
      container.setAttribute('aria-labelledby', title.id);
    }
  }, [props, titleId]);
  return (
    <div
      {...props}
      ref={ref}
      role="dialog"
      aria-modal="true"
      data-ui-overlay={variant === 'dialog' ? 'dialog' : undefined}
      tabIndex={-1}
      onClick={(event) => {
        // Clicking controls inside a panel must never dismiss that panel.
        if (event.target === event.currentTarget) {
          if (onClick) onClick(event);
          else onClose?.();
        }
      }}
    >
      {children}
    </div>
  );
}
