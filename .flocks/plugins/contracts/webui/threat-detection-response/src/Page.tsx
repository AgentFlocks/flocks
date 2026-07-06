import { useEffect, useState, useRef, useCallback, memo } from 'react';
import { Card } from '@flocks/webui-contract-sdk';

const COLORS = {
  bg: '#0a0e27',
  cardBg: 'rgba(16, 24, 64, 0.75)',
  cardBorder: 'rgba(24, 144, 255, 0.15)',
  red: '#ff4d4f',
  orange: '#fa8c16',
  yellow: '#fadb14',
  blue: '#1890ff',
  green: '#52c41a',
  cyan: '#13c2c2',
  white: '#e8e8e8',
  gray: '#8c8c8c',
  gridLine: 'rgba(255,255,255,0.04)',
};

const CATEGORY_LABELS = ['真实威胁-已成功', '真实威胁-已阻止', '攻击尝试-待观察', '未知', '安全/误报'];
const CATEGORY_COLORS = [COLORS.red, COLORS.orange, COLORS.yellow, COLORS.blue, COLORS.green];
const DEVICES = ['SIP', 'EDR', 'NDR'];

const ATTACK_TACTICS = [
  { id: 'TA0001', name: '初始访问' },
  { id: 'TA0002', name: '执行' },
  { id: 'TA0003', name: '持久化' },
  { id: 'TA0004', name: '提权' },
  { id: 'TA0005', name: '防御规避' },
  { id: 'TA0006', name: '凭证访问' },
  { id: 'TA0007', name: '发现' },
  { id: 'TA0008', name: '横向移动' },
  { id: 'TA0009', name: '收集' },
  { id: 'TA0010', name: '数据渗出' },
  { id: 'TA0011', name: 'C2' },
  { id: 'TA0040', name: '影响' },
];

const TECHNIQUES = ['T1059', 'T1566', 'T1053', 'T1003', 'T1021', 'T1041', 'T1071', 'T1486'];

