import React, { useContext } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeContext, ThemeProvider } from './ThemeContext';

function ThemeProbe() {
  const { theme, mode, effectiveTheme, toggleTheme, setTheme, setTemporaryThemeOverride } = useContext(ThemeContext);

  return (
    <div>
      <span data-testid="theme-value">{theme}</span>
      <span data-testid="mode-value">{mode}</span>
      <span data-testid="effective-theme-value">{effectiveTheme}</span>
      <button type="button" onClick={toggleTheme}>
        toggle
      </button>
      <button type="button" onClick={() => setTheme('dark')}>
        set dark
      </button>
      <button type="button" onClick={() => setTheme('light')}>
        set light
      </button>
      <button type="button" onClick={() => setTheme('system')}>
        set system
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

type SchemeListener = (event: { matches: boolean }) => void;

/** Fakes `prefers-color-scheme` and lets a test flip it like the OS would. */
function mockPreferredScheme(matchesDark: boolean) {
  const listeners = new Set<SchemeListener>();
  const state = { matchesDark };
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      get matches() {
        return query === '(prefers-color-scheme: dark)' ? state.matchesDark : false;
      },
      media: query,
      onchange: null,
      addEventListener: (_type: string, listener: SchemeListener) => listeners.add(listener),
      removeEventListener: (_type: string, listener: SchemeListener) => listeners.delete(listener),
      addListener: (listener: SchemeListener) => listeners.add(listener),
      removeListener: (listener: SchemeListener) => listeners.delete(listener),
      dispatchEvent: vi.fn(),
    })),
  });
  return {
    flip(nextMatchesDark: boolean) {
      state.matchesDark = nextMatchesDark;
      listeners.forEach((listener) => listener({ matches: nextMatchesDark }));
    },
    listenerCount: () => listeners.size,
  };
}

function renderProbe() {
  return render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>,
  );
}

describe('ThemeProvider', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.style.colorScheme = '';
    mockPreferredScheme(false);
  });

  it('follows the system appearance when nothing was chosen', () => {
    mockPreferredScheme(true);

    renderProbe();

    expect(screen.getByTestId('mode-value')).toHaveTextContent('system');
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    // Following the OS is not a choice, so nothing is written.
    expect(localStorage.getItem('flocks_theme_mode')).toBeNull();
  });

  it('ignores the legacy key that every visit used to write', () => {
    localStorage.setItem('flocks_theme', 'light');
    mockPreferredScheme(true);

    renderProbe();

    expect(screen.getByTestId('mode-value')).toHaveTextContent('system');
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
  });

  it('prefers an explicit choice over the system appearance', () => {
    localStorage.setItem('flocks_theme_mode', 'light');
    mockPreferredScheme(true);

    renderProbe();

    expect(screen.getByTestId('mode-value')).toHaveTextContent('light');
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('light');
  });

  it('restyles live when the OS appearance changes in system mode, and stops once a theme is pinned', async () => {
    const user = userEvent.setup();
    const scheme = mockPreferredScheme(false);

    renderProbe();
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(scheme.listenerCount()).toBe(1);

    act(() => scheme.flip(true));
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');

    act(() => scheme.flip(false));
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'set dark' }));
    });
    expect(screen.getByTestId('mode-value')).toHaveTextContent('dark');
    act(() => scheme.flip(false));
    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'set system' }));
    });
    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(localStorage.getItem('flocks_theme_mode')).toBe('system');
  });

  it('toggles and persists the dark class on the document root', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_theme', 'light');

    renderProbe();

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'toggle' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('dark');
    expect(screen.getByTestId('mode-value')).toHaveTextContent('dark');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    await waitFor(() => expect(localStorage.getItem('flocks_theme_mode')).toBe('dark'));
    // The legacy key goes away with the first real choice.
    expect(localStorage.getItem('flocks_theme')).toBeNull();
  });

  it('temporarily overrides the displayed theme without changing the stored preference', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_theme_mode', 'light');

    renderProbe();

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'temp dark' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem('flocks_theme_mode')).toBe('light');

    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'clear temp' }));
    });

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(localStorage.getItem('flocks_theme_mode')).toBe('light');
  });
});
