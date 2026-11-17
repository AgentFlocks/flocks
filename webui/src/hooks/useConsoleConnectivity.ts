import { useCallback, useEffect, useRef, useState } from 'react';
import { authApi, type ConsoleConnectivity } from '@/api/auth';

/**
 * How a licence is had from here: online (a cloud account, an application Console reviews, a sync) when
 * Console answers, offline (an application file or request code handed over, a licence file imported)
 * when it does not.
 */
export type LicenceMode = 'online' | 'offline';

export interface ConsoleConnectivityState {
  /** What the server said; null while the first check runs, or when it could not say (an older server). */
  connectivity: ConsoleConnectivity | null;
  checking: boolean;
  /** The way a licence page offers: online unless Console was found out of reach, as before the check existed. */
  mode: LicenceMode;
  recheck: () => void;
}

/** Whether Console answers from this install, asked when the page (or dialog) opens and on recheck. */
export function useConsoleConnectivity(): ConsoleConnectivityState {
  const [connectivity, setConnectivity] = useState<ConsoleConnectivity | null>(null);
  const [checking, setChecking] = useState(true);
  const mounted = useRef(true);

  const check = useCallback(async (refresh: boolean) => {
    setChecking(true);
    try {
      const value = await authApi.consoleConnectivity(refresh);
      if (mounted.current) setConnectivity(value);
    } catch {
      // No answer to the question itself says nothing of Console: the page stays as it was.
      if (mounted.current) setConnectivity(null);
    } finally {
      if (mounted.current) setChecking(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void check(false);
    return () => {
      mounted.current = false;
    };
  }, [check]);

  const offline = connectivity !== null && (!connectivity.configured || !connectivity.online);
  return { connectivity, checking, mode: offline ? 'offline' : 'online', recheck: () => void check(true) };
}
