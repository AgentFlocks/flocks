import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, ChevronLeft, ChevronRight, Code2, Download, Eye,
  FolderOpen, X, ZoomIn, ZoomOut,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import LoadingSpinner from '@/components/common/LoadingSpinner';
import { formatBytes, formatDate, fileIcon, type WorkspaceNode } from '@/api/workspace';

export type PreviewKind = 'markdown' | 'html' | 'json' | 'jsonl' | 'csv' | 'text' | 'image' | 'pdf' | 'unsupported';
type PreviewMode = 'preview' | 'source';

export interface PreviewFileAccess {
  previewUrl: (path: string) => string;
  downloadUrl: (path: string) => string;
}

const PDF_MIN_SCALE = 0.6;
const PDF_MAX_SCALE = 2.2;
const PDF_SCALE_STEP = 0.2;
const PDF_MAX_OUTPUT_SCALE = 3;
const PDF_RENDER_WINDOW = 2;
const IMAGE_MIN_SCALE = 0.5;
const IMAGE_MAX_SCALE = 3;
const IMAGE_SCALE_STEP = 0.25;

const LazyStreamingMarkdown = lazy(() => import('@/components/common/StreamingMarkdown')
  .then((module) => ({ default: module.StreamingMarkdown })));

function fileExtension(name: string): string {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index + 1).toLowerCase() : '';
}

export function getPreviewKind(node: WorkspaceNode): PreviewKind {
  const ext = fileExtension(node.name);
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image';
  if (ext === 'pdf') return 'pdf';
  if (node.is_text_file) {
    if (['md', 'markdown'].includes(ext)) return 'markdown';
    if (['html', 'htm'].includes(ext)) return 'html';
    if (ext === 'json') return 'json';
    if (ext === 'jsonl') return 'jsonl';
    if (ext === 'csv') return 'csv';
    return 'text';
  }
  return 'unsupported';
}

function prettyJson(content: string): { value: string; error: string | null } {
  try {
    return { value: JSON.stringify(JSON.parse(content), null, 2), error: null };
  } catch (e: any) {
    return { value: content, error: e?.message ?? 'Invalid JSON' };
  }
}

function prettyJsonLines(content: string): { value: string; errorCount: number } {
  let errorCount = 0;
  const value = content.split(/\r?\n/).map((line) => {
    if (!line.trim()) return line;
    try {
      return JSON.stringify(JSON.parse(line), null, 2);
    } catch {
      errorCount += 1;
      return line;
    }
  }).join('\n');
  return { value, errorCount };
}

const CSV_MAX_ROWS = 1_000;
const CSV_MAX_COLUMNS = 100;

function parseCsv(content: string): { rows: string[][]; truncated: boolean } {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  let truncated = false;

  const pushField = () => {
    if (row.length < CSV_MAX_COLUMNS) {
      row.push(field);
    } else {
      truncated = true;
    }
    field = '';
  };

  for (let i = 0; i < content.length; i += 1) {
    const char = content[i];
    const next = content[i + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        field += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === ',' && !inQuotes) {
      pushField();
      continue;
    }

    if ((char === '\n' || char === '\r') && !inQuotes) {
      if (char === '\r' && next === '\n') i += 1;
      pushField();
      rows.push(row);
      row = [];
      if (rows.length >= CSV_MAX_ROWS && i < content.length - 1) {
        truncated = true;
        return { rows, truncated };
      }
      continue;
    }

    field += char;
  }

  pushField();
  if (row.length > 1 || row[0] !== '' || content.endsWith(',')) rows.push(row);
  return { rows, truncated };
}

function SourcePreview({ content }: { content: string }) {
  return (
    <pre className="h-full overflow-auto bg-white p-4 text-sm font-mono text-gray-700 whitespace-pre-wrap break-words">
      {content}
    </pre>
  );
}

