import type { MouseEvent } from 'react';
import { Loader2 } from 'lucide-react';

/**
 * On/off switch for a plugin card's action bar. The card's top-right badge
 * shows the state; this is the only place that changes it.
 */
export default function EnabledSwitch({ enabled, loading = false, label, title, disabledReason, onChange }: {
  enabled: boolean;
  loading?: boolean;
  /** Visible text next to the switch, also its accessible name. */
  label: string;
  /** What a click does; shown as the tooltip. */
  title?: string;
  /** Set when the user cannot change the state; the switch is then disabled and says why. */
  disabledReason?: string;
  onChange: (event: MouseEvent) => void;
}) {
  const disabled = loading || disabledReason !== undefined;
  return (
    // Disabled buttons swallow hover, so the wrapper carries the tooltip.
    <span className="inline-flex items-center gap-1.5" title={disabledReason ?? title}>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={label}
        aria-busy={loading || undefined}
        aria-description={disabledReason}
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          onChange(event);
        }}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed ${
          enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-zinc-600'
        } ${disabledReason !== undefined ? 'opacity-50' : loading ? 'cursor-wait' : ''}`}
      >
        <span
          className={`inline-flex h-4 w-4 items-center justify-center rounded-full bg-white shadow-sm transition-transform ${
            enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        >
          {loading && <Loader2 className="h-3 w-3 animate-spin text-gray-500" />}
        </span>
      </button>
      <span aria-hidden className={`text-xs ${disabledReason !== undefined ? 'text-gray-400' : 'text-gray-600'}`}>{label}</span>
    </span>
  );
}
