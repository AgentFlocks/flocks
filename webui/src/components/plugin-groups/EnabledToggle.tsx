import type { MouseEvent } from 'react';
import { CheckCircle, Loader2, XCircle } from 'lucide-react';
import { EnabledBadge } from '@/pages/Tool/components/badges';

/** The enabled badge doubles as the switch, so state and control sit in one place (card top-right). */
export default function EnabledToggle({ enabled, loading, title, onChange, compact = false }: {
  enabled: boolean;
  loading: boolean;
  title?: string;
  onChange: (event: MouseEvent) => void;
  /** Icon-only pill for phone widths, where the text pill does not fit next to other actions. */
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={title}
      onClick={onChange}
      disabled={loading}
      title={title}
      className="relative inline-flex shrink-0 rounded-full transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-75"
    >
      {compact ? (
        <span className={`inline-flex h-7 w-7 items-center justify-center rounded-full ${enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
          {enabled ? <CheckCircle aria-hidden="true" className="h-3.5 w-3.5" /> : <XCircle aria-hidden="true" className="h-3.5 w-3.5" />}
        </span>
      ) : (
        <EnabledBadge enabled={enabled} />
      )}
      {loading && (
        <span className="absolute inset-0 inline-flex items-center justify-center rounded-full bg-white/70 dark:bg-zinc-900/70">
          <Loader2 className="h-3 w-3 animate-spin text-gray-500 dark:text-zinc-300" />
        </span>
      )}
    </button>
  );
}
