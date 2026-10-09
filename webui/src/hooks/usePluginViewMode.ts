import { useCallback, useState } from 'react';

export type PluginViewMode = 'cards' | 'list';
const storageKey = (key: string) => `flocks:plugin-view:${key}`;

function readMode(key: string, fallback: PluginViewMode): PluginViewMode {
  try {
    const value = window.localStorage.getItem(storageKey(key));
    return value === 'cards' || value === 'list' ? value : fallback;
  } catch {
    return fallback;
  }
}

/** Browser-only presentation preference. Never stores group definitions or membership. */
export function usePluginViewMode(key: string, defaultMode: PluginViewMode): readonly [PluginViewMode, (mode: PluginViewMode) => void] {
  const [state, setState] = useState(() => ({ key, mode: readMode(key, defaultMode) }));
  const mode = state.key === key ? state.mode : readMode(key, defaultMode);
  // Reset during render, not in a persistence effect that could copy an old tab's mode.
  if (state.key !== key) setState({ key, mode });

  const setMode = useCallback((next: PluginViewMode) => {
    setState({ key, mode: next });
    try {
      window.localStorage.setItem(storageKey(key), next);
    } catch {
      // Private browsing / storage quotas must not prevent changing the view.
    }
  }, [key]);

  return [mode, setMode] as const;
}
