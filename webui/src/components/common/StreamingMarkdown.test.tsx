import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, render } from '@testing-library/react';

import { StreamingMarkdown, useStreamingContent } from './StreamingMarkdown';

// ─── rAF fake setup ──────────────────────────────────────────────────────────

type RafCallback = (time: number) => void;

let rafQueue: RafCallback[] = [];
let rafIdCounter = 0;

function setupFakeRaf() {
  vi.stubGlobal('requestAnimationFrame', (cb: RafCallback) => {
    rafIdCounter++;
    rafQueue.push(cb);
    return rafIdCounter;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    // Mark cancelled by removing; simplified — good enough for these tests
    rafQueue = rafQueue.filter((_, i) => i !== id - 1);
  });
}

function flushRaf() {
  const pending = [...rafQueue];
  rafQueue = [];
  pending.forEach(cb => cb(performance.now()));
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('useStreamingContent', () => {
  beforeEach(() => {
    rafQueue = [];
    rafIdCounter = 0;
    setupFakeRaf();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns initial content immediately on mount', () => {
    const { result } = renderHook(() => useStreamingContent('hello', false));
    expect(result.current).toBe('hello');
  });

  it('non-streaming: updates displayContent synchronously when content changes', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: false } },
    );

    expect(result.current).toBe('a');

    act(() => {
      rerender({ content: 'b', isStreaming: false });
    });

    expect(result.current).toBe('b');
  });

  it('streaming: does not update displayContent until rAF fires, then advances smoothly', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'chunk1', isStreaming: true } },
    );

    // Initial value applied immediately (useState initializer)
    expect(result.current).toBe('chunk1');

    // New content arrives while streaming — should NOT update yet
    act(() => {
      rerender({ content: 'chunk1 chunk2', isStreaming: true });
    });
    expect(result.current).toBe('chunk1');

    // After rAF fires, it advances, but no longer jumps straight to the latest content.
    act(() => {
      flushRaf();
    });
    expect(result.current.length).toBeGreaterThan('chunk1'.length);
    expect(result.current.length).toBeLessThan('chunk1 chunk2'.length);
  });

  it('streaming: multiple content updates in same frame only trigger one rAF', () => {
    const rafSpy = vi.fn().mockImplementation((cb: RafCallback) => {
      rafQueue.push(cb);
      return ++rafIdCounter;
    });
    vi.stubGlobal('requestAnimationFrame', rafSpy);

    const { rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: true } },
    );

    act(() => { rerender({ content: 'ab', isStreaming: true }); });
    act(() => { rerender({ content: 'abc', isStreaming: true }); });
    act(() => { rerender({ content: 'abcd', isStreaming: true }); });

    // Only one rAF should have been scheduled (subsequent calls skipped because pendingRaf != null)
    expect(rafSpy).toHaveBeenCalledTimes(1);
  });

  it('streaming→done: cancels pending rAF and applies final content immediately', () => {
    const cancelSpy = vi.fn();
    vi.stubGlobal('cancelAnimationFrame', cancelSpy);

    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'chunk1', isStreaming: true } },
    );

    // Queue a pending rAF by updating content while streaming
    act(() => { rerender({ content: 'chunk1 chunk2', isStreaming: true }); });

    // Now streaming ends with the final content — should cancel rAF and update immediately
    act(() => { rerender({ content: 'chunk1 chunk2 final', isStreaming: false }); });

    expect(cancelSpy).toHaveBeenCalled();
    expect(result.current).toBe('chunk1 chunk2 final');
  });

  it('streaming: drains queued deltas progressively instead of jumping to latest content', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: true } },
    );

    // Multiple updates before the frame fires
    act(() => { rerender({ content: 'ab', isStreaming: true }); });
    act(() => { rerender({ content: 'abc', isStreaming: true }); });
    act(() => { rerender({ content: 'abcd', isStreaming: true }); });

    // One frame should advance the text, but not jump straight to the latest content.
    act(() => { flushRaf(); });
    expect(result.current).toBe('ab');

    act(() => { flushRaf(); });
    expect(result.current).toBe('abc');

    act(() => { flushRaf(); });
    expect(result.current).toBe('abcd');
  });
});

describe('StreamingMarkdown', () => {
  it('preserves single newlines as visible line breaks', () => {
    const { container } = render(
      <StreamingMarkdown content={'first line\nsecond line\nthird line'} isStreaming={false} />,
    );

    const paragraph = container.querySelector('p');
    expect(paragraph).not.toBeNull();
    expect(paragraph?.querySelectorAll('br')).toHaveLength(2);
    expect(paragraph?.textContent).toBe('first line\nsecond line\nthird line');
  });
});
