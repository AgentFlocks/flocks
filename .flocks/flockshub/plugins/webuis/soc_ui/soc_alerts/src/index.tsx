import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@flocks/webui-contract-sdk';

type Tone = 'red' | 'orange' | 'blue' | 'green' | 'purple' | 'slate';
type FilterKey = '_source_type' | 'net_type' | 'direction' | 'threat_name' | 'threat_type' | 'threat_phase' | 'threat_result' | 'rsp_status_code' | 'sip' | 'dport' | 'dip' | 'req_host' | 'threat_rule_id';
type TimeRangeKey = '15m' | '1h' | '2h' | '24h' | 'today' | '7d' | '30d';
type TimeFilterMode = 'relative' | 'custom';
type TimePanelTab = 'auto' | 'custom';
type RefreshKey = 'off' | '5s' | '15s' | '1m' | '5m' | '1h';
type Translate = (text: string) => string;

interface FilterConfig {
  key: FilterKey;
  label: string;
}

interface ChoiceOption<T extends string> {
  value: T;
  label: string;
}

interface TimeFilterState {
  mode: TimeFilterMode;
  range: TimeRangeKey;
  start: string;
  end: string;
}


interface AlertTableColumn {
  key: string;
  label: string;
  description?: string;
  widthClass?: string;
  mono?: boolean;
}

interface AlertTableCell {
  value: string;
  detail?: string;
  tone?: Tone;
  mono?: boolean;
}

interface IncidentCluster {
  id: string;
  sourceRecordId?: string;
  observedAt?: string;
  rawAlerts?: number;
  confidence?: number;
  priority?: 'P1' | 'P2';
  reportTitle?: string;
  reason?: string;
  owner?: string;
  srcIp?: string;
  ndrRule?: string;
  request?: {
    method?: string;
    host?: string;
    uri?: string;
    payload?: string;
    llmAnalysis?: string;
    evidence?: string[];
  };
  response?: {
    statusCode?: number;
    llmAnalysis?: string;
    evidence?: string[];
    sample?: string;
  };
  srcIntel?: {
    verdict?: string;
    location?: string;
    tags?: string[];
    summary?: string;
  };
  asset?: {
    name?: string;
    business?: string;
    exposure?: string;
    owner?: string;
    criticality?: string;
    context?: string;
  };
  conclusion?: {
    verdict?: string;
    summary?: string;
    recommendation?: string;
  };
  actions?: string[];
  title: string;
  triageReport?: string;
  tableCells?: Record<string, AlertTableCell>;
  overlayVersion?: number;
  manualVerdict?: string;
  analystNote?: string;
}

interface AlertOperationsData {
  schemaVersion?: string;
  generatedAt?: string;
  source?: {
    label?: string;
    pageId?: string;
    sampleMode?: boolean;
    dataSource?: string;
  };
  summary?: {
    sourcePageId?: string;
    sourceAssetDate?: string;
    sourceAssetFile?: string;
    totalRaw?: number;
    totalUnique?: number;
    duplicates?: number;
    attackSuccess?: number;
    attack?: number;
    attackFailed?: number;
    benign?: number;
    unknown?: number;
    representativeCount?: number;
  };
  tableColumns?: AlertTableColumn[];
  incidents?: IncidentCluster[];
}

interface TaggedReport {
  title: string;
  stepCount: number;
  sections: Record<string, string>;
}

interface MarkdownReport {
  title: string;
  sections: Array<[string, string]>;
  steps: Array<[string, string]>;
}

interface TimelineBucket {
  label: string;
  total: number;
  success: number;
  failed: number;
  unknown: number;
}

const REPORT_TAGS = [
  'report_title',
  'report_meta',
  'analysis_steps',
  'triage_conclusion',
  'attack_payload',
  'payload_explanation',
  'response_evidence',
  'key_evidence',
  'disposal_recommendation',
] as const;

const MARKDOWN_ANALYSIS_STEP_TITLES = [
  '日志类型分析',
  '测绘信息',
  '关联漏洞分析',
  '漏洞详情',
  '攻击负载分析',
  '攻击分析结果',
  '威胁情报',
  '情报信息',
];

const MARKDOWN_STEP_ORDER = [
  '日志类型分析',
  '情报信息',
  '测绘信息',
  '告警关联漏洞情报',
  '攻击负载分析',
  '攻击分析结果',
];

const EMPTY_DATA: Required<Pick<AlertOperationsData, 'summary' | 'source' | 'tableColumns' | 'incidents'>> = {
  source: { label: 'SOC Contract SQLite', pageId: 'soc-alerts', sampleMode: false, dataSource: 'sqlite' },
  summary: {
    sourcePageId: 'soc-alerts',
    sourceAssetDate: '-',
    sourceAssetFile: '',
    totalRaw: 0,
    totalUnique: 0,
    duplicates: 0,
    attackSuccess: 0,
    attack: 0,
    attackFailed: 0,
    benign: 0,
    unknown: 0,
    representativeCount: 0,
  },
  tableColumns: [],
  incidents: [],
};

const ALL_FILTER_VALUE = '__all__';

const EN_TEXT: Record<string, string> = {
  '数据源': 'Data Source',
  '协议类型': 'Protocol',
  '流量方向': 'Traffic Direction',
  '威胁名称': 'Threat Name',
  '威胁类型': 'Threat Type',
  '攻击阶段': 'Attack Stage',
  '攻击结果': 'Attack Result',
  '攻击行为': 'Attack Behavior',
  '攻击判定': 'Attack Verdict',
  '响应状态': 'Response Status',
  '源地址': 'Source Address',
  '源端口': 'Source Port',
  '目标地址': 'Destination Address',
  '目标端口': 'Destination Port',
  '规则 ID': 'Rule ID',
  'HTTP Host': 'HTTP Host',
  '请求 URL': 'Request URL',
  '响应码': 'Response Code',
  '事件时间': 'Event Time',
  '全部': 'All',
  '已选 {count} 项': '{count} selected',
  '未知响应': 'Unknown response',
  '空值': 'Empty',
  '最近15分钟': 'Last 15m',
  '最近1小时': 'Last 1h',
  '最近2小时': 'Last 2h',
  '最近24小时': 'Last 24h',
  '今天': 'Today',
  '最近7天': 'Last 7d',
  '最近30天': 'Last 30d',
  '5秒': '5s',
  '15秒': '15s',
  '1分钟': '1m',
  '5分钟': '5m',
  '1小时': '1h',
  '24小时': '24h',
  '关闭': 'Off',
  '自动刷新': 'Auto Refresh',
  '精确时间': 'Exact Time',
  '时间范围': 'Time Range',
  '刷新频率': 'Refresh Rate',
  '开始时间': 'Start Time',
  '结束时间': 'End Time',
  '取消': 'Cancel',
  '确定': 'Apply',
  '至': 'to',
  '日志调查': 'Log Investigation',
  '关闭筛选菜单': 'Close filter menu',
  '查找选择条件': 'Search options',
  '暂无可选值': 'No options',
  '全选': 'Select All',
  '清空': 'Clear',
  '查询': 'Search',
  '重置': 'Reset',
  '请输入源地址、目标地址、HTTP Host、URL、规则 ID 或威胁名称': 'Search source address, destination address, HTTP Host, URL, rule ID, or threat name',
  '收起更多筛选': 'Collapse More Filters',
  '更多筛选条件': 'More Filters',
  '收起查询': 'Collapse Query',
  '展开查询': 'Expand Query',
  '折叠趋势图': 'Collapse timeline',
  '展开趋势图': 'Expand timeline',
  '攻击成功': 'Attack Success',
  '攻击失败': 'Attack Failed',
  '未知': 'Unknown',
  '暂无可展示的告警数据。': 'No alerts to display.',
  '显示 {start}-{end} / {total} 条，每页 {pageSize} 条': 'Showing {start}-{end} / {total}, {pageSize} per page',
  '首页': 'First',
  '上一页': 'Previous',
  '下一页': 'Next',
  '末页': 'Last',
  '待确认': 'Pending',
  '收起': 'Collapse',
  '关闭详情': 'Close details',
  '告警': 'Alert',
  '告警摘要': 'Alert Summary',
  '基础信息': 'Basic Info',
  '请求链路': 'Request Path',
  '关键字段': 'Key Fields',
  '请求信息': 'Request Info',
  '响应信息': 'Response Info',
  '规则与结论': 'Rule & Conclusion',
  '威胁描述': 'Threat Description',
  '原始请求': 'Raw Request',
  '原始响应': 'Raw Response',
  '源信息': 'Source Info',
  '访问信息': 'Access Info',
  '目标信息': 'Destination Info',
  'HTTP 请求': 'HTTP Request',
  'HTTP 响应': 'HTTP Response',
  '请求行': 'Request Line',
  '响应行': 'Response Line',
  '请求体长度': 'Request Body Length',
  '响应体长度': 'Response Body Length',
  'User-Agent': 'User-Agent',
  '源': 'Source',
  '访问': 'Access',
  '目标': 'Destination',
  '当前状态': 'Current Status',
  '详细信息': 'Details',
  '研判结果': 'Triage Result',
  '日志信息': 'Log Info',
  '网络访问': 'Network Access',
  '端口': 'Port',
  '收起分析步骤': 'Collapse analysis steps',
  '展开 {count} 个步骤': 'Expand {count} steps',
  '展开查看': 'View details',
  '{count} 个步骤': '{count} steps',
  '分析步骤': 'Analysis Steps',
  '分析详情': 'Analysis Details',
  '分析报告': 'Analysis Report',
  '报告摘要': 'Report Summary',
  '日志类型分析': 'Log Type Analysis',
  '测绘信息': 'Asset Mapping',
  '关联漏洞分析': 'Related Vulnerability Analysis',
  '漏洞详情': 'Vulnerability Details',
  '攻击负载分析': 'Attack Payload Analysis',
  '攻击 Payload 分析': 'Attack Payload Analysis',
  '攻击分析结果': 'Attack Analysis Result',
  '威胁情报': 'Threat Intelligence',
  '情报信息': 'Intelligence',
  '告警关联漏洞情报': 'Related Vulnerability Intelligence',
  '攻击payload': 'Attack Payload',
  '重要证据': 'Key Evidence',
  '暂无研判摘要。': 'No triage summary.',
  '研判结论': 'Triage Conclusion',
  '请求证据': 'Request Evidence',
  'Payload 解释': 'Payload Explanation',
  '响应分析': 'Response Analysis',
  '关键证据': 'Key Evidence',
  '处置建议': 'Disposition',
  '人工备注': 'Analyst Note',
  '缺少研判结论。': 'Triage conclusion is missing.',
  '复制': 'Copy',
  '下载': 'Download',
  '复制报告': 'Copy report',
  '下载报告': 'Download report',
  '复制成功': 'Copied',
  'SOC 告警数据源请求失败': 'Failed to request SOC alert data source',
  '：': ':',
};

const identityTr: Translate = (text) => text;

function isEnglishLocale() {
  if (typeof window === 'undefined') return false;
  const stored = window.localStorage?.getItem('flocks-language') || '';
  const locale = stored || window.navigator?.language || '';
  return Boolean(locale) && !locale.toLowerCase().replace('_', '-').startsWith('zh');
}

function interpolate(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ''));
}

const BASE_FILTER_CONFIGS: FilterConfig[] = [
  { key: '_source_type', label: '数据源' },
  { key: 'net_type', label: '协议类型' },
  { key: 'direction', label: '流量方向' },
  { key: 'threat_name', label: '威胁名称' },
];

