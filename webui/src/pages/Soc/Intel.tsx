import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, BellRing, FileText, Radar, Search, Send } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ConfigWorkshop, ModeSwitch, WorkflowSteps } from './components';
import { intelBriefings, sharedStory } from './mockData';

export default function SocIntelPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="态势情报"
        description="接入微步 MCP，把每日行业情报转成资产影响排查、漏洞验证和值班群推送。"
        icon={<Radar className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/intel?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="intel" /> : <IntelOperation />}
    </div>
  );
}

function IntelOperation() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <FileText className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">今日情报筛选</h3>
          </div>
          <div className="space-y-3">
            {intelBriefings.map((item) => (
              <div key={item.title} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="font-medium text-gray-900">{item.title}</div>
                  <Badge tone={item.relevance === '高' ? 'red' : item.relevance === '中' ? 'orange' : 'slate'}>
                    {item.relevance}相关
                  </Badge>
                </div>
                <div className="text-sm text-gray-500">来源：{item.source}</div>
                <div className="mt-2 text-sm text-gray-700">Rex 建议：{item.action}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Search className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">情报到资产影响</h3>
          </div>
          <div className="rounded-xl bg-red-50 p-4">
            <div className="font-semibold text-gray-900">{sharedStory.intelTopic}</div>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              情报命中 {sharedStory.cve}，Rex 已关联 {sharedStory.asset}、攻击源 {sharedStory.attackerIp} 和昨日新增公网暴露面。
            </p>
          </div>
          <div className="mt-4 space-y-3">
            {['创建漏洞影响排查任务', '更新告警研判上下文', '生成值班群推送', '加入周报素材'].map((item) => (
              <div key={item} className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-700">
                {item}
                <ArrowRight className="h-4 w-4 text-gray-400" />
              </div>
            ))}
          </div>
          <Link
            to="/soc/vulnerabilities"
            className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            进入漏洞排查
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <BellRing className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">值班推送预览</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            今日重点关注边界设备 RCE 利用链。建议优先排查 DMZ WAF、VPN 和公网 API 网关，已生成 3 个自动排查任务。
          </p>
        </Card>
        <Card>
          <Send className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">定时动作</h3>
          <WorkflowSteps steps={['拉取情报', '过滤行业', '富化 IOC/CVE', '匹配资产', '生成任务', '推送摘要']} />
        </Card>
      </div>
    </div>
  );
}
