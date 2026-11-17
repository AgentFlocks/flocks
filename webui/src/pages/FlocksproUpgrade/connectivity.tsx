import { Loader2, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ConsoleConnectivityState } from '@/hooks/useConsoleConnectivity';

export { useConsoleConnectivity, type LicenceMode } from '@/hooks/useConsoleConnectivity';
export type ConnectivityState = ConsoleConnectivityState;

/** One line saying whether Console answers from here, and so which way of licensing the page offers. */
export function ConnectivityLine({ state }: { state: ConnectivityState }) {
  const { t } = useTranslation('flockspro');
  const { connectivity, checking } = state;
  let dot = 'bg-gray-300';
  let text = t('network.checking');
  if (!checking && connectivity) {
    if (!connectivity.configured) {
      dot = 'bg-amber-500';
      text = t('network.notConfigured');
    } else if (connectivity.online) {
      dot = 'bg-emerald-500';
      text = t('network.online', { host: connectivity.console_host });
    } else {
      dot = 'bg-amber-500';
      text = t('network.offline', { host: connectivity.console_host });
    }
  } else if (!checking) {
    text = t('network.unknown');
  }
  return (
    <div className="flex items-center gap-2 text-sm text-gray-600 max-md:flex-wrap" data-testid="network-status" data-mode={state.mode}>
      {checking ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-gray-400" aria-hidden="true" />
      ) : (
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      )}
      <span className="min-w-0 flex-1">{text}</span>
      <button
        type="button"
        onClick={state.recheck}
        disabled={checking}
        className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:text-gray-300"
      >
        <RefreshCw className="h-3 w-3" aria-hidden="true" />
        {t('network.recheck')}
      </button>
    </div>
  );
}
