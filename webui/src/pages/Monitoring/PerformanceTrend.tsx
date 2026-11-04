import { useContext } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ThemeContext } from '@/contexts/ThemeContext';
import type { MetricsSnapshot } from '@/api/monitoring';

export interface TrendPoint {
  timestamp: number;
  toolCallRate: number | null;
  toolParseFailureRate: number | null;
}

/** The trend shows the last 10 minutes. */
export const TREND_WINDOW_MS = 10 * 60_000;
/** At most this many samples (10 minutes at the page's 5 s poll). */
export const TREND_MAX_POINTS = 120;
/**
 * The server reports the tool-call rates per one-minute window, so the first
 * values arrive about a minute after the first poll. Without any value after
 * this long the metrics are taken as unavailable.
 */
export const TREND_FIRST_VALUE_WAIT_MS = 90_000;

/**
 * Append one polled snapshot; repeated or older timestamps are ignored.
 * Samples older than the window go by time (a background tab polls less
 * often), and the count cap bounds the memory.
 */
export function appendTrendPoint(points: TrendPoint[], snapshot: MetricsSnapshot): TrendPoint[] {
  const last = points[points.length - 1];
  if (last && last.timestamp >= snapshot.timestamp) return points;
  const oldest = snapshot.timestamp - TREND_WINDOW_MS;
  const next = [
    ...points.filter((point) => point.timestamp >= oldest),
    {
      timestamp: snapshot.timestamp,
      toolCallRate: snapshot.toolCallRate,
      toolParseFailureRate: snapshot.toolParseFailureRate,
    },
  ];
  return next.length > TREND_MAX_POINTS ? next.slice(next.length - TREND_MAX_POINTS) : next;
}

// Categorical slots 1 (blue) and 2 (orange), stepped per theme and checked
// for colour-blind separation and 3:1 contrast on the card surface
// (#ffffff light, #303842 dark).
const PALETTE = {
  light: { calls: '#2a78d6', failures: '#eb6834', axis: '#6b7280', grid: '#e5e7eb', surface: '#ffffff', border: '#e5e7eb', text: '#111827' },
  dark: { calls: '#3987e5', failures: '#d95926', axis: '#9aa7b4', grid: '#4a5563', surface: '#303842', border: '#4a5563', text: '#d7dee8' },
};

export default function PerformanceTrend({ points }: { points: TrendPoint[] }) {
  const { t, i18n } = useTranslation('monitoring');
  const { effectiveTheme } = useContext(ThemeContext);
  const colors = effectiveTheme === 'dark' ? PALETTE.dark : PALETTE.light;
  const sampled = points.filter(
    (point) => point.toolCallRate != null || point.toolParseFailureRate != null,
  );
  const formatTime = (value: number) => new Date(value).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const formatRate = (value: unknown) => (
    typeof value === 'number' ? `${value.toFixed(1)}/min` : t('unavailable')
  );

  if (sampled.length < 2) {
    const sampledFor = points.length ? points[points.length - 1].timestamp - points[0].timestamp : 0;
    const unavailable = sampled.length === 0 && sampledFor >= TREND_FIRST_VALUE_WAIT_MS;
    return (
      <div className="h-64 flex items-center justify-center rounded-lg border border-dashed border-gray-300 text-sm text-gray-500 text-center px-4">
        {unavailable ? t('trendUnavailable') : t('trendCollecting')}
      </div>
    );
  }

  return (
    <div className="h-64" role="img" aria-label={t('trendAriaLabel', {
      calls: formatRate(sampled[sampled.length - 1].toolCallRate),
      failures: formatRate(sampled[sampled.length - 1].toolParseFailureRate),
    })}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke={colors.grid} strokeDasharray="3 3" />
          <XAxis
            dataKey="timestamp"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={formatTime}
            tick={{ fill: colors.axis, fontSize: 12 }}
            stroke={colors.grid}
            minTickGap={32}
          />
          <YAxis
            allowDecimals
            tick={{ fill: colors.axis, fontSize: 12 }}
            stroke={colors.grid}
            width={56}
            tickFormatter={(value: number) => `${value}`}
            label={{ value: t('trendAxis'), angle: -90, position: 'insideLeft', fill: colors.axis, fontSize: 12 }}
          />
          <Tooltip
            labelFormatter={(value) => formatTime(Number(value))}
            formatter={(value) => formatRate(value)}
            cursor={{ stroke: colors.axis, strokeWidth: 1 }}
            contentStyle={{
              background: colors.surface,
              borderColor: colors.border,
              borderRadius: 8,
              color: colors.text,
              fontSize: 12,
            }}
            labelStyle={{ color: colors.text }}
            itemStyle={{ color: colors.text }}
          />
          {/* Labels in text colour; the line swatch beside each one carries the series colour. */}
          <Legend
            wrapperStyle={{ fontSize: 12 }}
            formatter={(value) => <span style={{ color: colors.axis }}>{value}</span>}
          />
          <Line
            type="monotone"
            dataKey="toolCallRate"
            name={t('metrics.toolCallRate')}
            stroke={colors.calls}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="toolParseFailureRate"
            name={t('metrics.toolParseFailureRate')}
            stroke={colors.failures}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
