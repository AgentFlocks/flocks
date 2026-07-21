import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, render } from '@testing-library/react';

import {
  StreamingMarkdown,
  fallbackSplitStreamingGraphemes,
  splitStreamingGraphemes,
  useStreamingContent,
} from './StreamingMarkdown';

// ─── rAF fake setup ──────────────────────────────────────────────────────────

type RafCallback = (time: number) => void;

let rafQueue = new Map<number, RafCallback>();
let rafIdCounter = 0;
let rafTime = 0;

function setupFakeRaf() {
  vi.stubGlobal('requestAnimationFrame', (cb: RafCallback) => {
    const id = ++rafIdCounter;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    rafQueue.delete(id);
  });
}

function flushRafAt(time: number) {
  rafTime = time;
  const pending = [...rafQueue.values()];
  rafQueue.clear();
  pending.forEach(cb => cb(time));
}

function flushRaf(stepMs = 1000 / 60) {
  flushRafAt(rafTime + stepMs);
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('useStreamingContent', () => {
  beforeEach(() => {
    rafQueue = new Map();
    rafIdCounter = 0;
    rafTime = 0;
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
      const id = ++rafIdCounter;
      rafQueue.set(id, cb);
      return id;
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
    const cancelSpy = vi.fn((id: number) => rafQueue.delete(id));
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

    act(() => { flushRaf(); });
    expect(result.current).toBe('chunk1 chunk2 final');
  });

  it('streaming: types a small English backlog one character per frame', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: true } },
    );

    // Multiple updates before the frame fires still retain a typewriter-sized
    // first step rather than jumping to the latest accumulated snapshot.
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

  it('streaming: types a small Chinese backlog one character per frame', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: '你', isStreaming: true } },
    );

    act(() => {
      rerender({ content: '你好世界', isStreaming: true });
    });

    act(() => { flushRaf(); });
    expect(result.current).toBe('你好');

    act(() => { flushRaf(); });
    expect(result.current).toBe('你好世');

    act(() => { flushRaf(); });
    expect(result.current).toBe('你好世界');
  });

  it('streaming: starts a large backlog with one character, then accelerates in bounded steps', () => {
    const fullContent = `a${'b'.repeat(120)}`;
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: true } },
    );

    act(() => {
      rerender({ content: fullContent, isStreaming: true });
    });

    act(() => {
      flushRaf();
    });
    expect(result.current).toBe('ab');

    act(() => {
      flushRaf();
    });
    const acceleratedLength = result.current.length;
    expect(acceleratedLength).toBeGreaterThan(2);
    expect(acceleratedLength - 2).toBeLessThanOrEqual(8);

    for (let i = 0; i < 90; i += 1) {
      act(() => {
        flushRaf();
      });
    }

    expect(result.current).toBe(fullContent);
  });

  it('streaming: advances at nearly the same rate on 60 Hz and 120 Hz displays', () => {
    const progressAfter = (frameMs: number) => {
      rafQueue.clear();
      rafTime = 0;
      const fullContent = `a${'b'.repeat(300)}`;
      const hook = renderHook(
        ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
        { initialProps: { content: 'a', isStreaming: true } },
      );

      act(() => { hook.rerender({ content: fullContent, isStreaming: true }); });
      const frameCount = Math.round(500 / frameMs);
      for (let frame = 1; frame <= frameCount; frame += 1) {
        act(() => { flushRafAt(frame * frameMs); });
      }
      const length = hook.result.current.length;
      hook.unmount();
      return length;
    };

    const progress60Hz = progressAfter(1000 / 60);
    const progress120Hz = progressAfter(1000 / 120);
    expect(Math.abs(progress60Hz - progress120Hz)).toBeLessThanOrEqual(4);
  });

  it('streaming: clamps a long stalled frame instead of dumping the backlog', () => {
    const fullContent = `a${'b'.repeat(300)}`;
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'a', isStreaming: true } },
    );

    act(() => { rerender({ content: fullContent, isStreaming: true }); });
    act(() => { flushRafAt(1000 / 60); });
    expect(result.current).toBe('ab');

    act(() => { flushRafAt(5000); });
    expect(result.current.length - 2).toBeGreaterThan(0);
    expect(result.current.length - 2).toBeLessThanOrEqual(8);

    const afterStallLength = result.current.length;
    act(() => { flushRaf(); });
    expect(result.current.length - afterStallLength).toBeLessThanOrEqual(7);
  });

  it('streaming: never splits a grapheme cluster across frames', () => {
    const graphemes = ['A', '👍🏽', '👨‍👩‍👧‍👦', '🇨🇳', 'é'];
    const fullContent = graphemes.join('');
    expect(splitStreamingGraphemes(fullContent)).toEqual(graphemes);

    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: graphemes[0], isStreaming: true } },
    );
    act(() => { rerender({ content: fullContent, isStreaming: true }); });

    for (let index = 2; index <= graphemes.length; index += 1) {
      act(() => { flushRaf(); });
      expect(result.current).toBe(graphemes.slice(0, index).join(''));
    }
  });

  it('streaming: re-segments an unpainted grapheme split across SSE updates', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'A', isStreaming: true } },
    );

    act(() => { rerender({ content: 'A👍', isStreaming: true }); });
    act(() => { rerender({ content: 'A👍🏽', isStreaming: true }); });
    act(() => { flushRaf(); });

    expect(result.current).toBe('A👍🏽');
  });

  it('fallback: keeps common compound graphemes intact without Intl.Segmenter', () => {
    const graphemes = ['A', '👍🏽', '👨‍👩‍👧‍👦', '🇨🇳', 'é', '1️⃣'];
    expect(fallbackSplitStreamingGraphemes(graphemes.join(''))).toEqual(graphemes);
  });

  it('streaming: the final delta and finish settle synchronously with no stale frame', () => {
    const { result, rerender } = renderHook(
      ({ content, isStreaming }) => useStreamingContent(content, isStreaming),
      { initialProps: { content: 'almost', isStreaming: true } },
    );

    act(() => {
      rerender({ content: 'almost done', isStreaming: true });
      rerender({ content: 'almost done', isStreaming: false });
    });
    expect(result.current).toBe('almost done');

    act(() => { flushRafAt(5000); });
    expect(result.current).toBe('almost done');
  });
});

describe('StreamingMarkdown', () => {
  it('constrains rendered Markdown to its message container', () => {
    const { container } = render(
      <StreamingMarkdown content={`\`\`\`text\n${'long-command'.repeat(100)}\n\`\`\``} isStreaming={false} />,
    );

    expect(container.firstElementChild).toHaveClass('w-full', 'min-w-0', 'max-w-full');
    expect(container.querySelector('pre')).not.toBeNull();
  });

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
