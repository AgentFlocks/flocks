import { Outlet } from 'react-router-dom';
import { Settings } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { useAuth } from '@/contexts/AuthContext';

export default function ConfigPage() {
  const { logout } = useAuth();

  return (
    <div className="space-y-6">
      <PageHeader
        title="系统配置"
        description="在这里管理账号与密码。"
        icon={<Settings className="w-8 h-8" />}
        action={(
          <button
            type="button"
            onClick={() => void logout()}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            退出登录
          </button>
        )}
      />

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="p-6">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