const MORE_FILTER_CONFIGS: FilterConfig[] = [
  { key: 'threat_type', label: '威胁类型' },
  { key: 'threat_phase', label: '攻击阶段' },
  { key: 'threat_result', label: '攻击结果' },
  { key: 'rsp_status_code', label: '响应状态' },
  { key: 'sip', label: '源地址' },
  { key: 'dport', label: '目标端口' },
  { key: 'dip', label: '目标地址' },
  { key: 'req_host', label: 'HTTP Host' },
  { key: 'threat_rule_id', label: '规则 ID' },
];

const FILTER_CONFIGS = [...BASE_FILTER_CONFIGS, ...MORE_FILTER_CONFIGS];

const DEFAULT_FILTER_VALUES: Record<FilterKey, string[]> = {
  _source_type: ['tdp'],
  net_type: ['http'],
  direction: [],
  threat_name: [],
  threat_type: [],
  threat_phase: [],
  threat_result: [],
  rsp_status_code: [],
  sip: [],
  dport: [],
  dip: [],
  req_host: [],
  threat_rule_id: [],
};

const TIME_RANGE_OPTIONS: ChoiceOption<TimeRangeKey>[] = [
  { value: '15m', label: '最近15分钟' },
  { value: '2h', label: '最近2小时' },
  { value: '24h', label: '最近24小时' },
  { value: 'today', label: '今天' },
  { value: '7d', label: '最近7天' },
  { value: '30d', label: '最近30天' },
];

const REFRESH_OPTIONS: ChoiceOption<RefreshKey>[] = [
  { value: '5s', label: '5秒' },
  { value: '15s', label: '15秒' },
  { value: '1m', label: '1分钟' },
  { value: '5m', label: '5分钟' },
  { value: '1h', label: '1小时' },
  { value: 'off', label: '关闭' },
];

const REFRESH_INTERVAL_MS: Record<RefreshKey, number> = {
  off: 0,
  '5s': 5_000,
  '15s': 15_000,
  '1m': 60_000,
  '5m': 300_000,
  '1h': 3_600_000,
};

const DEFAULT_TIME_RANGE: TimeRangeKey = '7d';
const PAGE_SIZE = 50;
const QUERY_LIMIT = 10000;
const ALERT_TABLE_COLUMN_WIDTH = 220;
const TIMELINE_HOUR_MS = 60 * 60 * 1000;

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function textValue(value: unknown, fallback = '') {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function formatNumber(value: number) {
  return value.toLocaleString('zh-CN');
}

function formatTimelineHourLabel(value: number) {
  const tick = new Date(Math.round(value / TIMELINE_HOUR_MS) * TIMELINE_HOUR_MS);
  return `${tick.getMonth() + 1}-${tick.getDate()} ${String(tick.getHours()).padStart(2, '0')}:00`;
}

function pad2(value: number) {
  return String(value).padStart(2, '0');
}

function toLocalInputValue(date: Date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function parseLocalInputValue(value: string) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function startOfToday(now: Date) {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
}

function createRelativeTimeFilter(range: TimeRangeKey = DEFAULT_TIME_RANGE): TimeFilterState {
  const [start, end] = resolveRelativeWindow(range);
  return { mode: 'relative', range, start: toLocalInputValue(start), end: toLocalInputValue(end) };
}

function createCustomTimeFilter(start: Date, end: Date, range: TimeRangeKey = DEFAULT_TIME_RANGE): TimeFilterState {
  return { mode: 'custom', range, start: toLocalInputValue(start), end: toLocalInputValue(end) };
}

function resolveRelativeWindow(range: TimeRangeKey, now = new Date()): [Date, Date] {
  const end = new Date(now);
  if (range === 'today') return [startOfToday(now), end];
  const spans: Record<Exclude<TimeRangeKey, 'today'>, number> = {
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '2h': 2 * 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
    '7d': 7 * 24 * 60 * 60 * 1000,
    '30d': 30 * 24 * 60 * 60 * 1000,
  };
  return [new Date(end.getTime() - spans[range]), end];
}

function resolveTimeWindow(filter: TimeFilterState, now = new Date()): [Date, Date] | null {
  if (filter.mode === 'relative') return resolveRelativeWindow(filter.range, now);
  const start = parseLocalInputValue(filter.start);
  const end = parseLocalInputValue(filter.end);
  if (!start || !end) return null;
  return start <= end ? [start, end] : [end, start];
}

function timeFilterParams(filter: TimeFilterState) {
  const window = resolveTimeWindow(filter);
  if (!window) return {};
  const [start, end] = window;
  return {
    startTime: Math.floor(start.getTime() / 1000),
    endTime: Math.floor(end.getTime() / 1000),
  };
}

function contractFilterParams(filters: Record<FilterKey, string[]>) {
  const activeFilters = Object.fromEntries(
    FILTER_CONFIGS
      .map((config) => [
        config.key,
        (filters[config.key] || []).filter((value) => value && value !== ALL_FILTER_VALUE),
      ] as const)
      .filter(([, values]) => values.length > 0),
  );
  return Object.keys(activeFilters).length ? { filters: activeFilters } : {};
}

function timeFilterLabel(filter: TimeFilterState, tr: Translate = identityTr) {
  if (filter.mode === 'relative') {
    if (filter.range === '1h') return tr('最近1小时');
    return tr(TIME_RANGE_OPTIONS.find((option) => option.value === filter.range)?.label || '最近7天');
  }
  const window = resolveTimeWindow(filter);
  if (!window) return tr('精确时间');
  const [start, end] = window;
  const format = (date: Date) => `${date.getFullYear()}/${pad2(date.getMonth() + 1)}/${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  return `${format(start)} ${tr('至')} ${format(end)}`;
}

function refreshLabel(value: RefreshKey, tr: Translate = identityTr) {
  return tr(REFRESH_OPTIONS.find((option) => option.value === value)?.label || '关闭');
}

function normalizeData(payload: AlertOperationsData): typeof EMPTY_DATA {
  return {
    source: {
      label: textValue(payload.source?.label, EMPTY_DATA.source.label),
      pageId: textValue(payload.source?.pageId, EMPTY_DATA.source.pageId),
      sampleMode: Boolean(payload.source?.sampleMode),
      dataSource: textValue(payload.source?.dataSource, EMPTY_DATA.source.dataSource),
    },
    summary: {
      sourcePageId: textValue(payload.summary?.sourcePageId, EMPTY_DATA.summary.sourcePageId),
      sourceAssetDate: textValue(payload.summary?.sourceAssetDate, EMPTY_DATA.summary.sourceAssetDate),
      sourceAssetFile: textValue(payload.summary?.sourceAssetFile, ''),
      totalRaw: numberValue(payload.summary?.totalRaw),
      totalUnique: numberValue(payload.summary?.totalUnique),
      duplicates: numberValue(payload.summary?.duplicates),
      attackSuccess: numberValue(payload.summary?.attackSuccess),
      attack: numberValue(payload.summary?.attack),
      attackFailed: numberValue(payload.summary?.attackFailed),
      benign: numberValue(payload.summary?.benign),
      unknown: numberValue(payload.summary?.unknown),
      representativeCount: numberValue(payload.summary?.representativeCount),
    },
    tableColumns: Array.isArray(payload.tableColumns) ? payload.tableColumns : [],
    incidents: Array.isArray(payload.incidents) ? payload.incidents : [],
  };
}

function parseTaggedReport(markdown?: string): TaggedReport | null {
  if (!markdown) return null;
  const root = markdown.match(/<triage_report\b[^>]*version=["']soc\.triage\.markdown\.v1["'][^>]*>([\s\S]*?)<\/triage_report>/i);
  if (!root) return null;
  const body = root[1];
  const sections: Record<string, string> = {};
  for (const tag of REPORT_TAGS) {
    const match = body.match(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'i'));
    if (!match) return null;
    sections[tag] = match[1].trim();
  }
  const title = sections.report_title.match(/^#\s+(.+)$/m)?.[1]?.trim() || 'Web日志分析';
  const stepCount = sections.analysis_steps.match(/^###\s+/gm)?.length || (sections.analysis_steps.trim() ? 1 : 0);
  return { title, stepCount, sections };
}

function normalizeMarkdownReportHeading(heading: string) {
  const title = heading.replace(/^\d+[.、]\s*/, '').trim();
  if (/^攻击\s*Payload\s*分析$/i.test(title)) return '攻击负载分析';
  return title;
}

function isMarkdownAnalysisStepTitle(title: string) {
  return MARKDOWN_ANALYSIS_STEP_TITLES.includes(normalizeMarkdownReportHeading(title));
}

function pushMarkdownReportPair(pairs: Array<[string, string]>, title: string, content: string) {
  const cleanContent = content.trim();
  if (!cleanContent) return;
  const existing = pairs.find(([existingTitle]) => existingTitle === title);
  if (existing) {
    existing[1] = [existing[1], cleanContent].filter(Boolean).join('\n\n');
    return;
  }
  pairs.push([title, cleanContent]);
}

function orderedMarkdownAnalysisSteps(rawSteps: Array<[string, string]>) {
  const stepMap = new Map(rawSteps);
  const vulnerabilityContent = [
    stepMap.get('漏洞详情') ? `### 漏洞详情\n${stepMap.get('漏洞详情')}` : '',
    stepMap.get('关联漏洞分析') ? `### 关联漏洞分析\n${stepMap.get('关联漏洞分析')}` : '',
  ].filter(Boolean).join('\n\n');
  const orderedContent = new Map<string, string>([
    ['日志类型分析', stepMap.get('日志类型分析') || ''],
    ['情报信息', stepMap.get('情报信息') || stepMap.get('威胁情报') || ''],
    ['测绘信息', stepMap.get('测绘信息') || ''],
    ['告警关联漏洞情报', vulnerabilityContent],
    ['攻击负载分析', stepMap.get('攻击负载分析') || ''],
    ['攻击分析结果', stepMap.get('攻击分析结果') || ''],
  ]);
  return MARKDOWN_STEP_ORDER
    .map((title) => [title, orderedContent.get(title) || ''] as [string, string])
    .filter(([, content]) => content.trim());
}

function parseMarkdownReport(markdown?: string): MarkdownReport | null {
  if (!markdown || /<triage_report\b/i.test(markdown)) return null;
  const source = markdown.trim();
  if (!source) return null;
  const title = source.match(/^#\s+(.+)$/m)?.[1]?.trim() || '分析报告';
  const withoutTitle = source.replace(/^#\s+.+\n?/, '').trim();
  const headingPattern = /^##\s+(.+)$/gm;
  const matches = Array.from(withoutTitle.matchAll(headingPattern));
  if (!matches.length) {
    return {
      title,
      sections: [['分析报告', withoutTitle.replace(/^---+$/gm, '').trim() || source]],
      steps: [],
    };
  }

  const sections: Array<[string, string]> = [];
  const steps: Array<[string, string]> = [];
  const intro = withoutTitle.slice(0, matches[0].index ?? 0).replace(/^---+$/gm, '').trim();
  if (intro) sections.push(['报告摘要', intro]);

  matches.forEach((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = index + 1 < matches.length ? matches[index + 1].index ?? withoutTitle.length : withoutTitle.length;
    const heading = normalizeMarkdownReportHeading(match[1]);
    const content = withoutTitle.slice(start, end).replace(/^---+$/gm, '').trim();
    if (!content) return;
    if (isMarkdownAnalysisStepTitle(heading)) {
      pushMarkdownReportPair(steps, heading || '分析详情', content);
      return;
    }
    pushMarkdownReportPair(sections, heading || '分析详情', content);
  });

  const orderedSteps = orderedMarkdownAnalysisSteps(steps);
  return sections.length || orderedSteps.length ? { title, sections, steps: orderedSteps } : null;
}

function xmlEscape(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function markdownToPlainText(content: string) {
  return content
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/^```[^\n]*\n?/, '').replace(/```$/, ''))
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^---+$/gm, '')
    .trim();
}

function sanitizeFileName(value: string) {
  return (value || '研判报告')
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, '')
    .slice(0, 80) || '研判报告';
}

