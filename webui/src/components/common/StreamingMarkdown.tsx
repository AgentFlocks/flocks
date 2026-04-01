import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import 'highlight.js/styles/github-dark.css';

/**
 * Throttles content updates to at most once per animation frame while streaming.
 * When streaming ends, immediately flushes the latest content.
 *
 * Mirrors Open WebUI's Markdown.svelte approach:
 *   if (done) { cancelAnimationFrame(pending); parseTokens(); }
 *   else if (!pending) { pending = rAF(() => { pending = null; parseTokens(); }) }
 */
function useStreamingContent(content: string, isStreaming: boolean): string {
  const [displayContent, setDisplayContent] = useState(content);
  const pendingRafRef = useRef<number | null>(null);
  const latestContentRef = useRef(content);

  useEffect(() => {
    latestContentRef.current = content;

    if (!isStreaming) {
      // Streaming done: cancel any pending frame and apply final content immediately
      if (pendingRafRef.current !== null) {
        cancelAnimationFrame(pendingRafRef.current);
        pendingRafRef.current = null;
      }
      setDisplayContent(content);
    } else if (pendingRafRef.current === null) {
      // Streaming: schedule at most one update per frame
      pendingRafRef.current = requestAnimationFrame(() => {
        pendingRafRef.current = null;
        setDisplayContent(latestContentRef.current);
      });
    }
    // If pendingRafRef.current !== null, a frame is already scheduled;
    // latestContentRef ensures it will pick up the most recent content when it fires.
  }, [content, isStreaming]);

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
        rehypePlugins={[rehypeRaw, [rehypeHighlight, { detect: false, ignoreMissing: true }]]}
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
