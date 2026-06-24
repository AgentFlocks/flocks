import type {
  AlertTableCell,
  AlertTableColumn,
  IncidentCluster,
} from './assetAlertData';

type AssetAlertRecord = Record<string, unknown>;

export interface AssetIncidentBuildOptions {
  threatCounts?: Map<string, number>;
}

export interface AssetIncidentsResult {
  columns: AlertTableColumn[];
  incidents: IncidentCluster[];
}

const FAILED_STATUS_CODES = new Set([401, 403, 404, 405, 406, 410]);

const ASSET_FIELD_DEFINITIONS: AlertTableColumn[] = [
  { key: 'id', label: 'id', description: '告警 UUID', widthClass: 'min-w-72', mono: true },
  { key: 'time', label: 'time', description: '事件时间', widthClass: 'min-w-44' },
  { key: '_source_type', label: '_source_type', description: '数据源', widthClass: 'min-w-28' },
  { key: 'direction', label: 'direction', description: '流量方向', widthClass: 'min-w-28' },
  { key: 'sip', label: 'sip', description: '源地址', widthClass: 'min-w-64', mono: true },
  { key: 'sport', label: 'sport', description: '源端口', widthClass: 'min-w-24' },
  { key: 'dip', label: 'dip', description: '目标地址', widthClass: 'min-w-64', mono: true },
  { key: 'dport', label: 'dport', description: '目标端口', widthClass: 'min-w-24' },
  { key: 'net_type', label: 'net_type', description: '网络类型', widthClass: 'min-w-28' },
  { key: 'req_host', label: 'req_host', description: 'HTTP Host', widthClass: 'min-w-56' },
  { key: 'req_http_url', label: 'req_http_url', description: '请求路径', widthClass: 'min-w-72' },
  { key: 'rsp_status_code', label: 'rsp_status_code', description: '响应码', widthClass: 'min-w-32' },
  { key: 'threat_rule_id', label: 'threat_rule_id', description: '规则 ID', widthClass: 'min-w-36', mono: true },
  { key: 'threat_name', label: 'threat_name', description: '威胁名称', widthClass: 'min-w-52' },
  { key: 'threat_level', label: 'threat_level', description: '威胁等级', widthClass: 'min-w-32' },
  { key: 'threat_severity', label: 'threat_severity', description: '严重度', widthClass: 'min-w-32' },
  { key: 'threat_phase', label: 'threat_phase', description: '攻击阶段', widthClass: 'min-w-36' },
  { key: 'threat_type', label: 'threat_type', description: '威胁类型', widthClass: 'min-w-36' },
  { key: 'threat_result', label: 'threat_result', description: '原始结果', widthClass: 'min-w-36' },
  { key: 'dedup_key', label: 'dedup_key', description: '去重键', widthClass: 'min-w-64', mono: true },
  { key: 'is_duplicate', label: 'is_duplicate', description: '重复标记', widthClass: 'min-w-32' },
];

