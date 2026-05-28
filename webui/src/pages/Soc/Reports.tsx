import { FileText } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card } from './components';
import { socReports } from './mockData';

export default function SocReportsPage() {
  return (
    <div className="h-full overflow-y-auto space-y-4">
      <PageHeader
        title="报告中心"
        description="统一查看事件报告、值班日报、周报和管理层摘要。"
        icon={<FileText className="h-8 w-8" />}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {socReports.map((report) => (
          <Card key={report.id}>
            <div className="flex items-center justify-between gap-2">
              <Badge tone={report.status === '已完成' ? 'green' : 'orange'}>{report.status}</Badge>
              <span className="text-xs text-gray-500">{report.updatedAt}</span>
            </div>
            <h3 className="mt-3 text-base font-semibold text-gray-900">{report.title}</h3>
            <p className="mt-2 text-sm leading-6 text-gray-600">{report.summary}</p>
            <div className="mt-3 text-xs text-gray-500">
              {report.type} · 关联案件 {report.caseId} · 负责人 {report.owner}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
