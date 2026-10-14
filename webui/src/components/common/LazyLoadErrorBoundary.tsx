import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { isChunkLoadError, retryChunkLoad } from '@/utils/chunkLoadRecovery';

interface LazyLoadErrorBoundaryProps {
  children: ReactNode;
  mode?: 'page' | 'overlay';
  onRetry?: () => void;
}

interface LazyLoadErrorBoundaryState {
  failed: boolean;
}

function LazyLoadErrorFallback({
  mode,
  onRetry,
}: Required<Pick<LazyLoadErrorBoundaryProps, 'mode' | 'onRetry'>>) {
  const { t } = useTranslation('common');
  const containerClassName = mode === 'overlay'
    ? 'fixed inset-0 z-[120] flex items-center justify-center bg-black/30 p-6'
    : 'flex min-h-64 items-center justify-center p-6';

  return (
    <div className={containerClassName} role="alert">
      <div className="max-w-md rounded-xl border border-gray-200 bg-white p-6 text-center shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
        <h2 className="text-base font-semibold text-gray-900 dark:text-zinc-100">
          {t('error.chunkLoadFailed')}
        </h2>
        <p className="mt-2 text-sm text-gray-500 dark:text-zinc-400">
          {t('error.chunkLoadHint')}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 dark:bg-zinc-700 dark:hover:bg-zinc-600"
        >
          {t('button.retry')}
        </button>
      </div>
    </div>
  );
}

export default class LazyLoadErrorBoundary extends Component<
  LazyLoadErrorBoundaryProps,
  LazyLoadErrorBoundaryState
> {
  state: LazyLoadErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(error: Error): LazyLoadErrorBoundaryState {
    if (!isChunkLoadError(error)) throw error;
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[LazyLoadErrorBoundary] Failed to load lazy UI:', error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <LazyLoadErrorFallback
        mode={this.props.mode ?? 'page'}
        onRetry={this.props.onRetry ?? retryChunkLoad}
      />
    );
  }
}