function rand(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function pick(arr) { return arr[rand(0, arr.length - 1)]; }

function generateDeviceData() {
  return DEVICES.map((name) => {
    const today = rand(10, 200);
    const counts = [rand(0, 5), rand(2, 15), rand(5, 30), rand(1, 8), rand(20, 80)];
    const total = counts.reduce((a, b) => a + b, 0);
    const normalized = counts.map((c) => Math.round((c / total) * today));
    return { name, today, categories: normalized };
  });
}

function generateAttckHeatmap() {
  return ATTACK_TACTICS.map((tactic) => ({
    tactic,
    values: TECHNIQUES.map(() => rand(0, 15)),
  }));
}

function generateAssetRisk() {
  const assets = [
    'srv-web-01', 'db-mysql-03', 'jumpserver', 'pc-zhang-san', 'srv-app-02',
    'vpn-gateway', 'file-server', 'dc-ad-01', 'pc-li-si', 'srv-redis-01'
  ];
  const levels = [COLORS.red, COLORS.orange, COLORS.yellow, COLORS.green];
  return assets.map((name, i) => ({
    name,
    count: rand(5, 120),
    level: levels[Math.min(i < 2 ? 0 : i < 4 ? 1 : i < 6 ? 2 : 3, 3)],
    levelLabel: ['高危', '中危', '低危', '正常'][Math.min(i < 2 ? 0 : i < 4 ? 1 : i < 6 ? 2 : 3, 3)],
  })).sort((a, b) => b.count - a.count);
}

function generateAlerts() {
  const alertTypes = [
    { name: 'Webshell上传检测', device: 'SIP', category: 0, status: '已隔离' },
    { name: '可疑进程启动', device: 'EDR', category: 2, status: '监控中' },
    { name: '暴力破解扫描', device: 'NDR', category: 4, status: '已忽略' },
    { name: 'C2通信检测', device: 'SIP', category: 1, status: '已阻断' },
    { name: '横向移动检测', device: 'EDR', category: 0, status: '已隔离' },
    { name: 'DNS隧道告警', device: 'NDR', category: 2, status: '监控中' },
    { name: 'Mimikatz执行', device: 'EDR', category: 0, status: '已处置' },
    { name: '端口扫描', device: 'NDR', category: 4, status: '已忽略' },
    { name: 'SQL注入检测', device: 'SIP', category: 1, status: '已阻断' },
    { name: '异常登录', device: 'SIP', category: 3, status: '人工研判' },
    { name: '恶意文件下载', device: 'EDR', category: 1, status: '已阻断' },
    { name: '目录遍历', device: 'NDR', category: 4, status: '已忽略' },
    { name: '计划任务创建', device: 'EDR', category: 2, status: '监控中' },
    { name: 'XSS攻击检测', device: 'SIP', category: 1, status: '已阻断' },
    { name: '数据渗出告警', device: 'NDR', category: 0, status: '已隔离' },
  ];
  const assets = ['srv-web-01', 'pc-zhang-san', '10.2.3.5', 'srv-app-02', 'db-mysql-03', 'pc-li-si', 'vpn-gateway', '10.1.8.22', 'srv-web-01', 'jumpserver', 'file-server', '10.3.1.7', 'dc-ad-01', 'srv-app-02', 'srv-web-01'];
  return alertTypes.map((a, i) => ({
    ...a,
    asset: assets[i % assets.length],
    time: `${String(14 - Math.floor(i / 3)).padStart(2, '0')}:${String(rand(0, 59)).padStart(2, '0')}:${String(rand(0, 59)).padStart(2, '0')}`,
    id: `alert-${Date.now()}-${i}`,
  }));
}

function generateIocBubbles() {
  const sources = ['微步在线', 'VirusTotal', 'X-Force', 'AlienVault', '内部情报'];
  const levels = ['核心', '重要', '一般'];
  const bubbles = [];
  for (let i = 0; i < 18; i++) {
    bubbles.push({
      x: rand(10, 90),
      y: rand(8, 88),
      r: rand(6, 32),
      source: pick(sources),
      assetLevel: pick(levels),
      hits: rand(3, 200),
    });
  }
  return bubbles;
}

function KpiCard({ label, value, sub, color, width }: { label: string; value: number; sub: string; color: string; width: string }) {
  return (
    <div style={{ ...styles.kpiCard, width }}>
      <div style={{ color: COLORS.gray, fontSize: 13, marginBottom: 4 }}>{label}</div>
      <div style={{ color, fontSize: 38, fontWeight: 700, fontFamily: 'DIN, Orbitron, monospace', lineHeight: 1.1 }}>{value}</div>
      <div style={{ color: COLORS.gray, fontSize: 11, marginTop: 2 }}>{sub}</div>
    </div>
  );
}

function StackedBar({ data, width, height }: { data: { name: string; today: number; categories: number[] }; width: number; height: number }) {
  const total = data.categories.reduce((a, b) => a + b, 0) || 1;
  const barH = 18;
  const gap = 22;
  const y = 26;
  let x = 0;
  return (
    <div style={{ position: 'relative' }}>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 4, fontWeight: 600 }}>{data.name}<span style={{ color: COLORS.white, fontSize: 22, marginLeft: 10 }}>{data.today}</span></div>
      <svg width={width} height={height}>
        {data.categories.map((v, i) => {
          const w = (v / total) * width;
          const rect = <rect key={i} x={x} y={y} width={Math.max(w, 0.5)} height={barH} fill={CATEGORY_COLORS[i]} rx={i === 0 ? 3 : 0} />;
          x += w;
          return rect;
        })}
        {CATEGORY_LABELS.map((label, i) => {
          const segX = data.categories.slice(0, i).reduce((s, v) => s + (v / total) * width, 0);
          const segW = (data.categories[i] / total) * width;
          if (segW < 30) return null;
          return (
            <text key={`t${i}`} x={segX + segW / 2} y={y + barH / 2 + 1} fill="#fff" fontSize={9} textAnchor="middle" dominantBaseline="middle" fontWeight={600}>
              {data.categories[i]}
            </text>
          );
        })}
      </svg>
      <div style={{ display: 'flex', gap: 10, marginTop: 2, flexWrap: 'wrap' }}>
        {CATEGORY_LABELS.map((l, i) => (
          <span key={i} style={{ fontSize: 9, color: CATEGORY_COLORS[i], whiteSpace: 'nowrap' }}>● {l} {data.categories[i]}</span>
        ))}
      </div>
    </div>
  );
}

