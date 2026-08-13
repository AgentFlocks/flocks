import { useState, useEffect } from 'react';
import { Card } from '@flocks/webui-contract-sdk';

interface AssetItem {
  id: string;
  name: string;
  type: string;
  ip: string;
  ports: string;
  risk: 'critical' | 'high' | 'medium' | 'low';
  source: string;
  tag: string;
}

interface TimelineEvent {
  time: string;
  type: 'added' | 'removed' | 'changed';
  asset: string;
  detail: string;
  ai: boolean;
}

interface MultiSourceRow {
  source: string;
  icon: string;
  subdomains: number;
  ips: number;
  ports: number;
  services: number;
  unique: number;
}

const MOCK_SOURCES: MultiSourceRow[] = [
  { source: 'ThreatBook', icon: 'TB', subdomains: 28, ips: 9, ports: 45, services: 18, unique: 3 },
  { source: 'FOFA', icon: 'FF', subdomains: 24, ips: 8, ports: 52, services: 15, unique: 5 },
  { source: 'Shodan', icon: 'SH', subdomains: 20, ips: 11, ports: 38, services: 12, unique: 2 },
  { source: 'AI 合并去重', icon: 'AI', subdomains: 32, ips: 11, ports: 67, services: 21, unique: 0 },
];

const MOCK_RISK: { label: string; value: number; color: string }[] = [
  { label: '严重', value: 3, color: '#ef4444' },
  { label: '高危', value: 8, color: '#f97316' },
  { label: '中危', value: 14, color: '#eab308' },
  { label: '低危', value: 22, color: '#22c55e' },
  { label: '无风险', value: 95, color: '#3b82f6' },
];

const MOCK_CLASSIFICATION: { label: string; value: number; color: string }[] = [
  { label: '主站/官网', value: 6, color: '#00d4ff' },
  { label: 'API 服务', value: 11, color: '#6366f1' },
  { label: '管理后台', value: 4, color: '#ef4444' },
  { label: 'CDN/静态', value: 18, color: '#22c55e' },
  { label: '文档/知识库', value: 3, color: '#eab308' },
  { label: '三方托管', value: 7, color: '#8b5cf6' },
  { label: '邮件服务', value: 2, color: '#06b6d4' },
  { label: '测试/预发', value: 5, color: '#f97316' },
];

const MOCK_TOP_RISK: AssetItem[] = [
  { id: '1', name: 'admin.example.com', type: '管理后台', ip: '203.0.113.10', ports: '443, 8080', risk: 'critical', source: 'FOFA', tag: '公网暴露无认证' },
  { id: '2', name: 'redis-prod.example.com', type: '缓存', ip: '203.0.113.55', ports: '6379', risk: 'critical', source: 'Shodan', tag: '未授权访问' },
  { id: '3', name: 'old-portal.example.com', type: 'Web', ip: '203.0.113.30', ports: '80, 443', risk: 'high', source: 'ThreatBook', tag: 'CVE-2024-4577' },
  { id: '4', name: 'jenkins.example.com', type: 'CI/CD', ip: '203.0.113.40', ports: '8080', risk: 'high', source: 'FOFA', tag: '弱口令风险' },
  { id: '5', name: 'api-v1.example.com', type: 'API', ip: '203.0.113.20', ports: '443', risk: 'high', source: 'ThreatBook', tag: 'TLS 1.0 弱协议' },
  { id: '6', name: 'db-admin.example.com', type: '数据库管理', ip: '203.0.113.60', ports: '3306, 5432', risk: 'medium', source: 'Shodan', tag: '端口暴露公网' },
  { id: '7', name: 'staging.example.com', type: '预发环境', ip: '203.0.113.70', ports: '443', risk: 'medium', source: 'FOFA', tag: '调试接口暴露' },
];

const MOCK_TIMELINE: TimelineEvent[] = [
  { time: '14:32', type: 'added', asset: 'api-v2.example.com', detail: '新 API 服务上线 443/tcp', ai: true },
  { time: '12:15', type: 'changed', asset: 'console.example.com', detail: '证书变更 Let\'s Encrypt → DigiCert', ai: true },
  { time: '10:48', type: 'added', asset: '*.s3.amazonaws.com', detail: '新存储桶发现', ai: true },
  { time: '09:47', type: 'removed', asset: 'staging-old.example.com', detail: '已下线 需确认是否合规', ai: false },
  { time: '08:20', type: 'changed', asset: 'www.example.com', detail: 'HTTP → HTTPS 强制跳转', ai: true },
  { time: '07:05', type: 'added', asset: 'grafana.example.com', detail: 'AI 识别为影子资产 无人认领', ai: true },
];

