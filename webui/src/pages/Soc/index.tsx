import { Link } from 'react-router-dom';
import { ArrowRight, Bot, Shield, Sparkles } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ProgressBar, WorkflowSteps } from './components';
import {
  intelCards,
  scenarioSummaries,
  sharedStory,
  socMetrics,
  supportingScenarios,
  threatTimeline,
} from './mockData';

export default function SocOverviewPage() {
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="SOC 总览"
        description="使用预置数据构建的 Agentic SOC 作战室，展示从安全场景到 Flocks 配置的最短路径。"
        icon={<Shield className="h-8 w-8" />}
      />

      <div className="mb-6 rounded-2xl border border-red-100 bg-gradient-to-r from-red-50 to-white p-6 shadow-sm">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-red-200 bg-white px-3 py-1 text-xs font-medium text-red-700">
              <Sparkles className="h-3.5 w-3.5" />
              Rex 今日建议
            </div>
            <h2 className="text-2xl font-semibold text-gray-900">
              先处理 {sharedStory.incidentId}，再验证 {sharedStory.cve}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
              该事件把 {sharedStory.attackerIp}、{sharedStory.asset}、漏洞情报和新增公网暴露面串在一起，适合作为首个 Agentic SOC 场景故事线。
            </p>
          </div>
          <Link
            to="/soc/alerts"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            进入告警运营
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {socMetrics.map((metric) => (
          <Card key={metric.label}>
            <Badge tone={metric.tone}>{metric.label}</Badge>
            <div className="mt-4 text-3xl font-bold text-gray-900">{metric.value}</div>
            <div className="mt-2 text-sm text-gray-500">{metric.hint}</div>
          </Card>
        ))}
      </div>

      <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-[1.25fr_0.75fr]">
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-gray-900">场景入口</h3>
              <p className="text-sm text-gray-500">每个场景都有运营视图和配置车间两种模式。</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {scenarioSummaries.map((scenario) => {
              const Icon = scenario.icon;
              return (
                <div key={scenario.key} className="rounded-xl border border-gray-200 p-4 transition-colors hover:border-red-200 hover:bg-red-50/40">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="rounded-lg bg-red-50 p-2 text-red-600">
                      <Icon className="h-5 w-5" />
                    </div>
                    <Badge tone="orange">{scenario.status}</Badge>
                  </div>
                  <h4 className="font-semibold text-gray-900">{scenario.title}</h4>
                  <p className="mt-1 text-sm leading-6 text-gray-600">{scenario.description}</p>
                  <p className="mt-3 text-xs text-gray-500">{scenario.impact}</p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Link to={scenario.href} className="text-sm font-medium text-red-600 hover:text-red-700">
                      查看场景
                    </Link>
                    <Link to={scenario.configureHref} className="text-sm font-medium text-gray-600 hover:text-gray-900">
                      配置这个场景
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Bot className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">跨场景故事线</h3>
          </div>
          <div className="space-y-4">
            {threatTimeline.map((item) => (
              <div key={item.time} className="flex gap-3">
                <div className="w-12 shrink-0 text-sm font-semibold text-red-600">{item.time}</div>
                <div>
                  <div className="text-sm font-medium text-gray-900">{item.title}</div>
                  <div className="mt-1 text-sm leading-5 text-gray-500">{item.detail}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        {intelCards.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.title}>
              <Icon className="mb-4 h-6 w-6 text-red-600" />
              <h3 className="font-semibold text-gray-900">{item.title}</h3>
              <p className="mt-2 text-sm leading-6 text-gray-600">{item.detail}</p>
            </Card>
          );
        })}
      </div>

      <Card className="mb-6">
        <div className="mb-4">
          <h3 className="font-semibold text-gray-900">从总览进入的扩展场景</h3>
          <p className="text-sm text-gray-500">这些场景不放进一级导航，但可以从 SOC 总览和相关场景页进入。</p>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {supportingScenarios.map((scenario) => {
            const Icon = scenario.icon;
            return (
              <div key={scenario.key} className="rounded-xl border border-gray-200 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="rounded-lg bg-gray-50 p-2 text-red-600">
                    <Icon className="h-5 w-5" />
                  </div>
                  <Badge tone="blue">{scenario.status}</Badge>
                </div>
                <h4 className="font-semibold text-gray-900">{scenario.title}</h4>
                <p className="mt-1 text-sm leading-6 text-gray-600">{scenario.description}</p>
                <p className="mt-3 text-xs text-gray-500">{scenario.impact}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link to={scenario.href} className="text-sm font-medium text-red-600 hover:text-red-700">
                    查看场景
                  </Link>
                  <Link to={scenario.configureHref} className="text-sm font-medium text-gray-600 hover:text-gray-900">
                    配置车间
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-gray-900">Agentic SOC 成熟度</h3>
            <p className="text-sm text-gray-500">使用预置数据展示“看结果”和“配能力”如何连在一起。</p>
          </div>
          <Badge tone="green">68%</Badge>
        </div>
        <ProgressBar value={68} />
        <div className="mt-5">
          <WorkflowSteps steps={['业务目标', '场景配置车间', 'Agent 分工', 'Workflow 执行', '证据和汇报']} />
        </div>
      </Card>
    </div>
  );
}