function AttckHeatmap({ data }: { data: { tactic: { id: string; name: string }; values: number[] }[] }) {
  const maxVal = Math.max(...data.flatMap((d) => d.values), 1);
  const cellW = 48, cellH = 24, leftW = 72, topH = 22;
  const w = leftW + TECHNIQUES.length * cellW;
  const h = topH + data.length * cellH;
  return (
    <div>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 6, fontWeight: 600 }}>MITRE ATT&CK 战术热力图</div>
      <svg width={w} height={h} style={{ overflow: 'visible' }}>
        {TECHNIQUES.map((t, i) => (
          <text key={t} x={leftW + i * cellW + cellW / 2} y={14} fill={COLORS.gray} fontSize={9} textAnchor="middle">{t}</text>
        ))}
        {data.map((row, ri) => (
          <text key={row.tactic.id} x={leftW - 6} y={topH + ri * cellH + cellH / 2 + 3} fill={COLORS.gray} fontSize={10} textAnchor="end" dominantBaseline="middle">{row.tactic.name}</text>
        ))}
        {data.map((row, ri) =>
          row.values.map((v, ci) => {
            const alpha = v / maxVal;
            const r = Math.round(10 + alpha * 120);
            const g = Math.round(22 + alpha * 50);
            const b = Math.round(40 + alpha * 20);
            return <rect key={`${ri}-${ci}`} x={leftW + ci * cellW + 1} y={topH + ri * cellH + 1} width={cellW - 2} height={cellH - 2} rx={2} fill={`rgb(${r},${g},${b})`} />;
          })
        )}
      </svg>
    </div>
  );
}

function AssetRiskList({ data, width }: { data: { name: string; count: number; level: string; levelLabel: string }[]; width: number }) {
  const maxCount = Math.max(...data.map((d) => d.count), 1);
  return (
    <div>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 8, fontWeight: 600 }}>资产风险榜 Top10</div>
      {data.map((item, i) => (
        <div key={item.name} style={{ display: 'flex', alignItems: 'center', marginBottom: 6, fontSize: 12 }}>
          <span style={{ width: 20, color: COLORS.gray, textAlign: 'right', marginRight: 8 }}>{i + 1}</span>
          <span style={{ width: 110, color: COLORS.white, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</span>
          <div style={{ flex: 1, height: 12, background: 'rgba(255,255,255,0.04)', borderRadius: 6, margin: '0 8px', overflow: 'hidden', position: 'relative' }}>
            <div style={{ width: `${(item.count / maxCount) * 100}%`, height: '100%', background: item.level, borderRadius: 6, transition: 'width 0.6s' }} />
          </div>
          <span style={{ width: 36, textAlign: 'right', color: item.level, fontWeight: 600 }}>{item.count}</span>
        </div>
      ))}
    </div>
  );
}

