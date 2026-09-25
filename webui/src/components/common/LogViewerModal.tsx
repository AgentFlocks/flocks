import { useState, useEffect, useRef, useCallback, useId } from 'react';
import { IconX, IconRefresh, IconChevronDown, IconFileText } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { logsAPI, type LogFileInfo, type LogContentResponse } from '@/api/logs';
import { useDialogFocus } from '@/hooks/useDialogFocus';

interface LogViewerModalProps {
  open: boolean;
  onClose: () => void;
}

export default function LogViewerModal({ open, onClose }: LogViewerModalProps) {
  const { t } = useTranslation('tool');
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(dialogRef, open, onClose);
  const [files, setFiles] = useState<LogFileInfo[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [logContent, setLogContent] = useState<LogContentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contentRef = useRef<HTMLPreElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const loadFiles = useCallback(async () => {
    try {
      const res = await logsAPI.list();
      setFiles(res.data?.files || []);
    } catch {
      setFiles([]);
    }
  }, []);

  const loadContent = useCallback(async (filename?: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = filename
        ? await logsAPI.read(filename, 500)
        : await logsAPI.readLatest(500);
      setLogContent(res.data);
      setSelectedFile(res.data?.filename || null);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to load logs');
      setLogContent(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      loadFiles();
      loadContent();
    }
  }, [open, loadFiles, loadContent]);

  useEffect(() => {
    if (autoScroll && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [logContent, autoScroll]);

  const handleRefresh = () => {
    loadContent(selectedFile || undefined);
  };

  const handleFileSelect = (name: string) => {
    setSelectedFile(name);
    loadContent(name);
  };

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={onClose} />
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} data-ui-overlay="dialog" className="fixed inset-x-4 top-4 bottom-4 mx-auto max-w-modal z-50 flex flex-col bg-surface rounded-overlay shadow-overlay overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-4 sm:px-6 py-4 border-b border-line bg-canvas flex-shrink-0">
          <IconFileText aria-hidden="true" className="w-5 h-5 text-ai" />
          <h2 id={titleId} className="text-title-section text-fg flex-1">{t('logs.title')}</h2>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={selectedFile || ''}
              onChange={(e) => handleFileSelect(e.target.value)}
              disabled={files.length === 0}
              aria-label={t('logs.file')}
              className="ui-select w-auto max-w-full"
            >
              {files.length === 0 && <option value="">{t('logs.noFiles')}</option>}
              {files.map((f) => (
                <option key={f.name} value={f.name}>{f.name} ({(f.size / 1024).toFixed(1)} KB)</option>
              ))}
            </select>
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="ui-btn ui-btn-ghost ui-btn-icon text-fg-muted"
              title={t('logs.refresh')}
              aria-label={t('logs.refresh')}
            >
              <IconRefresh aria-hidden="true" className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <label className="flex items-center gap-1.5 text-xs text-fg-muted cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded text-ai focus:ring-focus"
              />
              <IconChevronDown aria-hidden="true" className="w-4 h-4" />
              {t('logs.autoScroll')}
            </label>
            <button onClick={onClose} aria-label={t('common:button.close')} className="ui-btn ui-btn-ghost ui-btn-icon text-fg-muted">
              <IconX aria-hidden="true" className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden relative">
          {loading && !logContent && (
            <div className="flex items-center justify-center h-full">
              <IconRefresh aria-hidden="true" className="w-6 h-6 text-fg-muted animate-spin" />
            </div>
          )}
          {error && (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm text-danger">{error}</p>
            </div>
          )}
          {logContent && (
            <pre
              ref={contentRef}
              className="h-full overflow-auto p-4 text-xs font-mono bg-subtle text-fg whitespace-pre-wrap break-words"
            >
              {logContent.content}
            </pre>
          )}
        </div>

        {logContent && (
          <div className="flex-shrink-0 px-6 py-2 border-t border-line bg-canvas flex items-center gap-4 text-xs text-fg-muted">
            <span>{t('logs.file')}: {logContent.filename}</span>
            <span>{t('logs.totalLines')}: {logContent.total_lines}</span>
            {logContent.truncated && <span className="text-warning">{t('logs.truncated')}</span>}
          </div>
        )}
      </div>
    </>
  );
}