function formatFileTime(value: string) {
  const digits = (value || '').replace(/\D/g, '');
  if (digits.length >= 12) return digits.slice(0, 12);
  const now = new Date();
  const pad = (num: number) => String(num).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function exportFileName(title: string, observedAt: string) {
  return `${sanitizeFileName(title)}_${formatFileTime(observedAt)}.docx`;
}

function reportTextLines({
  title,
  observedAt,
  fields,
  summary,
  steps,
  sections,
}: {
  title: string;
  observedAt: string;
  fields: string[][];
  summary: string;
  steps: Array<[string, string]>;
  sections: Array<[string, string]>;
}) {
  const lines: string[] = [title, observedAt, '', '研判结果'];
  fields.forEach(([label, value]) => lines.push(`${label}: ${value || '-'}`));
  lines.push(`研判结论: ${summary || '-'}`);
  if (steps.length) {
    lines.push('', `分析步骤 (${steps.length} 个步骤)`);
    steps.forEach(([stepTitle, content], index) => {
      lines.push(`${index + 1}. ${stepTitle}`);
      lines.push(markdownToPlainText(content) || '-');
    });
  }
  lines.push('', '分析报告');
  sections.forEach(([sectionTitle, content]) => {
    lines.push(sectionTitle);
    lines.push(markdownToPlainText(content) || '-');
  });
  return lines;
}

function reportPlainText(payload: Parameters<typeof reportTextLines>[0]) {
  return reportTextLines(payload).join('\n');
}

function docxParagraph(text: string, style?: 'Title' | 'Heading1' | 'Heading2') {
  const lines = (text || '-').split(/\r?\n/);
  const styleXml = style ? `<w:pPr><w:pStyle w:val="${style}"/></w:pPr>` : '';
  const runs = lines.map((line, index) => {
    const br = index === 0 ? '' : '<w:br/>';
    return `<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/></w:rPr>${br}<w:t xml:space="preserve">${xmlEscape(line)}</w:t></w:r>`;
  }).join('');
  return `<w:p>${styleXml}${runs}</w:p>`;
}

function buildDocxDocumentXml(payload: Parameters<typeof reportTextLines>[0]) {
  const parts: string[] = [
    docxParagraph(payload.title, 'Title'),
    docxParagraph(payload.observedAt),
    docxParagraph('研判结果', 'Heading1'),
  ];
  payload.fields.forEach(([label, value]) => parts.push(docxParagraph(`${label}: ${value || '-'}`)));
  parts.push(docxParagraph(`研判结论: ${payload.summary || '-'}`));
  if (payload.steps.length) {
    parts.push(docxParagraph(`分析步骤 (${payload.steps.length} 个步骤)`, 'Heading1'));
    payload.steps.forEach(([title, content], index) => {
      parts.push(docxParagraph(`${index + 1}. ${title}`, 'Heading2'));
      markdownToPlainText(content).split(/\n{2,}/).forEach((paragraph) => parts.push(docxParagraph(paragraph)));
    });
  }
  parts.push(docxParagraph('分析报告', 'Heading1'));
  payload.sections.forEach(([title, content]) => {
    parts.push(docxParagraph(title, 'Heading2'));
    markdownToPlainText(content).split(/\n{2,}/).forEach((paragraph) => parts.push(docxParagraph(paragraph)));
  });
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    ${parts.join('\n')}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>`;
}

function buildDocxStylesXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="160" w:line="320" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:pPr><w:spacing w:after="240"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:pPr><w:spacing w:before="280" w:after="160"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:pPr><w:spacing w:before="180" w:after="120"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="24"/></w:rPr>
  </w:style>
</w:styles>`;
}

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc ^= bytes[i];
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function u16(value: number) {
  return new Uint8Array([value & 0xff, (value >>> 8) & 0xff]);
}

function u32(value: number) {
  return new Uint8Array([value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff]);
}

function concatBytes(chunks: Uint8Array[]) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const output = new Uint8Array(total);
  let offset = 0;
  chunks.forEach((chunk) => {
    output.set(chunk, offset);
    offset += chunk.length;
  });
  return output;
}

function zipFiles(files: Array<{ name: string; content: string }>) {
  const encoder = new TextEncoder();
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;
  files.forEach((file) => {
    const nameBytes = encoder.encode(file.name);
    const data = encoder.encode(file.content);
    const crc = crc32(data);
    const localHeader = concatBytes([
      u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0), u32(crc), u32(data.length), u32(data.length), u16(nameBytes.length), u16(0), nameBytes,
    ]);
    localParts.push(localHeader, data);
    const centralHeader = concatBytes([
      u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0), u32(crc), u32(data.length), u32(data.length), u16(nameBytes.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset), nameBytes,
    ]);
    centralParts.push(centralHeader);
    offset += localHeader.length + data.length;
  });
  const centralDirectory = concatBytes(centralParts);
  const localData = concatBytes(localParts);
  const end = concatBytes([
    u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length), u32(centralDirectory.length), u32(localData.length), u16(0),
  ]);
  return concatBytes([localData, centralDirectory, end]);
}

