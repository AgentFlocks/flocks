import { createContext, useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';

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

  const storage = window.localStorage;
  const stored = typeof storage?.getItem === 'function' ? storage.getItem(THEME_STORAGE_KEY) : null;
  if (stored === 'light' || stored === 'dark') return stored;

  return 'light';
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);
  const [temporaryThemeOverride, setTemporaryThemeOverride] = useState<Theme | null>(null);
  const effectiveTheme = temporaryThemeOverride ?? theme;

  useLayoutEffect(() => {
    applyTheme(effectiveTheme);
  }, [effectiveTheme]);

  useEffect(() => {
    if (typeof window.localStorage?.setItem === 'function') {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    }
  }, [theme]);

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
