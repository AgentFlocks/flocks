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
  denoise: { totalRaw: 0, totalUnique: 0, duplicates: 0, duplicateRate: 0, uniqueRate: 0, files: 0, parseErrors: 0 },
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

function todayLocal() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function mergeStats(raw) {
  return {
    ...EMPTY_STATS,
    ...(raw || {}),
    sourceStatus: { ...EMPTY_STATS.sourceStatus, ...((raw || {}).sourceStatus || {}) },
    denoise: { ...EMPTY_STATS.denoise, ...((raw || {}).denoise || {}) },
    triage: { ...EMPTY_STATS.triage, ...((raw || {}).triage || {}) },
    pipeline: { ...EMPTY_STATS.pipeline, ...((raw || {}).pipeline || {}) },
    closedLoop: { ...EMPTY_STATS.closedLoop, ...((raw || {}).closedLoop || {}) },
    dateRange: { ...EMPTY_STATS.dateRange, ...((raw || {}).dateRange || {}) },
    eventRange: { ...EMPTY_STATS.eventRange, ...((raw || {}).eventRange || {}) },
    timeline: { ...EMPTY_STATS.timeline, ...((raw || {}).timeline || {}) },
  };
}

function fullNumber(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat('zh-CN').format(n);
}

function compactNumber(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 100000000) return `${trim(n / 100000000)}亿`;
  if (Math.abs(n) >= 10000) return `${trim(n / 10000)}万`;
  return fullNumber(n);
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
      h(SummaryTile, { key: 'dup', label: '重复压缩', value: pct(stats.denoise.duplicateRate), sub: `${compactNumber(stats.denoise.duplicates)} 条收敛`, tone: 'green' }),
      h(SummaryTile, { key: 'unique', label: '唯一留存', value: pct(stats.denoise.uniqueRate), sub: `${compactNumber(stats.denoise.totalUnique)} 条进入研判`, tone: 'cyan' }),
      h(SummaryTile, { key: 'batch', label: '样例批次', value: compactNumber(stats.denoise.files), sub: '资产目录命中', tone: 'violet' }),
    ])),
  ]);
}

function CenterColumn({ stats }) {
  const radar = [
    { label: '降噪', value: 1 - clamp(stats.denoise.duplicateRate) },
    { label: '复用', value: stats.pipeline.workloadReuseRate },
    { label: '覆盖', value: stats.pipeline.coverageRate },
    { label: '攻击', value: stats.pipeline.attackRate },
    { label: '成功', value: stats.pipeline.successRate },
  ];
  return h('div', { className: 'column center-col' }, [
    h('div', { className: 'ai-stage', key: 'stage' }, [
      h(StageCard, {
        key: 'stage1',
        stage: '阶段一',
        title: '智能降噪',
        value: stats.denoise.totalUnique,
        sub: `重复压缩 ${pct(stats.denoise.duplicateRate)}`,
        tone: 'green',
      }),
      h(AiCore, { key: 'core', stats }),
      h(StageCard, {
        key: 'stage2',
        stage: '阶段二',
        title: '智能研判',
        value: stats.triage.totalRecords,
        sub: `缓存复用 ${pct(stats.triage.cacheRate)}`,
        tone: 'violet',
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

function AiCore({ stats }) {
  return h('div', { className: 'ai-core' }, [
    h('div', { className: 'energy-ring ring-a', key: 'ring-a' }),
    h('div', { className: 'energy-ring ring-b', key: 'ring-b' }),
    h('div', { className: 'scan-line', key: 'scan' }),
    h('div', { className: 'orbit orbit-a', key: 'orbit-a' }),
    h('div', { className: 'orbit orbit-b', key: 'orbit-b' }),
    h('div', { className: 'ai-sphere', key: 'sphere' }, [
      h('span', { key: 'ai' }, 'AI'),
      h('small', { key: 'label' }, '智能研判核心'),
    ]),
    h('div', { className: 'core-particle particle-a', key: 'particle-a' }),
    h('div', { className: 'core-particle particle-b', key: 'particle-b' }),
    h('div', { className: 'core-particle particle-c', key: 'particle-c' }),
    h('div', { className: 'core-numbers', key: 'numbers' }, [
      h('div', { key: 'left' }, [h('b', { key: 'v' }, pct(stats.pipeline.coverageRate)), h('span', { key: 'l' }, '覆盖率')]),
      h('div', { key: 'right' }, [h('b', { key: 'v' }, pct(stats.pipeline.successRate)), h('span', { key: 'l' }, '成功率')]),
    ]),
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

export default function Page() {
  const { useCallback, useEffect, useState } = getReact();
  const [startDate, setStartDate] = useState(todayLocal());
  const [endDate, setEndDate] = useState(todayLocal());
  const [stats, setStats] = useState(EMPTY_STATS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await getApi().page.get('/stats', { params: { startDate, endDate } });
      setStats(mergeStats(response.data));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'stats api failed');
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 30000);
    return () => window.clearInterval(id);
  }, [refresh]);

  return h('div', { className: 'adtd-root' }, [
    h('style', { key: 'style' }, CSS),
    h(Header, { key: 'header', startDate, endDate, setStartDate, setEndDate, stats, loading, refresh, error }),
    error ? h('div', { className: 'error-banner', key: 'error' }, `统计接口异常：${error}`) : null,
    h('main', { className: 'screen-grid', key: 'main' }, [
      h(SourceColumn, { key: 'left', stats }),
      h(CenterColumn, { key: 'center', stats }),
      h(RightColumn, { key: 'right', stats }),
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
.ai-core {
  position: relative;
  min-height: clamp(270px, 30vh, 304px);
  display: grid;
  place-items: center;
  isolation: isolate;
  overflow: hidden;
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
  0%, 100% {
    transform: scale(1);
    box-shadow: 0 0 58px rgba(43,231,255,.28), inset 0 0 46px rgba(43,231,255,.22);
  }
  50% {
    transform: scale(1.035);
    box-shadow: 0 0 78px rgba(43,231,255,.42), inset 0 0 58px rgba(155,140,255,.28);
  }
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
@media (prefers-reduced-motion: reduce) {
  .ai-core:before,
  .ai-sphere,
  .ai-sphere:before,
  .ai-sphere:after,
  .orbit,
  .energy-ring,
  .scan-line,
  .core-particle {
    animation: none;
  }
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
@media (prefers-reduced-motion: reduce) {
  .brand-mark:after,
  .panel-title i,
  .source-node.active,
  .source-track span:after,
  .spark-line,
  .flow-strip span,
  .flow-strip i,
  .flow-strip i:before,
  .donut-arc,
  .donut-arc.active,
  .radar-grid,
  .radar-value,
  .profile-track span:after,
  .funnel-bar:after,
  .loop-diagram:before,
  .loop-node.primary,
  .gauge-value {
    animation: none;
  }
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
`;
