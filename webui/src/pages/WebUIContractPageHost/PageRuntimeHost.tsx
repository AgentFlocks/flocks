import {
  Component,
  type ComponentType,
  type ErrorInfo,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { AlertCircle, Loader2 } from 'lucide-react';
import { getApiBase } from '@/api/client';
import { webuiContractPagesAPI } from '@/api/webuiContractPages';
import { useSSE } from '@/hooks/useSSE';
import { useDelayedVisible } from '@/hooks/useDelayedVisible';
import { loadWebUIContractPageBundle } from './runtime';

interface WebUIContractPageErrorBoundaryProps {
  children: ReactNode;
  errorTitle: string;
  fallbackMessage: string;
  onError?: (message: string) => void;
}

interface WebUIContractPageErrorBoundaryState {
  hasError: boolean;
  message: string;
}

class WebUIContractPageErrorBoundary extends Component<
  WebUIContractPageErrorBoundaryProps,
  WebUIContractPageErrorBoundaryState
> {
  state: WebUIContractPageErrorBoundaryState = { hasError: false, message: '' };

  static getDerivedStateFromError(error: Error): WebUIContractPageErrorBoundaryState {
    return { hasError: true, message: error.message || '' };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error.message || this.props.fallbackMessage);
    console.error('[WebUIContractPageHost] render error:', error, info);
  }

  render() {
    if (this.state.hasError) {
      const message = this.state.message || this.props.fallbackMessage;
      return (
        <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800">
          <AlertCircle className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div>
            <div className="font-medium">{this.props.errorTitle}</div>
            <div className="mt-1 text-sm">{message}</div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

interface PageRuntimeHostProps {
  pageId?: string;
  initialBuildHash?: string;
}

export default function PageRuntimeHost({ pageId, initialBuildHash }: PageRuntimeHostProps) {
  const { t } = useTranslation('webuiContractPage');
  const tr = useCallback(
    (key: string) => i18n.t(key, { ns: 'webuiContractPage' }),
    [],
  );
  const [PageComponent, setPageComponent] = useState<ComponentType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [buildHash, setBuildHash] = useState('');
  const buildHashRef = useRef('');
  buildHashRef.current = buildHash;
  // The build on screen loaded fine (a reconnect can then skip reloading it).
  const healthyRef = useRef(false);
  healthyRef.current = Boolean(PageComponent) && !error;
  // Loads are numbered: an update event and a reconnect check can overlap, and
  // an earlier, slower answer must not replace a newer build.
  const loadSeqRef = useRef(0);
  const showLoading = useDelayedVisible(loading ? 180 : 0);

  const loadBundle = useCallback(async (hash: string, isCurrent: () => boolean) => {
    if (!pageId || !hash) return;
    const base = getApiBase();
    const url = `${base}/api/contracts/webui/pages/${encodeURIComponent(pageId)}/bundle.js?v=${encodeURIComponent(hash)}`;
    // The runtime is installed right before the bundle evaluates, inside the loader.
    const component = await loadWebUIContractPageBundle(url, tr('host.bundleMissingExport'), pageId);
    if (!isCurrent()) return;
    setPageComponent(() => component);
    setError(null);
  }, [pageId, tr]);

  const refreshPage = useCallback(async (hash?: string) => {
    if (!pageId) return;
    const seq = ++loadSeqRef.current;
    const isCurrent = () => loadSeqRef.current === seq;
    setLoading(true);
    try {
      let nextHash = hash;
      if (!nextHash) {
        const response = await webuiContractPagesAPI.get(pageId);
        if (!isCurrent()) return;
        nextHash = response.data.build.hash;
        if (response.data.build.status !== 'ready' || !nextHash) {
          setPageComponent(null);
          setError(response.data.build.error || tr('host.notBuilt'));
          return;
        }
      }
      setBuildHash(nextHash);
      await loadBundle(nextHash, isCurrent);
    } catch (err: unknown) {
      if (!isCurrent()) return;
      setPageComponent(null);
      setError(err instanceof Error ? err.message : tr('host.loadFailed'));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [loadBundle, pageId, tr]);

  useEffect(() => {
    void refreshPage(initialBuildHash);
  }, [initialBuildHash, refreshPage]);

  useSSE({
    url: '/api/event',
    onEvent: useCallback((evt) => {
      if (!pageId) return;
      if (evt.type === 'contracts.webui.pages.updated' && evt.properties?.id === pageId) {
        const hash = evt.properties?.hash as string | undefined;
        void refreshPage(hash);
        return;
      }
      if (evt.type === 'contracts.webui.pages.build_failed' && evt.properties?.id === pageId) {
        loadSeqRef.current += 1;
        setError((evt.properties?.error as string | undefined) || tr('host.buildFailed'));
        setLoading(false);
        return;
      }
      if (evt.type === 'contracts.webui.pages.api_changed' && evt.properties?.id === pageId) {
        setError(null);
        return;
      }
      if (evt.type === 'contracts.webui.pages.api_failed' && evt.properties?.id === pageId) {
        setError((evt.properties?.error as string | undefined) || tr('host.apiFailed'));
        setLoading(false);
      }
    }, [pageId, refreshPage, tr]),
    // Build events sent while the stream was down are not replayed: re-read the
    // build. A healthy page on the same build stays mounted; a failed build shows
    // its error; anything else (a new build, a page that failed to load) reloads.
    onReconnect: useCallback(() => {
      if (!pageId) return;
      const seq = loadSeqRef.current;
      void webuiContractPagesAPI.get(pageId).then((response) => {
        // A load that started meanwhile (an update event) is newer than this answer.
        if (loadSeqRef.current !== seq) return;
        const build = response.data.build;
        if (build.status === 'failed') {
          loadSeqRef.current += 1;
          setError(build.error || tr('host.buildFailed'));
          setLoading(false);
          return;
        }
        if (build.status !== 'ready' || !build.hash) return;
        if (build.hash === buildHashRef.current && healthyRef.current) return;
        void refreshPage(build.hash);
      }).catch(() => undefined);
    }, [pageId, refreshPage, tr]),
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  if (!pageId) {
    return <div className="text-sm text-zinc-500">{t('host.missingPageId')}</div>;
  }

  if (loading) {
    if (!showLoading) return null;
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('host.loading')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900">
        <AlertCircle className="mt-0.5 h-5 w-5 flex-shrink-0" />
        <div>
          <div className="font-medium">{t('host.unavailableTitle')}</div>
          <div className="mt-1 text-sm">{error}</div>
          <button
            type="button"
            onClick={() => void refreshPage(buildHash)}
            className="mt-3 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm hover:bg-amber-100"
          >
            {t('host.retry')}
          </button>
        </div>
      </div>
    );
  }

  if (!PageComponent) {
    return <div className="text-sm text-zinc-500">{t('host.emptyComponent')}</div>;
  }

  return (
    <WebUIContractPageErrorBoundary
      errorTitle={t('host.renderFailedTitle')}
      fallbackMessage={t('host.renderFailed')}
      onError={setError}
    >
      <PageComponent />
    </WebUIContractPageErrorBoundary>
  );
}
