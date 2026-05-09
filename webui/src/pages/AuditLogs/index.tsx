import { useEffect, useMemo, useState } from 'react';
import { Download, Loader2, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import PageHeader from '@/components/common/PageHeader';
import { useAuth } from '@/contexts/AuthContext';
import { flocksproAuditApi, type AuditEventItem } from '@/api/flocksproAudit';
import { flocksproUsersApi } from '@/api/flocksproUsers';

const PAGE_SIZE = 20;
const EXPORT_PAGE_SIZE = 500;

interface AuditFilters {
  eventType: string;
  actorId: string;
  result: string;
  startAt: string;
  endAt: string;
}

const EMPTY_FILTERS: AuditFilters = {
  eventType: '',
  actorId: '',
  result: '',
  startAt: '',
  endAt: '',
};

function toLocalTimestampOrEmpty(value: string): string | undefined {
  if (!value) return undefined;
  return value.length === 16 ? `${value}:00` : value;
}

function formatLocalTime(value: string): string {
  if (!value) return '-';
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized);
  const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, '0');
  const day = String(parsed.getDate()).padStart(2, '0');
  const hour = String(parsed.getHours()).padStart(2, '0');
  const minute = String(parsed.getMinutes()).padStart(2, '0');
  const second = String(parsed.getSeconds()).padStart(2, '0');
  return `${year}/${month}/${day} ${hour}:${minute}:${second}`;
}

function payloadPreview(item: AuditEventItem): string {
  const data = item.payload ?? item.metadata ?? {};
  const serialized = JSON.stringify(data, null, 2);
  if (!serialized || serialized === '{}') return '-';
  return serialized.length > 260 ? `${serialized.slice(0, 257)}...` : serialized;
}

