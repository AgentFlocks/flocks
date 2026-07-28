import { createContext, useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';
import { uiConfigApi } from '@/api/uiConfig';

export type Theme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  effectiveTheme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
  setTemporaryThemeOverride: (theme: Theme | null) => void;
}

const THEME_STORAGE_KEY = 'flocks_theme';

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'light',
  effectiveTheme: 'light',
  toggleTheme: () => undefined,
  setTheme: () => undefined,
  setTemporaryThemeOverride: () => undefined,
});

function getInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'light';

  try {
    const stored = window.localStorage?.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;

    // Match index.html behavior: respect system preference when no stored value.
    if (
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches
    ) {
      return 'dark';
    }
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }

  return 'light';
}

function saveThemeToStorage(theme: Theme): boolean {
  try {
    if (typeof window.localStorage?.setItem === 'function') {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
      return true;
    }
  } catch {
    // Storage full, private browsing, or other restriction — non-fatal.
  }
  return false;
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [temporaryThemeOverride, setTemporaryThemeOverride] = useState<Theme | null>(null);
  const [serverThemeLoaded, setServerThemeLoaded] = useState(false);
  const effectiveTheme = temporaryThemeOverride ?? theme;

  useLayoutEffect(() => {
    applyTheme(effectiveTheme);
  }, [effectiveTheme]);

  // Persist to localStorage on every change (fast, sync — used by index.html anti-flash script).
  useEffect(() => {
    saveThemeToStorage(theme);
  }, [theme]);

  // On mount, fetch server-side theme preference (durable across browsers / origins).
  useEffect(() => {
    let cancelled = false;
    uiConfigApi
      .getDisplay()
      .then((config) => {
        if (cancelled) return;
        if (config.theme && (config.theme === 'light' || config.theme === 'dark')) {
          setThemeState(config.theme);
          saveThemeToStorage(config.theme);
        }
      })
      .catch(() => {
        // Server unavailable — localStorage value is the fallback, already applied.
      })
      .finally(() => {
        if (!cancelled) setServerThemeLoaded(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Persist to server on every change (after initial load, to avoid re-saving the fetched value).
  useEffect(() => {
    if (!serverThemeLoaded) return;
    uiConfigApi.update({ theme }).catch(() => {
      // Non-critical — localStorage already holds the latest value.
    });
  }, [theme, serverThemeLoaded]);

  const setTheme = useCallback((nextTheme: Theme) => {
    setThemeState(nextTheme);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((current) => (current === 'dark' ? 'light' : 'dark'));
  }, []);

  const value = useMemo(
    () => ({
      theme,
      effectiveTheme,
      toggleTheme,
      setTheme,
      setTemporaryThemeOverride,
    }),
    [effectiveTheme, setTheme, theme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export { ThemeContext };
