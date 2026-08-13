import { useEffect, useState, useCallback } from 'react';
import { Card } from '@flocks/webui-contract-sdk';
import {
  KpiCard, DonutChart, PieChart, LineChart,
  HorizontalBar, HeatmapGrid, Gauge, Timeline,
} from './charts';

/* ====== Types ====== */
interface ChannelItem { name: string; value: number; color: string; }
interface FileTypeItem { name: string; value: number; color: string; }
interface HeatmapCell { dept: string; high: number; medium: number; low: number; }
interface BarItem { name: string; dept: string; value: number; risk: 'high' | 'medium' | 'low'; }
interface TimelineItem { time: string; user: string; dept: string; action: string; risk: 'high' | 'medium' | 'low'; status: string; }

/* ====== Constants ====== */
const C = {
  green:  '#10b981',
  yellow: '#f59e0b',
  orange: '#f97316',
  red:    '#ef4444',
  blue:   '#3b82f6',
  purple: '#8b5cf6',
  cyan:   '#06b6d4',
};

/* ====== Mock Data Generators ====== */
function genChannelData(): ChannelItem[] {
  return [
    { name: '邮件外发', value: 423, color: C.blue },
    { name: '企业云盘', value: 348, color: C.purple },
    { name: '即时通讯', value: 312, color: C.orange },
    { name: 'USB拷贝',  value: 164, color: C.red },
  ];
}

function genFileTypeData(): FileTypeItem[] {
  return [
    { name: '合同文件', value: 318, color: C.orange },
    { name: '客户资料', value: 212, color: C.yellow },
    { name: '源代码',   value: 185, color: C.red },
    { name: '财务数据', value: 97,  color: C.blue },
    { name: '其他文件', value: 435, color: '#64748b' },
  ];
}

function genDeptHeatmap(): HeatmapCell[] {
  return [
    { dept: '研发部', high: 5, medium: 12, low: 47 },
    { dept: '销售部', high: 9, medium: 14, low: 35 },
    { dept: '财务部', high: 3, medium: 6,  low: 18 },
    { dept: '市场部', high: 2, medium: 9,  low: 29 },
    { dept: '运营部', high: 1, medium: 4,  low: 23 },
    { dept: '人力资源', high: 1, medium: 3, low: 16 },
    { dept: '法务部', high: 0, medium: 1, low: 10 },
    { dept: '管理层', high: 2, medium: 7, low: 14 },
  ];
}

function genTopEmployees(): BarItem[] {
  return [
    { name: '张*明', dept: '研发部', value: 47, risk: 'high' },
    { name: '李*华', dept: '销售部', value: 38, risk: 'high' },
    { name: '王*芳', dept: '财务部', value: 31, risk: 'medium' },
    { name: '陈*强', dept: '研发部', value: 28, risk: 'high' },
    { name: '刘*婷', dept: '市场部', value: 24, risk: 'medium' },
    { name: '赵*刚', dept: '销售部', value: 22, risk: 'high' },
    { name: '周*杰', dept: '研发部', value: 19, risk: 'medium' },
    { name: '吴*丽', dept: '财务部', value: 16, risk: 'low' },
    { name: '孙*强', dept: '管理层', value: 14, risk: 'high' },
    { name: '郑*敏', dept: '运营部', value: 12, risk: 'low' },
  ];
}

