import React, { useContext } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeContext, ThemeProvider } from './ThemeContext';

const { uiConfigApi } = vi.hoisted(() => ({
  uiConfigApi: {
    getDisplay: vi.fn(),
    update: vi.fn(),
  },
}));

vi.mock('@/api/uiConfig', () => ({
  uiConfigApi,
}));

function ThemeProbe() {
  const { theme, effectiveTheme, toggleTheme, setTheme, setTemporaryThemeOverride } = useContext(ThemeContext);

  return (
    <div>
      <span data-testid="theme-value">{theme}</span>
      <span data-testid="effective-theme-value">{effectiveTheme}</span>
      <button type="button" onClick={toggleTheme}>
        toggle
      </button>
      <button type="button" onClick={() => setTheme('dark')}>
        set dark
      </button>
      <button type="button" onClick={() => setTemporaryThemeOverride('dark')}>
        temp dark
      </button>
      <button type="button" onClick={() => setTemporaryThemeOverride(null)}>
        clear temp
      </button>
    </div>
  );
}

function mockPreferredScheme(matchesDark: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)' ? matchesDark : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe('ThemeProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.style.colorScheme = '';
    mockPreferredScheme(false);
    // Default: server has no theme configured yet
    uiConfigApi.getDisplay.mockResolvedValue({ displayName: 'Flocks', theme: null });
    uiConfigApi.update.mockResolvedValue({ displayName: 'Flocks', theme: 'dark' });
  });

  it('defaults to light when no stored or system theme exists', async () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('light');
    await waitFor(() => expect(localStorage.getItem('flocks_theme')).toBe('light'));
  });

  it('defaults to dark when system prefers dark and no stored theme', async () => {
    mockPreferredScheme(true);

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    await waitFor(() => expect(localStorage.getItem('flocks_theme')).toBe('dark'));
  });

  it('prefers localStorage over system preference', async () => {
    localStorage.setItem('flocks_theme', 'light');
    mockPreferredScheme(true); // system is dark, but localStorage says light

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('light');
    await waitFor(() => expect(localStorage.getItem('flocks_theme')).toBe('light'));
  });

  it('adopts server theme when it differs from localStorage', async () => {
    localStorage.setItem('flocks_theme', 'light');
    uiConfigApi.getDisplay.mockResolvedValue({ displayName: 'Flocks', theme: 'dark' });

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    // Starts with localStorage value (light)...
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');

    // ...then server value (dark) takes over
    await waitFor(() => expect(screen.getByTestId('theme-value')).toHaveTextContent('dark'));
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    await waitFor(() => expect(localStorage.getItem('flocks_theme')).toBe('dark'));
  });

  it('keeps localStorage value when server has no theme', async () => {
    localStorage.setItem('flocks_theme', 'dark');
    uiConfigApi.getDisplay.mockResolvedValue({ displayName: 'Flocks', theme: null });

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');

    // Wait for server fetch to complete
    await waitFor(() => expect(uiConfigApi.getDisplay).toHaveBeenCalled());

    // Theme should still be dark (server had no value to override with)
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
  });

  it('toggles and persists the dark class on the document root', async () => {
    const user = userEvent.setup();

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'toggle' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    await waitFor(() => expect(localStorage.getItem('flocks_theme')).toBe('dark'));
  });

  it('persists theme to server after user toggle', async () => {
    const user = userEvent.setup();
    uiConfigApi.update.mockResolvedValue({ displayName: 'Flocks', theme: 'dark' });

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    // Wait for initial server fetch
    await waitFor(() => expect(uiConfigApi.getDisplay).toHaveBeenCalled());

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'toggle' }));
    });

    await waitFor(() => {
      expect(uiConfigApi.update).toHaveBeenCalledWith({ theme: 'dark' });
    });
  });

  it('survives localStorage being unavailable', async () => {
    const originalStorage = window.localStorage;
    // Simulate localStorage throwing on access
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: () => { throw new Error('denied'); },
        setItem: () => { throw new Error('denied'); },
      },
    });

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    // Should default to light without crashing
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    // Restore
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: originalStorage,
    });
  });

  it('survives server fetch failure', async () => {
    localStorage.setItem('flocks_theme', 'dark');
    uiConfigApi.getDisplay.mockRejectedValue(new Error('network error'));

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    // Starts with localStorage value
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');

    // Wait for failed fetch to complete
    await waitFor(() => expect(uiConfigApi.getDisplay).toHaveBeenCalled());

    // Should stay dark (localStorage fallback)
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
  });

  it('temporarily overrides the displayed theme without changing the stored preference', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_theme', 'light');

    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'temp dark' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light'); // persisted unchanged
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('dark'); // overridden
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem('flocks_theme')).toBe('light'); // localStorage unchanged

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'clear temp' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(localStorage.getItem('flocks_theme')).toBe('light');
  });
});
