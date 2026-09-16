import { useCallback, useEffect, useRef, useState } from 'react';
import {
  claimTokenPolicy,
  confirmTokenPolicyDisplay,
  getTokenPolicyStatus,
  type TokenPolicyNotice,
  type TokenPolicyStatus,
} from '@/api/tokenPolicy';

const POLL_MS = 30_000;
interface Delivery {
  notice: TokenPolicyNotice;
  requestId: string;
  leaseDeadline: number;
  expiryDeadline: number;
  rendered: boolean;
}
type View =
  | { phase: 'checking' | 'idle'; delivery?: never }
  | { phase: 'reserved' | 'shown'; delivery: Delivery };

export function useTokenPolicyNotice(userId: string | undefined, blocked: boolean) {
  const [view, setViewState] = useState<View>({ phase: 'checking' });
  const [foreground, setForeground] = useState(document.visibilityState === 'visible');
  const viewRef = useRef(view);
  const blockedRef = useRef(blocked);
  blockedRef.current = blocked;
  const wakeRef = useRef<() => void>(() => {});
  // All state changes pass through one setter; async callbacks read the same view.
  const setView = useCallback((next: View) => {
    viewRef.current = next;
    setViewState(next);
  }, []);
  const close = useCallback(() => setView({ phase: 'idle' }), [setView]);
  const onPresented = useCallback(() => {
    const current = viewRef.current;
    if (current.phase !== 'reserved' || blockedRef.current || document.visibilityState !== 'visible') return;
    if (performance.now() >= current.delivery.leaseDeadline) {
      setView({ phase: 'checking' });
    } else {
      setView({ ...current, delivery: { ...current.delivery, rendered: true } });
    }
    wakeRef.current();
  }, [setView]);

  useEffect(() => {
    let disposed = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let periodicChecks = true;
    const controller = new AbortController();
    setView({ phase: userId ? 'checking' : 'idle' });
    if (!userId) return;

    const canDisplay = () => !disposed && !blockedRef.current && document.visibilityState === 'visible';
    const schedule = (status?: TokenPolicyStatus, requestedAt = performance.now()) => {
      clearTimeout(timer);
      if (status?.state === 'finished' || status?.state === 'disabled') periodicChecks = false;
      if (disposed || !periodicChecks) return;
      let delay = POLL_MS;
      if (status) {
        for (const time of [status.next_check_at, status.lease_expires_at]) {
          if (time)
            delay = Math.min(
              delay,
              Math.max(
                100,
                Date.parse(time) - Date.parse(status.server_now) - (performance.now() - requestedAt),
              ),
            );
        }
      }
      const current = viewRef.current;
      if (current.delivery) {
        delay = Math.min(delay, Math.max(100, current.delivery.expiryDeadline - performance.now()));
        if (current.phase === 'reserved') {
          delay = Math.min(delay, Math.max(100, current.delivery.leaseDeadline - performance.now()));
          if (current.delivery.rendered) delay = Math.min(delay, 1000);
        }
      }
      timer = setTimeout(() => void check(), delay);
    };

    const check = async () => {
      if (disposed || running) return;
      let current = viewRef.current;
      if (
        current.delivery &&
        (performance.now() >= current.delivery.expiryDeadline ||
          (current.phase === 'reserved' && performance.now() >= current.delivery.leaseDeadline))
      ) {
        setView({ phase: 'checking' });
        current = viewRef.current;
      }
      if (document.visibilityState !== 'visible') {
        schedule();
        return;
      }
      running = true;
      let status: TokenPolicyStatus | undefined;
      let requestedAt = performance.now();
      try {
        if (current.phase === 'reserved') {
          if (current.delivery.rendered && canDisplay()) {
            const delivery = current.delivery;
            const confirmed = await confirmTokenPolicyDisplay(
              delivery.notice.occurrence_id,
              delivery.requestId,
              controller.signal,
            );
            if (!disposed && viewRef.current.delivery === delivery) {
              setView(confirmed ? { phase: 'shown', delivery } : { phase: 'checking' });
            }
          }
          return;
        }
        status = await getTokenPolicyStatus(controller.signal);
        if (disposed) return;
        periodicChecks = status.state !== 'finished' && status.state !== 'disabled';
        if (!periodicChecks) {
          close();
          return;
        }
        if (viewRef.current.phase === 'shown') return;
        setView({ phase: status.notice || status.waiting_for_display ? 'checking' : 'idle' });
        if (status.notice && canDisplay()) {
          const bytes = crypto.getRandomValues(new Uint8Array(16));
          const requestId = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
          requestedAt = performance.now();
          status = await claimTokenPolicy(status.notice.occurrence_id, requestId, controller.signal);
          if (disposed) return;
          if (status.notice && status.lease_expires_at && canDisplay()) {
            // Count network time against the lease, never extend ownership
            // beyond the server deadline because a response arrived late.
            const deadline = (time: string) =>
              requestedAt + Date.parse(time) - Date.parse(status!.server_now);
            const leaseDeadline = deadline(status.lease_expires_at);
            setView(
              leaseDeadline <= performance.now()
                ? { phase: 'checking' }
                : {
                    phase: 'reserved',
                    delivery: {
                      notice: status.notice,
                      requestId,
                      rendered: false,
                      leaseDeadline,
                      expiryDeadline: deadline(status.notice.expires_at),
                    },
                  },
            );
          } else {
            // Hidden/blocked during the request: leave the unconfirmed lease
            // to expire. Neither this tab nor another records it as displayed.
            setView({ phase: status.waiting_for_display || status.lease_expires_at ? 'checking' : 'idle' });
          }
        }
      } catch {
        // API timeouts release the upgrade gate; reservations remain recoverable.
        if (!disposed && !viewRef.current.delivery) setView({ phase: 'idle' });
      } finally {
        running = false;
        if (!disposed) schedule(status, requestedAt);
      }
    };
    const wake = () => void check();
    const visibilityChanged = () => {
      const visible = document.visibilityState === 'visible';
      setForeground(visible);
      const current = viewRef.current;
      if (!current.delivery || (current.phase === 'reserved' && !current.delivery.rendered)) {
        setView({ phase: 'checking' });
      }
      wake();
    };
    wakeRef.current = wake;
    wake();
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
      window.removeEventListener('focus', wake);
      window.removeEventListener('online', wake);
      document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, [userId, setView, close]);

  useEffect(() => {
    if (!blocked) wakeRef.current();
  }, [blocked]);
  return {
    notice: foreground ? (view.delivery?.notice ?? null) : null,
    ready: foreground && view.phase !== 'checking',
    close,
    onPresented,
  };
}