function payloadFullText(item: AuditEventItem): string {
  const data = item.payload ?? item.metadata ?? {};
  const serialized = JSON.stringify(data, null, 2);
  return !serialized || serialized === '{}' ? '-' : serialized;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function exportFilename(): string {
  const stamp = new Date()
    .toLocaleString('sv-SE', { hour12: false })
    .replace(/[^\d]/g, '')
    .slice(0, 14);
  return `audit_logs_${stamp}.xls`;
}

function parseAuditTime(value: string): number {
  if (!value) return 0;
  const valueTrimmed = value.trim();
  const mmdd = valueTrimmed.match(
    /^(\d{2})\/(\d{2})\/(\d{4})(?:,\s*|\s+)(\d{2}):(\d{2}):(\d{2})$/,
  );
  if (mmdd) {
    const [, month, day, year, hour, minute, second] = mmdd;
    return new Date(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second),
    ).getTime();
  }
  const ymd = valueTrimmed.match(
    /^(\d{4})\/(\d{2})\/(\d{2})(?:,\s*|\s+)(\d{2}):(\d{2}):(\d{2})$/,
  );
  if (ymd) {
    const [, year, month, day, hour, minute, second] = ymd;
    return new Date(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second),
    ).getTime();
  }
  const normalized = valueTrimmed.includes('T') ? valueTrimmed : valueTrimmed.replace(' ', 'T');
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized);
  const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`);
  return Number.isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

function sortByCreatedAtDesc(items: AuditEventItem[]): AuditEventItem[] {
  return [...items].sort((a, b) => {
    const delta = parseAuditTime(b.created_at) - parseAuditTime(a.created_at);
    if (delta !== 0) return delta;
    return (b.id ?? 0) - (a.id ?? 0);
  });
}

function stringFromPayload(item: AuditEventItem, keys: string[]): string | undefined {
  const data = item.payload ?? item.metadata ?? {};
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function actorLabel(item: AuditEventItem): string {
  return (
    item.user_name
    || item.actor_name
    || stringFromPayload(item, ['user_name', 'username', 'actor_name', 'actor_id'])
    || item.user_id
    || item.actor_id
    || '-'
  );
}

function buildAuditQuery(filters: AuditFilters) {
  return {
    event_type: filters.eventType || undefined,
    username: filters.actorId || undefined,
    result: filters.result || undefined,
    start_at: toLocalTimestampOrEmpty(filters.startAt),
    end_at: toLocalTimestampOrEmpty(filters.endAt),
    sort_by: 'created_at',
    order: 'desc' as const,
  };
}

export default function AuditLogsPage() {
  const { t } = useTranslation('flockspro');
  const { user } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<AuditEventItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [eventType, setEventType] = useState('');
  const [actorId, setActorId] = useState('');
  const [result, setResult] = useState('');
  const [startAt, setStartAt] = useState('');
  const [endAt, setEndAt] = useState('');
  const [expandedRowId, setExpandedRowId] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [eventTypeOptions, setEventTypeOptions] = useState<string[]>([]);
  const [checkingCapability, setCheckingCapability] = useState(true);
  const [hasFlocksproCapability, setHasFlocksproCapability] = useState(false);

  const page = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset]);
  const pageCount = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);
  const currentFilters: AuditFilters = {
    eventType,
    actorId,
    result,
    startAt,
    endAt,
  };
  const load = async (nextOffset: number, filters: AuditFilters = currentFilters) => {
    setLoading(true);
    setError(null);
    try {
      const query = buildAuditQuery(filters);
      const response = await flocksproAuditApi.listEvents({
        ...query,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(sortByCreatedAtDesc(response.items));
      setTotal(response.total ?? response.count ?? response.items.length);
      setOffset(nextOffset);
    } catch (err: any) {
      const code = err?.response?.status;
      if (code === 403) {
        setError(t('audit.errors.forbidden'));
      } else {
        setError(err?.response?.data?.message || err?.message || t('audit.errors.fetch'));
      }
    } finally {
      setLoading(false);
    }
  };

  const exportToExcel = async () => {
    setExporting(true);
    setError(null);
    try {
      const query = buildAuditQuery(currentFilters);
      const allItems: AuditEventItem[] = [];
      let nextOffset = 0;
      let expectedTotal: number | null = null;
      while (expectedTotal === null || allItems.length < expectedTotal) {
        const response = await flocksproAuditApi.listEvents({
          ...query,
          limit: EXPORT_PAGE_SIZE,
          offset: nextOffset,
        });
        allItems.push(...response.items);
        expectedTotal = response.total ?? response.count ?? allItems.length;
        if (response.items.length === 0 || response.items.length < EXPORT_PAGE_SIZE) break;
        nextOffset += response.items.length;
      }
      const sortedItems = sortByCreatedAtDesc(allItems);

      const headers = [
        t('audit.table.time'),
        t('audit.table.eventType'),
        t('audit.table.actor'),
        t('audit.table.resource'),
        t('audit.table.result'),
        t('audit.table.payload'),
      ];
      const rows = sortedItems.map((item) => {
        const resource = item.resource_type ? `${item.resource_type}:${item.resource_id || '-'}` : '-';
        return [
          formatLocalTime(item.created_at),
          item.event_type,
          actorLabel(item),
          resource,
          item.result || item.status || '-',
          payloadFullText(item),
        ];
      });
      const html = `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    table { border-collapse: collapse; }
    th, td { border: 1px solid #d1d5db; padding: 6px; mso-number-format:"\\@"; vertical-align: top; }
    th { background: #f3f4f6; font-weight: bold; }
  </style>
</head>
<body>
  <table>
    <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join('')}</tr></thead>
    <tbody>
      ${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(String(cell)).replace(/\n/g, '<br/>')}</td>`).join('')}</tr>`).join('')}
    </tbody>
  </table>
