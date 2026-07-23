import { lazy, Suspense, useState, useEffect, useCallback, useMemo, useRef, useReducer } from 'react';
import {
  FolderOpen, Upload, Download, Trash2, Edit3, Save,
  X, ChevronRight, ChevronLeft, ChevronDown, ChevronUp, RefreshCw, FolderPlus,
  Brain, AlertTriangle, Search, ArrowLeft, Maximize2,
  Code2, Eye, ZoomIn, ZoomOut,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import {
  workspaceAPI, WorkspaceNode, formatBytes, formatDate, fileIcon,
} from '@/api/workspace';

// ─── Types ────────────────────────────────────────────────────────────────

type Tab = 'files' | 'memory';
type SortField = 'name' | 'size' | 'modified';
type SortDirection = 'asc' | 'desc';
type PreviewKind = 'markdown' | 'html' | 'json' | 'jsonl' | 'csv' | 'text' | 'image' | 'pdf' | 'unsupported';
type PreviewMode = 'preview' | 'source';

interface PreviewFileAccess {
  previewUrl: (path: string) => string;
  downloadUrl: (path: string) => string;
}

const WORKSPACE_PREVIEW_FILE_ACCESS: PreviewFileAccess = {
  previewUrl: (path) => workspaceAPI.previewUrl(path),
  downloadUrl: (path) => workspaceAPI.downloadUrl(path),
};

const MEMORY_PREVIEW_FILE_ACCESS: PreviewFileAccess = {
  previewUrl: (path) => workspaceAPI.memoryPreviewUrl(path),
  downloadUrl: (path) => workspaceAPI.memoryDownloadUrl(path),
};

const PREVIEW_PANEL_DEFAULT_RATIO = 0.5;
const PREVIEW_PANEL_MIN_WIDTH = 420;
const PREVIEW_PANEL_MIN_LIST_WIDTH = 360;
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

function getViewportWidth(): number {
  return typeof window === 'undefined' ? PREVIEW_PANEL_MIN_WIDTH * 2 : window.innerWidth;
}

function getPreviewPanelMaxWidth(containerWidth = getViewportWidth()): number {
  return Math.max(PREVIEW_PANEL_MIN_WIDTH, containerWidth - PREVIEW_PANEL_MIN_LIST_WIDTH);
}

function getDefaultPreviewPanelWidth(containerWidth = getViewportWidth()): number {
  const targetWidth = Math.floor(containerWidth * PREVIEW_PANEL_DEFAULT_RATIO);
  return Math.min(
    getPreviewPanelMaxWidth(containerWidth),
    Math.max(PREVIEW_PANEL_MIN_WIDTH, targetWidth),
  );
}

interface SortState {
  field: SortField;
  direction: SortDirection;
}

// Preview/edit panel state consolidated into a single object
interface PanelState {
  node: WorkspaceNode | null;
  content: string | null;
  editContent: string | null;
  truncated: boolean;
  previewLimitBytes: number | null;
  editing: boolean;
  saving: boolean;
}

const PANEL_INIT: PanelState = {
  node: null, content: null, editContent: null, truncated: false, previewLimitBytes: null, editing: false, saving: false,
};

type PanelAction =
  | { type: 'select'; node: WorkspaceNode }
  | { type: 'content_loaded'; content: string; truncated?: boolean; previewLimitBytes?: number | null }
  | { type: 'start_edit' }
  | { type: 'edit_change'; text: string }
  | { type: 'save_start' }
  | { type: 'save_done'; content: string }
  | { type: 'cancel_edit' }
  | { type: 'close' };

function panelReducer(state: PanelState, action: PanelAction): PanelState {
  switch (action.type) {
    case 'select':
      return { ...PANEL_INIT, node: action.node };
    case 'content_loaded':
      return {
        ...state,
        content: action.content,
        truncated: action.truncated ?? false,
        previewLimitBytes: action.previewLimitBytes ?? null,
      };
    case 'start_edit':
      return { ...state, editing: true, editContent: state.content ?? '' };
    case 'edit_change':
      return { ...state, editContent: action.text };
    case 'save_start':
      return { ...state, saving: true };
    case 'save_done':
      return { ...state, saving: false, editing: false, editContent: null, content: action.content };
    case 'cancel_edit':
      return { ...state, editing: false, editContent: null };
    case 'close':
      return PANEL_INIT;
    default:
      return state;
  }
}

// ─── Main Page ────────────────────────────────────────────────────────────

export default function WorkspacePage() {
  const [activeTab, setActiveTab] = useState<Tab>('files');
  const { t } = useTranslation('workspace');

  return (
    <div className="flex h-[calc(100vh-3rem)] min-h-[560px] flex-col">
      <PageHeader
        title="Workspace"
        description={t('description')}
        icon={<FolderOpen className="w-8 h-8" />}
      />

      <div className="flex gap-1 px-1 mb-4 border-b border-gray-200">
        <TabButton active={activeTab === 'files'} onClick={() => setActiveTab('files')} icon={<FolderOpen className="w-4 h-4" />} label={t('tabs.files')} />
        <TabButton active={activeTab === 'memory'} onClick={() => setActiveTab('memory')} icon={<Brain className="w-4 h-4" />} label={t('tabs.memory')} />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {activeTab === 'files' ? <FilesTab /> : <MemoryTab />}
      </div>
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
        active ? 'border-slate-700 text-slate-800' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function SortHeaderButton({
  label,
  field,
  sort,
  onClick,
  align = 'left',
}: {
  label: string;
  field: SortField;
  sort: SortState;
  onClick: (field: SortField) => void;
  align?: 'left' | 'right';
}) {
  const active = sort.field === field;
  const Icon = sort.direction === 'asc' ? ChevronUp : ChevronDown;

  return (
    <button
      type="button"
      onClick={() => onClick(field)}
      className={`inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-zinc-800 ${
        align === 'right' ? 'justify-end' : 'justify-start'
      }`}
    >
      <span>{label}</span>
      <Icon className={`w-3 h-3 ${active ? 'opacity-100' : 'opacity-0'}`} />
    </button>
  );
}

function fileExtension(name: string): string {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index + 1).toLowerCase() : '';
}

function getPreviewKind(node: WorkspaceNode): PreviewKind {
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

function parseCsv(content: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;

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
      row.push(field);
      field = '';
      continue;
    }

    if ((char === '\n' || char === '\r') && !inQuotes) {
      if (char === '\r' && next === '\n') {
        i += 1;
      }
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
      continue;
    }

    field += char;
  }

  row.push(field);
  if (row.length > 1 || row[0] !== '' || content.endsWith(',')) {
    rows.push(row);
  }
  return rows;
}

function SourcePreview({ content }: { content: string }) {
  return (
    <pre className="h-full overflow-auto bg-white p-4 text-sm font-mono text-gray-700 whitespace-pre-wrap break-words">
      {content}
    </pre>
  );
}

function CsvPreview({ content }: { content: string }) {
  const rows = parseCsv(content);
  if (rows.length === 0) {
    return <SourcePreview content={content} />;
  }

  const [header, ...body] = rows;
  const columnCount = Math.max(...rows.map((row) => row.length));

  return (
    <div className="h-full overflow-auto bg-white">
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
    setPagesToRender((previous) => {
      const next = new Set(previous);
      for (let candidate = Math.max(1, targetPage - PDF_RENDER_WINDOW); candidate <= Math.min(pageCount, targetPage + PDF_RENDER_WINDOW); candidate += 1) {
        next.add(candidate);
      }
      return next;
    });
    pageShellRefs.current.get(targetPage)?.scrollIntoView({ block: 'start' });
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
    setPagesToRender((previous) => {
      let changed = false;
      const next = new Set(previous);
      for (let candidate = Math.max(1, nearestPage - PDF_RENDER_WINDOW); candidate <= Math.min(pageCount, nearestPage + PDF_RENDER_WINDOW); candidate += 1) {
        if (!next.has(candidate)) {
          next.add(candidate);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
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
          {Array.from({ length: pageCount }).map((_, index) => {
            const page = index + 1;
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

function FilePreviewRenderer({
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

function PreviewModal({
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

// ─── Files Tab ────────────────────────────────────────────────────────────

function FilesTab() {
  const { success: toastSuccess, error: toastError } = useToast();
  const confirm = useConfirm();
  const { t } = useTranslation('workspace');

  // Navigation state
  const [loading, setLoading] = useState(true);
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState<WorkspaceNode[]>([]);
  const [sort, setSort] = useState<SortState>({ field: 'name', direction: 'asc' });

  // Preview/edit panel — consolidated into a reducer
  const [panel, dispatchPanel] = useReducer(panelReducer, PANEL_INIT);

  // Upload / new-dir state
  const [uploading, setUploading] = useState(false);
  const [newDir, setNewDir] = useState<{ show: boolean; name: string }>({ show: false, name: '' });
  const [dragOver, setDragOver] = useState(false);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);
  const [previewPanelWidth, setPreviewPanelWidth] = useState(() => getDefaultPreviewPanelWidth());
  const [fileListWidth, setFileListWidth] = useState(900);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const workspaceSplitRef = useRef<HTMLDivElement>(null);
  const fileListRef = useRef<HTMLDivElement>(null);
  const latestDirRequestIdRef = useRef(0);
  const didInitRef = useRef(false);
  const userResizedPreviewRef = useRef(false);

  const loadFileContent = useCallback(async (path: string) => {
    const res = await workspaceAPI.readFile(path);
    dispatchPanel({
      type: 'content_loaded',
      content: res.data.content,
      truncated: res.data.truncated,
      previewLimitBytes: res.data.preview_limit_bytes,
    });
  }, []);

  const loadDir = useCallback(async (path: string, options?: { preservePanel?: boolean }) => {
    const requestId = latestDirRequestIdRef.current + 1;
    latestDirRequestIdRef.current = requestId;
    setLoading(true);
    if (!options?.preservePanel) {
      dispatchPanel({ type: 'close' });
    }
    try {
      const res = await workspaceAPI.list(path);
      if (requestId !== latestDirRequestIdRef.current) {
        return;
      }
      setItems(Array.isArray(res.data) ? res.data : []);
      setCurrentPath(path);
    } catch (e: any) {
      if (requestId !== latestDirRequestIdRef.current) {
        return;
      }
      toastError(t('files.toast.loadDirFailed'), e?.response?.data?.detail ?? e.message);
    } finally {
      if (requestId === latestDirRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [t, toastError]);

  useEffect(() => {
    if (didInitRef.current) {
      return;
    }
    didInitRef.current = true;
    loadDir('');
  }, [loadDir]);

  useEffect(() => {
    const node = fileListRef.current;
    if (!node) return;

    if (node.clientWidth > 0) {
      setFileListWidth(node.clientWidth);
    }
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) {
        setFileListWidth(entry.contentRect.width);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = workspaceSplitRef.current;
    if (!node) return;

    const syncPreviewWidth = (containerWidth: number) => {
      if (containerWidth <= 0) return;
      const maxWidth = getPreviewPanelMaxWidth(containerWidth);
      setPreviewPanelWidth((current) => {
        if (userResizedPreviewRef.current) {
          return Math.min(maxWidth, Math.max(PREVIEW_PANEL_MIN_WIDTH, current));
        }
        return getDefaultPreviewPanelWidth(containerWidth);
      });
    };

    syncPreviewWidth(node.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(([entry]) => {
      syncPreviewWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const handleSelectNode = useCallback(async (node: WorkspaceNode) => {
    if (node.type === 'directory') {
      loadDir(node.path);
      return;
    }
    dispatchPanel({ type: 'select', node });
    if (node.is_text_file) {
      try {
        await loadFileContent(node.path);
      } catch (e: any) {
        toastError(t('files.toast.readFileFailed'), e?.response?.data?.detail ?? e.message);
      }
    }
  }, [loadDir, loadFileContent, toastError, t]);

  const handleRefresh = useCallback(async () => {
    await loadDir(currentPath, { preservePanel: true });

    if (panel.node?.is_text_file) {
      try {
        await loadFileContent(panel.node.path);
      } catch (e: any) {
        toastError(t('files.toast.readFileFailed'), e?.response?.data?.detail ?? e.message);
      }
    }
  }, [currentPath, loadDir, loadFileContent, panel.node, toastError, t]);

  const handleSave = useCallback(async () => {
    if (!panel.node || panel.editContent === null || panel.truncated) return;
    dispatchPanel({ type: 'save_start' });
    try {
      await workspaceAPI.writeFile(panel.node.path, panel.editContent);
      dispatchPanel({ type: 'save_done', content: panel.editContent });
      toastSuccess(t('files.toast.saveSuccess'));
      loadDir(currentPath);
    } catch (e: any) {
      dispatchPanel({ type: 'cancel_edit' });
      toastError(t('files.toast.saveFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [panel.node, panel.editContent, currentPath, loadDir, toastError, toastSuccess, t]);

  const handleDelete = useCallback(async (node: WorkspaceNode) => {
    const ok = await confirm({
      title: t('files.confirm.deleteTitle'),
      description: t('files.confirm.deleteDesc', { name: node.name }),
      confirmText: t('files.confirm.deleteBtn'),
      variant: 'danger',
    });
    if (!ok) return;
    try {
      if (node.type === 'file') {
        await workspaceAPI.deleteFile(node.path);
      } else {
        await workspaceAPI.deleteDir(node.path);
      }
      toastSuccess(t('files.toast.deleteSuccess'));
      if (panel.node?.path === node.path) dispatchPanel({ type: 'close' });
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.deleteFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [confirm, panel.node, currentPath, loadDir, toastError, toastSuccess, t]);

  const handleUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const res = await workspaceAPI.upload(Array.from(files), currentPath);
      const uploaded = res.data.uploaded;
      const errors = uploaded.filter((u) => u.error);
      const ok = uploaded.filter((u) => !u.error);
      if (ok.length > 0) toastSuccess(t('files.toast.uploadSuccess', { count: ok.length }));
      if (errors.length > 0) toastError(t('files.toast.uploadPartialFail', { count: errors.length }), errors.map((e) => e.error).join('; '));
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.uploadFailed'), e?.response?.data?.detail ?? e.message);
    } finally {
      setUploading(false);
    }
  }, [currentPath, loadDir, toastError, toastSuccess, t]);

  const handleCreateDir = useCallback(async () => {
    const name = newDir.name.trim();
    if (!name) return;
    const path = currentPath ? `${currentPath}/${name}` : name;
    try {
      await workspaceAPI.createDir(path);
      setNewDir({ show: false, name: '' });
      loadDir(currentPath);
    } catch (e: any) {
      toastError(t('files.toast.createDirFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [newDir.name, currentPath, loadDir, toastError, t]);

  const handleReveal = useCallback(async (node: WorkspaceNode) => {
    try {
      await workspaceAPI.reveal(node.path);
      toastSuccess(t('files.toast.revealSuccess'));
    } catch (e: any) {
      toastError(t('files.toast.revealFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [toastError, toastSuccess, t]);

  const handleSort = useCallback((field: SortField) => {
    setSort((current) => ({
      field,
      direction: current.field === field && current.direction === 'asc' ? 'desc' : 'asc',
    }));
  }, []);

  const handlePreviewResizeStart = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    userResizedPreviewRef.current = true;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = previewPanelWidth;
    const containerWidth = workspaceSplitRef.current?.clientWidth ?? getViewportWidth();
    const maxWidth = getPreviewPanelMaxWidth(containerWidth);

    event.currentTarget.setPointerCapture(pointerId);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const nextWidth = startWidth - (moveEvent.clientX - startX);
      setPreviewPanelWidth(Math.min(maxWidth, Math.max(PREVIEW_PANEL_MIN_WIDTH, nextWidth)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);
  }, [previewPanelWidth]);

  const sortedItems = useMemo(() => {
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    return [...items].sort((a, b) => {
      let result = 0;
      if (sort.field === 'name') {
        result = collator.compare(a.name, b.name);
      } else if (sort.field === 'size') {
        result = (a.type === 'file' ? (a.size ?? 0) : 0) - (b.type === 'file' ? (b.size ?? 0) : 0);
      } else {
        result = (a.modified_at ?? 0) - (b.modified_at ?? 0);
      }
      if (result === 0) {
        result = collator.compare(a.name, b.name);
      }
      return sort.direction === 'asc' ? result : -result;
    });
  }, [items, sort]);

  const breadcrumbs = currentPath ? ['', ...currentPath.split('/')] : [''];
  const showSizeColumn = fileListWidth >= 560;
  const showModifiedColumn = fileListWidth >= 760;

  return (
    <div ref={workspaceSplitRef} className="flex h-full min-h-0 gap-4">
      {/* File list */}
      <div ref={fileListRef} className="flex h-full min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-1 flex-1 min-w-0 text-sm text-gray-600">
            {breadcrumbs.map((crumb, i) => {
              const path = breadcrumbs.slice(1, i + 1).join('/');
              const isLast = i === breadcrumbs.length - 1;
              return (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <ChevronRight className="w-3 h-3 text-gray-300 flex-shrink-0" />}
                  <button
                    onClick={() => !isLast && loadDir(path)}
                    className={`truncate ${isLast ? 'text-gray-900 font-medium' : 'text-sky-700 hover:underline'}`}
                  >
                    {crumb === '' ? 'workspace' : crumb}
                  </button>
                </span>
              );
            })}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            {currentPath && (
              <button onClick={() => loadDir(currentPath.split('/').slice(0, -1).join('/'))} title={t('files.back')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <ArrowLeft className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={handleRefresh}
              disabled={loading}
              title={t('files.refresh')}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={() => setNewDir({ show: true, name: '' })} title={t('files.newDir')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
              <FolderPlus className="w-4 h-4" />
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              title={t('files.upload')}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
            >
              <Upload className="w-4 h-4" />
            </button>
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => handleUpload(e.target.files)} />
          </div>
        </div>

        {newDir.show && (
          <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-100">
            <FolderPlus className="w-4 h-4 text-slate-600 flex-shrink-0" />
            <input
              autoFocus
              value={newDir.name}
              onChange={(e) => setNewDir((d) => ({ ...d, name: e.target.value }))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateDir();
                if (e.key === 'Escape') setNewDir({ show: false, name: '' });
              }}
              placeholder={t('files.dirNamePlaceholder')}
              className="flex-1 text-sm bg-transparent border-none outline-none text-gray-800"
            />
            <button onClick={handleCreateDir} className="text-xs px-2 py-1 bg-slate-700 text-white rounded hover:bg-slate-800">{t('files.create')}</button>
            <button onClick={() => setNewDir({ show: false, name: '' })} className="text-gray-400 hover:text-gray-600">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        <div
          className={`flex-1 overflow-y-auto relative ${dragOver ? 'ring-2 ring-sky-400 ring-inset bg-sky-50/80' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleUpload(e.dataTransfer.files); }}
        >
          {dragOver && (
            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <div className="flex flex-col items-center gap-2 text-sky-700">
                <Upload className="w-8 h-8" />
                <span className="text-sm font-medium">{t('files.dropHere')}</span>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center h-32"><LoadingSpinner /></div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-gray-400">
              <FolderOpen className="w-8 h-8 mb-2 opacity-40" />
              <p className="text-sm">{t('files.emptyDir')}</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-50 dark:bg-zinc-900/95">
                <tr>
                  <th className="w-8 px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-zinc-500"></th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 dark:text-zinc-500" aria-sort={sort.field === 'name' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <SortHeaderButton label={t('files.columns.name')} field="name" sort={sort} onClick={handleSort} />
                  </th>
                  {showSizeColumn && (
                    <th className="w-24 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500" aria-sort={sort.field === 'size' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <SortHeaderButton label={t('files.columns.size')} field="size" sort={sort} onClick={handleSort} align="right" />
                    </th>
                  )}
                  {showModifiedColumn && (
                    <th className="w-36 px-4 py-2 text-right text-xs font-medium text-gray-500 dark:text-zinc-500" aria-sort={sort.field === 'modified' ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <SortHeaderButton label={t('files.columns.modified')} field="modified" sort={sort} onClick={handleSort} align="right" />
                    </th>
                  )}
                  <th className="w-20"></th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((item) => (
                  <tr
                    key={item.path}
                    onClick={() => handleSelectNode(item)}
                    className={`group border-t border-gray-50 cursor-pointer transition-colors ${
                      panel.node?.path === item.path
                        ? 'bg-slate-100 dark:bg-zinc-800/70'
                        : 'hover:bg-gray-50 dark:hover:bg-zinc-900/70'
                    }`}
                  >
                    <td className="px-4 py-2 text-sm">{fileIcon(item)}</td>
                    <td className="max-w-0 truncate px-2 py-2 font-medium text-gray-800 dark:text-zinc-100">
                      <span className="block truncate">{item.name}</span>
                    </td>
                    {showSizeColumn && (
                      <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-gray-400 dark:text-zinc-500">
                        {item.type === 'file' ? formatBytes(item.size ?? 0) : '—'}
                      </td>
                    )}
                    {showModifiedColumn && (
                      <td className="whitespace-nowrap px-4 py-2 text-right text-xs text-gray-400 dark:text-zinc-500">
                        {formatDate(item.modified_at)}
                      </td>
                    )}
                    <td className="px-2 py-2">
                      <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
                        {item.type === 'file' && (
                          <a href={workspaceAPI.downloadUrl(item.path)} download={item.name} title={t('files.download')} className="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100">
                            <Download className="w-3.5 h-3.5" />
                          </a>
                        )}
                        <button onClick={() => handleReveal(item)} title={t('files.reveal')} className="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100">
                          <FolderOpen className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDelete(item)} title={t('files.delete')} className="p-1 text-gray-400 hover:text-slate-700 rounded hover:bg-slate-100">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {uploading && (
          <div className="px-4 py-2 bg-slate-50 text-sm text-slate-700 flex items-center gap-2 border-t border-slate-100">
            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            {t('files.uploading')}
          </div>
        )}
      </div>

      {/* Right: preview / edit panel */}
      {panel.node && (
        <div
          className="relative flex h-full flex-shrink-0 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white"
          style={{ width: previewPanelWidth, minWidth: PREVIEW_PANEL_MIN_WIDTH }}
        >
          <button
            type="button"
            aria-label={t('files.preview.resize')}
            title={t('files.preview.resize')}
            onPointerDown={handlePreviewResizeStart}
            className="absolute left-0 top-0 z-10 h-full w-2 cursor-col-resize border-l border-transparent transition-colors hover:border-sky-300 hover:bg-sky-50/70 active:border-sky-400 active:bg-sky-100"
          />
          <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100 flex-shrink-0">
            <span className="text-sm flex-shrink-0">{fileIcon(panel.node)}</span>
            <span className="flex-1 text-sm font-medium text-gray-800 truncate">{panel.node.name}</span>
            <div className="flex items-center gap-1 flex-shrink-0">
              {panel.node.is_text_file && !panel.editing && !panel.truncated && (
                <button onClick={() => dispatchPanel({ type: 'start_edit' })} title={t('files.edit')} className="p-1.5 text-gray-400 hover:text-slate-700 hover:bg-slate-100 rounded">
                  <Edit3 className="w-4 h-4" />
                </button>
              )}
              {panel.editing && (
                <>
                  <button onClick={handleSave} disabled={panel.saving} title={t('files.save')} className="p-1.5 text-green-600 hover:bg-green-50 rounded">
                    <Save className="w-4 h-4" />
                  </button>
                  <button onClick={() => dispatchPanel({ type: 'cancel_edit' })} title={t('files.cancel')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                    <X className="w-4 h-4" />
                  </button>
                </>
              )}
              <a href={workspaceAPI.downloadUrl(panel.node.path)} download={panel.node.name} title={t('files.download')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <Download className="w-4 h-4" />
              </a>
              <button onClick={() => handleReveal(panel.node!)} title={t('files.reveal')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <FolderOpen className="w-4 h-4" />
              </button>
              <button onClick={() => setPreviewModalOpen(true)} title={t('files.preview.fullscreen')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <Maximize2 className="w-4 h-4" />
              </button>
              <button onClick={() => dispatchPanel({ type: 'close' })} title={t('files.close')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="px-4 py-1.5 bg-gray-50 border-b border-gray-100 flex gap-4 text-xs text-gray-400 flex-shrink-0">
            <span>{formatBytes(panel.node.size ?? 0)}</span>
            <span>{formatDate(panel.node.modified_at)}</span>
          </div>

          <div className="flex-1 min-h-0 overflow-hidden">
            <FilePreviewRenderer
              node={panel.node}
              content={panel.content}
              editing={panel.editing}
              editContent={panel.editContent}
              truncated={panel.truncated}
              previewLimitBytes={panel.previewLimitBytes}
              fileAccess={WORKSPACE_PREVIEW_FILE_ACCESS}
              onEditChange={(text) => dispatchPanel({ type: 'edit_change', text })}
              onReveal={handleReveal}
            />
          </div>
        </div>
      )}
      {panel.node && previewModalOpen && (
        <PreviewModal
          node={panel.node}
          content={panel.content}
          truncated={panel.truncated}
          previewLimitBytes={panel.previewLimitBytes}
          fileAccess={WORKSPACE_PREVIEW_FILE_ACCESS}
          onClose={() => setPreviewModalOpen(false)}
          onReveal={handleReveal}
        />
      )}
    </div>
  );
}

// ─── Memory Tab ───────────────────────────────────────────────────────────

type MemoryLoadState = 'idle' | 'loading' | 'error';

function MemoryTab() {
  const { error: toastError } = useToast();
  const { t } = useTranslation('workspace');
  const [files, setFiles] = useState<WorkspaceNode[]>([]);
  const [loadState, setLoadState] = useState<MemoryLoadState>('loading');
  const [selected, setSelected] = useState<WorkspaceNode | null>(null);

  // Distinguish "loading content" from "content failed" to avoid
  // the '加载中...' placeholder getting stuck on error.
  const [contentState, setContentState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [content, setContent] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [previewLimitBytes, setPreviewLimitBytes] = useState<number | null>(null);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);

  const [search, setSearch] = useState('');
  const latestMemoryRequestIdRef = useRef(0);

  const load = useCallback(async () => {
    setLoadState('loading');
    try {
      const res = await workspaceAPI.listMemory();
      setFiles(Array.isArray(res.data) ? res.data : []);
      setLoadState('idle');
    } catch (e: any) {
      setLoadState('error');
      toastError(t('memory.loadMemoryFailed'), e?.response?.data?.detail ?? e.message);
    }
  }, [toastError, t]);

  useEffect(() => { load(); }, [load]);

  const handleSelect = async (node: WorkspaceNode) => {
    const requestId = latestMemoryRequestIdRef.current + 1;
    latestMemoryRequestIdRef.current = requestId;
    setSelected(node);
    setPreviewModalOpen(false);
    setContent(null);
    setTruncated(false);
    setPreviewLimitBytes(null);

    if (!node.is_text_file) {
      setContentState('ready');
      return;
    }

    setContentState('loading');
    try {
      const res = await workspaceAPI.readMemoryFile(node.path);
      if (requestId !== latestMemoryRequestIdRef.current) {
        return;
      }
      setContent(res.data.content);
      setTruncated(res.data.truncated ?? false);
      setPreviewLimitBytes(res.data.preview_limit_bytes ?? null);
      setContentState('ready');
    } catch (e: any) {
      if (requestId !== latestMemoryRequestIdRef.current) {
        return;
      }
      setContentState('error');
      toastError(t('memory.readFileFailed'), e?.response?.data?.detail ?? e.message);
    }
  };

  const filtered = files.filter((f) => f.path.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="flex h-full gap-4">
      <div className="w-72 flex-shrink-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-gray-100 flex items-center gap-2">
          <Brain className="w-4 h-4 text-purple-500 flex-shrink-0" />
          <span className="text-sm font-medium text-gray-700">{t('memory.title')}</span>
          <span className="ml-auto text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">{files.length}</span>
          <button onClick={load} title={t('memory.refresh')} className="text-gray-400 hover:text-gray-600">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="px-3 py-2 border-b border-gray-100">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('memory.searchPlaceholder')}
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-300"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loadState === 'loading' ? (
            <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
          ) : loadState === 'error' ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400 text-sm gap-2">
              <AlertTriangle className="w-5 h-5 text-orange-400" />
              <span>{t('memory.loadFailed')}</span>
              <button onClick={load} className="text-xs text-sky-700 hover:underline">{t('memory.retry')}</button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400 text-sm">
              <Brain className="w-6 h-6 mb-1 opacity-40" />
              {files.length === 0 ? t('memory.noFiles') : t('memory.noMatch')}
            </div>
          ) : (
            filtered.map((f) => (
              <button
                key={f.path}
                onClick={() => handleSelect(f)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 border-t border-gray-50 text-left transition-colors ${
                  selected?.path === f.path ? 'bg-purple-50 text-purple-700' : 'hover:bg-gray-50 text-gray-700'
                }`}
              >
                <span className="text-sm flex-shrink-0">{fileIcon(f)}</span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{f.name}</div>
                  <div className="text-xs text-gray-400">{formatDate(f.modified_at)} · {formatBytes(f.size ?? 0)}</div>
                </div>
              </button>
            ))
          )}
        </div>

        <div className="px-3 py-2 border-t border-gray-100 text-xs text-gray-400">
          {t('memory.readOnly')}
        </div>
      </div>

      <div className="flex-1 min-w-0 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-100 flex-shrink-0">
              <span className="text-sm flex-shrink-0">{fileIcon(selected)}</span>
              <span className="flex-1 text-sm font-medium text-gray-800 truncate">{selected.name}</span>
              <span className="text-xs text-gray-400">{formatBytes(selected.size ?? 0)}</span>
              <span className="text-xs text-gray-400">{formatDate(selected.modified_at)}</span>
              <a href={workspaceAPI.memoryDownloadUrl(selected.path)} download={selected.name} title={t('files.download')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <Download className="w-4 h-4" />
              </a>
              <button onClick={() => setPreviewModalOpen(true)} title={t('files.preview.fullscreen')} className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded">
                <Maximize2 className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {contentState === 'loading' && (
                <div className="flex items-center justify-center h-24"><LoadingSpinner /></div>
              )}
              {contentState === 'error' && (
                <div className="flex flex-col items-center justify-center h-24 gap-2 text-gray-400">
                  <AlertTriangle className="w-5 h-5 text-orange-400" />
                  <span className="text-sm">{t('memory.readFailed')}</span>
                  <button onClick={() => handleSelect(selected)} className="text-xs text-sky-700 hover:underline">{t('memory.retry')}</button>
                </div>
              )}
              {contentState === 'ready' && (
                <FilePreviewRenderer
                  node={selected}
                  content={content}
                  editing={false}
                  editContent={null}
                  truncated={truncated}
                  previewLimitBytes={previewLimitBytes}
                  fileAccess={MEMORY_PREVIEW_FILE_ACCESS}
                  onEditChange={() => undefined}
                />
              )}
            </div>
            {previewModalOpen && (
              <PreviewModal
                node={selected}
                content={content}
                truncated={truncated}
                previewLimitBytes={previewLimitBytes}
                fileAccess={MEMORY_PREVIEW_FILE_ACCESS}
                onClose={() => setPreviewModalOpen(false)}
              />
            )}
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-400">
            <Brain className="w-12 h-12 opacity-20" />
            <p className="text-sm">{t('memory.selectPrompt')}</p>
            <p className="text-xs text-center px-8">{t('memory.selectDesc')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
