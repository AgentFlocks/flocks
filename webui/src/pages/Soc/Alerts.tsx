import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Bot,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Database,
  Download,
  FileText,
  Filter,
  GitBranch,
  Globe2,
  ListChecks,
  MessageSquare,
  ScanLine,
  Search,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, ConfigWorkshop, ModeSwitch } from './components';
import {
  alertDenoiseDailySummary,
  deepInvestigations,
  incidentClusters,
  responseActions,
} from './mockData';

type IncidentCluster = typeof incidentClusters[number];
type DeepInvestigation = typeof deepInvestigations[number];
type ResponseAction = typeof responseActions[number];
type DenoiseCategory = typeof alertDenoiseDailySummary.categories[number];
type DenoiseCategoryKey = DenoiseCategory['key'];
type InvestigationSessionMessage = {
  role: string;
  sender: string;
  time: string;
  content: string;
  delegate?: {
    title: string;
    description: string;
    status: string;
    elapsed: string;
    steps: number;
  };
  toolCalls?: Array<{
    name: string;
    target: string;
    status: 'success' | 'failed';
    result: string;
  }>;
  conclusion?: string;
};

export default function SocAlertsPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="告警运营"
        description="一个页面承载告警降噪、研判工作台、深度调查，并在场景内引导用户配置自己的 SOC SOP。"
        icon={<AlertTriangle className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/alerts?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="alerts" /> : <AlertsOperation />}
    </div>
  );
}

