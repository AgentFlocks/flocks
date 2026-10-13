import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Message, MessagePart } from '@/types';
import { useMonitoringMessageMerge } from './useMonitoringMessageMerge';

const message = (id: string, extra: Partial<Message> = {}): Message => ({
  id, role: 'assistant', parts: [{ id: `${id}-part`, type: 'text', text: id } as MessagePart], ...extra,
} as Message);

const ids = (messages: Message[]) => messages.map(item => item.id);

describe('incremental monitoring assistant merge', () => {
  it('preserves user messages and merges assistant parts in order using the final live finish state', () => {
    const user = message('user', { role: 'user' });
    const first = message('first', { finish: 'stop' });
    const second = message('second', { finish: undefined });
    const { result } = renderHook(() => useMonitoringMessageMerge([user, first, second]));
    expect(result.current[0]).toBe(user);
    expect(ids(result.current)).toEqual(['user', 'first']);
    expect(result.current[1]._merged).toBe(true);
    expect(result.current[1]._sourceMessageIds).toEqual(['first', 'second']);
    expect(result.current[1].parts).toEqual([...first.parts, ...second.parts]);
    expect(result.current[1].finish).toBeUndefined();
    expect(first.finish).toBe('stop');
    expect(first.parts).toHaveLength(1);
  });

  it('respects summary, role and compaction boundaries exactly', () => {
    const source = [
      message('first'), message('second'),
      message('summary', { finish: 'summary' }),
      message('after'),
      message('compacted1', { compacted: true } as unknown as Partial<Message>),
      message('compacted2', { compacted: true } as unknown as Partial<Message>),
      message('user', { role: 'user' }), message('last'),
    ];
    const { result } = renderHook(() => useMonitoringMessageMerge(source));
    expect(ids(result.current)).toEqual(['first', 'summary', 'after', 'compacted1', 'user', 'last']);
    expect(result.current[1]._merged).toBe(false);
    expect(result.current[1]._sourceMessageIds).toBeUndefined();
    expect(result.current[3]._sourceMessageIds).toEqual(['compacted1', 'compacted2']);
    expect(result.current[4]).toBe(source[6]);
  });

  it('does not read historical parts again when a live group updates or older pages are prepended', () => {
    let historicalReads = 0;
    const source = Array.from({ length: 100 }, (_, index) => {
      const user = message(`user-${index}`, { role: 'user' });
      const assistant = message(`assistant-${index}`);
      const parts = assistant.parts;
      Object.defineProperty(assistant, 'parts', { get: () => { historicalReads += 1; return parts; } });
      return [user, assistant];
    }).flat();
    const liveUser = message('live-user', { role: 'user' });
    const live = message('live');
    const { result, rerender } = renderHook(({ messages }) => useMonitoringMessageMerge(messages), { initialProps: { messages: [...source, liveUser, live] } });
    const historicalGroups = result.current.slice(0, source.length);
    historicalReads = 0;
    const updatedLive = { ...live, parts: [...live.parts, ...message('next').parts] };
    rerender({ messages: [...source, liveUser, updatedLive] });
    expect(historicalReads).toBe(0);
    historicalGroups.forEach((group, index) => {
      expect(result.current[index]).toBe(group);
      expect(result.current[index].parts).toBe(group.parts);
    });
    historicalReads = 0;
    const oldPage = [message('old-user', { role: 'user' }), message('old-answer')];
    rerender({ messages: [...oldPage, ...source, liveUser, updatedLive] });
    expect(historicalReads).toBe(0);
    historicalGroups.forEach((group, index) => expect(result.current[index + 2]).toBe(group));
  });

  it('reuses the complete display array when only the outer source array was recreated', () => {
    const source = [message('user', { role: 'user' }), message('one'), message('two')];
    const { result, rerender } = renderHook(({ messages }) => useMonitoringMessageMerge(messages), { initialProps: { messages: source } });
    const previous = result.current;
    rerender({ messages: [...source] });
    expect(result.current).toBe(previous);
  });

  it('rebuilds affected groups after an error update, append, removal or boundary change', () => {
    const first = message('one');
    const second = message('two');
    const { result, rerender } = renderHook(({ messages }) => useMonitoringMessageMerge(messages), { initialProps: { messages: [first, second] } });
    const original = result.current[0];
    const updated = { ...first, parts: [{ ...first.parts[0], state: { status: 'error', error: 'offline' } } as MessagePart] };
    rerender({ messages: [updated, second] });
    expect(result.current[0]).not.toBe(original);
    expect(result.current[0].parts[0].state?.status).toBe('error');
    const third = message('three', { finish: 'stop' });
    rerender({ messages: [updated, second, third] });
    expect(result.current[0]._sourceMessageIds).toEqual(['one', 'two', 'three']);
    expect(result.current[0].finish).toBe('stop');
    rerender({ messages: [updated, { ...second, finish: 'summary' }] });
    expect(ids(result.current)).toEqual(['one', 'two']);
    expect(result.current[0]._sourceMessageIds).toEqual(['one']);
    expect(result.current[1]._merged).toBe(false);
  });

  it('bypasses merge work when disabled and never mutates frozen source data', () => {
    const source = [message('one'), message('two')];
    source.forEach(item => { Object.freeze(item.parts); Object.freeze(item); });
    const { result, rerender } = renderHook(({ enabled }) => useMonitoringMessageMerge(source, enabled), { initialProps: { enabled: false } });
    expect(result.current).toBe(source);
    rerender({ enabled: true });
    expect(result.current[0].parts).toHaveLength(2);
    expect(source[0].parts).toHaveLength(1);
    rerender({ enabled: false });
    expect(result.current).toBe(source);
  });
});
