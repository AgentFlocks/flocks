import { useState, useEffect, useCallback } from 'react';

const API = '/api/contracts/webui/pages/cross-device-investigation/api';

function Icon({ n, s }: { n: string; s?: string }) {
  const c = s || 'w-4 h-4';
  const paths: Record<string, string> = {
    shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
    alert: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
    check: 'M20 6 9 17l-5-5',
    activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
    target: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4z',
    clock: 'M12 6v6l4 2M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z',
    server: 'M5 2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM5 14h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2z',
    barChart: 'M18 20V10M12 20V4M4 20v-4',
    zap: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
    search: 'M21 21l-4.35-4.35M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z',
    layers: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  };
  return <svg className={c} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={paths[n] || ''} /></svg>;
}

const SEV_L: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危' };

export default function Page() {
  const [d, setD] = useState<any>(null);
  const [cases, setCases] = useState<any[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [selCase, setSelCase] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [dtLoad, setDtLoad] = useState(false);
  const [view, setView] = useState<'dashboard' | 'cases'>('dashboard');
  const [ts, setTs] = useState('');

  const fetchAll = useCallback(async () => {
    try {
      const [dr, cr] = await Promise.all([fetch(`${API}/dashboard`), fetch(`${API}/cases`)]);
      setD(await dr.json()); setCases((await cr.json()).cases || []);
      setTs(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
    } catch {} finally { setLoading(false); }
  }, []);
  useEffect(() => { fetchAll(); const i = setInterval(() => setTs(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })), 30000); return () => clearInterval(i); }, [fetchAll]);

  const selectCase = async (id: string) => {
    setSelId(id); setDtLoad(true); setSelCase(null);
    try { const r = await fetch(`${API}/cases/${encodeURIComponent(id)}`); const data = await r.json(); if (data.ok) setSelCase(data.case); } catch {} finally { setDtLoad(false); }
  };

  if (loading) return <div className="flex justify-center py-32"><div className="animate-spin w-6 h-6 border-2 border-indigo-400/30 border-t-indigo-400 rounded-full" /></div>;

  return (
    <div className="flex flex-col gap-3 p-4 min-h-screen" style={{ background: '#070c17' }}>
      {/* Header */}
      <header className="flex items-center justify-between px-1">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/25">
            <Icon n="layers" s="w-4.5 h-4.5 text-white" />
          </div>
          <div>
            <h1 className="text-[15px] font-bold text-white tracking-tight leading-none">跨设备安全调查大屏</h1>
            <p className="text-[10px] text-slate-500 mt-0.5">Cross-Device Investigation Command Center</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center bg-slate-800/80 rounded-lg p-0.5 border border-slate-700/80">
            {(['dashboard','cases'] as const).map(k => (
              <button key={k} onClick={() => { setView(k); setSelId(null); setSelCase(null); }}
                className={`px-3.5 py-1.5 rounded-md text-[11px] font-semibold transition-all ${view===k ? 'bg-slate-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-300'}`}>
                {k==='dashboard' ? '态势大屏' : '案件管理'}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/25">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_6px_rgba(52,211,153,0.5)]" />
            <span className="text-[11px] text-emerald-400 font-semibold">{ts}</span>
          </div>
        </div>
      </header>

      {view === 'dashboard' && d && <DashboardView d={d} />}
      {view === 'cases' && <CasesView cases={cases} selId={selId} selCase={selCase} dtLoad={dtLoad} onSelect={selectCase} />}
    </div>
  );
}

/* ================================================
   DASHBOARD
   ================================================ */
function DashboardView({ d }: { d: any }) {
  return (
    <div className="flex flex-col gap-3 flex-1">
      {/* Row 1 – Metric Cards */}
      <div className="grid grid-cols-5 gap-3">
        <MetricCard
          label="安全评分" value={d.securityScore} unit="/100"
          color={d.securityScore >= 80 ? '#10b981' : d.securityScore >= 60 ? '#f59e0b' : '#ef4444'}
          icon="shield" sub={`较昨日 +3`}
        />
        <MetricCard
          label="今日告警" value={d.alertsToday?.total} unit="条"
          color="#f1f5f9" icon="alert"
          sub={[`${d.alertsToday?.critical}`, `${d.alertsToday?.high}`, `${d.alertsToday?.medium}`]}
          subColors={['#f43f5e','#f59e0b','#eab308']}
          subLabels={['严重','高危','中危']}
        />
        <MetricCard
          label="进行中" value={d.investigations?.active} unit={`件  /${d.investigations?.resolved}已处置`}
          color="#f59e0b" icon="activity"
        />
        <MetricCard
          label="MTTR" value={d.mttr?.value + d.mttr?.unit} unit="平均响应"
          color="#10b981" icon="clock" sub="↓ 较上周 -12%"
        />
        <MetricCard
          label="MTTD" value={d.mttd?.value + d.mttd?.unit} unit="平均检测"
          color="#818cf8" icon="target" sub="↓ 较上周 -8%"
        />
      </div>

      {/* Row 2 – Kill Chain (60%) + Active Cases (40%) */}
      <div className="grid grid-cols-[3fr_2fr] gap-3">
        <KillChainPanel data={d.killChain} />
        <ActivePanel cases={d.activeCases} />
      </div>

      {/* Row 3 – Devices + Targets + IOC */}
      <div className="grid grid-cols-3 gap-3">
        <DevicePanel devices={d.deviceHealth} />
        <TargetPanel targets={d.hotTargets} />
        <IOCPanel iocs={d.recentIocs} />
      </div>

      {/* Row 4 – Timeline */}
      <TimelinePanel events={d.timeline} />
    </div>
  );
}

/* ---------- Metric Card ---------- */
function MetricCard({ label, value, unit, color, icon, sub, subColors, subLabels }: any) {
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 flex items-center gap-3 shadow-sm">
      <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
        <Icon n={icon} s="w-4.5 h-4.5" />
      </div>
      <div className="min-w-0">
        <div className="text-[10px] text-slate-400 uppercase tracking-wider font-medium mb-0.5">{label}</div>
        <div className="flex items-baseline gap-1">
          <span className="text-xl font-extrabold text-white tabular-nums tracking-tight">{value}</span>
          <span className="text-[11px] text-slate-500">{unit}</span>
        </div>
        {sub && !subColors && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
        {subColors && (
          <div className="flex items-center gap-2 mt-1">
            {(sub as string[]).map((s, i) => (
              <span key={i} className="text-[10px] font-semibold flex items-center gap-0.5" style={{ color: (subColors as string[])[i] }}>
                <span className="w-1 h-1 rounded-full flex-shrink-0" style={{ backgroundColor: (subColors as string[])[i] }} />{s}<span className="text-slate-500 font-normal ml-0.5">{subLabels[i]}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- Kill Chain ---------- */
function KillChainPanel({ data }: { data: any[] }) {
  const phases = data || [];
  const max = Math.max(...phases.map(p => p.blocked || 0), 1);
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[12px] font-bold text-slate-200 flex items-center gap-2">
          <Icon n="activity" s="w-3.5 h-3.5 text-rose-400" />攻击链路态势
        </h3>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="flex items-center gap-1 text-slate-400"><span className="w-2 h-2 rounded-sm bg-rose-500/80" />活跃</span>
          <span className="flex items-center gap-1 text-slate-500"><span className="w-2 h-2 rounded-sm bg-rose-500/15" />已阻断</span>
        </div>
      </div>
      <div className="flex items-end gap-2 h-[120px]">
        {phases.map((p: any, i: number) => {
          const ah = Math.max((p.active || 0) / max * 112, 3);
          const bh = Math.max((p.blocked || 0) / max * 112, 3);
          return (
            <div key={i} className="flex-1 flex flex-col items-center gap-1.5 group">
              <div className="flex items-end gap-0.5">
                <div className="w-8 rounded-t-md transition-all group-hover:brightness-125 relative" style={{ height: bh, background: `${p.color}18`, borderTop: `1.5px solid ${p.color}35` }}>
                  <div className="absolute bottom-0 w-full rounded-t-md" style={{ height: ah, background: p.color, opacity: 0.85 }} />
                </div>
              </div>
              <span className="text-[10px] font-bold font-mono" style={{ color: p.color }}>{p.active}</span>
              <span className="text-[10px] text-slate-300 font-semibold text-center leading-tight">{p.phase}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Active Cases ---------- */
function ActivePanel({ cases }: { cases: any[] }) {
  const items = cases || [];
  const sevDot: Record<string, string> = { critical: 'bg-rose-500', high: 'bg-amber-500', medium: 'bg-yellow-500' };
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <h3 className="text-[12px] font-bold text-slate-200 mb-3 flex items-center gap-2">
        <Icon n="barChart" s="w-3.5 h-3.5 text-amber-400" />在查案件进度
      </h3>
      <div className="space-y-3.5">
        {items.map((c: any, i: number) => {
          const bc = c.progress >= 70 ? 'from-emerald-500 to-emerald-400' : c.progress >= 40 ? 'from-amber-500 to-amber-400' : 'from-blue-500 to-blue-400';
          return (
            <div key={i}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${sevDot[c.severity] || 'bg-sky-500'}`} />
                  <span className="text-[11px] text-slate-300 font-medium truncate">{c.host}</span>
                  <span className="text-[9px] text-slate-600 ml-1 truncate">{c.ip}</span>
                </div>
                <span className="text-[10px] text-slate-500 ml-2 flex-shrink-0">{c.elapsed}</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 rounded-full bg-slate-700/70 overflow-hidden">
                  <div className={`h-full rounded-full bg-gradient-to-r ${bc} transition-all`} style={{ width: `${c.progress}%`, boxShadow: `0 0 6px rgba(${c.progress>=70?'52,211,153':c.progress>=40?'245,158,11':'59,130,246'},0.4)` }} />
                </div>
                <span className="text-[10px] text-slate-400 font-mono tabular-nums">{c.progress}%</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Device Panel ---------- */
function DevicePanel({ devices }: { devices: any[] }) {
  const items = devices || [];
  const colors: Record<string, string> = { ndr: '#818cf8', hids: '#34d399', firewall: '#a78bfa', edr: '#22d3ee' };
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <h3 className="text-[12px] font-bold text-slate-200 mb-3 flex items-center gap-2">
        <Icon n="server" s="w-3.5 h-3.5 text-indigo-400" />设备健康度
      </h3>
      <div className="space-y-3.5">
        {items.map((d: any, i: number) => {
          const c = colors[d.id] || '#94a3b8';
          return (
            <div key={i}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${d.status==='healthy'?'bg-emerald-400':'bg-amber-400'} shadow-[0_0_4px_rgba(${d.status==='healthy'?'52,211,153':'245,158,11'},0.5)]`} />
                  <span className="text-[11px] text-slate-300 font-semibold">{d.name}</span>
                </div>
                <span className="text-[11px] font-bold text-slate-200 tabular-nums">{d.health}%</span>
              </div>
              <div className="h-2 rounded-full bg-slate-700/70 overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${d.health}%`, background: `linear-gradient(90deg, ${c}, ${c}cc)` }} />
              </div>
              <div className="flex items-center gap-3 mt-1 text-[9px] text-slate-500">
                <span>{d.alerts24h} 告警</span><span>{d.latency}</span><span>{d.uptime}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- Hot Targets ---------- */
function TargetPanel({ targets }: { targets: any[] }) {
  const items = targets || [];
  const max = Math.max(...items.map(t => t.attacks || 0), 1);
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <h3 className="text-[12px] font-bold text-slate-200 mb-3 flex items-center gap-2">
        <Icon n="target" s="w-3.5 h-3.5 text-rose-400" />热点攻击目标
      </h3>
      <div className="space-y-3">
        {items.map((t: any, i: number) => {
          const c = t.severity==='critical'?'#f43f5e':t.severity==='high'?'#f97316':'#818cf8';
          return (
            <div key={i} className="flex items-center gap-2.5">
              <span className="text-[10px] text-slate-400 font-mono w-[85px] text-right flex-shrink-0">{t.ip}</span>
              <div className="flex-1 h-2 rounded-full bg-slate-700/70 overflow-hidden">
                <div className="h-full rounded-full transition-all" style={{ width: `${(t.attacks/max)*100}%`, background: `linear-gradient(90deg, ${c}, ${c}88)` }} />
              </div>
              <span className="text-[10px] text-slate-400 font-mono w-7 text-right flex-shrink-0">{t.attacks}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- IOC ---------- */
function IOCPanel({ iocs }: { iocs: any[] }) {
  const items = iocs || [];
  const tc: Record<string, string> = { ip:'text-blue-400', domain:'text-purple-400', url:'text-orange-400', hash:'text-emerald-400', file:'text-rose-400' };
  const sc: Record<string, string> = {
    blocked: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    monitoring: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    signatured: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
    investigating: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
    quarantined: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
  };
  const sl: Record<string, string> = { blocked:'已封禁', monitoring:'监控中', signatured:'已入库', investigating:'调查中', quarantined:'已隔离' };
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <h3 className="text-[12px] font-bold text-slate-200 mb-3 flex items-center gap-2">
        <Icon n="zap" s="w-3.5 h-3.5 text-violet-400" />近期 IOC
      </h3>
      <div className="space-y-2">
        {items.map((io: any, i: number) => (
          <div key={i} className="flex items-center gap-2 py-1">
            <span className={`text-[9px] font-bold uppercase w-10 text-right flex-shrink-0 ${tc[io.type]||'text-slate-400'}`}>{io.type}</span>
            <span className="text-[10px] text-slate-300 font-mono truncate flex-1">{io.value}</span>
            <span className={`text-[9px] px-2 py-0.5 rounded font-medium border flex-shrink-0 ${sc[io.status]||''}`}>{sl[io.status]||io.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- Timeline ---------- */
function TimelinePanel({ events }: { events: any[] }) {
  const items = events || [];
  const dc: Record<string, string> = { critical:'bg-rose-500', high:'bg-amber-500', medium:'bg-yellow-500' };
  return (
    <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <Icon n="clock" s="w-3.5 h-3.5 text-amber-400" />
        <span className="text-[12px] font-bold text-slate-200">调查事件时间线</span>
        <span className="text-[10px] text-slate-500 ml-auto">{items.length} events</span>
      </div>
      <div className="relative flex items-center overflow-x-auto pb-1">
        <div className="absolute top-[15px] left-0 right-0 h-px bg-gradient-to-r from-slate-700 via-slate-600 to-slate-700" />
        {items.map((e: any, i: number) => (
          <div key={i} className="relative flex flex-col items-center gap-1.5 flex-shrink-0 px-4 first:pl-0 last:pr-0 min-w-[90px]">
            <span className={`w-2.5 h-2.5 rounded-full z-10 ring-[3px] ring-[#070c17] ${dc[e.severity]||'bg-sky-500'} shadow-[0_0_6px_rgba(${e.severity==='critical'?'239,68,68':e.severity==='high'?'245,158,11':'56,189,248'},0.5)]`} />
            <span className="text-[11px] font-mono text-slate-300 font-semibold">{e.time}</span>
            <span className="text-[10px] text-slate-400 text-center leading-tight">{e.event}</span>
            <span className="text-[9px] text-slate-500 font-mono">{e.target}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ================================================
   CASES VIEW
   ================================================ */
function CasesView({ cases, selId, selCase, dtLoad, onSelect }: any) {
  return (
    <div className="grid grid-cols-[280px_1fr] gap-3 flex-1">
      <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm flex flex-col max-h-[calc(100vh-120px)]">
        <div className="px-4 py-3 border-b border-slate-700/60">
          <span className="text-[12px] font-bold text-slate-200">案件列表</span>
          <span className="text-[10px] text-slate-500 ml-1">({cases.length})</span>
        </div>
        <div className="divide-y divide-slate-700/50 overflow-y-auto flex-1">
          {cases.map((c: any) => {
            const active = selId === c.id;
            return (
              <button key={c.id} onClick={() => onSelect(c.id)}
                className={`w-full text-left px-4 py-3 transition-all hover:bg-slate-700/30 ${active ? 'bg-indigo-500/8 border-l-[3px] border-l-indigo-400' : 'border-l-[3px] border-l-transparent'}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-[10px] font-mono font-medium ${active ? 'text-indigo-300' : 'text-slate-400'}`}>{c.id}</span>
                  <span className={`text-[9px] px-2 py-0.5 rounded font-semibold ${c.status==='resolved'?'text-emerald-400 bg-emerald-500/10':'text-blue-400 bg-blue-500/10'}`}>{c.status==='resolved'?'已处置':'调查中'}</span>
                </div>
                <div className="text-[11px] text-slate-300 font-medium mb-1 leading-tight">{c.title}</div>
                <div className="text-[10px] text-slate-500">{c.target?.ip}<span className="mx-1">·</span><span className={c.severity==='critical'?'text-rose-400':c.severity==='high'?'text-amber-400':'text-sky-400'}>{SEV_L[c.severity]}优先级</span></div>
              </button>
            );
          })}
        </div>
      </div>
      <div>
        {!selId ? (
          <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm flex flex-col items-center justify-center py-28 gap-3">
            <Icon n="search" s="w-9 h-9 text-slate-700" />
            <p className="text-[12px] text-slate-500">选择左侧案件查看检查清单</p>
          </div>
        ) : dtLoad ? (
          <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm flex justify-center py-28">
            <div className="animate-spin w-5 h-5 border-2 border-indigo-400/30 border-t-indigo-400 rounded-full" />
          </div>
        ) : selCase ? (
          <CaseDetail c={selCase} />
        ) : (
          <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm flex flex-col items-center justify-center py-28 gap-3">
            <p className="text-[12px] text-slate-500">加载失败</p>
            <button onClick={() => onSelect(selId)} className="text-[11px] text-slate-400 px-3 py-1.5 rounded-lg bg-slate-700/50 hover:bg-slate-600/50 transition-colors">重试</button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------- Case Detail + Checklist ---------- */
function CaseDetail({ c }: { c: any }) {
  const cl = c.checklist || [];
  const cp = c.checklistProgress || { total: 62, passed: 0, warning: 0, pending: 62 };
  const pct = cp.total ? Math.round((cp.passed/cp.total)*100) : 0;

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm p-4">
        <div className="flex items-start justify-between mb-2">
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <span className={`text-[10px] px-2 py-0.5 rounded font-semibold ${c.status==='resolved'?'text-emerald-400 bg-emerald-500/10':'text-blue-400 bg-blue-500/10'}`}>{c.status==='resolved'?'已处置':'调查中'}</span>
              <span className={`text-[10px] px-2 py-0.5 rounded font-semibold ${c.severity==='critical'?'text-rose-400 bg-rose-500/10':'text-amber-400 bg-amber-500/10'}`}>{SEV_L[c.severity]}优先级</span>
            </div>
            <h3 className="text-[13px] font-bold text-slate-100">{c.title}</h3>
            <p className="text-[10px] text-slate-500 mt-1">{c.target?.ip} ({c.target?.hostname}) · {c.target?.role} · {c.target?.sector}</p>
          </div>
          <div className="flex items-center gap-5">
            <div className="text-center"><div className="text-lg font-bold text-amber-400">{c.summary?.threatScore}</div><div className="text-[9px] text-slate-500">评分</div></div>
            <div className="w-px h-10 bg-slate-700/50" />
            <div className="flex items-center gap-3">
              <div className="text-center"><div className="text-sm font-bold text-emerald-400">{cp.passed}</div><div className="text-[9px] text-slate-500">通过</div></div>
              <div className="text-center"><div className="text-sm font-bold text-amber-400">{cp.warning}</div><div className="text-[9px] text-slate-500">告警</div></div>
              <div className="text-center"><div className="text-sm font-bold text-slate-500">{cp.pending}</div><div className="text-[9px] text-slate-500">待办</div></div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 rounded-full bg-slate-700/70 overflow-hidden">
            <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all" style={{ width: `${pct}%`, boxShadow: '0 0 6px rgba(52,211,153,0.4)' }} />
          </div>
          <span className="text-[10px] text-slate-400 font-mono">{pct}%</span>
        </div>
      </div>
      {cl.length > 0 && (
        <div className="rounded-xl border border-slate-700/80 bg-gradient-to-br from-slate-800/90 to-slate-800/70 shadow-sm">
          <div className="px-4 py-2.5 border-b border-slate-700/60 flex items-center gap-2">
            <Icon n="activity" s="w-3 h-3 text-indigo-400" />
            <span className="text-[12px] font-bold text-slate-200">调查检查清单</span>
            <span className="text-[10px] text-slate-500 ml-auto">{cp.total} 项</span>
          </div>
          <div className="p-3 max-h-[calc(100vh-300px)] overflow-y-auto">
            {cl.map((ph: any, pi: number) => <PhaseItem key={ph.id} item={ph} idx={pi} />)}
          </div>
        </div>
      )}
    </div>
  );
}

/* Checklist Tree */
const PHB: Record<number, string> = { 0: 'border-l-rose-500', 1: 'border-l-amber-500', 2: 'border-l-emerald-500', 3: 'border-l-indigo-500', 4: 'border-l-violet-500', 5: 'border-l-cyan-500' };

function PhaseItem({ item, idx }: { item: any; idx: number }) {
  const [open, setOpen] = useState(idx === 0);
  const cnt = cnt(item.children || []);
  return (
    <div className={`border-l-2 ${PHB[idx]||'border-l-slate-600'} pl-3 mb-2`}>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 w-full text-left py-0.5">
        <svg className={`w-2.5 h-2.5 text-slate-500 transition-transform ${open?'rotate-90':''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 18l6-6-6-6"/></svg>
        <span className="text-[12px] font-bold text-slate-200">{item.label}</span>
        <span className="text-[9px] text-slate-500 ml-auto">{cnt.passed}/{cnt.total}</span>
      </button>
      {open && item.children && <div className="ml-1.5">{item.children.map((ch: any) => ch.children ? <GroupItem key={ch.id} item={ch} /> : <LeafItem key={ch.id} item={ch} />)}</div>}
    </div>
  );
}
function GroupItem({ item }: { item: any }) {
  const [open, setOpen] = useState(true);
  const cnts = cnt(item.children || []);
  return (
    <div className="mb-0.5">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 w-full text-left py-0.5">
        <svg className={`w-2 h-2 text-slate-500 transition-transform ${open?'rotate-90':''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 18l6-6-6-6"/></svg>
        <span className="text-[11px] font-semibold text-slate-400">{item.label}</span>
        <span className="text-[9px] text-slate-600 ml-auto">{cnts.passed}/{cnts.total}</span>
      </button>
      {open && item.children && <div className="ml-2">{item.children.map((lf: any) => <LeafItem key={lf.id} item={lf} />)}</div>}
    </div>
  );
}
function LeafItem({ item }: { item: any }) {
  const s = item.status || 'pending';
  const d = s==='passed'?'bg-emerald-500':s==='warning'?'bg-amber-500':'bg-slate-600';
  return (
    <div className="flex items-start gap-1.5 py-0.5">
      <span className={`w-1.5 h-1.5 rounded-full ${d} mt-1.5 flex-shrink-0`} />
      <span className="text-[10px] text-slate-400 leading-relaxed flex-1">{item.label}</span>
      {item.note && <span className="text-[9px] text-slate-500 max-w-[120px] truncate flex-shrink-0">{item.note}</span>}
    </div>
  );
}
function cnt(children: any[]) {
  let p=0,w=0,pd=0,t=0;
  (function walk(items:any[]){for(const it of items){t++;if(it.status==='passed')p++;else if(it.status==='warning')w++;else if(it.status==='pending')pd++;if(it.children)walk(it.children);}})(children);
  return {passed:p,warning:w,pending:pd,total:t};
}