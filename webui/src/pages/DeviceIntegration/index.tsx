import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, Link2, ServerCog, ShieldCheck, TerminalSquare } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Card, Badge } from '@/pages/Soc/components';

type DimensionStatus = '已完成' | '进行中' | '待配置' | '不适用';
type HealthStatus = '在线' | '异常' | '未配置';

interface IntegrationDimension {
  status: DimensionStatus;
  summary: string;
  detail: string;
}

interface SourceRow {
  id: string;
  category: '设备' | '数据源';
  sourceType: 'firewall' | 'ndr' | 'hids' | 'siem' | 'ti' | 'asset' | 'ticket';
  name: string;
  vendor: string;
  product: string;
  region: string;
  version: string;
  health: HealthStatus;
  lastSync: string;
  connectivity: string;
  objects: string[];
  workflows: string[];
  api: IntegrationDimension;
  web2cli: IntegrationDimension;
  skill: IntegrationDimension;
}

const sources: SourceRow[] = [
  {
    id: 'FW-HZ-DMZ-01',
    category: '设备',
    sourceType: 'firewall',
    name: 'FW-HZ-DMZ-01',
    vendor: '深信服',
    product: 'AF-1000',
    region: '杭州机房 / DMZ',
    version: 'v6.2.1',
    health: '在线',
    lastSync: '17:50',
    connectivity: '通过',
    objects: ['alert', 'asset', 'evidence', 'action'],
    workflows: ['告警降噪', '设备健康巡检'],
    api: { status: '已完成', summary: '21 个接口', detail: '鉴权与连通性校验通过，告警与健康接口已纳入调用池。' },
    web2cli: { status: '已完成', summary: '2 个动作', detail: 'License 到期和策略命中详情已录制为可执行动作。' },
    skill: { status: '已完成', summary: '3 个巡检 Skill', detail: '健康巡检、规则版本核验、License 到期检查已发布。' },
  },
  {
    id: 'NDR-HZ-CORE-01',
    category: '设备',
    sourceType: 'ndr',
    name: 'NDR-HZ-CORE-01',
    vendor: '奇安信',
    product: 'NDR-X5',
    region: '杭州机房 / 核心区',
    version: 'v3.8.4',
    health: '在线',
    lastSync: '17:48',
    connectivity: '通过',
    objects: ['alert', 'entity', 'evidence'],
    workflows: ['深度调查', '规则核验'],
    api: { status: '已完成', summary: '18 个接口', detail: '威胁事件、规则同步和会话检索 API 已完成接入。' },
    web2cli: { status: '进行中', summary: '1 个动作录制中', detail: '管理台规则版本页面结构已识别，等待稳定路径确认。' },
    skill: { status: '进行中', summary: '2 个 Skill 已发布', detail: '规则核验 Skill 已上线，异常外联巡检 Skill 正在联调。' },
  },
  {
    id: 'HIDS-BJ-CLUSTER',
    category: '设备',
    sourceType: 'hids',
    name: 'HIDS-BJ-CLUSTER',
    vendor: '青藤',
    product: 'HIDS-Cluster',
    region: '北京机房 / 服务器区',
    version: 'v2.4.7',
    health: '异常',
    lastSync: '17:41',
    connectivity: '超时',
    objects: ['alert', 'asset', 'entity'],
    workflows: ['主机基线巡检'],
    api: { status: '进行中', summary: '12 个接口', detail: 'Token 已验证，资产基线与告警接口正在补齐字段映射。' },
    web2cli: { status: '待配置', summary: '0 个动作', detail: '尚未开始页面动作录制。' },
    skill: { status: '待配置', summary: '0 个 Skill', detail: '等待 API 与 web2cli 能力稳定后生成巡检 Skill。' },
  },
  {
    id: 'SIEM-SPLUNK-HQ',
    category: '数据源',
    sourceType: 'siem',
    name: 'SPLUNK-HQ',
    vendor: 'Splunk',
    product: 'SIEM',
    region: '总部 / 安全平台区',
    version: 'v9.2.0',
    health: '在线',
    lastSync: '17:52',
    connectivity: '通过',
    objects: ['alert', 'evidence', 'entity'],
    workflows: ['告警聚合', '事件簇构建'],
    api: { status: '已完成', summary: '13 个查询接口', detail: '日志查询、告警检索与上下文补全接口均已启用。' },
    web2cli: { status: '不适用', summary: '不适用', detail: '标准 SIEM 数据源通过 API 拉取，无需 web2cli。' },
    skill: { status: '已完成', summary: '2 个研判 Skill', detail: 'SIEM 字段归一化与告警聚合 Skill 已启用。' },
  },
  {
    id: 'TI-THREATBOOK',
    category: '数据源',
    sourceType: 'ti',
    name: 'ThreatBook-IOC',
    vendor: '微步',
    product: '威胁情报',
    region: '云侧 / 全球',
    version: '2026.05',
    health: '在线',
    lastSync: '17:53',
    connectivity: '通过',
    objects: ['entity', 'evidence', 'action'],
    workflows: ['IOC 富化', '情报摘要推送'],
    api: { status: '已完成', summary: '8 个情报接口', detail: 'IOC、IP、域名、URL 和样本查询已稳定可用。' },
    web2cli: { status: '不适用', summary: '不适用', detail: '情报数据源通过 API 与 MCP 提供能力，无需 web2cli。' },
    skill: { status: '进行中', summary: '1 个 Skill 已发布', detail: '情报富化 Skill 已启用，场景推荐 Skill 正在打磨。' },
  },
];

