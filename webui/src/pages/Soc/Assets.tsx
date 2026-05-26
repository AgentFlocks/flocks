import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { Globe2, Network } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ConfigWorkshop, ModeSwitch } from './components';
import { dataCenterAssets } from './mockData';

export default function SocAssetsPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="设备巡检"
        description="把设备管理、设备接入、API、web2cli、Skills、巡检和攻击面巡航放在同一个资产上下文里。"
        icon={<Network className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/assets?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="assets" /> : <AssetsOperation />}
    </div>
  );
}

function AssetsOperation() {
  const [activeDataCenterId, setActiveDataCenterId] = useState(dataCenterAssets[0].id);
  const activeDataCenter = dataCenterAssets.find((item) => item.id === activeDataCenterId) ?? dataCenterAssets[0];

  return (
    <div className="space-y-5">
      <Card className="p-0">
        <div className="border-b border-gray-200 px-5 pt-3">
          <div className="flex flex-wrap gap-2">
            {dataCenterAssets.map((dataCenter) => (
              <button
                key={dataCenter.id}
                type="button"
                onClick={() => setActiveDataCenterId(dataCenter.id)}
                className={`rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                  activeDataCenterId === dataCenter.id
                    ? 'border-red-600 text-red-700'
                    : 'border-transparent text-gray-500 hover:text-gray-900'
                }`}
              >
                {dataCenter.name}
              </button>
            ))}
          </div>
        </div>
        <div className="px-5 py-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm text-gray-500">{activeDataCenter.summary}</div>
            <div className="flex gap-2">
              <Link
                to="/soc/attack-surface"
                className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                <Globe2 className="h-4 w-4 text-red-600" />
                攻击面巡航
              </Link>
            </div>
          </div>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-[1180px] divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {['设备类型', '设备名称', 'CPU', '内存', '磁盘', '告警量', 'License 到期', '情报数据', '状态'].map((header) => (
                    <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {activeDataCenter.devices.map((device) => (
                  <tr key={device.name} className={device.status === '缺失' ? 'bg-gray-50 text-gray-400' : ''}>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{device.type}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-semibold text-gray-900">{device.name}</td>
                    <MetricCell value={device.cpu} suffix="%" />
                    <MetricCell value={device.memory} suffix="%" />
                    <MetricCell value={device.disk} suffix="%" />
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{device.alerts}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{device.license}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{device.intel}</td>
                    <td className="px-4 py-3">
                      <Badge tone={device.status === '健康' ? 'green' : device.status === '关注' ? 'orange' : 'slate'}>
                        {device.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Card>
    </div>
  );
}

function MetricCell({ value, suffix }: { value: number | null; suffix: string }) {
  if (value === null) {
    return <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-400">缺失</td>;
  }

  const tone = value >= 75 ? 'text-red-600' : value >= 65 ? 'text-orange-600' : 'text-gray-600';
  return (
    <td className={`whitespace-nowrap px-4 py-3 text-sm font-medium ${tone}`}>
      {value}{suffix}
    </td>
  );
}
