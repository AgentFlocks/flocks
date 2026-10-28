/**
 * One request for callers that ask the same question at about the same time.
 *
 * A caller takes over another caller's request, running or answered, only
 * when it started at or after `joinAfter` (a `requestClock()` time) and after
 * the last change noted for its topic: a request sent before a change cannot
 * include it. `-Infinity` takes any, `Infinity` none. Failed requests are
 * never handed out.
 */

interface Flight {
  startedAt: number;
  promise: Promise<unknown>;
}

// Answers older than this are dropped; callers never ask for older ones.
const ANSWER_RETENTION_MS = 60_000;
const running = new Map<string, Flight>();
const answered = new Map<string, Flight>();
const changedAt = new Map<string, number>();

/** Monotonic time used for `joinAfter`, change marks and request start times. */
export function requestClock(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

/** `topic` changed at `at`: no request started earlier is handed out for it. */
export function noteChange(topic: string, at = requestClock()): void {
  changedAt.set(topic, Math.max(changedAt.get(topic) ?? Number.NEGATIVE_INFINITY, at));
}

export function shareRequest<T>(
  topic: string,
  key: string,
  joinAfter: number,
  start: () => Promise<T>,
): Promise<T> {
  const now = requestClock();
  answered.forEach((flight, answeredKey) => {
    if (flight.startedAt < now - ANSWER_RETENTION_MS) answered.delete(answeredKey);
  });
  const fullKey = `${topic}\u0000${key}`;
  const earliest = Math.max(joinAfter, changedAt.get(topic) ?? Number.NEGATIVE_INFINITY);
  for (const flight of [running.get(fullKey), answered.get(fullKey)]) {
    if (flight && flight.startedAt >= earliest) return flight.promise as Promise<T>;
  }
  const flight: Flight = { startedAt: now, promise: start() };
  running.set(fullKey, flight);
  const finish = (succeeded: boolean) => {
    if (running.get(fullKey) === flight) running.delete(fullKey);
    const last = answered.get(fullKey);
    if (succeeded && (!last || last.startedAt <= flight.startedAt)) answered.set(fullKey, flight);
  };
  flight.promise.then(() => finish(true), () => finish(false));
  return flight.promise as Promise<T>;
}

export function resetSharedRequestsForTesting(): void {
  running.clear();
  answered.clear();
  changedAt.clear();
}
