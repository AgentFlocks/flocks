import { useEffect, useState } from 'react';
import { createInitialState, parseStoredState, STORAGE_KEY } from './model';

export function usePrototypeState() {
  const [initial] = useState(() => {
    try {
      return {
        ...parseStoredState(localStorage.getItem(STORAGE_KEY)),
        unavailable: false,
      };
    } catch {
      return {
        state: createInitialState(),
        recovered: false,
        unavailable: true,
      };
    }
  });
  const [state, setState] = useState(initial.state);
  const [storageUnavailable, setStorageUnavailable] = useState(
    initial.unavailable,
  );
  const [recovered, setRecovered] = useState(initial.recovered);

  useEffect(() => {
    // Do not overwrite records we could not read; keep this session in memory.
    if (initial.unavailable) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      setStorageUnavailable(false);
    } catch {
      setStorageUnavailable(true);
    }
  }, [state, initial.unavailable]);

  return {
    state,
    setState,
    storageUnavailable,
    recovered,
    dismissRecovery: () => setRecovered(false),
  };
}