const dimensionConfig = {
  api: { label: '设备 API 接入', icon: Link2 },
  web2cli: { label: '设备 web2cli', icon: TerminalSquare },
  skill: { label: '设备 Skill', icon: ShieldCheck },
} as const;

type DimensionKey = keyof typeof dimensionConfig;
type ScopeKey = 'all' | 'source' | 'device';

function dimensionTone(status: DimensionStatus): 'green' | 'orange' | 'slate' {
  if (status === '已完成') return 'green';
  if (status === '进行中' || status === '待配置') return 'orange';
  return 'slate';
}

export default function DeviceIntegrationPage() {
  const [params, setParams] = useSearchParams();
  const requestedScope = params.get('scope');
  const requestedView = params.get('view');
  const activeScope: ScopeKey = requestedScope === 'source' || requestedScope === 'device' ? requestedScope : 'all';
  const activeDimension: DimensionKey = requestedView === 'web2cli' || requestedView === 'skill' ? requestedView : 'api';
  const activeConfig = dimensionConfig[activeDimension];
  const ActiveIcon = activeConfig.icon;

  const visibleSources = useMemo(
    () => sources.filter((item) => (activeScope === 'all' ? true : activeScope === 'source' ? item.category === '数据源' : item.category === '设备')),
    [activeScope],
  );

  const dimensionStats = useMemo(() => {
    const stats = { done: 0, inProgress: 0, pending: 0 };
    for (const source of visibleSources) {
      const state = source[activeDimension].status;
      if (state === '已完成') stats.done += 1;
      else if (state === '进行中') stats.inProgress += 1;
      else if (state === '待配置') stats.pending += 1;
    }
    return stats;
  }, [activeDimension, visibleSources]);

  const overview = useMemo(() => {
    const online = visibleSources.filter((item) => item.health === '在线').length;
    const degraded = visibleSources.filter((item) => item.health === '异常').length;
    const unconfigured = visibleSources.filter((item) => item.health === '未配置').length;
    return { total: visibleSources.length, online, degraded, unconfigured };
  }, [visibleSources]);

  const updateQuery = (view: DimensionKey, scope = activeScope) => {
    const next = new URLSearchParams(params);
    next.set('scope', scope);
    next.set('view', view);
    setParams(next);
  };

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="数据源与设备"
        description="统一管理安全数据源和设备接入能力，并为每个实例维护 API、web2cli、设备 Skill 三个维度。"
        icon={<ServerCog className="h-8 w-8" />}
        action={(
          <Link
            to="/soc/inspections"
            className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            去 SOC 查看设备巡检
            <ArrowRight className="h-4 w-4" />
          </Link>
        )}
      />

      <div className="space-y-5">
        <Card>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
              <div className="text-xs text-blue-700">已接入总数</div>
              <div className="mt-1 text-2xl font-semibold text-blue-900">{overview.total}</div>
            </div>
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3">
              <div className="text-xs text-green-700">在线</div>
              <div className="mt-1 text-2xl font-semibold text-green-900">{overview.online}</div>
            </div>
            <div className="rounded-lg border border-orange-200 bg-orange-50 px-4 py-3">
              <div className="text-xs text-orange-700">异常</div>
              <div className="mt-1 text-2xl font-semibold text-orange-900">{overview.degraded}</div>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="text-xs text-slate-700">未配置</div>
              <div className="mt-1 text-2xl font-semibold text-slate-900">{overview.unconfigured}</div>
            </div>
          </div>
          <div className="mb-3 flex items-center justify-between text-xs text-gray-500">
            <span>最近同步：{visibleSources[0]?.lastSync ?? '-'}</span>
            <span>最近连通性检测：{visibleSources[0]?.connectivity ?? '-'}</span>
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex flex-wrap gap-2">
            {[
              { key: 'all' as ScopeKey, label: '全部' },
              { key: 'source' as ScopeKey, label: '数据源' },
              { key: 'device' as ScopeKey, label: '设备' },
            ].map((scope) => {
              const isActive = scope.key === activeScope;
              return (
                <button
                  key={scope.key}
                  type="button"
                  onClick={() => updateQuery(activeDimension, scope.key)}
                  className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? 'border-slate-900 bg-slate-900 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  {scope.label}
                </button>
              );
            })}
          </div>
          <div className="mb-3 flex flex-wrap gap-2">
            {(Object.keys(dimensionConfig) as DimensionKey[]).map((key) => {
              const Icon = dimensionConfig[key].icon;
              const isActive = key === activeDimension;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => updateQuery(key)}
                  className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? 'border-slate-900 bg-slate-900 text-white'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {dimensionConfig[key].label}
                </button>
              );
            })}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3">
              <div className="text-xs text-green-700">已完成</div>
              <div className="mt-1 text-2xl font-semibold text-green-900">{dimensionStats.done}</div>
            </div>
            <div className="rounded-lg border border-orange-200 bg-orange-50 px-4 py-3">
              <div className="text-xs text-orange-700">进行中</div>
              <div className="mt-1 text-2xl font-semibold text-orange-900">{dimensionStats.inProgress}</div>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="text-xs text-slate-700">待配置</div>
              <div className="mt-1 text-2xl font-semibold text-slate-900">{dimensionStats.pending}</div>
            </div>
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <ServerCog className="h-4 w-4 text-red-600" />
            <h3 className="text-sm font-semibold text-gray-900">数据源列表</h3>
          </div>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-[1260px] divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {['类型', '实例', '厂商/产品', '部署位置', '状态', '版本', '最近同步', '连通性', 'API 接入', 'web2cli', '设备 Skill'].map((header) => (
                    <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {visibleSources.map((source) => (
                  <tr key={source.id}>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">{source.category}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-semibold text-gray-900">{source.name}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">{source.vendor} / {source.product}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{source.region}</td>
                    <td className="px-4 py-3">
                      <Badge tone={source.health === '在线' ? 'green' : source.health === '异常' ? 'orange' : 'slate'}>{source.health}</Badge>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{source.version}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{source.lastSync}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{source.connectivity}</td>
                    <td className="px-4 py-3"><Badge tone={dimensionTone(source.api.status)}>{source.api.summary}</Badge></td>
                    <td className="px-4 py-3"><Badge tone={dimensionTone(source.web2cli.status)}>{source.web2cli.summary}</Badge></td>
                    <td className="px-4 py-3"><Badge tone={dimensionTone(source.skill.status)}>{source.skill.summary}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Link2 className="h-4 w-4 text-red-600" />
            <h3 className="text-sm font-semibold text-gray-900">接入向导</h3>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
            {[
              { title: '1 选择类型', detail: '设备 / SIEM / 威胁情报 / 资产系统 / 工单系统' },
              { title: '2 填写地址与凭证', detail: '统一管理 endpoint、token、AK/SK 与 Secret 引用' },
              { title: '3 测试连接', detail: '做连通性校验并记录健康状态' },
              { title: '4 选择能力', detail: '启用 API、web2cli、Skill 三维度能力' },
              { title: '5 关联编排', detail: '绑定默认 Workflow 与巡检计划' },
            ].map((step) => (
              <div key={step.title} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="text-sm font-semibold text-gray-900">{step.title}</div>
                <div className="mt-1 text-xs leading-5 text-gray-500">{step.detail}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <ActiveIcon className="h-4 w-4 text-red-600" />
            <h3 className="text-sm font-semibold text-gray-900">{activeConfig.label} 维度详情</h3>
          </div>
          <div className="space-y-3">
            {visibleSources.map((source) => {
              const detail = source[activeDimension];
              return (
                <div key={`${source.id}-${activeDimension}`} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-sm font-semibold text-gray-900">{source.name}</div>
                      <div className="mt-1 text-xs text-gray-500">{source.vendor} / {source.product} · {source.region}</div>
                    </div>
                    <Badge tone={dimensionTone(detail.status)}>{detail.status}</Badge>
                  </div>
                  <div className="mt-2 text-sm text-gray-700">{detail.summary}</div>
                  <div className="mt-1 text-xs text-gray-500">{detail.detail}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-4 text-xs text-gray-500">
            设备 Skill 仅表示数据源/设备接入页中的设备能力状态；完整技能管理请使用左侧
            <Link to="/skills" className="ml-1 font-medium text-red-600 hover:text-red-700">Skills 技能库</Link>
            导航。
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-red-600" />
            <h3 className="text-sm font-semibold text-gray-900">能力映射</h3>
          </div>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-[1080px] divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {['实例', '可用能力', '支持对象', '默认 Workflow'].map((header) => (
                    <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {visibleSources.map((source) => (
                  <tr key={`${source.id}-mapping`}>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-semibold text-gray-900">{source.name}</td>
                    <td className="px-4 py-3 text-sm text-gray-700">{source.api.summary} / {source.web2cli.summary} / {source.skill.summary}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{source.objects.join(' / ')}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{source.workflows.join(' / ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <Link to="/tools" className="rounded-md border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50">
              去工具清单维护 API 工具
            </Link>
            <Link to="/workflows" className="rounded-md border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50">
              去工作流绑定默认编排
            </Link>
            <Link to="/soc/inspections" className="rounded-md border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50">
              去 SOC 设备巡检查看运行结果
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
