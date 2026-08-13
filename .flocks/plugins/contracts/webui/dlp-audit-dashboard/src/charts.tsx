import React from 'react';

/* ====== Color Palette ====== */
const COLORS = {
  green:  '#10b981',
  yellow: '#f59e0b',
  orange: '#f97316',
  red:    '#ef4444',
  blue:   '#3b82f6',
  purple: '#8b5cf6',
  cyan:   '#06b6d4',
  gray:   '#64748b',
  bg:     '#0a0e27',
};

/* ====== DonutChart ====== */
interface DonutItem {
  name: string;
  value: number;
  color: string;
}

export function DonutChart({ data, size = 200, thickness = 28, label }: {
  data: DonutItem[];
  size?: number;
  thickness?: number;
  label?: string;
}) {
  const cx = size / 2;
  const cy = size / 2;
  const r = (size - thickness) / 2;
  const circumference = 2 * Math.PI * r;
  const total = data.reduce((s, d) => s + d.value, 0);
  let offset = 0;

  if (total === 0) return null;

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {data.map((item, i) => {
          const pct = item.value / total;
          const dash = pct * circumference;
          const seg = (
            <circle
              key={i}
              cx={cx}
              cy={cy}
              r={r}
              fill="none"
              stroke={item.color}
              strokeWidth={thickness}
              strokeDasharray={`${dash} ${circumference - dash}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
              transform={`rotate(-90 ${cx} ${cy})`}
              style={{ transition: 'stroke-dasharray 0.6s ease' }}
            />
          );
          offset += dash;
          return seg;
        })}
        {label && (
          <>
            <text x={cx} y={cy - 8} textAnchor="middle" fill="rgba(255,255,255,0.6)" fontSize="13">
              {label}
            </text>
            <text x={cx} y={cy + 16} textAnchor="middle" fill="#fff" fontSize="22" fontWeight="bold">
              {total.toLocaleString()}
            </text>
          </>
        )}
      </svg>
      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 mt-3">
        {data.map((item, i) => (
          <div key={i} className="flex items-center gap-1.5 text-xs">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: item.color }} />
            <span className="text-white/60">{item.name}</span>
            <span className="text-white/80 font-mono">{((item.value / total) * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== PieChart ====== */
interface PieItem {
  name: string;
  value: number;
  color: string;
}

export function PieChart({ data, size = 200, label }: {
  data: PieItem[];
  size?: number;
  label?: string;
}) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 4;
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) return null;

  let cumAngle = -Math.PI / 2;

  function polarToCartesian(cx2: number, cy2: number, r2: number, angle: number) {
    return { x: cx2 + r2 * Math.cos(angle), y: cy2 + r2 * Math.sin(angle) };
  }

  function describeArc(startAngle: number, endAngle: number) {
    const start = polarToCartesian(cx, cy, r, endAngle);
    const end = polarToCartesian(cx, cy, r, startAngle);
    const large = endAngle - startAngle > Math.PI ? 1 : 0;
    return `M ${cx} ${cy} L ${start.x} ${start.y} A ${r} ${r} 0 ${large} 0 ${end.x} ${end.y} Z`;
  }

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {data.map((item, i) => {
          const pct = item.value / total;
          const angle = pct * Math.PI * 2;
          const startAngle = cumAngle;
          const endAngle = cumAngle + angle;
          cumAngle = endAngle;
          return (
            <path
              key={i}
              d={describeArc(startAngle, endAngle)}
              fill={item.color}
              stroke={COLORS.bg}
              strokeWidth={2}
              style={{ transition: 'd 0.5s ease' }}
            />
          );
        })}
        {label && (
          <text x={cx} y={cy + 5} textAnchor="middle" fill="#fff" fontSize="16" fontWeight="bold">
            {label}
          </text>
        )}
      </svg>
      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 mt-3">
        {data.map((item, i) => (
          <div key={i} className="flex items-center gap-1.5 text-xs">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: item.color }} />
            <span className="text-white/60">{item.name}</span>
            <span className="text-white/80 font-mono">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== LineChart ====== */
interface LineSeries {
  key: string;
  data: number[];
  color: string;
  label: string;
  yAxis?: 'left' | 'right';
}

export function LineChart({ labels, series, width = 700, height = 260, padLeft = 50, padRight = 50, padTop = 20, padBottom = 32 }: {
  labels: string[];
  series: LineSeries[];
  width?: number;
  height?: number;
  padLeft?: number;
  padRight?: number;
  padTop?: number;
  padBottom?: number;
}) {
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const leftSeries = series.filter((s) => s.yAxis !== 'right');
  const rightSeries = series.filter((s) => s.yAxis === 'right');

  const allLeft = leftSeries.flatMap((s) => s.data);
  const allRight = rightSeries.flatMap((s) => s.data);

  const leftMin = Math.min(0, ...allLeft);
  const leftMax = Math.max(...allLeft, 1);
  const rightMin = rightSeries.length > 0 ? Math.min(0, ...allRight) : 0;
  const rightMax = rightSeries.length > 0 ? Math.max(...allRight, 1) : 1;

  function xPos(i: number) {
    return padLeft + (i / Math.max(labels.length - 1, 1)) * plotW;
  }

  function yPosLeft(v: number) {
    const range = leftMax - leftMin || 1;
    return padTop + plotH - ((v - leftMin) / range) * plotH;
  }

  function yPosRight(v: number) {
    const range = rightMax - rightMin || 1;
    return padTop + plotH - ((v - rightMin) / range) * plotH;
  }

  const gridLines = 4;
  const gridYs = Array.from({ length: gridLines + 1 }, (_, i) => padTop + (plotH * i) / gridLines);

  return (
    <div className="relative">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="w-full h-auto">
        {/* Grid */}
        {gridYs.map((gy, i) => (
          <line key={i} x1={padLeft} y1={gy} x2={width - padRight} y2={gy} stroke="rgba(255,255,255,0.06)" />
        ))}

        {/* Left-axis series */}
        {leftSeries.map((s) => {
          const points = s.data.map((v, i) => `${xPos(i)},${yPosLeft(v)}`).join(' ');
          return (
            <g key={s.key}>
              <polyline points={points} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
              {s.data.map((v, i) => (
                <circle key={i} cx={xPos(i)} cy={yPosLeft(v)} r={3} fill={s.color} stroke={COLORS.bg} strokeWidth={1.5} />
              ))}
            </g>
          );
        })}

        {/* Right-axis series */}
        {rightSeries.map((s) => {
          const points = s.data.map((v, i) => `${xPos(i)},${yPosRight(v)}`).join(' ');
          return (
            <g key={s.key}>
              <polyline points={points} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray="6 3" strokeLinejoin="round" />
              {s.data.map((v, i) => (
                <circle key={i} cx={xPos(i)} cy={yPosRight(v)} r={2.5} fill={s.color} />
              ))}
            </g>
          );
        })}

        {/* Left Y-axis labels */}
        {gridYs.map((gy, i) => {
          const val = leftMax - ((leftMax - leftMin) * i) / gridLines;
          return (
            <text key={i} x={padLeft - 8} y={gy + 4} textAnchor="end" fill="rgba(255,255,255,0.4)" fontSize="10">
              {val >= 1000 ? (val / 1000).toFixed(0) + 'k' : Math.round(val)}
            </text>
          );
        })}

        {/* Right Y-axis labels */}
        {rightSeries.length > 0 && gridYs.map((gy, i) => {
          const val = rightMax - ((rightMax - rightMin) * i) / gridLines;
          return (
            <text key={i} x={width - padRight + 8} y={gy + 4} textAnchor="start" fill="rgba(255,255,255,0.4)" fontSize="10">
              {Math.round(val)}%
            </text>
          );
        })}

        {/* X-axis labels (show every Nth) */}
        {labels.map((l, i) => {
          const step = Math.max(1, Math.floor(labels.length / 12));
          if (i % step !== 0) return null;
          return (
            <text key={i} x={xPos(i)} y={height - 6} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize="10">
              {l}
            </text>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="flex justify-center gap-6 mt-1">
        {series.map((s) => (
          <div key={s.key} className="flex items-center gap-1.5 text-xs">
            <span className="w-3 h-0.5 rounded" style={{ backgroundColor: s.color }} />
            <span className="text-white/50">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ====== HorizontalBar ====== */
interface BarItem {
  name: string;
  dept: string;
  value: number;
  risk: 'high' | 'medium' | 'low';
  color?: string;
}

export function HorizontalBar({ data, maxValue }: { data: BarItem[]; maxValue?: number }) {
  const max = maxValue ?? Math.max(...data.map((d) => d.value), 1);
  const riskColor = { high: COLORS.red, medium: COLORS.orange, low: COLORS.yellow };

  return (
    <div className="space-y-2.5">
      {data.map((item, i) => (
        <div key={i} className="flex items-center gap-3">
          <span className="text-white/40 text-xs w-5 text-right font-mono">{i + 1}</span>
          <span className="text-white/80 text-xs w-16 truncate" title={item.name}>{item.name}</span>
          <span className="text-white/30 text-xs w-14">{item.dept}</span>
          <div className="flex-1 h-5 bg-white/5 rounded-sm overflow-hidden relative">
            <div
              className="h-full rounded-sm transition-all duration-700 ease-out"
              style={{
                width: `${(item.value / max) * 100}%`,
                backgroundColor: item.color ?? riskColor[item.risk],
                opacity: 0.85,
              }}
            />
          </div>
          <span className="text-white/70 text-xs font-mono w-8 text-right">{item.value}</span>
          <span
            className="text-xs w-10 text-center rounded px-1 py-0.5 font-medium"
            style={{
              color: riskColor[item.risk],
              backgroundColor: `${riskColor[item.risk]}20`,
            }}
          >
            {item.risk === 'high' ? '高' : item.risk === 'medium' ? '中' : '低'}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ====== HeatmapGrid ====== */
interface HeatmapCell {
  dept: string;
  high: number;
  medium: number;
  low: number;
}

export function HeatmapGrid({ data }: { data: HeatmapCell[] }) {
  const maxVal = Math.max(...data.flatMap((d) => [d.high, d.medium, d.low]), 1);

  function intensity(v: number, base: string) {
    const alpha = 0.15 + (v / maxVal) * 0.7;
    return base + Math.round(alpha * 255).toString(16).padStart(2, '0');
  }

  const cols = ['高风险', '中风险', '低风险'];
  const baseColors = [COLORS.red, COLORS.orange, COLORS.green];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-separate" style={{ borderSpacing: '3px' }}>
        <thead>
          <tr>
            <th className="text-left text-white/40 font-normal py-1 px-1">部门</th>
            {cols.map((c) => (
              <th key={c} className="text-center text-white/40 font-normal py-1 px-1">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, ri) => (
            <tr key={ri}>
              <td className="text-white/70 py-1 px-1 whitespace-nowrap">{row.dept}</td>
              {[row.high, row.medium, row.low].map((v, ci) => (
                <td
                  key={ci}
                  className="text-center py-1.5 px-1 rounded font-mono font-bold"
                  style={{
                    backgroundColor: `${baseColors[ci]}20`,
                    color: v > 0 ? baseColors[ci] : 'rgba(255,255,255,0.2)',
                    borderLeft: v > 0 ? `2px solid ${baseColors[ci]}` : '2px solid transparent',
                  }}
                >
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ====== Gauge ====== */
export function Gauge({ value, max = 100, label, zones }: {
  value: number;
  max?: number;
  label?: string;
  zones?: { from: number; to: number; color: string }[];
}) {
  const size = 180;
  const cx = size / 2;
  const cy = size * 0.65;
  const r = size * 0.42;
  const strokeW = 18;
  const angleRange = 240; // degrees
  const startAngle = -120; // degrees from top

  const zones2 = zones ?? [
    { from: 0, to: 30, color: COLORS.green },
    { from: 30, to: 60, color: COLORS.yellow },
    { from: 60, to: max, color: COLORS.red },
  ];

  function angleToRad(deg: number) {
    return (deg * Math.PI) / 180;
  }

  function polarToCartesian(angle: number, radius: number) {
    const rad = angleToRad(angle);
    return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
  }

  function makeArc(from: number, to: number) {
    const s = polarToCartesian(startAngle + (from / max) * angleRange, r);
    const e = polarToCartesian(startAngle + (to / max) * angleRange, r);
    const large = (to - from) / max * angleRange > 180 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y}`;
  }

  const pct = Math.min(value / max, 1);
  const needleAngle = startAngle + pct * angleRange;
  const needleLen = r - strokeW / 2 - 6;
  const needle = polarToCartesian(needleAngle, needleLen);
  const needleBack = polarToCartesian(needleAngle, -10);

  let zoneColor = COLORS.green;
  if (zones2) {
    for (const z of zones2) {
      if (value >= z.from && value <= z.to) { zoneColor = z.color; break; }
    }
  }

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size * 0.85} viewBox={`0 0 ${size} ${size * 0.85}`}>
        {/* Zone arcs */}
        {zones2.map((z, i) => (
          <path key={i} d={makeArc(z.from, z.to)} fill="none" stroke={z.color} strokeWidth={strokeW} opacity={0.25} strokeLinecap="round" />
        ))}

        {/* Value arc */}
        <path d={makeArc(0, value)} fill="none" stroke={zoneColor} strokeWidth={strokeW} strokeLinecap="round" style={{ transition: 'd 0.8s ease' }} />

        {/* Needle */}
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y} stroke="#fff" strokeWidth={2.5} strokeLinecap="round" />
        <circle cx={cx} cy={cy} r={6} fill="#fff" />
        <circle cx={cx} cy={cy} r={3} fill={COLORS.bg} />

        {/* Value text */}
        <text x={cx} y={cy + 30} textAnchor="middle" fill="#fff" fontSize="28" fontWeight="bold" fontFamily="monospace">
          {value}%
        </text>
        {label && (
          <text x={cx} y={cy + 48} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize="12">
            {label}
          </text>
        )}
      </svg>
    </div>
  );
}

/* ====== Timeline ====== */
interface TimelineItem {
  time: string;
  user: string;
  dept: string;
  action: string;
  risk: 'high' | 'medium' | 'low';
  status: string;
}

export function Timeline({ items }: { items: TimelineItem[] }) {
  const riskColor = { high: COLORS.red, medium: COLORS.orange, low: COLORS.yellow };
  const riskLabel = { high: '高', medium: '中', low: '低' };

  return (
    <div className="relative max-h-64 overflow-y-auto pr-1">
      {items.map((item, i) => (
        <div key={i} className="flex items-start gap-3 py-2 border-b border-white/5 last:border-0">
          <span className="text-white/30 text-xs font-mono whitespace-nowrap mt-0.5">{item.time}</span>
          <span
            className="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0"
            style={{ backgroundColor: riskColor[item.risk] }}
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-white/80 text-xs font-medium truncate">{item.user}</span>
              <span className="text-white/30 text-xs">{item.dept}</span>
              <span
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ color: riskColor[item.risk], backgroundColor: `${riskColor[item.risk]}15` }}
              >
                {riskLabel[item.risk]}
              </span>
            </div>
            <p className="text-white/50 text-xs mt-0.5 truncate">{item.action}</p>
          </div>
          <span className="text-white/30 text-xs whitespace-nowrap">{item.status}</span>
        </div>
      ))}
    </div>
  );
}

/* ====== KpiCard ====== */
export function KpiCard({ label, value, unit, color, sub }: {
  label: string;
  value: string | number;
  unit?: string;
  color: string;
  sub?: string;
}) {
  return (
    <div
      className="rounded-lg p-4 flex flex-col justify-between border border-white/5 transition-all hover:border-white/10"
      style={{ backgroundColor: 'rgba(255,255,255,0.03)' }}
    >
      <div className="text-white/40 text-xs mb-2">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className="text-3xl font-bold font-mono" style={{ color }}>
          {typeof value === 'number' ? value.toLocaleString() : value}
        </span>
        {unit && <span className="text-white/30 text-sm">{unit}</span>}
      </div>
      {sub && <div className="text-white/25 text-xs mt-1">{sub}</div>}
    </div>
  );
}