export function resolveAssetRoutePath(displayPath: string) {
  const firstPath = displayPath.split(',')[0]?.trim() ?? '';
  if (!firstPath) return '';

  const markerIndex = firstPath.indexOf('/assets/');
  if (markerIndex >= 0) return firstPath.slice(markerIndex + '/assets/'.length);

  return firstPath.replace(/^assets\//, '');
}

export function parseThreatCounts(payload: unknown) {
  const counts = new Map<string, number>();
  const root = asRecord(payload);
  const topThreats = Array.isArray(root?.topThreats) ? root.topThreats : [];
  for (const item of topThreats) {
    const record = asRecord(item);
    const label = readString(record?.label, '');
    const value = readNumber(record?.value, 0);
    if (label && value > 0) counts.set(label, value);
  }
  return counts;
}

export async function readAssetIncidentsFromJsonl(
  assetUrl: string,
  limit: number,
  options: AssetIncidentBuildOptions = {},
): Promise<AssetIncidentsResult> {
  const response = await fetch(assetUrl, { credentials: 'include' });
  if (!response.ok) {
    throw new Error(`assets request failed: ${response.status}`);
  }

  const records: AssetAlertRecord[] = [];
  const seenRecordIds = new Set<string>();

  await readJsonlStream(response, (record) => {
    if (record._type === 'file_header' || record.is_duplicate === true) return records.length >= limit;

    const recordId = readString(record.id, '');
    if (recordId && seenRecordIds.has(recordId)) return records.length >= limit;
    if (recordId) seenRecordIds.add(recordId);

    records.push(record);

    return records.length >= limit;
  });

  const columns = buildAssetTableColumns(records);
  return {
    columns,
    incidents: records.map((record, index) => buildIncidentFromAssetRecord(record, index, {
      ...options,
      columns,
    })),
  };
}

export function buildIncidentFromAssetRecord(
  record: AssetAlertRecord,
  index: number,
  options: AssetIncidentBuildOptions & { columns?: AlertTableColumn[] } = {},
): IncidentCluster {
  const title = getThreatName(record) || `未知告警 ${index + 1}`;
  const method = parseRequestMethod(record);
  const host = readString(record.req_host, readString(record.dip, 'unknown'));
  const uri = readString(record.req_http_url, '/');
  const statusCode = readNumber(record.rsp_status_code, 0);
  const direction = readString(record.direction, 'unknown');
  const sourceType = readString(record._source_type, readString(record.source_type, 'assets'));
  const sourceLabel = sourceType.toUpperCase();
  const rawAlerts = options.threatCounts?.get(title) ?? 1;
  const severity = readNumber(record.threat_severity, 0);
  const responseBodyLength = readNumber(record.rsp_body_len, 0);
  const verdict = getVerdict(record);
  const priority = severity >= 3 || verdict === '攻击成功' ? 'P1' : 'P2';
  const owner = direction === 'lateral' ? '内网安全组' : '边界安全组';
  const requestPayload = buildRequestPayload(record, method, uri);
  const responseSample = buildResponseSample(record);
  const assetName = host !== 'unknown' ? host : readString(record.dip, 'unknown');
  const sourceRecordId = readString(record.id, `asset-record-${index + 1}`);
  const triageReport = readString(record.triage_report, readString(record.final_report, ''));

  return {
    id: sourceRecordId,
    sourceRecordId,
    observedAt: formatAssetTime(record),
    rawAlerts,
    confidence: getConfidence(record, statusCode, responseBodyLength),
    priority,
    title,
    reason: readString(record.threat_msg, `检测到 ${title} 相关流量。`),
    owner,
    srcIp: readString(record.sip, 'unknown'),
    ndrRule: readString(record.threat_rule_id, 'unknown'),
    request: {
      method,
      host,
      uri,
      payload: requestPayload,
      llmAnalysis: buildRequestAnalysis(title, record, method, host, uri),
      evidence: buildRequestEvidence(record, uri),
    },
    response: {
      statusCode,
      llmAnalysis: buildResponseAnalysis(verdict, statusCode, responseBodyLength),
      evidence: buildResponseEvidence(record, statusCode, responseBodyLength),
      sample: responseSample,
    },
    srcIntel: {
      verdict: buildSourceVerdict(direction, verdict),
      location: `${sourceLabel} assets / ${direction || 'unknown'}`,
      tags: buildTags(record),
      summary: `该记录来自自定义页面 assets，阶段为 ${readString(record.threat_phase, 'unknown')}，类型为 ${readString(record.threat_type, title)}，需要结合资产日志和响应证据确认处置优先级。`,
    },
    asset: {
      name: assetName,
      business: inferBusiness(title, host, direction),
      exposure: directionLabel(direction),
      owner,
      criticality: priority === 'P1' ? '高' : '中',
      context: `目标地址 ${readString(record.dip, 'unknown')}:${readNumber(record.dport, 0) || 'unknown'}，请求 Host 为 ${host}。`,
    },
    conclusion: {
      verdict,
      summary: buildConclusionSummary(title, verdict, statusCode),
      recommendation: buildRecommendation(title, verdict),
    },
    actions: buildActions(title, verdict),
    triageReport: triageReport || undefined,
    tableCells: buildAssetTableCells(record, options.columns ?? buildAssetTableColumns([record])),
  };
}

function buildAssetTableColumns(records: AssetAlertRecord[]) {
  const presentFields = new Set<string>();
  for (const record of records) {
    for (const [key, value] of Object.entries(record)) {
      if (isDisplayableValue(value)) presentFields.add(key);
    }
  }

  return ASSET_FIELD_DEFINITIONS.filter((column) => presentFields.has(column.key));
}

function buildAssetTableCells(record: AssetAlertRecord, columns: AlertTableColumn[]) {
  const cells: Record<string, AlertTableCell> = {};
  for (const column of columns) {
    cells[column.key] = formatAssetFieldCell(record, column);
  }
  return cells;
}

function formatAssetFieldCell(record: AssetAlertRecord, column: AlertTableColumn): AlertTableCell {
  const rawValue = record[column.key];
  const value = (() => {
    if (column.key === 'time') return formatAssetTime(record);
    if (typeof rawValue === 'boolean') return String(rawValue);
    if (typeof rawValue === 'number') return String(rawValue);
    if (typeof rawValue === 'string') return rawValue === 'none' ? '' : rawValue;
    if (rawValue === null || rawValue === undefined) return '';
    return JSON.stringify(rawValue);
  })();

  const detail = (() => {
    if (column.key === 'threat_name') return readString(record.threat_msg, '');
    if (column.key === 'req_http_url') return readString(record.req_line, '');
    if (column.key === 'rsp_status_code') return readString(record.rsp_line, '');
    return undefined;
  })();

  return {
    value,
    detail,
    tone: getCellTone(column.key, rawValue),
    mono: column.mono,
  };
}

function getCellTone(key: string, value: unknown): AlertTableCell['tone'] {
  if (key === 'rsp_status_code') {
    const status = readNumber(value, 0);
    if (status >= 500) return 'red';
    if (FAILED_STATUS_CODES.has(status)) return 'orange';
    if (status >= 200 && status < 300) return 'green';
    return 'slate';
  }

  if (key === 'threat_result') {
    const normalized = readString(value, '').toLowerCase();
    if (['success', 'succeeded'].includes(normalized)) return 'red';
    if (['failed', 'blocked'].includes(normalized)) return 'green';
    return 'orange';
  }

  if (key === 'threat_level') {
    return readString(value, '').toLowerCase() === 'attack' ? 'red' : 'slate';
  }

  if (key === 'direction') {
    const normalized = readString(value, '').toLowerCase();
    if (normalized === 'lateral') return 'purple';
    if (normalized === 'out') return 'orange';
    return 'blue';
  }

  return undefined;
}

function isDisplayableValue(value: unknown) {
  if (value === null || value === undefined || value === '' || value === 'none') return false;
  if (typeof value === 'object' && !Array.isArray(value)) return Object.keys(value).length > 0;
  return true;
}

async function readJsonlStream(response: Response, onRecord: (record: AssetAlertRecord) => boolean | void) {
  if (!response.body) {
    const text = await response.text();
    for (const line of text.split(/\r?\n/)) {
      if (parseJsonlLine(line, onRecord)) break;
    }
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        if (parseJsonlLine(line, onRecord)) {
          await reader.cancel();
          return;
        }
      }
    }

    buffer += decoder.decode();
    if (buffer) parseJsonlLine(buffer, onRecord);
  } finally {
    reader.releaseLock();
  }
}