function AlertsOperation() {
  const [activeTab, setActiveTab] = useState<'denoise' | 'triage' | 'investigation' | 'response'>('investigation');

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          {[
            ['9836', '原始告警', '来自 SIEM/NDR/WAF/EDR'],
            ['1023', '降噪后告警', '去重、合并、压制误报'],
            ['5', 'NDR 告警研判', '单条告警补全情报和资产上下文'],
            ['5', '深度调查', '研判告警全部触发协作调查'],
            ['20', '处置建议', '扫描、研判、调查动作汇总'],
          ].map(([value, label, hint], index) => (
            <div key={label} className="relative rounded-lg border border-gray-200 bg-gray-50 p-3">
              {index < 4 && (
                <ArrowRight className="absolute -right-4 top-1/2 z-10 hidden h-5 w-5 -translate-y-1/2 text-gray-300 md:block" />
              )}
              <div className="text-2xl font-bold text-gray-900">{value}</div>
              <div className="mt-1 text-sm font-medium text-gray-900">{label}</div>
              <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 pt-3">
          <div className="flex flex-wrap gap-2">
            <TabButton
              active={activeTab === 'denoise'}
              onClick={() => setActiveTab('denoise')}
              icon={<Filter className="h-4 w-4" />}
              label="降噪分析"
            />
            <TabButton
              active={activeTab === 'triage'}
              onClick={() => setActiveTab('triage')}
              icon={<Search className="h-4 w-4" />}
              label="告警研判"
            />
            <TabButton
              active={activeTab === 'investigation'}
              onClick={() => setActiveTab('investigation')}
              icon={<Bot className="h-4 w-4" />}
              label="深度调查"
            />
            <TabButton
              active={activeTab === 'response'}
              onClick={() => setActiveTab('response')}
              icon={<ListChecks className="h-4 w-4" />}
              label="响应处置"
            />
          </div>
          {activeTab === 'triage' && <Badge tone="red">5 条 NDR 待研判</Badge>}
          {activeTab === 'response' && <Badge tone="green">{responseActions.length} 条待处置</Badge>}
        </div>

        <div className="p-3">
          {activeTab === 'denoise' && <DenoiseAnalysis />}
          {activeTab === 'triage' && <TriageResult />}
          {activeTab === 'investigation' && <InvestigationResult />}
          {activeTab === 'response' && <ResponseActionsResult />}
        </div>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-t-lg border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? 'border-red-600 text-red-700'
          : 'border-transparent text-gray-500 hover:text-gray-900'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function DenoiseAnalysis() {
  const [activeCategory, setActiveCategory] = useState<DenoiseCategoryKey>('scan');
  const summary = alertDenoiseDailySummary;
  const totalRemoved = summary.rawCount - summary.triageCount;
  const categorizedFilterRemoved = summary.categories
    .filter((category) => category.key !== 'duplicate')
    .reduce((sum, category) => sum + category.removed, 0);
  const otherFilterRemoved = Math.max(0, summary.filterRemovedCount - categorizedFilterRemoved);
  const denoiseSegments = [
    { label: '归一化失败', value: summary.normalizeFailedCount, className: 'bg-slate-400 text-slate-900' },
    { label: '扫描告警', value: 3428, className: 'bg-red-500 text-white', categoryKey: 'scan' as const },
    { label: '条件过滤', value: 1124, className: 'bg-rose-500 text-white', categoryKey: 'condition' as const },
    { label: '规则过滤', value: 782, className: 'bg-pink-500 text-white', categoryKey: 'rule' as const },
    { label: '黑白名单', value: 738, className: 'bg-fuchsia-500 text-white', categoryKey: 'allowlist' as const },
    { label: '其他过滤', value: otherFilterRemoved, className: 'bg-slate-500 text-white' },
    { label: '重复告警', value: summary.dedupRemovedCount, className: 'bg-orange-500 text-white', categoryKey: 'duplicate' as const },
    { label: '进入研判', value: summary.triageCount, className: 'bg-green-500 text-white' },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 py-1 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 text-red-600" />
            <span className="text-sm font-semibold text-gray-900">{summary.date} 降噪日报</span>
            <span className="text-sm font-medium text-gray-500">
              今日减少 {totalRemoved.toLocaleString()} 条 · 压缩率 {Math.round((totalRemoved / summary.rawCount) * 1000) / 10}%
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {['今天', '近 7 天'].map((item, index) => (
            <button
              key={item}
              type="button"
              className={`rounded-lg border px-3 py-2 font-medium ${
                index === 0
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="py-3">
        <div className="flex h-16 overflow-hidden rounded-lg bg-gray-100 shadow-sm">
          {denoiseSegments.map((segment) => {
            const width = summary.rawCount ? (segment.value / summary.rawCount) * 100 : 0;
            if (segment.value <= 0) return null;
            return (
              <div
                key={segment.label}
                role={segment.categoryKey ? 'button' : undefined}
                tabIndex={segment.categoryKey ? 0 : undefined}
                onClick={() => segment.categoryKey && setActiveCategory(segment.categoryKey)}
                onKeyDown={(event) => {
                  if (!segment.categoryKey) return;
                  if (event.key === 'Enter' || event.key === ' ') {
                    setActiveCategory(segment.categoryKey);
                  }
                }}
                className={`flex min-w-[68px] items-center px-3 text-xs font-semibold ${segment.className} ${
                  segment.categoryKey ? 'cursor-pointer hover:brightness-95' : ''
                }`}
                style={{ flexBasis: `${width}%` }}
                title={`${segment.label} ${segment.value.toLocaleString()} 条，占 ${width.toFixed(1)}%`}
              >
                <span className="truncate">{segment.label} {segment.value.toLocaleString()}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[220px_1fr]">
        <div className="rounded-lg bg-gray-50 p-2">
          {summary.categories.map((category) => (
            <button
              key={category.key}
              type="button"
              onClick={() => setActiveCategory(category.key)}
              className={`mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                activeCategory === category.key ? 'bg-red-50 font-semibold text-red-700' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {category.title}
              <ChevronRight className="h-4 w-4" />
            </button>
          ))}
        </div>
        <div className="min-w-0 bg-white px-1 py-2">
          {activeCategory === 'scan' && <ScanDenoiseReport />}
          {activeCategory === 'duplicate' && <DuplicateDenoiseReport />}
          {activeCategory === 'condition' && <ConditionDenoiseReport />}
          {activeCategory === 'rule' && <RuleDenoiseReport />}
          {activeCategory === 'allowlist' && <ListDenoiseReport />}
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle?: string }) {
  return (
    <div className="mb-3 flex items-start gap-2">
      <div className="mt-0.5 text-red-600">{icon}</div>
      <div>
        <h3 className="font-semibold text-gray-900">{title}</h3>
        {subtitle && <p className="mt-1 text-sm leading-5 text-gray-500">{subtitle}</p>}
      </div>
    </div>
  );
}

function MockTable({ headers, rows }: { headers: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-[880px] divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {headers.map((header) => (
              <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {rows.map((row, index) => (
            <tr key={index} className="hover:bg-gray-50">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScanDenoiseReport() {
  const report = alertDenoiseDailySummary.scanReport;
  const sourceChart = report.sourceDistribution.map((item) => ({
    name: item.type,
    value: item.alerts,
  }));
  const sourceIpChart = report.sourceIpInventory.slice(0, 6).map((item) => ({
    name: item.ip,
    value: item.alerts,
  }));
  const statusChart = report.statusCodes.map((item) => ({
    name: item.code,
    value: item.count,
  }));

  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <ScanLine className="h-5 w-5 text-red-600" />
          <h2 className="font-semibold text-gray-900">{report.title}</h2>
          <Badge tone="red">{alertDenoiseDailySummary.date}</Badge>
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-700 shadow-sm hover:bg-red-50"
          >
            <FileText className="h-4 w-4" />
            导出 PDF 报告
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <DenoisePiePanel title="扫描来源分布" data={sourceChart} />
        <DenoisePiePanel title="来源 IP Top 6" data={sourceIpChart} />
        <DenoisePiePanel title="响应 Code 分布" data={statusChart} />
      </div>

      <section>
        <SectionTitle
          icon={<AlertTriangle className="h-5 w-5" />}
          title="200 响应成功扫描资产 Top 10"
        />
        <MockTable
          headers={['资产', '业务', '暴露面', '200 次数', '敏感接口', '命中路径样例', '风险', '建议']}
          rows={report.successfulAssets.slice(0, 10).map((item) => [
            <span className="font-mono text-gray-900">{item.asset}</span>,
            item.business,
            <Badge tone={item.exposure === '公网' ? 'orange' : 'blue'}>{item.exposure}</Badge>,
            item.okCount,
            item.sensitive,
            <span className="font-mono text-xs">{item.examples}</span>,
            <Badge tone={item.risk === '高' ? 'red' : 'orange'}>{item.risk}</Badge>,
            item.recommendation,
          ])}
        />
      </section>

      <section>
        <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <SectionTitle
            icon={<Database className="h-5 w-5" />}
            title="扫描源清单"
          />
          <div className="flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 shadow-sm hover:bg-gray-50"
            >
              <Download className="h-4 w-4" />
              导出 IP 清单
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-red-700"
            >
              <ShieldCheck className="h-4 w-4" />
              高风险一键加入 FW
            </button>
          </div>
        </div>
        <MockTable
          headers={['源 IP', '内外部', '类型', '地区/区域', 'ASN', '情报/行为', '告警数', '资产数', '200', '敏感命中', '首次', '最近', 'Top 路径', '风险', '处置', '操作']}
          rows={report.sourceIpInventory.map((item) => [
            <span className="font-mono text-gray-900">{item.ip}</span>,
            <Badge tone={item.side === '外部' ? 'orange' : 'blue'}>{item.side}</Badge>,
            item.type,
            item.region,
            item.asn,
            item.intel,
            item.alerts,
            item.assets,
            item.okCount,
            item.sensitiveHits,
            item.firstSeen,
            item.lastSeen,
            <span className="font-mono text-xs">{item.topPath}</span>,
            <Badge tone={item.risk === '高' ? 'red' : item.risk === '中' ? 'orange' : 'green'}>{item.risk}</Badge>,
            item.disposition,
            <div className="flex gap-2">
              <button type="button" className="whitespace-nowrap text-red-600 hover:text-red-700">加入 FW</button>
              <button type="button" className="whitespace-nowrap text-gray-500 hover:text-gray-700">查看样本</button>
            </div>,
          ])}
        />
      </section>

      <section>
        <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <SectionTitle
            icon={<ShieldCheck className="h-5 w-5" />}
            title="建议封禁列表"
            subtitle="根据黑灰产情报、敏感接口 200 响应、未授权内部扫描等条件生成，当前操作均为 mock。"
          />
          <div className="flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 shadow-sm hover:bg-gray-50"
            >
              <Download className="h-4 w-4" />
              导出封禁清单
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-red-700"
            >
              <ShieldCheck className="h-4 w-4" />
              批量加入 FW
            </button>
          </div>
        </div>
        <MockTable
          headers={['IP', '封禁范围', '原因', '证据', '建议动作', '优先级', '责任组', '操作']}
          rows={report.blockRecommendations.map((item) => [
            <span className="font-mono text-gray-900">{item.ip}</span>,
            item.scope,
            item.reason,
            item.evidence,
            item.action,
            <Badge tone={item.priority === '高' ? 'red' : 'orange'}>{item.priority}</Badge>,
            item.owner,
            <div className="flex gap-2">
              <button type="button" className="whitespace-nowrap text-red-600 hover:text-red-700">加入 FW</button>
              <button type="button" className="whitespace-nowrap text-gray-500 hover:text-gray-700">生成工单</button>
            </div>,
          ])}
        />
      </section>

      <section>
        <SectionTitle
          icon={<ListChecks className="h-5 w-5" />}
          title="内部扫描器排查清单"
          subtitle="用于区分授权扫描、待确认任务和未授权横向扫描，帮助值班员决定白名单、隔离或转研判。"
        />
        <MockTable
          headers={['内部源 IP', '归属', '授权状态', '预期窗口', '排查发现', '下一步']}
          rows={report.internalScannerChecklist.map((item) => [
            <span className="font-mono text-gray-900">{item.ip}</span>,
            item.owner,
            <Badge tone={item.status === '已授权' ? 'green' : item.status === '未授权' ? 'red' : 'orange'}>{item.status}</Badge>,
            item.expectedWindow,
            item.finding,
            item.nextStep,
          ])}
        />
      </section>

    </div>
  );
}

function DenoisePiePanel({ title, data }: { title: string; data: { name: string; value: number }[] }) {
  const colors = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899'];
  const total = data.reduce((sum, item) => sum + item.value, 0);
  const getColor = (name: string, index: number) => {
    if (title === '响应 Code 分布') {
      const statusColors: Record<string, string> = {
        '200': '#22c55e',
        '404': '#ef4444',
        '301/302': '#f59e0b',
        '401/403': '#f97316',
        '5xx': '#06b6d4',
      };
      return statusColors[name] || colors[index % colors.length];
    }
    return colors[index % colors.length];
  };

  return (
    <div className="rounded-lg bg-gray-50 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <span className="text-xs text-gray-500">总计 {total.toLocaleString()}</span>
      </div>
      <div className="grid grid-cols-[160px_1fr] items-center gap-3">
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                innerRadius={42}
                outerRadius={72}
                paddingAngle={1}
                stroke="none"
              >
                {data.map((item, index) => (
                  <Cell key={item.name} fill={getColor(item.name, index)} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number, name: string) => [`${Number(value).toLocaleString()} 条`, name]}
                contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="space-y-2">
          {data.map((item, index) => {
            const ratio = total ? (item.value / total) * 100 : 0;
            return (
              <div key={item.name} className="flex items-center gap-2 text-xs">
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: getColor(item.name, index) }} />
                <span className="min-w-0 flex-1 truncate text-gray-600">{item.name}</span>
                <span className="font-semibold text-gray-900">{ratio.toFixed(1)}%</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function DuplicateDenoiseReport() {
  const report = alertDenoiseDailySummary.duplicateReport;
  const workflow = alertDenoiseDailySummary.workflow;
  return (
    <div className="space-y-4">
      <SectionTitle icon={<GitBranch className="h-5 w-5" />} title="重复告警分析" subtitle="基于源目 IP 严格字段和 URI/body 模糊字段生成 dedup key。" />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        {[
          ['去重阈值', workflow.threshold],
          ['LSH 簇数量', report.clusters],
          ['唯一 dedup key', report.dedupKeys],
          ['批内压缩率', report.ratio],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="text-xs text-gray-500">{label}</div>
            <div className="mt-1 text-lg font-bold text-gray-900">{value}</div>
          </div>
        ))}
      </div>
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm leading-6 text-gray-600">
        strict fields: <span className="font-mono">{workflow.strictFields.join(', ')}</span>；lsh fields: <span className="font-mono">{workflow.lshFields.join(', ')}</span>
      </div>
      <MockTable
        headers={['dedup 簇', '代表告警', '重复数', '源 IP', '目标资产', '相似路径', '首次出现', '最近出现']}
        rows={report.rows.map((item) => [
          <span className="font-mono text-gray-900">{item.cluster}</span>,
          item.title,
          item.duplicates,
          item.source,
          item.target,
          <span className="font-mono text-xs">{item.pattern}</span>,
          item.firstSeen,
          item.lastSeen,
        ])}
      />
    </div>
  );
}

function ConditionDenoiseReport() {
  return (
    <div className="space-y-4">
      <SectionTitle icon={<Filter className="h-5 w-5" />} title="条件过滤分析" subtitle="对应 workflow 中 filter_logs 节点的 _process_type 分类。" />
      <MockTable
        headers={['process type', '告警数', '页面解释', '样本入口']}
        rows={alertDenoiseDailySummary.conditionReport.map((item) => [
          <span className="font-mono text-xs text-gray-900">{item.processType}</span>,
          item.count,
          item.explanation,
          <button type="button" className="text-red-600 hover:text-red-700">查看样本</button>,
        ])}
      />
    </div>
  );
}

function RuleDenoiseReport() {
  return (
    <div className="space-y-4">
      <SectionTitle icon={<FileText className="h-5 w-5" />} title="规则过滤分析" subtitle="展示低价值规则、噪声规则和长期误报规则的过滤效果。" />
      <MockTable
        headers={['规则 ID', '规则名称', '减少量', '最近调整', '说明']}
        rows={alertDenoiseDailySummary.ruleReport.map((item) => [
          <span className="font-mono text-gray-900">{item.id}</span>,
          item.name,
          item.removed,
          item.updatedAt,
          item.note,
        ])}
      />
    </div>
  );
}

function ListDenoiseReport() {
  const report = alertDenoiseDailySummary.listReport;
  return (
    <div className="space-y-5">
      <SectionTitle icon={<ShieldCheck className="h-5 w-5" />} title="黑白名单分析" subtitle="白名单用于压低运营噪声；黑名单命中压缩为少量情报事件，不直接丢失上下文。" />
      <section>
        <div className="mb-2 text-sm font-semibold text-gray-900">白名单</div>
        <MockTable
          headers={['对象', '类型', '减少量', '有效期', '说明']}
          rows={report.allow.map((item) => [
            <span className="font-mono text-gray-900">{item.object}</span>,
            item.type,
            item.removed,
            item.ttl,
            item.note,
          ])}
        />
      </section>
      <section>
        <div className="mb-2 text-sm font-semibold text-gray-900">黑名单</div>
        <MockTable
          headers={['对象', '类型', '命中数', '处理方式', '说明']}
          rows={report.deny.map((item) => [
            <span className="font-mono text-gray-900">{item.object}</span>,
            item.type,
            item.hits,
            item.action,
            item.note,
          ])}
        />
      </section>
    </div>
  );
}

function ResponseActionsResult() {
  const pendingCount = responseActions.filter((item) => item.status === '待执行').length;
  const processingCount = responseActions.filter((item) => item.status === '处理中').length;
  const confirmCount = responseActions.filter((item) => item.status === '待确认').length;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 py-1 lg:flex-row lg:items-center lg:justify-between">
        <SectionTitle
          icon={<ListChecks className="h-5 w-5" />}
          title="响应处置清单"
          subtitle="汇总降噪扫描、告警研判和深度调查产生的处置动作，当前全部为 mock 数据。"
        />
        <div className="flex flex-wrap gap-2 text-xs font-semibold text-gray-600">
          <span>待执行 {pendingCount}</span>
          <span>处理中 {processingCount}</span>
          <span>待确认 {confirmCount}</span>
        </div>
      </div>
      <MockTable
        headers={['动作 ID', '来源', '优先级', '处置对象', '处置动作', '关键证据', '责任组', '状态', '操作']}
        rows={responseActions.map((item) => [
          <span className="font-mono text-gray-900">{item.id}</span>,
          item.source,
          <Badge tone={item.priority === 'P1' ? 'red' : 'orange'}>{item.priority}</Badge>,
          <span className="font-mono text-xs text-gray-900">{item.object}</span>,
          <span className="font-medium text-gray-900">{item.action}</span>,
          item.evidence,
          item.owner,
          <Badge tone={getResponseStatusTone(item)}>{item.status}</Badge>,
          <div className="flex gap-2">
            <button type="button" className="whitespace-nowrap text-red-600 hover:text-red-700">生成工单</button>
            <button type="button" className="whitespace-nowrap text-gray-500 hover:text-gray-700">查看来源</button>
          </div>,
        ])}
      />
    </div>
  );
}

function getResponseStatusTone(item: ResponseAction) {
  if (item.status === '待执行') return 'red';
  if (item.status === '处理中') return 'orange';
  return 'blue';
}

function TriageResult() {
  const [selectedIncident, setSelectedIncident] = useState<IncidentCluster | null>(null);

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {['告警 ID', '优先级', 'NDR 告警', '源 IP 情报', '目标资产', '请求 / 响应', '结论'].map((header) => (
                <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {incidentClusters.map((cluster) => (
              <tr
                key={cluster.id}
                onClick={() => setSelectedIncident(cluster)}
                className="cursor-pointer transition-colors hover:bg-red-50/50"
              >
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{cluster.id}</td>
                <td className="px-4 py-3"><Badge tone={cluster.priority === 'P1' ? 'red' : 'orange'}>{cluster.priority}</Badge></td>
                <td className="px-4 py-3">
                  <div className="text-sm font-medium text-gray-900">{cluster.title}</div>
                  <div className="mt-1 max-w-xl truncate text-xs text-gray-500">{cluster.reason}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm font-medium text-gray-900">{cluster.srcIp}</div>
                  <div className="mt-1 text-xs text-gray-500">{cluster.srcIntel.verdict} · {cluster.srcIntel.location}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm font-medium text-gray-900">{cluster.asset.name}</div>
                  <div className="mt-1 text-xs text-gray-500">{cluster.asset.business} · {cluster.asset.exposure}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm text-gray-900">{cluster.request.method} {cluster.request.uri}</div>
                  <div className="mt-1 text-xs text-gray-500">响应 {cluster.response.statusCode}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-red-600">{cluster.conclusion.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedIncident && (
        <IncidentDrawer incident={selectedIncident} onClose={() => setSelectedIncident(null)} />
      )}
    </>
  );
}

function IncidentDrawer({ incident, onClose }: { incident: IncidentCluster; onClose: () => void }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const steps = buildAnalysisSteps(incident);
  const isSuccess = incident.conclusion.verdict.includes('成功') || incident.conclusion.verdict.includes('成立');
  const isPhishing = incident.title.includes('钓鱼');

  return (
    <div className="fixed inset-0 z-[70]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭告警详情"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-[82%]">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <FileText className="h-5 w-5 text-gray-900" />
            <div>
              <div className="text-base font-semibold text-gray-900">Web日志分析</div>
              <div className="text-xs text-gray-500">{incident.id} · {incident.ndrRule}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button className="rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900">
              <Download className="h-4 w-4" />
            </button>
            <button className="rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900">
              <FileText className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="relative flex-1 overflow-y-auto px-5 py-4">
          <div className="mx-auto max-w-none">
            <button
              type="button"
              onClick={() => setStepsOpen((open) => !open)}
              className="mb-5 flex w-full items-center justify-between rounded-lg border border-gray-200 bg-white px-5 py-3 text-left shadow-sm"
            >
              <div className="flex items-center gap-4">
                <span className="text-base font-semibold text-gray-900">分析步骤</span>
                <span className="text-sm text-gray-500">{steps.length} 个步骤</span>
              </div>
              <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-600">
                {stepsOpen ? '收起' : '展开查看'}
                <ChevronRight className={`h-4 w-4 transition-transform ${stepsOpen ? '-rotate-90' : 'rotate-90'}`} />
              </span>
            </button>

            {stepsOpen && (
              <div className="mb-6 border-l border-gray-200 pl-5">
                {steps.map((step) => (
                  <div key={step.title} className="relative mb-5 last:mb-0">
                    <div className="absolute -left-[29px] top-1 flex h-4 w-4 items-center justify-center rounded-full bg-gray-900 text-white">
                      <CheckCircle2 className="h-3 w-3" />
                    </div>
                    <h3 className="text-base font-semibold text-gray-900">{step.title}</h3>
                    <p className="mt-2 text-sm leading-6 text-gray-700">{step.content}</p>
                    {step.code && <ReportCodeBlock code={step.code} />}
                  </div>
                ))}
              </div>
            )}

            <article className="relative pb-10 text-gray-900">
              <div className="mb-3 flex items-center gap-3 text-sm">
                <span>2026-06-14 09:44</span>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold text-white ${isSuccess ? 'bg-red-600' : 'bg-orange-500'}`}>
                  {incident.conclusion.verdict}
                </span>
              </div>
              <h1 className="text-2xl font-bold tracking-normal text-gray-950">{getReportTitle(incident)}</h1>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 lg:grid-cols-4">
                {(isPhishing
                  ? [
                      ['源终端', incident.srcIp],
                      ['访问 URL', incident.asset.name],
                      ['微步标签', '钓鱼URL'],
                      ['置信度', `${incident.confidence}%`],
                    ]
                  : [
                      ['源 IP', incident.srcIp],
                      ['目标资产', incident.asset.name],
                      ['响应码', `${incident.response.statusCode}`],
                      ['置信度', `${incident.confidence}%`],
                    ]
                ).map(([label, value]) => (
                  <div key={label} className="rounded-md bg-gray-50 px-3 py-2">
                    <span className="text-gray-400">{label}</span>
                    <span className="ml-2 font-semibold text-gray-800">{value}</span>
                  </div>
                ))}
              </div>

              <ReportHeading>研判结论</ReportHeading>
              <p className="text-sm leading-7">
                {incident.conclusion.summary}
                {incident.title.includes('WordPress') && '攻击者试图利用 updatexml 函数执行 SQL 注入操作以获取数据库用户信息。从返回包判断，攻击者已经成功获取用户信息。相关漏洞“Wordpress -develop 等产品 SQL 注入漏洞”（CVE-2022-21661）。'}
              </p>

              {isPhishing ? (
                <>
                  <ReportHeading>微步情报命中</ReportHeading>
                  <div className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm leading-7 text-gray-800">
                    <div><span className="font-semibold text-gray-950">命中 URL：</span>hxxps://invoice-check.example/login</div>
                    <div><span className="font-semibold text-gray-950">情报来源：</span>微步在线威胁情报</div>
                    <div><span className="font-semibold text-gray-950">情报标签：</span>钓鱼 URL、凭证采集、仿冒供应商对账登录页</div>
                    <div><span className="font-semibold text-gray-950">风险说明：</span>该 URL 被标记为钓鱼登录页，风险判断主要来自情报标签；请求体和响应体只作为访问事实补充，不作为核心判据。</div>
                  </div>
                  <ReportHeading>访问事实</ReportHeading>
                  <p className="text-sm leading-7">
                    NDR 记录到财务网段终端 {incident.srcIp} 访问上述钓鱼 URL。当前告警没有复杂攻击 payload，响应体内容也不是判断重点；需要优先确认访问者身份、邮件投递来源以及是否发生凭证提交。
                  </p>
                </>
              ) : (
                <>
                  <ReportHeading>攻击payload</ReportHeading>
                  <ReportCodeBlock code={incident.request.payload} />
                  <p className="mt-4 text-sm font-semibold leading-7">具体含义解释：</p>
                  <ol className="mt-2 list-decimal space-y-2 pl-6 text-sm leading-7">
                    {getPayloadNotes(incident).map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ol>

                  <p className="mt-5 text-sm font-semibold leading-7">{getResponseIntro(incident)}</p>
                  <ReportCodeBlock code={getResponseExample(incident)} />
                  <p className="mt-4 text-sm leading-7">{incident.request.llmAnalysis}</p>
                  <p className="mt-3 text-sm leading-7">{incident.response.llmAnalysis}</p>
                </>
              )}

              <ReportHeading>重要证据</ReportHeading>
              {isPhishing ? (
                <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                  <li>微步情报显示访问 URL hxxps://invoice-check.example/login 被标记为钓鱼 URL，标签包含凭证采集和仿冒供应商对账登录页。</li>
                  <li>NDR 记录到源终端 {incident.srcIp} 访问该 URL，说明企业内部用户已经触达风险站点。</li>
                  <li>源终端上下文显示该地址属于财务网段，{incident.srcIntel.summary}</li>
                  <li>该告警需要和深度调查 INV-2026-0522-001 的邮件网关、EDR、OA 证据关联，确认邮件投递、点击用户和凭证提交风险。</li>
                </ol>
              ) : (
                <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                  <li>关联信息：{incident.asset.name} 的日志与 NDR 流量中出现非预期请求，命中规则 {incident.ndrRule}，请求证据包含 {incident.request.evidence.join('、')}。</li>
                  <li>威胁情报显示 {incident.srcIp} 判定为{incident.srcIntel.verdict}，{incident.srcIntel.summary}</li>
                  <li>资产信息显示目标为 {incident.asset.business}，暴露面为{incident.asset.exposure}，{incident.asset.context}</li>
                  <li>响应分析显示状态码为 {incident.response.statusCode}，关键证据包括 {incident.response.evidence.join('、')}。</li>
                </ol>
              )}

              <ReportHeading>处置建议</ReportHeading>
              <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                {incident.actions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
                <li>{incident.conclusion.recommendation}</li>
              </ol>

              {isSuccess && (
                <div className="pointer-events-none absolute bottom-4 right-4 rotate-[-12deg] rounded-full border-[4px] border-red-500/50 px-6 py-5 text-2xl font-black text-red-500/60">
                  攻击成功
                </div>
              )}
            </article>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            关闭
          </button>
          <button className="rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700">
            转入深度调查
          </button>
        </div>
      </aside>
    </div>
  );
}

function ReportHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-7 text-lg font-bold text-gray-950">{children}</h2>;
}

function ReportCodeBlock({ code }: { code: string }) {
  return (
    <div className="mt-3 overflow-hidden rounded-md bg-gray-100">
      <div className="flex justify-end bg-gray-100 px-3 py-2 text-xs font-medium text-gray-900">复制</div>
      <pre className="overflow-x-auto whitespace-pre-wrap bg-gray-50 px-4 py-3 font-mono text-sm leading-6 text-gray-700">
        {code}
      </pre>
    </div>
  );
}

function buildAnalysisSteps(incident: IncidentCluster) {
  const firstSql = incident.title.includes('WordPress');
  const phishing = incident.title.includes('钓鱼');
  if (phishing) {
    return [
      {
        title: 'NDR 访问事实确认',
        content: `NDR 记录到源终端 ${incident.srcIp} 访问 ${incident.request.payload}。该告警没有复杂攻击 payload，网络侧核心价值是确认企业内部终端已经触达外部风险 URL。`,
      },
      {
        title: '微步 URL 情报命中',
        content: '将访问 URL 提交微步在线威胁情报查询后，返回钓鱼 URL 标签，威胁类型为 credential-phishing，场景为 fake-invoice-portal，说明该地址被用于仿冒供应商对账登录页收集账号密码。',
      },
      {
        title: '源终端身份确认',
        content: `${incident.srcIp} 位于办公网财务网段，关联用户 li.yan。用户为财务实习生，入职时间短，和供应商对账主题具备业务诱导相关性。`,
      },
      {
        title: '邮件点击链路关联',
        content: '该 URL 情报命中需要继续关联邮件网关与终端侧证据，确认是否存在同主题邮件投递、Outlook 拉起浏览器访问、以及是否还有其他收件人触达同一钓鱼 URL。',
      },
      {
        title: '研判结论',
        content: `核心证据为微步钓鱼 URL 标签和 NDR 访问事实。请求体与响应体不是主要判定依据，当前结论为：${incident.conclusion.verdict}。建议转入深度调查并执行账号冻结、密码重置和同主题邮件收件人检索。`,
      },
    ];
  }
  return [
    {
      title: '日志类型分析',
      content: `该告警来自 NDR Web 日志，日志中包含源 IP、目标资产、HTTP 方法、请求路径、请求参数、响应状态码和响应体摘要，已经具备进行单条告警研判所需的关键字段。当前请求为 ${incident.request.method} ${incident.request.host}${incident.request.uri}。`,
    },
    {
      title: '情报信息',
      content: `${incident.srcIp} 被判定为${incident.srcIntel.verdict}，归属 ${incident.srcIntel.location}，标签为 ${incident.srcIntel.tags.join('、')}。${incident.srcIntel.summary}`,
    },
    {
      title: '测绘信息',
      content: `${incident.asset.name} 为${incident.asset.exposure}暴露的${incident.asset.business}资产，责任组为${incident.asset.owner}，资产重要性为${incident.asset.criticality}。${incident.asset.context}`,
      code: firstSql
        ? 'N\\x00\\x00\\x00\\x0a5.6.50-log\\x00\\xb5`\\x12\\x00,~Y$CfYk\\x00\\xff\\xf7-\\x02\\x00\\x7f\\x80\\x15\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00/EM5^[.qqbxS\\x00mysql_native_password\\x00\n\nVersion: 5.6.50-log\nThread ID:1204405\nServer Capabilities:0xf7ff\nServer Language:45\nServer Status:0x0002'
        : undefined,
    },
    {
      title: '告警关联漏洞情报',
      content: firstSql
        ? '关联漏洞为 Wordpress 等产品 SQL 注入漏洞（CVE-2022-21661），风险等级高。攻击负载 updatexml(0x7e,concat(1,user()),0x7e) 与公开 PoC 的技术细节一致，极可能被成功利用。'
        : `当前告警命中 ${incident.ndrRule}，攻击手法与 ${incident.title} 场景一致，结合源 IP 情报和目标资产暴露面，需要按照 ${incident.priority} 级别进行处置。`,
    },
    {
      title: '攻击负载分析',
      content: incident.request.llmAnalysis,
      code: incident.request.payload,
    },
    {
      title: '攻击分析结果',
      content: `响应状态码为 ${incident.response.statusCode}。${incident.response.llmAnalysis} 结合请求侧证据和响应侧证据，Rex 给出的最终结论为：${incident.conclusion.verdict}。`,
      code: getResponseExample(incident),
    },
  ];
}

function getReportTitle(incident: IncidentCluster) {
  if (incident.title.includes('SQL')) return 'SQL注入攻击分析报告';
  if (incident.title.includes('钓鱼')) return '钓鱼URL情报命中分析报告';
  if (incident.title.includes('Log4Shell')) return 'Log4Shell漏洞利用分析报告';
  if (incident.title.includes('命令注入')) return '命令注入攻击分析报告';
  if (incident.title.includes('远控')) return 'WebShell远控执行分析报告';
  if (incident.title.includes('WebShell')) return 'WebShell上传攻击分析报告';
  return `${incident.title}分析报告`;
}

function getPayloadNotes(incident: IncidentCluster) {
  if (incident.title.includes('WordPress')) {
    return [
      'updatexml() 是 MySQL 的 XML 处理函数，用于更新 XML 数据，其参数分别为 XML 文档、XPath 表达式、新值。',
      '0x7e 为 16 进制的波浪符号 ~，一般用于标记异常以便容易识别。',
      'concat(1,user()) 是拼接函数，目的是将数据库用户名暴露出来。',
    ];
  }
  if (incident.title.includes('钓鱼')) {
    return [
      '该告警没有复杂攻击 payload，核心证据来自 URL 情报、访问对象和页面特征。',
      '/login 表示用户访问的是登录入口，页面出现账号、密码表单时，应优先判断是否存在凭证收集风险。',
      '源终端属于财务用户且浏览器由邮件客户端拉起，和邮件钓鱼投递链路高度一致，需要进入深度调查补齐 EDR、邮件网关和 OA 证据。',
    ];
  }
  if (incident.title.includes('Log4Shell')) {
    return [
      '${jndi:ldap://...} 是 Log4Shell 漏洞利用中最典型的 JNDI 查找表达式，服务端日志组件解析后会尝试访问外部 LDAP 服务。',
      '1389 是攻击者常用于承载恶意 LDAP 服务的端口，目标资产出现对该端口的回连是高价值成功利用证据。',
      'Exploit.class 表示攻击链可能进入远程类加载阶段，需要重点确认 JVM 是否加载恶意类以及主机是否出现后续命令执行行为。',
    ];
  }
  if (incident.title.includes('命令注入')) {
    return [
      '分号是常见 shell 命令连接符，可让后端在原始命令之后继续执行攻击者追加的命令。',
      'whoami 用于探测当前命令执行身份，常作为命令注入验证 payload。',
      'ping 工具类接口若直接拼接用户输入到系统命令，容易形成命令执行漏洞。',
    ];
  }
  if (incident.title.includes('远控')) {
    return [
      '上传目录下的 JSP 文件不应作为业务入口出现，若可被外部 POST 访问并接受 cmd 参数，说明存在已落地 WebShell。',
      'id 和 uname -a 常用于确认当前执行用户、主机名和系统版本，是攻击者建立远控后进行环境探测的典型命令。',
      'curl http://...|sh 表示攻击者尝试下载并直接执行二阶段脚本，风险从单点 WebShell 扩大到主机级持久化或横向移动。',
    ];
  }
  return [
    'shell.php 文件名与 PHP 脚本执行环境高度相关，属于高风险上传对象。',
    'system($_GET["cmd"]) 会执行外部传入的 cmd 参数，是典型 WebShell 行为。',
    'multipart/form-data 上传接口如果缺少后缀、内容和存储路径限制，容易导致脚本落盘执行。',
  ];
}

function getResponseExample(incident: IncidentCluster) {
  if (incident.title.includes('WordPress')) return "XPATH syntax error: '~root@localhost~'";
  if (incident.title.includes('钓鱼')) return 'HTTP/1.1 200 OK\n<title>Invoice Verification Portal</title>\n<form action="/session" method="post">\n  <input name="account">\n  <input name="password" type="password">\n</form>';
  if (incident.title.includes('Log4Shell')) return 'HTTP/1.1 200 OK\n\n[ndr] outbound ldap connection: shop-api.example.com -> 45.83.12.21:1389\n[ndr] follow-up request: GET /Exploit.class';
  if (incident.title.includes('命令注入')) return 'HTTP/1.1 200 OK\nPING 127.0.0.1 ...\nwww-data';
  if (incident.title.includes('远控')) return 'HTTP/1.1 200 OK\nuid=1001(www-data) gid=1001(www-data) groups=1001(www-data)\nLinux cms-prod-02 5.10.0-23-amd64 x86_64\n[ndr] outbound: cms-prod-02 -> 185.220.101.47:8080/stage.sh';
  return 'HTTP/1.1 200 OK\n{"message":"upload success","url":"/uploads/2026/06/shell.php"}';
}

function getResponseIntro(incident: IncidentCluster) {
  if (incident.title.includes('钓鱼')) return '页面返回中可看到：';
  if (incident.conclusion.verdict.includes('成功') || incident.conclusion.verdict.includes('成立')) return '攻击成功，回包中会看到：';
  return '响应证据中可看到：';
}

function InvestigationResult() {
  const [selectedInvestigation, setSelectedInvestigation] = useState<DeepInvestigation | null>(null);

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {[
                ['调查 ID', 'w-[150px]'],
                ['优先级', 'w-[80px]'],
                ['调查主题', 'min-w-[340px]'],
                ['跨设备证据', 'min-w-[260px]'],
                ['状态', 'w-[90px]'],
                ['责任组', 'w-[120px]'],
                ['推荐动作', 'w-[90px]'],
              ].map(([header, widthClass]) => (
                <th key={header} className={`${widthClass} px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500`}>
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {deepInvestigations.map((item) => (
              <tr
                key={item.id}
                onClick={() => setSelectedInvestigation(item)}
                className="cursor-pointer transition-colors hover:bg-red-50/50"
              >
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{item.id}</td>
                <td className="px-4 py-3"><Badge tone={item.severity === 'P1' ? 'red' : 'orange'}>{item.severity}</Badge></td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm font-medium text-gray-900">{item.title}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm text-gray-600">
                    {item.entities.join(' / ')}
                  </div>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{item.status}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{item.owner}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-red-600">查看详情</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex justify-end">
        <Link
          to="/soc/alerts?mode=configure"
          className="inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          用我的数据配置告警 SOP
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      {selectedInvestigation && (
        <InvestigationDrawer investigation={selectedInvestigation} onClose={() => setSelectedInvestigation(null)} />
      )}
    </>
  );
}

function InvestigationDrawer({ investigation, onClose }: { investigation: DeepInvestigation; onClose: () => void }) {
  const [showAgentSession, setShowAgentSession] = useState(false);

  return (
    <div className="fixed inset-0 z-[70]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭深度调查详情"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-2/3">
        <div className="flex items-start justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={investigation.severity === 'P1' ? 'red' : 'orange'}>{investigation.severity}</Badge>
              <Badge tone="blue">{investigation.id}</Badge>
              <Badge tone="slate">{investigation.status}</Badge>
            </div>
            <h2 className="mt-3 text-xl font-semibold text-gray-900">{investigation.title}</h2>
            <p className="mt-1 text-sm leading-6 text-gray-500">{investigation.summary}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              ['责任组', investigation.owner],
              ['涉及设备', investigation.entities.join(' / ')],
              ['证据数量', `${investigation.evidence.length} 条`],
              ['推荐处置', investigation.recommendation],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="text-xs text-gray-500">{label}</div>
                <div className="mt-1 text-sm font-semibold leading-5 text-gray-900">{value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
            <div>
              <div className="mb-3 flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-red-600" />
                <h3 className="font-semibold text-gray-900">跨设备证据链</h3>
              </div>
              <div className="space-y-3">
                {investigation.evidence.map((item) => (
                  <div key={`${item.source}-${item.time}`} className="rounded-lg border border-gray-200 p-4">
                    <div className="mb-2 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Badge tone="blue">{item.source}</Badge>
                        <span className="text-sm font-semibold text-red-600">{item.time}</span>
                      </div>
                    </div>
                    <p className="text-sm leading-6 text-gray-600">{item.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-3 flex items-center gap-2">
                <Bot className="h-5 w-5 text-red-600" />
                <h3 className="font-semibold text-gray-900">Rex 调查结论</h3>
              </div>
              <div className="rounded-xl border border-red-100 bg-red-50 p-4">
                <div className="font-semibold text-gray-900">多源证据已形成闭环</div>
                <p className="mt-2 text-sm leading-6 text-gray-600">
                  该调查同时关联终端、网络、邮件和身份系统，能够说明攻击路径、受影响用户和建议处置动作。
                </p>
              </div>
              <div className="mt-4 rounded-xl border border-gray-200 p-4">
                <div className="font-semibold text-gray-900">处置建议</div>
                <p className="mt-2 text-sm leading-6 text-gray-600">{investigation.recommendation}</p>
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">
          <button
            type="button"
            onClick={() => setShowAgentSession(true)}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <MessageSquare className="h-4 w-4" />
            查看 Agent 调查过程
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            关闭
          </button>
          <button className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
            生成调查报告
          </button>
        </div>
      </aside>

      {showAgentSession && (
        <AgentSessionDrawer investigation={investigation} onClose={() => setShowAgentSession(false)} />
      )}
    </div>
  );
}

function getInvestigationSessionMessages(investigation: DeepInvestigation): InvestigationSessionMessage[] {
  const firstEvidence = investigation.evidence[0];
  const laterEvidence = investigation.evidence.slice(1);
  const lastEvidence = laterEvidence.length > 0 ? laterEvidence[laterEvidence.length - 1] : firstEvidence;

  return [
    {
      role: 'user',
      sender: '值班分析员',
      time: firstEvidence?.time ?? '10:00',
      content: `请围绕 ${investigation.title} 做深度调查，补齐跨设备证据并给出处置建议。`,
    },
    {
      role: 'assistant',
      sender: 'Rex',
      time: firstEvidence?.time ?? '10:01',
      content: `先从 ${firstEvidence?.source ?? '首个证据源'} 验证告警是否成立，再逐步关联 ${investigation.entities.join('、')} 的上下文。`,
    },
    ...investigation.evidence.flatMap((item, index): InvestigationSessionMessage[] => [
      {
        role: 'delegate',
        sender: 'Rex',
        time: item.time,
        content: `查询 ${item.source}，确认第 ${index + 1} 个证据点。`,
        delegate: {
          title: `${item.source} 调查`,
          description: `${item.source} 证据检索和上下文补全`,
          status: 'completed',
          elapsed: index % 2 === 0 ? '42s' : '1m08s',
          steps: 3,
        },
      },
      {
        role: 'tool',
        sender: `${item.source} 调查`,
        time: item.time,
        content: item.detail,
        toolCalls: [
          {
            name: `${item.source}.query_evidence`,
            target: investigation.id,
            status: 'success',
            result: item.detail,
          },
        ],
        conclusion: item.detail,
      },
    ]),
    {
      role: 'assistant',
      sender: 'Rex',
      time: lastEvidence?.time ?? firstEvidence?.time ?? '10:05',
      content: `多源证据已完成关联：${investigation.summary} 建议：${investigation.recommendation}`,
    },
  ];
}

function AgentSessionDrawer({ investigation, onClose }: { investigation: DeepInvestigation; onClose: () => void }) {
  const messages = getInvestigationSessionMessages(investigation);

  return (
    <div className="fixed inset-0 z-[80]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭 Agent 调查过程"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-2/3">
        <div className="flex items-start justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <MessageSquare className="h-5 w-5 text-red-600" />
              <h2 className="text-lg font-semibold text-gray-900">Agent 调查过程</h2>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              场景会话：围绕 {investigation.title} 关联 {investigation.entities.join('、')} 证据。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto bg-gray-50 px-5 py-4">
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
            {messages.map((message, index) => (
              <MockSessionMessage key={`${message.time}-${message.sender}-${index}`} message={message} />
            ))}
          </div>
        </div>

        <div className="border-t border-gray-200 bg-white px-5 py-4">
          <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-500">
            <Bot className="h-4 w-4 text-red-600" />
            场景会话已完成：可继续追问证据细节，或生成处置工单和调查报告。
          </div>
        </div>
      </aside>
    </div>
  );
}

function MockSessionMessage({ message }: { message: InvestigationSessionMessage }) {
  const isUser = message.role === 'user';
  const isDelegate = message.role === 'delegate';
  const hasToolCalls = Boolean(message.toolCalls?.length);

  return (
    <div className={`border-b border-gray-100 px-4 py-3 last:border-b-0 ${isUser ? 'flex justify-end' : ''}`}>
      <div
        className={`${isDelegate || hasToolCalls ? 'w-full max-w-[82%]' : 'w-fit max-w-[82%]'} ${
          isUser
            ? 'rounded-2xl bg-slate-900 px-4 py-3 text-white'
            : 'text-gray-800'
        }`}
      >
        <div className={`mb-2 flex items-center gap-2 text-xs ${isUser ? 'text-slate-200' : 'text-gray-500'}`}>
          <span className="font-semibold">{message.sender}</span>
          <span>{message.time}</span>
        </div>
        <p className="text-sm leading-6">{message.content}</p>

        {message.delegate && (
          <div className="mt-2 w-full border-l-4 border-emerald-500 bg-emerald-50/70 px-3 py-2">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-purple-600 text-xs font-bold text-white">
                  {message.delegate.title.slice(0, 1)}
                </div>
                <div>
                  <div className="text-sm font-semibold text-gray-900">{message.delegate.title}</div>
                  <div className="text-xs text-gray-600">{message.delegate.description}</div>
                  <div className="mt-1 flex flex-wrap gap-3 text-xs text-gray-500">
                    <span>耗时 {message.delegate.elapsed}</span>
                    <span>{message.delegate.steps} 步</span>
                  </div>
                </div>
              </div>
              <Badge tone="green">已完成</Badge>
            </div>
            <div className="mt-2 flex items-center gap-1 text-xs font-medium text-red-600">
              查看对话框
              <ChevronRight className="h-3.5 w-3.5" />
            </div>
          </div>
        )}

        {message.toolCalls && (
          <div className="mt-2 divide-y divide-gray-100 border-l-2 border-gray-200 pl-3">
            {message.toolCalls.map((tool) => (
              <div
                key={`${tool.name}-${tool.target}`}
                className={`py-2 ${
                  tool.status === 'success'
                    ? 'bg-green-50/60'
                    : 'bg-red-50/60'
                }`}
              >
                <div className="flex items-center justify-between gap-3 px-2">
                  <div className="flex min-w-0 items-center gap-2">
                    {tool.status === 'success'
                      ? <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-green-600" />
                      : <XCircle className="h-4 w-4 flex-shrink-0 text-red-600" />}
                    <span className="truncate text-sm font-semibold text-gray-900">{tool.name}</span>
                    <span className="truncate text-xs text-gray-500">{tool.target}</span>
                  </div>
                  <span className={`text-xs font-medium ${tool.status === 'success' ? 'text-green-700' : 'text-red-700'}`}>
                    {tool.status === 'success' ? '已完成' : '失败'}
                  </span>
                </div>
                <p className="mt-1 px-2 pl-8 text-xs leading-5 text-gray-600">{tool.result}</p>
              </div>
            ))}
          </div>
        )}

        {message.conclusion && (
          <div className="mt-2 px-1">
            <p className="text-sm leading-6 text-gray-800">{message.conclusion}</p>
          </div>
        )}
      </div>
    </div>
  );
}
