import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { authApi } from '@/api/auth';
import CopyButton from '@/components/common/CopyButton';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';

function formatDateTime(value: string | null | undefined, locale: string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, { hour12: false });
}

export default function AdminUsersPage() {
  const { t, i18n } = useTranslation('auth');
  const { user, logout } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [resetCredential, setResetCredential] = useState<{
    username: string;
    password: string;
  } | null>(null);

  const closeResetCredentialModal = () => {
    setResetCredential(null);
    void logout();
  };

  const resetOwnPassword = async () => {
    const confirmed = await confirm({
      title: t('admin.resetConfirmTitle'),
      description: t('admin.resetConfirmDescription'),
      confirmText: t('admin.resetConfirmButton'),
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      const result = await authApi.resetPassword();
      if (result.temporary_password && user) {
        setResetCredential({
          username: user.username,
          password: result.temporary_password,
        });
      } else {
        toast.success(t('admin.resetSuccessToast'));
        await logout();
      }
    } catch (err: any) {
      toast.error(
        t('admin.resetFailedToast'),
        err?.response?.data?.detail || err?.message || t('admin.resetFailedToast'),
      );
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">{t('admin.sectionTitle')}</h1>
        <p className="mt-1 text-sm text-gray-500">{t('admin.sectionDescription')}</p>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-4 py-3">{t('admin.columnUsername')}</th>
              <th className="text-left px-4 py-3">{t('admin.columnRole')}</th>
              <th className="text-left px-4 py-3">{t('admin.columnLastLogin')}</th>
              <th className="text-left px-4 py-3">{t('admin.columnActions')}</th>
            </tr>
          </thead>
          <tbody>
            {user && (
              <tr className="border-t border-gray-100 align-top">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-900">{user.username}</div>
                  <div className="mt-1 text-xs text-blue-600">{t('admin.currentAccountTag')}</div>
                </td>
                <td className="px-4 py-3">{t('admin.roleAdmin')}</td>
                <td className="px-4 py-3 whitespace-nowrap">
                  {formatDateTime(user.last_login_at, i18n.language)}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => void resetOwnPassword()}
                    className="text-blue-600 hover:underline"
                  >
                    {t('admin.resetAction')}
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {resetCredential && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={closeResetCredentialModal} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">{t('admin.resetDialogTitle')}</h3>
                  <p className="mt-1 text-sm text-gray-500">{t('admin.resetDialogDescription')}</p>
                </div>
                <button
                  type="button"
                  onClick={closeResetCredentialModal}
                  className="text-sm text-gray-400 hover:text-gray-600"
                >
                  {t('admin.resetDialogClose')}
                </button>
              </div>
              <div className="mt-5 space-y-4">
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <div className="rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="text-xs text-gray-500">{t('admin.resetDialogUsernameLabel')}</div>
                    <div className="mt-1 font-medium text-gray-900">{resetCredential.username}</div>
                  </div>
                  <div className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs text-gray-500">{t('admin.resetDialogPasswordLabel')}</div>
                        <div className="mt-1 font-mono text-base font-semibold text-gray-900">
                          {resetCredential.password}
                        </div>
                      </div>
                      <CopyButton text={resetCredential.password} />
                    </div>
                  </div>
                  <div className="mt-3 text-sm text-amber-900">{t('admin.resetDialogWarning')}</div>
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={closeResetCredentialModal}
                    className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                  >
                    {t('admin.resetDialogDone')}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
