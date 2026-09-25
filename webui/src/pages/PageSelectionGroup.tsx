import type { HTMLAttributes } from 'react';

/** Arrow keys and Home/End activate the same choices as a pointer click. */
export default function PageSelectionGroup({ children, className = '', onKeyDown, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...props}
      className={`ui-segmented ${className}`}
      data-page-selection-group="true"
      onKeyDown={(event) => {
        onKeyDown?.(event);
        if (event.defaultPrevented || !(event.target instanceof HTMLButtonElement)) return;
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        const current = buttons.indexOf(event.target);
        if (current < 0 || buttons.length < 2) return;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
          : (current + (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
        event.preventDefault();
        buttons[next].focus();
        buttons[next].click();
      }}
    >
      {children}
    </div>
  );
}
