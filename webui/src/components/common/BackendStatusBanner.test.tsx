import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BackendStatusBanner } from './BackendStatusBanner';

const { useBackendStatusMock } = vi.hoisted(() => ({
  useBackendStatusMock: vi.fn(),
}));

vi.mock('@/hooks/useBackendStatus', () => ({
  useBackendStatus: useBackendStatusMock,
}));

function banners(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-backend-status-banner]'));
}

function classes(element: HTMLElement): string[] {
  return element.className.split(/\s+/);
}

function disconnected() {
  useBackendStatusMock.mockReturnValue({ status: 'disconnected', checked: true, checkHealth: vi.fn() });
}

describe('BackendStatusBanner', () => {
  beforeEach(() => {
    useBackendStatusMock.mockReset();
  });

  it('is fixed to the top of the window when no layout is mounted', () => {
    disconnected();

    render(<BackendStatusBanner />);

    const [banner] = banners();
    expect(banner).toHaveAttribute('data-backend-status-banner', 'window');
    expect(classes(banner)).toEqual(expect.arrayContaining(['fixed', 'top-0']));
    expect(screen.getByText('backend.disconnected')).toBeInTheDocument();
  });

  it('sits in the page flow when the layout renders it under its header', () => {
    disconnected();

    render(<BackendStatusBanner inline />);

    const [banner] = banners();
    expect(banner).toHaveAttribute('data-backend-status-banner', 'inline');
    expect(classes(banner)).not.toContain('fixed');
    expect(classes(banner).some((name) => name.startsWith('z-'))).toBe(false);
  });

  it('leaves the top to the inline banner while the layout is mounted and comes back after', () => {
    disconnected();

    const { rerender } = render(
      <>
        <BackendStatusBanner />
        <BackendStatusBanner inline />
      </>,
    );

    expect(banners()).toHaveLength(1);
    expect(banners()[0]).toHaveAttribute('data-backend-status-banner', 'inline');

    rerender(
      <>
        <BackendStatusBanner />
      </>,
    );

    expect(banners()).toHaveLength(1);
    expect(banners()[0]).toHaveAttribute('data-backend-status-banner', 'window');
  });

  it.each(['connecting', 'disconnected', 'error'] as const)(
    'uses an opaque dark background while %s so what is behind it does not show through',
    (status) => {
      useBackendStatusMock.mockReturnValue({ status, checked: true, checkHealth: vi.fn() });

      render(<BackendStatusBanner />);

      const darkBackgrounds = classes(banners()[0]).filter((name) => name.startsWith('dark:bg-'));
      expect(darkBackgrounds.length).toBeGreaterThan(0);
      for (const name of darkBackgrounds) {
        expect(name).not.toMatch(/\/\d+$/);
      }
    },
  );

  it('stays hidden until the first health check answers', () => {
    useBackendStatusMock.mockReturnValue({ status: 'connecting', checked: false, checkHealth: vi.fn() });

    render(
      <>
        <BackendStatusBanner />
        <BackendStatusBanner inline />
      </>,
    );

    expect(banners()).toHaveLength(0);
  });

  it('shows the connecting state once a check has answered with it', () => {
    useBackendStatusMock.mockReturnValue({ status: 'connecting', checked: true, checkHealth: vi.fn() });

    render(<BackendStatusBanner />);

    expect(screen.getByText('backend.connecting')).toBeInTheDocument();
    expect(screen.getByText('backend.retry')).toBeInTheDocument();
  });

  it('renders nothing while connected', () => {
    useBackendStatusMock.mockReturnValue({ status: 'connected', checked: true, checkHealth: vi.fn() });

    render(<BackendStatusBanner inline />);

    expect(banners()).toHaveLength(0);
  });
});
