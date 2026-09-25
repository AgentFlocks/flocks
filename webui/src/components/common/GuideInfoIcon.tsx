import { useCallback, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { IconInfoCircle } from '@tabler/icons-react';

interface GuideTooltip {
  title: string;
  description: string;
  x: number;
  y: number;
}

interface GuideInfoIconProps {
  label: string;
  description: string;
  className?: string;
  interactive?: boolean;
}

export default function GuideInfoIcon({
  label,
  description,
  className = '',
  interactive = true,
}: GuideInfoIconProps) {
  const [tooltip, setTooltip] = useState<GuideTooltip | null>(null);
  const tooltipId = useId();
  const canPortal = typeof document !== 'undefined';

  const showTooltip = useCallback((target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    setTooltip({
      title: label,
      description,
      x: rect.left + rect.width / 2,
      y: rect.top - 8,
    });
  }, [description, label]);

  const hideTooltip = useCallback(() => setTooltip(null), []);

  return (
    <>
      <span
        tabIndex={interactive ? 0 : undefined}
        className={`inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md text-fg transition-colors hover:bg-hover hover:text-danger ${className}`}
        aria-label={interactive ? `${label}说明` : undefined}
        aria-hidden={interactive ? undefined : true}
        aria-describedby={interactive && tooltip ? tooltipId : undefined}
        role={interactive ? 'img' : undefined}
        onMouseDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
        onPointerEnter={(event) => showTooltip(event.currentTarget)}
        onFocus={interactive ? (event) => showTooltip(event.currentTarget) : undefined}
        onMouseEnter={(event) => showTooltip(event.currentTarget)}
        onMouseOver={(event) => showTooltip(event.currentTarget)}
        onPointerLeave={hideTooltip}
        onBlur={interactive ? hideTooltip : undefined}
        onMouseLeave={hideTooltip}
      >
        <IconInfoCircle className="h-4 w-4" aria-hidden="true" />
      </span>
      {tooltip && canPortal && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none fixed z-[1000] w-52 -translate-x-1/2 -translate-y-full rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-fg-secondary shadow-md"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className="mb-0.5 font-semibold text-fg">{tooltip.title}</div>
          <div>{tooltip.description}</div>
          <div className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-line" />
        </div>,
        document.body,
      )}
    </>
  );
}
