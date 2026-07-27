import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@flocks/webui-contract-sdk';

type TimeRangeKey = '15m' | '1h' | '2h' | '24h' | 'today' | '7d' | '30d';
type TimeFilterMode = 'relative' | 'custom';
type TimePanelTab = 'auto' | 'custom';
type RefreshKey = 'off' | '5s' | '15s' | '1m' | '5m' | '1h';

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

interface CounterItem {
  key?: string;
  label: string;
  value: number;
  rate?: number;
}

interface FieldStats {
  totalRecords?: number;
  duplicates?: number;
  uniqueRecords?: number;
  uniqueSourceIps?: number;
  uniqueDestinationIps?: number;
  uniqueDestinationPorts?: number;
  uniqueHosts?: number;
  uniqueUrls?: number;
  uniqueRules?: number;
  topSourceIps?: CounterItem[];
  topDestinationIps?: CounterItem[];
  topHosts?: CounterItem[];
  topUrls?: CounterItem[];
  topRules?: CounterItem[];
  ports?: CounterItem[];
  statusCodes?: CounterItem[];
  directions?: CounterItem[];
  protocols?: CounterItem[];
  threatTypes?: CounterItem[];
  threatResults?: CounterItem[];
  threatPhases?: CounterItem[];
}

interface Stats {
  date?: string;
  dateRange?: { label?: string; start?: string; end?: string };
  eventRange?: { label?: string; start?: string; end?: string };
  generatedAt?: string;
  denoise?: {
    totalRaw?: number;
    totalUnique?: number;
    duplicates?: number;
    duplicateRate?: number;
  };
  triage?: {
    totalRecords?: number;
    attackSuccess?: number;
    attack?: number;
    attackFailed?: number;
    unknown?: number;
  };
  topThreats?: CounterItem[];
  fieldStats?: FieldStats;
}

const EMPTY_FIELD_STATS: Required<FieldStats> = {
  totalRecords: 0,
  duplicates: 0,
  uniqueRecords: 0,
  uniqueSourceIps: 0,
  uniqueDestinationIps: 0,
  uniqueDestinationPorts: 0,
  uniqueHosts: 0,
  uniqueUrls: 0,
  uniqueRules: 0,
  topSourceIps: [],
  topDestinationIps: [],
  topHosts: [],
  topUrls: [],
  topRules: [],
  ports: [],
  statusCodes: [],
  directions: [],
  protocols: [],
  threatTypes: [],
  threatResults: [],
  threatPhases: [],
};

const EMPTY_STATS: Required<Stats> = {
  date: '',
  dateRange: { label: '', start: '', end: '' },
  eventRange: { label: '', start: '', end: '' },
  generatedAt: '',
  denoise: { totalRaw: 0, totalUnique: 0, duplicates: 0, duplicateRate: 0 },
  triage: { totalRecords: 0, attackSuccess: 0, attack: 0, attackFailed: 0, unknown: 0 },
  topThreats: [],
  fieldStats: EMPTY_FIELD_STATS,
};

const LABELS: Record<string, string> = {
  exploit: '漏洞利用',
  recon: '侦察探测',
  post_exploit: '后渗透',
  control: '控制通信',
  tunneling: '隧道通信',
  file: '文件风险',
  c2: '控制通信',
  trojan: '木马',
  ransom: '勒索',
  shell: '命令执行',
  botnet: '僵尸网络',
  success: '攻击成功',
  failed: '攻击失败',
  unknown: '待确认',
  in: '入站',
  out: '出站',
  lateral: '横向',
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

function list(value: unknown): CounterItem[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item.label === 'string') : [];
}

function mergeFieldStats(value: FieldStats | undefined): Required<FieldStats> {
  return {
    ...EMPTY_FIELD_STATS,
    ...(value || {}),
    topSourceIps: list(value?.topSourceIps),
    topDestinationIps: list(value?.topDestinationIps),
    topHosts: list(value?.topHosts),
    topUrls: list(value?.topUrls),
    topRules: list(value?.topRules),
    ports: list(value?.ports),
    statusCodes: list(value?.statusCodes),
    directions: list(value?.directions),
    protocols: list(value?.protocols),
    threatTypes: list(value?.threatTypes),
    threatResults: list(value?.threatResults),
    threatPhases: list(value?.threatPhases),
  };
}

