import { useSearchParams } from 'react-router-dom';
import { BarChart3, CalendarClock, MailWarning, ShieldCheck, Users } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Card, ConfigWorkshop, ModeSwitch, WorkflowSteps } from './components';
import { drillStats } from './mockData';

export default function SocDrillsPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="钓鱼演练"
        description="用 Agentic 方式覆盖演练策划、审批、模板生成、投递、指标采集和复盘报告。"
        icon={<MailWarning className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/drills?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="drills" /> : <DrillOperation />}
    </div>
  );
}

function DrillOperation() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {drillStats.map((stat) => (
          <Card key={stat.label}>
            <div className="text-sm text-gray-500">{stat.label}</div>
            <div className="mt-2 text-2xl font-bold text-gray-900">{stat.value}</div>
            <div className="mt-2 text-sm leading-5 text-gray-500">{stat.hint}</div>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">合规边界</h3>
          </div>
          <div className="space-y-3">
            {[
              ['审批链', '合规、HR、财务负责人已确认'],
              ['豁免名单', '高压项目组、外包临时账号、请假人员'],
              ['演练窗口', '工作日 10:00-16:00，避开结算日'],
              ['数据保护', '只采集点击、提交、上报行为，不保存真实密码'],
            ].map(([title, detail]) => (
              <div key={title} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="font-medium text-gray-900">{title}</div>
                <p className="mt-1 text-sm text-gray-500">{detail}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="mb-4 flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">演练流程</h3>
          </div>
          <WorkflowSteps steps={['生成演练方案', '审批确认', '模板投递', '行为采集', '教育反馈', '复盘报告']} />
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card>
          <Users className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">目标人群分层</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            Rex 根据岗位、历史演练表现和敏感系统权限，把目标拆成三类，分别配置不同难度模板。
          </p>
        </Card>
        <Card>
          <MailWarning className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">模板生成</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            生成发票、供应商对账和合同补充协议三类主题，同时自动避开真实品牌侵权风险。
          </p>
        </Card>
        <Card>
          <BarChart3 className="mb-4 h-6 w-6 text-red-600" />
          <h3 className="font-semibold text-gray-900">复盘指标</h3>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            展示点击率、提交率、主动上报率和二次教育完成率，并生成团队改进建议。
          </p>
        </Card>
      </div>
    </div>
  );
}