const RISK_TOTAL = MOCK_RISK.reduce((s, r) => s + r.value, 0);
const CLASS_TOTAL = MOCK_CLASSIFICATION.reduce((s, c) => s + c.value, 0);

function DonutChart({ data, size = 140, thickness = 28 }: { data: { label: string; value: number; color: string }[]; size?: number; thickness?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const radius = (size - thickness) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {data.map((d, i) => {
        const ratio = total > 0 ? d.value / total : 0;
        const dash = ratio * circumference;
        const gap = total > 1 ? 1.5 : 0;
        const strokeDash = Math.max(0, dash - gap);
        const seg = (
          <circle
            key={i}
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke={d.color}
            strokeWidth={thickness}
            strokeDasharray={`${strokeDash} ${circumference - strokeDash}`}
            strokeDashoffset={-offset}
            strokeLinecap="butt"
            style={{ transition: 'stroke-dashoffset 0.6s ease' }}
          />
        );
        offset += dash;
        return seg;
      })}
      <text x={center} y={center - 6} textAnchor="middle" fill="#e2e8f0" fontSize="22" fontWeight="700" fontFamily="system-ui">{total}</text>
      <text x={center} y={center + 14} textAnchor="middle" fill="#64748b" fontSize="11" fontFamily="system-ui">风险资产</text>
    </svg>
  );
}

function BarChart({ data, maxBars = 8 }: { data: { label: string; value: number; color: string }[]; maxBars?: number }) {
  const maxVal = Math.max(...data.map(d => d.value), 1);
  const barH = 20;
  const gap = 8;
  const labelW = 80;
  const chartW = 180;
  const h = data.length * (barH + gap) + 4;

  return (
    <svg width={labelW + chartW + 40} height={h} viewBox={`0 0 ${labelW + chartW + 40} ${h}`}>
      {data.map((d, i) => {
        const y = i * (barH + gap);
        const w = (d.value / maxVal) * chartW;
        return (
          <g key={i}>
            <text x={labelW - 6} y={y + barH / 2 + 4} textAnchor="end" fill="#94a3b8" fontSize="11" fontFamily="system-ui">{d.label}</text>
            <rect x={labelW} y={y} width={w} height={barH} rx="3" fill={d.color} opacity="0.85" />
            <text x={labelW + w + 6} y={y + barH / 2 + 4} fill="#cbd5e1" fontSize="11" fontFamily="monospace" fontWeight="600">{d.value}</text>
          </g>
        );
      })}
    </svg>
  );
}

function riskColor(r: string) {
  const map: Record<string, string> = { critical: '#ef4444', high: '#f97316', medium: '#eab308', low: '#22c55e' };
  return map[r] || '#6b7280';
}

function riskLabel(r: string) {
  const map: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危' };
  return map[r] || r;
}

