import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { usePluginViewMode } from './usePluginViewMode';

beforeEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

describe('usePluginViewMode', () => {
  it('defaults to the page native layout and stores only a user-requested view change', () => {
    const { result } = renderHook(() => usePluginViewMode('agent', 'cards'));
    expect(result.current[0]).toBe('cards');
    expect(window.localStorage.length).toBe(0);
    act(() => result.current[1]('list'));
    expect(result.current[0]).toBe('list');
    expect(window.localStorage.getItem('flocks:plugin-view:agent')).toBe('list');
    expect(window.localStorage.length).toBe(1);
  });

  it('restores valid view preferences and ignores untrusted stored values', () => {
    window.localStorage.setItem('flocks:plugin-view:skill', 'cards');
    const { result, rerender } = renderHook(({ key }) => usePluginViewMode(key, 'list'), { initialProps: { key: 'skill' } });
    expect(result.current[0]).toBe('cards');
    window.localStorage.setItem('flocks:plugin-view:tool-all', '{"groups": ["not-a-view"]}');
    rerender({ key: 'tool-all' });
    expect(result.current[0]).toBe('list');
  });

  it('never writes an old tab mode into a new key on key changes', () => {
    window.localStorage.setItem('flocks:plugin-view:tool-all', 'cards');
    window.localStorage.setItem('flocks:plugin-view:tool-mcp', 'list');
    const { result, rerender } = renderHook(({ key }) => usePluginViewMode(key, 'list'), { initialProps: { key: 'tool-all' } });
    expect(result.current[0]).toBe('cards');
    rerender({ key: 'tool-mcp' });
    expect(result.current[0]).toBe('list');
    expect(window.localStorage.getItem('flocks:plugin-view:tool-mcp')).toBe('list');
    act(() => result.current[1]('cards'));
    expect(window.localStorage.getItem('flocks:plugin-view:tool-mcp')).toBe('cards');
    rerender({ key: 'tool-all' });
    expect(result.current[0]).toBe('cards');
    rerender({ key: 'tool-api' });
    expect(result.current[0]).toBe('list');
    expect(window.localStorage.getItem('flocks:plugin-view:tool-api')).toBeNull();
  });

  it('continues in memory when reading or writing browser storage fails', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    const { result } = renderHook(() => usePluginViewMode('device', 'cards'));
    expect(result.current[0]).toBe('cards');
    act(() => result.current[1]('list'));
    expect(result.current[0]).toBe('list');
  });
});
