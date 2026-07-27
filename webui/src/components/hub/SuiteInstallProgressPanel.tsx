import { CheckCircle, Loader2, X, XCircle } from 'lucide-react';
import type {
  HubCatalogEntry,
  HubInstallProgressEvent,
  HubInstallProgressItem,
  HubPluginType,
} from '@/api/hub';

export interface SuiteInstallProgressState {
  id: string;
  name: string;
  nameCn?: string;
  status: 'running' | 'completed' | 'failed';
  total: number;
  items: HubInstallProgressItem[];
  message?: string;
}

const TYPE_LABEL: Record<HubPluginType, string> = {
  skill: 'Skill',
  agent: 'Agent',
  tool: 'Tool',
  device: 'Device',
  workflow: 'Workflow',
  webui: 'WebUI',
  component: 'Scenario Suite',
};

const TYPE_LABEL_CN: Record<HubPluginType, string> = {
  skill: 'Skill',
  agent: 'Agent',
  tool: 'Tool',
  device: '设备',
  workflow: 'Workflow',
  webui: 'WebUI',
  component: '场景套件',
};

const PANEL_TEXT = {
  zh: {
    title: '场景套件安装进度',
    running: '正在安装',
    completed: '安装完成',
    failed: '安装失败',
    progress: '安装进度',
    close: '关闭',
    statuses: {
      pending: '等待中',
      installing: '安装中',
      installed: '已安装',
      skipped: '已跳过',
      failed: '失败',
      completed: '已完成',
    },
  },
  en: {
    title: 'Scenario suite install progress',
    running: 'Installing',
    completed: 'Installed',
    failed: 'Install failed',
    progress: 'Progress',
    close: 'Close',
    statuses: {
      pending: 'Pending',
      installing: 'Installing',
      installed: 'Installed',
      skipped: 'Skipped',
      failed: 'Failed',
      completed: 'Completed',
    },
  },
};

function isZh(language: string) {
  return language.toLowerCase().startsWith('zh');
}

function formatPluginTypeLabel(type: HubPluginType, language: string): string {
  return isZh(language) ? (TYPE_LABEL_CN[type] ?? TYPE_LABEL[type]) : TYPE_LABEL[type];
}

function progressItemKey(item: Pick<HubInstallProgressItem, 'type' | 'id'>) {
  return `${item.type}:${item.id}`;
}

function mergeProgressItem(items: HubInstallProgressItem[], nextItem: HubInstallProgressItem) {
  const nextKey = progressItemKey(nextItem);
  let found = false;
  const nextItems = items.map(item => {
    if (progressItemKey(item) !== nextKey) return item;
    found = true;
    return { ...item, ...nextItem };
  });
  if (!found) nextItems.push(nextItem);
  return nextItems;
}

export function createSuiteInstallProgressState(
  entry: Pick<HubCatalogEntry, 'id' | 'name' | 'nameCn'>,
): SuiteInstallProgressState {
  return {
    id: entry.id,
    name: entry.name,
    nameCn: entry.nameCn,
    status: 'running',
    total: 0,
    items: [],
  };
}

export function applySuiteInstallProgressEvent(
  current: SuiteInstallProgressState | null,
  progress: HubInstallProgressEvent,
): SuiteInstallProgressState {
  const base: SuiteInstallProgressState = current ?? {
    id: progress.id,
    name: progress.name,
    nameCn: progress.nameCn,
    status: 'running',
    total: progress.total,
    items: [],
  };

  if (progress.event === 'start') {
    return {
      id: progress.id,
      name: progress.name,
      nameCn: progress.nameCn,
      status: 'running',
      total: progress.total,
      items: progress.items ?? [],
      message: progress.message,
    };
  }

  if (progress.event === 'item' && progress.item) {
    return {
      ...base,
      total: progress.total || base.total,
      items: mergeProgressItem(base.items, progress.item),
      message: progress.message ?? base.message,
    };
  }

  if (progress.event === 'complete') {
    return {
      ...base,
      status: 'completed',
      total: progress.total || base.total,
      message: progress.message,
    };
  }

  if (progress.event === 'error') {
    return {
      ...base,
      status: 'failed',
      total: progress.total || base.total,
      message: progress.message,
    };
  }

  return base;
}

export function failSuiteInstallProgress(
  current: SuiteInstallProgressState | null,
  entry: Pick<HubCatalogEntry, 'id' | 'name' | 'nameCn'>,
  message: string,
): SuiteInstallProgressState {
  return current
    ? { ...current, status: 'failed', message }
    : {
      ...createSuiteInstallProgressState(entry),
      status: 'failed',
      message,
    };
}