</body>
</html>`;
      const blob = new Blob([html], { type: 'application/vnd.ms-excel;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = exportFilename();
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || t('audit.errors.export'));
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setItems([]);
    setTotal(0);
    setOffset(0);
    setEventTypeOptions([]);
    setHasFlocksproCapability(false);
    if (user?.role !== 'admin') {
      setCheckingCapability(false);
      return () => {
        cancelled = true;
      };
    }

    setCheckingCapability(true);
    void flocksproUsersApi.hasCapability()
      .then((ok) => {
        if (cancelled) return;
        setHasFlocksproCapability(ok);
        if (!ok) {
          setError(t('audit.errors.unavailable'));
        }
      })
      .catch(() => {
        if (cancelled) return;
        setHasFlocksproCapability(false);
        setError(t('audit.errors.unavailable'));
      })
      .finally(() => {
        if (!cancelled) {
          setCheckingCapability(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [t, user?.role]);

  useEffect(() => {
    if (!hasFlocksproCapability) return;
    void load(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasFlocksproCapability]);

  useEffect(() => {
    if (!hasFlocksproCapability) return;
    let cancelled = false;
    void flocksproAuditApi.listEventTypes()
      .then((types) => {
        if (!cancelled) {
          setEventTypeOptions(types);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEventTypeOptions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [hasFlocksproCapability]);

  if (user?.role !== 'admin') {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <p className="text-sm text-red-600">{t('audit.errors.forbidden')}</p>
      </div>
    );
  }

  if (checkingCapability) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <p className="text-sm text-gray-500">{t('audit.loading')}</p>
      </div>
    );
  }

  if (!hasFlocksproCapability) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <p className="text-sm text-red-600">{t('audit.errors.unavailable')}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('audit.title')}
        description={t('audit.description')}
        icon={<ShieldCheck className="w-8 h-8" />}
      />

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <select
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
          >
            <option value="">{t('audit.filters.allEventTypes')}</option>
            {eventType && !eventTypeOptions.includes(eventType) && (
              <option value={eventType}>{eventType}</option>
            )}
            {eventTypeOptions.map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </select>
          <input
            value={actorId}
            onChange={(e) => setActorId(e.target.value)}
            placeholder={t('audit.filters.actor')}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
          <select
            value={result}
            onChange={(e) => setResult(e.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
          >
            <option value="">{t('audit.filters.allResults')}</option>
            <option value="success">{t('audit.result.success')}</option>
            <option value="failed">{t('audit.result.failed')}</option>
          </select>
          <input
            type="datetime-local"
            value={startAt}
            onChange={(e) => setStartAt(e.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
          <input
            type="datetime-local"
            value={endAt}
            onChange={(e) => setEndAt(e.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void load(0)}
            disabled={loading}
            className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm hover:bg-slate-800 disabled:opacity-50"
          >
            {t('audit.actions.search')}
          </button>
          <button
            type="button"
            onClick={() => {
              setEventType('');
              setActorId('');
              setResult('');
              setStartAt('');
              setEndAt('');
              void load(0, EMPTY_FILTERS);
            }}
            disabled={loading}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {t('audit.actions.reset')}
          </button>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-sm text-gray-500">
              {t('audit.total', { total })}
            </span>
            <button
              type="button"
              onClick={() => void exportToExcel()}
              disabled={loading || exporting}
              title={exporting ? t('audit.actions.exporting') : t('audit.actions.exportExcel')}
              aria-label={exporting ? t('audit.actions.exporting') : t('audit.actions.exportExcel')}
              className="rounded-md border border-gray-300 p-2 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full table-fixed text-sm">
            <colgroup>
              <col className="w-40" />
              <col className="w-44" />
              <col className="w-36" />
              <col className="w-36" />
              <col className="w-24" />
              <col />
            </colgroup>
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-200">
                <th className="py-2 pr-4">{t('audit.table.time')}</th>
                <th className="py-2 pr-4">{t('audit.table.eventType')}</th>
                <th className="py-2 pr-4">{t('audit.table.actor')}</th>
                <th className="py-2 pr-4">{t('audit.table.resource')}</th>
                <th className="py-2 pr-4">{t('audit.table.result')}</th>
                <th className="py-2">{t('audit.table.payload')}</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td className="py-4 text-gray-500" colSpan={6}>
                    {loading ? t('audit.loading') : t('audit.empty')}
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const expanded = expandedRowId === item.id;
                const actor = actorLabel(item);
                const resource = item.resource_type ? `${item.resource_type}:${item.resource_id || '-'}` : '-';
                const payload = payloadPreview(item);

                return (
                <tr
                  key={item.id}
                  onClick={() => setExpandedRowId(expanded ? null : item.id)}
                  className="border-b border-gray-100 align-top cursor-pointer hover:bg-slate-50"
                >
                  <td className="py-2 pr-4 whitespace-nowrap">{formatLocalTime(item.created_at)}</td>
                  <td className={`py-2 pr-4 ${expanded ? 'break-words' : 'truncate whitespace-nowrap'}`} title={item.event_type}>
                    {item.event_type}
                  </td>
                  <td
                    className={`py-2 pr-4 ${expanded ? 'break-words' : 'truncate whitespace-nowrap'}`}
                    title={actor}
                  >
                    {actor}
                  </td>
                  <td
                    className={`py-2 pr-4 ${expanded ? 'break-words' : 'truncate whitespace-nowrap'}`}
                    title={resource}
                  >
                    {resource}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">{item.result || item.status}</td>
                  <td
                    className={`py-2 leading-5 ${
                      expanded
                        ? 'whitespace-pre-wrap break-words'
                        : 'truncate whitespace-nowrap'
                    }`}
                    title={payload}
                  >
                    {payload}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between pt-2">
          <span className="text-sm text-gray-500">{t('audit.page', { page, pageCount })}</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
              disabled={loading || offset <= 0}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              {t('audit.actions.prev')}
            </button>
            <button
              type="button"
              onClick={() => void load(offset + PAGE_SIZE)}
              disabled={loading || offset + PAGE_SIZE >= total}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              {t('audit.actions.next')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
