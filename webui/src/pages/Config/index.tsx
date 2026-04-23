import { UserCog } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { useAuth } from '@/contexts/AuthContext';
import AdminUsersPage from '@/pages/AdminUsers';

export default function ConfigPage() {
  const { logout } = useAuth();

  return (
    <div className="space-y-6">
      <PageHeader
        title="账号管理"
        description="管理本地账号与密码。"
        icon={<UserCog className="w-8 h-8" />}
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

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <AdminUsersPage />
      </div>
    </div>
  );
}
