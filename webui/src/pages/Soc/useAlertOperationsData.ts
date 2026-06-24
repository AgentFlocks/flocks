import { useCallback, useEffect, useState } from 'react';
import {
  defaultSocAlertDataSource,
  socAPI,
  type SocAlertDataSource,
} from '@/api/soc';
import {
  alertAssetSummary,
  type AlertTableColumn,
  incidentClusters as fallbackIncidentClusters,
  type IncidentCluster,
} from './assetAlertData';
import {
  parseThreatCounts,
  readAssetIncidentsFromJsonl,
  resolveAssetRoutePath,
} from './assetAlertAdapter';

export type { AlertTableColumn, IncidentCluster } from './assetAlertData';

const MAX_ASSET_INCIDENTS = 1000;

export interface AlertOperationsSummary {
  sourcePageId: string;
  sourceEndpoint: string;
  sourceAssetDate: string;
  sourceAssetFile: string;
  totalRaw: number;
  totalUnique: number;
  duplicates: number;
  attackSuccess: number;
  attack: number;
  attackFailed: number;
  benign: number;
  unknown: number;
  representativeCount: number;
}

export interface AlertOperationsData {
  schemaVersion: 'soc.alerts.v1';
  generatedAt: string;
  source: {
    type: SocAlertDataSource['type'];
    pageId: string;
    endpoint: string;
    label: string;
    sampleMode: boolean;
  };
  summary: AlertOperationsSummary;
  tableColumns: AlertTableColumn[];
  incidents: IncidentCluster[];
}

const fallbackAlertOperationsData: AlertOperationsData = {
  schemaVersion: 'soc.alerts.v1',
  generatedAt: '',
  source: {
    type: defaultSocAlertDataSource.type,
    pageId: defaultSocAlertDataSource.pageId,
    endpoint: defaultSocAlertDataSource.endpoint,
    label: 'assets 样例',
    sampleMode: true,
  },
  summary: {
    sourcePageId: alertAssetSummary.sourcePageId,
    sourceEndpoint: defaultSocAlertDataSource.endpoint,
    sourceAssetDate: alertAssetSummary.sourceAssetDate,
    sourceAssetFile: alertAssetSummary.sourceAssetFile,
    totalRaw: alertAssetSummary.totalRaw,
    totalUnique: alertAssetSummary.totalUnique,
    duplicates: alertAssetSummary.duplicates,
    attackSuccess: alertAssetSummary.attackSuccess,
    attack: alertAssetSummary.attack,
    attackFailed: alertAssetSummary.attackFailed,
    benign: 0,
    unknown: 0,
    representativeCount: alertAssetSummary.representativeCount,
  },
  tableColumns: [],
  incidents: fallbackIncidentClusters,
};

export function getAlertNeedsReview(summary: AlertOperationsSummary) {
  return summary.attackSuccess + summary.attack;
}

export function useAlertOperationsData(source: SocAlertDataSource = defaultSocAlertDataSource) {
  const [data, setData] = useState<AlertOperationsData>(fallbackAlertOperationsData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await socAPI.getAlertOperationsData(source);
      const normalized = normalizeAlertOperationsData(response.data, source);
      setData(await hydrateAssetIncidents(normalized, response.data));
    } catch (err: unknown) {
      setData(fallbackAlertOperationsData);
      setError(err instanceof Error ? err.message : 'SOC alert data source request failed');
    } finally {
      setLoading(false);
    }
  }, [source]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return {
    data,
    loading,
    error,
    refetch: fetchData,
  };
}

async function hydrateAssetIncidents(data: AlertOperationsData, payload: unknown): Promise<AlertOperationsData> {
  if (!data.source.sampleMode) return data;

  const assetPath = resolveAssetRoutePath(data.summary.sourceAssetFile);
  if (!assetPath) return data;

  try {
    const threatCounts = parseThreatCounts(payload);
    const incidentLimit = Math.max(1, Math.min(data.summary.totalUnique || MAX_ASSET_INCIDENTS, MAX_ASSET_INCIDENTS));
    const { columns, incidents } = await readAssetIncidentsFromJsonl(
      socAPI.getUserDefinedPageAssetUrl(data.source.pageId, assetPath),
      incidentLimit,
      { threatCounts },
    );
    if (incidents.length === 0) return data;

    return {
      ...data,
      summary: {
        ...data.summary,
        sourceAssetFile: `assets/${assetPath}`,
        representativeCount: incidents.length,
      },
      tableColumns: columns,
      incidents,
    };
  } catch {
    return data;
  }
}

export function normalizeAlertOperationsData(
  payload: unknown,
  source: SocAlertDataSource = defaultSocAlertDataSource,
): AlertOperationsData {
  const root = asRecord(payload);
  if (!root) return fallbackAlertOperationsData;

  const standard = normalizeStandardAlertOperations(root, source);
  if (standard) return standard;

  return normalizeUserDefinedPageStats(root, source);
}

