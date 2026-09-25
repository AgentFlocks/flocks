import { IconUserCog } from '@tabler/icons-react';
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
        icon={<IconUserCog aria-hidden="true" className="w-8 h-8" />}
      />

      <div className="bg-surface rounded-card border border-line shadow-sm p-6">
        <AdminUsersPage />
      </div>
    </div>
  );
}
