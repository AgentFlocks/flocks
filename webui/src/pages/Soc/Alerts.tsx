import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, ChevronRight, GitBranch, MessageSquare, Search, X, XCircle } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, ConfigWorkshop, ModeSwitch } from './components';
import {
  agentInvestigationMessages,
  deepInvestigations,
  incidentClusters,
  sharedStory,
  threatTimeline,
} from './mockData';

type IncidentCluster = typeof incidentClusters[number];
type DeepInvestigation = typeof deepInvestigations[number];

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
  const [activeTab, setActiveTab] = useState<'triage' | 'investigation'>('triage');

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
          {[
            ['9836', '原始告警', '来自 SIEM/NDR/WAF/EDR'],
            ['1023', '降噪后告警', '去重、合并、压制误报'],
            ['102', 'Rex 初步研判', '补全情报和资产上下文'],
            ['13', '深度调查', '需要多 Agent 协作调查'],
            ['3', '处置建议', '工单、隔离、复测'],
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
              active={activeTab === 'triage'}
              onClick={() => setActiveTab('triage')}
              icon={<Search className="h-4 w-4" />}
              label="研判告警"
            />
            <TabButton
              active={activeTab === 'investigation'}
              onClick={() => setActiveTab('investigation')}
              icon={<Bot className="h-4 w-4" />}
              label="深度调查"
            />
          </div>
          {activeTab === 'triage' && <Badge tone="red">102 条待研判</Badge>}
        </div>

        <div className="p-3">
          {activeTab === 'triage' ? <TriageResult /> : <InvestigationResult />}
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

function TriageResult() {
  const [selectedIncident, setSelectedIncident] = useState<IncidentCluster | null>(null);

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {['事件 ID', '优先级', '事件标题', '原始告警', '置信度', '责任组', '推荐动作'].map((header) => (
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
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{cluster.rawAlerts} 条</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{cluster.confidence}%</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{cluster.owner}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-red-600">查看详情</td>
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
  return (
    <div className="fixed inset-0 z-[70]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭告警详情"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-2/3">
        <div className="flex items-start justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={incident.priority === 'P1' ? 'red' : 'orange'}>{incident.priority}</Badge>
              <Badge tone="blue">{incident.id}</Badge>
            </div>
            <h2 className="mt-3 text-xl font-semibold text-gray-900">{incident.title}</h2>
            <p className="mt-1 text-sm text-gray-500">{incident.reason}</p>
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
              ['原始告警', `${incident.rawAlerts} 条`],
              ['聚合置信度', `${incident.confidence}%`],
              ['责任组', incident.owner],
              ['目标资产', sharedStory.asset],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="text-xs text-gray-500">{label}</div>
                <div className="mt-1 text-sm font-semibold text-gray-900">{value}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[0.9fr_1.1fr]">
            <div>
              <div className="mb-3 flex items-center gap-2">
                <Search className="h-5 w-5 text-red-600" />
                <h3 className="font-semibold text-gray-900">Rex 研判结论</h3>
              </div>
              <div className="rounded-xl border border-red-100 bg-red-50 p-4">
                <div className="font-semibold text-gray-900">建议升级为 P1 深度调查</div>
                <p className="mt-2 text-sm leading-6 text-gray-600">
                  {sharedStory.asset} 的外联、WAF 探测和微步情报命中处于同一攻击窗口，建议先隔离管理面访问，并启动深度调查。
                </p>
              </div>
              <div className="mt-4 rounded-xl border border-gray-200 p-4">
                <div className="font-semibold text-gray-900">推荐动作</div>
                <div className="mt-3 grid grid-cols-1 gap-2 text-sm text-gray-600">
                  {['进入深度调查', '拉取 WAF/NDR/主机日志', '关联漏洞排查任务', '生成值班工单'].map((item) => (
                    <div key={item} className="rounded-lg bg-gray-50 px-3 py-2">{item}</div>
                  ))}
                </div>
              </div>
            </div>

            <div>
              <div className="mb-3 flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-red-600" />
                <h3 className="font-semibold text-gray-900">事件时间线</h3>
              </div>
              <div className="space-y-4">
                {threatTimeline.map((item) => (
                  <div key={item.time} className="flex gap-3">
                    <div className="w-12 shrink-0 text-sm font-semibold text-red-600">{item.time}</div>
                    <div className="border-l border-gray-200 pl-3">
                      <div className="text-sm font-medium text-gray-900">{item.title}</div>
                      <div className="mt-1 text-sm leading-5 text-gray-500">{item.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            关闭
          </button>
          <button className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
            转入深度调查
          </button>
        </div>
      </aside>
    </div>
  );
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
                ['跨设备证据', 'min-w-[280px]'],
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
                  <div className="flex flex-nowrap gap-1 overflow-hidden">
                    {item.entities.map((entity) => (
                      <Badge key={entity} tone="blue">{entity}</Badge>
                    ))}
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
        <AgentSessionDrawer onClose={() => setShowAgentSession(false)} />
      )}
    </div>
  );
}

function AgentSessionDrawer({ onClose }: { onClose: () => void }) {
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
              mock 会话：Rex 调度 NDR、EDR、邮件网关和 OA 上下文 Agent 完成调查。
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
            {agentInvestigationMessages.map((message, index) => (
              <MockSessionMessage key={`${message.time}-${message.sender}-${index}`} message={message} />
            ))}
          </div>
        </div>

        <div className="border-t border-gray-200 bg-white px-5 py-4">
          <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-500">
            <Bot className="h-4 w-4 text-red-600" />
            mock 会话已完成：可继续追问“哪些用户还收到同主题邮件？”或“生成处置工单”。
          </div>
        </div>
      </aside>
    </div>
  );
}

function MockSessionMessage({ message }: { message: typeof agentInvestigationMessages[number] }) {
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
