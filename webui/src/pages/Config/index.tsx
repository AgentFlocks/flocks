import { Link, Outlet, useLocation } from 'react-router-dom';
import { Settings, ShieldCheck, Users } from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { useAuth } from '@/contexts/AuthContext';

export default function ConfigPage() {
  const location = useLocation();
  const { user, logout } = useAuth();

  const tabs = [
    { name: '账号管理', href: '/config/accounts', icon: Users },
    ...(user?.role === 'admin'
      ? [{ name: '审计日志', href: '/config/audit-logs', icon: ShieldCheck }]
      : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="系统配置"
        description="在这里统一管理账号与审计设置。"
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
        <div className="border-b border-gray-200 px-4 sm:px-6">
          <div className="flex gap-2 overflow-x-auto py-4">
            {tabs.map((item) => {
              const isActive = location.pathname === item.href || location.pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  to={item.href}
                  className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors ${
                    isActive
                      ? 'bg-slate-900 text-white'
                      : 'bg-gray-50 text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                  }`}
                >
                  <item.icon className="w-4 h-4" />
                  {item.name}
                </Link>
              );
            })}
          </div>
        </div>
        <div className="p-6">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
