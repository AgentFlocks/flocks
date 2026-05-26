import { Link } from 'react-router-dom';
import { ClipboardList } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge } from './components';
import { deepInvestigations } from './mockData';

export default function SocCasesPage() {
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="调查案件"
        description="统一查看深度调查案件状态、证据进展与处置建议。"
        icon={<ClipboardList className="h-8 w-8" />}
      />

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {['案件 ID', '优先级', '主题', '状态', '责任组', '关联设备', '操作'].map((header) => (
                <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {deepInvestigations.map((item) => (
              <tr key={item.id} className="hover:bg-gray-50">
                <td className="whitespace-nowrap px-4 py-3 text-sm font-semibold text-gray-900">{item.id}</td>
                <td className="px-4 py-3">
                  <Badge tone={item.severity === 'P1' ? 'red' : 'orange'}>{item.severity}</Badge>
                </td>
                <td className="px-4 py-3">
                  <div className="text-sm font-medium text-gray-900">{item.title}</div>
                  <div className="mt-1 max-w-xl truncate text-xs text-gray-500">{item.summary}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{item.status}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{item.owner}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {item.entities.map((entity) => (
                      <Badge key={`${item.id}-${entity}`} tone="blue">{entity}</Badge>
                    ))}
                  </div>
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  <Link to={`/soc/cases/${item.id}`} className="text-sm font-medium text-red-600 hover:text-red-700">
                    查看详情
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
