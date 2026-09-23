import { getApiBase } from '@/api/client';
import { useCallback, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useSSE } from './useSSE';
import { monitoringApi } from '@/api/securityMonitoring';

export function shouldNavigateMonitor(sequence: number, seen: number): boolean {
  return Number.isSafeInteger(sequence) && sequence > seen;
}

export function useMonitorNavigation(ownerID: string | undefined) {
  const ownerRef = useRef(ownerID);
  ownerRef.current = ownerID;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const navigate = useNavigate();
  const location = useLocation();
  const locationRef = useRef(location);
  locationRef.current = location;
  const seen = useRef<Record<string, number>>({});
  const refresh = useCallback(async () => {
    try {
      // SSE is an invalidation signal only; the authorized snapshot decides.
      const { data } = await monitoringApi.state();
      if (!mounted.current || !ownerRef.current || data.owner !== ownerRef.current) return;
      const latest = data.latest;
      if (!latest) return;
      const key = `flocks:monitor-nav:${data.owner}`;
      const previous = Math.max(seen.current[key] || 0, Number(sessionStorage.getItem(key)) || 0);
      if (!shouldNavigateMonitor(latest.sequence, previous)) return;
      seen.current[key] = latest.sequence;
      sessionStorage.setItem(key, String(latest.sequence));
      const current = locationRef.current;
      const currentSession = new URLSearchParams(current.search).get('session')
        || /^\/sessions\/([^/]+)\/?$/.exec(current.pathname)?.[1];
      if (currentSession && decodeURIComponent(currentSession) === latest.session_id) return;
      navigate(`/sessions?session=${encodeURIComponent(latest.session_id)}`, { replace: true });
    } catch { /* Connection/auth errors cannot trigger navigation. Poll repairs missed events. */ }
  }, [navigate]);
  useSSE({ url: `${getApiBase()}/api/event`, onEvent: event => { if (event.type === 'monitor.execution.started') void refresh(); }, onReconnect: () => void refresh() });
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);
}
