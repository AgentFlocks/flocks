import { Link } from 'react-router-dom';
import { ClipboardList, ArrowRight } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card } from './components';

const inspectionPlans = [
  {
    id: 'INSP-HZ-FW-DAILY',
    name: '杭州机房防火墙每日巡检',
    sourceSkill: '防火墙健康巡检 Skill',
    sourceIntegration: 'FW-HZ-DMZ API 接入',
    schedule: '每日 09:00',
    latest: '成功（09:00）',
    status: '正常',
  },
  {
    id: 'INSP-HZ-NDR-HOURLY',
    name: 'NDR 每小时威胁规则同步检查',
    sourceSkill: 'NDR 规则版本核验 Skill',
    sourceIntegration: 'NDR-HZ-CORE API 接入',
    schedule: '每小时',
    latest: '异常（16:00）',
    status: '关注',
  },
  {
    id: 'INSP-BJ-HIDS-DAILY',
    name: 'HIDS 集群基线巡检',
    sourceSkill: '主机基线巡检 Skill',
    sourceIntegration: 'HIDS API 接入',
    schedule: '每日 10:00',
    latest: '待执行',
    status: '待运行',
  },
];

export default function SocInspectionsPage() {
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="设备巡检"
        description="在 SOC 工作区消费已发布的设备 Skill，按策略执行巡检并产出异常与告警。"
        icon={<ClipboardList className="h-8 w-8" />}
        action={(
          <Link
            to="/soc/cases"
            className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            查看关联调查案件
            <ArrowRight className="h-4 w-4" />
          </Link>
        )}
      />

      <div className="space-y-5">
        <Card>
          <div className="mb-3 text-sm font-semibold text-gray-900">巡检任务</div>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-[1080px] divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {['任务', '来源 Skill', '来源接入', '调度', '最近执行', '状态'].map((header) => (
                    <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {inspectionPlans.map((plan) => (
                  <tr key={plan.id}>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{plan.name}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">{plan.sourceSkill}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{plan.sourceIntegration}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{plan.schedule}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{plan.latest}</td>
                    <td className="px-4 py-3">
                      <Badge tone={plan.status === '正常' ? 'green' : plan.status === '关注' ? 'orange' : 'slate'}>
                        {plan.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="text-sm text-gray-600">
            巡检能力来源于 Agent 工作区发布的设备 Skill。若需要调整接入参数、web2cli 动作或 Skill 逻辑，请返回
            <Link to="/devices" className="ml-1 font-medium text-red-600 hover:text-red-700">设备 API 接入</Link>
            与
            <Link to="/devices?view=web2cli" className="ml-1 font-medium text-red-600 hover:text-red-700">设备 web2cli</Link>
            页面处理。
          </div>
        </Card>
      </div>
    </div>
  );
}
