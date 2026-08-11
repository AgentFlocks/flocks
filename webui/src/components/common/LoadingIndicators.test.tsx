import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LoadingSpinner from './LoadingSpinner';
import RoutePageSkeleton from './RoutePageSkeleton';

describe('delayed loading indicators', () => {
  it('hides short spinner flashes until the delay elapses', async () => {
    vi.useFakeTimers();

    render(<LoadingSpinner delayMs={180} />);

    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(179);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole('status')).toBeInTheDocument();

    vi.useRealTimers();
  });

  it('hides short route skeleton flashes until the delay elapses', async () => {
    vi.useFakeTimers();

    render(<RoutePageSkeleton delayMs={180} />);

    expect(screen.queryByTestId('route-page-skeleton')).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(180);
    });
    expect(screen.getByTestId('route-page-skeleton')).toBeInTheDocument();

    vi.useRealTimers();
  });
});