function CsvPreview({ content }: { content: string }) {
  const { t } = useTranslation('workspace');
  const parsed = parseCsv(content);
  const rows = parsed.rows;
  if (rows.length === 0) {
    return <SourcePreview content={content} />;
  }

  const [header, ...body] = rows;
  const columnCount = Math.max(...rows.map((row) => row.length));

  return (
    <div className="h-full overflow-auto bg-white">
      {parsed.truncated && (
        <div className="sticky left-0 top-0 z-20 border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t('files.preview.csvTruncated', { rows: CSV_MAX_ROWS, columns: CSV_MAX_COLUMNS })}
        </div>
      )}
      <table className="min-w-full border-separate border-spacing-0 text-sm">
        <thead className="sticky top-0 z-10 bg-gray-50">
          <tr>
            {Array.from({ length: columnCount }).map((_, index) => (
              <th key={index} className="whitespace-nowrap border-b border-r border-gray-200 px-3 py-2 text-left text-xs font-semibold text-gray-600">
                {header[index] || `Column ${index + 1}`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex} className={rowIndex % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
              {Array.from({ length: columnCount }).map((_, columnIndex) => (
                <td key={columnIndex} className="max-w-[320px] whitespace-nowrap border-b border-r border-gray-100 px-3 py-2 text-gray-700">
                  <span className="block truncate" title={row[columnIndex] ?? ''}>{row[columnIndex] ?? ''}</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PdfPreview({
  node,
  fileAccess,
  onReveal,
}: {
  node: WorkspaceNode;
  fileAccess: PreviewFileAccess;
  onReveal?: (node: WorkspaceNode) => void;
}) {
  const { t } = useTranslation('workspace');
  const previewAreaRef = useRef<HTMLDivElement>(null);
  const pageCanvasRefs = useRef(new Map<number, HTMLCanvasElement>());
  const pageShellRefs = useRef(new Map<number, HTMLDivElement>());
  const renderedPageKeysRef = useRef(new Map<number, string>());
  const renderedPageSizesRef = useRef(new Map<number, { width: number; height: number }>());
  const [pdfDoc, setPdfDoc] = useState<any>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1);
  const [previewAreaWidth, setPreviewAreaWidth] = useState(0);
  const [pagesToRender, setPagesToRender] = useState<Set<number>>(() => new Set());
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewUrl = fileAccess.previewUrl(node.path);

  useEffect(() => {
    let cancelled = false;
    let loadingTask: any = null;
    setPdfDoc(null);
    setPageNumber(1);
    setPageCount(0);
    setScale(1);
    setPagesToRender(new Set());
    renderedPageKeysRef.current.clear();
    renderedPageSizesRef.current.clear();
    setLoading(true);
    setError(null);

    async function loadPdf() {
      try {
        const [pdfjsLib, pdfWorkerModule] = await Promise.all([
          import('pdfjs-dist'),
          import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
        ]);
        if (cancelled) {
          return;
        }

        pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerModule.default;
        loadingTask = pdfjsLib.getDocument({ url: previewUrl, withCredentials: true });
        const doc = await loadingTask.promise;
        if (cancelled) {
          doc?.destroy?.();
          return;
        }

        setPdfDoc(doc);
        setPageCount(doc.numPages);
        setPagesToRender(new Set(Array.from({ length: Math.min(doc.numPages, PDF_RENDER_WINDOW + 1) }, (_, index) => index + 1)));
        setLoading(false);
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message ?? 'PDF preview failed');
        setLoading(false);
      }
    }

    loadPdf();

    return () => {
      cancelled = true;
      loadingTask?.destroy?.();
    };
  }, [previewUrl]);

  const setPageCanvasRef = useCallback((page: number, element: HTMLCanvasElement | null) => {
    if (element) {
      pageCanvasRefs.current.set(page, element);
    } else {
      pageCanvasRefs.current.delete(page);
    }
  }, []);

  const setPageShellRef = useCallback((page: number, element: HTMLDivElement | null) => {
    if (element) {
      pageShellRefs.current.set(page, element);
    } else {
      pageShellRefs.current.delete(page);
    }
  }, []);

  const scrollToPage = useCallback((page: number) => {
    const targetPage = Math.min(pageCount, Math.max(1, page));
    setPageNumber(targetPage);
    setPagesToRender(new Set(
      Array.from(
        { length: Math.min(pageCount, targetPage + PDF_RENDER_WINDOW) - Math.max(1, targetPage - PDF_RENDER_WINDOW) + 1 },
        (_, index) => Math.max(1, targetPage - PDF_RENDER_WINDOW) + index,
      ),
    ));
    requestAnimationFrame(() => {
      pageShellRefs.current.get(targetPage)?.scrollIntoView({ block: 'start' });
    });
  }, [pageCount]);

  const handlePreviewScroll = useCallback(() => {
    const area = previewAreaRef.current;
    if (!area || pageCount === 0) return;

    const scrollTop = area.scrollTop + 16;
    let nearestPage = pageNumber;
    let nearestDistance = Number.POSITIVE_INFINITY;
    pageShellRefs.current.forEach((element, page) => {
      const distance = Math.abs(element.offsetTop - scrollTop);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestPage = page;
      }
    });
    if (nearestPage !== pageNumber) {
      setPageNumber(nearestPage);
    }
    const firstPage = Math.max(1, nearestPage - PDF_RENDER_WINDOW);
    const lastPage = Math.min(pageCount, nearestPage + PDF_RENDER_WINDOW);
    setPagesToRender(new Set(
      Array.from({ length: lastPage - firstPage + 1 }, (_, index) => firstPage + index),
    ));
  }, [pageCount, pageNumber]);

  useEffect(() => {
    const area = previewAreaRef.current;
    if (!area) return;

    if (area.clientWidth > 0) {
      setPreviewAreaWidth(area.clientWidth);
    }
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) {
        setPreviewAreaWidth(entry.contentRect.width);
      }
    });
    observer.observe(area);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pdfDoc || pageCount === 0) return;
    renderedPageKeysRef.current.clear();
  }, [pageCount, pdfDoc, previewAreaWidth, scale]);

  useEffect(() => {
    pageCanvasRefs.current.forEach((canvas, page) => {
      if (pagesToRender.has(page)) return;
      const size = renderedPageSizesRef.current.get(page);
      if (!size || canvas.width <= 1) return;
      canvas.width = 1;
      canvas.height = 1;
      canvas.style.width = `${size.width}px`;
      canvas.style.height = `${size.height}px`;
      renderedPageKeysRef.current.delete(page);
    });
  }, [pagesToRender]);

  useEffect(() => {
    if (!pdfDoc || pageCount === 0 || pagesToRender.size === 0) return;
    let cancelled = false;
    const renderTasks: any[] = [];

    async function renderPages() {
      setRendering(true);
      try {
        const orderedPages = [...pagesToRender].sort((a, b) => Math.abs(a - pageNumber) - Math.abs(b - pageNumber));
        for (const pageIndex of orderedPages) {
          if (pageIndex < 1 || pageIndex > pageCount) continue;
          const canvas = pageCanvasRefs.current.get(pageIndex);
          if (!canvas) continue;

          const page = await pdfDoc.getPage(pageIndex);
          if (cancelled) return;
          const baseViewport = page.getViewport({ scale: 1 });
          const availableWidth = Math.max(0, previewAreaWidth - 32);
          const fitScale = availableWidth > 0 ? availableWidth / baseViewport.width : 1;
          const viewport = page.getViewport({ scale: fitScale * scale });
          const context = canvas.getContext('2d');
          if (!context) {
            throw new Error('Canvas unavailable');
          }
          const outputScale = Math.min(window.devicePixelRatio || 1, PDF_MAX_OUTPUT_SCALE);
          const cssWidth = Math.ceil(viewport.width);
          const cssHeight = Math.ceil(viewport.height);
          const renderKey = `${cssWidth}x${cssHeight}@${outputScale}`;
          if (renderedPageKeysRef.current.get(pageIndex) === renderKey) {
            continue;
          }
          canvas.width = Math.ceil(viewport.width * outputScale);
          canvas.height = Math.ceil(viewport.height * outputScale);
          canvas.style.width = `${cssWidth}px`;
          canvas.style.height = `${cssHeight}px`;
          renderedPageSizesRef.current.set(pageIndex, { width: cssWidth, height: cssHeight });
          const renderTask = page.render({
            canvasContext: context,
            viewport,
            transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
          });
          renderTasks.push(renderTask);
          await renderTask.promise;
          renderedPageKeysRef.current.set(pageIndex, renderKey);
        }
      } catch (e: any) {
        if (!cancelled && e?.name !== 'RenderingCancelledException') {
          setError(e?.message ?? 'PDF preview failed');
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    }

    renderPages();
    return () => {
      cancelled = true;
      renderTasks.forEach((task) => task?.cancel?.());
    };
  }, [pageCount, pageNumber, pagesToRender, pdfDoc, previewAreaWidth, scale]);

  if (error) {
    return (
      <div className="h-full overflow-auto bg-white p-5">
        <div className="flex max-w-xl items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-800">
          <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div className="min-w-0 flex-1 space-y-3">
            <div className="space-y-1">
              <p className="text-sm font-medium">{t('files.preview.pdfLoadFailed')}</p>
              <p className="break-words text-xs leading-5">{error}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <a
                href={fileAccess.downloadUrl(node.path)}
                download={node.name}
                className="flex items-center gap-2 rounded-lg bg-slate-700 px-3 py-1.5 text-sm text-white hover:bg-slate-800"
              >
                <Download className="h-4 w-4" />
                {t('files.downloadFile')}
              </a>
              {onReveal && (
                <button
                  type="button"
                  onClick={() => onReveal(node)}
                  className="flex items-center gap-2 rounded-lg border border-amber-200 bg-white px-3 py-1.5 text-sm text-amber-800 hover:bg-amber-100"
                >
                  <FolderOpen className="h-4 w-4" />
                  {t('files.reveal')}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-gray-100">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 bg-white px-3 py-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => scrollToPage(pageNumber - 1)}
            disabled={loading || pageNumber <= 1}
            title={t('files.preview.previousPage')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="min-w-[5.5rem] text-center text-xs text-gray-600">
            {loading ? t('files.preview.pdfLoading') : t('files.preview.pageIndicator', { page: pageNumber, total: pageCount })}
          </span>
          <button
            type="button"
            onClick={() => scrollToPage(pageNumber + 1)}
            disabled={loading || pageNumber >= pageCount}
            title={t('files.preview.nextPage')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setScale((value) => Math.max(PDF_MIN_SCALE, Number((value - PDF_SCALE_STEP).toFixed(2))))}
            disabled={loading || scale <= PDF_MIN_SCALE}
            title={t('files.preview.zoomOut')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="w-12 text-center text-xs text-gray-600">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((value) => Math.min(PDF_MAX_SCALE, Number((value + PDF_SCALE_STEP).toFixed(2))))}
            disabled={loading || scale >= PDF_MAX_SCALE}
            title={t('files.preview.zoomIn')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div ref={previewAreaRef} onScroll={handlePreviewScroll} className="relative min-h-0 flex-1 overflow-auto p-4">
        {(loading || rendering) && (
          <div className="absolute inset-x-0 top-4 z-10 flex justify-center">
            <div className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-500 shadow-sm">
              {loading ? t('files.preview.pdfLoading') : t('files.preview.pdfRendering')}
            </div>
          </div>
        )}
        <div className="flex min-h-full flex-col items-center gap-4">
          {[...pagesToRender].sort((left, right) => left - right).map((page) => {
            return (
              <div
                key={page}
                ref={(element) => setPageShellRef(page, element)}
                className="flex w-full flex-col items-center gap-1"
              >
                <canvas
                  ref={(element) => setPageCanvasRef(page, element)}
                  className="h-fit max-w-none bg-white shadow"
                />
                <span className="text-[11px] text-gray-400">{page}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ImagePreview({ node, fileAccess }: { node: WorkspaceNode; fileAccess: PreviewFileAccess }) {
  const { t } = useTranslation('workspace');
  const previewAreaRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [previewAreaWidth, setPreviewAreaWidth] = useState(0);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const previewUrl = fileAccess.previewUrl(node.path);

  useEffect(() => {
    setScale(1);
    setNaturalSize(null);
  }, [node.path]);

  useEffect(() => {
    const area = previewAreaRef.current;
    if (!area) return;

    if (area.clientWidth > 0) {
      setPreviewAreaWidth(area.clientWidth);
    }
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) {
        setPreviewAreaWidth(entry.contentRect.width);
      }
    });
    observer.observe(area);
    return () => observer.disconnect();
  }, []);

  const availableWidth = Math.max(0, previewAreaWidth - 32);
  const fitScale = naturalSize && availableWidth > 0 ? Math.min(1, availableWidth / naturalSize.width) : 1;
  const displayWidth = naturalSize ? Math.max(1, Math.round(naturalSize.width * fitScale * scale)) : undefined;

  return (
    <div className="flex h-full flex-col bg-gray-100">
      <div className="flex justify-end border-b border-gray-200 bg-white px-3 py-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setScale((value) => Math.max(IMAGE_MIN_SCALE, Number((value - IMAGE_SCALE_STEP).toFixed(2))))}
            disabled={scale <= IMAGE_MIN_SCALE}
            title={t('files.preview.zoomOut')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="w-12 text-center text-xs text-gray-600">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((value) => Math.min(IMAGE_MAX_SCALE, Number((value + IMAGE_SCALE_STEP).toFixed(2))))}
            disabled={scale >= IMAGE_MAX_SCALE}
            title={t('files.preview.zoomIn')}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div ref={previewAreaRef} className="min-h-0 flex-1 overflow-auto p-4">
        <div className="flex min-h-full justify-center">
          <img
            src={previewUrl}
            alt={node.name}
            onLoad={(event) => {
              setNaturalSize({
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              });
            }}
            style={displayWidth ? { width: displayWidth } : undefined}
            className="h-fit max-w-none self-start bg-white object-contain shadow"
          />
        </div>
      </div>
    </div>
  );
}

function RenderedPreview({
  node,
  content,
  kind,
  fileAccess,
  onReveal,
}: {
  node: WorkspaceNode;
  content: string | null;
  kind: PreviewKind;
  fileAccess: PreviewFileAccess;
  onReveal?: (node: WorkspaceNode) => void;
}) {
  const { t } = useTranslation('workspace');

  if (kind === 'image') {
    return <ImagePreview node={node} fileAccess={fileAccess} />;
  }

  if (kind === 'pdf') {
    return <PdfPreview node={node} fileAccess={fileAccess} onReveal={onReveal} />;
  }

  if (kind === 'unsupported') {
    return <UnsupportedPreview node={node} fileAccess={fileAccess} onReveal={onReveal} />;
  }

  if (content === null) {
    return <div className="flex h-32 items-center justify-center"><LoadingSpinner /></div>;
  }

  if (kind === 'markdown') {
    return (
      <div className="h-full overflow-auto bg-white p-5">
        <Suspense fallback={<div className="flex h-32 items-center justify-center"><LoadingSpinner /></div>}>
          <LazyStreamingMarkdown content={content} isStreaming={false} />
        </Suspense>
      </div>
    );
  }

  if (kind === 'html') {
    return (
      <div className="flex h-full flex-col bg-white">
        <div className="border-b border-amber-100 bg-amber-50 px-4 py-2 text-xs text-amber-800">
          {t('files.preview.htmlSandbox')}
        </div>
        <iframe
          title={node.name}
          sandbox=""
          srcDoc={content}
          className="min-h-0 flex-1 border-0 bg-white"
        />
      </div>
    );
  }

  if (kind === 'json') {
    const formatted = prettyJson(content);
    return (
      <div className="flex h-full flex-col">
        {formatted.error && (
          <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {t('files.preview.jsonParseFailed')}
          </div>
        )}
        <SourcePreview content={formatted.value} />
      </div>
    );
  }

  if (kind === 'jsonl') {
    const formatted = prettyJsonLines(content);
    return (
      <div className="flex h-full flex-col">
        {formatted.errorCount > 0 && (
          <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {t('files.preview.jsonlParseFailed', { count: formatted.errorCount })}
          </div>
        )}
        <SourcePreview content={formatted.value} />
      </div>
    );
  }

  if (kind === 'csv') {
    return <CsvPreview content={content} />;
  }

  if (kind === 'text') {
    return <SourcePreview content={content} />;
  }

  return <UnsupportedPreview node={node} fileAccess={fileAccess} onReveal={onReveal} />;
}

function UnsupportedPreview({
  node,
  fileAccess,
  onReveal,
}: {
  node: WorkspaceNode;
  fileAccess: PreviewFileAccess;
  onReveal?: (node: WorkspaceNode) => void;
}) {
  const { t } = useTranslation('workspace');
  return (
    <div className="h-full overflow-auto bg-white p-5">
      <div className="flex max-w-xl items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-gray-500">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-orange-300" />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-medium text-gray-700">{t('files.preview.unsupportedTitle')}</p>
            <p className="text-xs leading-5 text-gray-500">{t('files.preview.unsupportedDesc')}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <a
              href={fileAccess.downloadUrl(node.path)}
              download={node.name}
              className="flex items-center gap-2 rounded-lg bg-slate-700 px-3 py-1.5 text-sm text-white hover:bg-slate-800"
            >
              <Download className="h-4 w-4" />
              {t('files.downloadFile')}
            </a>
            {onReveal && (
              <button
                type="button"
                onClick={() => onReveal(node)}
                className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-800"
              >
                <FolderOpen className="h-4 w-4" />
                {t('files.reveal')}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function PreviewModeToggle({
  mode,
  onChange,
}: {
  mode: PreviewMode;
  onChange: (mode: PreviewMode) => void;
}) {
  const { t } = useTranslation('workspace');
  return (
    <div className="flex items-center rounded-md border border-gray-200 bg-gray-50 p-0.5">
      <button
        type="button"
        onClick={() => onChange('preview')}
        className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${mode === 'preview' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
      >
        <Eye className="h-3.5 w-3.5" />
        {t('files.preview.previewMode')}
      </button>
      <button
        type="button"
        onClick={() => onChange('source')}
        className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${mode === 'source' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
      >
        <Code2 className="h-3.5 w-3.5" />
        {t('files.preview.sourceMode')}
      </button>
    </div>
  );
}

export function FilePreviewRenderer({
  node,
  content,
  editing,
  editContent,
  truncated,
  previewLimitBytes,
  fileAccess,
  onEditChange,
  onReveal,
}: {
  node: WorkspaceNode;
  content: string | null;
  editing: boolean;
  editContent: string | null;
  truncated: boolean;
  previewLimitBytes: number | null;
  fileAccess: PreviewFileAccess;
  onEditChange: (text: string) => void;
  onReveal?: (node: WorkspaceNode) => void;
}) {
  const { t } = useTranslation('workspace');
  const [mode, setMode] = useState<PreviewMode>('preview');
  const kind = getPreviewKind(node);
  const canToggleSource = ['markdown', 'html', 'json', 'jsonl', 'csv'].includes(kind) && node.is_text_file;

  useEffect(() => {
    setMode('preview');
  }, [node.path]);

  if (editing) {
    return (
      <textarea
        value={editContent ?? ''}
        onChange={(e) => onEditChange(e.target.value)}
        className="h-full w-full resize-none border-none bg-white p-4 text-sm font-mono text-gray-800 outline-none"
        spellCheck={false}
      />
    );
  }

  const showSource = mode === 'source' && content !== null && canToggleSource;

  return (
    <div className="flex h-full flex-col">
      {truncated && (
        <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t('files.truncatedPreview', { limit: formatBytes(previewLimitBytes ?? 0) })}
        </div>
      )}
      {canToggleSource && (
        <div className="flex justify-end border-b border-gray-100 px-4 py-2">
          <PreviewModeToggle mode={mode} onChange={setMode} />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-hidden">
        {showSource ? (
          <SourcePreview content={content} />
        ) : (
          <RenderedPreview node={node} content={content} kind={kind} fileAccess={fileAccess} onReveal={onReveal} />
        )}
      </div>
    </div>
  );
}

export function PreviewModal({
  node,
  content,
  truncated,
  previewLimitBytes,
  fileAccess,
  onClose,
  onReveal,
}: {
  node: WorkspaceNode;
  content: string | null;
  truncated: boolean;
  previewLimitBytes: number | null;
  fileAccess: PreviewFileAccess;
  onClose: () => void;
  onReveal?: (node: WorkspaceNode) => void;
}) {
  const { t } = useTranslation('workspace');
  return (
    <div className="fixed inset-0 z-50 flex bg-black/40 p-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3">
          <span className="text-sm">{fileIcon(node)}</span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-gray-900">{node.name}</span>
          <span className="text-xs text-gray-400">{formatBytes(node.size ?? 0)}</span>
          <span className="text-xs text-gray-400">{formatDate(node.modified_at)}</span>
          <a href={fileAccess.downloadUrl(node.path)} download={node.name} title={t('files.download')} className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <Download className="h-4 w-4" />
          </a>
          {onReveal && (
            <button onClick={() => onReveal(node)} title={t('files.reveal')} className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
              <FolderOpen className="h-4 w-4" />
            </button>
          )}
          <button onClick={onClose} title={t('files.close')} className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1">
          <FilePreviewRenderer
            node={node}
            content={content}
            editing={false}
            editContent={null}
            truncated={truncated}
            previewLimitBytes={previewLimitBytes}
            fileAccess={fileAccess}
            onEditChange={() => undefined}
            onReveal={onReveal}
          />
        </div>
      </div>
    </div>
  );
}