function getSuiteDisplayName(progress: Pick<SuiteInstallProgressState, 'id' | 'name' | 'nameCn'>, language: string) {
  const name = progress.name?.trim();
  const nameCn = progress.nameCn?.trim();
  return isZh(language) ? (nameCn || name || progress.id) : (name || nameCn || progress.id);
}

function getProgressItemDisplayName(item: HubInstallProgressItem, language: string) {
  const name = item.name?.trim();
  const nameCn = item.nameCn?.trim();
  return isZh(language) ? (nameCn || name || item.id) : (name || nameCn || item.id);
}

export default function SuiteInstallProgressPanel({ progress, language, onClose }: {
  progress: SuiteInstallProgressState;
  language: string;
  onClose: () => void;
}) {
  const text = isZh(language) ? PANEL_TEXT.zh : PANEL_TEXT.en;
  const total = progress.total || progress.items.length;
  const settledCount = progress.items.filter(item => (
    item.status === 'installed' || item.status === 'skipped' || item.status === 'failed'
  )).length;
  const percent = total > 0 ? Math.round((settledCount / total) * 100) : 0;
  const title = progress.status === 'completed'
    ? text.completed
    : progress.status === 'failed'
      ? text.failed
      : text.running;

  return (
    <div className="fixed bottom-3 right-3 z-50 w-[280px] max-w-[calc(100vw-1.5rem)] rounded-lg border border-gray-200 bg-white shadow-2xl">
      <div className="px-3 py-2 border-b border-gray-100 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-900">
            {progress.status === 'completed' ? (
              <CheckCircle className="w-3.5 h-3.5 text-green-600" />
            ) : progress.status === 'failed' ? (
              <XCircle className="w-3.5 h-3.5 text-red-600" />
            ) : (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-600" />
            )}
            <span>{text.title}</span>
          </div>
          <div className="mt-0.5 truncate text-[10px] text-gray-500">
            {title}: {getSuiteDisplayName(progress, language)}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label={text.close}
          title={text.close}
          className="shrink-0 rounded-md p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="px-3 py-2">
        <div className="flex items-center justify-between text-[10px] text-gray-500">
          <span>{text.progress}</span>
          <span>{settledCount} / {total}</span>
        </div>
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-gray-100">
          <div
            className={`h-full rounded-full transition-all ${
              progress.status === 'failed' ? 'bg-red-500' : progress.status === 'completed' ? 'bg-green-500' : 'bg-slate-700'
            }`}
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      <div className="max-h-40 overflow-auto px-1.5 pb-1.5">
        {progress.items.map(item => (
          <div
            key={progressItemKey(item)}
            className="grid grid-cols-[0.875rem_1fr_auto] items-center gap-1.5 rounded-md px-1.5 py-1.5 text-[10px] hover:bg-gray-50"
          >
            <InstallProgressStatusIcon status={item.status} />
            <div className="min-w-0">
              <div className="truncate font-medium text-gray-800">{getProgressItemDisplayName(item, language)}</div>
              <div className="truncate text-[9px] text-gray-400">
                {formatPluginTypeLabel(item.type, language)} · {item.id}
              </div>
              {item.message && <div className="mt-0.5 truncate text-[9px] text-gray-500">{item.message}</div>}
            </div>
            <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${
              item.status === 'installed'
                ? 'bg-green-50 text-green-700'
                : item.status === 'failed'
                  ? 'bg-red-50 text-red-700'
                  : item.status === 'skipped'
                    ? 'bg-gray-100 text-gray-500'
                    : item.status === 'installing'
                      ? 'bg-slate-100 text-slate-700'
                      : 'bg-gray-50 text-gray-400'
            }`}>
              {text.statuses[item.status]}
            </span>
          </div>
        ))}
        {progress.items.length === 0 && (
          <div className="px-2 py-4 text-center text-[10px] text-gray-400">{text.statuses.pending}</div>
        )}
      </div>
    </div>
  );
}

function InstallProgressStatusIcon({ status }: { status: HubInstallProgressItem['status'] }) {
  if (status === 'installing') return <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-600" />;
  if (status === 'installed' || status === 'completed') return <CheckCircle className="w-3.5 h-3.5 text-green-600" />;
  if (status === 'failed') return <XCircle className="w-3.5 h-3.5 text-red-600" />;
  if (status === 'skipped') return <CheckCircle className="w-3.5 h-3.5 text-gray-400" />;
  return <span className="ml-1 h-1.5 w-1.5 rounded-full bg-gray-300" />;
}
