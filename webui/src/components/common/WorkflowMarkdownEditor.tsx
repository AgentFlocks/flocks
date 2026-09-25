import { useCallback, useEffect, useMemo, useRef } from 'react';

interface WorkflowMarkdownEditorProps {
  id?: string;
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
}

export default function WorkflowMarkdownEditor({
  id = 'workflow-edit-doc',
  label,
  placeholder = '',
  value,
  onChange,
}: WorkflowMarkdownEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lineNumberTrackRef = useRef<HTMLDivElement | null>(null);
  const lineNumbers = useMemo(() => {
    const totalLines = Math.max(1, value.split('\n').length);
    return Array.from({ length: totalLines }, (_, index) => index + 1);
  }, [value]);
  const gutterWidth = Math.max(56, String(lineNumbers.length).length * 8 + 32);

  const syncLineNumberOffset = useCallback(() => {
    if (!lineNumberTrackRef.current) return;
    const scrollTop = textareaRef.current?.scrollTop ?? 0;
    lineNumberTrackRef.current.style.transform = `translateY(-${scrollTop}px)`;
  }, []);

  useEffect(() => {
    syncLineNumberOffset();
  }, [lineNumbers.length, syncLineNumberOffset]);

  return (
    <div className="flex h-full min-h-0 w-full flex-1 overflow-hidden bg-canvas">
      <label htmlFor={id} className="sr-only">{label}</label>
      <div
        aria-hidden="true"
        data-testid="workflow-md-line-numbers"
        className="h-full flex-shrink-0 overflow-hidden select-none border-r border-line-control bg-selected py-5 pr-3 text-right font-mono text-sm leading-6 text-fg-muted"
        style={{ width: gutterWidth }}
      >
        <div ref={lineNumberTrackRef}>
          {lineNumbers.map((lineNumber) => (
            <div key={lineNumber} data-line-number={lineNumber} className="h-6 leading-6">
              {lineNumber}
            </div>
          ))}
        </div>
      </div>
      <textarea
        ref={textareaRef}
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={syncLineNumberOffset}
        placeholder={placeholder}
        wrap="off"
        className="h-full min-h-0 min-w-0 w-full resize-none overflow-auto border-0 bg-canvas px-6 py-5 font-mono text-sm leading-6 text-fg caret-danger outline-none selection:bg-danger-soft placeholder:text-fg-muted"
        spellCheck={false}
      />
    </div>
  );
}
