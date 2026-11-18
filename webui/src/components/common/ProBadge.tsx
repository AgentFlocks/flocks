import { Gem } from 'lucide-react';

/**
 * The enterprise edition's mark: PRO on gold with a gem, the same in both themes but for the shade of gold.
 * The colours are the design's own (its account-pro-badge), not the palette's ambers.
 */
export const PRO_BADGE_CLASS =
  'inline-flex shrink-0 items-center gap-0.5 rounded-md border border-[#d59624] bg-gradient-to-br from-[#fff6d4] via-[#ffe69d] to-[#ffce65] px-1.5 text-[10px] font-bold leading-4 tracking-wide text-[#674000] dark:border-[#ffdb7c] dark:from-[#fff2c5] dark:via-[#ffdf8c] dark:to-[#ffc752] dark:text-[#593300]';

export default function ProBadge({ label = 'PRO', className = '' }: { label?: string; className?: string }) {
  return (
    <span data-testid="pro-badge" className={`${PRO_BADGE_CLASS} ${className}`.trim()}>
      <Gem aria-hidden="true" className="h-2.5 w-2.5" strokeWidth={2.4} />
      {label}
    </span>
  );
}
