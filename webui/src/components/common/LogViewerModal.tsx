import { useState, useEffect, useRef, useCallback } from 'react';
import { X, RefreshCw, ChevronDown, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { logsAPI, type LogFileInfo, type LogContentResponse } from '@/api/logs';

interface LogViewerModalProps {
  open: boolean;
  onClose: () => void;
}

export default function LogViewerModal({ open, onClose }: LogViewerModalProps) {
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
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-4 md:inset-8 lg:inset-12 z-50 flex flex-col bg-white rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-200 bg-gray-50 flex-shrink-0">
          <FileText className="w-5 h-5 text-purple-600" />
          <h2 className="text-lg font-semibold text-gray-900 flex-1">{t('logs.title')}</h2>
          <div className="flex items-center gap-2">
            <select
              value={selectedFile || ''}
              onChange={(e) => handleFileSelect(e.target.value)}
              disabled={files.length === 0}
              className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50"
            >
              {files.length === 0 && <option value="">{t('logs.noFiles')}</option>}
              {files.map((f) => (
                <option key={f.name} value={f.name}>{f.name} ({(f.size / 1024).toFixed(1)} KB)</option>
              ))}
            </select>
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-50"
              title={t('logs.refresh')}
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
                className="rounded text-purple-600 focus:ring-purple-500"
              />
              <ChevronDown className="w-3 h-3" />
              {t('logs.autoScroll')}
            </label>
            <button onClick={onClose} className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden relative">
          {loading && !logContent && (
            <div className="flex items-center justify-center h-full">
              <RefreshCw className="w-6 h-6 text-gray-400 animate-spin" />
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
              className="h-full overflow-auto p-4 text-xs font-mono bg-gray-900 text-green-400 leading-relaxed whitespace-pre-wrap break-words"
            >
              {logContent.content}
            </pre>
          )}
        </div>

        {logContent && (
          <div className="flex-shrink-0 px-6 py-2 border-t border-gray-200 bg-gray-50 flex items-center gap-4 text-xs text-gray-500">
            <span>{t('logs.file')}: {logContent.filename}</span>
            <span>{t('logs.totalLines')}: {logContent.total_lines}</span>
            {logContent.truncated && <span className="text-amber-600">{t('logs.truncated')}</span>}
          </div>
        )}
      </div>
    </>
  );
}
