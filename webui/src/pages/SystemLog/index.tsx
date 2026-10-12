import { useState, useEffect, useRef, useCallback } from 'react';
import { ChevronDown, RefreshCw, ScrollText } from 'lucide-react';
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
    <div className="flex flex-col">
      <PageHeader
        title={t('logs.title')}
        description={t('logs.description')}
        icon={<ScrollText aria-hidden="true" className="w-8 h-8" />}
      />

      {/* The page around it grows with its content, so the viewer takes a viewport-based
          height and scrolls inside (auto-scroll follows the newest lines). */}
      <div className="flex h-[calc(100dvh-18rem)] min-h-[420px] flex-col overflow-hidden rounded-xl border border-gray-200 bg-white">
      {/* Toolbar */}
      {/* Wraps on narrow screens; the select yields width (min-w-0 + truncate) so the
          auto-scroll label keeps one line and the refresh button stays on screen. */}
      <div className="px-4 py-2 border-b border-gray-100 flex flex-wrap items-center gap-3">
        {/* File selector */}
        <select
          value={selectedFile || ''}
          onChange={(e) => handleFileSelect(e.target.value)}
          disabled={files.length === 0}
          className="min-w-0 max-w-full truncate text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white
                     focus:outline-none focus:ring-1 focus:ring-slate-300 focus:border-slate-400
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {files.length === 0 && <option value="">{t('logs.noFiles')}</option>}
          {files.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name} ({(f.size / 1024).toFixed(1)} KB)
            </option>
          ))}
        </select>

        {/* Auto-scroll toggle */}
        <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none shrink-0 whitespace-nowrap">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="rounded text-slate-600 focus:ring-slate-400"
          />
          <ChevronDown aria-hidden="true" className="w-3 h-3" />
          {t('logs.autoScroll')}
        </label>

        <div className="ml-auto shrink-0">
          <button
            onClick={handleRefresh}
            disabled={loading}
            title={t('logs.refresh')}
            className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:bg-gray-50 hover:text-gray-600 disabled:opacity-50 transition-colors"
          >
            <RefreshCw aria-hidden="true" className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Log content area */}
      <div className="flex-1 overflow-hidden relative">
        {loading && !logContent && (
          <div className="flex items-center justify-center h-full">
            <RefreshCw aria-hidden="true" className="w-6 h-6 text-gray-400 animate-spin" />
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-red-500">{error}</p>
          </div>
        )}
        {logContent && (
          <pre
            ref={contentRef}
            className="h-full p-4 text-xs font-mono bg-gray-900 text-green-400 leading-relaxed overflow-auto whitespace-pre-wrap break-words"
          >
            {logContent.content}
          </pre>
        )}
        {!loading && !error && !logContent && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-gray-400">{t('logs.noFiles')}</p>
          </div>
        )}
      </div>

      {/* Footer meta */}
      {logContent && (
        <div className="px-4 py-2 border-t border-gray-200 bg-gray-50 flex items-center gap-4 text-xs text-gray-500 flex-shrink-0">
          <span>{t('logs.file')}: {logContent.filename}</span>
          <span>{t('logs.totalLines')}: {logContent.total_lines}</span>
          {logContent.truncated && (
            <span className="text-amber-600">{t('logs.truncated')}</span>
          )}
        </div>
      )}
      </div>
    </div>
  );
}