function parseJsonlLine(line: string, onRecord: (record: AssetAlertRecord) => boolean | void) {
  const trimmed = line.trim();
  if (!trimmed) return false;

  try {
    const payload = JSON.parse(trimmed);
    if (!asRecord(payload)) return false;
    return onRecord(payload) === true;
  } catch {
    return false;
  }
}

function buildRequestPayload(record: AssetAlertRecord, method: string, uri: string) {
  const reqLine = readString(record.req_line, `${method} ${uri} HTTP/1.1`);
  const reqBody = readString(record.req_body, '');
  return truncateText(reqBody ? `${reqLine}\n\n${reqBody}` : reqLine, 1800);
}

function buildResponseSample(record: AssetAlertRecord) {
  const body = readString(record.rsp_body, '');
  const line = readString(record.rsp_line, '');
  if (body) return truncateText(body, 1800);
  if (line) return line;
  return 'assets 记录未提供响应体';
}

function buildRequestAnalysis(title: string, record: AssetAlertRecord, method: string, host: string, uri: string) {
  const phase = readString(record.threat_phase, 'unknown');
  const threatType = readString(record.threat_type, title);
  return `请求为 ${method} ${host}${uri}，命中 ${title}，攻击阶段 ${phase}，威胁类型 ${threatType}。`;
}

function buildResponseAnalysis(verdict: string, statusCode: number, responseBodyLength: number) {
  if (verdict === '攻击成功') {
    return `响应状态为 ${statusCode}，响应体长度 ${responseBodyLength}，当前证据支持攻击已获得有效响应。`;
  }
  if (verdict === '攻击失败') {
    return `响应状态为 ${statusCode}，当前状态码或响应内容不支持成功利用。`;
  }
  return `响应状态为 ${statusCode || '未知'}，需要继续结合响应体、资产日志和终端侧证据确认攻击成功性。`;
}

function buildRequestEvidence(record: AssetAlertRecord, uri: string) {
  return [
    uri,
    readString(record.threat_rule_id, '未知规则'),
    readString(record.threat_phase, 'unknown'),
  ].filter(Boolean);
}

