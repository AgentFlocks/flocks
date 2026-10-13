import type { MouseEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { EnabledBadge } from '@/pages/Tool/components/badges';

/** The enabled badge doubles as the switch, so state and control sit in one place (card top-right). */
export default function EnabledToggle({ enabled, loading, title, onChange }: {
  enabled: boolean;
  loading: boolean;
  title?: string;
  onChange: (event: MouseEvent) => void;
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
      className="relative inline-flex rounded-full transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-300 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-75"
    >
      <EnabledBadge enabled={enabled} />
      {loading && (
        <span className="absolute inset-0 inline-flex items-center justify-center rounded-full bg-white/70 dark:bg-zinc-900/70">
          <Loader2 className="h-3 w-3 animate-spin text-gray-500 dark:text-zinc-300" />
        </span>
      )}
    </button>
  );
}
