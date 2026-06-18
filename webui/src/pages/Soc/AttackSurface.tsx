import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, Compass, Globe2, Network, SearchCheck, ShieldAlert } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Card, ConfigWorkshop, ModeSwitch, WorkflowSteps } from './components';
import { exposureFindings } from './mockData';

export default function SocAttackSurfacePage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="互联网攻击面"
        description="让 Agent 每日巡航公网资产、暴露服务、证书、域名和影子资产，并把变化转成可处置任务。"
        icon={<Globe2 className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/attack-surface?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="attackSurface" /> : <AttackSurfaceOperation />}
    </div>
  );
}

function AttackSurfaceOperation() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Compass className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">发现种子</h3>
          </div>
          <div className="space-y-3">
            {['example.com 根域名', '公司中文主体和英文主体', '云账号标签 owner=prod', '支付业务证书主体'].map((item) => (
              <div key={item} className="rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-700">
                {item}
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <SearchCheck className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">今日发现</h3>
          </div>
          <div className="space-y-3">
            {exposureFindings.map((finding) => (
              <div key={finding.title} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="font-medium text-gray-900">{finding.title}</div>
                <p className="mt-1 text-sm leading-5 text-gray-500">{finding.detail}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card>
          <Network className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">资产归属</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            Rex 会把新增域名、IP 和证书主体与 CMDB、云标签、历史工单匹配，给出最可能的 owner。
          </p>
        </Card>
        <Card>
          <ShieldAlert className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">风险验证</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            对未知入口先生成无破坏性验证计划，再由用户确认是否转入漏洞验证或封禁流程。
          </p>
        </Card>
        <Card>
          <Globe2 className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">每日巡航</h3>
          <WorkflowSteps steps={['加载种子', '多源发现', '去重归属', '风险评分', '生成验证', '推送摘要']} />
        </Card>
      </div>

      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="font-semibold text-gray-900">与资产和漏洞闭环</h3>
            <p className="mt-1 text-sm text-gray-500">
              发现未知暴露面后，可以回到资产接入补全 owner，也可以进入漏洞验证确认真实风险。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/soc/assets" className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
              回到资产
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link to="/soc/vulnerabilities" className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
              进入漏洞验证
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </Card>
    </div>
  );
}
