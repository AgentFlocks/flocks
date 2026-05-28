import { BookOpen } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';

const docRows = [
  { name: '应急响应 SOP v3.2', domain: '安全运营', status: '已启用' },
  { name: '边界资产分级规范', domain: '资产管理', status: '已启用' },
  { name: '告警误报处置手册', domain: '告警研判', status: '草稿' },
];

const listRows = [
  { type: '白名单', target: '10.10.0.0/16', source: '运维审批', expires: '长期' },
  { type: '黑名单', target: '45.91.83.24', source: '威胁情报', expires: '2026-06-30' },
  { type: '白名单', target: 'scanner.prod.internal', source: '漏洞扫描平台', expires: '2026-09-01' },
];

const contextProviders = [
  { name: 'CMDB', status: '已连接', mode: '按需查询' },
  { name: 'IAM', status: '已连接', mode: '按需查询' },
  { name: '工单系统', status: '待配置', mode: '按需查询' },
  { name: '拓扑系统', status: '已连接', mode: '按需查询' },
];

export default function KnowledgePage() {
  return (
    <div className="h-full overflow-y-auto space-y-4">
      <PageHeader
        title="知识库"
        description="聚合企业文档、黑白名单、历史研判结论与外部 Context Provider。"
        icon={<BookOpen className="h-8 w-8" />}
      />

      <div className="rounded-xl border border-gray-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
        知识库只存储可复用的安全知识，不存储 CMDB/OA/IAM/工单系统全量主数据。外部系统通过 Context Provider 按需查询。
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-900">企业文档</h3>
        <div className="mt-3 space-y-2">
          {docRows.map((row) => (
            <div key={row.name} className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
              <div>
                <div className="font-medium text-gray-900">{row.name}</div>
                <div className="text-xs text-gray-500">{row.domain}</div>
              </div>
              <span className="text-xs text-gray-600">{row.status}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-900">黑白名单</h3>
        <div className="mt-3 space-y-2">
          {listRows.map((row) => (
            <div key={`${row.type}-${row.target}`} className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
              <div>
                <div className="font-medium text-gray-900">{row.target}</div>
                <div className="text-xs text-gray-500">{row.type} · 来源 {row.source}</div>
              </div>
              <span className="text-xs text-gray-600">{row.expires}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-900">外部 Context Provider</h3>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          {contextProviders.map((provider) => (
            <div key={provider.name} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm">
              <div className="font-medium text-gray-900">{provider.name}</div>
              <div className="mt-1 text-xs text-gray-500">{provider.mode}</div>
              <div className="mt-1 text-xs text-gray-600">{provider.status}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
