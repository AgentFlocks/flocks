import { Component, type ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import LazyLoadErrorBoundary from './LazyLoadErrorBoundary';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

function BrokenContent({ error }: { error: Error }): never {
  throw error;
}

class CapturingBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) return <div data-testid="outer-error">{this.state.error.message}</div>;
    return this.props.children;
  }
}

describe('LazyLoadErrorBoundary', () => {
  it('shows a recoverable fallback when lazy content fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const onRetry = vi.fn();
    const user = userEvent.setup();
    const error = new Error('Loading chunk route failed');
    error.name = 'ChunkLoadError';

    render(
      <LazyLoadErrorBoundary onRetry={onRetry}>
        <BrokenContent error={error} />
      </LazyLoadErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('error.chunkLoadFailed');
    await user.click(screen.getByRole('button', { name: 'button.retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('propagates ordinary render errors to the surrounding error boundary', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(
      <CapturingBoundary>
        <LazyLoadErrorBoundary>
          <BrokenContent error={new Error('ordinary render failure')} />
        </LazyLoadErrorBoundary>
      </CapturingBoundary>,
    );

    expect(screen.getByTestId('outer-error')).toHaveTextContent('ordinary render failure');
    expect(screen.queryByText('error.chunkLoadFailed')).not.toBeInTheDocument();
  });
});
