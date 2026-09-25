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
    ? 'fixed inset-0 z-[120] flex items-center justify-center bg-overlay-backdrop p-6'
    : 'flex min-h-64 items-center justify-center p-6';

  return (
    <div className={containerClassName} role="alert">
      <div className="max-w-md rounded-xl border border-line bg-surface p-6 text-center shadow-lg dark:border-line dark:bg-surface">
        <h2 className="text-base font-semibold text-fg dark:text-fg">
          {t('error.chunkLoadFailed')}
        </h2>
        <p className="mt-2 text-sm text-fg-muted dark:text-fg-muted">
          {t('error.chunkLoadHint')}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="ui-btn ui-btn-primary mt-4"
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
