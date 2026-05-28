import { useSearchParams } from 'react-router-dom';
import { Bug, ClipboardCheck, FileSearch, ShieldAlert, Target } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ConfigWorkshop, ModeSwitch, ProgressBar } from './components';
import { sharedStory, vulnerabilityRows } from './mockData';

export default function SocVulnerabilitiesPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="漏洞排查"
        description="把漏洞情报、影响资产排查、授权验证和复测报告串成一个 Agentic 闭环。"
        icon={<Bug className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/vulnerabilities?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="vulnerabilities" /> : <VulnerabilityOperation />}
    </div>
  );
}

function VulnerabilityOperation() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <FileSearch className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">影响资产排查</h3>
          </div>
          <div className="overflow-hidden rounded-lg border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {['漏洞', '影响组件', '匹配资产', '置信度', 'Rex 建议'].map((header) => (
                    <th key={header} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {vulnerabilityRows.map((row) => (
                  <tr key={row.cve}>
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">{row.cve}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{row.product}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{row.assets}</td>
                    <td className="px-4 py-3"><Badge tone={row.confidence === '高' ? 'red' : row.confidence === '中' ? 'orange' : 'slate'}>{row.confidence}</Badge></td>
                    <td className="px-4 py-3 text-sm text-gray-600">{row.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Target className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">Rex 影响判断</h3>
          </div>
          <div className="rounded-xl bg-red-50 p-4">
            <div className="font-medium text-gray-900">{sharedStory.asset} 建议优先验证</div>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              资产暴露在 DMZ，版本指纹和 {sharedStory.cve} 受影响范围匹配，且同一攻击源已出现在告警事件中。
            </p>
          </div>
          <div className="mt-4">
            <div className="mb-1 flex justify-between text-xs text-gray-500">
              <span>影响置信度</span>
              <span>88%</span>
            </div>
            <ProgressBar value={88} />
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card>
          <ShieldAlert className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">验证计划</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            只在授权窗口内对 {sharedStory.asset} 执行无破坏性探测，保留请求、响应和截图证据。
          </p>
        </Card>
        <Card>
          <ClipboardCheck className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">授权范围</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            范围限定为华东一区 DMZ 资产，不触碰生产数据库和第三方托管服务。
          </p>
        </Card>
        <Card>
          <Bug className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">复测闭环</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            修复工单关闭后自动触发复测 Workflow，并生成管理层可读的风险关闭摘要。
          </p>
        </Card>
      </div>
    </div>
  );
}
