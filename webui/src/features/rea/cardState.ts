import { useCallback, useLayoutEffect, useRef, useSyncExternalStore, type RefObject } from 'react';

/*
 * UI state of an REA card that has to outlive its component instance.
 *
 * SessionChat renders the reply that is still running in a separate list and
 * moves it into the history list when it finishes, which re-mounts every part
 * in it. Rex usually finishes its sentence right after the card appears, so a
 * user who already clicked (a switched view, an upload in flight, the focused
 * next action) would otherwise see the card snap back. State lives here, keyed
 * by account, session and tool part id (``reaCardKey``), which stay the same
 * across that move. When the account changes, the previous account's entries
 * are dropped and its late callbacks can no longer write.
 */

type Listener = () => void;

const FOCUS_RESTORE_MS = 2_000;
const SEPARATOR = '\u0000';
const values = new Map<string, unknown>();
const listeners = new Map<string, Set<Listener>>();
const refs = new Map<string, { current: unknown }>();
const focusMemory = new Map<string, { label: string; at: number }>();
let activeIdentity: string | null = null;

export function reaCardKey(identity: string, sessionId: string | null, partId: string): string {
  return [identity, sessionId || '', partId].join(SEPARATOR);
}

/** True while the card key belongs to the account currently signed in. */
export function isActiveReaCard(cardKey: string): boolean {
  return activeIdentity === null || cardKey.startsWith(`${activeIdentity}${SEPARATOR}`);
}

/** Switch the signed-in account: forget every other account's card state. */
export function setReaCardIdentity(identity: string): void {
  if (activeIdentity === identity) return;
  activeIdentity = identity;
  const prefix = `${identity}${SEPARATOR}`;
  for (const store of [values, refs, focusMemory] as Array<Map<string, unknown>>) {
    for (const key of Array.from(store.keys())) {
      if (!key.startsWith(prefix)) store.delete(key);
    }
  }
}

function slotOf(cardKey: string, field: string): string {
  return `${cardKey}${SEPARATOR}${field}`;
}

function subscribe(slot: string, listener: Listener): () => void {
  let set = listeners.get(slot);
  if (!set) {
    set = new Set();
    listeners.set(slot, set);
  }
  set.add(listener);
  return () => {
    set?.delete(listener);
    if (set && set.size === 0) listeners.delete(slot);
  };
}

/** ``useState`` whose value is shared by every mount of the same card. */
export function useCardState<T>(
  cardKey: string,
  field: string,
  initial: T,
): [T, (next: T | ((previous: T) => T)) => void] {
  const slot = slotOf(cardKey, field);
  const initialRef = useRef(initial);
  const read = useCallback(
    () => (values.has(slot) ? (values.get(slot) as T) : initialRef.current),
    [slot],
  );
  const listen = useCallback((listener: Listener) => subscribe(slot, listener), [slot]);
  const value = useSyncExternalStore(listen, read, read);
  // Writes go to the store, not to one instance, so an async action that
  // finishes after its card re-mounted still updates the new instance.
  const update = useCallback((next: T | ((previous: T) => T)) => {
    // A late callback of a signed-out account must not write into the store.
    if (!isActiveReaCard(cardKey)) return;
    const previous = values.has(slot) ? (values.get(slot) as T) : initialRef.current;
    const resolved = typeof next === 'function' ? (next as (previous: T) => T)(previous) : next;
    if (values.has(slot) && Object.is(previous, resolved)) return;
    values.set(slot, resolved);
    listeners.get(slot)?.forEach((listener) => listener());
  }, [cardKey, slot]);
  return [value, update];
}

/** ``useRef`` shared by every mount of the same card. */
export function useCardRef<T>(cardKey: string, field: string, initial: T): { current: T } {
  const slot = slotOf(cardKey, field);
  let box = refs.get(slot) as { current: T } | undefined;
  if (!box) {
    box = { current: initial };
    refs.set(slot, box as { current: unknown });
  }
  return box;
}

function focusLabel(element: Element): string {
  return `${element.tagName}:${element.getAttribute('aria-label') || (element.textContent || '').trim()}`;
}

/**
 * Hand focus back to the same control when the card re-mounts right after it
 * was unmounted with focus inside (and nothing else took focus meanwhile).
 */
export function useKeepFocusAcrossRemount(cardKey: string, rootRef: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const root = rootRef.current;
    const saved = focusMemory.get(cardKey);
    focusMemory.delete(cardKey);
    if (root && saved && Date.now() - saved.at < FOCUS_RESTORE_MS) {
      const active = document.activeElement;
      if (!active || active === document.body) {
        const target = Array.from(root.querySelectorAll<HTMLElement>('button, [tabindex]'))
          .find((element) => focusLabel(element) === saved.label);
        target?.focus({ preventScroll: true });
      }
    }
    return () => {
      const active = document.activeElement;
      if (root && active && active !== document.body && root.contains(active) && isActiveReaCard(cardKey)) {
        focusMemory.set(cardKey, { label: focusLabel(active), at: Date.now() });
      }
    };
  }, [cardKey, rootRef]);
}

export function __resetReaCardStateForTesting(): void {
  activeIdentity = null;
  values.clear();
  listeners.clear();
  refs.clear();
  focusMemory.clear();
}
