import { Link, useParams } from 'react-router-dom';
import { Bot, FileText, GitBranch } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card } from './components';
import { agentInvestigationMessages, deepInvestigations, socReports } from './mockData';

export default function SocCaseDetailPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const caseItem = deepInvestigations.find((item) => item.id === caseId);

  if (!caseItem) {
    return (
      <div className="h-full overflow-y-auto">
        <PageHeader
          title="调查案件"
          description="未找到对应案件，请返回案件列表重新选择。"
          icon={<FileText className="h-8 w-8" />}
        />
      </div>
    );
  }

  const relatedReports = socReports.filter((report) => report.caseId === caseItem.id);

  return (
    <div className="h-full overflow-y-auto space-y-4">
      <PageHeader
        title={caseItem.title}
        description={`案件 ${caseItem.id} 的证据链、时间线、Agent 调查过程与报告输出。`}
        icon={<FileText className="h-8 w-8" />}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Card>
          <div className="text-xs text-gray-500">优先级</div>
          <div className="mt-2"><Badge tone={caseItem.severity === 'P1' ? 'red' : 'orange'}>{caseItem.severity}</Badge></div>
        </Card>
        <Card>
          <div className="text-xs text-gray-500">状态</div>
          <div className="mt-2 text-sm font-semibold text-gray-900">{caseItem.status}</div>
        </Card>
        <Card>
          <div className="text-xs text-gray-500">责任组</div>
          <div className="mt-2 text-sm font-semibold text-gray-900">{caseItem.owner}</div>
        </Card>
        <Card>
          <div className="text-xs text-gray-500">关联设备</div>
          <div className="mt-2 flex flex-wrap gap-1">
            {caseItem.entities.map((entity) => (
              <Badge key={`entity-${entity}`} tone="blue">{entity}</Badge>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">证据链</h3>
          </div>
          <div className="space-y-3">
            {caseItem.evidence.map((evidence) => (
              <div key={`${evidence.source}-${evidence.time}`} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-sm font-semibold text-gray-900">{evidence.source}</div>
                  <div className="text-xs text-gray-500">{evidence.time}</div>
                </div>
                <p className="mt-2 text-sm leading-6 text-gray-600">{evidence.detail}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center gap-2">
            <Bot className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">Agent 调查过程</h3>
          </div>
          <div className="space-y-3">
            {agentInvestigationMessages.slice(0, 6).map((message, idx) => (
              <div key={`${message.time}-${idx}`} className="rounded-lg border border-gray-200 bg-white p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-gray-900">{message.sender}</span>
                  <span className="text-xs text-gray-500">{message.time}</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-gray-600">{message.content}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <h3 className="font-semibold text-gray-900">处置建议</h3>
            <p className="text-sm text-gray-500">基于当前证据链和上下文生成的建议动作。</p>
          </div>
          <Link to="/soc/reports" className="text-sm font-medium text-red-600 hover:text-red-700">
            查看报告中心
          </Link>
        </div>
        <div className="rounded-lg border border-red-100 bg-red-50 p-4 text-sm leading-6 text-gray-700">
          {caseItem.recommendation}
        </div>
        {relatedReports.length > 0 && (
          <div className="mt-4 space-y-2">
            {relatedReports.map((report) => (
              <div key={report.id} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <div className="text-sm font-semibold text-gray-900">{report.title}</div>
                <div className="mt-1 text-xs text-gray-500">{report.type} · {report.updatedAt}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
