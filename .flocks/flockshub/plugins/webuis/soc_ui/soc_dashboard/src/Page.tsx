function getSdk() {
  const sdk = globalThis.__FLOCKS_WEBUI_CONTRACT_SDK__;
  if (!sdk || !sdk.React || !sdk.api) {
    throw new Error('Flocks WebUI contract page runtime is not initialized.');
  }
  return sdk;
}

function getReact() {
  return getSdk().React;
}

function getApi() {
  return getSdk().api;
}

const h = (...args) => getReact().createElement(...args);

const EMPTY_STATS = {
  date: '',
  dateRange: { start: '', end: '', label: '', availableDates: [], fileDates: [] },
  eventRange: { start: '', end: '', label: '', source: '' },
  generatedAt: '',
  latencyMs: 0,
  sourceStatus: { workflowRoot: '', denoise: [], triage: [], denoiseFiles: [], triageFiles: [], missing: [] },
  denoise: {
    totalRaw: 0,
    totalNormalized: 0,
    afterFilter: 0,
    totalUnique: 0,
    filterRemoved: 0,
    dedupRemoved: 0,
    duplicates: 0,
    duplicateRate: 0,
    dedupRate: 0,
    uniqueRate: 0,
    files: 0,
    parseErrors: 0,
  },
  triage: {
    totalRecords: 0,
    newTriaged: 0,
    cacheHit: 0,
    triageFailed: 0,
    followersReused: 0,
    attackTotal: 0,
    attackSuccess: 0,
    attack: 0,
    attackFailed: 0,
    benign: 0,
    unknown: 0,
    attackRate: 0,
    successRate: 0,
    cacheRate: 0,
    coverageRate: 0,
    avgTriageMs: 0,
    files: 0,
    parseErrors: 0,
  },
  pipeline: {
    raw: 0,
    unique: 0,
    triageTotal: 0,
    attackTotal: 0,
    reductionSaved: 0,
    llmSaved: 0,
    uniqueRate: 0,
    workloadReuseRate: 0,
    coverageRate: 0,
    attackRate: 0,
    successRate: 0,
  },
  sources: [],
  closedLoop: { autoClosed: 0, resolved: 0, manualDecision: 0, pending: 0, resolutionRate: 0 },
  verdicts: [],
  attackProfile: [],
  topThreats: [],
  riskLevels: [],
  timeline: { denoiseRaw: [], denoiseUnique: [], triageTotal: [], triageAttack: [] },
};

const ACTIVITY_QUEUE_LIMIT = 8;
const EVENT_RAIL_TASK_LIMIT = 10;
const ACTIVITY_POLL_MS = 3000;
const ACTIVITY_REPLAY_WINDOW_MS = 10 * 60 * 1000;
const ACTIVITY_SEEN_KEY = 'soc-dashboard-seen-activity-v1';
const DEFAULT_TIME_RANGE = '7d';
const TIME_RANGE_OPTIONS = [
  { value: '15m', label: '最近15分钟' },
  { value: '2h', label: '最近2小时' },
  { value: '24h', label: '最近24小时' },
  { value: 'today', label: '今天' },
  { value: '7d', label: '最近7天' },
  { value: '30d', label: '最近30天' },
];
const REFRESH_OPTIONS = [
  { value: '5s', label: '5秒' },
  { value: '15s', label: '15秒' },
  { value: '1m', label: '1分钟' },
  { value: '5m', label: '5分钟' },
  { value: '1h', label: '1小时' },
  { value: 'off', label: '关闭' },
];
const REFRESH_INTERVAL_MS = {
  off: 0,
  '5s': 5000,
  '15s': 15000,
  '1m': 60000,
  '5m': 300000,
  '1h': 3600000,
};

function emptyActivityBatch() {
  return {
    mode: 'normal',
    windowMs: ACTIVITY_POLL_MS,
    receivedCount: 0,
    duplicateCount: 0,
    uniqueCount: 0,
    clusterCount: 0,
    triageUpdatedCount: 0,
    sampledCount: 0,
    suppressedCount: 0,
    ratePerSecond: 0,
  };
}

function createActivityState() {
  return {
    connection: 'initializing',
    denoise: { current: null, queue: [], last: null },
    triage: { current: null, queue: [], last: null },
    recent: [],
    batch: emptyActivityBatch(),
    batchUpdatedAt: 0,
    mode: 'normal',
    calmPolls: 0,
    generatedAt: '',
  };
}

function activityDuration(event) {
  if (!event) return 0;
  if (event.stage === 'denoise') {
    if (event.playbackMode === 'surge') return 5200;
    if (event.playbackMode === 'burst') return 6200;
    return 7200;
  }
  if (event.status === 'failed') return 6000;
  if (['cache', 'cached', 'follower', 'follower_reused'].includes(event.result?.triageSource)) return 8000;
  return 30000;
}

function normalizeActivityBatch(raw) {
  const batch = { ...emptyActivityBatch(), ...(raw || {}) };
  for (const key of ['windowMs', 'receivedCount', 'duplicateCount', 'uniqueCount', 'clusterCount', 'triageUpdatedCount', 'sampledCount', 'suppressedCount', 'ratePerSecond']) {
    batch[key] = Math.max(Number(batch[key] || 0), 0);
  }
  if (!['normal', 'burst', 'surge'].includes(batch.mode)) batch.mode = 'normal';
  return batch;
}

function resolveActivityMode(previous, batch) {
  const busy = batch.receivedCount > 5;
  const calmPolls = busy ? 0 : Math.min(previous.calmPolls + 1, 3);
  let mode = batch.mode;
  if (mode === 'normal' && previous.mode === 'surge' && calmPolls < 3) mode = 'surge';
  if (mode === 'normal' && previous.mode === 'burst' && calmPolls < 2) mode = 'burst';
  return { mode, calmPolls };
}

function enqueueActivity(previous, events, generatedAt, recentEvents, rawBatch) {
  const batch = normalizeActivityBatch(rawBatch);
  const modeState = resolveActivityMode(previous, batch);
  const hasBatch = batch.receivedCount > 0 || batch.triageUpdatedCount > 0;
  const incomingEvents = (events || []).filter(Boolean);
  const incomingRecentEvents = (recentEvents || []).filter(Boolean);
  if (
    !hasBatch
    && !incomingEvents.length
    && !incomingRecentEvents.length
    && previous.connection === 'online'
    && previous.mode === modeState.mode
    && previous.calmPolls === modeState.calmPolls
  ) return previous;
  const next = {
    ...previous,
    connection: 'online',
    generatedAt: generatedAt || previous.generatedAt,
    denoise: { ...previous.denoise, queue: [...previous.denoise.queue] },
    triage: { ...previous.triage, queue: [...previous.triage.queue] },
    recent: [...previous.recent],
    batch: hasBatch ? batch : previous.batch,
    batchUpdatedAt: hasBatch ? Date.now() : previous.batchUpdatedAt,
    mode: modeState.mode,
    calmPolls: modeState.calmPolls,
  };
  if (batch.mode !== 'normal' && batch.receivedCount > 0) next.denoise.queue = [];
  for (const event of incomingEvents) {
    if (!event || !['denoise', 'triage'].includes(event.stage) || !event.eventId) continue;
    const enriched = event.stage === 'denoise'
      ? { ...event, playbackMode: batch.mode, batch }
      : event;
    const lane = next[enriched.stage];
    const known = [lane.current, lane.last, ...lane.queue].some((item) => item?.eventId === enriched.eventId);
    if (!known) lane.queue.push(enriched);
  }
  for (const kind of ['denoise', 'triage']) {
    const lane = next[kind];
    if (lane.queue.length > ACTIVITY_QUEUE_LIMIT) {
      lane.queue = lane.queue.slice(-ACTIVITY_QUEUE_LIMIT);
    }
  }
  const incomingRecent = incomingRecentEvents.length ? incomingRecentEvents : [...incomingEvents].reverse();
  for (const event of [...incomingRecent].reverse()) {
    if (!event?.eventId) continue;
    const merged = [event, ...next.recent.filter((item) => item.eventId !== event.eventId)];
    next.recent = [
      ...merged.filter((item) => item.stage === 'triage').slice(0, 12),
      ...merged.filter((item) => item.stage === 'denoise').slice(0, 12),
    ].sort((left, right) => Date.parse(right.occurredAt || '') - Date.parse(left.occurredAt || ''));
  }
  return next;
}

function completeActivity(previous, kind, completed) {
  const lane = previous[kind];
  if (lane.current?.eventId !== completed.eventId) return previous;
  const [current = null, ...queue] = lane.queue;
  return {
    ...previous,
    [kind]: { ...lane, current, queue, last: completed },
  };
}

function recentUnseenActivity(events) {
  const fresh = (events || []).filter((event) => {
    const occurredAt = Date.parse(event?.occurredAt || '');
    return event?.eventId && Number.isFinite(occurredAt) && Date.now() - occurredAt <= ACTIVITY_REPLAY_WINDOW_MS;
  });
  if (!fresh.length) return [];
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(ACTIVITY_SEEN_KEY) || '[]');
    const seen = new Set(Array.isArray(stored) ? stored : []);
    const unseen = fresh.filter((event) => !seen.has(event.eventId));
    const nextSeen = [...seen, ...fresh.map((event) => event.eventId)].slice(-120);
    window.sessionStorage.setItem(ACTIVITY_SEEN_KEY, JSON.stringify(nextSeen));
    return [...unseen].reverse();
  } catch {
    return [...fresh].reverse();
  }
}