const AlertStream = memo(({ data }: { data: ReturnType<typeof generateAlerts> }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setOffset((prev) => {
        const container = containerRef.current;
        if (!container) return prev;
        const maxScroll = container.scrollHeight - container.clientHeight;
        const next = prev + 1;
        return next > maxScroll ? 0 : next;
      });
    }, 80);
    return () => clearInterval(interval);
  }, [data]);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = offset;
    }
  }, [offset]);

  return (
    <div>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 8, fontWeight: 600 }}>实时告警流 <span style={{ color: COLORS.gray, fontSize: 11 }}>Top 50</span></div>
      <div ref={containerRef} style={{ height: 260, overflow: 'hidden', fontSize: 11 }}>
        {[...data, ...data].map((alert, i) => (
          <div key={alert.id + i} style={{ display: 'flex', alignItems: 'center', padding: '5px 8px', borderBottom: `1px solid ${COLORS.gridLine}`, gap: 8 }}>
            <span style={{ color: COLORS.gray, width: 40, flexShrink: 0 }}>{alert.time}</span>
            <span style={{ color: COLORS.blue, width: 28, fontWeight: 600, flexShrink: 0 }}>{alert.device}</span>
            <span style={{ color: COLORS.white, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{alert.name}</span>
            <span style={{ color: COLORS.gray, width: 80, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{alert.asset}</span>
            <span style={{ color: CATEGORY_COLORS[alert.category], width: 90, flexShrink: 0, fontWeight: 600, fontSize: 10 }}>
              {CATEGORY_LABELS[alert.category]}
            </span>
            <span style={{ color: alert.category === 0 ? COLORS.red : alert.category === 1 ? COLORS.orange : COLORS.gray, width: 48, flexShrink: 0, textAlign: 'right' }}>{alert.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
});

function RingChart({ autoIgnore, autoAction, manual, size }: { autoIgnore: number; autoAction: number; manual: number; size: number }) {
  const total = autoIgnore + autoAction + manual || 1;
  const cx = size / 2, cy = size / 2, r = size / 2 - 12, strokeW = 16;
  const circumference = 2 * Math.PI * r;
  const segments = [
    { val: autoIgnore, color: COLORS.green },
    { val: autoAction, color: COLORS.orange },
    { val: manual, color: COLORS.blue },
  ];
  let offset = -Math.PI / 2;
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 6, fontWeight: 600 }}>自动化覆盖率</div>
      <svg width={size} height={size}>
        {segments.map((seg, i) => {
          const pct = seg.val / total;
          const dashLen = circumference * pct;
          const dashGap = circumference - dashLen;
          const rot = (offset / (2 * Math.PI)) * 360;
          offset += 2 * Math.PI * pct;
          return <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={seg.color} strokeWidth={strokeW} strokeDasharray={`${dashLen} ${dashGap}`} strokeLinecap="butt" transform={`rotate(${rot} ${cx} ${cy})`} />;
        })}
        <text x={cx} y={cy - 10} textAnchor="middle" fill={COLORS.cyan} fontSize={30} fontWeight={700} fontFamily="DIN,Orbitron,monospace">{Math.round((autoIgnore + autoAction) / total * 100)}%</text>
        <text x={cx} y={cy + 16} textAnchor="middle" fill={COLORS.gray} fontSize={11}>自动化率</text>
      </svg>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 14, marginTop: 4 }}>
        {[
          { label: '自动忽略', val: autoIgnore, color: COLORS.green },
          { label: '自动联动', val: autoAction, color: COLORS.orange },
          { label: '人工处置', val: manual, color: COLORS.blue },
        ].map((item) => (
          <div key={item.label} style={{ textAlign: 'center' }}>
            <div style={{ color: item.color, fontSize: 18, fontWeight: 700, fontFamily: 'DIN,Orbitron,monospace' }}>{item.val}</div>
            <div style={{ color: COLORS.gray, fontSize: 10 }}>{item.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function IocBubbleChart({ data, width, height }: { data: ReturnType<typeof generateIocBubbles>; width: number; height: number }) {
  const sourceColors: Record<string, string> = {
    '微步在线': COLORS.cyan,
    'VirusTotal': COLORS.orange,
    'X-Force': COLORS.blue,
    'AlienVault': COLORS.green,
    '内部情报': COLORS.red,
  };
  const opacityByLevel: Record<string, number> = { '核心': 0.9, '重要': 0.65, '一般': 0.4 };
  return (
    <div>
      <div style={{ color: COLORS.cyan, fontSize: 13, marginBottom: 6, fontWeight: 600 }}>IOC 命中分布</div>
      <svg width={width} height={height}>
        {data.map((b, i) => (
          <circle key={i} cx={`${b.x}%`} cy={`${b.y}%`} r={b.r} fill={sourceColors[b.source] || COLORS.gray} opacity={opacityByLevel[b.assetLevel] || 0.5}>
            <title>{b.source} | {b.assetLevel}资产 | 命中{b.hits}次</title>
          </circle>
        ))}
      </svg>
      <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 4 }}>
        {Object.entries(sourceColors).map(([name, color]) => (
          <span key={name} style={{ fontSize: 10, color }}>● {name}</span>
        ))}
      </div>
    </div>
  );
}

function GaugeChart({ value, max, label, color }: { value: number; max: number; color: string }) {
  const pct = Math.min(value / max, 1);
  const angle = pct * 180;
  const r = 48, cx = 50, cy = 52;
  const rad = (angle - 180) * (Math.PI / 180);
  const needleX = cx + r * Math.cos(rad);
  const needleY = cy + r * Math.sin(rad);
  return (
    <div style={{ textAlign: 'center' }}>
      <svg width={100} height={70} viewBox="0 0 100 70">
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={8} />
        <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${needleX} ${needleY}`} fill="none" stroke={color} strokeWidth={8} strokeLinecap="round" />
        <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke={COLORS.white} strokeWidth={1.5} />
        <circle cx={cx} cy={cy} r={3} fill={COLORS.white} />
      </svg>
      <div style={{ color, fontSize: 22, fontWeight: 700, fontFamily: 'DIN,Orbitron,monospace', marginTop: -14 }}>{value}<span style={{ fontSize: 13, color: COLORS.gray }}>min</span></div>
      <div style={{ color: COLORS.gray, fontSize: 10 }}>{label}</div>
    </div>
  );
}

export default function Page() {
  const [ready, setReady] = useState(false);
  const [deviceData, setDeviceData] = useState(generateDeviceData);
  const [attckData, setAttckData] = useState(generateAttckHeatmap);
  const [assetRisk, setAssetRisk] = useState(generateAssetRisk);
  const [alerts, setAlerts] = useState(generateAlerts);
  const [iocBubbles, setIocBubbles] = useState(generateIocBubbles);
  const [now, setNow] = useState(new Date());
  const [blinking, setBlinking] = useState(true);

  const refresh = useCallback(() => {
    setDeviceData(generateDeviceData());
    setAttckData(generateAttckHeatmap());
    setAssetRisk(generateAssetRisk());
    setAlerts(generateAlerts());
    setIocBubbles(generateIocBubbles());
    setNow(new Date());
  }, []);

  useEffect(() => {
    setReady(true);
    const timer = setInterval(refresh, 30000);
    const blink = setInterval(() => setBlinking((b) => !b), 1000);
    return () => { clearInterval(timer); clearInterval(blink); };
  }, [refresh]);

  const totalEvents = deviceData.reduce((s, d) => s + d.today, 0);
  const totalCategories = deviceData.reduce((acc, d) => {
    d.categories.forEach((v, i) => { acc[i] = (acc[i] || 0) + v; });
    return acc;
  }, [0, 0, 0, 0, 0]);
  const autoIgnore = Math.round(totalEvents * 0.58);
  const autoAction = Math.round(totalEvents * 0.14);
  const manual = totalEvents - autoIgnore - autoAction;

  const timeStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;

  const totalThreats = totalCategories[0] + totalCategories[1];

  if (!ready) return <div style={{ color: COLORS.gray, padding: 40 }}>加载中...</div>;

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: COLORS.red, fontSize: 22 }}>🛡</span>
          <span style={{ color: COLORS.white, fontSize: 24, fontWeight: 700, letterSpacing: 2 }}>威胁检测响应态势</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ ...styles.blinkDot, opacity: blinking ? 1 : 0.3 }} />
            <span style={{ color: COLORS.green, fontSize: 12 }}>运行中</span>
          </div>
          <span style={{ color: COLORS.gray, fontSize: 13 }}>{timeStr}</span>
          <span style={{ color: COLORS.gray, fontSize: 11 }}>每30s刷新</span>
        </div>
      </div>

      <div style={styles.grid}>

        <div style={styles.module}>
          <StackedBar data={deviceData[0]} width={280} height={80} />
        </div>
        <div style={styles.module}>
          <StackedBar data={deviceData[1]} width={280} height={80} />
        </div>
        <div style={styles.module}>
          <StackedBar data={deviceData[2]} width={280} height={80} />
        </div>

        <div style={styles.module}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ color: COLORS.gray, fontSize: 13, marginBottom: 4 }}>今日总事件</div>
            <div style={{ color: COLORS.cyan, fontSize: 48, fontWeight: 700, fontFamily: 'DIN,Orbitron,monospace', lineHeight: 1.1 }}>{totalEvents}</div>
            <div style={{ color: COLORS.gray, fontSize: 11, marginTop: 2 }}>
              真实威胁 <span style={{ color: COLORS.red, fontWeight: 600 }}>{totalThreats}</span>
              <span style={{ margin: '0 8px' }}>|</span>
              待处置 <span style={{ color: COLORS.yellow, fontWeight: 600 }}>{manual}</span>
            </div>
          </div>
        </div>

        <div style={styles.module}>
          <RingChart autoIgnore={autoIgnore} autoAction={autoAction} manual={manual} size={170} />
        </div>

        <div style={{ ...styles.module, gridColumn: 'span 2' }}>
          <AttckHeatmap data={attckData} />
        </div>

        <div style={styles.module}>
          <GaugeChart value={4.2} max={10} label="5min研判完成率 94%" color={COLORS.green} />
          <div style={{ marginTop: 16 }}>
            <GaugeChart value={18} max={30} label="24h平均处置时长" color={COLORS.orange} />
          </div>
        </div>

        <div style={styles.module}>
          <AssetRiskList data={assetRisk} width={300} />
        </div>

        <div style={{ ...styles.module, gridColumn: 'span 2', overflow: 'hidden' }}>
          <AlertStream data={alerts} />
        </div>

        <div style={styles.module}>
          <IocBubbleChart data={iocBubbles} width={300} height={180} />
        </div>

        <div style={{ ...styles.module, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: 12 }}>
          <div style={{ color: COLORS.cyan, fontSize: 13, fontWeight: 600 }}>攻击源地理分布</div>
          <div style={{ position: 'relative', width: 260, height: 180 }}>
            <svg width={260} height={180} viewBox="0 0 260 180">
              <rect x={30} y={20} width={200} height={140} rx={6} fill="rgba(255,255,255,0.02)" stroke="rgba(255,255,255,0.06)" />
              <text x={130} y={90} fill={COLORS.gray} fontSize={13} textAnchor="middle">中国地图</text>
              <text x={130} y={108} fill={COLORS.gray} fontSize={10} textAnchor="middle">省份热力 + 海外飞线</text>
            </svg>
          </div>
          <div style={{ fontSize: 10, color: COLORS.gray }}>Top 攻击源: 美国 43% | 俄罗斯 18% | 越南 12% | 印度 9%</div>
        </div>

      </div>
    </div>
  );
}

const styles: Record<string, any> = {
  wrapper: {
    minHeight: '100vh',
    background: COLORS.bg,
    padding: 16,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Microsoft YaHei", "Segoe UI", sans-serif',
    color: COLORS.white,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 20px',
    marginBottom: 14,
    background: COLORS.cardBg,
    borderRadius: 8,
    border: `1px solid ${COLORS.cardBorder}`,
    backdropFilter: 'blur(8px)',
  },
  blinkDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: COLORS.green,
    boxShadow: `0 0 6px ${COLORS.green}`,
    transition: 'opacity 0.4s',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, 1fr)',
    gap: 12,
  },
  module: {
    background: COLORS.cardBg,
    borderRadius: 8,
    border: `1px solid ${COLORS.cardBorder}`,
    padding: 14,
    backdropFilter: 'blur(8px)',
    minHeight: 120,
  },
  kpiCard: {
    background: COLORS.cardBg,
    borderRadius: 8,
    border: `1px solid ${COLORS.cardBorder}`,
    padding: '12px 16px',
    backdropFilter: 'blur(8px)',
  },
};