export default function Page() {
  const [ready, setReady] = useState(false);

  useEffect(() => { setReady(true); }, []);

  const c = (n: number) => n >= 0 ? `+${n}` : `${n}`;

  return (
    <div style={styles.wrapper}>
      {/* ====== HEADER ====== */}
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <span style={styles.headerIcon}>🌐</span>
          <span style={styles.headerTitle}>互联网资产测绘大屏</span>
          <span style={styles.headerBadge}>AI 驱动 · 多源融合</span>
        </div>
        <div style={styles.headerRight}>
          <span style={styles.scanInfo}>扫描对象: <b>example.com</b></span>
          <span style={styles.scanInfo}>最后扫描: <b style={{ color: '#22c55e' }}>14 分钟前</b></span>
          <span style={styles.scanInfo}>数据源: <b>ThreatBook + FOFA + Shodan</b></span>
        </div>
      </div>

      {/* ====== KPI ROW ====== */}
      <div style={styles.kpiRow}>
        <KpiCard label="资产总数" value="142" sub={c(3) + ' 本周新增'} color="#00d4ff" />
        <KpiCard label="AI 发现影子资产" value="18" sub="占比 12.7% · 无人认领" color="#ef4444" highlight />
        <KpiCard label="高危风险" value="11" sub="严重 3 · 高危 8" color="#f97316" />
        <KpiCard label="AI 提效节省" value="47 人天" sub="传统 5天 → AI 18分钟" color="#8b5cf6" />
        <KpiCard label="AI 自动分类" value="56 条" sub="准确率 96.8%" color="#22c55e" />
        <KpiCard label="暴露端口" value="67" sub="含 22/3389/6379 高危" color="#eab308" />
        <KpiCard label="域名 / IP" value="32 / 11" sub="含 28 子域" color="#3b82f6" />
      </div>

      {/* ====== MAIN CONTENT ====== */}
      <div style={styles.mainGrid}>
        {/* 多源测绘数据对比 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> 多源测绘数据对比
            <span style={styles.panelTag}>AI 合并去重</span>
          </div>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>数据源</th>
                <th style={styles.th}>子域</th>
                <th style={styles.th}>IP</th>
                <th style={styles.th}>端口</th>
                <th style={styles.th}>服务</th>
                <th style={styles.th}>独有</th>
              </tr>
            </thead>
            <tbody>
              {MOCK_SOURCES.map((row, i) => (
                <tr key={i} style={row.source === 'AI 合并去重' ? styles.trHighlight : undefined}>
                  <td style={{ ...styles.td, color: row.source === 'AI 合并去重' ? '#00d4ff' : '#e2e8f0' }}>
                    <span style={styles.sourceBadge}>{row.icon}</span> {row.source}
                  </td>
                  <td style={styles.td}>{row.subdomains}</td>
                  <td style={styles.td}>{row.ips}</td>
                  <td style={styles.td}>{row.ports}</td>
                  <td style={styles.td}>{row.services}</td>
                  <td style={{ ...styles.td, color: row.unique > 0 ? '#f59e0b' : '#64748b' }}>{row.unique > 0 ? row.unique : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={styles.tableHint}>
            AI 去重合并：3 源原始数据 187 条 → 唯一资产 142 条，去重率 24.1%
          </div>
        </div>

        {/* 风险分布 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> 资产风险分布
            <span style={styles.panelTag}>AI 自动打标</span>
          </div>
          <div style={styles.donutWrap}>
            <DonutChart data={MOCK_RISK} size={150} thickness={26} />
            <div style={styles.legendList}>
              {MOCK_RISK.map((r, i) => (
                <div key={i} style={styles.legendItem}>
                  <span style={{ ...styles.legendDot, background: r.color }} />
                  <span style={styles.legendLabel}>{r.label}</span>
                  <span style={styles.legendVal}>{r.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ====== SECOND ROW ====== */}
      <div style={styles.mainGrid}>
        {/* AI 自动分类 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> AI 自动资产分类
            <span style={{ ...styles.panelTag, background: 'rgba(34,197,94,0.15)', color: '#22c55e' }}>准确率 96.8%</span>
          </div>
          <BarChart data={MOCK_CLASSIFICATION} />
        </div>

        {/* 高危资产列表 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> 高危资产 Top 7
            <span style={{ ...styles.panelTag, background: 'rgba(239,68,68,0.15)', color: '#ef4444' }}>需优先处置</span>
          </div>
          <div style={{ overflowY: 'auto', maxHeight: 230 }}>
            <table style={{ ...styles.table, width: '100%' }}>
              <thead>
                <tr>
                  <th style={styles.thSmall}>资产</th>
                  <th style={styles.thSmall}>类型</th>
                  <th style={styles.thSmall}>风险</th>
                  <th style={styles.thSmall}>AI 标签</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_TOP_RISK.map(a => (
                  <tr key={a.id}>
                    <td style={styles.tdSmall}>{a.name}</td>
                    <td style={styles.tdSmall}>{a.type}</td>
                    <td style={styles.tdSmall}>
                      <span style={{ ...styles.badge, background: riskColor(a.risk) + '20', color: riskColor(a.risk), borderColor: riskColor(a.risk) + '40' }}>
                        {riskLabel(a.risk)}
                      </span>
                    </td>
                    <td style={{ ...styles.tdSmall, color: '#f59e0b', fontSize: 11 }}>{a.tag}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ====== AI 提效看板 + 变更时间线 ====== */}
      <div style={styles.mainGrid}>
        {/* AI 提效看板 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> AI 提效看板
          </div>
          <div style={styles.efficiencyGrid}>
            <EfficiencyItem icon="🔍" title="自动资产发现" value="18 个" sub="AI 发现传统手段遗漏的影子资产" />
            <EfficiencyItem icon="🏷️" title="智能分类打标" value="96.8%" sub="按业务/环境/技术栈自动归类" />
            <EfficiencyItem icon="⚡" title="测绘耗时缩短" value="94%" sub="5 人天 → 18 分钟 · 一次对话完成" />
            <EfficiencyItem icon="🔗" title="多源数据融合" value="3 平台" sub="FOFA + Shodan + ThreatBook 交叉验证" />
            <EfficiencyItem icon="📊" title="自动报告生成" value="秒级" sub="Markdown + JSON 双格式输出" />
            <EfficiencyItem icon="🔄" title="持续跟踪 Diff" value="每 6h" sub="新增/消失/变更自动推送告警" />
          </div>
        </div>

        {/* 变更时间线 */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={styles.panelDot}>◆</span> 近期资产变更
            <span style={{ ...styles.panelTag, background: 'rgba(139,92,246,0.15)', color: '#8b5cf6' }}>AI 实时感知</span>
          </div>
          <div style={{ padding: '0 4px' }}>
            {MOCK_TIMELINE.map((e, i) => (
              <div key={i} style={styles.timelineItem}>
                <span style={{ ...styles.timelineIcon, color: e.type === 'added' ? '#22c55e' : e.type === 'removed' ? '#ef4444' : '#f59e0b' }}>
                  {e.type === 'added' ? '＋' : e.type === 'removed' ? '－' : '△'}
                </span>
                <div style={{ flex: 1 }}>
                  <div style={styles.timelineAsset}>
                    {e.asset}
                    {e.ai && <span style={styles.aiTag}>AI</span>}
                  </div>
                  <div style={styles.timelineDetail}>{e.detail}</div>
                </div>
                <span style={styles.timelineTime}>{e.time}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ====== FOOTER ====== */}
      <div style={styles.footer}>
        <span>Flocks AI · 互联网资产测绘大屏</span>
        <span>数据更新于 2 分钟前</span>
        <span>资产来源: 公开测绘数据 (FOFA / Shodan / ThreatBook) · 非攻击性扫描</span>
      </div>

      {!ready && <div style={styles.loading}>加载中...</div>}
    </div>
  );
}

function KpiCard({ label, value, sub, color, highlight }: { label: string; value: string; sub: string; color: string; highlight?: boolean }) {
  return (
    <div style={{ ...styles.kpiCard, borderColor: highlight ? color + '60' : 'rgba(255,255,255,0.06)' }}>
      <div style={{ ...styles.kpiGlow, background: highlight ? `radial-gradient(ellipse at center, ${color}15 0%, transparent 70%)` : 'none' }} />
      <div style={styles.kpiLabel}>{label}</div>
      <div style={{ ...styles.kpiValue, color, textShadow: highlight ? `0 0 18px ${color}40` : 'none' }}>{value}</div>
      <div style={styles.kpiSub}>{sub}</div>
    </div>
  );
}

function EfficiencyItem({ icon, title, value, sub }: { icon: string; title: string; value: string; sub: string }) {
  return (
    <div style={styles.effItem}>
      <div style={styles.effIcon}>{icon}</div>
      <div>
        <div style={styles.effTitle}>{title}</div>
        <div style={styles.effValue}>{value}</div>
        <div style={styles.effSub}>{sub}</div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    minHeight: '100vh',
    background: 'linear-gradient(135deg, #060b14 0%, #0a1628 40%, #0d1f3c 100%)',
    padding: '20px 24px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    color: '#e2e8f0',
    position: 'relative',
  },
  loading: {
    position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(6,11,20,0.85)', color: '#00d4ff', fontSize: 16,
  },

  /* ---- HEADER ---- */
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '14px 20px', marginBottom: 16,
    background: 'rgba(12,24,48,0.7)', backdropFilter: 'blur(12px)',
    border: '1px solid rgba(0,212,255,0.12)', borderRadius: 10,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 12 },
  headerIcon: { fontSize: 22 },
  headerTitle: { fontSize: 18, fontWeight: 700, color: '#f1f5f9', letterSpacing: 1 },
  headerBadge: {
    fontSize: 10, fontWeight: 600, padding: '2px 10px', borderRadius: 10,
    background: 'linear-gradient(135deg, rgba(0,212,255,0.2), rgba(139,92,246,0.2))',
    border: '1px solid rgba(0,212,255,0.3)', color: '#00d4ff',
  },
  headerRight: { display: 'flex', gap: 20 },
  scanInfo: { fontSize: 11, color: '#64748b' },

  /* ---- KPI ---- */
  kpiRow: {
    display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 12, marginBottom: 16,
  },
  kpiCard: {
    position: 'relative',
    background: 'rgba(12,24,48,0.7)', backdropFilter: 'blur(10px)',
    border: '1px solid', borderRadius: 10, padding: '14px 16px',
    overflow: 'hidden',
  },
  kpiGlow: { position: 'absolute', inset: 0, pointerEvents: 'none' },
  kpiLabel: { fontSize: 11, color: '#64748b', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1 },
  kpiValue: { fontSize: 26, fontWeight: 800, fontFamily: 'monospace', lineHeight: 1.1 },
  kpiSub: { fontSize: 10, color: '#475569', marginTop: 4 },

  /* ---- PANELS ---- */
  mainGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 },
  panel: {
    background: 'rgba(12,24,48,0.65)', backdropFilter: 'blur(10px)',
    border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10,
    padding: 16, overflow: 'hidden',
  },
  panelHeader: {
    fontSize: 13, fontWeight: 700, color: '#cbd5e1', marginBottom: 14,
    display: 'flex', alignItems: 'center', gap: 8,
  },
  panelDot: { color: '#00d4ff', fontSize: 10 },
  panelTag: {
    fontSize: 10, fontWeight: 600, padding: '1px 8px', borderRadius: 8,
    background: 'rgba(0,212,255,0.12)', color: '#00d4ff', marginLeft: 'auto',
  },

  /* ---- TABLE ---- */
  table: { width: '100%', borderCollapse: 'collapse' },
  th: {
    textAlign: 'left', padding: '6px 8px', fontSize: 10, fontWeight: 600,
    color: '#64748b', borderBottom: '1px solid rgba(255,255,255,0.06)', textTransform: 'uppercase',
  },
  td: {
    padding: '7px 8px', fontSize: 11, borderBottom: '1px solid rgba(255,255,255,0.03)', color: '#cbd5e1',
  },
  trHighlight: { background: 'rgba(0,212,255,0.06)' },
  thSmall: { padding: '5px 6px', fontSize: 10, fontWeight: 600, color: '#64748b', textAlign: 'left', borderBottom: '1px solid rgba(255,255,255,0.06)' },
  tdSmall: { padding: '5px 6px', fontSize: 11, color: '#cbd5e1', borderBottom: '1px solid rgba(255,255,255,0.03)' },
  sourceBadge: {
    display: 'inline-block', width: 22, height: 18, lineHeight: '18px', textAlign: 'center',
    fontSize: 9, fontWeight: 700, borderRadius: 3, marginRight: 6,
    background: 'rgba(0,212,255,0.15)', color: '#00d4ff',
  },
  tableHint: {
    marginTop: 10, fontSize: 10, color: '#475569', textAlign: 'center',
    borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: 8,
  },
  badge: {
    display: 'inline-block', padding: '1px 7px', borderRadius: 4, fontSize: 10, fontWeight: 600,
    border: '1px solid',
  },

  /* ---- DONUT ---- */
  donutWrap: { display: 'flex', alignItems: 'center', gap: 24, justifyContent: 'center' },
  legendList: { display: 'flex', flexDirection: 'column', gap: 8 },
  legendItem: { display: 'flex', alignItems: 'center', gap: 8 },
  legendDot: { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  legendLabel: { fontSize: 11, color: '#94a3b8', width: 36 },
  legendVal: { fontSize: 12, fontWeight: 700, fontFamily: 'monospace', color: '#e2e8f0', width: 24, textAlign: 'right' },

  /* ---- EFFICIENCY ---- */
  efficiencyGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  effItem: {
    display: 'flex', gap: 10, padding: 10, borderRadius: 8,
    background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)',
  },
  effIcon: { fontSize: 18, flexShrink: 0, width: 28, textAlign: 'center' },
  effTitle: { fontSize: 11, color: '#94a3b8', marginBottom: 2 },
  effValue: { fontSize: 15, fontWeight: 700, color: '#00d4ff', fontFamily: 'monospace' },
  effSub: { fontSize: 10, color: '#475569', marginTop: 1 },

  /* ---- TIMELINE ---- */
  timelineItem: {
    display: 'flex', alignItems: 'flex-start', gap: 10, padding: '6px 0',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
  },
  timelineIcon: { fontSize: 14, fontWeight: 700, width: 18, textAlign: 'center', flexShrink: 0 },
  timelineAsset: { fontSize: 12, fontWeight: 600, color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: 6 },
  timelineDetail: { fontSize: 10, color: '#64748b', marginTop: 1 },
  timelineTime: { fontSize: 10, color: '#475569', flexShrink: 0 },
  aiTag: {
    fontSize: 8, fontWeight: 700, padding: '0px 5px', borderRadius: 3,
    background: 'linear-gradient(135deg, rgba(139,92,246,0.3), rgba(0,212,255,0.3))',
    color: '#a78bfa', border: '1px solid rgba(139,92,246,0.3)',
  },

  /* ---- FOOTER ---- */
  footer: {
    display: 'flex', justifyContent: 'space-between', padding: '10px 16px',
    fontSize: 10, color: '#334155', borderTop: '1px solid rgba(255,255,255,0.04)',
  },
};
