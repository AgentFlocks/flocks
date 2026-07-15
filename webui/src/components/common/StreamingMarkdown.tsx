import { useState, useEffect, useRef, useCallback } from 'react';
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
  const queuedCharsRef = useRef<string[]>([]);
  const isStreamingRef = useRef(isStreaming);

  const scheduleDrain = useCallback((drainQueue: (time: number) => void) => {
    if (pendingRafRef.current !== null) return;
    pendingRafRef.current = requestAnimationFrame(drainQueue);
  }, []);

  const drainQueue = useCallback(() => {
    pendingRafRef.current = null;

    if (queuedCharsRef.current.length === 0) {
      return;
    }

    // Drain progressively so a stalled frame does not dump the whole backlog
    // in a single repaint. Larger backlogs should still catch up within a
    // handful of frames instead of lagging behind for visibly too long.
    const charsToRenderCount = Math.max(1, Math.ceil(queuedCharsRef.current.length / 3));
    const nextChunk = queuedCharsRef.current.splice(0, charsToRenderCount).join('');
    displayedContentRef.current += nextChunk;
    setDisplayContent(displayedContentRef.current);

    if (queuedCharsRef.current.length > 0 && isStreamingRef.current) {
      scheduleDrain(drainQueue);
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
      queuedCharsRef.current = [];
      incomingContentRef.current = content;
      displayedContentRef.current = content;
      setDisplayContent(content);
      return;
    }

    const previousIncoming = incomingContentRef.current;
    incomingContentRef.current = content;

    if (!content.startsWith(previousIncoming)) {
      // Content replaced or rewound: reset immediately to preserve correctness.
      queuedCharsRef.current = [];
      displayedContentRef.current = content;
      setDisplayContent(content);
      return;
    }

    const delta = content.slice(previousIncoming.length);
    if (!delta) return;

    queuedCharsRef.current.push(...Array.from(delta));
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

  return displayContent;
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
export function StreamingMarkdown({ content, isStreaming }: StreamingMarkdownProps) {
  const displayContent = useStreamingContent(content, isStreaming);

  return (
    <div className="prose prose-sm max-w-none">
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
        {displayContent}
      </ReactMarkdown>
    </div>
  );
}