function todayLocal() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function toLocalInputValue(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function parseLocalInputValue(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function startOfToday(now) {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
}

function resolveRelativeWindow(range, now = new Date()) {
  const end = new Date(now);
  if (range === 'today') return [startOfToday(now), end];
  const spans = {
    '15m': 15 * 60 * 1000,
    '1h': 60 * 60 * 1000,
    '2h': 2 * 60 * 60 * 1000,
    '24h': 24 * 60 * 60 * 1000,
    '7d': 7 * 24 * 60 * 60 * 1000,
    '30d': 30 * 24 * 60 * 60 * 1000,
  };
  return [new Date(end.getTime() - (spans[range] || spans[DEFAULT_TIME_RANGE])), end];
}

function createRelativeTimeFilter(range = DEFAULT_TIME_RANGE) {
  const [start, end] = resolveRelativeWindow(range);
  return { mode: 'relative', range, start: toLocalInputValue(start), end: toLocalInputValue(end) };
}

function resolveTimeWindow(filter, now = new Date()) {
  if (filter.mode === 'relative') return resolveRelativeWindow(filter.range, now);
  const start = parseLocalInputValue(filter.start);
  const end = parseLocalInputValue(filter.end);
  if (!start || !end) return null;
  return start <= end ? [start, end] : [end, start];
}

function timeFilterParams(filter) {
  const window = resolveTimeWindow(filter);
  if (!window) return {};
  if (filter.mode === 'relative') {
    const bucketMinutes = {
      '15m': 1,
      '1h': 1,
      '2h': 1,
      '24h': 5,
      today: 1,
      '7d': 15,
      '30d': 60,
    }[filter.range] || 1;
    const bucketSeconds = bucketMinutes * 60;
    const endTime = Math.floor(window[1].getTime() / (bucketSeconds * 1000)) * bucketSeconds + bucketSeconds - 1;
    if (filter.range === 'today') {
      return {
        startTime: Math.floor(window[0].getTime() / 1000),
        endTime,
      };
    }
    const spanSeconds = Math.max(Math.round((window[1].getTime() - window[0].getTime()) / 1000), 1);
    return {
      startTime: endTime - spanSeconds + 1,
      endTime,
    };
  }
  return {
    startTime: Math.floor(window[0].getTime() / 1000),
    endTime: Math.floor(window[1].getTime() / 1000),
  };
}

function timeFilterLabel(filter) {
  if (filter.mode === 'relative') {
    if (filter.range === '1h') return '最近1小时';
    return TIME_RANGE_OPTIONS.find((option) => option.value === filter.range)?.label || '最近7天';
  }
  const window = resolveTimeWindow(filter);
  if (!window) return '精确时间';
  const format = (date) => `${date.getFullYear()}/${pad2(date.getMonth() + 1)}/${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  return `${format(window[0])} 至 ${format(window[1])}`;
}

function refreshLabel(value) {
  return REFRESH_OPTIONS.find((option) => option.value === value)?.label || '关闭';
}

function mergeStats(raw) {
  const denoise = { ...EMPTY_STATS.denoise, ...((raw || {}).denoise || {}) };
  const processedTotal = Math.max(Number(denoise.totalRaw || 0), 0);
  denoise.totalNormalized = processedTotal;
  return {
    ...EMPTY_STATS,
    ...(raw || {}),
    sourceStatus: { ...EMPTY_STATS.sourceStatus, ...((raw || {}).sourceStatus || {}) },
    denoise,
    triage: { ...EMPTY_STATS.triage, ...((raw || {}).triage || {}) },
    pipeline: { ...EMPTY_STATS.pipeline, ...((raw || {}).pipeline || {}) },
    closedLoop: { ...EMPTY_STATS.closedLoop, ...((raw || {}).closedLoop || {}) },
    dateRange: { ...EMPTY_STATS.dateRange, ...((raw || {}).dateRange || {}) },
    eventRange: { ...EMPTY_STATS.eventRange, ...((raw || {}).eventRange || {}) },
    timeline: { ...EMPTY_STATS.timeline, ...((raw || {}).timeline || {}) },
    sources: [
      { key: 'ndr', label: 'NDR', value: processedTotal, rate: processedTotal > 0 ? 1 : 0, active: processedTotal > 0 },
      { key: 'other', label: '其他接入', value: 0, rate: 0, active: false },
    ],
  };
}

function fullNumber(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat('zh-CN').format(n);
}

function workflowDenoiseActivity(callCount, delta, generatedAt, workflowEvent) {
  if (workflowEvent) {
    return {
      ...workflowEvent,
      eventId: `workflow-playback:${callCount}:${workflowEvent.eventId}`,
      statsDelta: delta,
      workflowCallCount: callCount,
    };
  }
  const occurredAt = generatedAt || new Date().toISOString();
  return {
    eventId: `workflow-denoise:${callCount}:${occurredAt}`,
    stage: 'denoise',
    status: 'completed',
    occurredAt,
    triggerSource: 'workflow_stats',
    statsDelta: delta,
    workflowCallCount: callCount,
    hiddenFromQueue: true,
    sampleCount: delta,
    alert: {
      sourceType: 'workflow.db',
      threatName: '降噪工作流统计更新',
    },
    result: {
      clusterId: `累计 ${fullNumber(callCount)}`,
      isDuplicate: false,
    },
  };
}

function compactNumber(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 100000000) return `${trim(n / 100000000)}亿`;
  if (Math.abs(n) >= 10000) return `${trim(n / 10000)}万`;
  return fullNumber(n);
}

function AnimatedNumber({ value, format, tag = 'span', className, duration = 900 }) {
  const { useEffect, useRef, useState } = getReact();
  const target = Number(value || 0);
  const current = useRef(0);
  const [display, setDisplay] = useState(0);
  const formatter = format || ((number) => compactNumber(Math.round(number)));

  useEffect(() => {
    const from = current.current;
    if (!Number.isFinite(target) || from === target) {
      current.current = Number.isFinite(target) ? target : 0;
      setDisplay(current.current);
      return undefined;
    }
    let frame = 0;
    const startedAt = window.performance.now();
    const tick = (now) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - ((1 - progress) ** 3);
      const next = from + (target - from) * eased;
      current.current = next;
      setDisplay(next);
      if (progress < 1) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [target, duration]);

  return h(tag, {
    className: cx('animated-number', className),
    title: formatter(target),
  }, formatter(display));
}

function trim(value) {
  return Number(value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)).toString();
}

function pct(value) {
  return `${Math.round(Number(value || 0) * 1000) / 10}%`;
}

function ratio(part, total) {
  const denominator = Number(total || 0);
  if (denominator <= 0) return 0;
  return Number(part || 0) / denominator;
}

function clamp(value, min = 0, max = 1) {
  return Math.max(min, Math.min(max, Number(value || 0)));
}

function cx(...items) {
  return items.filter(Boolean).join(' ');
}

function Header({ startDate, endDate, setStartDate, setEndDate, stats, loading, refresh, error }) {
  const totalSaved = (stats.denoise.duplicates || 0) + (stats.triage.cacheHit || 0) + (stats.triage.followersReused || 0);
  const savedHours = trim(totalSaved * 0.018);
  const rangeLabel = stats.eventRange?.label || stats.dateRange?.label || (startDate === endDate ? startDate : `${startDate} 至 ${endDate}`);
  return h('header', { className: 'adtd-header' }, [
    h('div', { className: 'brand', key: 'brand' }, [
      h('div', { className: 'brand-mark', key: 'mark' }, 'AI'),
      h('div', { key: 'copy' }, [
        h('div', { className: 'brand-title', key: 'title' }, 'Flocks 智能告警运营态势'),
        h('div', { className: 'brand-sub', key: 'sub' }, '告警降噪与智能研判运营大屏'),
      ]),
    ]),
    h('div', { className: 'header-center', key: 'center' }, [
      h('span', { className: 'chip', key: 'chip-a' }, `累计节省 ${compactNumber(totalSaved)} 条人工处理`),
      h('span', { className: 'chip strong', key: 'chip-b' }, `${savedHours} 小时运营时间`),
      error ? h('span', { className: 'chip warn', key: 'chip-c' }, '数据刷新异常') : null,
    ]),
    h('div', { className: 'header-tools', key: 'tools' }, [
      h('div', { className: 'date-range', key: 'range' }, [
        h('input', {
          key: 'start',
          className: 'date-input',
          type: 'date',
          value: startDate,
          onChange: (event) => {
            const next = event.target.value || todayLocal();
            setStartDate(next);
            if (endDate < next) setEndDate(next);
          },
        }),
        h('span', { className: 'range-sep', key: 'sep' }, '至'),
        h('input', {
          key: 'end',
          className: 'date-input',
          type: 'date',
          value: endDate,
          onChange: (event) => {
            const next = event.target.value || startDate;
            setEndDate(next);
            if (next < startDate) setStartDate(next);
          },
        }),
      ]),
      h('button', { key: 'refresh', className: 'icon-button', type: 'button', onClick: refresh, disabled: loading }, loading ? '刷新中' : '刷新'),
      h('div', { className: 'clock', key: 'clock' }, [
        h('b', { key: 'time' }, stats.generatedAt ? stats.generatedAt.slice(11, 19) : '--:--:--'),
        h('span', { key: 'date-label' }, rangeLabel),
      ]),
    ]),
  ]);
}

function Metric({ label, value, unit, tone, sub }) {
  return h('div', { className: cx('metric', tone && `metric-${tone}`) }, [
    h('span', { className: 'metric-label', key: 'label' }, label),
    h('strong', { className: 'metric-value', key: 'value' }, [compactNumber(value), unit ? h('em', { key: 'unit' }, unit) : null]),
    sub ? h('span', { className: 'metric-sub', key: 'sub' }, sub) : null,
  ]);
}

function Panel({ title, meta, children, className }) {
  return h('section', { className: cx('panel', className) }, [
    h('div', { className: 'panel-head', key: 'head' }, [
      h('div', { className: 'panel-title', key: 'title' }, [h('i', { key: 'dot' }), h('span', { key: 'text' }, title)]),
      meta ? h('div', { className: 'panel-meta', key: 'meta' }, meta) : null,
    ]),
    h('div', { className: 'panel-body', key: 'body' }, children),
  ]);
}

function SourceColumn({ stats }) {
  const total = stats.sources.reduce((sum, item) => sum + (item.value || 0), 0);
  return h('div', { className: 'column left-col' }, [
    h(Panel, { key: 'sources', title: '多源告警接入', meta: `${stats.denoise.files || 0} 个降噪批次` }, [
      h('div', { className: 'source-list', key: 'list' }, stats.sources.map((item) => {
        const width = `${Math.max(4, Math.round((item.rate || 0) * 100))}%`;
        return h('div', { className: 'source-row', key: item.key }, [
          h('div', { className: cx('source-node', item.active && 'active'), key: 'node' }),
          h('div', { className: 'source-main', key: 'main' }, [
            h('div', { className: 'source-line', key: 'line' }, [
              h('span', { key: 'label' }, item.label),
              h('b', { key: 'value' }, compactNumber(item.value)),
            ]),
            h('div', { className: 'source-track', key: 'track' }, h('span', { style: { width }, key: 'bar' })),
          ]),
        ]);
      })),
      h('div', { className: 'source-total', key: 'total' }, [
        h('span', { key: 'label' }, '接入总量'),
        h('b', { key: 'value' }, compactNumber(stats.denoise.totalRaw)),
      ]),
    ]),
    h(Panel, { key: 'raw', title: '告警接入趋势', meta: stats.timeline.window || '按批次统计' }, [
      h(Sparkline, { values: stats.timeline.denoiseRaw, color: '#2be7ff', key: 'spark' }),
      h('div', { className: 'mini-metrics', key: 'mini' }, [
        h(Metric, { label: '原始告警', value: stats.denoise.totalRaw, tone: 'cyan', key: 'a' }),
        h(Metric, { label: '唯一告警', value: stats.denoise.totalUnique, tone: 'green', key: 'b' }),
      ]),
    ]),
    h(Panel, { key: 'efficiency', title: '降噪效率', meta: '压缩收益' }, h('div', { className: 'side-summary' }, [
      h(SummaryTile, { key: 'dup', label: '降噪收敛', value: pct(stats.denoise.duplicateRate), sub: `${compactNumber(stats.denoise.duplicates)} 条过滤/收敛`, tone: 'green' }),
      h(SummaryTile, { key: 'unique', label: '唯一留存', value: pct(stats.denoise.uniqueRate), sub: `${compactNumber(stats.denoise.totalUnique)} 条进入研判`, tone: 'cyan' }),
      h(SummaryTile, { key: 'batch', label: '工作流批次', value: compactNumber(stats.denoise.files), sub: '当前时间范围', tone: 'violet' }),
    ])),
  ]);
}

function CenterColumn({ stats, activity }) {
  const radar = [
    { label: '降噪', value: 1 - clamp(stats.denoise.duplicateRate) },
    { label: '复用', value: stats.pipeline.workloadReuseRate },
    { label: '覆盖', value: stats.pipeline.coverageRate },
    { label: '攻击', value: stats.pipeline.attackRate },
    { label: '成功', value: stats.pipeline.successRate },
  ];
  return h('div', { className: 'column center-col' }, [
    h('div', { className: 'ai-stage', key: 'stage' }, [
      h(ActivityStageCard, {
        key: 'stage1',
        kind: 'denoise',
        lane: activity.denoise,
        stats,
      }),
      h(AiCore, { key: 'core', stats, activity }),
      h(ActivityStageCard, {
        key: 'stage2',
        kind: 'triage',
        lane: activity.triage,
        stats,
      }),
    ]),
    h('div', { className: 'flow-strip', key: 'flow' }, [
      h('span', { key: 'a' }, [h('small', { key: 'l' }, '原始告警'), h('b', { key: 'v' }, compactNumber(stats.denoise.totalRaw))]),
      h('i', { key: 'arrow-a' }),
      h('span', { key: 'b' }, [h('small', { key: 'l' }, '唯一告警'), h('b', { key: 'v' }, compactNumber(stats.denoise.totalUnique))]),
      h('i', { key: 'arrow-b' }),
      h('span', { key: 'c' }, [h('small', { key: 'l' }, 'AI 研判'), h('b', { key: 'v' }, compactNumber(stats.triage.totalRecords))]),
      h('i', { key: 'arrow-c' }),
      h('span', { key: 'd', className: 'flow-success', title: '攻击成功' }, [h('small', { key: 'l' }, '攻击成功'), h('b', { key: 'v' }, compactNumber(stats.triage.attackSuccess))]),
    ]),
    h('div', { className: 'dashboard-grid', key: 'dash' }, [
      h(Panel, { title: '攻击判定环', meta: '判定分布', className: 'panel-donut', key: 'donut' }, h(Donut, { data: stats.verdicts })),
      h(Panel, { title: 'AI 能效雷达', meta: '能力评分', className: 'panel-radar', key: 'radar' }, h(Radar, { data: radar })),
      h(Panel, { title: '攻击画像', meta: '案例维度', className: 'panel-profile', key: 'profile' }, h(ProfileMatrix, { data: stats.attackProfile })),
    ]),
  ]);
}

function RightColumn({ stats }) {
  const threatRows = stats.topThreats || [];
  const threatRankLabel = threatRows.length ? `Top ${threatRows.length}` : '排行';
  const threatTotal = threatRows.reduce((sum, item) => sum + (item.value || 0), 0);
  const threatBase = stats.triage.totalRecords || stats.triage.attackTotal || threatTotal;
  const topThreat = threatRows[0] || { value: 0, rate: 0 };
  const tailThreats = Math.max(threatBase - threatTotal, 0);
  return h('div', { className: 'column right-col' }, [
    h(Panel, { key: 'loop', title: '处置闭环', meta: pct(stats.closedLoop.resolutionRate) }, [
      h('div', { className: 'loop-diagram', key: 'diagram' }, [
        h('div', { className: 'loop-node primary', key: 'valid' }, [h('b', { key: 'v' }, compactNumber(stats.triage.attackTotal)), h('span', { key: 'l' }, '有效事件')]),
        h('div', { className: 'loop-node', key: 'auto' }, [h('b', { key: 'v' }, compactNumber(stats.closedLoop.autoClosed)), h('span', { key: 'l' }, '自动闭环')]),
        h('div', { className: 'loop-node warn', key: 'manual' }, [h('b', { key: 'v' }, compactNumber(stats.closedLoop.manualDecision)), h('span', { key: 'l' }, '人工决策')]),
        h('div', { className: 'loop-node hot', key: 'pending' }, [h('b', { key: 'v' }, compactNumber(stats.closedLoop.pending)), h('span', { key: 'l' }, '待处理')]),
      ]),
      h(Gauge, { label: '闭环率', value: stats.closedLoop.resolutionRate, color: '#2ee6a6', key: 'gauge' }),
    ]),
    h(Panel, { key: 'threats', title: '威胁类型排行', meta: threatRankLabel }, [
      h('div', { className: 'rank-list', key: 'list', style: { '--rank-count': Math.max(threatRows.length, 1) } }, threatRows.length ? threatRows.map((item, index) => h('div', { className: 'rank-row', key: item.label }, [
        h('span', { key: 'idx' }, String(index + 1).padStart(2, '0')),
        h('b', { key: 'label', title: item.label }, item.label),
        h('em', { key: 'value' }, compactNumber(item.value)),
      ])) : h('div', { className: 'empty', key: 'empty' }, '暂无威胁分类数据')),
      h('div', { className: 'rank-summary', key: 'summary' }, [
        h('div', { className: 'rank-stat', key: 'cover' }, [h('span', { key: 'l' }, `${threatRankLabel}覆盖`), h('b', { key: 'v' }, pct(ratio(threatTotal, threatBase)))]),
        h('div', { className: 'rank-stat', key: 'lead' }, [h('span', { key: 'l' }, '首位占比'), h('b', { key: 'v' }, pct(topThreat.rate || ratio(topThreat.value, threatBase)))]),
        h('div', { className: 'rank-stat', key: 'tail' }, [h('span', { key: 'l' }, '长尾余量'), h('b', { key: 'v' }, compactNumber(tailThreats))]),
      ]),
    ]),
  ]);
}

function SummaryTile({ label, value, sub, tone }) {
  return h('div', { className: cx('summary-tile', tone && `summary-${tone}`) }, [
    h('span', { key: 'label' }, label),
    h('b', { key: 'value' }, value),
    h('small', { key: 'sub' }, sub),
  ]);
}

function StageCard({ stage, title, value, sub, tone }) {
  return h('div', { className: cx('stage-card', `stage-${tone}`) }, [
    h('span', { className: 'stage-label', key: 'stage' }, stage),
    h('strong', { key: 'title' }, title),
    h('b', { key: 'value' }, compactNumber(value)),
    h('small', { key: 'sub' }, sub),
  ]);
}

function activityResultText(event) {
  if (!event) return '';
  if (event.stage === 'denoise') {
    if (event.triggerSource === 'workflow_execution') {
      return '';
    }
    if (event.triggerSource === 'workflow_stats') {
      return `工作流调用 +${fullNumber(event.statsDelta || 1)}`;
    }
    const count = Math.max(Number(event.sampleCount || 1), 1);
    const result = event.result?.isDuplicate ? '重复告警已收敛' : '保留代表告警';
    return count > 1 ? `${result} × ${count}` : result;
  }
  if (event.status === 'failed') return '研判未完成';
  const risk = { high: '高风险', medium: '中风险', low: '低风险' }[String(event.result?.riskLevel || '').toLowerCase()]
    || event.result?.riskLevel
    || '风险待确认';
  return `${risk} · ${event.result?.verdictLabel || '待确认'}`;
}

function activitySourceText(event) {
  if (!event) return '';
  if (event.stage === 'denoise') {
    if (['workflow_stats', 'workflow_execution'].includes(event.triggerSource)) return 'WORKFLOW.DB';
    return event.alert?.sourceType ? String(event.alert.sourceType).toUpperCase() : '新告警';
  }
  const source = event.result?.triageSource;
  if (['cache', 'cached'].includes(source)) return '历史研判复用';
  if (['follower', 'follower_reused'].includes(source)) return '同类结论复用';
  const seconds = Math.round(Number(event.result?.durationMs || 0) / 1000);
  return seconds > 0 ? `AI 研判 ${seconds}s` : 'AI 研判';
}

function ActivityStageCard({ kind, lane, stats }) {
  const event = lane.current || lane.last;
  if (!event) {
    return h(StageCard, {
      stage: kind === 'denoise' ? '阶段一' : '阶段二',
      title: kind === 'denoise' ? '智能降噪' : '智能研判',
      value: kind === 'denoise' ? stats.denoise.totalUnique : stats.triage.totalRecords,
      sub: kind === 'denoise' ? `降噪收敛 ${pct(stats.denoise.duplicateRate)}` : `缓存复用 ${pct(stats.triage.cacheRate)}`,
      tone: kind === 'denoise' ? 'green' : 'violet',
    });
  }

  const active = Boolean(lane.current);
  const steps = kind === 'denoise' ? ['告警接入', '特征提取', '相似聚类', '降噪结果'] : ['证据提取', '情报关联', 'AI 推理', '生成结论'];
  const endpoint = [event.alert?.srcIp, event.alert?.dstIp].filter(Boolean).join(' → ');
  const duration = activityDuration(event);
  const outcome = activityResultText(event);
  return h('div', {
    key: event.eventId,
    className: cx('stage-card', 'activity-card', kind === 'denoise' ? 'stage-green' : 'stage-violet', active && 'activity-active', event.status === 'failed' && 'activity-failed'),
    style: { '--activity-outcome-delay': `${Math.round(duration * 0.65)}ms` },
  }, [
    h('div', { className: 'activity-card-head', key: 'head' }, [
      h('span', { className: 'stage-label', key: 'stage' }, kind === 'denoise' ? '智能降噪' : '智能研判'),
      h('span', { className: cx('activity-live', active && 'live'), key: 'live' }, active ? '处理中' : '最近完成'),
    ]),
    h('strong', { className: 'activity-title', title: event.alert?.threatName, key: 'title' }, event.alert?.threatName || '未知告警'),
    h('div', { className: 'activity-meta', key: 'meta' }, [
      h('span', { key: 'source' }, activitySourceText(event)),
      endpoint ? h('span', { title: endpoint, key: 'endpoint' }, endpoint) : null,
    ]),
    h('div', { className: 'activity-stepper', key: 'steps' }, steps.map((step, index) => h('span', {
      className: 'activity-step',
      key: step,
      style: active ? { animationDelay: `${Math.round(index * duration * 0.18)}ms` } : undefined,
    }, [h('i', { key: 'dot' }), step]))),
    outcome ? h('div', {
      className: cx('activity-outcome', event.status === 'failed' && 'failed'),
      key: 'outcome',
    }, outcome) : null,
  ]);
}

function AiCore({ stats, activity }) {
  const denoiseActive = Boolean(activity.denoise.current);
  const triageActive = Boolean(activity.triage.current);
  const activeCount = Number(denoiseActive) + Number(triageActive);
  const activeEvent = activity.denoise.current || activity.triage.current;
  const activeKind = activeEvent?.stage || '';
  const queueCount = activity.denoise.queue.length + activity.triage.queue.length;
  const coreLabel = activeCount === 2
    ? '双任务处理中'
    : denoiseActive
      ? activity.mode === 'surge' ? '降噪洪峰处理中' : activity.mode === 'burst' ? '告警批量降噪中' : '告警降噪中'
      : triageActive ? '告警研判中' : '智能研判核心';
  const taskDuration = activityDuration(activeEvent);
  const workflowDenoiseActive = activeKind === 'denoise'
    && ['workflow_stats', 'workflow_execution'].includes(activeEvent?.triggerSource);
  const workflowExecutionActive = workflowDenoiseActive && activeEvent?.triggerSource === 'workflow_execution';
  const workflowMetricsAvailable = workflowExecutionActive && activeEvent?.result?.metricsAvailable;
  const alertName = activeEvent?.alert?.threatName || '未知告警';
  const alertSource = activeEvent?.alert?.sourceType || '未知来源';
  const sourceAddress = activeEvent?.alert?.srcIp || '待识别';
  const targetAddress = activeEvent?.alert?.dstIp || '待识别';
  const operations = workflowExecutionActive && !workflowMetricsAvailable
    ? [
        `接入告警 ${alertName}`,
        `识别来源 ${alertSource}`,
        `关联资产 ${sourceAddress} → ${targetAddress}`,
        '等待可用降噪结果',
      ]
    : workflowExecutionActive
    ? [
        `接入原始告警 ${fullNumber(activeEvent?.result?.rawCount)}`,
        `完成标准化 ${fullNumber(activeEvent?.result?.normalizedCount)}`,
        `过滤与收敛 ${fullNumber(activeEvent?.result?.reducedCount)}`,
        `留存研判告警 ${fullNumber(activeEvent?.result?.uniqueCount)}`,
      ]
    : workflowDenoiseActive
    ? [
        '检测 workflow.db 统计更新',
        `读取降噪调用增量 +${fullNumber(activeEvent?.statsDelta || 1)}`,
        '同步降噪处理状态',
        `累计调用 ${fullNumber(activeEvent?.workflowCallCount)}`,
      ]
    : activeKind === 'denoise'
    ? [
        `接入 ${activeEvent?.alert?.sourceType || '告警数据'}`,
        '提取请求与网络特征',
        `匹配相似簇 ${activeEvent?.result?.clusterId || '--'}`,
        activeEvent?.result?.isDuplicate ? '输出：重复告警收敛' : '输出：保留代表告警',
      ]
    : [
        '提取攻击证据',
        '关联历史情报与资产',
        '执行风险推理',
        `生成结论：${activeEvent?.result?.verdictLabel || '待确认'}`,
      ];
  const evidenceItems = workflowExecutionActive && !workflowMetricsAvailable
    ? [
        { label: '告警名称', value: alertName },
        { label: '来源类型', value: alertSource },
        { label: '源地址', value: sourceAddress },
        { label: '目标地址', value: targetAddress },
      ]
    : workflowExecutionActive
    ? [
        { label: '原始告警', value: fullNumber(activeEvent?.result?.rawCount) },
        { label: '过滤数量', value: fullNumber(activeEvent?.result?.filterRemovedCount) },
        { label: '去重数量', value: fullNumber(activeEvent?.result?.duplicateCount) },
        { label: '降噪率', value: pct(activeEvent?.result?.reductionRate) },
      ]
    : workflowDenoiseActive
    ? [
        { label: '本次增量', value: `+${fullNumber(activeEvent?.statsDelta || 1)}` },
        { label: '累计处理', value: fullNumber(activeEvent?.workflowCallCount) },
        { label: '处理模式', value: activity.mode === 'surge' ? '洪峰' : activity.mode === 'burst' ? '批量' : '实时' },
        { label: '当前队列', value: fullNumber(queueCount) },
      ]
    : activeEvent ? [
        { label: '攻击源', value: activeEvent.alert?.srcIp || activeEvent.alert?.sourceType || '新告警' },
        { label: '目标资产', value: activeEvent.alert?.dstIp || '待识别资产' },
        { label: activeKind === 'denoise' ? '特征' : '攻击路径', value: activeEvent.alert?.requestUri || activeEvent.alert?.threatName || '特征提取中' },
        { label: activeKind === 'denoise' ? '相似聚类' : '风险判断', value: activeKind === 'denoise' ? `簇 ${activeEvent.result?.clusterId || '--'}` : activityResultText(activeEvent) },
      ] : [];
  const statusLabel = activity.connection === 'error'
    ? '活动数据等待重连'
    : activeCount
      ? coreLabel
      : activity.connection === 'initializing'
        ? '活动通道初始化'
        : activity.mode === 'surge' ? '洪峰缓冲处理中' : activity.mode === 'burst' ? '批量任务处理中' : '实时活动待机';
  const visibleBatch = activity.batch?.receivedCount > 0 ? activity.batch : null;
  return h('div', {
    className: cx('ai-core', activeCount && 'core-processing', activeCount === 2 && 'core-dual', activeEvent && 'core-task-active', activeKind && `core-task-${activeKind}`, `core-load-${activity.mode}`),
    style: {
      '--core-task-duration': `${taskDuration || 8000}ms`,
      '--core-task-accent': activeKind === 'triage' ? '#9b8cff' : '#2be7ff',
    },
  }, [
    h('div', { className: cx('core-live-status', `status-${activity.connection}`), key: 'status' }, [
      h('i', { key: 'dot' }),
      h('span', { key: 'label' }, statusLabel),
      queueCount ? h('b', { key: 'queue' }, `队列 ${queueCount}`) : null,
    ]),
    activeEvent ? h('svg', {
      className: 'ai-task-progress',
      viewBox: '0 0 340 340',
      key: activeKind === 'denoise' ? 'progress-denoise' : `progress-${activeEvent.eventId}`,
    }, [
      h('circle', { className: 'ai-task-progress-base', cx: 170, cy: 170, r: 157, pathLength: 100, key: 'base' }),
      h('circle', { className: 'ai-task-progress-value', cx: 170, cy: 170, r: 157, pathLength: 100, key: 'value' }),
    ]) : null,
    h('div', { className: 'energy-ring ring-a', key: 'ring-a' }),
    h('div', { className: 'energy-ring ring-b', key: 'ring-b' }),
    h('div', { className: 'scan-line', key: 'scan' }),
    h('div', { className: 'orbit orbit-a', key: 'orbit-a' }),
    h('div', { className: 'orbit orbit-b', key: 'orbit-b' }),
    h('div', { className: 'ai-sphere', key: 'sphere' }, [
      h('span', { key: 'ai' }, 'AI'),
      h('small', { key: 'label' }, coreLabel),
      activeEvent ? h('div', { className: 'ai-operation-window', key: `operation-${activeEvent.eventId}` }, [
        h('div', { className: 'ai-operation-track', key: 'track' }, [...operations, operations[0]].map((operation, index) => h('span', { key: `${operation}-${index}` }, operation))),
      ]) : h('div', { className: 'ai-operation-idle', key: 'operation-idle' }, '等待新的处理任务'),
    ]),
    activeEvent ? h('div', { className: 'ai-evidence-field', key: `evidence-${activeEvent.eventId}` }, evidenceItems.map((item, index) => h('div', {
      className: `ai-evidence-card evidence-${index + 1}`,
      key: item.label,
      style: { animationDelay: `${180 + index * 220}ms` },
    }, [
      h('span', { key: 'label' }, item.label),
      h('b', { title: item.value, key: 'value' }, item.value),
    ]))) : null,
    h('div', { className: 'core-particle particle-a', key: 'particle-a' }),
    h('div', { className: 'core-particle particle-b', key: 'particle-b' }),
    h('div', { className: 'core-particle particle-c', key: 'particle-c' }),
    h('div', { className: 'core-numbers', key: 'numbers' }, [
      h('div', { key: 'left' }, [h('b', { key: 'v' }, pct(stats.pipeline.coverageRate)), h('span', { key: 'l' }, '覆盖率')]),
      h('div', { key: 'right' }, [h('b', { key: 'v' }, pct(stats.pipeline.successRate)), h('span', { key: 'l' }, '成功率')]),
    ]),
    visibleBatch ? h('div', { className: 'core-batched', key: `batched-${activity.batchUpdatedAt}` }, [
      h('b', { key: 'rate' }, `${trim(visibleBatch.ratePerSecond)} 条/秒`),
      h('span', { key: 'received' }, `本批 ${fullNumber(visibleBatch.receivedCount)}`),
      h('span', { key: 'duplicate' }, `收敛 ${fullNumber(visibleBatch.duplicateCount)}`),
      h('span', { key: 'cluster' }, `${fullNumber(visibleBatch.clusterCount)} 类`),
    ]) : null,
  ]);
}

function Sparkline({ values, color }) {
  const nums = (values || []).map((v) => Number(v || 0));
  const max = Math.max(...nums, 1);
  const points = nums.length
    ? nums.map((value, index) => {
        const x = nums.length === 1 ? 150 : (index / (nums.length - 1)) * 300;
        const y = 88 - (value / max) * 72;
        return `${x},${y}`;
      }).join(' ')
    : '0,88 300,88';
  return h('svg', { className: 'sparkline', viewBox: '0 0 300 96', role: 'img' }, [
    h('path', { key: 'grid', d: 'M0 88 H300 M0 56 H300 M0 24 H300', className: 'spark-grid' }),
    h('polyline', { key: 'line', className: 'spark-line', points, fill: 'none', stroke: color || '#2be7ff', strokeWidth: 3, strokeLinecap: 'round', strokeLinejoin: 'round' }),
  ]);
}

function polarPoint(cx, cy, radius, angle) {
  const radians = (angle - 90) * Math.PI / 180;
  return {
    x: cx + radius * Math.cos(radians),
    y: cy + radius * Math.sin(radians),
  };
}

function ringSegmentPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle) {
  const outerStart = polarPoint(cx, cy, outerRadius, startAngle);
  const outerEnd = polarPoint(cx, cy, outerRadius, endAngle);
  const innerEnd = polarPoint(cx, cy, innerRadius, endAngle);
  const innerStart = polarPoint(cx, cy, innerRadius, startAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return [
    `M ${outerStart.x} ${outerStart.y}`,
    `A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y}`,
    `L ${innerEnd.x} ${innerEnd.y}`,
    `A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${innerStart.x} ${innerStart.y}`,
    'Z',
  ].join(' ');
}

function Donut({ data }) {
  const { useState } = getReact();
  const rows = (data || []).filter((item) => item.value > 0);
  const total = rows.reduce((sum, item) => sum + item.value, 0);
  const [activeKey, setActiveKey] = useState('');
  const active = rows.find((item) => item.key === activeKey) || rows[0] || null;
  const activeRate = active && total ? active.value / total : 0;
  const centerX = 60;
  const centerY = 60;
  const innerRadius = 32;
  const outerRadius = 50;
  let cursor = 0;
  const arcs = rows.map((item) => {
    const share = total ? item.value / total : 0;
    const start = cursor;
    const end = cursor + share * 360;
    const gap = rows.length > 1 ? Math.min(2.4, (end - start) * 0.18) : 0;
    const arcStart = start + gap / 2;
    const arcEnd = Math.max(arcStart + 0.1, end - gap / 2);
    const mid = (arcStart + arcEnd) / 2;
    const label = polarPoint(centerX, centerY, 56, mid);
    const lineStart = polarPoint(centerX, centerY, outerRadius + 1, mid);
    const lineEnd = polarPoint(centerX, centerY, 53, mid);
    const selected = active?.key === item.key;
    cursor = end;
    return h('g', { key: item.key, className: 'donut-segment' }, [
      h('path', {
        key: 'arc',
        className: cx('donut-arc', selected && 'active'),
        d: ringSegmentPath(centerX, centerY, innerRadius, outerRadius, arcStart, arcEnd),
        fill: item.color,
        role: 'button',
        tabIndex: 0,
        'aria-label': `${item.label} ${compactNumber(item.value)}，占比 ${pct(share)}`,
        onClick: () => setActiveKey(item.key),
        onMouseEnter: () => setActiveKey(item.key),
        onFocus: () => setActiveKey(item.key),
        onKeyDown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setActiveKey(item.key);
          }
        },
      }),
      share >= 0.04 ? h('line', {
        key: 'line',
        className: 'donut-label-line',
        x1: lineStart.x,
        y1: lineStart.y,
        x2: lineEnd.x,
        y2: lineEnd.y,
        stroke: item.color,
      }) : null,
      share >= 0.04 ? h('text', {
        key: 'rate',
        className: cx('donut-percent', selected && 'active'),
        x: label.x,
        y: label.y,
        textAnchor: label.x >= centerX ? 'start' : 'end',
        dominantBaseline: 'middle',
        fill: item.color,
      }, pct(share)) : null,
    ]);
  });
  const emptyArc = total ? null : h('circle', {
      key: 'empty',
      className: 'donut-empty',
      cx: centerX,
      cy: centerY,
      r: 42,
      fill: 'none',
      stroke: 'rgba(130,170,210,.16)',
      strokeWidth: 12,
      strokeLinecap: 'round',
  });
  return h('div', { className: 'donut-wrap' }, [
    h('svg', { viewBox: '-8 -8 136 136', className: 'donut', key: 'svg' }, [
      h('circle', { key: 'base', cx: centerX, cy: centerY, r: 41, fill: 'none', stroke: 'rgba(130,170,210,.14)', strokeWidth: 18 }),
      ...arcs,
      emptyArc,
      h('text', { key: 'text-a', x: 60, y: 53, textAnchor: 'middle', className: 'donut-number' }, active ? compactNumber(active.value) : compactNumber(total)),
      h('text', { key: 'text-b', x: 60, y: 70, textAnchor: 'middle', className: 'donut-label' }, active ? active.label : '研判结果'),
      h('text', { key: 'text-c', x: 60, y: 86, textAnchor: 'middle', className: 'donut-rate' }, active ? pct(activeRate) : pct(total ? 1 : 0)),
    ]),
    h('div', { className: 'legend', key: 'legend' }, (data || []).map((item) => {
      const selected = active?.key === item.key;
      return h('div', {
        className: cx('legend-item', selected && 'active'),
        key: item.key,
        role: 'button',
        tabIndex: 0,
        onClick: () => setActiveKey(item.key),
        onKeyDown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setActiveKey(item.key);
          }
        },
      }, [
      h('i', { style: { background: item.color }, key: 'dot' }),
      h('span', { key: 'label' }, item.label),
      h('b', { key: 'value' }, compactNumber(item.value)),
      h('em', { key: 'rate' }, pct(total ? item.value / total : 0)),
    ]);
    })),
  ]);
}

function Radar({ data }) {
  const centerX = 76;
  const centerY = 84;
  const radius = 48;
  const labelRadius = radius + 15;
  const ringPoints = (scale) => data.map((item, index) => {
    const angle = (-90 + index * (360 / data.length)) * Math.PI / 180;
    const r = radius * scale;
    return `${centerX + Math.cos(angle) * r},${centerY + Math.sin(angle) * r}`;
  }).join(' ');
  const points = data.map((item, index) => {
    const angle = (-90 + index * (360 / data.length)) * Math.PI / 180;
    const value = clamp(item.value);
    return `${centerX + Math.cos(angle) * radius * value},${centerY + Math.sin(angle) * radius * value}`;
  }).join(' ');
  const spokes = data.map((item, index) => {
    const angle = (-90 + index * (360 / data.length)) * Math.PI / 180;
    const x = centerX + Math.cos(angle) * radius;
    const y = centerY + Math.sin(angle) * radius;
    const lx = centerX + Math.cos(angle) * labelRadius;
    const ly = centerY + Math.sin(angle) * labelRadius;
    return h('g', { key: item.label }, [
      h('line', { key: 'line', x1: centerX, y1: centerY, x2: x, y2: y, className: 'radar-line' }),
      h('text', { key: 'text', x: lx, y: ly, textAnchor: 'middle', dominantBaseline: 'middle', className: 'radar-text' }, item.label),
    ]);
  });
  return h('svg', { viewBox: '0 0 152 168', className: 'radar', role: 'img' }, [
    h('polygon', { key: 'grid1', points: ringPoints(1), className: 'radar-grid' }),
    h('polygon', { key: 'grid2', points: ringPoints(0.58), className: 'radar-grid' }),
    ...spokes,
    h('polygon', { key: 'value', points, className: 'radar-value' }),
  ]);
}

function ProfileMatrix({ data }) {
  const groups = (data || []).filter((group) => group && (group.items || []).length);
  if (!groups.length) {
    return h('div', { className: 'empty' }, '暂无攻击画像数据');
  }
  return h('div', { className: 'profile-grid' }, groups.map((group) => {
    const color = group.color || '#2be7ff';
    return h('div', { className: 'profile-group', key: group.key, style: { '--profile-color': color } }, [
      h('div', { className: 'profile-head', key: 'head' }, [
        h('span', { key: 'label' }, group.label),
        h('b', { key: 'total' }, compactNumber(group.total)),
      ]),
      h('div', { className: 'profile-items', key: 'items' }, group.items.map((item) => h('div', { className: 'profile-row', key: item.key }, [
        h('div', { className: 'profile-line', key: 'line' }, [
          h('span', { key: 'label', title: item.label }, item.label),
          h('b', { key: 'value' }, `${compactNumber(item.value)} · ${pct(item.rate)}`),
        ]),
        h('div', { className: 'profile-track', key: 'track' }, h('span', { style: { width: `${Math.max(5, Math.round(clamp(item.rate) * 100))}%` }, key: 'bar' })),
      ]))),
    ]);
  }));
}

function Funnel({ data }) {
  const max = Math.max(...(data || []).map((item) => item.value || 0), 1);
  return h('div', { className: 'funnel' }, (data || []).map((item) => {
    const width = `${Math.max(16, Math.round((item.value / max) * 100))}%`;
    return h('div', { className: 'funnel-row', key: item.label }, [
      h('span', { key: 'label' }, item.label),
      h('div', { className: 'funnel-bar', style: { width, borderColor: item.color, background: `linear-gradient(90deg, ${item.color}33, ${item.color}aa)` }, key: 'bar' }),
      h('b', { key: 'value' }, compactNumber(item.value)),
    ]);
  }));
}

function Gauge({ label, value, color }) {
  const radius = 42;
  const circumference = Math.PI * radius;
  const dash = clamp(value) * circumference;
  return h('div', { className: 'gauge-wrap' }, [
    h('svg', { viewBox: '0 0 120 72', className: 'gauge', key: 'svg' }, [
      h('path', { key: 'base', d: 'M18 60 A42 42 0 0 1 102 60', fill: 'none', stroke: 'rgba(130,170,210,.16)', strokeWidth: 12, strokeLinecap: 'round' }),
      h('path', {
        key: 'value',
        className: 'gauge-value',
        d: 'M18 60 A42 42 0 0 1 102 60',
        fill: 'none',
        stroke: color || '#2ee6a6',
        strokeWidth: 12,
        strokeLinecap: 'round',
        strokeDasharray: `${dash} ${circumference - dash}`,
      }),
    ]),
    h('div', { className: 'gauge-label', key: 'label' }, [h('b', { key: 'v' }, pct(value)), h('span', { key: 'l' }, label)]),
  ]);
}

function formatDurationMs(value) {
  const totalSeconds = Math.max(Math.round(Number(value || 0) / 1000), 0);
  if (!totalSeconds) return '--';
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function eventTimeLabel(value) {
  if (!value) return '--:--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function eventEndpoint(event) {
  return [event?.alert?.srcIp, event?.alert?.dstIp].filter(Boolean).join(' → ') || '未提供网络端点';
}

function eventMatchesTimeFilter(event, filter) {
  const occurredAt = Date.parse(event?.occurredAt || '');
  const window = resolveTimeWindow(filter);
  return Number.isFinite(occurredAt)
    && Boolean(window)
    && occurredAt >= window[0].getTime()
    && occurredAt <= window[1].getTime();
}

function TimeRefreshPopover({ value, refreshValue, open, onToggle, onApply, onClose }) {
  const { useEffect, useState } = getReact();
  const [tab, setTab] = useState(value.mode === 'custom' ? 'custom' : 'auto');
  const [range, setRange] = useState(value.range);
  const [refresh, setRefresh] = useState(refreshValue);
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

  const chooseRange = (next) => {
    const window = resolveRelativeWindow(next);
    setRange(next);
    setStart(toLocalInputValue(window[0]));
    setEnd(toLocalInputValue(window[1]));
  };
  const confirm = () => onApply(
    tab === 'custom' ? { mode: 'custom', range, start, end } : createRelativeTimeFilter(range),
    refresh,
  );
  const optionButton = (option, selected, onClick) => h('button', {
    className: cx('command-time-option', selected && 'selected'),
    type: 'button',
    onClick,
    key: option.value,
  }, option.label);

  return h('div', { className: 'command-time-filter', 'data-soc-menu-root': 'true' }, [
    h('button', { className: cx('command-time-trigger', open && 'open'), type: 'button', onClick: onToggle, key: 'trigger' }, [
      h('span', { title: timeFilterLabel(value), key: 'range' }, ['时间范围：', h('b', { key: 'value' }, timeFilterLabel(value))]),
      h('i', { key: 'divider' }),
      h('span', { key: 'refresh' }, ['刷新频率：', h('b', { key: 'value' }, refreshLabel(refreshValue))]),
      h('em', { key: 'arrow' }, open ? '⌃' : '⌄'),
    ]),
    open ? h('div', { className: 'command-time-panel', key: 'panel' }, [
      h('div', { className: 'command-time-tabs', key: 'tabs' }, [
        h('button', { className: tab === 'auto' ? 'active' : '', type: 'button', onClick: () => setTab('auto'), key: 'auto' }, '自动刷新'),
        h('button', { className: tab === 'custom' ? 'active' : '', type: 'button', onClick: () => setTab('custom'), key: 'custom' }, '精确时间'),
      ]),
      h('div', { className: 'command-time-panel-body', key: 'body' }, tab === 'auto' ? [
        h('div', { key: 'ranges' }, [
          h('label', { key: 'label' }, '时间范围'),
          h('div', { className: 'command-time-options', key: 'options' }, TIME_RANGE_OPTIONS.map((option) => optionButton(option, range === option.value, () => chooseRange(option.value)))),
        ]),
        h('div', { key: 'refreshes' }, [
          h('label', { key: 'label' }, '刷新频率'),
          h('div', { className: 'command-time-options', key: 'options' }, REFRESH_OPTIONS.map((option) => optionButton(option, refresh === option.value, () => setRefresh(option.value)))),
        ]),
      ] : [
        h('div', { className: 'command-time-inputs', key: 'inputs' }, [
          h('label', { key: 'start' }, [h('span', { key: 'label' }, '开始时间'), h('input', { type: 'datetime-local', value: start, onChange: (event) => setStart(event.target.value), key: 'input' })]),
          h('label', { key: 'end' }, [h('span', { key: 'label' }, '结束时间'), h('input', { type: 'datetime-local', value: end, onChange: (event) => setEnd(event.target.value), key: 'input' })]),
        ]),
        h('div', { className: 'command-time-shortcuts', key: 'shortcuts' }, [
          { value: '1h', label: '1小时' },
          { value: '24h', label: '24小时' },
          { value: 'today', label: '今天' },
          { value: '7d', label: '最近7天' },
          { value: '30d', label: '最近30天' },
        ].map((option) => h('button', { type: 'button', onClick: () => chooseRange(option.value), key: option.value }, option.label))),
      ]),
      h('div', { className: 'command-time-panel-actions', key: 'actions' }, [
        h('button', { type: 'button', onClick: onClose, key: 'cancel' }, '取消'),
        h('button', { className: 'primary', type: 'button', onClick: confirm, key: 'confirm' }, '确定'),
      ]),
    ]) : null,
  ]);
}

function CommandHeader({ timeFilter, refreshKey, timeMenuOpen, setTimeMenuOpen, applyTimeRefresh, stats, loading, refresh, activity }) {
  const active = activity.denoise.current || activity.triage.current;
  const loadActive = activity.mode !== 'normal' && activity.batch?.receivedCount > 0;
  const status = activity.connection === 'error'
    ? '活动通道重连中'
    : activity.mode === 'surge' ? '降噪洪峰处理中'
      : activity.mode === 'burst' ? '告警批量处理中'
        : active ? 'AI任务处理中' : 'AI运营处理';
  return h('header', { className: 'command-header' }, [
    h('div', { className: 'command-brand', key: 'brand' }, [
      h('div', { className: 'command-logo', key: 'logo' }, 'AI'),
      h('div', { key: 'copy' }, [
        h('strong', { key: 'title' }, 'Flocks AI 智能告警态势中心'),
        h('span', { key: 'sub' }, '告警汇聚 · 智能降噪 · 自动研判 · 风险聚合'),
      ]),
    ]),
    h('div', { className: cx('command-live', loadActive && `load-${activity.mode}`), key: 'live' }, [
      h('i', { className: activity.connection === 'error' ? 'warn' : '', key: 'dot' }),
      h('span', { key: 'text' }, status),
      h(AnimatedNumber, {
        tag: 'b',
        value: loadActive ? activity.batch.ratePerSecond : stats.denoise.totalRaw,
        format: (value) => loadActive ? `${trim(value)} 条/秒` : `${compactNumber(Math.round(value))} 条告警`,
        key: loadActive ? 'rate' : 'count',
      }),
    ]),
    h('div', { className: 'command-tools', key: 'tools' }, [
      h(TimeRefreshPopover, {
        value: timeFilter,
        refreshValue: refreshKey,
        open: timeMenuOpen,
        onToggle: () => setTimeMenuOpen((current) => !current),
        onApply: applyTimeRefresh,
        onClose: () => setTimeMenuOpen(false),
        key: 'time-filter',
      }),
      h('button', { className: 'command-refresh', type: 'button', disabled: loading, onClick: refresh, key: 'refresh' }, loading ? '刷新中' : '刷新'),
      h('div', { className: 'command-clock', key: 'clock' }, [
        h('b', { key: 'time' }, stats.generatedAt ? stats.generatedAt.slice(11, 19) : '--:--:--'),
        h('span', { title: timeFilterLabel(timeFilter), key: 'range' }, timeFilterLabel(timeFilter)),
      ]),
    ]),
  ]);
}

function CommandConnections() {
  const sourceTopPath = 'M98 110 C188 110 235 224 330 224';
  const sourceBottomPath = 'M98 348 C188 348 235 224 330 224';
  const denoisePath = 'M330 224 C375 224 410 224 460 224';
  const triageAutoPath = 'M755 224 C800 224 815 128 860 128';
  const triageManualPath = 'M755 224 C800 224 815 311 860 311';
  const severityTargets = [109, 185, 261, 336];
  const connectionPath = (className, path, key) => [
    h('path', { className: `command-link ${className}`, d: path, key: `${key}-band` }),
    h('path', { className: `command-link-dots ${className}`, d: path, key: `${key}-dots` }),
  ];
  const movingParticle = (className, path, duration, begin, key) => h('circle', {
    className: `command-flow-particle ${className}`,
    r: className.includes('result') ? 4.5 : 3.5,
    key,
  }, [
    h('animateMotion', { path, dur: duration, begin, repeatCount: 'indefinite', key: 'motion' }),
    h('animate', {
      attributeName: 'fill-opacity',
      values: '0;1;1;0',
      keyTimes: '0;.12;.82;1',
      dur: duration,
      begin,
      repeatCount: 'indefinite',
      key: 'opacity',
    }),
  ]);
  const severityPaths = severityTargets.flatMap((targetY, index) => [
    ...connectionPath('risk-link', `M930 128 C975 128 1008 ${targetY} 1058 ${targetY}`, `risk-a-${index}`),
    ...connectionPath('risk-link dim', `M930 311 C975 311 1008 ${targetY} 1058 ${targetY}`, `risk-b-${index}`),
  ]);
  return h('svg', { className: 'command-links', viewBox: '0 0 1200 510', preserveAspectRatio: 'none', 'aria-hidden': 'true' }, [
    ...connectionPath('source-link', sourceTopPath, 'source-a'),
    ...connectionPath('source-link', sourceBottomPath, 'source-b'),
    ...connectionPath('denoise-link', denoisePath, 'denoise'),
    ...connectionPath('triage-link', triageAutoPath, 'triage-a'),
    ...connectionPath('triage-link dim', triageManualPath, 'triage-b'),
    ...severityPaths,
    movingParticle('flow-denoise', sourceTopPath, '2.8s', '0s', 'particle-source-a'),
    movingParticle('flow-denoise', sourceBottomPath, '3.1s', '-1.2s', 'particle-source-b'),
    movingParticle('flow-denoise result-particle', denoisePath, '1.45s', '-.5s', 'particle-denoise'),
    movingParticle('flow-triage', triageAutoPath, '1.9s', '0s', 'particle-triage'),
    movingParticle('flow-triage result-particle', 'M930 128 C975 128 1008 185 1058 185', '1.7s', '-.8s', 'particle-result'),
  ]);
}

function CommandActivityLane({ kind, lane }) {
  const event = lane.current || lane.last;
  const active = Boolean(lane.current);
  const steps = kind === 'denoise' ? ['接入', '特征', '聚类', '降噪'] : ['证据', '情报', '推理', '结论'];
  const duration = activityDuration(event);
  const drumDuration = kind === 'denoise' ? (active ? '2.6s' : '6.6s') : (active ? '7.2s' : '9.2s');
  const drumSteps = [...steps, ...steps];
  const playbackMode = kind === 'denoise' ? event?.playbackMode : 'normal';
  const status = active
    ? playbackMode === 'surge' ? '洪峰处理' : playbackMode === 'burst' ? '批量处理' : '处理中'
    : event ? '最近完成' : '待机巡航';
  const sampleCount = Math.max(Number(event?.sampleCount || 1), 1);
  const eventTitle = event?.alert?.threatName
    ? `${event.alert.threatName}${sampleCount > 1 ? ` × ${sampleCount}` : ''}`
    : '等待新告警进入';
  const resultText = event ? activityResultText(event) : '自动巡检 · 等待任务';
  return h('div', {
    className: cx('command-activity-lane', `lane-${kind}`, active && 'active'),
    style: {
      '--drum-duration': drumDuration,
      '--drum-accent': kind === 'denoise' ? '#2be7ff' : '#9b8cff',
    },
  }, [
    h('div', { className: 'command-lane-copy', key: 'copy' }, [
      h('div', { className: 'command-lane-head', key: 'head' }, [
        h('span', { key: 'label' }, kind === 'denoise' ? '智能降噪' : '智能研判'),
        h('b', { key: 'status' }, status),
      ]),
      h('strong', { title: eventTitle, key: 'title' }, eventTitle),
      resultText ? h('div', {
        className: cx('command-lane-result', !event && 'idle'),
        style: active ? { animationDelay: `${Math.round(duration * 0.65)}ms` } : undefined,
        key: 'result',
      }, resultText) : null,
    ]),
    h('div', { className: 'command-drum-shell', 'aria-label': steps.join('、'), key: 'drum' }, [
      h('div', { className: 'command-drum-caption', key: 'caption' }, [
        h('b', { key: 'mode' }, active ? '处理' : '巡航'),
      ]),
      h('div', { className: 'command-drum-window', key: 'window' }, [
        h('div', { className: 'command-drum-track', 'aria-hidden': 'true', key: 'track' }, drumSteps.map((step, index) => h('span', {
          className: 'command-drum-step',
          key: `${step}-${index}`,
        }, [h('i', { key: 'dot' }), h('b', { key: 'label' }, step), h('small', { key: 'index' }, String((index % steps.length) + 1).padStart(2, '0'))]))),
        h('div', { className: 'command-drum-focus', key: 'focus' }),
        h('i', { className: 'command-drum-scan', key: 'scan' }),
      ]),
    ]),
  ]);
}

function severityRows(stats) {
  return [
    { key: 'critical', label: '严重', value: stats.triage.attackSuccess || 0, tone: 'critical' },
    { key: 'high', label: '高危', value: stats.triage.attack || 0, tone: 'high' },
    { key: 'medium', label: '中危', value: stats.triage.attackFailed || 0, tone: 'medium' },
    { key: 'low', label: '低危', value: stats.triage.benign || 0, tone: 'low' },
  ];
}

function CommandGraph({ stats, activity }) {
  const denoiseActive = Boolean(activity.denoise.current);
  const triageActive = Boolean(activity.triage.current);
  const severityToneFor = (event) => {
    if (!event) return '';
    if (event.result?.verdict === 'attack_success') return 'critical';
    const risk = String(event.result?.riskLevel || '').toLowerCase();
    if (risk === 'high') return 'high';
    if (risk === 'medium') return 'medium';
    if (risk === 'low' || event.result?.verdict === 'benign') return 'low';
    return '';
  };
  const activeSeverityTone = severityToneFor(activity.triage.current);
  const recentSeverityTone = severityToneFor(activity.triage.last);
  const activeSources = [...(stats.sources || [])].sort((a, b) => Number(b.value || 0) - Number(a.value || 0)).slice(0, 2);
  while (activeSources.length < 2) activeSources.push({ key: `source-${activeSources.length}`, label: activeSources.length ? '备用数据源' : '告警数据源', value: 0 });
  const severities = severityRows(stats);
  return h('section', { className: cx('command-graph', denoiseActive && 'denoise-running', triageActive && 'triage-running', `load-${activity.mode}`) }, [
    h(CommandConnections, { key: 'links' }),
    h('div', { className: 'source-stack', key: 'sources' }, activeSources.map((source) => h('div', { className: 'command-source', key: source.key }, [
      h('span', { key: 'label' }, source.label),
      h(AnimatedNumber, { tag: 'b', value: source.value, key: 'value' }),
      h('i', { key: 'port' }),
    ]))),
    h('div', { className: 'merge-node', key: 'merge' }, [
      h(AnimatedNumber, { tag: 'b', value: stats.denoise.totalNormalized, duration: 1100, key: 'value' }),
      h('span', { key: 'label' }, '汇聚告警'),
      h('small', { key: 'sub' }, `过滤 ${compactNumber(stats.denoise.filterRemoved)} · 去重 ${compactNumber(stats.denoise.dedupRemoved)}`),
    ]),
    h('div', { className: 'command-core command-original-core', key: 'core' }, [
      h(AiCore, { stats, activity, key: 'sphere' }),
    ]),
    h('div', { className: 'outcome-stack', key: 'outcomes' }, [
      h('div', { title: 'AI 新完成且未使用缓存、复用或失败的研判数量', key: 'auto' }, [h(AnimatedNumber, { tag: 'b', value: stats.triage.newTriaged, key: 'value' }), h('span', { key: 'label' }, 'AI自主研判')]),
      h('div', { className: cx('primary', triageActive && 'processing'), title: '研判结论为攻击成功、攻击行为或攻击失败的事件数量', key: 'events' }, [h(AnimatedNumber, { tag: 'b', value: stats.triage.attackTotal, key: 'value' }), h('span', { key: 'label' }, triageActive ? '结果生成中' : '安全事件')]),
      h('div', { key: 'manual' }, [h(AnimatedNumber, { tag: 'b', value: stats.closedLoop.manualDecision, key: 'value' }), h('span', { key: 'label' }, '人工研判')]),
    ]),
    h('div', { className: 'severity-stack', key: 'severity' }, severities.map((item) => h('div', {
      className: cx(`severity-node severity-${item.tone}`, item.tone === activeSeverityTone && 'active-target', !activeSeverityTone && item.tone === recentSeverityTone && 'recent-target'),
      key: `${item.key}-${activity.triage.last?.eventId || 'idle'}`,
    }, [
      h('span', { key: 'label' }, item.label),
      h(AnimatedNumber, { tag: 'b', value: item.value, key: 'value' }),
    ]))),
    h('div', { className: 'command-lanes', key: 'lanes' }, [
      h(CommandActivityLane, { kind: 'denoise', lane: activity.denoise, key: 'denoise' }),
      h(CommandActivityLane, { kind: 'triage', lane: activity.triage, key: 'triage' }),
    ]),
  ]);
}

function CommandMetric({ label, value, format, sub, values, color }) {
  return h('div', { className: 'command-metric', style: { '--metric-color': color } }, [
    h('span', { key: 'label' }, label),
    h(AnimatedNumber, { tag: 'b', className: 'command-metric-value', value, format, duration: 1200, key: 'value' }),
    h('small', { key: 'sub' }, sub),
    h(Sparkline, { values, color, key: 'trend' }),
  ]);
}

function CommandMetrics({ stats }) {
  return h('section', { className: 'command-metrics' }, [
    h(CommandMetric, { label: '原始告警量', value: stats.denoise.totalRaw, sub: `${compactNumber(stats.denoise.totalUnique)} 条进入研判`, values: stats.timeline.denoiseRaw, color: '#2e72ff', key: 'raw' }),
    h(CommandMetric, { label: '安全事件量', value: stats.triage.attackTotal, sub: `${compactNumber(stats.triage.attackSuccess)} 条攻击成功`, values: stats.timeline.triageAttack, color: '#23ca8e', key: 'events' }),
    h(CommandMetric, { label: '降噪率', value: stats.denoise.duplicateRate * 100, format: (value) => `${trim(value)}%`, sub: `${compactNumber(stats.denoise.duplicates)} 条告警已过滤/收敛`, values: stats.timeline.denoiseUnique, color: '#21d8a3', key: 'rate' }),
    h(CommandMetric, { label: '平均研判时间', value: stats.triage.avgTriageMs, format: (value) => formatDurationMs(value), sub: `${compactNumber(stats.triage.totalRecords)} 条已完成研判`, values: stats.timeline.triageTotal, color: '#ff674d', key: 'mtta' }),
  ]);
}

function activityTimestamp(event) {
  const value = Date.parse(event?.occurredAt || '');
  return Number.isFinite(value) ? value : 0;
}

function activityTaskKey(event) {
  const alertId = String(event?.alert?.id || '').trim();
  return alertId || String(event?.eventId || '').trim();
}

function buildEventQueueTasks(activity, timeFilter) {
  const stateByEventId = new Map();
  for (const kind of ['denoise', 'triage']) {
    const lane = activity[kind];
    if (lane.current?.eventId) stateByEventId.set(lane.current.eventId, 'processing');
    for (const event of lane.queue) {
      if (event?.eventId) stateByEventId.set(event.eventId, 'waiting');
    }
  }

  const allEvents = [
    ...(activity.recent || []),
    activity.denoise.last,
    activity.triage.last,
    ...activity.denoise.queue,
    ...activity.triage.queue,
    activity.denoise.current,
    activity.triage.current,
  ].filter((event) => event?.eventId && !event.hiddenFromQueue && eventMatchesTimeFilter(event, timeFilter));
  const taskByKey = new Map();
  for (const event of allEvents) {
    const key = activityTaskKey(event);
    if (!key) continue;
    const task = taskByKey.get(key) || { key, denoise: null, triage: null, latestAt: 0 };
    task[event.stage] = event;
    task.latestAt = Math.max(task.latestAt, activityTimestamp(event));
    taskByKey.set(key, task);
  }

  const tasks = [...taskByKey.values()].map((task) => {
    const denoiseState = stateByEventId.get(task.denoise?.eventId) || '';
    const triageState = stateByEventId.get(task.triage?.eventId) || '';
    let state = 'completed';
    let stage = task.triage ? 'triage' : 'denoise';
    if (triageState === 'processing') {
      state = 'processing';
      stage = 'triage';
    } else if (denoiseState === 'processing') {
      state = 'processing';
      stage = 'denoise';
    } else if (triageState === 'waiting') {
      state = 'waiting';
      stage = 'triage';
    } else if (denoiseState === 'waiting') {
      state = 'waiting';
      stage = 'denoise';
    } else if (!task.triage && task.denoise && task.denoise.status !== 'failed' && !task.denoise.result?.isDuplicate) {
      state = 'waiting';
      stage = 'triage';
    }
    return {
      ...task,
      state,
      stage,
      event: stage === 'triage' && task.triage ? task.triage : task.denoise,
    };
  });

  const stateRank = { processing: 0, waiting: 1, completed: 2 };
  tasks.sort((left, right) => {
    const stateDelta = stateRank[left.state] - stateRank[right.state];
    if (stateDelta) return stateDelta;
    if (left.state === 'waiting') return right.latestAt - left.latestAt;
    if (left.state === 'processing' && left.stage !== right.stage) return left.stage === 'triage' ? -1 : 1;
    return right.latestAt - left.latestAt;
  });

  return tasks;
}

function useAnimatedTaskWindow(tasks, transitionKey) {
  const { useEffect, useRef, useState } = getReact();
  const sourceTasks = tasks.slice(0, EVENT_RAIL_TASK_LIMIT);
  const sourceRef = useRef(sourceTasks);
  const initialTasks = useRef(sourceTasks.map((task) => ({ ...task, motion: 'stable' })));
  const [displayedTasks, setDisplayedTasks] = useState(initialTasks.current);
  const displayedRef = useRef(displayedTasks);
  const exitTimerRef = useRef(0);
  const transitionKeyRef = useRef(transitionKey);
  const filterTransitionRef = useRef(false);

  if (transitionKeyRef.current !== transitionKey) {
    transitionKeyRef.current = transitionKey;
    filterTransitionRef.current = true;
  }

  sourceRef.current = sourceTasks;
  displayedRef.current = displayedTasks;

  const signature = sourceTasks.map((task) => [
    task.key,
    task.state,
    task.stage,
    task.event?.eventId || '',
    task.event?.playbackStartedAt || '',
    task.latestAt || '',
  ].join(':')).join('|');

  useEffect(() => {
    const current = displayedRef.current;
    const latest = sourceRef.current;
    const latestByKey = new Map(latest.map((task) => [task.key, task]));
    const isExiting = (task) => task.motion === 'exit' || task.motion === 'filter-exit';
    const removed = current.some((task) => !isExiting(task) && !latestByKey.has(task.key));

    if (removed) {
      const filterTransition = filterTransitionRef.current;
      const exitMotion = filterTransition ? 'filter-exit' : 'exit';
      const exiting = current.map((task) => {
        if (isExiting(task)) return task;
        const updated = latestByKey.get(task.key);
        return updated ? { ...updated, motion: 'stable' } : { ...task, motion: exitMotion };
      });
      displayedRef.current = exiting;
      setDisplayedTasks(exiting);
      window.clearTimeout(exitTimerRef.current);
      exitTimerRef.current = window.setTimeout(() => {
        const retainedKeys = new Set(displayedRef.current
          .filter((task) => !isExiting(task))
          .map((task) => task.key));
        const filled = sourceRef.current.map((task) => ({
          ...task,
          motion: retainedKeys.has(task.key) ? 'stable' : 'enter',
        }));
        displayedRef.current = filled;
        setDisplayedTasks(filled);
        filterTransitionRef.current = false;
      }, filterTransition ? 240 : 380);
      return;
    }

    if (current.some(isExiting)) {
      const refreshed = current.map((task) => {
        if (isExiting(task)) return task;
        const updated = latestByKey.get(task.key);
        return updated ? { ...updated, motion: 'stable' } : task;
      });
      displayedRef.current = refreshed;
      setDisplayedTasks(refreshed);
      return;
    }

    if (!current.length) filterTransitionRef.current = false;

    const currentKeys = new Set(current.map((task) => task.key));
    const reconciled = latest.map((task) => ({
      ...task,
      motion: currentKeys.has(task.key) ? 'stable' : 'enter',
    }));
    displayedRef.current = reconciled;
    setDisplayedTasks(reconciled);
  }, [signature, transitionKey]);

  useEffect(() => () => window.clearTimeout(exitTimerRef.current), []);
  return displayedTasks;
}

function EventQueueProgress({ event }) {
  const { useEffect, useRef, useState } = getReact();
  const duration = Math.max(activityDuration(event), 1);
  const start = useRef({ eventId: '', value: 0 });
  if (start.current.eventId !== event.eventId) {
    start.current = {
      eventId: event.eventId,
      value: Number(event.playbackStartedAt || Date.now()),
    };
  }
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const update = () => setNow(Date.now());
    update();
    const id = window.setInterval(update, 500);
    return () => window.clearInterval(id);
  }, [event.eventId]);
  const elapsed = Math.min(Math.max(now - start.current.value, 0), duration);
  const progress = elapsed / duration;
  const elapsedSeconds = Math.min(Math.floor(elapsed / 1000), Math.round(duration / 1000));
  return h('div', { className: 'event-rail-progress', 'aria-label': 'AI 任务处理进度' }, [
    h('span', { className: 'event-rail-progress-track', style: { '--queue-progress': progress }, key: 'track' }, [h('i', { key: 'fill' })]),
    h('small', { key: 'duration' }, `${elapsedSeconds}s / ${Math.round(duration / 1000)}s`),
  ]);
}

function CommandEventRail({ activity, timeFilter, collapsed, onToggle }) {
  const tasks = buildEventQueueTasks(activity, timeFilter);
  const filterTransitionKey = [timeFilter.mode, timeFilter.range, timeFilter.start, timeFilter.end].join('|');
  const visibleTasks = useAnimatedTaskWindow(
    tasks.filter((task) => task.state !== 'completed'),
    filterTransitionKey,
  );
  const counts = {
    processing: visibleTasks.filter((task) => task.state === 'processing').length,
    waiting: visibleTasks.filter((task) => task.state === 'waiting').length,
  };
  const queueCount = visibleTasks.length;
  const banner = activity.connection === 'error'
    ? '处理任务连接异常，正在重试'
    : counts.processing
      ? `AI 正在并行处理 ${counts.processing} 个任务`
      : counts.waiting ? '最新 10 条待处理任务' : '等待新的降噪或研判任务';
  const content = collapsed ? [] : [
    h('div', { className: 'event-rail-head', key: 'head' }, [
      h('div', { key: 'title' }, [h('strong', { key: 'label' }, 'AI处理任务'), h('span', { key: 'sub' }, '最新 10 条待处理任务')]),
      h(AnimatedNumber, { tag: 'b', value: queueCount, duration: 600, key: 'count' }),
    ]),
    h('div', { className: cx('event-update-banner', activity.connection === 'error' && 'warn'), key: 'banner' }, banner),
    h('div', { className: 'event-rail-list', key: 'list' }, visibleTasks.length ? visibleTasks.map((task) => {
      const event = task.event;
      const sampleCount = Math.max(Number(task.denoise?.sampleCount || 1), 1);
      const title = `${event?.alert?.threatName || '未知告警'}${sampleCount > 1 ? ` × ${sampleCount}` : ''}`;
      const stageLabel = task.stage === 'triage'
        ? task.state === 'waiting' ? '待研判' : '智能研判'
        : task.state === 'waiting' ? '待降噪' : '智能降噪';
      const stateLabel = task.state === 'processing'
        ? '处理中'
        : '等待处理';
      const detail = event?.triggerSource === 'workflow_execution'
        ? event.result?.isDuplicate ? '重复告警已收敛' : '降噪处理完成'
        : task.state === 'processing'
          ? task.stage === 'triage' ? '证据关联与结论生成中' : '特征提取与相似聚类中'
          : '等待 AI 处理';
      return h('article', {
        className: cx('event-rail-item', `state-${task.state}`, `kind-${task.stage}`, `motion-${task.motion || 'stable'}`),
        key: task.key,
      }, [
        h('div', { className: 'event-rail-meta', key: 'meta' }, [
          h('span', { className: cx('event-queue-kind', `kind-${task.stage}`), key: 'kind' }, stageLabel),
          h('span', { className: 'event-stage', key: 'stage' }, stateLabel),
          h('time', { key: 'time' }, eventTimeLabel(event?.occurredAt)),
        ]),
        h('strong', { title, key: 'title' }, title),
        h('span', { title: eventEndpoint(event), key: 'endpoint' }, eventEndpoint(event)),
        h('small', { key: 'result' }, detail),
        task.state === 'processing' ? h(EventQueueProgress, { event, key: 'progress' }) : null,
      ]);
    }) : h('div', { className: 'event-rail-empty' }, '等待新的降噪或研判任务')),
  ];
  return h('aside', { className: cx('command-event-rail', collapsed && 'collapsed') }, [
    h('button', {
      className: 'event-rail-toggle',
      type: 'button',
      title: collapsed ? '展开 AI处理任务' : '向右折叠',
      'aria-label': collapsed ? '展开 AI处理任务' : '向右折叠 AI处理任务',
      onClick: onToggle,
      key: 'toggle',
    }, collapsed ? h('span', { key: 'label' }, ['任', '务', '面', '板'].map((text) => h('i', { key: text }, text))) : '›'),
    ...content,
  ]);
}

export default function Page() {
  const { useCallback, useEffect, useRef, useState } = getReact();
  const [timeFilter, setTimeFilter] = useState(() => createRelativeTimeFilter());
  const [refreshKey, setRefreshKey] = useState('off');
  const [timeMenuOpen, setTimeMenuOpen] = useState(false);
  const [eventRailCollapsed, setEventRailCollapsed] = useState(false);
  const [stats, setStats] = useState(EMPTY_STATS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activity, setActivity] = useState(createActivityState);
  const activityCursor = useRef('');
  const workflowProgressByFilter = useRef(new Map());
  const statsRequestId = useRef(0);
  const statsPending = useRef(0);

  const loadStats = useCallback(async (filter, options = {}) => {
    if (options.skipIfBusy && statsPending.current > 0) return;
    const requestId = ++statsRequestId.current;
    statsPending.current += 1;
    if (!options.silent) setLoading(true);
    try {
      const params = timeFilterParams(filter);
      if (options.force) params.force = '1';
      const response = await getApi().page.get('/stats', { params });
      if (requestId !== statsRequestId.current) return;
      setStats(mergeStats(response.data));
      setError('');
    } catch (err) {
      if (requestId !== statsRequestId.current) return;
      setError(err instanceof Error ? err.message : 'stats api failed');
    } finally {
      statsPending.current = Math.max(statsPending.current - 1, 0);
      if (requestId === statsRequestId.current) setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => loadStats(timeFilter, { force: true }), [loadStats, timeFilter]);

  useEffect(() => {
    void loadStats(timeFilter);
  }, [loadStats, timeFilter]);

  useEffect(() => {
    const intervalMs = REFRESH_INTERVAL_MS[refreshKey];
    if (!intervalMs) return undefined;
    const id = window.setInterval(() => void loadStats(timeFilter, { skipIfBusy: true }), intervalMs);
    return () => window.clearInterval(id);
  }, [loadStats, refreshKey, timeFilter]);

  useEffect(() => {
    if (!timeMenuOpen) return undefined;
    const closeOnOutsidePress = (event) => {
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

  const applyTimeRefresh = useCallback((nextTimeFilter, nextRefreshKey) => {
    setTimeFilter(nextTimeFilter);
    setRefreshKey(nextRefreshKey);
    setTimeMenuOpen(false);
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let statsTimer = 0;
    let lastStatsRefreshAt = 0;
    let retryDelay = ACTIVITY_POLL_MS;
    const workflowFilterKey = [timeFilter.mode, timeFilter.range, timeFilter.start, timeFilter.end].join('|');
    activityCursor.current = '';
    setActivity(createActivityState());

    const schedule = (delay) => {
      if (!stopped) timer = window.setTimeout(() => void poll(false), delay);
    };

    const scheduleStatsRefresh = (immediate = false) => {
      window.clearTimeout(statsTimer);
      const delay = immediate ? 0 : Math.max(5000 - (Date.now() - lastStatsRefreshAt), 0);
      statsTimer = window.setTimeout(() => {
        if (stopped) return;
        lastStatsRefreshAt = Date.now();
        void loadStats(timeFilter, { force: true, silent: true });
      }, delay);
    };

    const poll = async (bootstrap) => {
      if (stopped) return;
      if (document.hidden) {
        schedule(ACTIVITY_POLL_MS);
        return;
      }
      try {
        const params = bootstrap || !activityCursor.current
          ? { bootstrap: 'latest', ...timeFilterParams(timeFilter) }
          : { cursor: activityCursor.current, limit: 40, ...timeFilterParams(timeFilter) };
        const response = await getApi().page.get('/activity', { params });
        const payload = response.data || {};
        if (payload.error) throw new Error(payload.error);
        activityCursor.current = payload.cursor || activityCursor.current;
        if (!stopped) {
          const rawIncomingEvents = bootstrap
            ? recentUnseenActivity(payload.recentEvents)
            : (payload.events || []);
          const incomingEvents = rawIncomingEvents.filter((event) => event?.stage !== 'denoise');
          const workflowEvents = Array.isArray(payload.workflowEvents) ? payload.workflowEvents : [];
          const rawCallCount = payload.workflowStats?.callCount;
          const hasWorkflowCount = rawCallCount !== null
            && rawCallCount !== undefined
            && Number.isFinite(Number(rawCallCount));
          let workflowDelta = 0;
          let workflowChanged = false;
          if (hasWorkflowCount) {
            const callCount = Math.max(Math.trunc(Number(rawCallCount)), 0);
            const latestStartedAt = Math.max(Math.trunc(Number(payload.workflowStats?.latestStartedAt || 0)), 0);
            const previousProgress = workflowProgressByFilter.current.get(workflowFilterKey);
            workflowChanged = Boolean(previousProgress) && (
              callCount > previousProgress.callCount
              || latestStartedAt > previousProgress.latestStartedAt
            );
            if (workflowChanged) {
              workflowDelta = Math.max(callCount - previousProgress.callCount, 1);
              incomingEvents.push(workflowDenoiseActivity(
                callCount,
                workflowDelta,
                payload.generatedAt,
                workflowEvents[0],
              ));
            }
            workflowProgressByFilter.current.set(workflowFilterKey, { callCount, latestStartedAt });
          }
          const incomingRecentEvents = bootstrap
            ? [...(payload.recentEvents || []), ...workflowEvents]
            : workflowChanged
              ? [...rawIncomingEvents, ...workflowEvents]
              : rawIncomingEvents;
          setActivity((previous) => enqueueActivity(
            previous,
            incomingEvents,
            payload.generatedAt,
            incomingRecentEvents,
            payload.batch,
          ));
          const batch = normalizeActivityBatch(payload.batch);
          const hasStatsChange = workflowChanged
            || batch.receivedCount > 0
            || batch.triageUpdatedCount > 0;
          if (payload.cursorReset || hasStatsChange) scheduleStatsRefresh(Boolean(payload.cursorReset));
        }
        retryDelay = ACTIVITY_POLL_MS;
      } catch (activityError) {
        if (!stopped) setActivity((previous) => (
          previous.connection === 'error' ? previous : { ...previous, connection: 'error' }
        ));
        retryDelay = Math.min(retryDelay * 2, 30000);
      }
      schedule(retryDelay);
    };

    void poll(true);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(statsTimer);
    };
  }, [loadStats, timeFilter]);

  useEffect(() => {
    setActivity((previous) => {
      let changed = false;
      const next = { ...previous };
      for (const kind of ['denoise', 'triage']) {
        const lane = previous[kind];
        if (!lane.current && lane.queue.length) {
          changed = true;
          const nextEvent = lane.queue[0];
          next[kind] = {
            ...lane,
            current: nextEvent.playbackStartedAt
              ? nextEvent
              : { ...nextEvent, playbackStartedAt: Date.now() },
            queue: lane.queue.slice(1),
          };
        }
      }
      return changed ? next : previous;
    });
  }, [activity.denoise.current, activity.denoise.queue.length, activity.triage.current, activity.triage.queue.length]);

  useEffect(() => {
    const event = activity.denoise.current;
    if (!event) return undefined;
    const id = window.setTimeout(() => {
      setActivity((previous) => completeActivity(previous, 'denoise', event));
    }, activityDuration(event));
    return () => window.clearTimeout(id);
  }, [activity.denoise.current?.eventId]);

  useEffect(() => {
    const event = activity.triage.current;
    if (!event) return undefined;
    const id = window.setTimeout(() => {
      setActivity((previous) => completeActivity(previous, 'triage', event));
    }, activityDuration(event));
    return () => window.clearTimeout(id);
  }, [activity.triage.current?.eventId]);

  useEffect(() => {
    if (!activity.batchUpdatedAt) return undefined;
    const id = window.setTimeout(() => {
      setActivity((previous) => ({
        ...previous,
        batch: emptyActivityBatch(),
        batchUpdatedAt: 0,
      }));
    }, 12000);
    return () => window.clearTimeout(id);
  }, [activity.batchUpdatedAt]);

  const activityBusy = Boolean(
    activity.denoise.current
    || activity.triage.current
    || activity.denoise.queue.length
    || activity.triage.queue.length
    || activity.batch?.receivedCount
    || activity.batch?.triageUpdatedCount
  );

  return h('div', {
    className: cx('adtd-root command-root', activityBusy && 'command-is-processing', eventRailCollapsed && 'event-rail-is-collapsed'),
    'data-animations': 'on',
  }, [
    h('style', { key: 'style' }, CSS),
    h(CommandHeader, { key: 'header', timeFilter, refreshKey, timeMenuOpen, setTimeMenuOpen, applyTimeRefresh, stats, loading, refresh, activity }),
    error ? h('div', { className: 'error-banner', key: 'error' }, `统计接口异常：${error}`) : null,
    h('main', { className: cx('command-shell', eventRailCollapsed && 'event-rail-collapsed'), key: 'main' }, [
      h('div', { className: 'command-main', key: 'workspace' }, [
        h(CommandGraph, { key: 'graph', stats, activity }),
        h(CommandMetrics, { key: 'metrics', stats }),
      ]),
      h(CommandEventRail, { key: 'events', activity, timeFilter, collapsed: eventRailCollapsed, onToggle: () => setEventRailCollapsed((current) => !current) }),
    ]),
  ]);
}

const CSS = `
.adtd-root {
  box-sizing: border-box;
  min-height: 100vh;
  min-width: 1100px;
  margin: 0;
  padding: 0 16px;
  color: #d9f7ff;
  background:
    linear-gradient(90deg, rgba(43,231,255,.08) 1px, transparent 1px),
    linear-gradient(0deg, rgba(43,231,255,.06) 1px, transparent 1px),
    linear-gradient(145deg, #05111f 0%, #07182a 38%, #150f26 100%);
  background-size: 34px 34px, 34px 34px, auto;
  font-family: Inter, "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  overflow-x: auto;
}
.adtd-root * { box-sizing: border-box; }
.adtd-header {
  position: relative;
  display: grid;
  grid-template-columns: minmax(260px, 360px) 1fr minmax(500px, 620px);
  align-items: center;
  gap: 12px;
  min-height: 62px;
  padding: 10px 14px;
  border: 1px solid rgba(43,231,255,.38);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(7, 25, 42, .94), rgba(4, 12, 24, .86));
  box-shadow: 0 0 32px rgba(43,231,255,.12), inset 0 0 24px rgba(88,166,255,.08);
}
.adtd-header:before, .adtd-header:after {
  content: "";
  position: absolute;
  top: -1px;
  width: 180px;
  height: 2px;
  background: linear-gradient(90deg, transparent, #2be7ff, transparent);
}
.adtd-header:before { left: 18px; }
.adtd-header:after { right: 18px; }
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand-mark {
  position: relative;
  width: 48px;
  height: 38px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 1px solid rgba(43,231,255,.55);
  border-radius: 8px;
  color: #ffd166;
  font-weight: 900;
  font-size: 18px;
  background: linear-gradient(145deg, rgba(43,231,255,.18), rgba(255,177,32,.12));
  box-shadow: inset 0 0 18px rgba(43,231,255,.16);
}
.brand-mark:after {
  content: "";
  position: absolute;
  inset: -35% auto -35% -70%;
  width: 30px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.5), transparent);
  transform: rotate(18deg);
  animation: markSweep 4.8s ease-in-out infinite;
  pointer-events: none;
}
.brand-title { font-size: 18px; font-weight: 800; color: #f7fdff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.brand-sub { margin-top: 3px; font-size: 12px; color: rgba(170,222,255,.66); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.header-center { display: flex; justify-content: center; flex-wrap: wrap; gap: 8px; min-width: 0; }
.chip {
  min-height: 28px;
  padding: 5px 12px;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
  border: 1px solid rgba(88,166,255,.28);
  border-radius: 999px;
  background: rgba(8,31,54,.62);
  color: #aadeff;
  font-size: 12px;
}
.chip.strong { color: #2ee6a6; border-color: rgba(46,230,166,.42); }
.chip.warn { color: #ffb020; border-color: rgba(255,176,32,.45); }
.header-tools { display: flex; justify-content: flex-end; align-items: center; gap: 8px; min-width: 0; }
.date-range { display: flex; align-items: center; gap: 6px; min-width: 0; }
.range-sep { color: rgba(170,222,255,.62); font-size: 12px; white-space: nowrap; }
.date-input, .icon-button {
  height: 34px;
  border: 1px solid rgba(43,231,255,.34);
  border-radius: 6px;
  color: #d9f7ff;
  background: rgba(6,18,34,.82);
  font: inherit;
  font-size: 12px;
}
.date-input { width: 136px; padding: 0 8px; color-scheme: dark; }
.icon-button { min-width: 70px; padding: 0 12px; cursor: pointer; }
.icon-button:hover { border-color: rgba(46,230,166,.7); color: #2ee6a6; }
.icon-button:disabled { opacity: .62; cursor: default; }
.clock { display: grid; gap: 1px; min-width: 146px; text-align: right; }
.clock b { font-size: 15px; color: #ffffff; }
.clock span { font-size: 11px; color: rgba(170,222,255,.68); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.screen-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(330px, 380px);
  grid-template-areas:
    "center right"
    "left right";
  gap: 12px;
  margin-top: 12px;
  align-items: start;
}
.column { display: grid; gap: 12px; align-content: stretch; min-width: 0; height: 100%; }
.left-col {
  grid-area: left;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: none;
  height: auto;
}
.left-col .panel {
  min-height: 0;
}
.left-col .panel:last-child {
  display: flex;
  flex-direction: column;
}
.left-col .panel:last-child .panel-body {
  flex: 1;
  display: grid;
}
.left-col .panel:last-child .side-summary { align-content: stretch; }
.center-col {
  grid-area: center;
  grid-template-rows: auto auto minmax(0, 1fr);
}
.right-col {
  grid-area: right;
  grid-template-rows: auto minmax(0, 1fr);
  align-self: start;
}
.right-col .panel {
  min-height: 0;
}
.right-col .panel:last-child {
  display: flex;
  flex-direction: column;
}
.right-col .panel:last-child .panel-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.panel {
  position: relative;
  min-width: 0;
  border: 1px solid rgba(43,231,255,.28);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(6,22,39,.86), rgba(5,13,28,.78));
  box-shadow: inset 0 0 20px rgba(43,231,255,.06), 0 12px 26px rgba(0,0,0,.2);
  overflow: hidden;
}
.panel:before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(135deg, rgba(43,231,255,.18), transparent 22%, transparent 76%, rgba(155,140,255,.18));
  opacity: .7;
}
.panel-head {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  padding: 10px 12px 6px;
  border-bottom: 1px solid rgba(88,166,255,.14);
}
.panel-title { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 14px; font-weight: 800; color: #eefcff; }
.panel-title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.panel-title i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ffd166;
  box-shadow: 0 0 10px rgba(255,209,102,.88);
  flex: 0 0 auto;
  animation: statusPulse 2.2s ease-in-out infinite;
}
.panel-meta { color: rgba(170,222,255,.62); font-size: 12px; white-space: nowrap; }
.panel-body { position: relative; z-index: 1; padding: 12px; }
.source-list { display: grid; gap: 12px; }
.source-row { display: flex; align-items: center; gap: 10px; min-width: 0; }
.source-node {
  width: 18px;
  height: 18px;
  border-radius: 5px;
  border: 1px solid rgba(88,166,255,.44);
  background: rgba(88,166,255,.12);
  flex: 0 0 auto;
}
.source-node.active {
  border-color: rgba(46,230,166,.75);
  background: rgba(46,230,166,.22);
  box-shadow: 0 0 14px rgba(46,230,166,.25);
  animation: nodePulse 2.4s ease-in-out infinite;
}
.source-main { min-width: 0; flex: 1; }
.source-line { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; color: rgba(217,247,255,.84); }
.source-line span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-line b { color: #ffffff; flex: 0 0 auto; }
.source-track { height: 4px; margin-top: 6px; border-radius: 999px; background: rgba(88,166,255,.14); overflow: hidden; }
.source-track span {
  position: relative;
  display: block;
  height: 100%;
  border-radius: inherit;
  overflow: hidden;
  background: linear-gradient(90deg, #2be7ff, #2ee6a6);
}
.source-track span:after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.72), transparent);
  transform: translateX(-120%);
  animation: barFlow 2.8s linear infinite;
}
.source-total {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(88,166,255,.14);
  display: flex;
  justify-content: space-between;
  color: rgba(170,222,255,.72);
}
.source-total b { color: #2be7ff; font-size: 18px; }
.status-grid { display: grid; gap: 8px; }
.status-item {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  padding: 8px;
  border-radius: 6px;
  background: rgba(88,166,255,.08);
}
.status-light {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #7f8ca3;
  box-shadow: 0 0 0 4px rgba(127,140,163,.1);
  flex: 0 0 auto;
}
.status-light.ok { background: #2ee6a6; box-shadow: 0 0 0 4px rgba(46,230,166,.13), 0 0 16px rgba(46,230,166,.55); }
.status-item b { display: block; font-size: 12px; color: #f7fdff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-item small { display: block; margin-top: 2px; color: rgba(170,222,255,.58); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.missing-note, .ok-note {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
}
.missing-note { color: #ffcf7a; background: rgba(255,176,32,.1); border: 1px solid rgba(255,176,32,.24); }
.ok-note { color: #90f6cf; background: rgba(46,230,166,.08); border: 1px solid rgba(46,230,166,.2); }
.sparkline { width: 100%; height: 96px; display: block; }
.left-col .sparkline {
  height: 142px;
  min-height: 0;
  padding: 6px 0;
  border-radius: 8px;
  background:
    radial-gradient(circle at 64% 22%, rgba(43,231,255,.13), transparent 36%),
    linear-gradient(180deg, rgba(88,166,255,.05), rgba(43,231,255,.02));
}
.left-col .mini-metrics {
  margin-top: 8px;
}
.spark-grid { stroke: rgba(130,170,210,.13); stroke-width: 1; }
.spark-line {
  stroke-dasharray: 520;
  stroke-dashoffset: 520;
  filter: drop-shadow(0 0 7px rgba(43,231,255,.45));
  animation: sparkTrace 2.3s ease-out forwards, sparkGlow 2.6s ease-in-out 2.3s infinite alternate;
}
.mini-metrics, .quad {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.metric {
  min-width: 0;
  min-height: 78px;
  padding: 10px;
  border: 1px solid rgba(88,166,255,.18);
  border-radius: 8px;
  background: rgba(8,26,46,.58);
}
.metric-label, .metric-sub { display: block; color: rgba(170,222,255,.65); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.metric-value { display: block; margin-top: 7px; color: #ffffff; font-size: clamp(20px, 2.1vw, 30px); line-height: 1; overflow-wrap: anywhere; }
.metric-value em { margin-left: 3px; color: rgba(170,222,255,.65); font-size: 12px; font-style: normal; }
.metric-sub { margin-top: 7px; }
.metric-cyan .metric-value { color: #2be7ff; }
.metric-green .metric-value { color: #2ee6a6; }
.metric-violet .metric-value { color: #9b8cff; }
.metric-amber .metric-value { color: #ffb020; }
.metric-red .metric-value { color: #ff4d6d; }
.side-summary {
  display: grid;
  gap: 8px;
}
.summary-tile {
  min-height: 46px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "label value"
    "sub value";
  align-items: center;
  column-gap: 10px;
  padding: 8px 10px;
  border: 1px solid rgba(88,166,255,.18);
  border-radius: 7px;
  background: linear-gradient(90deg, rgba(88,166,255,.09), rgba(8,26,46,.42));
}
.summary-tile span {
  grid-area: label;
  color: rgba(217,247,255,.78);
  font-size: 12px;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.summary-tile b {
  grid-area: value;
  color: #ffffff;
  font-size: 17px;
  line-height: 1;
  white-space: nowrap;
}
.summary-tile small {
  grid-area: sub;
  color: rgba(170,222,255,.56);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.summary-cyan b { color: #2be7ff; }
.summary-green b { color: #2ee6a6; }
.summary-violet b { color: #9b8cff; }
.summary-amber b { color: #ffb020; }
.summary-red b { color: #ff4d6d; }
.ai-stage {
  min-height: clamp(300px, 34vh, 344px);
  display: grid;
  grid-template-columns: minmax(142px, 190px) minmax(240px, 1fr) minmax(142px, 190px);
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 1px solid rgba(43,231,255,.26);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(8,27,50,.72), rgba(4,10,22,.58));
  box-shadow: inset 0 0 50px rgba(43,231,255,.07);
  overflow: hidden;
}
.stage-card {
  min-height: 144px;
  padding: 14px;
  display: grid;
  align-content: center;
  gap: 9px;
  border-radius: 8px;
  border: 1px solid rgba(88,166,255,.26);
  background: linear-gradient(145deg, rgba(7,24,44,.9), rgba(12,18,38,.72));
}
.stage-label { color: rgba(170,222,255,.62); font-size: 12px; }
.stage-card strong { color: #f7fdff; font-size: 16px; }
.stage-card b { font-size: clamp(28px, 3.4vw, 40px); line-height: 1; overflow-wrap: anywhere; }
.stage-card small { color: rgba(170,222,255,.72); font-size: 12px; }
.stage-green b { color: #2ee6a6; }
.stage-violet b { color: #9b8cff; }
.activity-card {
  min-width: 0;
  min-height: 230px;
  align-content: start;
  gap: 9px;
  padding: 12px;
  overflow: hidden;
  transition: border-color .35s ease, box-shadow .35s ease;
}
.activity-card.activity-active {
  border-color: rgba(43,231,255,.62);
  box-shadow: inset 0 0 26px rgba(43,231,255,.1), 0 0 22px rgba(43,231,255,.12);
  animation: activityCardPulse 2.2s ease-in-out infinite;
}
.activity-card.activity-failed { border-color: rgba(255,77,109,.58); }
.activity-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.activity-live {
  padding: 3px 6px;
  border: 1px solid rgba(88,166,255,.24);
  border-radius: 999px;
  color: rgba(170,222,255,.58);
  font-size: 9px;
  line-height: 1;
}
.activity-live.live {
  border-color: rgba(46,230,166,.42);
  color: #8fffd3;
  background: rgba(46,230,166,.1);
  box-shadow: 0 0 12px rgba(46,230,166,.14);
}
.activity-title {
  display: block;
  min-width: 0;
  color: #f7fdff;
  font-size: 14px !important;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-meta {
  min-width: 0;
  display: grid;
  gap: 4px;
  color: rgba(170,222,255,.62);
  font-size: 9px;
}
.activity-meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-meta span:first-child { color: #2be7ff; font-weight: 800; }
.activity-stepper {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 5px;
}
.activity-step {
  min-width: 0;
  padding: 5px 4px;
  border: 1px solid rgba(88,166,255,.18);
  border-radius: 5px;
  color: rgba(170,222,255,.62);
  background: rgba(88,166,255,.06);
  font-size: 9px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-step i {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 4px;
  border-radius: 50%;
  background: rgba(88,166,255,.38);
  box-shadow: 0 0 6px rgba(88,166,255,.24);
}
.activity-active .activity-step {
  opacity: .34;
  animation: activityStepIn .45s ease forwards;
}
.activity-active .activity-step i,
.stage-green .activity-step i { background: #2ee6a6; box-shadow: 0 0 8px rgba(46,230,166,.58); }
.stage-violet .activity-step i { background: #9b8cff; box-shadow: 0 0 8px rgba(155,140,255,.58); }
.activity-outcome {
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid rgba(46,230,166,.28);
  border-radius: 6px;
  color: #8fffd3;
  background: rgba(46,230,166,.08);
  font-size: 10px;
  font-weight: 800;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-active .activity-outcome {
  opacity: 0;
  animation: activityOutcomeIn .5s ease var(--activity-outcome-delay) forwards;
}
.activity-outcome.failed {
  border-color: rgba(255,77,109,.38);
  color: #ff9caf;
  background: rgba(255,77,109,.09);
}
.ai-core {
  position: relative;
  min-height: clamp(270px, 30vh, 304px);
  display: grid;
  place-items: center;
  isolation: isolate;
  overflow: hidden;
}
.core-live-status {
  position: absolute;
  top: 4px;
  left: 50%;
  min-width: 128px;
  max-width: calc(100% - 20px);
  padding: 5px 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  transform: translateX(-50%);
  border: 1px solid rgba(88,166,255,.24);
  border-radius: 999px;
  color: rgba(170,222,255,.72);
  background: rgba(5,14,28,.76);
  font-size: 9px;
  z-index: 5;
}
.core-live-status i {
  width: 6px;
  height: 6px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #2ee6a6;
  box-shadow: 0 0 10px rgba(46,230,166,.72);
}
.core-live-status b { color: #2be7ff; font-size: 9px; }
.core-live-status.status-error { border-color: rgba(255,176,32,.36); color: #ffd166; }
.core-live-status.status-error i { background: #ffb020; box-shadow: 0 0 10px rgba(255,176,32,.68); }
.core-processing .ai-sphere {
  border-color: rgba(43,231,255,.62);
  box-shadow:
    0 0 68px rgba(43,231,255,.32),
    0 0 108px rgba(111,116,255,.1),
    inset 0 0 50px rgba(64,147,255,.18);
}
.core-dual .ai-sphere {
  border-color: rgba(155,140,255,.78);
  box-shadow: 0 0 78px rgba(155,140,255,.38), inset 0 0 54px rgba(43,231,255,.25);
}
.core-batched {
  position: absolute;
  top: 36px;
  left: 50%;
  max-width: calc(100% - 24px);
  padding: 4px 7px;
  transform: translateX(-50%);
  border-radius: 5px;
  color: #ffd166;
  background: rgba(255,176,32,.08);
  font-size: 9px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  z-index: 5;
}
.ai-core:before {
  content: "";
  position: absolute;
  width: 210px;
  aspect-ratio: 1;
  border-radius: 50%;
  background: conic-gradient(from 120deg, rgba(43,231,255,0), rgba(43,231,255,.36), rgba(155,140,255,.34), rgba(255,209,102,.22), rgba(43,231,255,0));
  filter: blur(22px);
  opacity: .42;
  animation: coreAura 10s linear infinite;
  z-index: 0;
}
.ai-sphere {
  position: relative;
  width: clamp(190px, 24vw, 232px);
  aspect-ratio: 1;
  border-radius: 50%;
  display: grid;
  place-items: center;
  align-content: center;
  border: 1px solid rgba(43,231,255,.46);
  background:
    radial-gradient(circle at 35% 25%, rgba(255,255,255,.25), transparent 18%),
    radial-gradient(circle at 65% 70%, rgba(155,140,255,.28), transparent 28%),
    radial-gradient(circle, rgba(43,231,255,.34), rgba(20,60,110,.2) 45%, rgba(3,10,22,.74) 72%);
  box-shadow: 0 0 58px rgba(43,231,255,.28), inset 0 0 46px rgba(43,231,255,.22);
  z-index: 2;
  overflow: hidden;
  animation: sphereBreathe 4.6s ease-in-out infinite;
  transition: border-color .65s ease, box-shadow .85s ease, filter .85s ease;
}
.ai-sphere:before {
  content: "";
  position: absolute;
  inset: -18%;
  border-radius: 50%;
  background: conic-gradient(from 0deg, transparent 0 20%, rgba(43,231,255,.34) 28%, transparent 38% 58%, rgba(155,140,255,.28) 68%, transparent 78% 100%);
  opacity: .72;
  animation: sphereSpin 8s linear infinite;
  z-index: 0;
}
.ai-sphere:after {
  content: "";
  position: absolute;
  inset: -30% 42%;
  width: 38px;
  background: linear-gradient(180deg, transparent, rgba(255,255,255,.36), rgba(43,231,255,.22), transparent);
  transform: translateX(-180%) rotate(22deg);
  animation: sphereScan 3.8s ease-in-out infinite;
  z-index: 1;
}
.ai-sphere span {
  position: relative;
  z-index: 2;
  color: #ffd166;
  font-size: clamp(52px, 7vw, 78px);
  font-weight: 900;
  text-shadow: 0 0 22px rgba(255,209,102,.5);
}
.ai-sphere small {
  position: relative;
  z-index: 2;
  margin-top: 4px;
  color: rgba(217,247,255,.78);
}
.orbit {
  position: absolute;
  border: 1px solid rgba(43,231,255,.26);
  border-radius: 50%;
  z-index: 1;
  filter: drop-shadow(0 0 8px rgba(43,231,255,.22));
}
.orbit:before {
  content: "";
  position: absolute;
  top: 50%;
  left: -4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #2be7ff;
  box-shadow: 0 0 14px rgba(43,231,255,.85), 0 0 28px rgba(43,231,255,.35);
}
.orbit-a {
  width: min(286px, 92%);
  height: 196px;
  transform: rotate(18deg);
  animation: orbitA 12s linear infinite;
}
.orbit-b {
  width: 204px;
  height: min(286px, 96%);
  transform: rotate(52deg);
  border-color: rgba(155,140,255,.24);
  animation: orbitB 15s linear infinite;
}
.energy-ring {
  position: absolute;
  width: 238px;
  aspect-ratio: 1;
  border-radius: 50%;
  border: 1px solid rgba(43,231,255,.28);
  box-shadow: inset 0 0 26px rgba(43,231,255,.08), 0 0 26px rgba(43,231,255,.1);
  z-index: 0;
  animation: ringPulse 4s ease-out infinite;
}
.ring-b {
  width: 286px;
  border-color: rgba(155,140,255,.2);
  animation-delay: 1.55s;
}
.scan-line {
  position: absolute;
  width: 2px;
  height: 226px;
  border-radius: 999px;
  background: linear-gradient(180deg, transparent, rgba(43,231,255,.88), transparent);
  box-shadow: 0 0 16px rgba(43,231,255,.72);
  opacity: .72;
  transform: translateX(-140px) rotate(22deg);
  z-index: 1;
  animation: coreSweep 3.2s ease-in-out infinite;
  pointer-events: none;
}
.core-particle {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ffd166;
  box-shadow: 0 0 12px rgba(255,209,102,.85), 0 0 24px rgba(43,231,255,.45);
  z-index: 3;
  pointer-events: none;
}
.particle-a {
  transform: translate(122px, -78px);
  animation: particleA 5.8s ease-in-out infinite;
}
.particle-b {
  width: 5px;
  height: 5px;
  background: #2be7ff;
  transform: translate(-128px, 64px);
  animation: particleB 6.6s ease-in-out infinite;
}
.particle-c {
  width: 4px;
  height: 4px;
  background: #9b8cff;
  transform: translate(52px, 132px);
  animation: particleC 7.2s ease-in-out infinite;
}
.core-numbers {
  position: absolute;
  inset: auto 8px 14px;
  display: flex;
  justify-content: space-between;
  pointer-events: none;
  z-index: 4;
}
.core-numbers div {
  min-width: 78px;
  padding: 7px 9px;
  border: 1px solid rgba(88,166,255,.22);
  border-radius: 8px;
  background: rgba(5,14,28,.72);
  text-align: center;
}
.core-numbers b { display: block; color: #2be7ff; }
.core-numbers span { font-size: 11px; color: rgba(170,222,255,.62); }
@keyframes coreAura {
  from { transform: rotate(0deg) scale(.96); }
  50% { transform: rotate(180deg) scale(1.05); }
  to { transform: rotate(360deg) scale(.96); }
}
@keyframes sphereBreathe {
  0%, 100% { transform: scale(.99); filter: brightness(.96) saturate(1); }
  50% { transform: scale(1.025); filter: brightness(1.07) saturate(1.1); }
}
@keyframes sphereSpin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@keyframes sphereScan {
  0%, 18% { transform: translateX(-210%) rotate(22deg); opacity: 0; }
  38% { opacity: .55; }
  62% { opacity: .28; }
  84%, 100% { transform: translateX(210%) rotate(22deg); opacity: 0; }
}
@keyframes orbitA {
  from { transform: rotate(18deg); }
  to { transform: rotate(378deg); }
}
@keyframes orbitB {
  from { transform: rotate(52deg); }
  to { transform: rotate(-308deg); }
}
@keyframes ringPulse {
  0% { opacity: 0; transform: scale(.68); }
  18% { opacity: .46; }
  76% { opacity: .08; transform: scale(1.18); }
  100% { opacity: 0; transform: scale(1.28); }
}
@keyframes coreSweep {
  0%, 16% { transform: translateX(-154px) rotate(22deg); opacity: 0; }
  36% { opacity: .72; }
  70% { opacity: .3; }
  100% { transform: translateX(154px) rotate(22deg); opacity: 0; }
}
@keyframes particleA {
  0%, 100% { transform: translate(122px, -78px) scale(1); opacity: .82; }
  50% { transform: translate(98px, -104px) scale(1.45); opacity: 1; }
}
@keyframes particleB {
  0%, 100% { transform: translate(-128px, 64px) scale(1); opacity: .72; }
  50% { transform: translate(-102px, 92px) scale(1.35); opacity: 1; }
}
@keyframes particleC {
  0%, 100% { transform: translate(52px, 132px) scale(1); opacity: .62; }
  50% { transform: translate(78px, 104px) scale(1.4); opacity: .95; }
}
@keyframes activityCardPulse {
  0%, 100% { box-shadow: inset 0 0 22px rgba(43,231,255,.08), 0 0 14px rgba(43,231,255,.08); }
  50% { box-shadow: inset 0 0 32px rgba(43,231,255,.14), 0 0 26px rgba(43,231,255,.18); }
}
@keyframes activityStepIn {
  from { opacity: .34; transform: translateY(2px); }
  to { opacity: 1; transform: translateY(0); border-color: rgba(43,231,255,.34); color: #d9f7ff; }
}
@keyframes activityOutcomeIn {
  from { opacity: 0; transform: translateY(5px) scale(.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.flow-strip {
  min-height: 54px;
  display: grid;
  grid-template-columns: minmax(96px, 1fr) 28px minmax(96px, 1fr) 28px minmax(96px, 1fr) 28px minmax(96px, 1fr);
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid rgba(43,231,255,.24);
  border-radius: 8px;
  background: rgba(5,15,30,.76);
  overflow: hidden;
}
.flow-strip span {
  position: relative;
  min-width: 0;
  padding: 8px 8px;
  border-radius: 6px;
  background: rgba(88,166,255,.09);
  color: #ffffff;
  text-align: center;
  font-weight: 800;
  overflow-wrap: anywhere;
  box-shadow: inset 0 0 18px rgba(88,166,255,.06);
  animation: flowValuePulse 4.5s ease-in-out infinite;
}
.flow-strip .flow-success {
  border: 1px solid rgba(255,77,109,.36);
  color: #ffebf0;
  background: rgba(255,77,109,.11);
  box-shadow: inset 0 0 20px rgba(255,77,109,.1), 0 0 14px rgba(255,77,109,.12);
}
.flow-strip span small {
  display: block;
  color: rgba(170,222,255,.64);
  font-size: 10px;
  font-weight: 700;
  line-height: 1.15;
  white-space: nowrap;
}
.flow-strip span b {
  display: block;
  margin-top: 3px;
  color: #ffffff;
  font-size: 15px;
  line-height: 1;
}
.flow-strip .flow-success small { color: rgba(255,210,220,.75); }
.flow-strip .flow-success b { color: #ffffff; }
.flow-strip i {
  height: 2px;
  overflow: visible;
  background: linear-gradient(90deg, rgba(43,231,255,.18), rgba(43,231,255,.9), rgba(46,230,166,.2));
  background-size: 220% 100%;
  position: relative;
  box-shadow: 0 0 12px rgba(43,231,255,.35);
  animation: arrowFlow 1.8s linear infinite;
}
.flow-strip i:nth-of-type(2) { animation-delay: .28s; }
.flow-strip i:nth-of-type(3) { animation-delay: .56s; }
.flow-strip i:before {
  content: "";
  position: absolute;
  top: -3px;
  left: 12%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #2be7ff;
  box-shadow: 0 0 16px rgba(43,231,255,.85);
  animation: arrowDot 1.8s linear infinite;
}
.flow-strip i:after {
  content: "";
  position: absolute;
  right: -1px;
  top: -4px;
  border-left: 7px solid #2be7ff;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
}
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: stretch;
}
.dashboard-grid > .panel {
  align-self: stretch;
}
.panel-donut .panel-body,
.panel-radar .panel-body {
  min-height: 252px;
  display: grid;
}
.panel-donut .panel-body,
.panel-radar .panel-body { align-items: center; }
.panel-donut .panel-body { padding: 10px 12px; }
.panel-radar .panel-body { padding: 8px 12px 10px; justify-items: center; }
.panel-profile {
  grid-column: 1 / -1;
}
.panel-profile .panel-body {
  min-height: 174px;
  display: grid;
  padding: 10px;
  align-items: stretch;
}
.panel-donut .donut-wrap {
  width: 100%;
}
.donut-wrap {
  display: grid;
  grid-template-columns: minmax(120px, 150px) 1fr;
  align-items: center;
  gap: 10px;
}
.donut { width: 100%; max-height: 150px; }
.donut-arc {
  transform-origin: 60px 60px;
  filter: drop-shadow(0 0 4px rgba(43,231,255,.2));
  animation: donutSlicePulse 3.4s ease-in-out infinite;
  cursor: pointer;
  opacity: .88;
  transition: opacity .18s ease, transform .18s ease, filter .18s ease;
  outline: none;
}
.donut-arc:hover,
.donut-arc:focus-visible {
  opacity: 1;
  transform: scale(1.025);
}
.donut-arc.active {
  opacity: 1;
  transform: scale(1.035);
  animation: donutActivePulse 2.2s ease-in-out infinite;
}
.donut-label-line {
  stroke-width: 1;
  opacity: .72;
  pointer-events: none;
}
.donut-percent {
  font-size: 7.5px;
  font-weight: 900;
  pointer-events: none;
  paint-order: stroke;
  stroke: rgba(3,10,22,.92);
  stroke-width: 3px;
  stroke-linejoin: round;
}
.donut-percent.active { font-size: 8.5px; }
.donut-empty { opacity: .8; }
.donut-number { fill: #ffffff; font-size: 15px; font-weight: 800; }
.donut-label { fill: rgba(170,222,255,.62); font-size: 10px; }
.donut-rate { fill: #2be7ff; font-size: 10px; font-weight: 800; }
.legend { display: grid; gap: 6px; min-width: 0; }
.legend-item {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  border: 1px solid transparent;
  border-radius: 5px;
  color: rgba(217,247,255,.78);
  font-size: 11px;
  cursor: pointer;
  transition: border-color .18s ease, background .18s ease, box-shadow .18s ease;
  outline: none;
}
.legend-item:hover,
.legend-item:focus-visible,
.legend-item.active {
  border-color: rgba(43,231,255,.28);
  background: rgba(43,231,255,.08);
  box-shadow: inset 0 0 12px rgba(43,231,255,.08);
}
.legend-item i { width: 8px; height: 8px; border-radius: 50%; }
.legend-item span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend-item b { color: #ffffff; }
.legend-item em { color: #2be7ff; font-style: normal; font-weight: 800; }
.radar { width: min(100%, 190px); height: 176px; display: block; }
.radar-grid { fill: rgba(88,166,255,.05); stroke: rgba(88,166,255,.2); stroke-width: 1; animation: radarGridPulse 4s ease-in-out infinite; }
.radar-line { stroke: rgba(88,166,255,.16); stroke-width: 1; }
.radar-text { fill: rgba(170,222,255,.72); font-size: 9px; }
.radar-value {
  fill: rgba(46,230,166,.22);
  stroke: #2ee6a6;
  stroke-width: 2;
  filter: drop-shadow(0 0 7px rgba(46,230,166,.32));
  animation: radarValuePulse 2.8s ease-in-out infinite;
}
.profile-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-template-rows: minmax(0, 1fr);
  gap: 10px;
  min-height: 0;
}
.profile-group {
  min-width: 0;
  min-height: 0;
  padding: 9px;
  border: 1px solid rgba(88,166,255,.18);
  border-radius: 8px;
  background: rgba(7,23,42,.55);
  background: radial-gradient(circle at 12% 0%, color-mix(in srgb, var(--profile-color) 22%, transparent), transparent 42%), rgba(7,23,42,.55);
  box-shadow: inset 0 0 18px rgba(88,166,255,.05);
}
.profile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
  color: rgba(217,247,255,.9);
  font-size: 12px;
}
.profile-head span { font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.profile-head b { color: var(--profile-color); flex: 0 0 auto; }
.profile-items { display: grid; gap: 6px; }
.profile-row { min-width: 0; }
.profile-line {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: rgba(170,222,255,.74);
  font-size: 11px;
  line-height: 1.18;
}
.profile-line span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.profile-line b { color: #ffffff; font-size: 11px; flex: 0 0 auto; }
.profile-track {
  position: relative;
  height: 4px;
  margin-top: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(88,166,255,.12);
}
.profile-track span {
  position: relative;
  display: block;
  height: 100%;
  border-radius: inherit;
  overflow: hidden;
  background: linear-gradient(90deg, rgba(43,231,255,.38), var(--profile-color));
  background: linear-gradient(90deg, color-mix(in srgb, var(--profile-color) 48%, transparent), var(--profile-color));
  box-shadow: 0 0 12px rgba(43,231,255,.28);
}
.profile-track span:after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.72), transparent);
  transform: translateX(-120%);
  animation: profileFlow 2.6s ease-in-out infinite;
}
.funnel { display: grid; gap: 9px; padding: 6px 0; }
.funnel-row {
  display: grid;
  grid-template-columns: 68px minmax(70px, 1fr) auto;
  align-items: center;
  gap: 8px;
  color: rgba(217,247,255,.78);
  font-size: 12px;
}
.funnel-row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.funnel-row b { color: #ffffff; font-size: 12px; }
.funnel-bar {
  position: relative;
  height: 20px;
  border: 1px solid;
  border-radius: 4px;
  justify-self: center;
  min-width: 28px;
  overflow: hidden;
}
.funnel-bar:after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.38), transparent);
  transform: translateX(-120%);
  animation: funnelFlow 3s ease-in-out infinite;
}
.loop-diagram {
  min-height: 166px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  position: relative;
}
.loop-diagram:before {
  content: "";
  position: absolute;
  inset: 50% 18% auto;
  height: 1px;
  background: linear-gradient(90deg, rgba(43,231,255,.1), rgba(43,231,255,.75), rgba(46,230,166,.1));
  background-size: 220% 100%;
  box-shadow: 0 0 12px rgba(43,231,255,.26);
  animation: lineFlow 2.8s linear infinite;
}
.loop-node {
  min-height: 72px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 4px;
  border: 1px solid rgba(88,166,255,.24);
  border-radius: 8px;
  background: rgba(8,28,50,.62);
}
.loop-node b { color: #ffffff; font-size: 22px; overflow-wrap: anywhere; }
.loop-node span { color: rgba(170,222,255,.7); font-size: 12px; }
.loop-node.primary { border-color: rgba(43,231,255,.45); animation: loopNodePulse 3s ease-in-out infinite; }
.loop-node.primary b { color: #2be7ff; }
.loop-node.warn b { color: #ffb020; }
.loop-node.hot b { color: #ff4d6d; }
.gauge-wrap { position: relative; display: grid; place-items: center; margin-top: 2px; }
.gauge { width: 152px; height: 88px; overflow: visible; }
.gauge-value { filter: drop-shadow(0 0 8px rgba(46,230,166,.45)); animation: gaugePulse 2.7s ease-in-out infinite; }
.gauge-label { position: absolute; top: 36px; display: grid; text-align: center; }
.gauge-label b { color: #2ee6a6; font-size: 20px; }
.gauge-label span { color: rgba(170,222,255,.65); font-size: 12px; }
.rank-list {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-rows: repeat(var(--rank-count), minmax(30px, 1fr));
  align-content: start;
  gap: 4px;
}
.rank-row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  min-height: 31px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(88,166,255,.08);
}
.rank-row span { color: #ffd166; font-size: 11px; }
.rank-row b { color: rgba(217,247,255,.88); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-row em { color: #ffffff; font-size: 12px; font-style: normal; }
.rank-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid rgba(88,166,255,.14);
}
.rank-stat {
  min-width: 0;
  min-height: 46px;
  display: grid;
  align-content: center;
  gap: 4px;
  padding: 7px 6px;
  border: 1px solid rgba(88,166,255,.16);
  border-radius: 6px;
  background: rgba(8,26,46,.48);
  text-align: center;
}
.rank-stat span {
  color: rgba(170,222,255,.58);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-stat b {
  color: #2be7ff;
  font-size: 14px;
  line-height: 1;
}
.empty { color: rgba(170,222,255,.58); font-size: 12px; padding: 12px; text-align: center; }
.error-banner {
  margin-top: 10px;
  padding: 9px 12px;
  border: 1px solid rgba(255,77,109,.38);
  border-radius: 8px;
  color: #ffd1dc;
  background: rgba(255,77,109,.12);
  font-size: 13px;
}
@keyframes markSweep {
  0%, 20% { transform: translateX(0) rotate(18deg); opacity: 0; }
  42% { opacity: .72; }
  72%, 100% { transform: translateX(165px) rotate(18deg); opacity: 0; }
}
@keyframes statusPulse {
  0%, 100% { transform: scale(1); box-shadow: 0 0 10px rgba(255,209,102,.72); }
  50% { transform: scale(1.28); box-shadow: 0 0 16px rgba(255,209,102,1), 0 0 24px rgba(43,231,255,.28); }
}
@keyframes nodePulse {
  0%, 100% { box-shadow: 0 0 12px rgba(46,230,166,.22); }
  50% { box-shadow: 0 0 18px rgba(46,230,166,.48), 0 0 0 4px rgba(46,230,166,.08); }
}
@keyframes barFlow {
  from { transform: translateX(-120%); }
  to { transform: translateX(120%); }
}
@keyframes sparkTrace {
  to { stroke-dashoffset: 0; }
}
@keyframes sparkGlow {
  from { filter: drop-shadow(0 0 5px rgba(43,231,255,.32)); opacity: .84; }
  to { filter: drop-shadow(0 0 12px rgba(43,231,255,.72)); opacity: 1; }
}
@keyframes flowValuePulse {
  0%, 100% { box-shadow: inset 0 0 18px rgba(88,166,255,.06); }
  50% { box-shadow: inset 0 0 24px rgba(43,231,255,.14), 0 0 12px rgba(43,231,255,.1); }
}
@keyframes arrowFlow {
  from { background-position: 180% 0; }
  to { background-position: -40% 0; }
}
@keyframes arrowFlowDown {
  from { background-position: 0 180%; }
  to { background-position: 0 -40%; }
}
@keyframes arrowDot {
  from { left: 0; opacity: 0; transform: scale(.72); }
  20% { opacity: .95; transform: scale(1); }
  80% { opacity: .95; transform: scale(1); }
  to { left: calc(100% - 4px); opacity: 0; transform: scale(.72); }
}
@keyframes arrowDotDown {
  from { top: 0; opacity: 0; transform: scale(.72); }
  20% { opacity: .95; transform: scale(1); }
  80% { opacity: .95; transform: scale(1); }
  to { top: calc(100% - 4px); opacity: 0; transform: scale(.72); }
}
@keyframes donutSlicePulse {
  0%, 100% { opacity: .82; filter: drop-shadow(0 0 3px rgba(43,231,255,.18)); }
  50% { opacity: 1; filter: drop-shadow(0 0 9px rgba(43,231,255,.42)); }
}
@keyframes donutActivePulse {
  0%, 100% { opacity: .95; filter: drop-shadow(0 0 8px rgba(43,231,255,.42)); }
  50% { opacity: 1; filter: drop-shadow(0 0 15px rgba(43,231,255,.72)); }
}
@keyframes radarGridPulse {
  0%, 100% { opacity: .72; }
  50% { opacity: 1; }
}
@keyframes radarValuePulse {
  0%, 100% { opacity: .78; }
  50% { opacity: 1; }
}
@keyframes funnelFlow {
  0%, 22% { transform: translateX(-120%); opacity: 0; }
  48% { opacity: .82; }
  100% { transform: translateX(120%); opacity: 0; }
}
@keyframes profileFlow {
  0%, 22% { transform: translateX(-120%); opacity: 0; }
  45% { opacity: .74; }
  100% { transform: translateX(120%); opacity: 0; }
}
@keyframes lineFlow {
  from { background-position: 180% 0; }
  to { background-position: -40% 0; }
}
@keyframes loopNodePulse {
  0%, 100% { box-shadow: 0 0 0 rgba(43,231,255,0); }
  50% { box-shadow: 0 0 22px rgba(43,231,255,.18); }
}
@keyframes gaugePulse {
  0%, 100% { opacity: .82; filter: drop-shadow(0 0 7px rgba(46,230,166,.34)); }
  50% { opacity: 1; filter: drop-shadow(0 0 14px rgba(46,230,166,.68)); }
}
@media (max-width: 1280px) {
  .screen-grid {
    grid-template-columns: minmax(0, 1fr) minmax(315px, 350px);
    grid-template-areas:
      "center right"
      "left left";
  }
  .right-col { height: auto; align-content: start; }
  .left-col { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .profile-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
@media (max-width: 980px) {
  .adtd-root { margin: 0; padding: 0 10px; }
  .adtd-header, .screen-grid, .dashboard-grid, .right-col, .profile-grid { grid-template-columns: 1fr; }
  .screen-grid { grid-template-areas: "center" "right" "left"; }
  .left-col { grid-template-columns: 1fr; }
  .header-center, .header-tools { justify-content: flex-start; }
  .header-tools, .date-range { flex-wrap: wrap; }
  .date-input { width: min(150px, 100%); }
  .ai-stage { grid-template-columns: 1fr; }
  .flow-strip { grid-template-columns: 1fr; }
  .flow-strip i {
    height: 18px;
    width: 2px;
    justify-self: center;
    background: linear-gradient(180deg, rgba(43,231,255,.18), rgba(43,231,255,.9), rgba(46,230,166,.2));
    background-size: 100% 220%;
    animation-name: arrowFlowDown;
  }
  .flow-strip i:before { top: 0; left: -3px; animation-name: arrowDotDown; }
  .flow-strip i:after { right: -4px; top: auto; bottom: -1px; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 7px solid #2be7ff; border-bottom: 0; }
  .donut-wrap { grid-template-columns: 1fr; }
  .right-col .panel:last-child { display: block; }
  .right-col .panel:last-child .panel-body { display: block; }
}

/* AI command-center layout */
.command-root {
  --command-green: #20d59b;
  --command-green-soft: rgba(32, 213, 155, .18);
  --command-blue: #3677bc;
  --command-border: rgba(255, 255, 255, .09);
  position: relative;
  height: 100vh;
  min-height: 720px;
  min-width: 1180px;
  margin: 0;
  padding: 0;
  overflow: hidden;
  color: #f0f4f3;
  color-scheme: dark;
  background:
    radial-gradient(circle at 48% 42%, rgba(19, 72, 61, .16), transparent 28%),
    linear-gradient(180deg, #0a0d0c 0%, #070908 100%);
}
.command-root:before {
  content: "";
  position: absolute;
  inset: 68px 330px 142px 0;
  pointer-events: none;
  opacity: .16;
  background-image:
    linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
  background-size: 54px 54px;
  mask-image: radial-gradient(circle at center, #000, transparent 78%);
}
.command-root.event-rail-is-collapsed:before { right: 0; }
.command-header {
  position: relative;
  z-index: 10;
  display: grid;
  grid-template-columns: minmax(350px, 1fr) auto minmax(560px, 1fr);
  align-items: center;
  gap: 20px;
  height: 68px;
  padding: 0 20px;
  border-bottom: 1px solid var(--command-border);
  background: rgba(7, 9, 8, .96);
  box-shadow: 0 12px 34px rgba(0, 0, 0, .22);
}
.command-brand,
.command-tools,
.command-live,
.command-brand > div:last-child,
.event-rail-head > div {
  display: flex;
  align-items: center;
}
.command-brand { gap: 12px; min-width: 0; }
.command-logo {
  display: grid;
  place-items: center;
  flex: 0 0 42px;
  height: 42px;
  border: 1px solid rgba(32, 213, 155, .66);
  border-radius: 9px;
  color: #46e4af;
  background: linear-gradient(145deg, rgba(29, 128, 100, .34), rgba(8, 23, 19, .8));
  box-shadow: inset 0 0 18px rgba(32, 213, 155, .15), 0 0 18px rgba(32, 213, 155, .09);
  font-size: 16px;
  font-weight: 800;
}
.command-brand > div:last-child {
  align-items: flex-start;
  flex-direction: column;
  min-width: 0;
  line-height: 1.25;
}
.command-brand strong { font-size: 17px; letter-spacing: .02em; }
.command-brand span { margin-top: 3px; color: #788680; font-size: 11px; }
.command-live {
  justify-content: center;
  gap: 9px;
  min-width: 230px;
  padding: 8px 16px;
  border: 1px solid rgba(32, 213, 155, .25);
  border-radius: 999px;
  color: #b8c4c0;
  background: rgba(17, 31, 27, .5);
  font-size: 12px;
}
.command-live i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--command-green);
  box-shadow: 0 0 10px var(--command-green);
  animation: commandLive 1.6s ease-in-out infinite;
}
.command-live i.warn { background: #ffae34; box-shadow: 0 0 10px #ffae34; }
.command-live b { color: #47ddb0; font-weight: 600; }
.command-tools { justify-content: flex-end; gap: 8px; min-width: 0; }
.command-refresh {
  height: 34px;
  border: 1px solid rgba(255,255,255,.16);
  border-radius: 7px;
  color: #d8dedc;
  background: #141716;
  font: inherit;
  font-size: 12px;
}
.command-refresh { padding: 0 15px; cursor: pointer; }
.command-refresh:hover { border-color: rgba(32,213,155,.55); color: #53e0b3; }
.command-refresh:disabled { opacity: .55; cursor: default; }
.command-time-filter { position: relative; z-index: 40; }
.command-time-trigger {
  height: 34px;
  max-width: 370px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(88,166,255,.28);
  border-radius: 7px;
  color: rgba(217,247,255,.72);
  background: rgba(10,31,51,.9);
  padding: 0 10px;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.command-time-trigger:hover,
.command-time-trigger.open { border-color: rgba(43,231,255,.58); box-shadow: inset 0 0 18px rgba(43,231,255,.06); }
.command-time-trigger span { display: flex; min-width: 0; align-items: center; white-space: nowrap; }
.command-time-trigger span:first-child { max-width: 184px; overflow: hidden; text-overflow: ellipsis; }
.command-time-trigger b { overflow: hidden; color: #70dfff; font-weight: 650; text-overflow: ellipsis; }
.command-time-trigger i { width: 1px; height: 14px; flex: 0 0 1px; background: rgba(88,166,255,.25); }
.command-time-trigger em { color: rgba(170,222,255,.62); font-style: normal; }
.command-time-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 380px;
  overflow: hidden;
  border: 1px solid rgba(43,231,255,.3);
  border-radius: 9px;
  background: linear-gradient(155deg, rgba(8,31,51,.99), rgba(5,18,34,.99));
  box-shadow: 0 18px 46px rgba(0,0,0,.48), inset 0 0 28px rgba(43,231,255,.035);
}
.command-time-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 3px;
  margin: 12px;
  border-radius: 6px;
  background: rgba(2,12,24,.72);
  padding: 3px;
}
.command-time-tabs button,
.command-time-option,
.command-time-shortcuts button,
.command-time-panel-actions button {
  border: 1px solid transparent;
  border-radius: 5px;
  color: rgba(190,222,240,.68);
  background: transparent;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.command-time-tabs button { height: 30px; }
.command-time-tabs button.active { color: #73e7ff; background: rgba(28,75,111,.58); box-shadow: inset 0 -1px rgba(43,231,255,.2); }
.command-time-panel-body { display: grid; gap: 13px; border-top: 1px solid rgba(88,166,255,.14); padding: 13px; }
.command-time-panel-body label,
.command-time-inputs span { display: block; margin-bottom: 7px; color: rgba(170,222,255,.62); font-size: 11px; font-weight: 650; }
.command-time-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.command-time-option,
.command-time-shortcuts button { height: 30px; background: rgba(18,48,73,.56); }
.command-time-option:hover,
.command-time-shortcuts button:hover { color: #d9f7ff; background: rgba(29,73,105,.72); }
.command-time-option.selected { border-color: rgba(43,231,255,.52); color: #061725; background: linear-gradient(135deg, #35d4ff, #40e1bd); font-weight: 700; }
.command-time-inputs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.command-time-inputs input {
  box-sizing: border-box;
  width: 100%;
  height: 34px;
  border: 1px solid rgba(88,166,255,.3);
  border-radius: 5px;
  color: #d9f7ff;
  background: rgba(4,18,33,.86);
  padding: 0 7px;
  font: inherit;
  font-size: 11px;
  outline: none;
}
.command-time-inputs input:focus { border-color: rgba(43,231,255,.7); box-shadow: 0 0 0 2px rgba(43,231,255,.08); }
.command-time-shortcuts { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
.command-time-shortcuts button { padding: 0 3px; font-size: 10px; }
.command-time-panel-actions { display: flex; justify-content: flex-end; gap: 8px; border-top: 1px solid rgba(88,166,255,.14); padding: 10px 12px; }
.command-time-panel-actions button { height: 30px; min-width: 64px; border-color: rgba(88,166,255,.28); background: rgba(11,34,53,.8); }
.command-time-panel-actions button.primary { border-color: rgba(43,231,255,.58); color: #061725; background: linear-gradient(135deg, #35d4ff, #40e1bd); font-weight: 700; }
.command-clock {
  display: flex;
  align-items: flex-end;
  flex-direction: column;
  min-width: 112px;
  margin-left: 7px;
  line-height: 1.15;
}
.command-clock b { color: #f7faf9; font-size: 14px; font-variant-numeric: tabular-nums; }
.command-clock span {
  max-width: 150px;
  margin-top: 4px;
  overflow: hidden;
  color: #687671;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-root > .error-banner {
  position: absolute;
  z-index: 30;
  top: 76px;
  left: 50%;
  width: min(520px, 60vw);
  margin: 0;
  transform: translateX(-50%);
}
.command-shell {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  height: calc(100vh - 68px);
  min-height: 652px;
  transition: grid-template-columns .24s ease;
}
.command-shell.event-rail-collapsed { grid-template-columns: minmax(0, 1fr) 0; }
.command-main {
  display: grid;
  grid-template-rows: minmax(0, 1fr) 142px;
  min-width: 0;
  min-height: 0;
}
.command-graph {
  position: relative;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid var(--command-border);
  background:
    radial-gradient(circle at 50% 45%, rgba(22, 96, 76, .09), transparent 34%),
    linear-gradient(180deg, rgba(255,255,255,.008), transparent);
}
.command-graph:before,
.command-graph:after {
  content: "";
  position: absolute;
  z-index: 0;
  top: 17%;
  bottom: 21%;
  width: 1px;
  background: linear-gradient(transparent, rgba(32,213,155,.15), transparent);
}
.command-graph:before { left: 34%; }
.command-graph:after { right: 22%; }
.command-links {
  position: absolute;
  z-index: 1;
  inset: 2% 0 13%;
  width: 100%;
  height: 85%;
  overflow: visible;
  pointer-events: none;
}
.command-link {
  fill: none;
  stroke: rgba(47, 102, 157, .43);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-dasharray: 2 10;
  filter: drop-shadow(0 0 4px rgba(54, 119, 188, .3));
}
.command-link.dim { opacity: .55; }
.denoise-running .source-link,
.denoise-running .denoise-link,
.triage-running .triage-link,
.triage-running .risk-link {
  stroke: rgba(32, 213, 155, .85);
  stroke-dasharray: 9 9;
  filter: drop-shadow(0 0 7px rgba(32, 213, 155, .55));
  animation: commandFlow .78s linear infinite;
}
.source-stack {
  position: absolute;
  z-index: 3;
  top: 20%;
  bottom: 27%;
  left: 2.4%;
  display: flex;
  justify-content: space-between;
  flex-direction: column;
  width: 12.5%;
}
.command-source {
  position: relative;
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: 5px;
  padding-left: 6px;
}
.command-source span { color: #7c8783; font-size: 13px; }
.command-source b { color: #eff3f2; font-size: 18px; font-variant-numeric: tabular-nums; }
.command-source i {
  position: absolute;
  top: 11px;
  right: -4px;
  width: 4px;
  height: 13px;
  border-radius: 5px;
  background: var(--command-green);
  box-shadow: 0 0 11px var(--command-green);
}
.merge-node {
  position: absolute;
  z-index: 4;
  top: 46%;
  left: 27.5%;
  display: flex;
  align-items: center;
  flex-direction: column;
  width: 105px;
  transform: translate(-50%, -50%);
  text-align: center;
}
.merge-node:before {
  content: "";
  position: absolute;
  z-index: -1;
  top: -24px;
  width: 2px;
  height: 90px;
  background: linear-gradient(transparent, rgba(54,119,188,.5), transparent);
}
.merge-node b { color: #f4f6f5; font-size: 27px; line-height: 1; }
.merge-node span { margin-top: 7px; color: #8c9692; font-size: 13px; }
.merge-node small { margin-top: 8px; color: #36c998; font-size: 10px; }
.command-core {
  position: absolute;
  z-index: 5;
  top: 44%;
  left: 50%;
  width: min(25vw, 340px);
  height: min(25vw, 340px);
  min-width: 282px;
  min-height: 282px;
  transform: translate(-50%, -50%);
}
.core-ring {
  position: absolute;
  border-radius: 50%;
}
.command-ring.ring-1 {
  inset: 0;
  border: 1px solid rgba(32,213,155,.26);
  background:
    repeating-conic-gradient(from 0deg, rgba(32,213,155,.68) 0deg 1deg, transparent 1deg 7deg);
  mask: radial-gradient(circle, transparent 69%, #000 70% 72%, transparent 73%);
  animation: commandSpin 24s linear infinite;
}
.command-ring.ring-2 {
  inset: 10%;
  border: 1px dashed rgba(65,162,126,.52);
  box-shadow: inset 0 0 30px rgba(32,213,155,.07), 0 0 25px rgba(32,213,155,.07);
  animation: commandSpinReverse 18s linear infinite;
}
.command-ring.ring-3 {
  inset: 25%;
  border: 10px double rgba(44, 94, 80, .42);
  box-shadow: inset 0 0 28px rgba(32,213,155,.1);
  animation: commandCorePulse 2.8s ease-in-out infinite;
}
.command-core:before,
.command-core:after {
  content: "";
  position: absolute;
  inset: 5%;
  border-radius: 50%;
  border-left: 12px solid rgba(32,213,155,.28);
  border-right: 12px solid rgba(32,213,155,.28);
  filter: blur(.2px) drop-shadow(0 0 9px rgba(32,213,155,.22));
}
.command-core:after { inset: 18%; border-width: 2px; border-color: rgba(54,119,188,.44); }
.command-core-hex {
  position: absolute;
  z-index: 4;
  top: 50%;
  left: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  width: 92px;
  height: 104px;
  transform: translate(-50%, -50%);
  color: #58e3b7;
  background: linear-gradient(145deg, #14251f, #08100d);
  clip-path: polygon(25% 6%, 75% 6%, 100% 50%, 75% 94%, 25% 94%, 0 50%);
  filter: drop-shadow(0 0 13px rgba(32,213,155,.58));
}
.command-core-hex:before {
  content: "";
  position: absolute;
  inset: 4px;
  border: 1px solid rgba(32,213,155,.75);
  clip-path: inherit;
}
.command-core-hex span { position: relative; font-size: 27px; font-weight: 800; letter-spacing: .07em; }
.command-core-hex small { position: relative; margin-top: 2px; color: #d5e0dc; font-size: 10px; }
.command-core-status {
  position: absolute;
  z-index: 5;
  top: calc(50% + 59px);
  left: 50%;
  padding: 4px 10px;
  transform: translateX(-50%);
  border-radius: 12px;
  color: #96a49f;
  background: rgba(3,8,6,.84);
  font-size: 10px;
  white-space: nowrap;
}
.agent-badge {
  position: absolute;
  z-index: 6;
  display: grid;
  grid-template-columns: 25px auto;
  grid-template-rows: auto auto;
  min-width: 106px;
  padding: 8px 11px;
  border: 1px solid rgba(32,213,155,.42);
  border-radius: 9px;
  background: linear-gradient(145deg, rgba(17,54,43,.9), rgba(6,15,12,.92));
  box-shadow: inset 0 0 18px rgba(32,213,155,.08), 0 0 13px rgba(32,213,155,.09);
}
.agent-badge i {
  grid-row: 1 / 3;
  align-self: center;
  color: #3ce1ae;
  font-size: 20px;
  font-style: normal;
}
.agent-badge span { color: #35d9a6; font-size: 11px; }
.agent-badge b { margin-top: 2px; color: #e8eeec; font-size: 14px; font-weight: 600; }
.agent-badge.active {
  border-color: #3be4af;
  box-shadow: inset 0 0 22px rgba(32,213,155,.16), 0 0 20px rgba(32,213,155,.28);
  animation: agentPulse 1.2s ease-in-out infinite;
}
.agent-denoise { top: -4%; left: 50%; transform: translate(-50%, -50%); }
.agent-investigate { bottom: -1%; left: 12%; transform: translate(-50%, 50%); }
.agent-aggregate { right: 12%; bottom: -1%; transform: translate(50%, 50%); }
.outcome-stack {
  position: absolute;
  z-index: 4;
  top: 24%;
  left: 71.5%;
  display: flex;
  justify-content: space-between;
  flex-direction: column;
  height: 42%;
}
.outcome-stack > div { display: flex; flex-direction: column; min-width: 90px; }
.outcome-stack b { color: #eef1f0; font-size: 23px; line-height: 1; }
.outcome-stack span { margin-top: 6px; color: #78837f; font-size: 12px; }
.outcome-stack .primary b { color: #4bdbae; }
.severity-stack {
  position: absolute;
  z-index: 4;
  top: 20%;
  right: 2.7%;
  display: flex;
  justify-content: space-between;
  flex-direction: column;
  width: 9.5%;
  height: 48%;
}
.severity-node { display: flex; align-items: center; gap: 9px; }
.severity-node span {
  min-width: 46px;
  padding: 5px 8px;
  border-radius: 6px;
  color: #fff;
  text-align: center;
  font-size: 12px;
}
.severity-node b { color: #ecf0ee; font-size: 17px; }
.severity-critical span { background: #bd1632; }
.severity-high span { background: #e94747; }
.severity-medium span { background: #ee8d28; }
.severity-low span { background: #d5ad20; }
.command-lanes {
  position: absolute;
  z-index: 8;
  right: 15%;
  bottom: 12px;
  left: 15%;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.command-activity-lane {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 168px;
  align-items: stretch;
  gap: 14px;
  min-width: 0;
  height: 112px;
  padding: 10px 12px;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 10px;
  background: rgba(11, 15, 13, .86);
  box-shadow: inset 0 0 22px rgba(255,255,255,.015);
}
.command-activity-lane.active {
  border-color: rgba(32,213,155,.5);
  box-shadow: inset 0 0 24px rgba(32,213,155,.08), 0 0 12px rgba(32,213,155,.08);
}
.command-lane-copy { min-width: 0; }
.command-lane-head { display: flex; align-items: center; justify-content: space-between; }
.command-lane-head span { color: var(--drum-accent); font-size: 12px; font-weight: 700; }
.command-lane-head b { color: #7890a4; font-size: 11px; font-weight: 600; }
.command-activity-lane.active .command-lane-head b { color: #50e3b5; }
.command-lane-copy > strong {
  display: block;
  margin-top: 9px;
  overflow: hidden;
  color: #edf8ff;
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-lane-result {
  max-width: 100%;
  margin-top: 12px;
  overflow: hidden;
  color: #75e8c4;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-lane-result.idle { color: rgba(170,222,255,.55); }
.command-activity-lane.active .command-lane-result { opacity: 0; animation: laneResult .35s ease forwards; }
.command-drum-shell {
  position: relative;
  display: grid;
  grid-template-rows: 18px minmax(0, 1fr);
  min-width: 0;
  padding-left: 12px;
  border-left: 1px solid color-mix(in srgb, var(--drum-accent) 34%, transparent);
}
.command-drum-caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: rgba(170,222,255,.58);
  font-size: 10px;
}
.command-drum-caption b {
  color: var(--drum-accent);
  font-size: 10px;
  font-weight: 700;
  text-shadow: 0 0 8px var(--drum-accent);
}
.command-drum-window {
  position: relative;
  height: 78px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--drum-accent) 34%, transparent);
  border-radius: 8px;
  background:
    linear-gradient(90deg, rgba(2,10,21,.72), rgba(11,36,58,.78) 50%, rgba(2,10,21,.72)),
    repeating-linear-gradient(90deg, transparent 0 12px, rgba(43,231,255,.04) 12px 13px);
  box-shadow:
    inset 0 13px 18px rgba(0,0,0,.58),
    inset 0 -13px 18px rgba(0,0,0,.58),
    inset 0 0 24px color-mix(in srgb, var(--drum-accent) 10%, transparent),
    0 0 12px color-mix(in srgb, var(--drum-accent) 10%, transparent);
  perspective: 220px;
}
.command-drum-window:before {
  content: "";
  position: absolute;
  z-index: 4;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(180deg, rgba(2,8,18,.92), transparent 30%, transparent 70%, rgba(2,8,18,.92)),
    repeating-linear-gradient(180deg, transparent 0 12px, rgba(170,222,255,.12) 12px 13px, transparent 13px 26px);
  mask-image: linear-gradient(90deg, #000 0 6px, transparent 6px calc(100% - 6px), #000 calc(100% - 6px));
}
.command-drum-track {
  position: absolute;
  z-index: 1;
  top: 0;
  right: 7px;
  left: 7px;
  display: flex;
  flex-direction: column;
  animation: commandDrumRoll var(--drum-duration) linear infinite;
  will-change: transform;
}
.command-drum-step {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  align-items: center;
  gap: 8px;
  flex: 0 0 26px;
  height: 26px;
  padding: 0 9px;
  color: rgba(202,232,246,.62);
  transform: perspective(150px) rotateX(-5deg);
  transform-origin: center;
}
.command-drum-step i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--drum-accent);
  box-shadow: 0 0 8px var(--drum-accent);
}
.command-drum-step b {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: .16em;
  text-shadow: 0 0 9px color-mix(in srgb, var(--drum-accent) 55%, transparent);
}
.command-drum-step small { color: rgba(170,222,255,.4); font-size: 9px; font-variant-numeric: tabular-nums; }
.command-drum-focus {
  position: absolute;
  z-index: 3;
  top: 25px;
  right: 5px;
  left: 5px;
  height: 28px;
  pointer-events: none;
  border-top: 1px solid color-mix(in srgb, var(--drum-accent) 72%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--drum-accent) 72%, transparent);
  border-radius: 5px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--drum-accent) 14%, transparent), transparent);
  box-shadow: inset 0 0 12px color-mix(in srgb, var(--drum-accent) 12%, transparent), 0 0 12px color-mix(in srgb, var(--drum-accent) 16%, transparent);
  animation: commandDrumFocusPulse 1.7s ease-in-out infinite;
}
.command-drum-scan {
  position: absolute;
  z-index: 5;
  top: 25px;
  bottom: 25px;
  left: -14%;
  width: 20%;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.26), transparent);
  filter: blur(1px);
  animation: commandDrumScan 2.8s ease-in-out infinite;
}
.command-activity-lane.active .command-drum-window {
  border-color: color-mix(in srgb, var(--drum-accent) 72%, transparent);
  box-shadow: inset 0 0 26px color-mix(in srgb, var(--drum-accent) 16%, transparent), 0 0 20px color-mix(in srgb, var(--drum-accent) 24%, transparent);
}
.command-metrics {
  position: relative;
  z-index: 4;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  min-width: 0;
  border-top: 1px solid var(--command-border);
  border-right: 1px solid var(--command-border);
  background: rgba(7,9,8,.96);
}
.command-metric {
  position: relative;
  min-width: 0;
  padding: 15px 28px 9px;
  overflow: hidden;
  border-right: 1px solid var(--command-border);
}
.command-metric:last-child { border-right: 0; }
.command-metric > span { display: block; color: #b6bdbb; font-size: 12px; }
.command-metric > b { display: block; margin-top: 7px; color: #f1f4f3; font-size: 25px; line-height: 1; }
.command-metric > small {
  position: absolute;
  top: 38px;
  right: 24px;
  max-width: 48%;
  overflow: hidden;
  color: var(--metric-color);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-metric .sparkline { width: 100%; height: 43px; margin-top: 6px; opacity: .75; }
.command-metric .spark-grid { opacity: 0; }
.command-metric .spark-line { stroke-width: 4; filter: drop-shadow(0 0 5px var(--metric-color)); }
.command-event-rail {
  position: relative;
  z-index: 6;
  display: grid;
  grid-template-rows: 65px auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  border-left: 1px solid rgba(255,255,255,.025);
  background: #0b0d0c;
}
.command-event-rail.collapsed {
  display: block;
  border-left: 0;
  background: transparent;
  overflow: visible;
}
.event-rail-toggle {
  position: absolute;
  z-index: 12;
  top: 50%;
  left: -14px;
  width: 28px;
  height: 44px;
  border: 0;
  color: #6ee8ff;
  background: transparent;
  transform: translateY(-50%);
  opacity: .72;
  padding: 0;
  font-size: 28px;
  line-height: 1;
  text-shadow: 0 0 9px rgba(43,231,255,.48);
  cursor: pointer;
  transition: color .16s ease, opacity .16s ease, transform .16s ease;
}
.event-rail-toggle:hover {
  color: #fff;
  opacity: 1;
  transform: translate(2px, -50%);
}
.command-event-rail.collapsed .event-rail-toggle {
  left: -40px;
  width: 40px;
  height: 104px;
  border-radius: 12px 0 0 12px;
  color: #f4f7f9;
  background: rgba(65, 73, 82, .96);
  box-shadow: -5px 0 16px rgba(0,0,0,.22);
  opacity: .96;
  text-shadow: none;
}
.command-event-rail.collapsed .event-rail-toggle:hover {
  color: #fff;
  background: rgba(78, 88, 98, .98);
  transform: translate(-2px, -50%);
}
.command-event-rail.collapsed .event-rail-toggle span {
  display: grid;
  place-items: center;
  gap: 2px;
}
.command-event-rail.collapsed .event-rail-toggle i {
  font-style: normal;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.12;
}
.event-rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 17px;
  border-bottom: 1px solid var(--command-border);
}
.event-rail-head > div { align-items: flex-start; flex-direction: column; }
.event-rail-head strong { color: #e9edeb; font-size: 14px; }
.event-rail-head span { margin-top: 3px; color: #66716d; font-size: 10px; }
.event-rail-head b {
  min-width: 26px;
  padding: 4px 7px;
  border-radius: 12px;
  color: #48d8ac;
  background: rgba(32,213,155,.1);
  text-align: center;
  font-size: 11px;
}
.event-update-banner {
  margin: 10px 14px 4px;
  padding: 10px 12px;
  border: 1px solid rgba(58,117,183,.48);
  border-radius: 6px;
  color: #b8cff0;
  background: rgba(45,82,135,.28);
  font-size: 11px;
}
.event-update-banner:before { content: "ⓘ"; margin-right: 7px; color: #6ba4fb; }
.event-update-banner.warn { border-color: rgba(255,174,52,.42); color: #f1c67d; background: rgba(139,88,22,.2); }
.event-rail-list {
  min-height: 0;
  padding: 4px 16px 18px 20px;
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: #3a403e transparent;
}
.event-rail-item {
  position: relative;
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: 5px;
  min-height: 112px;
  padding: 13px 2px 12px 14px;
  border-bottom: 1px solid rgba(255,255,255,.1);
}
.event-rail-item:before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 1px;
  width: 1px;
  background: rgba(105,129,143,.25);
}
.event-rail-item:after {
  content: "";
  position: absolute;
  top: 21px;
  left: -2px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #466170;
  box-shadow: 0 0 7px rgba(70,97,112,.42);
}
.event-rail-item.kind-denoise:after { background: #2be7ff; box-shadow: 0 0 9px rgba(43,231,255,.7); }
.event-rail-item.kind-triage:after { background: #9b8cff; box-shadow: 0 0 9px rgba(155,140,255,.75); }
.event-rail-item.state-processing {
  background: linear-gradient(90deg, rgba(35,214,157,.07), transparent 72%);
}
.event-rail-meta { display: flex; align-items: center; width: 100%; gap: 7px; }
.event-rail-meta time { margin-left: auto; color: #646d69; font-size: 10px; }
.event-queue-kind,
.event-stage { padding: 4px 7px; border-radius: 4px; color: #fff; font-size: 10px; }
.event-queue-kind.kind-denoise { color: #55eaff; background: rgba(24,112,137,.38); }
.event-queue-kind.kind-triage { color: #c3baff; background: rgba(83,67,151,.45); }
.event-stage { color: #83aef1; background: rgba(52,86,143,.5); }
.event-rail-item.state-processing .event-stage { color: #57e1b5; background: rgba(23,111,83,.48); }
.event-rail-item.state-waiting .event-stage { color: #d6a95e; background: rgba(120,81,23,.38); }
.event-rail-item > strong,
.event-rail-item > span,
.event-rail-item > small {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-rail-item > strong { color: #dce1df; font-size: 12px; font-weight: 600; }
.event-rail-item > span { color: #7b8581; font-size: 10px; }
.event-rail-item > small { color: #4dcfa7; font-size: 9px; }
.event-rail-progress {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 3px;
}
.event-rail-progress-track {
  position: relative;
  height: 3px;
  overflow: hidden;
  border-radius: 3px;
  background: rgba(127,111,255,.16);
  box-shadow: inset 0 0 0 1px rgba(155,140,255,.12);
}
.event-rail-progress-track i {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(90deg, #716cff 0%, #a98dff 58%, #55e8ff 100%);
  box-shadow: 0 0 9px rgba(155,140,255,.82);
  transform: scaleX(var(--queue-progress, 0));
  transform-origin: left center;
  transition: transform .5s linear;
}
.event-rail-progress-track i:after {
  content: "";
  position: absolute;
  top: 50%;
  right: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #bdf7ff;
  box-shadow: 0 0 10px #73e9ff;
  transform: translate(50%, -50%);
}
.event-rail-progress > small {
  color: #9a8eff;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}
.event-rail-empty {
  display: grid;
  place-items: center;
  height: 180px;
  color: #535c59;
  font-size: 11px;
}
@keyframes commandFlow { to { stroke-dashoffset: -36; } }
@keyframes commandSpin { to { transform: rotate(360deg); } }
@keyframes commandSpinReverse { to { transform: rotate(-360deg); } }
@keyframes commandCorePulse {
  0%, 100% { opacity: .62; transform: scale(.97); }
  50% { opacity: 1; transform: scale(1.03); }
}
@keyframes commandLive {
  0%, 100% { opacity: .45; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.1); }
}
@keyframes agentPulse {
  0%, 100% { filter: brightness(.94); }
  50% { filter: brightness(1.2); }
}
@keyframes laneResult { to { opacity: 1; } }
@media (max-width: 1360px) {
  .command-header { grid-template-columns: minmax(330px, 1fr) auto minmax(500px, 1fr); gap: 12px; padding: 0 14px; }
  .command-shell { grid-template-columns: minmax(0, 1fr) 292px; }
  .command-root:before { right: 292px; }
  .command-core { width: 288px; height: 288px; min-width: 288px; min-height: 288px; }
  .command-time-trigger { max-width: 320px; }
  .command-clock { min-width: 92px; }
  .command-lanes { right: 14%; left: 14%; }
  .command-metric { padding-right: 18px; padding-left: 18px; }
}
@media (max-width: 1120px) {
  .command-root { padding: 0; }
  .command-header { grid-template-columns: 330px 1fr; }
  .command-live { display: none; }
  .command-shell { grid-template-columns: minmax(850px, 1fr) 280px; }
  .command-core { width: 270px; height: 270px; min-width: 270px; min-height: 270px; }
  .agent-badge { min-width: 96px; padding: 7px 8px; }
  .command-lanes { right: 12%; left: 12%; }
}

/* Align the command layout with the Flocks dark theme. */
.command-root {
  --command-green: #2ee6a6;
  --command-green-soft: rgba(46, 230, 166, .16);
  --command-blue: #2be7ff;
  --command-border: rgba(43, 231, 255, .19);
  background:
    radial-gradient(circle at 49% 41%, rgba(43, 231, 255, .11), transparent 30%),
    radial-gradient(circle at 78% 58%, rgba(155, 140, 255, .08), transparent 32%),
    linear-gradient(145deg, #04101d 0%, #07192a 48%, #0b1021 100%);
}
.command-root:before {
  opacity: .34;
  background-image:
    linear-gradient(rgba(43,231,255,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(43,231,255,.045) 1px, transparent 1px);
}
.command-header {
  border-bottom-color: rgba(43,231,255,.22);
  background: linear-gradient(180deg, rgba(5,20,34,.98), rgba(4,14,27,.96));
  box-shadow: 0 12px 34px rgba(1, 7, 16, .38), inset 0 -1px rgba(88,166,255,.05);
}
.command-logo {
  border-color: rgba(43,231,255,.56);
  color: #ffd166;
  background: linear-gradient(145deg, rgba(43,231,255,.22), rgba(6,27,44,.9));
  box-shadow: inset 0 0 18px rgba(43,231,255,.15), 0 0 18px rgba(43,231,255,.12);
  animation: commandLogoPulse 3.6s ease-in-out infinite;
}
.command-brand span,
.command-clock span { color: rgba(170,222,255,.6); }
.command-live {
  border-color: rgba(43,231,255,.28);
  color: rgba(217,247,255,.76);
  background: rgba(5, 24, 40, .78);
  box-shadow: inset 0 0 20px rgba(43,231,255,.06);
}
.command-live b { color: #2ee6a6; }
.command-time-trigger,
.command-refresh {
  border-color: rgba(88,166,255,.28);
  color: #d9f7ff;
  background: rgba(10,31,51,.9);
}
.command-graph {
  background:
    radial-gradient(circle at 50% 44%, rgba(43,231,255,.1), transparent 35%),
    linear-gradient(180deg, rgba(5,21,37,.94), rgba(5,16,31,.92));
}
.command-graph:before,
.command-graph:after {
  background: linear-gradient(transparent, rgba(43,231,255,.22), transparent);
}
.command-link {
  stroke: rgba(57, 140, 224, .55);
  filter: drop-shadow(0 0 5px rgba(43, 141, 255, .4));
  animation: commandFlow 3s linear infinite;
}
.command-link.risk-link { animation-direction: reverse; animation-duration: 3.8s; }
.triage-running .risk-link { animation-direction: normal; animation-duration: .78s; }
.command-source span,
.merge-node span,
.outcome-stack span { color: rgba(170,222,255,.62); }
.command-source i {
  background: #2be7ff;
  box-shadow: 0 0 12px rgba(43,231,255,.86);
  animation: commandPortPulse 1.8s ease-in-out infinite;
}
.command-source:nth-child(2) i { animation-delay: .7s; }
.merge-node b {
  color: #f7fdff;
  text-shadow: 0 0 16px rgba(43,231,255,.4);
  animation: commandValuePulse 2.6s ease-in-out infinite;
}
.merge-node:before {
  background: linear-gradient(transparent, rgba(43,231,255,.62), transparent);
  animation: commandMergeScan 2.4s ease-in-out infinite;
}
.command-original-core:before,
.command-original-core:after { display: none; }
.command-original-core > .ai-core {
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: visible;
}
.command-original-core .core-live-status { top: 8px; }
.command-original-core .core-numbers { display: none; }
.command-original-core .agent-denoise { top: -14%; }
.command-original-core .agent-investigate,
.command-original-core .agent-aggregate { bottom: -8%; }
.agent-badge {
  border-color: rgba(43,231,255,.38);
  background: linear-gradient(145deg, rgba(7,42,62,.94), rgba(5,18,34,.94));
  box-shadow: inset 0 0 18px rgba(43,231,255,.08), 0 0 13px rgba(43,231,255,.1);
}
.agent-badge i,
.agent-badge span { color: #2be7ff; }
.agent-badge.active {
  border-color: #2ee6a6;
  box-shadow: inset 0 0 22px rgba(46,230,166,.18), 0 0 22px rgba(43,231,255,.28);
}
.outcome-stack .primary {
  animation: commandOutcomePulse 2.5s ease-in-out infinite;
}
.outcome-stack .primary b { color: #2ee6a6; text-shadow: 0 0 14px rgba(46,230,166,.42); }
.severity-node {
  animation: commandSeverityFloat 4s ease-in-out infinite;
}
.severity-node:nth-child(2) { animation-delay: .55s; }
.severity-node:nth-child(3) { animation-delay: 1.1s; }
.severity-node:nth-child(4) { animation-delay: 1.65s; }
.command-activity-lane {
  border-color: rgba(88,166,255,.2);
  background: linear-gradient(145deg, rgba(7,28,47,.93), rgba(5,17,32,.92));
  box-shadow: inset 0 0 22px rgba(43,231,255,.025);
  animation: commandLaneIdle 4.8s ease-in-out infinite;
}
.command-activity-lane:nth-child(2) { animation-delay: 1.1s; }
.command-activity-lane.active {
  border-color: rgba(46,230,166,.62);
  animation: activityCardPulse 1.4s ease-in-out infinite;
}
.command-metrics {
  border-color: rgba(43,231,255,.2);
  background: linear-gradient(180deg, rgba(6,22,38,.98), rgba(4,14,27,.98));
}
.command-metric {
  border-right-color: rgba(43,231,255,.16);
  background: radial-gradient(circle at 50% 120%, rgba(43,231,255,.07), transparent 54%);
}
.command-metric .spark-line {
  stroke-dashoffset: 0;
  animation: commandMetricGlow 2.8s ease-in-out infinite;
}
.command-metric:nth-child(2) .spark-line { animation-delay: .45s; }
.command-metric:nth-child(3) .spark-line { animation-delay: .9s; }
.command-metric:nth-child(4) .spark-line { animation-delay: 1.35s; }
.command-event-rail {
  border-left-color: rgba(43,231,255,.16);
  background: linear-gradient(180deg, rgba(6,22,38,.99), rgba(5,15,29,.99));
}
.event-rail-head,
.event-rail-item { border-color: rgba(43,231,255,.15); }
.event-update-banner {
  position: relative;
  overflow: hidden;
  border-color: rgba(88,166,255,.48);
  color: #b9d8ff;
  background: rgba(31,76,132,.3);
}
.event-update-banner:after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -45%;
  width: 36%;
  background: linear-gradient(90deg, transparent, rgba(140,203,255,.18), transparent);
  animation: commandBannerSweep 3.8s ease-in-out infinite;
}
.event-rail-item.state-processing {
  background: linear-gradient(90deg, rgba(43,231,255,.06), transparent 64%);
  animation: commandEventActive 1.8s ease-in-out infinite;
}
.event-rail-item.motion-enter {
  will-change: opacity, transform;
  animation: commandQueueEnter .32s cubic-bezier(.22,.78,.24,1) both;
}
.event-rail-item.state-processing.motion-enter {
  animation:
    commandQueueEnter .32s cubic-bezier(.22,.78,.24,1) both,
    commandEventActive 1.8s .32s ease-in-out infinite;
}
.event-rail-item.motion-exit,
.event-rail-item.state-processing.motion-exit {
  overflow: hidden;
  pointer-events: none;
  transform-origin: 50% 0;
  will-change: opacity, transform, min-height, max-height, padding;
  animation: commandQueueExit .38s cubic-bezier(.4,0,.2,1) both;
}
.event-rail-item.motion-filter-exit,
.event-rail-item.state-processing.motion-filter-exit {
  pointer-events: none;
  will-change: opacity, transform;
  animation: commandQueueFilterExit .24s cubic-bezier(.4,0,.2,1) both;
}
@keyframes commandLogoPulse {
  0%, 100% { filter: brightness(.92); }
  50% { filter: brightness(1.18); box-shadow: inset 0 0 24px rgba(43,231,255,.22), 0 0 24px rgba(43,231,255,.2); }
}
@keyframes commandPortPulse {
  0%, 100% { opacity: .55; transform: scaleY(.72); }
  50% { opacity: 1; transform: scaleY(1.15); }
}
@keyframes commandValuePulse {
  0%, 100% { opacity: .82; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.06); }
}
@keyframes commandMergeScan {
  0%, 100% { opacity: .25; transform: translateY(-10px); }
  50% { opacity: .9; transform: translateY(10px); }
}
@keyframes commandOutcomePulse {
  0%, 100% { opacity: .72; transform: translateX(0); }
  50% { opacity: 1; transform: translateX(4px); }
}
@keyframes commandSeverityFloat {
  0%, 100% { transform: translateX(0); filter: brightness(.92); }
  50% { transform: translateX(5px); filter: brightness(1.14); }
}
@keyframes commandLaneIdle {
  0%, 100% { box-shadow: inset 0 0 22px rgba(43,231,255,.025); }
  50% { box-shadow: inset 0 0 30px rgba(43,231,255,.075), 0 0 12px rgba(43,231,255,.04); }
}
@keyframes commandDrumRoll {
  from { transform: translate3d(0, 0, 0); }
  to { transform: translate3d(0, -104px, 0); }
}
@keyframes commandDrumFocusPulse {
  0%, 100% { opacity: .58; filter: brightness(.88); }
  50% { opacity: 1; filter: brightness(1.28); }
}
@keyframes commandDrumScan {
  0%, 22% { transform: translateX(0); opacity: 0; }
  46% { opacity: .78; }
  78%, 100% { transform: translateX(620%); opacity: 0; }
}
@keyframes commandMetricGlow {
  0%, 100% { opacity: .46; filter: drop-shadow(0 0 3px var(--metric-color)); }
  50% { opacity: 1; filter: drop-shadow(0 0 9px var(--metric-color)); }
}
@keyframes commandBannerSweep {
  0%, 30% { transform: translateX(0); opacity: 0; }
  50% { opacity: 1; }
  80%, 100% { transform: translateX(420%); opacity: 0; }
}
@keyframes commandQueueEnter {
  from {
    opacity: 0;
    transform: translate3d(0,-6px,0) scale(.992);
  }
  to {
    opacity: 1;
    transform: translate3d(0,0,0) scale(1);
  }
}
@keyframes commandQueueExit {
  0% {
    opacity: 1;
    min-height: 112px;
    max-height: 160px;
    gap: 5px;
    padding-top: 13px;
    padding-bottom: 12px;
    border-color: rgba(43,231,255,.15);
    transform: translate3d(0,0,0) scale(1);
  }
  32% {
    opacity: 0;
    min-height: 112px;
    max-height: 160px;
    gap: 5px;
    padding-top: 13px;
    padding-bottom: 12px;
    border-color: rgba(43,231,255,.15);
    transform: translate3d(6px,-2px,0) scale(.994);
  }
  100% {
    opacity: 0;
    min-height: 0;
    max-height: 0;
    gap: 0;
    padding-top: 0;
    padding-bottom: 0;
    border-color: transparent;
    transform: translate3d(6px,-2px,0) scale(.994);
  }
}
@keyframes commandQueueFilterExit {
  from {
    opacity: 1;
    transform: translate3d(0,0,0) scale(1);
  }
  to {
    opacity: 0;
    transform: translate3d(8px,-2px,0) scale(.992);
  }
}
@keyframes commandEventActive {
  0%, 100% { box-shadow: inset 2px 0 rgba(46,230,166,.18); }
  50% { box-shadow: inset 5px 0 rgba(46,230,166,.38); }
}
.ai-task-progress {
  position: absolute;
  z-index: 2;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
  transform: rotate(-90deg);
  filter: drop-shadow(0 0 7px color-mix(in srgb, var(--core-task-accent) 52%, transparent));
}
.ai-task-progress circle {
  fill: none;
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
.ai-task-progress-base {
  stroke: color-mix(in srgb, var(--core-task-accent) 18%, transparent);
  stroke-dasharray: 2 3;
}
.ai-task-progress-value {
  stroke: var(--core-task-accent);
  stroke-linecap: round;
  stroke-dasharray: 100;
  stroke-dashoffset: 100;
  animation: aiTaskProgress var(--core-task-duration) linear forwards;
}
.core-task-active .ai-sphere {
  box-shadow:
    0 0 74px color-mix(in srgb, var(--core-task-accent) 32%, transparent),
    0 0 116px rgba(111,116,255,.11),
    inset 0 0 54px rgba(64,147,255,.19);
}
.core-task-active .ai-sphere > span:first-child { font-size: clamp(48px, 6vw, 68px); }
.ai-operation-window {
  position: absolute;
  z-index: 4;
  right: 23px;
  bottom: 27px;
  left: 23px;
  height: 19px;
  overflow: hidden;
  border-top: 1px solid color-mix(in srgb, var(--core-task-accent) 30%, transparent);
  border-bottom: 1px solid color-mix(in srgb, var(--core-task-accent) 30%, transparent);
  border-radius: 4px;
  background: rgba(3,12,25,.56);
  box-shadow: inset 0 0 12px color-mix(in srgb, var(--core-task-accent) 10%, transparent);
  animation: aiOperationWindowIn .45s cubic-bezier(.2,.72,.24,1) both;
}
.ai-operation-track {
  display: flex;
  flex-direction: column;
  animation: aiOperationRoll var(--core-task-duration) linear forwards;
}
.ai-operation-track span {
  display: block;
  flex: 0 0 18px;
  height: 18px;
  overflow: hidden;
  color: #e1f8ff;
  font-size: 10px;
  font-weight: 700;
  line-height: 18px;
  letter-spacing: .02em;
  text-align: center;
  text-overflow: ellipsis;
  text-shadow: 0 0 8px var(--core-task-accent);
  white-space: nowrap;
}
.ai-operation-idle {
  position: absolute;
  z-index: 4;
  right: 34px;
  bottom: 30px;
  left: 34px;
  color: rgba(170,222,255,.55);
  font-size: 9px;
  text-align: center;
  animation: aiOperationIdleIn .7s ease .25s both;
}
.ai-evidence-field {
  position: absolute;
  z-index: 7;
  inset: 0;
  pointer-events: none;
}
.ai-evidence-card {
  position: absolute;
  display: flex;
  flex-direction: column;
  width: 104px;
  min-height: 42px;
  padding: 6px 8px;
  opacity: 0;
  border: 1px solid color-mix(in srgb, var(--core-task-accent) 48%, transparent);
  border-radius: 6px;
  background: linear-gradient(145deg, rgba(7,38,61,.94), rgba(4,15,30,.94));
  box-shadow: inset 0 0 15px color-mix(in srgb, var(--core-task-accent) 8%, transparent), 0 0 14px color-mix(in srgb, var(--core-task-accent) 14%, transparent);
  animation: aiEvidenceArrive .48s ease forwards, aiEvidenceFloat 3.2s ease-in-out 1s infinite;
}
.ai-evidence-card:after {
  content: "";
  position: absolute;
  top: 50%;
  width: 28px;
  height: 1px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--core-task-accent) 64%, transparent), transparent);
  box-shadow: 0 0 7px var(--core-task-accent);
}
.ai-evidence-card:nth-child(odd):after { right: -29px; }
.ai-evidence-card:nth-child(even):after { left: -29px; transform: rotate(180deg); }
.ai-evidence-card span { color: var(--core-task-accent); font-size: 8px; }
.ai-evidence-card b {
  margin-top: 3px;
  overflow: hidden;
  color: #dff6ff;
  font-size: 9px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-evidence-card.evidence-1 { top: 24%; left: -19%; }
.ai-evidence-card.evidence-2 { top: 24%; right: -19%; }
.ai-evidence-card.evidence-3 { bottom: 22%; left: -22%; }
.ai-evidence-card.evidence-4 { right: -22%; bottom: 22%; }
.command-flow-particle {
  opacity: .28;
  fill: #2be7ff;
  filter: drop-shadow(0 0 6px rgba(43,231,255,.88));
  transition: opacity .3s ease;
}
.command-flow-particle.flow-triage {
  fill: #9b8cff;
  filter: drop-shadow(0 0 7px rgba(155,140,255,.92));
}
.denoise-running .command-flow-particle.flow-denoise,
.triage-running .command-flow-particle.flow-triage { opacity: 1; }
.denoise-running .command-flow-particle.result-particle,
.triage-running .command-flow-particle.result-particle {
  filter: drop-shadow(0 0 11px #fff) drop-shadow(0 0 16px currentColor);
}
.outcome-stack .primary.processing {
  padding: 8px 10px;
  margin-left: -10px;
  border-left: 2px solid #9b8cff;
  border-radius: 4px;
  background: linear-gradient(90deg, rgba(155,140,255,.13), transparent);
  animation: aiOutcomeProcessing 1s ease-in-out infinite;
}
.severity-node.active-target {
  animation: aiRiskTarget 1s ease-in-out infinite;
}
.severity-node.recent-target {
  animation: aiRiskResult .8s ease 2;
}
.command-metric-value {
  transform-origin: center bottom;
  animation: aiMetricFlip .58s cubic-bezier(.22,.9,.34,1.2);
}
@keyframes aiTaskProgress {
  to { stroke-dashoffset: 0; }
}
@keyframes aiOperationRoll {
  0%, 18% { transform: translateY(0); }
  22%, 38% { transform: translateY(-18px); }
  42%, 68% { transform: translateY(-36px); }
  72%, 94% { transform: translateY(-54px); }
  100% { transform: translateY(-72px); }
}
@keyframes aiOperationWindowIn {
  from { opacity: 0; transform: translateY(5px) scale(.96); filter: blur(2px); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes aiOperationIdleIn {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes aiEvidenceArrive {
  from { opacity: 0; transform: translateY(8px) scale(.88); filter: blur(3px); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}
@keyframes aiEvidenceFloat {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
@keyframes aiOutcomeProcessing {
  0%, 100% { opacity: .64; box-shadow: 0 0 0 rgba(155,140,255,0); }
  50% { opacity: 1; box-shadow: -6px 0 18px rgba(155,140,255,.2); }
}
@keyframes aiRiskTarget {
  0%, 100% { transform: translateX(0) scale(1); filter: brightness(.9); }
  50% { transform: translateX(7px) scale(1.08); filter: brightness(1.35) drop-shadow(0 0 9px rgba(255,85,85,.52)); }
}
@keyframes aiRiskResult {
  0% { transform: scale(1); }
  45% { transform: scale(1.16); filter: brightness(1.55); }
  100% { transform: scale(1); }
}
@keyframes aiMetricFlip {
  from { opacity: .15; transform: perspective(160px) rotateX(-72deg) translateY(5px); }
  to { opacity: 1; transform: perspective(160px) rotateX(0) translateY(0); }
}
.animated-number {
  display: inline-block;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.command-core {
  width: min(30vw, 410px);
  height: min(30vw, 410px);
  min-width: 330px;
  min-height: 330px;
}
.command-original-core .ai-core:before { width: 270px; }
.command-original-core .ai-sphere { width: clamp(238px, 27vw, 286px); }
.command-original-core .orbit-a { width: min(350px, 96%); height: 250px; }
.command-original-core .orbit-b { width: 250px; height: min(350px, 98%); }
.command-original-core .energy-ring { width: 292px; }
.command-original-core .energy-ring.ring-b { width: 350px; }
.command-original-core .scan-line { height: 280px; }
.command-original-core .ai-evidence-card.evidence-1 { top: 10%; left: 0; }
.command-original-core .ai-evidence-card.evidence-2 { top: 10%; right: 0; }
.command-original-core .ai-evidence-card.evidence-3 { bottom: 10%; left: 0; }
.command-original-core .ai-evidence-card.evidence-4 { right: 0; bottom: 10%; }
.command-original-core .ai-core:after {
  content: "";
  position: absolute;
  z-index: 0;
  top: 50%;
  left: 50%;
  width: 96%;
  aspect-ratio: 1;
  border-radius: 50%;
  pointer-events: none;
  opacity: .34;
  background: repeating-conic-gradient(from 0deg, color-mix(in srgb, var(--core-task-accent) 62%, transparent) 0deg 1deg, transparent 1deg 8deg);
  -webkit-mask: radial-gradient(circle, transparent 0 73%, #000 73.5% 75%, transparent 75.5%);
  mask: radial-gradient(circle, transparent 0 73%, #000 73.5% 75%, transparent 75.5%);
  animation: coreTickSpin 26s linear infinite;
}
.command-original-core .energy-ring:before {
  content: "";
  position: absolute;
  inset: -3px;
  border-radius: inherit;
  pointer-events: none;
  opacity: .78;
  background: conic-gradient(
    from 0deg,
    transparent 0 8%,
    color-mix(in srgb, var(--core-task-accent) 88%, #fff 12%) 9% 17%,
    transparent 18% 44%,
    rgba(255,209,102,.78) 45% 48%,
    transparent 49% 73%,
    rgba(155,140,255,.76) 74% 83%,
    transparent 84% 100%
  );
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px));
  mask: radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px));
  animation: coreArcSpin 8.8s linear infinite;
}
.command-original-core .energy-ring.ring-b:before {
  opacity: .56;
  animation-direction: reverse;
  animation-duration: 13.6s;
}
.command-original-core .orbit,
.command-original-core .energy-ring,
.command-original-core .ai-core:before,
.command-original-core .ai-core:after,
.command-original-core .scan-line,
.command-drum-track {
  will-change: transform, opacity;
  backface-visibility: hidden;
}
.command-original-core .orbit-a {
  border-top-color: rgba(43,231,255,.72);
  border-right-color: transparent;
  border-bottom-color: rgba(43,231,255,.16);
  animation-duration: 10.4s;
}
.command-original-core .orbit-b {
  border-top-color: transparent;
  border-right-color: rgba(155,140,255,.5);
  border-bottom-color: rgba(155,140,255,.12);
  animation-duration: 14.8s;
}
.core-task-denoise .ai-task-progress-value {
  stroke-dasharray: 18 82;
  stroke-dashoffset: 0;
  animation: coreDenoiseProgress 1.15s linear infinite;
}
.source-stack { width: 5.8%; }
.command-source span { white-space: nowrap; }
.command-activity-lane {
  grid-template-columns: minmax(0, 1.08fr) minmax(0, .92fr);
  gap: 8px;
  border: 0;
  border-radius: 0;
  background:
    radial-gradient(circle at 84% 48%, color-mix(in srgb, var(--drum-accent) 7%, transparent), transparent 46%),
    linear-gradient(90deg, rgba(5,25,43,.68), rgba(5,22,39,.38) 76%, transparent);
  box-shadow: none;
}
.command-activity-lane:before {
  content: "";
  position: absolute;
  z-index: 0;
  top: 0;
  right: 2%;
  left: 2%;
  height: 1px;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--drum-accent) 46%, transparent), transparent);
}
.command-activity-lane:after {
  content: "";
  position: absolute;
  z-index: 0;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(90deg, transparent 72%, color-mix(in srgb, var(--drum-accent) 5%, transparent), transparent);
}
.command-activity-lane > * { position: relative; z-index: 1; }
.command-activity-lane.active { border: 0; }
.command-drum-shell {
  padding-left: 4px;
  border-left: 0;
}
.command-drum-window {
  border: 0;
  border-radius: 0;
  background:
    radial-gradient(ellipse at center, color-mix(in srgb, var(--drum-accent) 8%, transparent), transparent 68%),
    linear-gradient(90deg, transparent, rgba(4,18,34,.7) 18%, rgba(4,18,34,.7) 82%, transparent);
  box-shadow: inset 0 14px 18px rgba(2,8,18,.72), inset 0 -14px 18px rgba(2,8,18,.72);
}
.command-drum-window:before {
  background: linear-gradient(180deg, rgba(2,8,18,.86), transparent 30%, transparent 70%, rgba(2,8,18,.86));
  mask-image: none;
}
.command-drum-focus {
  border: 0;
  border-radius: 0;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--drum-accent) 12%, transparent), transparent);
  box-shadow: 0 0 14px color-mix(in srgb, var(--drum-accent) 11%, transparent);
}
.command-activity-lane.active .command-drum-window {
  border: 0;
  box-shadow:
    inset 0 14px 18px rgba(2,8,18,.72),
    inset 0 -14px 18px rgba(2,8,18,.72),
    0 0 18px color-mix(in srgb, var(--drum-accent) 12%, transparent);
}
.command-drum-caption { justify-content: flex-end; }
.command-metrics {
  border: 0;
  background: linear-gradient(180deg, rgba(5,16,31,.92), rgba(4,14,27,.98));
}
.command-metric {
  border-right: 0;
  background: radial-gradient(ellipse at 50% 116%, color-mix(in srgb, var(--metric-color) 9%, transparent), transparent 58%);
}
.command-links {
  inset: 0;
  height: 100%;
}
.command-link {
  vector-effect: non-scaling-stroke;
  stroke: rgba(24, 64, 105, .68);
  stroke-width: 6;
  stroke-dasharray: none;
  opacity: .58;
  filter: drop-shadow(0 0 3px rgba(38,112,181,.36)) drop-shadow(0 0 7px rgba(18,67,112,.18));
  animation: none;
}
.command-link.risk-link { stroke-width: 5; }
.command-link.dim { opacity: .28; }
.command-link-dots {
  vector-effect: non-scaling-stroke;
  fill: none;
  stroke: rgba(87, 160, 226, .72);
  stroke-width: 1.35;
  stroke-linecap: round;
  stroke-dasharray: .7 12;
  opacity: .68;
  filter: drop-shadow(0 0 2px rgba(91,170,242,.64));
  animation: commandDotFlow 3.2s linear infinite;
}
.command-link-dots.dim { opacity: .3; }
.denoise-running .command-link.source-link,
.denoise-running .command-link.denoise-link {
  stroke: rgba(34, 118, 188, .86);
  stroke-width: 7;
  stroke-dasharray: none;
  filter: drop-shadow(0 0 5px rgba(43,174,255,.62)) drop-shadow(0 0 12px rgba(30,115,185,.35));
  animation: none;
}
.triage-running .command-link.triage-link,
.triage-running .command-link.risk-link {
  stroke: rgba(83, 91, 178, .82);
  stroke-width: 7;
  stroke-dasharray: none;
  filter: drop-shadow(0 0 5px rgba(155,140,255,.55)) drop-shadow(0 0 12px rgba(79,84,177,.3));
  animation: none;
}
.triage-running .command-link.risk-link { stroke-width: 6; }
.denoise-running .command-link-dots.source-link,
.denoise-running .command-link-dots.denoise-link {
  stroke: #7edcff;
  stroke-dasharray: 1 9;
  opacity: 1;
  animation: commandDotFlowActive .78s linear infinite;
}
.triage-running .command-link-dots.triage-link,
.triage-running .command-link-dots.risk-link {
  stroke: #b4a9ff;
  stroke-dasharray: 1 9;
  opacity: 1;
  animation: commandDotFlowActive .9s linear infinite;
}
.severity-node,
.severity-node:nth-child(2),
.severity-node:nth-child(3),
.severity-node:nth-child(4) {
  transform: none;
  animation: none;
}
.severity-node.active-target { animation: aiRiskGlow 1s ease-in-out infinite; }
.severity-node.recent-target { animation: aiRiskResultGlow .8s ease 2; }
.command-metric-value { animation: none; }
@keyframes commandDotFlow {
  to { stroke-dashoffset: -50.8; }
}
@keyframes commandDotFlowActive { to { stroke-dashoffset: -50; } }
@keyframes coreDenoiseProgress { to { stroke-dashoffset: -100; } }
@keyframes coreArcSpin { to { transform: rotate(360deg); } }
@keyframes coreTickSpin {
  from { transform: translate(-50%, -50%) rotate(0deg); }
  to { transform: translate(-50%, -50%) rotate(-360deg); }
}
@keyframes aiRiskGlow {
  0%, 100% { filter: brightness(.9) drop-shadow(0 0 0 rgba(255,85,85,0)); }
  50% { filter: brightness(1.35) drop-shadow(0 0 10px rgba(255,85,85,.58)); }
}
@keyframes aiRiskResultGlow {
  0%, 100% { filter: brightness(1); }
  45% { filter: brightness(1.55) drop-shadow(0 0 9px rgba(255,164,79,.48)); }
}
.core-batched {
  top: 37px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 9px;
  border: 1px solid rgba(43,231,255,.2);
  border-radius: 999px;
  color: rgba(217,247,255,.72);
  background: rgba(3,14,28,.78);
  box-shadow: 0 0 18px rgba(43,231,255,.09), inset 0 0 12px rgba(43,231,255,.05);
  backdrop-filter: blur(5px);
}
.core-batched b { color: #2be7ff; font-size: 10px; }
.core-batched span { color: rgba(170,222,255,.68); font-size: 9px; }
.core-load-burst .ai-sphere {
  box-shadow:
    0 0 80px rgba(43,231,255,.36),
    0 0 112px rgba(111,116,255,.12),
    inset 0 0 56px rgba(64,147,255,.2);
}
.core-load-surge .ai-sphere {
  animation-duration: 3.6s;
  box-shadow:
    0 0 88px rgba(43,231,255,.42),
    0 0 126px rgba(155,140,255,.15),
    inset 0 0 62px rgba(64,147,255,.22);
}
.core-load-surge .energy-ring:before { animation-duration: 3.8s; }
.core-load-surge .energy-ring.ring-b:before { animation-duration: 5.6s; }
.core-load-surge .core-batched {
  border-color: rgba(255,209,102,.38);
  box-shadow: 0 0 22px rgba(255,209,102,.11), inset 0 0 14px rgba(43,231,255,.07);
}
.core-load-surge .core-batched b { color: #ffd166; }
.command-live.load-burst,
.command-live.load-surge { border-color: rgba(43,231,255,.48); box-shadow: inset 0 0 24px rgba(43,231,255,.1), 0 0 16px rgba(43,231,255,.08); }
.command-live.load-surge { border-color: rgba(255,209,102,.42); }
.load-surge .command-link-dots.source-link,
.load-surge .command-link-dots.denoise-link { stroke-dasharray: 1 7; animation-duration: .58s; }
@media (max-width: 1360px) {
  .command-core { width: 350px; height: 350px; min-width: 350px; min-height: 350px; }
  .command-original-core .ai-sphere { width: 250px; }
  .command-original-core .orbit-a { width: 310px; height: 218px; }
  .command-original-core .orbit-b { width: 218px; height: 310px; }
  .command-original-core .energy-ring { width: 260px; }
  .command-original-core .energy-ring.ring-b { width: 310px; }
}
@media (max-width: 1120px) {
  .command-core { width: 310px; height: 310px; min-width: 310px; min-height: 310px; }
  .command-original-core .ai-sphere { width: 232px; }
}
@media (prefers-reduced-motion: no-preference) {
  .command-root:not(.command-is-processing) .command-link-dots {
    opacity: .4;
    filter: none;
    animation-duration: 6.4s;
  }
  .command-root:not(.command-is-processing) .command-flow-particle {
    opacity: .16;
    filter: none;
  }
  .command-root:not(.command-is-processing) .command-original-core .energy-ring:before {
    animation-duration: 18s;
  }
  .command-root:not(.command-is-processing) .command-original-core .energy-ring.ring-b:before {
    animation-duration: 28s;
  }
}
[data-animations="on"],
[data-animations="on"] *,
[data-animations="on"] *::before,
[data-animations="on"] *::after {
  animation-play-state: running !important;
}
`;