function genHourlyTrend() {
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`);
  const counts = [12,8,5,3,2,4,8,18,35,52,68,75,82,78,65,72,58,45,38,25,18,14,10,8];
  const ratios = [8,5,3,2,1,2,5,10,18,22,25,28,26,24,20,25,22,18,15,12,10,8,6,5];
  return { hours, counts, ratios };
}

function genDailyTrend() {
  const days = ['06/25','06/26','06/27','06/28','06/29','06/30','07/01'];
  const counts = [1180,1245,1320,1100,980,1150,1247];
  const ratios = [22,25,28,20,18,24,23];
  return { days, counts, ratios };
}

function genTimeline(): TimelineItem[] {
  return [
    { time: '14:32:15', user: '李*华', dept: '销售部', action: '外发客户名单至 personal@163.com', risk: 'high', status: '已处置' },
    { time: '14:18:40', user: '张*明', dept: '研发部', action: '上传源代码压缩包至个人云盘', risk: 'high', status: '处置中' },
    { time: '14:05:22', user: '王*芳', dept: '财务部', action: '发送财务报表至外部审计邮箱', risk: 'medium', status: '待复核' },
    { time: '13:48:10', user: '陈*强', dept: '研发部', action: '通过IM外发API密钥文档', risk: 'high', status: '已处置' },
    { time: '13:30:05', user: '赵*刚', dept: '销售部', action: '外发合同扫描件至合作方', risk: 'high', status: '已处置' },
    { time: '13:15:33', user: '刘*婷', dept: '市场部', action: '上传营销方案至企业云盘个人区', risk: 'medium', status: '已处置' },
    { time: '12:58:18', user: '郑*敏', dept: '运营部', action: '邮件外发运营数据报表', risk: 'low', status: '自动忽略' },
    { time: '12:42:50', user: '孙*强', dept: '管理层', action: 'USB拷贝战略规划文档', risk: 'high', status: '已处置' },
    { time: '12:20:11', user: '吴*丽', dept: '财务部', action: '发送工资明细至HR系统', risk: 'low', status: '自动忽略' },
    { time: '11:55:40', user: '周*杰', dept: '研发部', action: '上传技术方案至GitHub私有仓库', risk: 'medium', status: '已处置' },
    { time: '11:30:08', user: '张*明', dept: '研发部', action: '外发离职交接文档至竞对公司域名', risk: 'high', status: '已处置' },
    { time: '11:05:19', user: '李*华', dept: '销售部', action: '发送报价单至未授权供应商', risk: 'medium', status: '待复核' },
  ];
}

/* ====== Main Page ====== */
export default function Page() {
  const [time, setTime] = useState(new Date());
  const [trendMode, setTrendMode] = useState<'24h' | '7d'>('24h');
  const [kpi, setKpi] = useState({ total: 1247, highRisk: 23, disposed: 18, pending: 5, autoRate: 78 });

  /* Simulated live refresh */
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      setKpi((prev) => ({
        total: prev.total + Math.floor(Math.random() * 3),
        highRisk: Math.max(0, prev.highRisk + (Math.random() > 0.7 ? 1 : 0)),
        disposed: prev.disposed + (Math.random() > 0.6 ? 1 : 0),
        pending: Math.max(0, prev.pending + (Math.random() > 0.5 ? 1 : -1)),
        autoRate: Math.min(100, Math.max(60, prev.autoRate + (Math.random() - 0.5) * 2)),
      }));
    }, 8000);
    return () => clearInterval(t);
  }, []);

  /* Static data */
  const [channelData] = useState(genChannelData);
  const [fileTypeData] = useState(genFileTypeData);
  const [deptHeatmap] = useState(genDeptHeatmap);
  const [topEmployees] = useState(genTopEmployees);
  const [timeline] = useState(genTimeline);
  const hourlyTrend = genHourlyTrend();
  const dailyTrend = genDailyTrend();

  const gaugeValue = 32;

  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;

  return (
    <div className="min-h-screen" style={{ backgroundColor: '#0a0e27', color: '#fff' }}>
      {/* ===== Header ===== */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="w-1 h-6 rounded" style={{ backgroundColor: C.red }} />
          <h1 className="text-xl font-bold tracking-wide">DLP 员工外发合规态势</h1>
          <span className="text-white/20 text-xs ml-2">|</span>
          <span className="text-white/30 text-xs">云枢 DLP · 智能审计</span>
        </div>
        <div className="flex items-center gap-4 text-xs text-white/40">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: C.green }} />
            <span>实时监控中</span>
          </div>
          <span>{fmt(time)}</span>
        </div>
      </div>

      {/* ===== Content ===== */}
      <div className="px-4 py-3 space-y-3">

        {/* KPI Row */}
        <div className="grid grid-cols-5 gap-3">
          <KpiCard label="今日外发总条数" value={kpi.total} color={C.blue} sub="较昨日 +5.6%" />
          <KpiCard label="高风险条数" value={kpi.highRisk} color={C.red} sub="需人工复核" />
          <KpiCard label="已处置" value={kpi.disposed} color={C.green} sub={`处置率 ${((kpi.disposed / Math.max(kpi.highRisk, 1)) * 100).toFixed(0)}%`} />
          <KpiCard label="待人工复核" value={kpi.pending} color={kpi.pending > 5 ? C.orange : C.yellow} sub="三级·人工必审" />
          <KpiCard label="自动化处置率" value={`${Math.round(kpi.autoRate)}%`} color={C.purple} sub={`${Math.round(kpi.autoRate * 0.8)}% 一级自动忽略`} />
        </div>

        {/* Row: 外发渠道 | 外发文件类型 | 外发趋势 — 三列横排 */}
        <div className="grid grid-cols-3 gap-3">
          <Card title="外发渠道分布">
            <DonutChart data={channelData} size={160} thickness={22} label="外发渠道" />
          </Card>

          <Card title="外发文件类型">
            <PieChart data={fileTypeData} size={160} label="文件分类" />
          </Card>

          <Card title={
            <div className="flex items-center justify-between w-full">
              <span>外发趋势</span>
              <div className="flex rounded bg-white/5 p-0.5 text-xs">
                {(['24h', '7d'] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setTrendMode(m)}
                    className={`px-3 py-1 rounded transition-colors ${
                      trendMode === m ? 'bg-white/15 text-white' : 'text-white/40 hover:text-white/60'
                    }`}
                  >
                    {m === '24h' ? '近24小时' : '近7天'}
                  </button>
                ))}
              </div>
            </div>
          }>
            <LineChart
              labels={trendMode === '24h' ? hourlyTrend.hours : dailyTrend.days}
              series={[
                {
                  key: 'count',
                  label: '外发量',
                  data: trendMode === '24h' ? hourlyTrend.counts : dailyTrend.counts,
                  color: C.blue,
                  yAxis: 'left',
                },
                {
                  key: 'ratio',
                  label: '高风险占比',
                  data: trendMode === '24h' ? hourlyTrend.ratios : dailyTrend.ratios,
                  color: C.red,
                  yAxis: 'right',
                },
              ]}
              width={360}
              height={240}
            />
          </Card>
        </div>

        {/* Row: Heatmap + Top10 等宽两列 */}
        <div className="grid grid-cols-2 gap-3">
          <Card title="部门风险热力图">
            <HeatmapGrid data={deptHeatmap} />
          </Card>
          <Card title="Top10 高风险员工">
            <HorizontalBar data={topEmployees} />
          </Card>
        </div>

        {/* Row: 误报噪声占比 | 实时处置流 — 横向排列 */}
        <div className="grid grid-cols-12 gap-3">
          <div className="col-span-3">
            <Card title="误报噪声占比">
              <div className="flex flex-col items-center pt-1">
                <Gauge
                  value={gaugeValue}
                  max={100}
                  label="LLM研判误报率"
                  zones={[
                    { from: 0, to: 30, color: C.green },
                    { from: 30, to: 60, color: C.yellow },
                    { from: 60, to: 100, color: C.red },
                  ]}
                />
                <div className="flex justify-between w-full mt-2 text-xs text-white/30 px-4">
                  <span style={{ color: C.green }}>优秀</span>
                  <span style={{ color: C.yellow }}>关注</span>
                  <span style={{ color: C.red }}>警告</span>
                </div>
              </div>
            </Card>
          </div>
          <div className="col-span-9">
            <Card title="实时处置流（最近24h）">
              <Timeline items={timeline} />
            </Card>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-white/15 text-xs py-2">
          数据来源：云枢 DLP · Flocks yunshu_dlp_audit Workflow · 模拟数据
        </div>
      </div>
    </div>
  );
}
