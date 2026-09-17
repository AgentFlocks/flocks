import { createContext, useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';

export type Theme = 'light' | 'dark';
/** What the user picked: a fixed theme, or follow the operating system. */
export type ThemeMode = Theme | 'system';

interface ThemeContextValue {
  /** The theme the preference resolves to (`system` resolved through the OS). */
  theme: Theme;
  /** The stored preference itself; `system` is the default. */
  mode: ThemeMode;
  /** What is on screen: a page's temporary override wins over `theme`. */
  effectiveTheme: Theme;
  toggleTheme: () => void;
  setTheme: (mode: ThemeMode) => void;
  setTemporaryThemeOverride: (theme: Theme | null) => void;
}

// A new key on purpose: the old `flocks_theme` was written on every mount, so
// it says "light" for everyone whether or not they ever chose it. Only an
// explicit choice lands here; the legacy key is dropped once one is made.
export const THEME_MODE_STORAGE_KEY = 'flocks_theme_mode';
const LEGACY_THEME_STORAGE_KEY = 'flocks_theme';
const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'light',
  mode: 'system',
  effectiveTheme: 'light',
  toggleTheme: () => undefined,
  setTheme: () => undefined,
  setTemporaryThemeOverride: () => undefined,
});

function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

function getInitialMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system';

  try {
    const storage = window.localStorage;
    const stored = typeof storage?.getItem === 'function' ? storage.getItem(THEME_MODE_STORAGE_KEY) : null;
    return isThemeMode(stored) ? stored : 'system';
  } catch {
    // Storage access itself can throw (blocked site data); fall back to the OS.
    return 'system';
  }
}

function readSystemTheme(): Theme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'light';
  return window.matchMedia(DARK_SCHEME_QUERY).matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(getInitialMode);
  const [systemTheme, setSystemTheme] = useState<Theme>(readSystemTheme);
  const [temporaryThemeOverride, setTemporaryThemeOverride] = useState<Theme | null>(null);
  const theme: Theme = mode === 'system' ? systemTheme : mode;
  const effectiveTheme = temporaryThemeOverride ?? theme;

  // Follow the OS live while in `system` mode, so flipping the appearance in
  // the OS settings restyles the console without a reload.
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia(DARK_SCHEME_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemTheme(event.matches ? 'dark' : 'light');
    setSystemTheme(query.matches ? 'dark' : 'light');
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', onChange);
      return () => query.removeEventListener('change', onChange);
    }
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  useLayoutEffect(() => {
    applyTheme(effectiveTheme);
  }, [effectiveTheme]);

  const persistMode = useCallback((nextMode: ThemeMode) => {
    if (typeof window === 'undefined') return;
    try {
      const storage = window.localStorage;
      if (typeof storage?.setItem !== 'function') return;
      storage.setItem(THEME_MODE_STORAGE_KEY, nextMode);
      storage.removeItem(LEGACY_THEME_STORAGE_KEY);
    } catch {
      // Storage can be full or blocked; the choice still applies for this session.
    }
  }, []);

  const setTheme = useCallback((nextMode: ThemeMode) => {
    setMode(nextMode);
    persistMode(nextMode);
  }, [persistMode]);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  }, [setTheme, theme]);

  const value = useMemo(
    () => ({
      theme,
      mode,
      effectiveTheme,
      toggleTheme,
      setTheme,
      setTemporaryThemeOverride,
    }),
    [effectiveTheme, mode, setTheme, theme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export { ThemeContext };
