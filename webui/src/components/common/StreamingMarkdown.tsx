import { memo, useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import 'highlight.js/styles/github-dark.css';

const sanitizeSchema = {
  ...defaultSchema,
  strip: [...(defaultSchema.strip || []), 'style'],
};

const BASE_STREAMING_GRAPHEMES_PER_SECOND = 60;
const BACKLOG_RATE_BOOST = 4;
const MAX_STREAMING_GRAPHEMES_PER_SECOND = 360;
const MAX_STREAMING_GRAPHEMES_PER_FRAME = 8;
const MAX_DRAIN_ELAPSED_MS = 50;

interface SegmentData {
  segment: string;
}

interface GraphemeSegmenter {
  segment(input: string): Iterable<SegmentData>;
}

type GraphemeSegmenterConstructor = new (
  locales?: string | string[],
  options?: { granularity: 'grapheme' },
) => GraphemeSegmenter;

const Segmenter = (Intl as typeof Intl & { Segmenter?: GraphemeSegmenterConstructor }).Segmenter;
const graphemeSegmenter = Segmenter
  ? new Segmenter(undefined, { granularity: 'grapheme' })
  : null;

const MARK_PATTERN = /\p{Mark}/u;

/** Narrow fallback for engines that predate Intl.Segmenter. */
export function fallbackSplitStreamingGraphemes(value: string): string[] {
  const clusters: string[] = [];
  let cluster = '';
  let regionalIndicatorCount = 0;

  for (const codePoint of Array.from(value)) {
    const code = codePoint.codePointAt(0) ?? 0;
    const isRegionalIndicator = code >= 0x1f1e6 && code <= 0x1f1ff;
    const isEmojiModifier = code >= 0x1f3fb && code <= 0x1f3ff;
    const isVariationSelector = (
      (code >= 0xfe00 && code <= 0xfe0f)
      || (code >= 0xe0100 && code <= 0xe01ef)
    );
    const isEmojiTag = code >= 0xe0020 && code <= 0xe007f;
    const isJoiner = code === 0x200d;
    const joinsPrevious = (
      MARK_PATTERN.test(codePoint)
      || isEmojiModifier
      || isVariationSelector
      || isEmojiTag
      || isJoiner
      || cluster.endsWith('\u200d')
      || (isRegionalIndicator && regionalIndicatorCount === 1)
    );

    if (!cluster || joinsPrevious) {
      cluster += codePoint;
    } else {
      clusters.push(cluster);
      cluster = codePoint;
    }

    if (isRegionalIndicator) {
      regionalIndicatorCount = joinsPrevious ? regionalIndicatorCount + 1 : 1;
    } else if (!isVariationSelector && !isEmojiModifier && !MARK_PATTERN.test(codePoint)) {
      regionalIndicatorCount = 0;
    }
  }

  if (cluster) clusters.push(cluster);
  return clusters;
}

export function splitStreamingGraphemes(value: string): string[] {
  if (!graphemeSegmenter) return fallbackSplitStreamingGraphemes(value);
  return Array.from(graphemeSegmenter.segment(value), ({ segment }) => segment);
}

interface DrainBudget {
  count: number;
  credit: number;
}

/**
 * Convert elapsed time into a bounded render budget. A small backlog keeps a
 * natural typewriter cadence; a growing backlog raises the target rate so the
 * UI catches up without dumping an entire model chunk in one paint.
 */
export function getStreamingDrainBudget(
  backlogLength: number,
  elapsedMs: number,
  credit: number,
  firstFrame: boolean,
): DrainBudget {
  if (backlogLength <= 0) return { count: 0, credit: 0 };
  if (firstFrame) return { count: 1, credit: 0 };

  const boundedElapsedMs = Math.max(0, Math.min(MAX_DRAIN_ELAPSED_MS, elapsedMs));
  const targetRate = Math.min(
    MAX_STREAMING_GRAPHEMES_PER_SECOND,
    BASE_STREAMING_GRAPHEMES_PER_SECOND
      + Math.max(0, backlogLength - 1) * BACKLOG_RATE_BOOST,
  );
  const availableCredit = credit + targetRate * boundedElapsedMs / 1000;
  const count = Math.min(
    backlogLength,
    MAX_STREAMING_GRAPHEMES_PER_FRAME,
    Math.floor(availableCredit),
  );
  // Preserve only fractional credit. Integer work rejected by the per-frame
  // cap must not become a multi-frame burst after a long main-thread stall.
  return { count, credit: availableCredit - Math.floor(availableCredit) };
}

/**
 * Smooths streamed content by queueing appended text and draining it across
 * animation frames. The previous implementation collapsed all updates that
 * arrived before the next rAF into a single "jump to latest" repaint, which
 * caused visible bursts after a brief main-thread stall.
 */
export function useStreamingContent(content: string, isStreaming: boolean): string {
  const [displayContent, setDisplayContent] = useState(content);
  const pendingRafRef = useRef<number | null>(null);
  const incomingContentRef = useRef(content);
  const displayedContentRef = useRef(content);
  const queuedGraphemesRef = useRef<string[]>([]);
  const isStreamingRef = useRef(isStreaming);
  const lastDrainTimeRef = useRef<number | null>(null);
  const drainCreditRef = useRef(0);

  const scheduleDrain = useCallback((drainQueue: (time: number) => void) => {
    if (pendingRafRef.current !== null) return;
    pendingRafRef.current = requestAnimationFrame(drainQueue);
  }, []);

  const drainQueue = useCallback((time: number) => {
    pendingRafRef.current = null;

    if (queuedGraphemesRef.current.length === 0) {
      lastDrainTimeRef.current = null;
      drainCreditRef.current = 0;
      return;
    }

    const previousDrainTime = lastDrainTimeRef.current;
    const budget = getStreamingDrainBudget(
      queuedGraphemesRef.current.length,
      previousDrainTime === null ? 0 : time - previousDrainTime,
      drainCreditRef.current,
      previousDrainTime === null,
    );
    lastDrainTimeRef.current = time;
    drainCreditRef.current = budget.credit;

    if (budget.count > 0) {
      const nextChunk = queuedGraphemesRef.current.splice(0, budget.count).join('');
      displayedContentRef.current += nextChunk;
      setDisplayContent(displayedContentRef.current);
    }

    if (queuedGraphemesRef.current.length > 0 && isStreamingRef.current) {
      scheduleDrain(drainQueue);
    } else {
      lastDrainTimeRef.current = null;
      drainCreditRef.current = 0;
    }
  }, [scheduleDrain]);

  useEffect(() => {
    isStreamingRef.current = isStreaming;

    if (!isStreaming) {
      // Streaming done: cancel any pending frame and apply final content immediately
      if (pendingRafRef.current !== null) {
        cancelAnimationFrame(pendingRafRef.current);
        pendingRafRef.current = null;
      }
      queuedGraphemesRef.current = [];
      lastDrainTimeRef.current = null;
      drainCreditRef.current = 0;
      incomingContentRef.current = content;
      displayedContentRef.current = content;
      setDisplayContent(content);
      return;
    }

    const previousIncoming = incomingContentRef.current;
    incomingContentRef.current = content;

    if (!content.startsWith(previousIncoming)) {
      // Content replaced or rewound: reset immediately to preserve correctness.
      queuedGraphemesRef.current = [];
      lastDrainTimeRef.current = null;
      drainCreditRef.current = 0;
      displayedContentRef.current = content;
      setDisplayContent(content);
      return;
    }

    const delta = content.slice(previousIncoming.length);
    if (!delta) return;

    // The last queued grapheme may be completed by the next SSE delta (for
    // example 👍 + 🏽 or an emoji ZWJ sequence). Re-segment the unpainted tail
    // together with the new text before it reaches the screen.
    const queuedTail = queuedGraphemesRef.current.pop() ?? '';
    queuedGraphemesRef.current.push(...splitStreamingGraphemes(queuedTail + delta));
    scheduleDrain(drainQueue);
  }, [content, isStreaming, drainQueue, scheduleDrain]);

  // Cancel any pending rAF on unmount
  useEffect(
    () => () => {
      if (pendingRafRef.current !== null) {
        cancelAnimationFrame(pendingRafRef.current);
      }
    },
    [],
  );

  // Completion must win in the same paint as the finish event. The effect
  // above still clears queued work, but rendering does not wait for it.
  return isStreaming ? displayContent : content;
}

export interface StreamingMarkdownProps {
  /** Full accumulated text content to render */
  content: string;
  /** When true, content updates are throttled to one per animation frame */
  isStreaming: boolean;
}

/**
 * Renders Markdown at all times (no plain-text fallback during streaming).
 * Content updates are throttled via requestAnimationFrame while streaming,
 * limiting ReactMarkdown re-parses to ~60fps instead of every SSE chunk.
 */
const MarkdownContent = memo(function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="prose prose-sm w-full min-w-0 max-w-full">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], [rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={{
          code({ className, children, ...props }) {
            // Detect block-level code (fenced code block):
            // 1. Has a language-* class (explicit language tag)
            // 2. Has the hljs class (added by rehype-highlight)
            // 3. Children end with \n (react-markdown appends trailing newline for blocks)
            const isBlock =
              /language-/.test(className || '') ||
              /\bhljs\b/.test(className || '') ||
              String(children ?? '').endsWith('\n');
            if (!isBlock) {
              return (
                <code
                  className="bg-gray-100 text-gray-800 px-1 py-0.5 rounded text-[0.85em] font-mono"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export function StreamingMarkdown({ content, isStreaming }: StreamingMarkdownProps) {
  const displayContent = useStreamingContent(content, isStreaming);
  return <MarkdownContent content={displayContent} />;
}
