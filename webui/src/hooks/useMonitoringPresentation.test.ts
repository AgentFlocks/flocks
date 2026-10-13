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

describe('incremental monitoring presentation', () => {
  it('never rescans unchanged historical parts during SSE updates, animation ticks or skip', () => {
    let historyReads = 0;
    const history = Array.from({ length: 120 }, (_, messageIndex) => msg(
      Array.from({ length: 24 }, (_, partIndex) => {
        const item = part(`history-${messageIndex}-${partIndex}`);
        Object.defineProperty(item, 'id', { get: () => { historyReads += 1; return `history-${messageIndex}-${partIndex}`; } });
        return item;
      }), { id: `history-${messageIndex}` },
    ));
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: history } });
    expect(result.current.messages).toBe(history);
    historyReads = 0;
    const live = msg([part('query'), part('lookup'), part('result'), part('summary')]);
    rerender({ ...base, messages: [...history, live] });
    expect(historyReads).toBe(0);
    history.forEach((message, index) => expect(result.current.messages[index]).toBe(message));
    act(() => vi.advanceTimersByTime(450));
    expect(historyReads).toBe(0);
    history.forEach((message, index) => expect(result.current.messages[index]).toBe(message));
    rerender({ ...base, messages: [...history, { ...live, parts: [...live.parts, part('end')] }] });
    expect(historyReads).toBe(0);
    act(() => result.current.skip());
    expect(historyReads).toBe(0);
    expect(result.current.pending).toBe(false);
  });

  it('prepends older pages immediately without replaying them or losing the live queue', () => {
    const recent = msg([part('recent')], { id: 'recent' });
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [recent] } });
    const live = msg([part('query'), part('result'), part('end')]);
    rerender({ ...base, messages: [recent, live] });
    expect(ids(result.current.messages)).toEqual(['recent', 'query']);
    const older = msg([part('older1'), part('older2')], { id: 'older' });
    const source = [older, recent, live];
    rerender({ ...base, messages: source });
    expect(ids(result.current.messages)).toEqual(['older1', 'older2', 'recent', 'query']);
    expect(result.current.messages[0]).toBe(older);
    expect(result.current.messages[1]).toBe(recent);
    expect(result.current.pending).toBe(true);
    act(() => vi.advanceTimersByTime(450));
    act(() => vi.advanceTimersByTime(450));
    expect(result.current.messages).toBe(source);
    expect(result.current.pending).toBe(false);
  });

  it('does not replay prepended history even when its keys were live earlier', () => {
    const liveKeys = new Set(['old:old1', 'old:old2']);
    const recent = msg([part('recent')], { id: 'recent' });
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [recent], liveKeys } });
    const history = msg([part('old1'), part('old2')], { id: 'old' });
    const source = [history, recent];
    rerender({ ...base, messages: source, liveKeys });
    expect(result.current.messages).toBe(source);
    expect(result.current.pending).toBe(false);
  });

  it('keeps a partial message reference when an update only changes a later queued message', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const first = msg([part('query'), part('result')]);
    const later = msg([part('end')], { id: 'later' });
    rerender({ ...base, messages: [first, later] });
    const presented = result.current.messages[0];
    rerender({ ...base, messages: [first, { ...later, parts: [part('end', { text: 'Updated summary' })] }] });
    expect(result.current.messages[0]).toBe(presented);
    act(() => vi.advanceTimersByTime(450));
    act(() => vi.advanceTimersByTime(450));
    expect(result.current.messages[1].parts[0].text).toBe('Updated summary');
  });

  it('removes queued parts that disappear during reconciliation and releases later results', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const first = msg([part('query'), part('discarded')]);
    const later = msg([part('result')], { id: 'later' });
    rerender({ ...base, messages: [first, later] });
    const source = [msg([first.parts[0]]), later];
    rerender({ ...base, messages: source });
    act(() => vi.advanceTimersByTime(450));
    expect(result.current.messages).toBe(source);
    expect(ids(result.current.messages)).toEqual(['query', 'result']);
  });

  it('detects legacy in-place appends while leaving the source and historical messages unchanged', () => {
    const source = msg([part('query')]);
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [source] } });
    source.parts.push(part('lookup'), part('result'), part('end'));
    rerender({ ...base, messages: [source] });
    expect(ids(result.current.messages)).toEqual(['query', 'lookup']);
    expect(source.parts).toHaveLength(4);
    act(() => vi.advanceTimersByTime(450));
    act(() => vi.advanceTimersByTime(450));
    expect(result.current.messages[0]).toBe(source);
  });

  it.each(['request_user_input', 'permission'])('immediately exposes %s and all preceding steps', tool => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const parts = [part('query'), part('result')];
    rerender({ ...base, messages: [msg(parts)] });
    const source = [msg([...parts, part('approval', { type: 'tool', tool })])];
    rerender({ ...base, messages: source });
    expect(result.current.messages).toBe(source);
    expect(result.current.pending).toBe(false);
  });

  it('flushes a burst beyond the 32 part queue instead of building an unbounded backlog', () => {
    const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
    const source = [msg(Array.from({ length: 40 }, (_, index) => part(`step-${index}`)))];
    Object.freeze(source[0].parts);
    Object.freeze(source[0]);
    rerender({ ...base, messages: source });
    expect(result.current.messages).toBe(source);
    expect(result.current.pending).toBe(false);
  });
});

it('follows authoritative source order when reconciliation reorders queued parts', () => {
  const { result, rerender } = renderHook(props => useMonitoringPresentation(props), { initialProps: { ...base, messages: [] as Message[] } });
  const query = part('query'); const first = part('first'); const second = part('second');
  rerender({ ...base, messages: [msg([query, first, second])] });
  rerender({ ...base, messages: [msg([query, second, first])] });
  expect(ids(result.current.messages)).toEqual(['query']);
  act(() => vi.advanceTimersByTime(450));
  expect(ids(result.current.messages)).toEqual(['query', 'second']);
  act(() => vi.advanceTimersByTime(450));
  expect(ids(result.current.messages)).toEqual(['query', 'second', 'first']);
});
