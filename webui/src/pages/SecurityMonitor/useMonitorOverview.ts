import { useCallback, useEffect, useRef, useState } from 'react';
import { monitoringApi, type MonitorSnapshot } from '@/api/securityMonitoring';
import { getApiBase } from '@/api/client';
import { useSSE, type SSEEvent } from '@/hooks/useSSE';

const EVENT_REFRESH_DELAY = 500;

/** Generic task traffic must not reload a whole monitoring day. */
export function isMonitoringOverviewEvent(event: SSEEvent, snapshot: MonitorSnapshot | null): boolean {
  if (['monitor.execution.started', 'monitor.execution.finished', 'monitor.control.changed'].includes(event.type)) return true;
  if (event.type !== 'task.updated' || !snapshot) return false;
  const { sessionID, executionID } = event.properties || {};
  return !!((sessionID && (sessionID === snapshot.sessionID || sessionID === snapshot.currentRun?.session_id))
    || snapshot.runs.some(run => (sessionID && run.session_id === sessionID) || (executionID && run.execution_id === executionID)));
}

/** One in-flight request, with at most one pending refresh for newer facts. */
export function useMonitorOverview(day: string) {
  const [data, setData] = useState<MonitorSnapshot | null>(null);
  const [error, setError] = useState('');
  const [loadedDay, setLoadedDay] = useState<string | null>(null);
  const dataRef = useRef(data);
  const dayRef = useRef(day);
  const mounted = useRef(false);
  const revision = useRef(0);
  const suspended = useRef(false);
  const pending = useRef(false);
  const inFlight = useRef<Promise<void> | null>(null);
  const eventTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  dataRef.current = data;
  dayRef.current = day;

  const clearEventTimer = useCallback(() => {
    if (eventTimer.current !== null) clearTimeout(eventTimer.current);
    eventTimer.current = null;
  }, []);
  const request = useCallback((followUp = false): Promise<void> => {
    if (!mounted.current || suspended.current) return Promise.resolve();
    if (inFlight.current) {
      if (followUp) pending.current = true;
      return inFlight.current;
    }
    // Register the promise before invoking a potentially synchronous fetcher.
    inFlight.current = Promise.resolve().then(async () => {
      if (!mounted.current || suspended.current) return;
      do {
        pending.current = false;
        const requestedDay = dayRef.current;
        const requestedRevision = revision.current;
        try {
          const result = await monitoringApi.overview(requestedDay || undefined);
          if (mounted.current && !suspended.current && requestedRevision === revision.current && requestedDay === dayRef.current) {
            dataRef.current = result.data;
            setData(result.data);
            setLoadedDay(requestedDay);
            setError('');
          }
        } catch {
          if (mounted.current && !suspended.current && requestedRevision === revision.current && requestedDay === dayRef.current) {
            setError('数据更新失败，当前内容可能已过期。');
          }
        }
      } while (pending.current && mounted.current && !suspended.current);
    }).finally(() => { inFlight.current = null; });
    return inFlight.current;
  }, []);
  const refresh = useCallback(() => {
    const changed = eventTimer.current !== null;
    clearEventTimer();
    return request(changed);
  }, [clearEventTimer, request]);
  // Mutation callers must not reuse a pre-mutation overview response.
  const refreshAfterChange = useCallback(() => {
    revision.current++;
    clearEventTimer();
    return request(true);
  }, [clearEventTimer, request]);
  const suspendRefresh = useCallback(() => {
    suspended.current = true;
    revision.current++;
    clearEventTimer();
  }, [clearEventTimer]);
  const resumeRefresh = useCallback(() => {
    suspended.current = false;
    return refreshAfterChange();
  }, [refreshAfterChange]);
  const scheduleRefresh = useCallback(() => {
    if (eventTimer.current !== null || suspended.current) return;
    eventTimer.current = setTimeout(() => {
      eventTimer.current = null;
      void request(true);
    }, EVENT_REFRESH_DELAY);
  }, [request]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; revision.current++; pending.current = false; clearEventTimer(); };
  }, [clearEventTimer]);
  useEffect(() => {
    revision.current++;
    clearEventTimer();
    void request(true);
  }, [day, clearEventTimer, request]);
  useEffect(() => {
    const timer = setInterval(() => { void refresh(); }, 10000);
    return () => clearInterval(timer);
  }, [refresh]);
  useSSE({
    url: `${getApiBase()}/api/event`,
    onEvent: event => { if (isMonitoringOverviewEvent(event, dataRef.current)) scheduleRefresh(); },
    onReconnect: scheduleRefresh,
  });
  return { data, error, isCurrentSnapshot: !!data && loadedDay === day, refresh, refreshAfterChange, suspendRefresh, resumeRefresh };
}