function mergeStats(raw: Stats | undefined): Required<Stats> {
  const value = raw || {};
  return {
    ...EMPTY_STATS,
    ...value,
    dateRange: { ...EMPTY_STATS.dateRange, ...(value.dateRange || {}) },
    eventRange: { ...EMPTY_STATS.eventRange, ...(value.eventRange || {}) },
    denoise: { ...EMPTY_STATS.denoise, ...(value.denoise || {}) },
    triage: { ...EMPTY_STATS.triage, ...(value.triage || {}) },
    topThreats: list(value.topThreats),
    fieldStats: mergeFieldStats(value.fieldStats),
  };
}

function formatNumber(value: number | undefined) {
  return new Intl.NumberFormat('zh-CN').format(Math.round(Number(value || 0)));
}

function percent(value: number | undefined) {
  return `${Math.round(Number(value || 0) * 1000) / 10}%`;
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

function createRelativeTimeFilter(range: TimeRangeKey = DEFAULT_TIME_RANGE): TimeFilterState {
  const [start, end] = resolveRelativeWindow(range);
  return { mode: 'relative', range, start: toLocalInputValue(start), end: toLocalInputValue(end) };
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

function timeFilterLabel(filter: TimeFilterState) {
  if (filter.mode === 'relative') {
    if (filter.range === '1h') return '最近1小时';
    return TIME_RANGE_OPTIONS.find((option) => option.value === filter.range)?.label || '最近7天';
  }
  const window = resolveTimeWindow(filter);
  if (!window) return '精确时间';
  const [start, end] = window;
  const format = (date: Date) => `${date.getFullYear()}/${pad2(date.getMonth() + 1)}/${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  return `${format(start)} 至 ${format(end)}`;
}

function refreshLabel(value: RefreshKey) {
  return REFRESH_OPTIONS.find((option) => option.value === value)?.label || '关闭';
}

function labelOf(item: CounterItem) {
  return LABELS[item.key || item.label] || LABELS[item.label] || item.label;
}

function truncate(value: string, max = 34) {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function count(items: CounterItem[], key: string) {
  return items.find((item) => item.key === key || item.label === key)?.value || 0;
}

export default function SocOverviewPage() {
  const [stats, setStats] = useState(EMPTY_STATS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [timeFilter, setTimeFilter] = useState<TimeFilterState>(() => createRelativeTimeFilter(DEFAULT_TIME_RANGE));
  const [refreshKey, setRefreshKey] = useState<RefreshKey>('off');
  const [timeMenuOpen, setTimeMenuOpen] = useState(false);

  const load = useCallback(async (activeTimeFilter: TimeFilterState) => {
    setLoading(true);
    setError('');
    try {
      const response = await api.page.get('/stats', { params: timeFilterParams(activeTimeFilter) });
      setStats(mergeStats(response.data));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'SOC 总览数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    void load(timeFilter);
  }, [load, timeFilter]);

  useEffect(() => {
    void load(timeFilter);
  }, [load, timeFilter]);

  useEffect(() => {
    const intervalMs = REFRESH_INTERVAL_MS[refreshKey];
    if (!intervalMs) return undefined;
    const timer = window.setInterval(() => {
      void load(timeFilter);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [load, refreshKey, timeFilter]);

  useEffect(() => {
    if (!timeMenuOpen) return undefined;
    const closeOnOutsidePress = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('[data-soc-menu-root="true"]')) return;
      setTimeMenuOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsidePress, true);
    document.addEventListener('touchstart', closeOnOutsidePress, true);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsidePress, true);
      document.removeEventListener('touchstart', closeOnOutsidePress, true);
    };
  }, [timeMenuOpen]);

  const applyTimeRefresh = useCallback((nextTimeFilter: TimeFilterState, nextRefreshKey: RefreshKey) => {
    setTimeFilter(nextTimeFilter);
    setRefreshKey(nextRefreshKey);
    setTimeMenuOpen(false);
  }, []);


  const cards = useMemo(() => [
    { label: '原始告警', value: stats.denoise.totalRaw, hint: `重复 ${formatNumber(stats.denoise.duplicates)} 条` },
    { label: '有效告警', value: stats.denoise.totalUnique, hint: `去重率 ${percent(stats.denoise.duplicateRate)}` },
    { label: '攻击源地址', value: stats.fieldStats.uniqueSourceIps, hint: '字段 sip' },
    { label: '目标地址', value: stats.fieldStats.uniqueDestinationIps, hint: '字段 dip' },
    { label: 'HTTP 主机', value: stats.fieldStats.uniqueHosts, hint: '字段 req_host' },
    { label: 'URL 样本', value: stats.fieldStats.uniqueUrls, hint: '字段 req_http_url' },
    { label: '威胁规则', value: stats.fieldStats.uniqueRules, hint: '字段 threat_rule_id' },
    { label: '目标端口', value: stats.fieldStats.uniqueDestinationPorts, hint: '字段 dport' },
  ], [stats]);

  const resultTotal = Math.max(stats.denoise.totalUnique, 1);
  const success = stats.triage.attackSuccess || count(stats.fieldStats.threatResults, 'success');
  const failed = stats.triage.attackFailed || count(stats.fieldStats.threatResults, 'failed');
  const unknown = Math.max(0, stats.denoise.totalUnique - success - failed) || count(stats.fieldStats.threatResults, 'unknown');

  return (
    <div className="soc-overview-root">
      <style>{CSS}</style>
      <header className="soc-header">
        <div>
          <h1>SOC 网络告警概览</h1>
          <p>基于真实告警字段统计源地址、目标地址、HTTP 请求和威胁规则。</p>
        </div>
        <div className="soc-actions">
          <TimeRefreshPopover
            value={timeFilter}
            refreshValue={refreshKey}
            open={timeMenuOpen}
            onToggle={() => setTimeMenuOpen((open) => !open)}
            onApply={applyTimeRefresh}
            onClose={() => setTimeMenuOpen(false)}
          />
          <button type="button" onClick={refresh}>{loading ? '刷新中' : '刷新'}</button>
        </div>
      </header>

      {error && <div className="soc-error">{error}</div>}

      <section className="metric-grid">
        {cards.map((card) => (
          <article key={card.label} className="metric-card">
            <span>{card.label}</span>
            <b>{formatNumber(card.value)}</b>
            <small>{card.hint}</small>
          </article>
        ))}
      </section>

      <section className="result-card">
        <div className="section-head">
          <div>
            <h2>告警研判结果</h2>
            <p>按 threat_result 与研判结论聚合。</p>
          </div>
          <b>{formatNumber(resultTotal)} 条有效告警</b>
        </div>
        <div className="result-bar">
          <span className="success" style={{ width: `${(success / resultTotal) * 100}%` }} />
          <span className="unknown" style={{ width: `${(unknown / resultTotal) * 100}%` }} />
          <span className="failed" style={{ width: `${(failed / resultTotal) * 100}%` }} />
        </div>
        <div className="result-legend">
          <span><i className="success" />攻击成功 {formatNumber(success)}</span>
          <span><i className="unknown" />待确认 {formatNumber(unknown)}</span>
          <span><i className="failed" />攻击失败 {formatNumber(failed)}</span>
        </div>
      </section>

      <main className="panel-grid">
        <Panel title="TOP 威胁名称"><RankList rows={stats.topThreats.slice(0, 8)} /></Panel>
        <Panel title="威胁类型分布"><TileList rows={stats.fieldStats.threatTypes} /></Panel>
        <Panel title="攻击阶段"><ProgressList rows={stats.fieldStats.threatPhases} /></Panel>
        <Panel title="流量方向与响应"><SplitRanks leftTitle="流量方向" leftRows={stats.fieldStats.directions} rightTitle="响应码" rightRows={stats.fieldStats.statusCodes} /></Panel>
        <Panel title="高频 HTTP 主机"><RankList rows={stats.fieldStats.topHosts} mono /></Panel>
        <Panel title="高频地址与规则"><SplitRanks leftTitle="源地址" leftRows={stats.fieldStats.topSourceIps} rightTitle="规则 ID" rightRows={stats.fieldStats.topRules} mono /></Panel>
      </main>
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
}: {
  value: TimeFilterState;
  refreshValue: RefreshKey;
  open: boolean;
  onToggle: () => void;
  onApply: (timeFilter: TimeFilterState, refresh: RefreshKey) => void;
  onClose: () => void;
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
    onApply(tab === 'custom' ? { mode: 'custom', range, start, end } : createRelativeTimeFilter(range), refresh);
  };

  const currentTimeLabel = timeFilterLabel(value);
  const optionClass = (selected: boolean) => `time-option${selected ? ' selected' : ''}`;

  return (
    <div className="time-filter" data-soc-menu-root="true">
      <button type="button" className={`time-trigger${open ? ' open' : ''}`} onClick={onToggle}>
        <span title={currentTimeLabel}>时间范围：<b>{currentTimeLabel}</b></span>
        <i />
        <span>刷新频率：<b>{refreshLabel(refreshValue)}</b></span>
        <em>{open ? '⌃' : '⌄'}</em>
      </button>
      {open && (
        <div className="time-panel">
          <div className="time-tabs">
            {[
              ['auto', '自动刷新'],
              ['custom', '精确时间'],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={tab === key ? 'active' : ''}
                onClick={() => setTab(key as TimePanelTab)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="time-panel-body">
            {tab === 'auto' ? (
              <>
                <div>
                  <label>时间范围</label>
                  <div className="time-options">
                    {TIME_RANGE_OPTIONS.map((option) => (
                      <button key={option.value} type="button" className={optionClass(range === option.value)} onClick={() => chooseRange(option.value)}>
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label>刷新频率</label>
                  <div className="time-options">
                    {REFRESH_OPTIONS.map((option) => (
                      <button key={option.value} type="button" className={optionClass(refresh === option.value)} onClick={() => setRefresh(option.value)}>
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <>
                <div className="time-inputs">
                  <label>
                    <span>开始时间</span>
                    <input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
                  </label>
                  <label>
                    <span>结束时间</span>
                    <input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} />
                  </label>
                </div>
                <div className="time-shortcuts">
                  {[
                    ['1h', '1小时'],
                    ['24h', '24小时'],
                    ['today', '今天'],
                    ['7d', '最近7天'],
                    ['30d', '最近30天'],
                  ].map(([key, label]) => (
                    <button key={key} type="button" onClick={() => chooseRange(key as TimeRangeKey)}>{label}</button>
                  ))}
                </div>
              </>
            )}
          </div>
          <div className="time-panel-actions">
            <button type="button" onClick={onClose}>取消</button>
            <button type="button" className="primary" onClick={confirm}>确定</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel-card">
      <div className="panel-head"><h2>{title}</h2></div>
      {children}
    </section>
  );
}

function RankList({ rows, mono = false }: { rows: CounterItem[]; mono?: boolean }) {
  return (
    <div className="rank-list">
      {rows.length ? rows.map((item, index) => (
        <div key={`${item.label}-${index}`}>
          <span>{String(index + 1).padStart(2, '0')}</span>
          <b className={mono ? 'mono' : ''} title={item.label}>{truncate(labelOf(item), mono ? 44 : 30)}</b>
          <em>{formatNumber(item.value)}</em>
        </div>
      )) : <div className="empty">暂无数据</div>}
    </div>
  );
}

function TileList({ rows }: { rows: CounterItem[] }) {
  return (
    <div className="tile-list">
      {rows.slice(0, 9).map((item) => (
        <div key={item.label}>
          <b>{labelOf(item)}</b>
          <span>{formatNumber(item.value)}</span>
        </div>
      ))}
      {!rows.length && <div className="empty">暂无数据</div>}
    </div>
  );
}

function ProgressList({ rows }: { rows: CounterItem[] }) {
  const total = Math.max(1, rows.reduce((value, item) => value + item.value, 0));
  return (
    <div className="progress-list">
      {rows.map((item) => (
        <div key={item.label}>
          <span>{labelOf(item)}</span>
          <i><b style={{ width: `${Math.max(4, (item.value / total) * 100)}%` }} /></i>
          <em>{formatNumber(item.value)}</em>
        </div>
      ))}
      {!rows.length && <div className="empty">暂无数据</div>}
    </div>
  );
}

function SplitRanks({ leftTitle, leftRows, rightTitle, rightRows, mono = false }: { leftTitle: string; leftRows: CounterItem[]; rightTitle: string; rightRows: CounterItem[]; mono?: boolean }) {
  return (
    <div className="split-ranks">
      <div><h3>{leftTitle}</h3><RankList rows={leftRows.slice(0, 4)} mono={mono} /></div>
      <div><h3>{rightTitle}</h3><RankList rows={rightRows.slice(0, 4)} mono={mono} /></div>
    </div>
  );
}

const CSS = `
.soc-overview-root {
  min-height: 100%;
  overflow: auto;
  padding: 24px;
  background: #f6f7fb;
  color: #111827;
  font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}
.soc-overview-root * { box-sizing: border-box; }
.soc-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}
.soc-header h1 { margin: 0; font-size: 24px; line-height: 1.25; font-weight: 750; letter-spacing: 0; }
.soc-header p { margin: 8px 0 0; color: #667085; font-size: 14px; }
	.soc-actions { position: relative; display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; color: #475467; font-size: 13px; }
	.soc-actions > span, .soc-actions > button {
	  height: 34px;
	  display: inline-flex;
	  align-items: center;
  border: 1px solid #d9dee8;
  border-radius: 6px;
	  background: #fff;
	  padding: 0 12px;
	}
	.soc-actions > button { cursor: pointer; color: #111827; font-weight: 600; }
	.time-filter { position: relative; z-index: 30; }
	.time-trigger {
	  min-height: 34px;
	  max-width: min(760px, calc(100vw - 64px));
	  display: flex;
	  flex-wrap: wrap;
	  align-items: center;
	  gap: 10px;
	  border: 1px solid #d9dee8;
	  border-radius: 6px;
	  background: #fff;
	  padding: 6px 12px;
	  color: #344054;
	  font-size: 13px;
	  cursor: pointer;
	  transition: border-color .16s ease, background .16s ease;
	}
	.time-trigger.open { border-color: #2563eb; }
	.time-trigger:hover { border-color: #9fb0c7; }
	.time-trigger span { display: inline-flex; align-items: center; white-space: nowrap; }
	.time-trigger b { color: #2563eb; font-weight: 650; }
	.time-trigger i { width: 1px; height: 16px; background: #e2e8f0; }
	.time-trigger em { color: #64748b; font-style: normal; font-size: 14px; line-height: 1; }
	.time-panel {
	  position: absolute;
	  top: calc(100% + 8px);
	  right: 0;
	  width: min(360px, calc(100vw - 32px));
	  overflow: hidden;
	  border: 1px solid #d9e2ef;
	  border-radius: 8px;
	  background: #fff;
	  z-index: 40;
	}
	.time-tabs {
	  display: inline-grid;
	  grid-template-columns: repeat(2, minmax(0, 1fr));
	  gap: 2px;
	  width: calc(100% - 24px);
	  margin: 12px;
	  border-radius: 6px;
	  background: #f1f5f9;
	  padding: 2px;
	}
	.time-tabs button {
	  height: 30px;
	  border: 0;
	  border-radius: 5px;
	  background: transparent;
	  color: #64748b;
	  font-size: 13px;
	  font-weight: 650;
	  cursor: pointer;
	}
	.time-tabs button.active { background: #fff; color: #1d4ed8; }
	.time-panel-body {
	  display: grid;
	  gap: 12px;
	  border-top: 1px solid #e2e8f0;
	  padding: 12px;
	}
	.time-panel-body label,
	.time-inputs span {
	  display: block;
	  margin-bottom: 6px;
	  color: #94a3b8;
	  font-size: 12px;
	  font-weight: 650;
	}
	.time-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
	.time-option,
	.time-shortcuts button {
	  height: 28px;
	  border: 1px solid transparent;
	  border-radius: 5px;
	  background: #f8fafc;
	  color: #64748b;
	  font-size: 12px;
	  font-weight: 650;
	  white-space: nowrap;
	  cursor: pointer;
	}
	.time-option:hover,
	.time-shortcuts button:hover { background: #eef2f7; color: #334155; }
	.time-option.selected { border-color: #2563eb; background: #2563eb; color: #fff; }
	.time-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
	.time-inputs input {
	  width: 100%;
	  height: 32px;
	  border: 1px solid #cbd5e1;
	  border-radius: 5px;
	  background: #fff;
	  padding: 0 8px;
	  color: #334155;
	  font-size: 12px;
	  outline: none;
	}
	.time-inputs input:focus { border-color: #2563eb; box-shadow: 0 0 0 2px #dbeafe; }
	.time-shortcuts { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
	.time-shortcuts button { padding: 0 4px; }
	.time-panel-actions {
	  display: flex;
	  justify-content: flex-end;
	  gap: 8px;
	  border-top: 1px solid #e2e8f0;
	  background: #f8fafc;
	  padding: 10px 12px;
	}
	.time-panel-actions button {
	  height: 30px;
	  min-width: 64px;
	  border: 1px solid #cbd5e1;
	  border-radius: 5px;
	  background: #fff;
	  color: #334155;
	  font-size: 13px;
	  cursor: pointer;
	}
	.time-panel-actions button.primary { border-color: #2563eb; background: #2563eb; color: #fff; font-weight: 650; }
	.soc-error { margin-bottom: 16px; border: 1px solid #fed7aa; background: #fff7ed; color: #c2410c; border-radius: 6px; padding: 10px 12px; font-size: 13px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.metric-card, .result-card, .panel-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}
.metric-card { min-height: 112px; padding: 18px; }
.metric-card span { color: #667085; font-size: 13px; }
.metric-card b { display: block; margin-top: 10px; color: #111827; font-size: 28px; line-height: 1; }
.metric-card small { display: block; margin-top: 10px; color: #98a2b3; font-size: 12px; }
.result-card { margin-top: 14px; padding: 18px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.section-head h2, .panel-head h2 { margin: 0; color: #111827; font-size: 15px; font-weight: 700; }
.section-head p { margin: 6px 0 0; color: #667085; font-size: 13px; }
.section-head > b { color: #475467; font-size: 13px; }
.result-bar { display: flex; height: 14px; overflow: hidden; margin-top: 18px; border-radius: 999px; background: #edf2f7; }
.result-bar .success { background: #ef4444; }
.result-bar .unknown { background: #f59e0b; }
.result-bar .failed { background: #cbd5e1; }
.result-legend { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 12px; color: #667085; font-size: 13px; }
.result-legend span { display: inline-flex; align-items: center; gap: 7px; }
.result-legend i { width: 9px; height: 9px; border-radius: 50%; }
.result-legend .success { background: #ef4444; }
.result-legend .unknown { background: #f59e0b; }
.result-legend .failed { background: #cbd5e1; }
.panel-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.panel-card { min-height: 288px; padding: 0 16px 16px; }
.panel-head { height: 48px; display: flex; align-items: center; border-bottom: 1px solid #edf2f7; margin-bottom: 14px; }
.rank-list { display: grid; gap: 8px; }
.rank-list div { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 34px; border: 1px solid #edf2f7; border-radius: 6px; background: #fbfcfe; padding: 7px 10px; }
.rank-list span { color: #94a3b8; font-size: 12px; font-weight: 700; }
.rank-list b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #344054; font-size: 13px; }
.rank-list b.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.rank-list em { color: #111827; font-size: 13px; font-style: normal; font-weight: 700; }
.tile-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.tile-list div { min-height: 72px; display: grid; align-content: center; justify-items: center; gap: 8px; border-radius: 6px; background: #f8fafc; color: #475467; }
.tile-list b { font-size: 13px; }
.tile-list span { color: #111827; font-size: 18px; font-weight: 700; }
.progress-list { display: grid; gap: 13px; }
.progress-list div { display: grid; grid-template-columns: 88px minmax(0, 1fr) 62px; align-items: center; gap: 12px; color: #475467; font-size: 13px; }
.progress-list i { height: 8px; overflow: hidden; border-radius: 999px; background: #edf2f7; }
.progress-list b { display: block; height: 100%; border-radius: inherit; background: #2563eb; }
.progress-list em { color: #111827; text-align: right; font-style: normal; font-weight: 700; }
.split-ranks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.split-ranks h3 { margin: 0 0 10px; color: #667085; font-size: 13px; font-weight: 700; }
.empty { min-height: 80px; display: grid; place-items: center; color: #98a2b3; font-size: 13px; }
@media (max-width: 1180px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .panel-grid { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .soc-overview-root { padding: 16px; } .soc-header { flex-direction: column; } .metric-grid, .split-ranks { grid-template-columns: 1fr; } }
`;
