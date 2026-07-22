import React, { useContext } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ThemeContext, ThemeProvider } from './ThemeContext';

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
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.style.colorScheme = '';
    mockPreferredScheme(false);
  });

  it('defaults to light when no stored theme exists', async () => {
    mockPreferredScheme(true);

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

  it('prefers the stored theme over system preference', async () => {
    localStorage.setItem('flocks_theme', 'light');
    mockPreferredScheme(true);

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

    expect(screen.getByTestId('theme-value')).toHaveTextContent('light');
    expect(screen.getByTestId('effective-theme-value')).toHaveTextContent('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem('flocks_theme')).toBe('light');

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
