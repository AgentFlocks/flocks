import { useEffect, useState } from 'react';
import { Card } from '@flocks/webui-contract-sdk';

const MOCK = {
  kpi: {
    totalPulled: 12847,
    totalPulledDelta: 12.3,
    internalHits: 1246,
    internalHitsDelta: 8.7,
    blocked: 892,
    blockedDelta: 15.2,
    pending: 354,
    pendingDelta: -3.1,
    autoBlockRate: 71.5,
  },
  iocTypes: [
    { type: 'Domain', count: 4201, color: '#40c4ff' },
    { type: 'IP', count: 3102, color: '#00e676' },
    { type: 'URL', count: 2844, color: '#ffd740' },
    { type: 'Hash', count: 2700, color: '#ff9100' },
  ],
  sourceRanking: [
    { name: '银狐 IOC', count: 3847, highConf: 2103 },
    { name: 'HRTI 态势情报', count: 3102, highConf: 1847 },
    { name: 'ThreatBook', count: 2547, highConf: 1203 },
    { name: 'VirusTotal', count: 1892, highConf: 674 },
    { name: '勒索软件监控', count: 1459, highConf: 523 },
  ],
  confidence: {
    high: 2847,
    medium: 5103,
    low: 4897,
  },
  blockStream: [
    { time: '14:30:12', asset: '192.168.1.5', action: '防火墙封禁', type: 'block', target: '45.33.32.156' },
    { time: '14:30:08', asset: 'DNS 防火墙', action: '域名阻断', type: 'block', target: 'evil-c2.xyz' },
    { time: '14:30:03', asset: 'web-prod-01', action: '命中内部', type: 'hit', target: '103.224.182.253' },
    { time: '14:29:55', asset: 'EDR 终端', action: '进程Hash拦截', type: 'block', target: 'a3f2b9c1d4e5' },
    { time: '14:29:41', asset: 'db-master', action: '命中内部', type: 'hit', target: 'malware-cdn.net' },
    { time: '14:29:33', asset: '零信任网关', action: '身份封禁', type: 'block', target: 'user_suspect_042' },
    { time: '14:29:18', asset: 'app-node-03', action: '命中内部', type: 'hit', target: '198.51.100.42' },
    { time: '14:29:02', asset: '防火墙', action: 'URL封禁', type: 'block', target: 'phish.badactor.io' },
    { time: '14:28:47', asset: 'mail-gw-01', action: '命中内部', type: 'hit', target: 'spam-relay.cc' },
    { time: '14:28:31', asset: 'DNS 防火墙', action: '域名阻断', type: 'block', target: 'ransom-c2.onion' },
    { time: '14:28:15', asset: 'vpn-node-02', action: '命中内部', type: 'hit', target: '185.220.101.34' },
    { time: '14:28:00', asset: 'EDR 终端', action: '进程Hash拦截', type: 'block', target: 'f7e8d9c0b1a2' },
  ],
  affectedAssets: [
    { name: 'web-prod-01', hits: 142, level: 'high' },
    { name: 'db-master', hits: 98, level: 'high' },
    { name: 'app-node-03', hits: 67, level: 'medium' },
    { name: 'mail-gw-01', hits: 54, level: 'medium' },
    { name: 'vpn-node-02', hits: 43, level: 'medium' },
    { name: 'file-srv-01', hits: 38, level: 'low' },
    { name: 'ad-dc-01', hits: 31, level: 'low' },
    { name: 'monitor-01', hits: 25, level: 'low' },
    { name: 'jump-srv-01', hits: 19, level: 'low' },
    { name: 'log-srv-02', hits: 15, level: 'low' },
  ],
  decay: [100, 82, 61, 45, 32, 22, 15, 10, 7, 5, 3, 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  strategy: {
    firewall: 412,
    zeroTrust: 198,
    dnsFirewall: 337,
    edr: 245,
    multiLayerRate: 94.3,
  },
};

const BG = '#0a0f1e';
const CARD_BG = '#0d1326';
const BORDER = '#1a2340';
const TEXT_PRIMARY = '#e0e6f0';
const TEXT_SECONDARY = '#8892a8';
const GREEN = '#00e676';
const YELLOW = '#ffd740';
const ORANGE = '#ff9100';
const RED = '#ff1744';
const BLUE = '#40c4ff';

function DeltaBadge({ delta }: { delta: number }) {
  const isUp = delta > 0;
  return (
    <span style={{ color: isUp ? (delta > 10 ? RED : ORANGE) : GREEN, fontSize: 13, marginLeft: 6 }}>
      {isUp ? '↑' : '↓'}{Math.abs(delta)}%
    </span>
  );
}

function levelColor(level: string) {
  switch (level) {
    case 'high': return RED;
    case 'medium': return ORANGE;
    case 'low': return YELLOW;
    default: return TEXT_SECONDARY;
  }
}

function levelLabel(level: string) {
  switch (level) {
    case 'high': return '高';
    case 'medium': return '中';
    case 'low': return '低';
    default: return level;
  }
}

export default function Page() {
  const [time, setTime] = useState('');
  const [streamIdx, setStreamIdx] = useState(0);

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setTime(
        now.getFullYear() + '-' +
        String(now.getMonth() + 1).padStart(2, '0') + '-' +
        String(now.getDate()).padStart(2, '0') + ' ' +
        String(now.getHours()).padStart(2, '0') + ':' +
        String(now.getMinutes()).padStart(2, '0') + ':' +
        String(now.getSeconds()).padStart(2, '0')
      );
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      setStreamIdx(prev => (prev + 1) % MOCK.blockStream.length);
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const maxIoc = Math.max(...MOCK.iocTypes.map(i => i.count));
  const maxSource = Math.max(...MOCK.sourceRanking.map(s => s.count));
  const totalConf = MOCK.confidence.high + MOCK.confidence.medium + MOCK.confidence.low;
  const maxAssetHits = MOCK.affectedAssets[0]?.hits ?? 1;
  const maxStrat = Math.max(MOCK.strategy.firewall, MOCK.strategy.zeroTrust, MOCK.strategy.dnsFirewall, MOCK.strategy.edr);

  const cardStyle: React.CSSProperties = {
    background: CARD_BG,
    border: `1px solid ${BORDER}`,
    borderRadius: 8,
    padding: '16px 20px',
  };

  const sectionTitle: React.CSSProperties = {
    color: TEXT_SECONDARY,
    fontSize: 12,
    textTransform: 'uppercase' as const,
    letterSpacing: 1.5,
    marginBottom: 12,
    fontWeight: 600,
  };

  return (
    <div style={{ background: BG, minHeight: '100vh', color: TEXT_PRIMARY, fontFamily: 'system-ui, -apple-system, sans-serif' }}>

      {/* ====== HEADER ====== */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 28px 0', borderBottom: `1px solid ${BORDER}`, paddingBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ width: 32, height: 32, borderRadius: '50%', background: `linear-gradient(135deg, ${BLUE}, ${GREEN})`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>
            🛡
          </div>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: 2 }}>威胁情报运营态势</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: TEXT_SECONDARY, fontSize: 14 }}>{time}</span>
          <span style={{ color: GREEN, fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: GREEN, display: 'inline-block' }} />
            实时监控中
          </span>
        </div>
      </div>

      {/* ====== KPI ROW ====== */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 14, padding: '16px 28px' }}>
        {[
          { label: '今日拉取 IOC 总数', value: MOCK.kpi.totalPulled.toLocaleString(), delta: MOCK.kpi.totalPulledDelta, color: BLUE },
          { label: '命中内部资产', value: MOCK.kpi.internalHits.toLocaleString(), delta: MOCK.kpi.internalHitsDelta, color: ORANGE },
          { label: '已执行封禁', value: MOCK.kpi.blocked.toLocaleString(), delta: MOCK.kpi.blockedDelta, color: GREEN },
          { label: '待处置', value: MOCK.kpi.pending.toLocaleString(), delta: MOCK.kpi.pendingDelta, color: YELLOW },
          { label: '自动化封禁率', value: MOCK.kpi.autoBlockRate + '%', delta: 0, color: GREEN },
        ].map((kpi, i) => (
          <div key={i} style={{ ...cardStyle, borderLeft: `3px solid ${kpi.color}`, position: 'relative', overflow: 'hidden' }}>
            <div style={{ color: TEXT_SECONDARY, fontSize: 11, marginBottom: 6 }}>{kpi.label}</div>
            <div style={{ display: 'flex', alignItems: 'baseline' }}>
              <span style={{ fontSize: 28, fontWeight: 800, fontFamily: "'SF Mono', 'Fira Code', monospace", color: kpi.color }}>
                {kpi.value}
              </span>
              {kpi.delta !== 0 && <DeltaBadge delta={kpi.delta} />}
            </div>
            <div style={{ position: 'absolute', top: 0, right: 0, width: 60, height: 60, borderRadius: '50%', background: kpi.color, opacity: 0.04, transform: 'translate(20px, -20px)' }} />
          </div>
        ))}
      </div>

      {/* ====== MAIN CONTENT GRID ====== */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, padding: '0 28px 16px' }}>

        {/* IOC 类型分布 */}
        <div style={cardStyle}>
          <div style={sectionTitle}>IOC 类型分布 (24h)</div>
          {MOCK.iocTypes.map((item, i) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12 }}>
                <span style={{ color: TEXT_PRIMARY }}>{item.type}</span>
                <span style={{ color: item.color, fontFamily: "'SF Mono', 'Fira Code', monospace" }}>{item.count.toLocaleString()}</span>
              </div>
              <div style={{ height: 8, background: '#1a2340', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(item.count / maxIoc) * 100}%`, background: item.color, borderRadius: 4, transition: 'width 1s ease' }} />
              </div>
            </div>
          ))}
          <div style={{ textAlign: 'center', marginTop: 8 }}>
            <span style={{ fontSize: 11, color: TEXT_SECONDARY }}>
              合计 {MOCK.iocTypes.reduce((s, i) => s + i.count, 0).toLocaleString()} 条 IOC
            </span>
          </div>
        </div>

        {/* 情报贡献排行 */}
        <div style={cardStyle}>
          <div style={sectionTitle}>情报源贡献排行</div>
          {MOCK.sourceRanking.map((item, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 12 }}>
                <span style={{ color: TEXT_PRIMARY }}>{item.name}</span>
                <span style={{ color: BLUE, fontFamily: "'SF Mono', 'Fira Code', monospace", fontSize: 11 }}>
                  高置信 {item.highConf.toLocaleString()}
                </span>
              </div>
              <div style={{ height: 6, background: '#1a2340', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(item.count / maxSource) * 100}%`, background: `linear-gradient(90deg, ${BLUE}, ${GREEN})`, borderRadius: 3 }} />
              </div>
            </div>
          ))}
        </div>

        {/* 置信度分布 */}
        <div style={cardStyle}>
          <div style={sectionTitle}>情报置信度分布</div>
          <div style={{ display: 'flex', justifyContent: 'space-around', alignItems: 'flex-end', height: 130, paddingTop: 8 }}>
            {[
              { label: '高置信', value: MOCK.confidence.high, color: RED },
              { label: '中置信', value: MOCK.confidence.medium, color: ORANGE },
              { label: '低置信', value: MOCK.confidence.low, color: YELLOW },
            ].map((item, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: "'SF Mono', 'Fira Code', monospace", fontSize: 18, fontWeight: 700, color: item.color }}>
                  {item.value.toLocaleString()}
                </span>
                <div style={{ width: 48, height: `${(item.value / totalConf) * 100}%`, maxHeight: 80, minHeight: 20, background: item.color, borderRadius: '4px 4px 0 0', opacity: 0.8 }} />
                <span style={{ fontSize: 11, color: TEXT_SECONDARY }}>
                  {item.label} ({((item.value / totalConf) * 100).toFixed(1)}%)
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ====== SECOND ROW ====== */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, padding: '0 28px 16px' }}>

        {/* 实时封禁流水 */}
        <div style={cardStyle}>
          <div style={{ ...sectionTitle, display: 'flex', justifyContent: 'space-between' }}>
            <span>实时封禁流水</span>
            <span style={{ color: GREEN, fontSize: 10, letterSpacing: 0 }}>LIVE</span>
          </div>
          <div style={{ maxHeight: 260, overflow: 'hidden', position: 'relative' }}>
            {MOCK.blockStream.map((item, i) => {
              const isVisible = i >= streamIdx && i < streamIdx + 8;
              const visibleIdx = i >= streamIdx ? i - streamIdx : -1;
              return (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '6px 0',
                    borderBottom: `1px solid ${BORDER}`,
                    opacity: isVisible ? 1 - visibleIdx * 0.12 : 0,
                    transform: isVisible ? 'translateY(0)' : 'translateY(-10px)',
                    transition: 'all 0.5s ease',
                    fontSize: 12,
                  }}
                >
                  <span style={{ color: TEXT_SECONDARY, fontFamily: "'SF Mono', 'Fira Code', monospace", fontSize: 11, minWidth: 60 }}>
                    {item.time}
                  </span>
                  <span style={{ color: item.type === 'block' ? GREEN : ORANGE, minWidth: 80, fontWeight: 600 }}>
                    {item.action}
                  </span>
                  <span style={{ color: TEXT_PRIMARY, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
                    {item.target}
                  </span>
                  <span style={{
                    color: item.type === 'block' ? GREEN : ORANGE,
                    fontSize: 10,
                    padding: '1px 6px',
                    borderRadius: 8,
                    border: `1px solid ${item.type === 'block' ? GREEN : ORANGE}`,
                    opacity: 0.7,
                  }}>
                    {item.type === 'block' ? '已封禁' : '待处置'}
                  </span>
                </div>
              );
            })}
          </div>
          <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 40, background: 'linear-gradient(transparent, #0d1326)', pointerEvents: 'none' }} />
        </div>

        {/* 受影响资产 Top10 */}
        <div style={{ ...cardStyle }}>
          <div style={sectionTitle}>受影响资产 Top10</div>
          {MOCK.affectedAssets.map((item, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3, fontSize: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: TEXT_SECONDARY, fontSize: 11 }}>#{i + 1}</span>
                  <span style={{ color: TEXT_PRIMARY }}>{item.name}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontFamily: "'SF Mono', 'Fira Code', monospace", fontSize: 13, fontWeight: 600, color: levelColor(item.level) }}>
                    {item.hits}
                  </span>
                  <span style={{
                    fontSize: 10,
                    color: levelColor(item.level),
                    padding: '1px 6px',
                    borderRadius: 4,
                    border: `1px solid ${levelColor(item.level)}`,
                  }}>
                    {levelLabel(item.level)}危
                  </span>
                </div>
              </div>
              <div style={{ height: 4, background: '#1a2340', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(item.hits / maxAssetHits) * 100}%`, background: levelColor(item.level), borderRadius: 2, opacity: 0.7 }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ====== THIRD ROW: Decay Curve ====== */}
      <div style={{ padding: '0 28px 16px' }}>
        <div style={cardStyle}>
          <div style={sectionTitle}>封禁效果衰减曲线（封禁后 24h 命中次数）</div>
          <svg viewBox="0 0 800 140" style={{ width: '100%', height: 140 }}>
            {/* Grid lines */}
            {[0, 25, 50, 75, 100].map(y => (
              <line key={`g${y}`} x1={40} y1={y + 20} x2={780} y2={y + 20} stroke="#1a2340" strokeWidth={0.5} />
            ))}
            {/* Y axis labels */}
            {[100, 75, 50, 25, 0].map((v, i) => (
              <text key={`y${i}`} x={32} y={i * 25 + 24} fill={TEXT_SECONDARY} fontSize={10} textAnchor="end">
                {v}%
              </text>
            ))}
            {/* Decay line */}
            <polyline
              points={MOCK.decay.map((v, i) => `${40 + (i / (MOCK.decay.length - 1)) * 740},${20 + 100 - v}`).join(' ')}
              fill="none"
              stroke={BLUE}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {/* Area fill */}
            <polygon
              points={`40,120 ${MOCK.decay.map((v, i) => `${40 + (i / (MOCK.decay.length - 1)) * 740},${20 + 100 - v}`).join(' ')} 780,120`}
              fill="url(#decayGrad)"
              opacity={0.3}
            />
            <defs>
              <linearGradient id="decayGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={BLUE} stopOpacity={0.6} />
                <stop offset="100%" stopColor={BLUE} stopOpacity={0} />
              </linearGradient>
            </defs>
            {/* X axis labels */}
            {['T0', 'T4', 'T8', 'T12', 'T16', 'T20', 'T24'].map((label, i) => (
              <text key={`x${i}`} x={40 + i * 123.33} y={135} fill={TEXT_SECONDARY} fontSize={10} textAnchor="middle">
                {label}h
              </text>
            ))}
            {/* Data points */}
            {[0, 1, 2, 4, 6, 12, 24].map(t => {
              const v = MOCK.decay[t] ?? 0;
              return (
                <circle
                  key={`dot${t}`}
                  cx={40 + (t / 24) * 740}
                  cy={20 + 100 - v}
                  r={3}
                  fill={BLUE}
                  stroke={CARD_BG}
                  strokeWidth={1}
                />
              );
            })}
          </svg>
        </div>
      </div>

      {/* ====== BOTTOM: Strategy Status ====== */}
      <div style={{ padding: '0 28px 24px' }}>
        <div style={cardStyle}>
          <div style={sectionTitle}>封禁策略执行状态</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 20 }}>
            {[
              { label: '防火墙封禁', value: MOCK.strategy.firewall, color: RED },
              { label: '零信任封禁', value: MOCK.strategy.zeroTrust, color: ORANGE },
              { label: 'DNS 防火墙', value: MOCK.strategy.dnsFirewall, color: BLUE },
              { label: 'EDR 拦截', value: MOCK.strategy.edr, color: GREEN },
            ].map((item, i) => (
              <div key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 12 }}>
                  <span style={{ color: TEXT_PRIMARY }}>{item.label}</span>
                  <span style={{ color: item.color, fontFamily: "'SF Mono', 'Fira Code', monospace", fontWeight: 600 }}>
                    {item.value}
                  </span>
                </div>
                <div style={{ height: 8, background: '#1a2340', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${(item.value / maxStrat) * 100}%`,
                    background: item.color,
                    borderRadius: 4,
                    transition: 'width 1.2s ease',
                  }} />
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: 16, gap: 24 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 800, fontFamily: "'SF Mono', 'Fira Code', monospace", color: GREEN }}>
                {MOCK.strategy.multiLayerRate}%
              </div>
              <div style={{ fontSize: 11, color: TEXT_SECONDARY }}>多层叠加率</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 800, fontFamily: "'SF Mono', 'Fira Code', monospace", color: BLUE }}>
                &lt;30s
              </div>
              <div style={{ fontSize: 11, color: TEXT_SECONDARY }}>平均封禁延迟</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 800, fontFamily: "'SF Mono', 'Fira Code', monospace", color: GREEN }}>
                99.7%
              </div>
              <div style={{ fontSize: 11, color: TEXT_SECONDARY }}>封禁成功率</div>
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