function normalizeStandardAlertOperations(
  root: Record<string, unknown>,
  source: SocAlertDataSource,
): AlertOperationsData | null {
  const summaryRecord = asRecord(root.summary);
  if (!summaryRecord) return null;

  const incidents = normalizeIncidents(root.incidents);
  const tableColumns = normalizeTableColumns(root.tableColumns);
  const sourceRecord = asRecord(root.source);
  const pageId = readString(sourceRecord?.pageId, source.pageId);
  const endpoint = readString(sourceRecord?.endpoint, source.endpoint);

  return {
    schemaVersion: 'soc.alerts.v1',
    generatedAt: readString(root.generatedAt, fallbackAlertOperationsData.generatedAt),
    source: {
      type: source.type,
      pageId,
      endpoint,
      label: readString(sourceRecord?.label, 'SOC 告警数据源'),
      sampleMode: readBoolean(sourceRecord?.sampleMode, false),
    },
    summary: {
      sourcePageId: readString(summaryRecord.sourcePageId, pageId),
      sourceEndpoint: readString(summaryRecord.sourceEndpoint, endpoint),
      sourceAssetDate: readString(summaryRecord.sourceAssetDate, alertAssetSummary.sourceAssetDate),
      sourceAssetFile: readString(summaryRecord.sourceAssetFile, alertAssetSummary.sourceAssetFile),
      totalRaw: readNumber(summaryRecord.totalRaw, alertAssetSummary.totalRaw),
      totalUnique: readNumber(summaryRecord.totalUnique, alertAssetSummary.totalUnique),
      duplicates: readNumber(summaryRecord.duplicates, alertAssetSummary.duplicates),
      attackSuccess: readNumber(summaryRecord.attackSuccess, alertAssetSummary.attackSuccess),
      attack: readNumber(summaryRecord.attack, alertAssetSummary.attack),
      attackFailed: readNumber(summaryRecord.attackFailed, alertAssetSummary.attackFailed),
      benign: readNumber(summaryRecord.benign, 0),
      unknown: readNumber(summaryRecord.unknown, 0),
      representativeCount: incidents.length,
    },
    tableColumns,
    incidents,
  };
}

function normalizeUserDefinedPageStats(
  root: Record<string, unknown>,
  source: SocAlertDataSource,
): AlertOperationsData {
  const denoise = asRecord(root.denoise);
  const triage = asRecord(root.triage);
  const sourceStatus = asRecord(root.sourceStatus);
  const dateRange = asRecord(root.dateRange);
  const assets = asRecord(sourceStatus?.assets);
  const fileDates = readStringArray(dateRange?.fileDates);
  const selectedDates = readStringArray(assets?.selectedDates);
  const sourceAssetDate = fileDates[0] ?? selectedDates[0] ?? readString(root.date, alertAssetSummary.sourceAssetDate);
  const sampleFile = readString(sourceStatus?.sampleFile, alertAssetSummary.sourceAssetFile);
  const sampleMode = readBoolean(sourceStatus?.sampleMode, false);

  return {
    schemaVersion: 'soc.alerts.v1',
    generatedAt: readString(root.generatedAt, fallbackAlertOperationsData.generatedAt),
    source: {
      type: source.type,
      pageId: source.pageId,
      endpoint: source.endpoint,
      label: sampleMode ? '自定义页 assets' : '自定义页 API',
      sampleMode,
    },
    summary: {
      sourcePageId: source.pageId,
      sourceEndpoint: source.endpoint,
      sourceAssetDate,
      sourceAssetFile: sampleFile,
      totalRaw: readNumber(denoise?.totalRaw, alertAssetSummary.totalRaw),
      totalUnique: readNumber(denoise?.totalUnique, alertAssetSummary.totalUnique),
      duplicates: readNumber(denoise?.duplicates, alertAssetSummary.duplicates),
      attackSuccess: readNumber(triage?.attackSuccess, alertAssetSummary.attackSuccess),
      attack: readNumber(triage?.attack, alertAssetSummary.attack),
      attackFailed: readNumber(triage?.attackFailed, alertAssetSummary.attackFailed),
      benign: readNumber(triage?.benign, 0),
      unknown: readNumber(triage?.unknown, 0),
      representativeCount: fallbackIncidentClusters.length,
    },
    tableColumns: [],
    incidents: fallbackIncidentClusters,
  };
}

function normalizeTableColumns(value: unknown): AlertTableColumn[] {
  if (!Array.isArray(value)) return [];
  const columns: AlertTableColumn[] = [];
  for (const item of value) {
    const record = asRecord(item);
    const key = readString(record?.key, '');
    if (!key) continue;
    columns.push({
      key,
      label: readString(record?.label, key),
      description: readString(record?.description, ''),
      widthClass: readString(record?.widthClass, ''),
      mono: readBoolean(record?.mono, false),
    });
  }
  return columns;
}

function normalizeIncidents(value: unknown) {
  if (!Array.isArray(value)) return fallbackIncidentClusters;
  const incidents = value.filter(isIncidentCluster).map((incident) => {
    const record = incident as IncidentCluster & { final_report?: unknown; triage_report?: unknown };
    const triageReport = incident.triageReport ?? readString(record.triage_report, readString(record.final_report, ''));
    return triageReport ? { ...incident, triageReport } : incident;
  });
  return incidents.length > 0 ? incidents : fallbackIncidentClusters;
}

function isIncidentCluster(value: unknown): value is IncidentCluster {
  const record = asRecord(value);
  return Boolean(
    record
      && typeof record.id === 'string'
      && typeof record.title === 'string'
      && typeof record.srcIp === 'string'
      && asRecord(record.request)
      && asRecord(record.response)
      && asRecord(record.asset)
      && asRecord(record.conclusion),
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function readNumber(value: unknown, fallback: number) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function readString(value: unknown, fallback: string) {
  return typeof value === 'string' && value.trim() !== '' ? value : fallback;
}

function readBoolean(value: unknown, fallback: boolean) {
  return typeof value === 'boolean' ? value : fallback;
}

function readStringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}