function buildResponseEvidence(record: AssetAlertRecord, statusCode: number, bodyLength: number) {
  return [
    statusCode ? `HTTP ${statusCode}` : '无有效响应码',
    readString(record.rsp_line, ''),
    bodyLength > 0 ? `响应体 ${bodyLength} bytes` : '无响应体样本',
  ].filter(Boolean);
}

function buildTags(record: AssetAlertRecord) {
  return [
    readString(record.threat_phase, ''),
    readString(record.threat_type, ''),
    readString(record._source_type, ''),
  ].filter(Boolean);
}

function buildSourceVerdict(direction: string, verdict: string) {
  if (direction === 'lateral') return '横向穿透';
  if (direction === 'out') return verdict === '攻击成功' ? '出站风险已响应' : '出站可疑访问';
  return verdict === '攻击失败' ? '外部扫描源' : '可疑攻击源';
}

function inferBusiness(title: string, host: string, direction: string) {
  if (title.toLowerCase().includes('tailscale')) return 'Tailscale 控制面访问目标';
  if (title.includes('脚本') || title.includes('恶意软件')) return '外部下载或控制站点';
  if (host && host !== 'unknown') return 'Web 站点';
  if (direction === 'lateral') return '内网资产';
  return 'Web 应用';
}

function directionLabel(direction: string) {
  if (direction === 'in') return '公网';
  if (direction === 'out') return '互联网出站';
  if (direction === 'lateral') return '横向';
  return '未知';
}

function buildConclusionSummary(title: string, verdict: string, statusCode: number) {
  return `${title} 的 assets 记录研判为${verdict}，响应状态码为 ${statusCode || '未知'}。`;
}

function buildRecommendation(title: string, verdict: string) {
  if (verdict === '攻击成功') {
    return `优先封禁相关 IOC，排查目标资产与源主机日志，确认 ${title} 是否产生文件落地、回连或权限变更。`;
  }
  if (verdict === '攻击失败') {
    return `保留同源扫描画像，确认目标路径没有其它成功响应，并补齐 ${title} 相关防护规则。`;
  }
  return `按 ${title} 场景补充资产侧日志，确认攻击成功性后再执行封禁、隔离或修复动作。`;
}

function buildActions(title: string, verdict: string) {
  const actions = ['关联同源请求', '复核目标资产日志', '检查防护规则命中'];
  if (verdict === '攻击成功') actions.unshift('封禁相关 IOC');
  if (title.includes('敏感') || title.includes('env')) actions.push('排查敏感文件暴露');
  if (title.includes('脚本') || title.includes('恶意软件')) actions.push('关联 EDR 文件落地与进程树');
  return actions;
}

function getThreatName(record: AssetAlertRecord) {
  return readString(record._threat_type, readString(record.threat_name, readString(record.threat_type, '')));
}

function getVerdict(record: AssetAlertRecord) {
  const threatLevel = readString(record.threat_level, '').toLowerCase();
  const threatResult = readString(record.threat_result, '').toLowerCase();
  const status = readNumber(record.rsp_status_code, 0);
  const bodyLength = readNumber(record.rsp_body_len, 0);

  if (['benign', 'info', 'low'].includes(threatLevel)) return '良性';
  if (['success', 'succeeded'].includes(threatResult)) return '攻击成功';
  if (['failed', 'blocked'].includes(threatResult) || FAILED_STATUS_CODES.has(status)) return '攻击失败';
  if (status === 200 && bodyLength > 0) return '攻击成功';
  if (threatLevel === 'attack' || getThreatName(record)) return '攻击行为';
  return '待确认';
}

function getConfidence(record: AssetAlertRecord, statusCode: number, bodyLength: number) {
  const value = readNumber(record.threat_confidence, 0);
  if (value > 0) return Math.min(Math.max(value, 1), 99);
  if (statusCode === 200 && bodyLength > 0) return 86;
  if (readNumber(record.threat_severity, 0) >= 3) return 93;
  return 79;
}

function parseRequestMethod(record: AssetAlertRecord) {
  const reqLine = readString(record.req_line, '');
  const method = reqLine.split(/\s+/)[0];
  return /^[A-Z]+$/.test(method) ? method : 'GET';
}

function formatAssetTime(record: AssetAlertRecord) {
  const seconds = readNumber(record.time, 0);
  if (!seconds) return readString(asRecord(record._syslog_meta)?.timestamp, '');
  const date = new Date(seconds * 1000);
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function truncateText(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength)}\n...` : value;
}

function asRecord(value: unknown): AssetAlertRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as AssetAlertRecord
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
  return typeof value === 'string' && value.trim() !== '' && value !== 'none' ? value : fallback;
}
