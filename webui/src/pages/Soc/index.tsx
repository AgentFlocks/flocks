import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRight,
  Bug,
  Globe2,
  MailWarning,
  Network,
  Radar,
  Shield,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Card } from './components';

const sceneCards = [
  {
    title: '告警运营',
    href: '/soc/alerts',
    icon: AlertTriangle,
    tone: 'red' as const,
    stat: '1,023',
    label: '降噪后告警',
    metrics: [
      { value: '5', label: '事件待确认' },
      { value: '20', label: '待处置动作' },
    ],
    action: '确认攻击成功性，处理封禁、修复、复测建议',
  },
  {
    title: '态势情报',
    href: '/soc/intel',
    icon: Radar,
    tone: 'purple' as const,
    stat: '5',
    label: '情报报告',
    metrics: [
      { value: '1', label: '类资产命中' },
    ],
    action: '生成影响面追踪任务',
  },
  {
    title: '漏洞排查',
    href: '/soc/vulnerabilities',
    icon: Bug,
    tone: 'orange' as const,
    stat: '4',
    label: '漏洞报告',
    metrics: [
      { value: '3', label: '台需处理' },
    ],
    action: '验证漏洞并安排修复',
  },
  {
    title: '设备巡检',
    href: '/soc/assets',
    icon: Network,
    tone: 'blue' as const,
    stat: '7',
    label: '接入设备',
    metrics: [
      { value: '2', label: '台巡检异常' },
    ],
    action: '确认设备状态和巡检配置',
  },
  {
    title: '钓鱼演练',
    href: '/soc/drills',
    icon: MailWarning,
    tone: 'green' as const,
    stat: '1',
    label: '进行中演练',
    metrics: [
      { value: '1', label: '项待确认' },
    ],
    action: '检查名单和审批状态',
  },
  {
    title: '互联网攻击面',
    href: '/soc/attack-surface',
    icon: Globe2,
    tone: 'slate' as const,
    stat: '3',
    label: '新增公网入口',
    metrics: [
      { value: '3', label: '个待归属' },
    ],
    action: '确认 owner 和暴露原因',
  },
];

const todayFocus = [
  { title: '优先研判 NDR 告警', detail: 'SQL 注入成功、WebShell 远控、钓鱼登录口访问。', href: '/soc/alerts' },
  { title: '确认漏洞影响资产', detail: 'Fortinet SSL-VPN 与 AI 网关漏洞需完成验证。', href: '/soc/vulnerabilities' },
  { title: '处理外部扫描噪声', detail: '扫描源清单、200 响应资产和封禁建议已生成。', href: '/soc/alerts' },
];

export default function SocOverviewPage() {
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="SOC 总览"
        description="查看今日 SOC 重点数据，并快速进入各运营场景。"
        icon={<Shield className="h-8 w-8" />}
      />

      <div className="mb-3 grid grid-cols-1 gap-2 lg:grid-cols-3">
        {sceneCards.map((scene) => {
          const Icon = scene.icon;
          return (
            <Link key={scene.title} to={scene.href}>
              <Card className="h-full p-3 transition-colors hover:border-red-200 hover:bg-red-50/30">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="rounded-md bg-red-50 p-1.5 text-red-600">
                      <Icon className="h-4 w-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900">{scene.title}</h3>
                      <p className="mt-0.5 text-xs text-gray-500">{scene.stat} {scene.label}</p>
                    </div>
                  </div>
                  <ArrowRight className="mt-1 h-4 w-4 text-gray-400" />
                </div>
                <div className="mt-3 rounded-lg bg-gray-50 px-3 py-2.5">
                  <div className="flex items-end gap-5">
                    {scene.metrics.map((metric) => (
                      <div key={metric.label} className="flex items-baseline gap-1.5">
                        <span className="text-3xl font-semibold leading-none text-gray-950">{metric.value}</span>
                        <span className="text-sm font-medium text-gray-700">{metric.label}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 text-xs text-gray-500">{scene.action}</div>
                </div>
              </Card>
            </Link>
          );
        })}
      </div>

      <Card className="p-3">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">今日关注</h3>
          <span className="text-xs text-gray-500">点击进入对应场景</span>
        </div>
        <div className="grid grid-cols-1 gap-2 xl:grid-cols-3">
          {todayFocus.map((item) => (
            <Link
              key={item.title}
              to={item.href}
              className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 transition-colors hover:border-red-200 hover:bg-white"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-gray-900">{item.title}</div>
                <ArrowRight className="h-4 w-4 shrink-0 text-gray-400" />
              </div>
              <p className="mt-0.5 truncate text-xs text-gray-500">{item.detail}</p>
            </Link>
          ))}
        </div>
      </Card>
    </div>
  );
}
