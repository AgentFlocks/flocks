import { useState, useEffect, useRef, useCallback } from 'react';
import { IconRefresh, IconChevronDown, IconScript } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { logsAPI, type LogFileInfo, type LogContentResponse } from '@/api/logs';

export default function SystemLogPage() {
  const { t } = useTranslation('tool');

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
    loadFiles();
    loadContent();
  }, [loadFiles, loadContent]);

  useEffect(() => {
    if (autoScroll && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [logContent, autoScroll]);

  const handleRefresh = () => loadContent(selectedFile || undefined);

  const handleFileSelect = (name: string) => {
    setSelectedFile(name);
    loadContent(name);
  };

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('logs.title')}
        description={t('logs.description')}
        icon={<IconScript aria-hidden="true" className="w-8 h-8" />}
      />

      {/* Toolbar */}
      {/* On narrow screens the select must yield width (min-w-0 + truncate) so
          the auto-scroll label keeps one line and the refresh button stays on
          screen instead of being pushed past the right edge. */}
      <div className="px-4 py-2 border-b border-line flex items-center gap-3">
        {/* File selector */}
        <select
          value={selectedFile || ''}
          onChange={(e) => handleFileSelect(e.target.value)}
          disabled={files.length === 0}
          className="ui-select min-w-0 flex-1 max-w-sm truncate text-sm border border-line rounded-control px-2.5  bg-surface
 focus:border-line-control
 disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed"
        >
          {files.length === 0 && <option value="">{t('logs.noFiles')}</option>}
          {files.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name} ({(f.size / 1024).toFixed(1)} KB)
            </option>
          ))}
        </select>

        {/* Auto-scroll toggle */}
        <label className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-fg-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="rounded-tag text-fg-secondary "
          />
          <IconChevronDown aria-hidden="true" className="w-4 h-4" />
          {t('logs.autoScroll')}
        </label>

        <div className="ml-auto shrink-0">
          <button
            onClick={handleRefresh}
            disabled={loading}
            title={t('logs.refresh')}
            className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-line text-fg-muted
 hover:bg-hover hover:text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled transition-colors"
          >
            <IconRefresh aria-hidden="true" className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Log content area */}
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
            className="h-full overflow-auto p-4 text-xs font-mono bg-subtle text-success
 whitespace-pre-wrap break-words"
          >
            {logContent.content}
          </pre>
        )}
        {!loading && !error && !logContent && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-fg-muted">{t('logs.noFiles')}</p>
          </div>
        )}
      </div>

      {/* Footer meta */}
      {logContent && (
        <div className="flex-shrink-0 px-4 py-2 border-t border-line bg-subtle
 flex items-center gap-4 text-xs text-fg-muted">
          <span>{t('logs.file')}: {logContent.filename}</span>
          <span>{t('logs.totalLines')}: {logContent.total_lines}</span>
          {logContent.truncated && (
            <span className="text-warning">{t('logs.truncated')}</span>
          )}
        </div>
      )}
    </div>
  );
}
