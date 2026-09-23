import { UserCog } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import AdminUsersPage from '@/pages/AdminUsers';

export default function ConfigPage() {
  const { t } = useTranslation('auth');

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('admin.pageTitle')}
        description={t('admin.pageDescription')}
        icon={<UserCog className="w-8 h-8" />}
      />

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <AdminUsersPage />
      </div>
    </div>
  );
}