function buildDocxBlob(payload: Parameters<typeof reportTextLines>[0]) {
  const files = [
    { name: '[Content_Types].xml', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>' },
    { name: '_rels/.rels', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>' },
    { name: 'word/_rels/document.xml.rels', content: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' },
    { name: 'word/document.xml', content: buildDocxDocumentXml(payload) },
    { name: 'word/styles.xml', content: buildDocxStylesXml() },
  ];
  return new Blob([zipFiles(files)], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
}

function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

function cellValue(incident: IncidentCluster, key: string, fallback = '') {
  return textValue(incident.tableCells?.[key]?.value, fallback);
}

function rawAttackResultValue(incident: IncidentCluster) {
  return cellValue(incident, 'attach_result') || cellValue(incident, 'attack_result') || cellValue(incident, 'threat_result');
}

function verdictBucket(incident: IncidentCluster): 'success' | 'failed' | 'unknown' {
  const rawResult = rawAttackResultValue(incident).toLowerCase();
  const verdict = incident.conclusion?.verdict || '';
  if (rawResult === 'success' || rawResult === 'succeeded' || verdict.includes('成功')) return 'success';
  if (rawResult === 'failed' || rawResult === 'blocked' || verdict.includes('失败')) return 'failed';
  return 'unknown';
}

function attackResultLabel(incident: IncidentCluster, tr: Translate) {
  const bucket = verdictBucket(incident);
  if (bucket === 'success') return tr('攻击成功');
  if (bucket === 'failed') return tr('攻击失败');
  return tr('未知');
}

function attackResultTone(incident: IncidentCluster): Tone {
  const bucket = verdictBucket(incident);
  if (bucket === 'success') return 'red';
  if (bucket === 'failed') return 'green';
  return 'slate';
}

function severityTone(incident: IncidentCluster): Tone {
  if (incident.priority === 'P1' || incident.conclusion?.verdict?.includes('成功')) return 'red';
  if (incident.conclusion?.verdict?.includes('失败')) return 'green';
  return 'orange';
}

function dateFromIncident(incident: IncidentCluster) {
  const value = incident.observedAt || cellValue(incident, 'time');
  if (!value) return null;
  const parsed = new Date(value.replace(' ', 'T'));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function incidentTimeValue(incident: IncidentCluster) {
  return dateFromIncident(incident)?.getTime() ?? 0;
}

function sortIncidentsByTimeDesc(incidents: IncidentCluster[]) {
  return [...incidents].sort((left, right) => incidentTimeValue(right) - incidentTimeValue(left));
}

function displayThreatName(incident: IncidentCluster) {
  const raw = cellValue(incident, 'threat_name', incident.title).trim() || incident.title;
  return raw
    .replace(/^检测到/, '')
    .replace(/工具流量[。.]?$/, '')
    .replace(/[。.]$/, '');
}

function buildTimeline(incidents: IncidentCluster[], timeWindow: [Date, Date] | null = null): TimelineBucket[] {
  const dates = incidents.map(dateFromIncident).filter(Boolean) as Date[];
  if (!dates.length && !timeWindow) {
    return Array.from({ length: 36 }, (_, index) => ({ label: `${String(index).padStart(2, '0')}:00`, total: 0, success: 0, failed: 0, unknown: 0 }));
  }
  const rawStart = timeWindow ? timeWindow[0].getTime() : Math.min(...dates.map((date) => date.getTime()));
  const rawEnd = timeWindow ? timeWindow[1].getTime() : Math.max(...dates.map((date) => date.getTime()));
  const bucketCount = 42;
  const start = Math.floor(rawStart / TIMELINE_HOUR_MS) * TIMELINE_HOUR_MS;
  const minEnd = rawStart + bucketCount * 60 * 1000;
  const end = Math.max(Math.ceil(Math.max(rawEnd, minEnd) / TIMELINE_HOUR_MS) * TIMELINE_HOUR_MS, start + 4 * TIMELINE_HOUR_MS);
  const span = end - start;
  const buckets = Array.from({ length: bucketCount }, (_, index) => {
    return {
      label: formatTimelineHourLabel(start + (span * index) / Math.max(bucketCount - 1, 1)),
      total: 0,
      success: 0,
      failed: 0,
      unknown: 0,
    };
  });
  incidents.forEach((incident) => {
    const date = dateFromIncident(incident);
    if (!date) return;
    const index = Math.min(bucketCount - 1, Math.max(0, Math.floor(((date.getTime() - start) / span) * bucketCount)));
    const category = verdictBucket(incident);
    buckets[index].total += 1;
    buckets[index][category] += 1;
  });
  return buckets;
}

function niceAxisMax(value: number) {
  const safeValue = Math.max(1, Math.ceil(value));
  const targetStep = Math.max(1, safeValue / 2);
  const magnitude = 10 ** Math.floor(Math.log10(targetStep));
  const normalized = targetStep / magnitude;
  const factor = [1, 2, 2.5, 5, 10].find((candidate) => normalized <= candidate) || 10;
  const step = Math.max(1, Math.ceil(factor * magnitude));
  return step * 2;
}

function readFilterValue(incident: IncidentCluster, key: FilterKey) {
  return cellValue(incident, key);
}

function optionText(key: FilterKey, value: string, tr: Translate = identityTr) {
  if (key === 'rsp_status_code') return value || tr('未知响应');
  if (key === '_source_type' || key === 'net_type') return value || 'unknown';
  return value || tr('空值');
}

function optionLabel(key: FilterKey, value: string | string[], tr: Translate = identityTr) {
  if (Array.isArray(value)) {
    if (!value.length) return tr('全部');
    if (value.length === 1) return optionText(key, value[0], tr);
    return interpolate(tr('已选 {count} 项'), { count: value.length });
  }
  if (value === ALL_FILTER_VALUE) return tr('全部');
  return optionText(key, value, tr);
}

function normalized(value: string) {
  return value.trim().toLowerCase();
}

function buildFilterOptions(incidents: IncidentCluster[], key: FilterKey) {
  const counts = new Map<string, number>();
  incidents.forEach((incident) => {
    const value = readFilterValue(incident, key).trim();
    if (!value) return;
    counts.set(value, (counts.get(value) || 0) + 1);
  });
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 80)
    .map(([value]) => value);
}

function matchesSelectedFilters(incident: IncidentCluster, filters: Record<FilterKey, string[]>) {
  return FILTER_CONFIGS.every((config) => {
    const expected = filters[config.key] || [];
    if (!expected.length) return true;
    const actual = normalized(readFilterValue(incident, config.key));
    return expected.some((value) => normalized(value) === actual);
  });
}

function cloneFilters(filters: Record<FilterKey, string[]>) {
  return Object.fromEntries(
    FILTER_CONFIGS.map((config) => [config.key, [...(filters[config.key] || [])]]),
  ) as Record<FilterKey, string[]>;
}

function matchesTimeFilter(incident: IncidentCluster, filter: TimeFilterState) {
  const window = resolveTimeWindow(filter);
  if (!window) return true;
  const date = dateFromIncident(incident);
  if (!date) return false;
  const [start, end] = window;
  return start <= date && date <= end;
}

function filterIncident(
  incident: IncidentCluster,
  keyword: string,
  filters: Record<FilterKey, string[]>,
  timeFilter: TimeFilterState,
) {
  if (!matchesTimeFilter(incident, timeFilter)) return false;
  if (!matchesSelectedFilters(incident, filters)) return false;
  if (!keyword.trim()) return true;
  const haystack = [
    incident.title,
    incident.reason,
    incident.srcIp,
    incident.ndrRule,
    incident.request?.host,
    incident.request?.uri,
    incident.asset?.name,
    incident.conclusion?.verdict,
    cellValue(incident, 'sip'),
    cellValue(incident, 'dip'),
    cellValue(incident, 'req_host'),
    cellValue(incident, 'req_http_url'),
    cellValue(incident, 'threat_name'),
    cellValue(incident, 'threat_type'),
    cellValue(incident, 'threat_phase'),
    cellValue(incident, 'threat_rule_id'),
  ].filter(Boolean).join(' ').toLowerCase();
  return haystack.includes(keyword.trim().toLowerCase());
}

function Badge({ children, tone = 'slate' }: { children: React.ReactNode; tone?: Tone }) {
  const classes: Record<Tone, string> = {
    red: 'border-red-200 bg-red-50 text-red-700',
    orange: 'border-orange-200 bg-orange-50 text-orange-700',
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    green: 'border-green-200 bg-green-50 text-green-700',
    purple: 'border-purple-200 bg-purple-50 text-purple-700',
    slate: 'border-slate-200 bg-slate-50 text-slate-700',
  };
  return <span className={`inline-flex items-center rounded border px-2 py-1 text-xs font-medium ${classes[tone]}`}>{children}</span>;
}

function FilterDropdown({
  config,
  value,
  options,
  open,
  onToggle,
  onApply,
  onQuery,
  tr,
}: {
  config: FilterConfig;
  value: string[];
  options: string[];
  open: boolean;
  onToggle: () => void;
  onApply: (value: string[]) => void;
  onQuery: (value: string[]) => void;
  tr: Translate;
}) {
  const [search, setSearch] = useState('');
  const [draft, setDraft] = useState<string[]>(value);

  useEffect(() => {
    if (open) {
      setDraft(value);
      setSearch('');
    }
  }, [open, value]);

  const choices = options.filter((option) => option && option !== ALL_FILTER_VALUE);
  const visibleChoices = choices.filter((choice) => optionText(config.key, choice, tr).toLowerCase().includes(search.trim().toLowerCase()));
  const selected = new Set(draft.map(normalized));

  const toggleChoice = (choice: string) => {
    const choiceKey = normalized(choice);
    setDraft((current) => current.some((item) => normalized(item) === choiceKey)
      ? current.filter((item) => normalized(item) !== choiceKey)
      : [...current, choice]);
  };

  return (
    <div className="relative" data-soc-menu-root="true" style={{ zIndex: open ? 40 : 1 }}>
      <button
        type="button"
        onClick={onToggle}
        className={`flex h-10 w-full items-center justify-between rounded border bg-white px-3 text-left text-sm text-slate-600 transition ${open ? 'border-blue-500 ring-4 ring-blue-100' : 'border-slate-300 hover:border-slate-400'}`}
      >
        <span className="min-w-0 truncate">{tr(config.label)}{tr('：')}<b className="font-medium text-blue-600">{optionLabel(config.key, value, tr)}</b></span>
        <span className="ml-2 shrink-0 text-slate-400">{open ? '⌃' : '⌄'}</span>
      </button>
      {open && (
        <div className="absolute left-0 top-[calc(100%+8px)] w-full rounded-md border border-slate-200 bg-white shadow-xl" style={{ zIndex: 41 }}>
          <div className="p-4">
            <label className="flex h-10 items-center gap-2 rounded border border-slate-300 bg-white px-3 text-sm text-slate-500">
              <span className="text-lg leading-none">⌕</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="min-w-0 flex-1 border-0 bg-transparent outline-none placeholder:text-slate-400"
                placeholder={tr('查找选择条件')}
              />
            </label>
            <div className="mt-3 max-h-[200px] overflow-y-auto overscroll-contain pr-1">
              {visibleChoices.map((choice) => {
                const checked = selected.has(normalized(choice));
                return (
                  <button
                    key={choice}
                    type="button"
                    role="checkbox"
                    aria-checked={checked}
                    onClick={() => toggleChoice(choice)}
                    className="flex h-10 w-full items-center gap-3 rounded px-1 text-left text-sm text-slate-700 hover:bg-slate-50"
                  >
                    <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${checked ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-300 bg-white'}`}>{checked ? '✓' : ''}</span>
                    <span className="min-w-0 truncate">{optionText(config.key, choice, tr)}</span>
                  </button>
                );
              })}
              {!visibleChoices.length && <div className="py-8 text-center text-sm text-slate-400">{tr('暂无可选值')}</div>}
            </div>
          </div>
          <div className="flex items-center justify-between border-t border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center gap-6 text-sm">
              <button type="button" onClick={() => setDraft(choices)} className="text-blue-600 hover:text-blue-700">{tr('全选')}</button>
              <button type="button" onClick={() => setDraft([])} className="text-slate-600 hover:text-slate-900">{tr('清空')}</button>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => onApply(draft)} className="rounded border border-slate-300 bg-white px-5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">{tr('确定')}</button>
              <button type="button" onClick={() => onQuery(draft)} className="rounded bg-[#303a65] px-5 py-2 text-sm font-medium text-white hover:bg-[#263052]">{tr('查询')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ChoiceDropdown<T extends string>({
  label,
  value,
  options,
  open,
  onToggle,
  onChange,
}: {
  label: string;
  value: T;
  options: ChoiceOption<T>[];
  open: boolean;
  onToggle: () => void;
  onChange: (value: T) => void;
}) {
  const selected = options.find((option) => option.value === value)?.label || value;
  return (
    <div className="relative" data-soc-menu-root="true" style={{ zIndex: open ? 50 : 1 }}>
      <button type="button" onClick={onToggle} className="rounded border border-slate-300 bg-white px-3 py-2 text-slate-600 hover:border-slate-400">
        {label}：<b className="font-medium text-blue-600">{selected}</b>
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] w-40 rounded-md border border-slate-200 bg-white p-1 shadow-lg" style={{ zIndex: 51 }}>
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
              className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${option.value === value ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}
            >
              <span>{option.label}</span>
              {option.value === value && <span className="text-xs">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TimeRefreshPopover({
  value,
  refreshValue,
  open,
  onToggle,
  onApply,
  onClose,
  tr,
}: {
  value: TimeFilterState;
  refreshValue: RefreshKey;
  open: boolean;
  onToggle: () => void;
  onApply: (timeFilter: TimeFilterState, refresh: RefreshKey) => void;
  onClose: () => void;
  tr: Translate;
}) {
  const [tab, setTab] = useState<TimePanelTab>(value.mode === 'custom' ? 'custom' : 'auto');
  const [range, setRange] = useState<TimeRangeKey>(value.range);
  const [refresh, setRefresh] = useState<RefreshKey>(refreshValue);
  const [start, setStart] = useState(value.start);
  const [end, setEnd] = useState(value.end);

  useEffect(() => {
    if (!open) return;
    const window = resolveTimeWindow(value) || resolveRelativeWindow(value.range);
    setTab(value.mode === 'custom' ? 'custom' : 'auto');
    setRange(value.range);
    setRefresh(refreshValue);
    setStart(toLocalInputValue(window[0]));
    setEnd(toLocalInputValue(window[1]));
  }, [open, refreshValue, value]);

  const chooseRange = (next: TimeRangeKey) => {
    const window = resolveRelativeWindow(next);
    setRange(next);
    setStart(toLocalInputValue(window[0]));
    setEnd(toLocalInputValue(window[1]));
  };

  const confirm = () => {
    const nextTimeFilter: TimeFilterState = tab === 'custom'
      ? { mode: 'custom', range, start, end }
      : createRelativeTimeFilter(range);
    onApply(nextTimeFilter, refresh);
  };
  const currentTimeLabel = timeFilterLabel(value, tr);

  const panelStyle = {
    top: 'calc(100% + 8px)',
    width: 'min(360px, calc(100vw - 32px))',
    maxHeight: 'calc(100vh - 160px)',
  };

  const optionClass = (selected: boolean) => (
    `h-7 whitespace-nowrap rounded border px-2 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100 ${
      selected
        ? 'border-blue-600 bg-blue-600 text-white'
        : 'border-transparent bg-slate-50 text-slate-600 hover:bg-slate-100 hover:text-slate-900'
    }`
  );
  const shortcutOptionClass = 'h-7 whitespace-nowrap rounded border border-transparent bg-slate-50 px-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100';

  return (
    <div className="relative" data-soc-menu-root="true" style={{ zIndex: open ? 60 : 1 }}>
      <button
        type="button"
        onClick={onToggle}
        className={`flex min-h-9 max-w-full flex-wrap items-center gap-3 rounded-md border bg-white px-3 py-1.5 text-sm text-slate-700 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100 ${open ? 'border-blue-500' : 'border-slate-300 hover:border-slate-400'}`}
        style={{ maxWidth: 'min(900px, calc(100vw - 48px))' }}
      >
        <span className="whitespace-nowrap" title={currentTimeLabel}>{tr('时间范围')}{tr('：')}<b className="font-medium text-blue-600">{currentTimeLabel}</b></span>
        <span className="h-4 w-px bg-slate-200" />
        <span className="whitespace-nowrap">{tr('刷新频率')}{tr('：')}<b className="font-medium text-blue-600">{refreshLabel(refreshValue, tr)}</b></span>
        <span className="text-slate-500">{open ? '⌃' : '⌄'}</span>
      </button>
      {open && (
        <div className="absolute right-0 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg" style={{ ...panelStyle, zIndex: 61 }}>
          <div className="border-b border-slate-200 px-3 py-2">
            <div className="inline-grid grid-cols-2 gap-1 rounded-md bg-slate-100 p-0.5">
            {[
              ['auto', '自动刷新'],
              ['custom', '精确时间'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key as TimePanelTab)}
                className={`h-7 rounded px-4 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100 ${tab === key ? 'bg-white text-blue-700' : 'text-slate-500 hover:text-slate-800'}`}
              >
                {tr(label)}
              </button>
            ))}
            </div>
          </div>
          <div className="space-y-3 overflow-y-auto px-3 py-3" style={{ maxHeight: 'calc(100vh - 230px)' }}>
            {tab === 'auto' ? (
              <>
                <div>
                  <div className="mb-1.5 text-xs font-medium text-slate-400">{tr('时间范围')}</div>
                  <div className="grid grid-cols-3 gap-1.5">
                    {TIME_RANGE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => chooseRange(option.value)}
                        className={optionClass(range === option.value)}
                      >
                        {tr(option.label)}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="mb-1.5 text-xs font-medium text-slate-400">{tr('刷新频率')}</div>
                  <div className="grid grid-cols-3 gap-1.5">
                    {REFRESH_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setRefresh(option.value)}
                        className={optionClass(refresh === option.value)}
                      >
                        {tr(option.label)}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="mb-1.5 block text-xs font-medium text-slate-400">{tr('开始时间')}</span>
                    <input
                      type="datetime-local"
                      value={start}
                      onChange={(event) => setStart(event.target.value)}
                      className="h-8 w-full rounded border border-slate-300 bg-white px-2 text-xs text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-xs font-medium text-slate-400">{tr('结束时间')}</span>
                    <input
                      type="datetime-local"
                      value={end}
                      onChange={(event) => setEnd(event.target.value)}
                      className="h-8 w-full rounded border border-slate-300 bg-white px-2 text-xs text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </label>
                </div>
                <div className="grid grid-cols-5 gap-1">
                  {[
                    ['1h', '1小时'],
                    ['24h', '24小时'],
                    ['today', '今天'],
                    ['7d', '最近7天'],
                    ['30d', '最近30天'],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => chooseRange(key as TimeRangeKey)}
                      className={shortcutOptionClass}
                    >
                      {tr(label)}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <div className="flex justify-end gap-1.5 border-t border-slate-200 bg-slate-50 px-3 py-2">
            <button type="button" onClick={onClose} className="h-7 rounded border border-slate-300 bg-white px-3 text-xs text-slate-700 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100">{tr('取消')}</button>
            <button type="button" onClick={confirm} className="h-7 rounded bg-blue-600 px-4 text-xs font-medium text-white transition hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100">{tr('确定')}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SocAlertsPage() {
  const english = isEnglishLocale();
  const tr = useCallback((text: string) => (english ? EN_TEXT[text] || text : text), [english]);
  const [data, setData] = useState(EMPTY_DATA);
  const [selectedIncident, setSelectedIncident] = useState<IncidentCluster | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [page, setPage] = useState(1);
  const [selectedFilters, setSelectedFilters] = useState<Record<FilterKey, string[]>>(cloneFilters(DEFAULT_FILTER_VALUES));
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [appliedFilters, setAppliedFilters] = useState<Record<FilterKey, string[]>>(cloneFilters(DEFAULT_FILTER_VALUES));
  const [timeFilter, setTimeFilter] = useState<TimeFilterState>(() => createRelativeTimeFilter(DEFAULT_TIME_RANGE));
  const [appliedTimeFilter, setAppliedTimeFilter] = useState<TimeFilterState>(() => createRelativeTimeFilter(DEFAULT_TIME_RANGE));
  const [refreshKey, setRefreshKey] = useState<RefreshKey>('off');
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const loadSeqRef = useRef(0);

  const load = useCallback(async (activeTimeFilter: TimeFilterState, activeFilters: Record<FilterKey, string[]>) => {
    const loadSeq = loadSeqRef.current + 1;
    loadSeqRef.current = loadSeq;
    setLoading(true);
    setError('');
    try {
      const response = await api
        .contract('soc-alerts', 'soc.alerts.operations')
        .operation<AlertOperationsData>('list', { params: { limit: QUERY_LIMIT, ...timeFilterParams(activeTimeFilter), ...contractFilterParams(activeFilters) } });
      if (loadSeq !== loadSeqRef.current) return;
      setData(normalizeData(response.data));
    } catch (err) {
      if (loadSeq !== loadSeqRef.current) return;
      setError(err instanceof Error ? err.message : tr('SOC 告警数据源请求失败'));
      setData(EMPTY_DATA);
    } finally {
      if (loadSeq === loadSeqRef.current) setLoading(false);
    }
  }, [tr]);

  useEffect(() => {
    void load(appliedTimeFilter, appliedFilters);
  }, [appliedFilters, appliedTimeFilter, load]);

  const filterOptions = useMemo(() => (
    Object.fromEntries(FILTER_CONFIGS.map((config) => [config.key, buildFilterOptions(data.incidents, config.key)])) as Record<FilterKey, string[]>
  ), [data.incidents]);

  const visibleFilterConfigs = showMoreFilters ? FILTER_CONFIGS : BASE_FILTER_CONFIGS;
  const openFilterKey = openMenu?.startsWith('filter:') ? openMenu.slice('filter:'.length) as FilterKey : null;

  const filteredIncidents = useMemo(
    () => data.incidents.filter((incident) => filterIncident(incident, appliedKeyword, appliedFilters, appliedTimeFilter)),
    [appliedFilters, appliedKeyword, appliedTimeFilter, data.incidents],
  );
  const sortedIncidents = useMemo(() => sortIncidentsByTimeDesc(filteredIncidents), [filteredIncidents]);

  useEffect(() => {
    setPage(1);
  }, [appliedFilters, appliedKeyword, appliedTimeFilter, data.incidents.length]);

  useEffect(() => {
    const intervalMs = REFRESH_INTERVAL_MS[refreshKey];
    if (!intervalMs) return undefined;
    const timer = window.setInterval(() => {
      void load(appliedTimeFilter, appliedFilters);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [appliedFilters, appliedTimeFilter, load, refreshKey]);

  useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnOutsidePress = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('[data-soc-menu-root="true"]')) return;
      setOpenMenu(null);
    };
    document.addEventListener('mousedown', closeOnOutsidePress, true);
    document.addEventListener('touchstart', closeOnOutsidePress, true);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsidePress, true);
      document.removeEventListener('touchstart', closeOnOutsidePress, true);
    };
  }, [openMenu]);

  const runQuery = useCallback(() => {
    const nextFilters = cloneFilters(selectedFilters);
    setAppliedKeyword(keyword);
    setAppliedFilters(nextFilters);
    setAppliedTimeFilter(timeFilter);
    setOpenMenu(null);
    setPage(1);
  }, [keyword, selectedFilters, timeFilter]);

  const applyTimeRefresh = useCallback((nextTimeFilter: TimeFilterState, nextRefreshKey: RefreshKey) => {
    setTimeFilter(nextTimeFilter);
    setAppliedTimeFilter(nextTimeFilter);
    setRefreshKey(nextRefreshKey);
    setOpenMenu(null);
    setPage(1);
  }, []);

  const resetQuery = useCallback(() => {
    const defaults = cloneFilters(DEFAULT_FILTER_VALUES);
    const defaultTimeFilter = createRelativeTimeFilter(DEFAULT_TIME_RANGE);
    setKeyword('');
    setSelectedFilters(defaults);
    setAppliedKeyword('');
    setAppliedFilters(cloneFilters(defaults));
    setTimeFilter(defaultTimeFilter);
    setAppliedTimeFilter(defaultTimeFilter);
    setOpenMenu(null);
    setPage(1);
  }, []);

  const pageCount = Math.max(1, Math.ceil(sortedIncidents.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pagedIncidents = sortedIncidents.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const timeline = useMemo(() => buildTimeline(filteredIncidents, resolveTimeWindow(appliedTimeFilter)), [appliedTimeFilter, filteredIncidents]);
  return (
    <div className="relative min-h-full overflow-y-auto bg-[#f5f7fb] text-slate-900" style={{ isolation: 'isolate' }}>
      {openMenu && <button type="button" aria-label={tr('关闭筛选菜单')} className="absolute inset-0 cursor-default" style={{ zIndex: 5 }} onClick={() => setOpenMenu(null)} />}
      <div className="relative border-b border-slate-200 bg-white" style={{ zIndex: 10 }}>
        <div className="px-6 py-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="text-base font-semibold text-slate-950">{tr('日志调查')}</div>
            <div className="flex items-center gap-2 text-sm">
              <TimeRefreshPopover
                value={timeFilter}
                refreshValue={refreshKey}
                open={openMenu === 'timeRefresh'}
                onToggle={() => setOpenMenu(openMenu === 'timeRefresh' ? null : 'timeRefresh')}
                onApply={applyTimeRefresh}
                onClose={() => setOpenMenu(null)}
                tr={tr}
              />
            </div>
          </div>

          {filtersOpen && (
            <div className="grid gap-3 xl:grid-cols-[1fr_auto]">
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  {visibleFilterConfigs.map((config) => (
                    <FilterDropdown
                      key={config.key}
                      config={config}
                      value={selectedFilters[config.key] || []}
                      options={filterOptions[config.key] || []}
                      open={openMenu === `filter:${config.key}`}
                      onToggle={() => setOpenMenu(openMenu === `filter:${config.key}` ? null : `filter:${config.key}`)}
                      onApply={(next) => {
                        setSelectedFilters((current) => ({ ...current, [config.key]: next }));
                        setOpenMenu(null);
                      }}
                      onQuery={(next) => {
                        const nextFilters = cloneFilters({ ...selectedFilters, [config.key]: next });
                        setSelectedFilters(nextFilters);
                        setAppliedKeyword(keyword);
                        setAppliedFilters(cloneFilters(nextFilters));
                        setAppliedTimeFilter(timeFilter);
                        setOpenMenu(null);
                        setPage(1);
                      }}
                      tr={tr}
                    />
                  ))}
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <label className="flex h-10 items-center gap-2 rounded border border-slate-300 bg-white px-3 text-sm text-slate-500">
                    <span className="text-lg leading-none">⌕</span>
                    <input
                      value={keyword}
                      onChange={(event) => setKeyword(event.target.value)}
                      className="min-w-0 flex-1 border-0 bg-transparent outline-none placeholder:text-slate-400"
                      placeholder={tr('请输入源地址、目标地址、HTTP Host、URL、规则 ID 或威胁名称')}
                    />
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-500 text-xs font-bold text-white">?</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      setShowMoreFilters((show) => {
                        const next = !show;
                        if (!next && openFilterKey && MORE_FILTER_CONFIGS.some((config) => config.key === openFilterKey)) setOpenMenu(null);
                        return next;
                      });
                    }}
                    className="flex h-10 items-center justify-center rounded border border-slate-300 bg-white px-3 text-sm text-slate-600 hover:bg-slate-50"
                  >＋ {showMoreFilters ? tr('收起更多筛选') : tr('更多筛选条件')}</button>
                </div>
              </div>
              <div className="flex flex-wrap items-start gap-3 xl:w-[260px]">
                <button
                  type="button"
                  onClick={runQuery}
                  style={{ backgroundColor: '#303a65', borderColor: '#303a65', color: '#ffffff' }}
                  className="inline-flex h-10 min-w-[96px] items-center justify-center gap-2 rounded border px-4 text-sm font-medium transition hover:opacity-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100"
                >
                  <span className="text-lg leading-none">⌕</span>
                  {tr('查询')}
                </button>
                <button
                  type="button"
                  onClick={resetQuery}
                  className="inline-flex h-10 min-w-[96px] items-center justify-center gap-2 rounded border border-slate-300 bg-white px-4 text-sm font-medium text-slate-600 transition hover:border-slate-400 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100"
                >
                  <span className="text-base leading-none">✖</span>
                  {tr('重置')}
                </button>
              </div>
            </div>
          )}

          <div className="mt-3 flex justify-end">
            <button type="button" onClick={() => setFiltersOpen((open) => !open)} className="text-sm text-blue-600 hover:text-blue-700">{filtersOpen ? `⌃ ${tr('收起查询')}` : `⌄ ${tr('展开查询')}`}</button>
          </div>
        </div>
      </div>

      <section className="relative z-0 border-t border-slate-200 bg-white px-6 py-4">
        {timelineOpen && <TimelineChart buckets={timeline} tr={tr} />}
      </section>
      <div className="relative z-10 flex h-8 items-end justify-center border-b border-slate-200 bg-white">
        <button
          type="button"
          onClick={() => setTimelineOpen((open) => !open)}
          className="flex h-7 w-16 items-center justify-center bg-slate-100 text-sm text-slate-500 shadow-[0_1px_0_rgba(148,163,184,0.25)] transition hover:bg-slate-200 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-100"
          style={{ clipPath: 'polygon(9px 0, calc(100% - 9px) 0, 100% 100%, 0 100%)' }}
          aria-label={timelineOpen ? tr('折叠趋势图') : tr('展开趋势图')}
        >
          {timelineOpen ? '⌃' : '⌄'}
        </button>
      </div>

      <section className="relative z-0 bg-white px-6 pb-6 pt-5">
        <div className="flex flex-wrap border-b border-slate-200">
          <div className="min-w-44 border-x border-t-2 border-t-red-500 bg-white px-6 py-3 text-sm font-medium text-slate-950">
            {tr('全部')} ({formatNumber(filteredIncidents.length)})
          </div>
        </div>

        {error && (
          <div className="border-b border-slate-200 px-6 py-3 text-sm text-orange-600">
            {error}
          </div>
        )}

        <IncidentTable
          incidents={pagedIncidents}
          total={filteredIncidents.length}
          page={currentPage}
          pageCount={pageCount}
          onPageChange={setPage}
          selectedIncidentId={selectedIncident?.id}
          onSelect={(incident) => setSelectedIncident((current) => (current?.id === incident.id ? null : incident))}
          onCloseSelected={() => setSelectedIncident(null)}
          tr={tr}
        />
      </section>
    </div>
  );
}

function TimelineChart({ buckets, tr }: { buckets: TimelineBucket[]; tr: Translate }) {
  const [activeBucket, setActiveBucket] = useState<{ bucket: TimelineBucket; x: number; y: number } | null>(null);
  const maxValue = niceAxisMax(Math.max(1, ...buckets.map((bucket) => bucket.total)));
  const midValue = maxValue / 2;
  const labelIndexes = Array.from(new Set([0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.round((buckets.length - 1) * ratio))));
  const chartStep = 920 / Math.max(buckets.length - 1, 1);
  const showBucket = (bucket: TimelineBucket, event: any) => {
    if (!bucket.total) {
      setActiveBucket(null);
      return;
    }
    const rect = event.currentTarget.ownerSVGElement.getBoundingClientRect();
    setActiveBucket({
      bucket,
      x: Math.min(Math.max(event.clientX - rect.left + 12, 48), rect.width - 190),
      y: Math.max(event.clientY - rect.top - 18, 12),
    });
  };
  return (
    <div className="relative h-64">
      <div className="mb-3 flex items-center justify-end gap-4 text-xs text-slate-500">
        <span><i className="mr-1 inline-block h-3 w-1 bg-red-500" />{tr('攻击成功')}</span>
        <span><i className="mr-1 inline-block h-3 w-1 bg-green-500" />{tr('攻击失败')}</span>
        <span><i className="mr-1 inline-block h-3 w-1" style={{ backgroundColor: '#94a3b8' }} />{tr('未知')}</span>
      </div>
      <svg
        viewBox="0 0 1000 170"
        className="h-44 w-full overflow-visible"
        onMouseLeave={() => setActiveBucket(null)}
      >
        {[24, 85, 146].map((y) => (
          <line key={y} x1="42" x2="980" y1={y} y2={y} stroke="#dbe3ef" strokeWidth="1" />
        ))}
        {buckets.map((bucket, index) => {
          const x = 42 + index * (920 / Math.max(buckets.length - 1, 1));
          const successHeight = Math.max(bucket.success ? 2 : 0, (bucket.success / maxValue) * 116);
          const failedHeight = Math.max(bucket.failed ? 2 : 0, (bucket.failed / maxValue) * 116);
          const unknownHeight = Math.max(bucket.unknown ? 2 : 0, (bucket.unknown / maxValue) * 116);
          const failedY = 146 - successHeight - failedHeight;
          const unknownY = failedY - unknownHeight;
          return (
            <g
              key={`${bucket.label}-${index}`}
              onMouseEnter={(event) => showBucket(bucket, event)}
              onMouseMove={(event) => showBucket(bucket, event)}
              onClick={(event) => showBucket(bucket, event)}
              className={bucket.total ? 'cursor-pointer' : undefined}
            >
              {unknownHeight > 0 && <rect x={x - 3} y={unknownY} width="7" height={unknownHeight} fill="#cbd5e1" />}
              {failedHeight > 0 && <rect x={x - 3} y={failedY} width="7" height={failedHeight} fill="#22c55e" />}
              {successHeight > 0 && <rect x={x - 3} y={146 - successHeight} width="7" height={successHeight} fill="#ef4444" />}
              {bucket.total > 0 && <rect x={x - 5} y="20" width="11" height="126" fill="transparent" />}
            </g>
          );
        })}
        <line x1="42" x2="980" y1="146" y2="146" stroke="#475569" strokeWidth="1" />
        <text x="8" y="28" fontSize="11" fill="#475569">{formatNumber(maxValue)}</text>
        <text x="8" y="89" fontSize="11" fill="#475569">{formatNumber(midValue)}</text>
        <text x="22" y="150" fontSize="11" fill="#475569">0</text>
        {labelIndexes.map((bucketIndex, labelIndex) => {
          const bucket = buckets[bucketIndex];
          const anchor = labelIndex === 0 ? 'start' : labelIndex === labelIndexes.length - 1 ? 'end' : 'middle';
          return (
            <text key={`${bucket.label}-label-${bucketIndex}`} x={42 + bucketIndex * chartStep} y="166" fontSize="11" fill="#475569" textAnchor={anchor}>{bucket.label}</text>
          );
        })}
      </svg>
      {activeBucket && (
        <div
          className="pointer-events-none absolute rounded px-3 py-2 text-xs text-white"
          style={{
            left: activeBucket.x,
            top: activeBucket.y,
            zIndex: 30,
            minWidth: 168,
            backgroundColor: '#0f172a',
            border: '1px solid rgba(148, 163, 184, 0.35)',
            boxShadow: '0 18px 40px rgba(15, 23, 42, 0.3)',
            lineHeight: 1.7,
            whiteSpace: 'nowrap',
          }}
        >
          <div className="mb-1 text-slate-300">{activeBucket.bucket.label}</div>
          <div><span className="mr-2 inline-block h-3 w-1 bg-red-500" />{tr('攻击成功')}{tr('：')}{formatNumber(activeBucket.bucket.success)}</div>
          <div><span className="mr-2 inline-block h-3 w-1 bg-green-500" />{tr('攻击失败')}{tr('：')}{formatNumber(activeBucket.bucket.failed)}</div>
          <div><span className="mr-2 inline-block h-3 w-1 bg-slate-300" />{tr('未知')}{tr('：')}{formatNumber(activeBucket.bucket.unknown)}</div>
        </div>
      )}
    </div>
  );
}

function IncidentTable({
  incidents,
  total,
  page,
  pageCount,
  onPageChange,
  selectedIncidentId,
  onSelect,
  onCloseSelected,
  tr,
}: {
  incidents: IncidentCluster[];
  total: number;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  selectedIncidentId?: string;
  onSelect: (incident: IncidentCluster) => void;
  onCloseSelected: () => void;
  tr: Translate;
}) {
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);
  const go = (next: number) => onPageChange(Math.max(1, Math.min(pageCount, next)));

  if (total === 0) {
    return (
      <div className="m-6 rounded border border-dashed border-slate-300 px-4 py-10 text-center text-sm text-slate-500">
        {tr('暂无可展示的告警数据。')}
      </div>
    );
  }

  const headers = ['事件时间', '威胁名称', '威胁类型', '攻击阶段', '攻击结果', '流量方向', '源地址', '源端口', '目标地址', '目标端口', '请求 URL'];
  const tableWidth = headers.length * ALERT_TABLE_COLUMN_WIDTH;
  return (
    <div className="overflow-hidden border border-slate-200 bg-white">
      <div className="overflow-x-auto">
        <table className="table-fixed divide-y divide-slate-200 text-sm" style={{ width: `max(100%, ${tableWidth}px)`, minWidth: tableWidth }}>
          <colgroup>
            {headers.map((header) => (
              <col key={`col-${header}`} style={{ width: ALERT_TABLE_COLUMN_WIDTH }} />
            ))}
          </colgroup>
          <thead className="bg-slate-50 text-xs font-semibold text-slate-500">
            <tr>
              {headers.map((header) => (
                <th key={header} className="px-4 py-3 text-left"><div className="truncate">{tr(header)}</div></th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {incidents.map((incident) => {
              const selected = selectedIncidentId === incident.id;
              const threatName = displayThreatName(incident);
              return (
                <Fragment key={incident.id}>
                  <tr onClick={() => onSelect(incident)} className={`cursor-pointer ${selected ? 'bg-blue-50/70' : 'hover:bg-blue-50/60'}`}>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={incident.observedAt || cellValue(incident, 'time', '-')}>{incident.observedAt || cellValue(incident, 'time', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-800"><div className="truncate font-medium" title={threatName}>{threatName}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'threat_type', '-')}>{cellValue(incident, 'threat_type', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'threat_phase', '-')}>{cellValue(incident, 'threat_phase', '-')}</div></td>
                    <td className="px-4 py-3"><Badge tone={attackResultTone(incident)}>{attackResultLabel(incident, tr)}</Badge></td>
                    <td className="px-4 py-3"><Badge tone="blue">{cellValue(incident, 'direction', '-')}</Badge></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={incident.srcIp || cellValue(incident, 'sip', '-')}>{incident.srcIp || cellValue(incident, 'sip', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'sport', '-')}>{cellValue(incident, 'sport', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'dip', '-')}>{cellValue(incident, 'dip', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'dport', '-')}>{cellValue(incident, 'dport', '-')}</div></td>
                    <td className="px-4 py-3 text-slate-700"><div className="truncate" title={cellValue(incident, 'req_http_url', incident.request?.uri || '-')}>{cellValue(incident, 'req_http_url', incident.request?.uri || '-')}</div></td>
                  </tr>
                  {selected && (
                    <tr className="bg-slate-50">
                      <td colSpan={headers.length} className="p-0">
                        <div className="sticky left-0" style={{ width: 'max(920px, calc(100vw - 340px))' }}>
                          <IncidentInlineDetail incident={incident} onClose={onCloseSelected} tr={tr} />
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white px-6 py-4 text-sm text-slate-600">
        <span>{interpolate(tr('显示 {start}-{end} / {total} 条，每页 {pageSize} 条'), {
          start: formatNumber(rangeStart),
          end: formatNumber(rangeEnd),
          total: formatNumber(total),
          pageSize: PAGE_SIZE,
        })}</span>
        <div className="flex items-center gap-2">
          <button type="button" disabled={page <= 1} onClick={() => go(1)} className="rounded border border-slate-300 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">{tr('首页')}</button>
          <button type="button" disabled={page <= 1} onClick={() => go(page - 1)} className="rounded border border-slate-300 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">{tr('上一页')}</button>
          <span className="px-2 font-medium text-slate-900">{page} / {pageCount}</span>
          <button type="button" disabled={page >= pageCount} onClick={() => go(page + 1)} className="rounded border border-slate-300 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">{tr('下一页')}</button>
          <button type="button" disabled={page >= pageCount} onClick={() => go(pageCount)} className="rounded border border-slate-300 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">{tr('末页')}</button>
        </div>
      </div>
    </div>
  );
}

function IncidentInlineDetail({ incident, onClose, tr }: { incident: IncidentCluster; onClose: () => void; tr: Translate }) {
  const [stepsOpen, setStepsOpen] = useState(true);
  const [activePane, setActivePane] = useState<'detail' | 'triage'>('detail');
  const [copyDone, setCopyDone] = useState(false);
  const report = parseTaggedReport(incident.triageReport);
  const markdownReport = report ? null : parseMarkdownReport(incident.triageReport);
  const attackJudgement = incident.conclusion?.verdict || tr('待确认');
  const attackResult = attackResultLabel(incident, tr);
  const attackResultBucket = verdictBucket(incident);
  const srcAddress = incident.srcIp || cellValue(incident, 'sip', '-');
  const srcPort = cellValue(incident, 'sport', '-');
  const dstAddress = cellValue(incident, 'dip', '-');
  const dstPort = cellValue(incident, 'dport', '-');
  const host = cellValue(incident, 'req_host', incident.request?.host || '-');
  const url = cellValue(incident, 'req_http_url', incident.request?.uri || '-');
  const requestLine = cellValue(incident, 'req_line', '');
  const requestHeader = cellValue(incident, 'req_header', '');
  const requestBody = cellValue(incident, 'req_body', '');
  const responseLine = cellValue(incident, 'rsp_line', '');
  const responseHeader = cellValue(incident, 'rsp_header', '');
  const responseBody = cellValue(incident, 'rsp_body', '');
  const requestText = [requestLine, requestHeader, requestBody].filter(Boolean).join('\n');
  const responseText = [responseLine, responseHeader, responseBody].filter(Boolean).join('\n');
  const ruleId = incident.ndrRule || cellValue(incident, 'threat_rule_id', '-');
  const observedAt = incident.observedAt || cellValue(incident, 'time', '-');
  const threatName = cellValue(incident, 'threat_name', incident.title);
  const threatMessage = cellValue(incident, 'threat_msg', incident.reason || '-');
  const basicFields = [
    ['事件时间', observedAt],
    ['威胁名称', threatName],
    ['威胁类型', cellValue(incident, 'threat_type', '-')],
    ['攻击阶段', cellValue(incident, 'threat_phase', '-')],
    ['攻击行为', attackJudgement],
    ['攻击结果', attackResult],
    ['流量方向', cellValue(incident, 'direction', '-')],
    ['响应码', cellValue(incident, 'rsp_status_code', '-')],
    ['规则 ID', ruleId],
    ['威胁描述', threatMessage],
  ];
  const sourceFields = [
    ['源地址', srcAddress],
    ['源端口', srcPort],
    ['流量方向', cellValue(incident, 'direction', '-')],
    ['数据源', cellValue(incident, '_source_type', '-')],
  ];
  const accessFields = [
    ['HTTP Host', host],
    ['请求 URL', url],
    ['请求行', requestLine || '-'],
    ['User-Agent', cellValue(incident, 'req_user_agent', '-')],
    ['请求体长度', cellValue(incident, 'req_body_len', '-')],
    ['协议类型', cellValue(incident, 'net_type', '-')],
  ];
  const destinationFields = [
    ['目标地址', dstAddress],
    ['目标端口', dstPort],
    ['响应码', cellValue(incident, 'rsp_status_code', '-')],
    ['响应行', responseLine || '-'],
    ['响应体长度', cellValue(incident, 'rsp_body_len', '-')],
    ['规则 ID', ruleId],
  ];
  const triageSummary = incident.conclusion?.summary || incident.reason || tr('暂无研判摘要。');
  const triageFields = [
    ['事件时间', observedAt],
    ['攻击行为', attackJudgement],
    ['攻击结果', attackResult],
    ['威胁名称', threatName],
    ['响应码', cellValue(incident, 'rsp_status_code', '-')],
    ['攻击阶段', cellValue(incident, 'threat_phase', '-')],
    ['规则 ID', ruleId],
  ];
  const reportContent = (...parts: Array<string | undefined>) => parts
    .map((part) => (part || '').trim())
    .filter(Boolean)
    .join('\n\n') || '-';
  const reportSectionMap = new Map(markdownReport?.sections || []);
  const reportStepMap = new Map(markdownReport?.steps || []);
  const markdownReportSection = (title: string) => reportSectionMap.get(title) || '';
  const markdownStepSection = (title: string) => reportStepMap.get(title) || '';
  const markdownRawLog = markdownReportSection('原始日志');
  const triageSections = report ? [
    ['研判结论', report.sections.triage_conclusion],
    ['攻击payload', reportContent(report.sections.attack_payload, report.sections.payload_explanation)],
    ['重要证据', reportContent(report.sections.response_evidence, report.sections.key_evidence)],
    ['处置建议', report.sections.disposal_recommendation],
  ] : markdownReport ? [
    ['研判结论', reportContent(markdownReportSection('分析结果'), markdownReportSection('安全分析报告'), markdownReportSection('报告摘要'), triageSummary)],
    ['攻击payload', reportContent(markdownReportSection('攻击负载分析结果'), markdownStepSection('攻击负载分析'), requestLine || incident.request?.payload || url)],
    ['重要证据', reportContent(markdownReportSection('漏洞情报原始数据'), markdownRawLog || reportContent(requestText, responseText))],
    ['处置建议', incident.conclusion?.recommendation || (incident.actions || []).join('；') || '-'],
  ] : [
    ['研判结论', triageSummary],
    ['攻击payload', requestLine || incident.request?.payload || url || '-'],
    ['重要证据', reportContent(incident.response?.llmAnalysis, responseLine || incident.response?.sample)],
    ['处置建议', incident.conclusion?.recommendation || (incident.actions || []).join('；') || '-'],
  ];
  const reportTitle = incident.reportTitle || report?.title || markdownReport?.title || incident.title;
  const triageDetailTitle = markdownReport?.title || threatName;
  const markdownSteps = markdownReport?.steps || [];
  const stepCount = report?.stepCount || markdownSteps.length;
  const exportPayload = {
    title: reportTitle,
    observedAt,
    fields: triageFields,
    summary: triageSummary,
    steps: report ? [['分析步骤', report.sections.analysis_steps] as [string, string]] : markdownSteps,
    sections: triageSections,
  };
  const handleCopyReport = useCallback(async () => {
    await copyTextToClipboard(reportPlainText(exportPayload));
    setCopyDone(true);
    window.setTimeout(() => setCopyDone(false), 1400);
  }, [exportPayload]);
  const handleDownloadReport = useCallback(() => {
    downloadBlob(buildDocxBlob(exportPayload), exportFileName(reportTitle, observedAt));
  }, [exportPayload, observedAt, reportTitle]);

  return (
    <div className="border-y border-slate-200 bg-slate-50 px-4 py-4">
      <div className="overflow-hidden rounded border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge tone={severityTone(incident)}>{attackJudgement}</Badge>
              <span className="text-xs text-slate-500">{observedAt}</span>
              <span className="text-xs text-slate-300">/</span>
              <span className="max-w-[320px] truncate font-mono text-xs text-slate-500" title={ruleId}>{ruleId}</span>
            </div>
            <div className="truncate text-base font-semibold text-slate-950" title={reportTitle}>
              {reportTitle}
            </div>
            <div className="mt-1 truncate text-xs text-slate-500" title={threatMessage}>
              {threatMessage}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {activePane === 'triage' && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={handleDownloadReport}
                  aria-label={tr('下载报告')}
                  title={tr('下载报告')}
                  className="flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus:outline-none"
                >
                  <span style={{ fontSize: 22, lineHeight: 1 }}>↓</span>
                </button>
                <button
                  type="button"
                  onClick={handleCopyReport}
                  aria-label={copyDone ? tr('复制成功') : tr('复制报告')}
                  title={copyDone ? tr('复制成功') : tr('复制报告')}
                  className="flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus:outline-none"
                >
                  <span style={{ fontSize: copyDone ? 18 : 17, lineHeight: 1 }}>{copyDone ? '✓' : '⧉'}</span>
                </button>
              </div>
            )}
            <div className="rounded bg-slate-100 p-1">
              {[
                ['detail', '详细信息'],
                ['triage', '研判结果'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setActivePane(key as 'detail' | 'triage')}
                  className={`h-8 rounded px-3 text-sm font-medium transition ${
                    activePane === key
                      ? 'bg-white text-blue-600 shadow-sm'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {tr(label)}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={tr('关闭详情')}
              title={tr('关闭详情')}
              className="flex h-8 w-8 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus:outline-none"
            >
              ×
            </button>
          </div>
        </div>

        <div className="overflow-y-auto bg-white px-8 py-6" style={{ maxHeight: 560 }}>
          {activePane === 'detail' ? (
            <div className="bg-white">
              <ArchiveSection title={tr('告警')} value={threatName}>
                <InfoGrid fields={basicFields} tr={tr} monoLabels={['规则 ID']} wideLabels={['威胁描述']} />
              </ArchiveSection>

              <ArchiveSection title={tr('源信息')} value={srcAddress}>
                <InfoGrid fields={sourceFields} tr={tr} monoLabels={['源地址']} />
              </ArchiveSection>

              <ArchiveSection title={tr('访问信息')} value={host}>
                <InfoGrid fields={accessFields} tr={tr} monoLabels={['HTTP Host', '请求 URL', '请求行']} />
              </ArchiveSection>

              <ArchiveSection title={tr('目标信息')} value={dstAddress}>
                <InfoGrid fields={destinationFields} tr={tr} monoLabels={['目标地址', '响应行', '规则 ID']} />
              </ArchiveSection>

              <ArchiveSection title={tr('HTTP 请求')} value={requestLine || url}>
                <PlainTextBlock label={tr('原始请求')} content={requestText || incident.request?.payload || '-'} mono />
              </ArchiveSection>

              <ArchiveSection title={tr('HTTP 响应')} value={responseLine || cellValue(incident, 'rsp_status_code', '-')}>
                <PlainTextBlock label={tr('原始响应')} content={responseText || incident.response?.sample || '-'} mono />
              </ArchiveSection>
            </div>
          ) : (
            <div className="bg-white">
              {stepCount > 0 && (
                <AnalysisStepsCard
                  count={stepCount}
                  open={stepsOpen}
                  onToggle={() => setStepsOpen((open) => !open)}
                  tr={tr}
                >
                  {report ? (
                    <MarkdownText content={report.sections.analysis_steps} compact />
                  ) : (
                    <AnalysisStepTimeline steps={markdownSteps} tr={tr} />
                  )}
                </AnalysisStepsCard>
              )}

              <ArchiveSection title={tr(markdownReport ? '分析报告' : '分析详情')} value={triageDetailTitle}>
                <div
                  className="relative min-w-0"
                  style={{ minHeight: 220 }}
                >
                  <div
                    className="pointer-events-none sticky z-20 ml-auto"
                    style={{ top: 332, width: 260, height: 0 }}
                  >
                    <AttackResultStamp bucket={attackResultBucket} tr={tr} />
                  </div>
                  <div className="relative z-10 grid min-w-0" style={{ rowGap: 22 }}>
                    <TriageConclusionSection title="研判结果" fields={triageFields} summary={triageSummary} tr={tr} />
                    {triageSections.map(([title, content]) => (
                      <TriageTextSection key={title} title={tr(title)} content={content} />
                    ))}
                  </div>
                </div>
              </ArchiveSection>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-sm font-semibold text-slate-950">{title}</div>
      {subtitle && <div className="mt-1 truncate text-xs text-slate-500" title={subtitle}>{subtitle}</div>}
    </div>
  );
}

function ArchiveSection({ title, value, children }: { title: string; value: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-slate-200 last:border-b-0">
      <div className="px-5" style={{ paddingTop: 24, paddingBottom: 30 }}>
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-semibold text-slate-900" style={{ fontSize: 14, lineHeight: '20px' }}>{title}:</span>
          <span className="truncate font-semibold text-slate-800" style={{ fontSize: 14, lineHeight: '20px' }} title={value}>{value || '-'}</span>
        </div>
        <div style={{ marginTop: 26 }}>
          {children}
        </div>
      </div>
    </section>
  );
}

function InfoGrid({ fields, tr, monoLabels = [], wideLabels = [] }: { fields: string[][]; tr: Translate; monoLabels?: string[]; wideLabels?: string[] }) {
  return (
    <div
      className="grid"
      style={{
        gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
        columnGap: 96,
        rowGap: 18,
      }}
    >
      {fields.map(([label, value]) => {
        const mono = monoLabels.includes(label);
        const wide = wideLabels.includes(label);
        return (
          <div
            key={label}
            className="grid min-w-0"
            style={{
              gridColumn: wide ? '1 / -1' : undefined,
              gridTemplateColumns: '170px minmax(0, 1fr)',
              columnGap: 26,
            }}
          >
            <div className="text-slate-400" style={{ fontSize: 13, lineHeight: '20px' }}>{tr(label)}:</div>
            <div
              className={`min-w-0 break-words text-slate-800 ${mono ? 'font-mono' : ''}`}
              style={{ fontSize: mono ? 12 : 13, lineHeight: '20px' }}
              title={value}
            >
              {value || '-'}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PlainTextBlock({ label, content, mono = false }: { label: string; content: string; mono?: boolean }) {
  return (
    <div
      className="mt-7 grid min-w-0"
      style={{ gridTemplateColumns: '170px minmax(0, 1fr)', columnGap: 26 }}
    >
      <div className="text-slate-400" style={{ fontSize: 13, lineHeight: '22px' }}>{label}:</div>
      <div
        className={`max-h-48 overflow-auto whitespace-pre-wrap break-words text-slate-800 ${mono ? 'font-mono' : ''}`}
        style={{ fontSize: mono ? 12 : 13, lineHeight: mono ? '20px' : '22px' }}
      >
        {content || '-'}
      </div>
    </div>
  );
}

function TriageTextSection({ title, content }: { title: string; content: string }) {
  return (
    <section
      className="grid min-w-0"
      style={{ gridTemplateColumns: '170px minmax(0, 1fr)', columnGap: 26 }}
    >
      <div className="font-medium text-slate-400" style={{ fontSize: 13, lineHeight: '22px' }}>{title}:</div>
      <MarkdownText content={content || '-'} compact />
    </section>
  );
}

function TriageConclusionSection({ title, fields, summary, tr }: { title: string; fields: string[][]; summary: string; tr: Translate }) {
  return (
    <section className="grid min-w-0" style={{ rowGap: 16 }}>
      <div className="font-semibold text-slate-950" style={{ fontSize: 15, lineHeight: '24px' }}>
        {tr(title)}
      </div>
      <InfoGrid fields={fields} tr={tr} monoLabels={['规则 ID']} />
      <div
        className="grid min-w-0"
        style={{ gridTemplateColumns: '170px minmax(0, 1fr)', columnGap: 26 }}
      >
        <div className="font-medium text-slate-400" style={{ fontSize: 13, lineHeight: '22px' }}>{tr('研判结论')}:</div>
        <MarkdownText content={summary || '-'} compact />
      </div>
    </section>
  );
}

function AnalysisStepsCard({
  count,
  open,
  onToggle,
  children,
  tr,
}: {
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  tr: Translate;
}) {
  return (
    <section
      className="mb-5 bg-white"
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-4 rounded-lg px-7 text-left transition hover:bg-slate-50 focus:outline-none"
        style={{ minHeight: open ? 62 : 76 }}
      >
        <div className="flex min-w-0 items-baseline gap-5">
          <span className="shrink-0 font-semibold text-slate-950" style={{ fontSize: 17, lineHeight: '24px' }}>
            {tr('分析步骤')}
          </span>
          <span className="text-slate-500" style={{ fontSize: 14, lineHeight: '22px' }}>
            {interpolate(tr('{count} 个步骤'), { count })}
          </span>
        </div>
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
          title={open ? tr('收起') : tr('展开查看')}
          aria-label={open ? tr('收起') : tr('展开查看')}
          style={{ fontSize: 20, transform: open ? 'rotate(180deg)' : undefined }}
        >
          ⌄
        </span>
      </button>
      {open && (
        <div className="px-9 pb-8 pt-4">
          {children}
        </div>
      )}
    </section>
  );
}

function AnalysisStepTimeline({ steps, tr }: { steps: Array<[string, string]>; tr: Translate }) {
  return (
    <div className="grid min-w-0" style={{ paddingLeft: 18, paddingRight: 8 }}>
      {steps.map(([title, content], index) => {
        const isLast = index === steps.length - 1;
        return (
          <div
            key={`${title}-${index}`}
            className="relative grid min-w-0"
            style={{
              gridTemplateColumns: '32px minmax(0, 1fr)',
              columnGap: 22,
              paddingBottom: isLast ? 0 : 30,
            }}
          >
            {!isLast && (
              <div
                className="absolute bg-slate-200"
                style={{ left: 15, top: 30, bottom: 0, width: 1 }}
              />
            )}
            <div
              className="relative z-10 flex items-center justify-center rounded-full bg-slate-950 text-white"
              style={{ width: 28, height: 28, fontSize: 15, lineHeight: '28px' }}
            >
              ✓
            </div>
            <div className="min-w-0 pb-1">
              <div className="font-semibold text-slate-950" style={{ fontSize: 15, lineHeight: '24px' }}>
                {tr(title)}
              </div>
              <div className="mt-2 min-w-0">
                <MarkdownText content={content || '-'} compact />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AttackResultStamp({ bucket, tr }: { bucket: 'success' | 'failed' | 'unknown'; tr: Translate }) {
  const config = bucket === 'success'
    ? { label: tr('攻击成功'), color: '#ef4444', fontSize: 29 }
    : bucket === 'failed'
      ? { label: tr('攻击失败'), color: '#16a34a', fontSize: 29 }
      : { label: tr('未知'), color: '#64748b', fontSize: 35 };
  return (
    <div
      className="pointer-events-none select-none"
      style={{
        width: 260,
        height: 200,
        opacity: 0.72,
      }}
    >
      <svg viewBox="0 0 260 200" width="260" height="200" aria-hidden="true">
        <g fill="none" stroke={config.color} strokeLinecap="round" strokeLinejoin="round">
          <circle cx="130" cy="100" r="72" strokeWidth="5.2" strokeDasharray="72 12 210 16" />
          <circle cx="130" cy="100" r="58" strokeWidth="2.4" opacity="0.62" />
          <path d="M58 74 C80 42 122 29 160 43" strokeWidth="3.2" opacity="0.48" />
          <path d="M71 151 C104 173 150 170 183 143" strokeWidth="3.2" opacity="0.48" />
        </g>

        <g transform="rotate(-12 130 100)" stroke={config.color} strokeLinecap="round" strokeLinejoin="round">
          <g fill={config.color} stroke="none" textAnchor="middle" dominantBaseline="middle" fontWeight="800">
            <text x="113" y="55" style={{ fontSize: 13 }}>★</text>
            <text x="130" y="51" style={{ fontSize: 18 }}>★</text>
            <text x="147" y="55" style={{ fontSize: 13 }}>★</text>
            <text x="113" y="145" style={{ fontSize: 13 }}>★</text>
            <text x="130" y="149" style={{ fontSize: 18 }}>★</text>
            <text x="147" y="145" style={{ fontSize: 13 }}>★</text>
          </g>
          <path d="M32 78 H228 V122 H32 Z" fill="#fff" strokeWidth="5.2" />
          <path d="M42 88 H218" opacity="0.36" strokeWidth="2" />
          <path d="M42 112 H218" opacity="0.36" strokeWidth="2" />
          <text
            x="130"
            y="102"
            fill={config.color}
            stroke="none"
            textAnchor="middle"
            dominantBaseline="middle"
            style={{ fontSize: config.fontSize, fontWeight: 900, letterSpacing: 1 }}
          >
            {config.label}
          </text>
        </g>
      </svg>
    </div>
  );
}

function MarkdownText({ content, compact = false }: { content: string; compact?: boolean }) {
  const lines = content.split(/\r?\n/);
  let inCodeBlock = false;
  const codeLines: string[] = [];
  const paragraphLines: string[] = [];
  const flushCodeBlock = (key: string) => {
    const text = codeLines.join('\n') || '-';
    codeLines.length = 0;
    return (
      <pre key={key} className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-700">
        {text}
      </pre>
    );
  };
  const renderInline = (line: string) => line.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1');
  const nodes: React.ReactNode[] = [];
  const flushParagraph = (key: string) => {
    if (!paragraphLines.length) return;
    const text = paragraphLines.join(' ');
    paragraphLines.length = 0;
    nodes.push(<p key={key}>{text}</p>);
  };
  lines.forEach((line, index) => {
    const key = `${index}-${line}`;
    const trimmed = line.trim();
    if (line.trim().startsWith('```')) {
      flushParagraph(`${key}-paragraph`);
      if (inCodeBlock) {
        nodes.push(flushCodeBlock(key));
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      return;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }
    if (!trimmed) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} style={{ height: compact ? 4 : 8 }} />);
      return;
    }
    if (trimmed.startsWith('# ')) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} className="font-semibold text-slate-950" style={{ fontSize: 14, lineHeight: '22px' }}>{renderInline(trimmed.slice(2))}</div>);
      return;
    }
    if (trimmed.startsWith('## ')) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} className="font-semibold text-slate-950" style={{ marginTop: compact ? 6 : 12, fontSize: 14, lineHeight: '22px' }}>{renderInline(trimmed.slice(3))}</div>);
      return;
    }
    if (trimmed.startsWith('### ')) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} className="font-semibold text-slate-900" style={{ marginTop: compact ? 4 : 10, fontSize: 13, lineHeight: '22px' }}>{renderInline(trimmed.slice(4))}</div>);
      return;
    }
    if (/^\d+[.、]\s+/.test(trimmed)) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} className="pl-3">{renderInline(trimmed)}</div>);
      return;
    }
    if (trimmed.startsWith('- ')) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key} className="pl-3">• {renderInline(trimmed.slice(2))}</div>);
      return;
    }
    if (/^\*\*[^*]+?\*\*\s*[:：]/.test(trimmed)) {
      flushParagraph(`${key}-paragraph`);
      nodes.push(<div key={key}>{renderInline(trimmed)}</div>);
      return;
    }
    paragraphLines.push(renderInline(trimmed));
  });
  flushParagraph('paragraph-tail');
  if (inCodeBlock) nodes.push(flushCodeBlock('code-tail'));
  return (
    <div className={`text-slate-800 ${compact ? 'space-y-1' : 'space-y-2'}`} style={{ fontSize: 13, lineHeight: '22px' }}>
      {nodes}
    </div>
  );
}
