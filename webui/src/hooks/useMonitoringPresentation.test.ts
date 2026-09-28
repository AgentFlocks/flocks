import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message, MessagePart } from '@/types';
import { useMonitoringPresentation } from './useMonitoringPresentation';
const part = (id: string, extra = {}) => ({ id, type: 'text', text: id, ...extra } as MessagePart);
const msg = (parts: MessagePart[], extra = {}) => ({ id: 'round', role: 'assistant', parts, ...extra } as Message);
const base = { sessionId: 'today', enabled: true, ready: true, interrupted: false };
const ids = (messages: Message[]) => messages.flatMap(message => message.parts.map(item => item.id));
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-29T00:00:00Z')); Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('monitor view-only presentation', () => {
  it('renders initial history and subsequent snapshots immediately', () => {
    const initial = [msg([part('old1'), part('old2')])];
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: initial } });
    expect(result.current.messages).toEqual(initial);
    rerender({ ...base, ready: false, messages: initial });
    const snapshot = [msg([part('old1'), part('old2'), part('recovered1'), part('recovered2')])];
    rerender({ ...base, messages: snapshot });
    expect(result.current.messages).toEqual(snapshot);
    expect(result.current.pending).toBe(false);
  });
  it('paces only new parts at 450ms and preserves authoritative completion state', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const source = [msg([part('query'), part('result'), part('end')], { finish: 'stop' })];
    rerender({ ...base, messages: source });
    expect(ids(result.current.messages)).toEqual(['query']);
    expect(result.current.messages[0].finish).toBe('stop');
    expect(source[0].parts).toHaveLength(3);
    act(() => vi.advanceTimersByTime(450));
    expect(ids(result.current.messages)).toEqual(['query', 'result']);
    act(() => vi.advanceTimersByTime(450));
    expect(ids(result.current.messages)).toEqual(['query', 'result', 'end']);
    expect(result.current.pending).toBe(false);
  });
  it('caps total lag at 2 seconds even while new messages keep arriving', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    let parts = Array.from({ length: 12 }, (_, index) => part(`step-${index}`));
    rerender({ ...base, messages: [msg(parts)] });
    act(() => vi.advanceTimersByTime(900));
    parts = [...parts, part('next')];
    rerender({ ...base, messages: [msg(parts)] });
    act(() => vi.advanceTimersByTime(1100));
    expect(ids(result.current.messages)).toEqual(parts.map(item => item.id));
    expect(result.current.pending).toBe(false);
  });
  it('does not queue duplicate updates or delay a real error transition', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const parts = [part('tool', { type: 'tool', state: { status: 'running' } }), part('summary')];
    rerender({ ...base, messages: [msg(parts)] });
    rerender({ ...base, messages: [msg(parts.map(p => ({ ...p })))] });
    expect(ids(result.current.messages)).toEqual(['tool']);
    rerender({ ...base, messages: [msg([{ ...parts[0], state: { status: 'error', error: 'offline' } } as MessagePart, parts[1]])] });
    expect(ids(result.current.messages)).toEqual(['tool', 'summary']);
    expect(result.current.messages[0].parts[0].state?.error).toBe('offline');
    expect(result.current.pending).toBe(false);
  });
  it.each(['pause', 'hidden', 'skip', 'disconnect'])('flushes immediately on %s', reason => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const messages = [msg([part('query'), part('result'), part('end')])];
    rerender({ ...base, messages });
    expect(result.current.pending).toBe(true);
    if (reason === 'pause') rerender({ ...base, messages, interrupted: true });
    if (reason === 'disconnect') rerender({ ...base, messages, ready: false });
    if (reason === 'skip') act(() => result.current.skip());
    if (reason === 'hidden') act(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    expect(ids(result.current.messages)).toEqual(['query', 'result', 'end']);
    expect(result.current.pending).toBe(false);
  });
  it('honors reduced motion and leaves regular workbench messages unchanged', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const messages = [msg([part('query'), part('result')])];
    rerender({ ...base, messages });
    expect(result.current.messages).toBe(messages);
    rerender({ ...base, enabled: false, messages });
    expect(result.current.messages).toBe(messages);
  });
  it('switches date without carrying an old queue or replaying history', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    rerender({ ...base, messages: [msg([part('query'), part('result')])] });
    const yesterday = [msg([part('old1'), part('old2')])];
    rerender({ ...base, sessionId: 'yesterday', messages: yesterday });
    act(() => vi.advanceTimersByTime(3000));
    expect(result.current.messages).toEqual(yesterday);
    expect(result.current.pending).toBe(false);
  });
});


it('shows snapshot-only parts immediately without flushing the genuine live queue', () => {
  const liveKeys = new Set<string>();
  const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[], liveKeys } });
  liveKeys.add('round:query'); liveKeys.add('round:result'); liveKeys.add('round:end');
  const messages = [msg([part('query'), part('result'), part('end')])];
  rerender({ ...base, messages, liveKeys });
  expect(ids(result.current.messages)).toEqual(['query']);
  // A background completion refetch adds a saved part without replaying history.
  rerender({ ...base, messages: [msg([...messages[0].parts, part('saved')], { finish: 'stop' })], liveKeys });
  expect(ids(result.current.messages)).toEqual(['query']);
  expect(result.current.pending).toBe(true);
  act(() => vi.advanceTimersByTime(2000));
  expect(ids(result.current.messages)).toEqual(['query', 'result', 'end', 'saved']);
});
