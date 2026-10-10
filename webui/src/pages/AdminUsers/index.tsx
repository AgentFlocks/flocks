import PageDialog from '../PageDialog';
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

function roleLabel(role: string, t: (key: string) => string) {
  return role === 'admin' ? t('admin.roleAdmin') : t('admin.roleMember');
}

/** Current local account: who is signed in, and a one-time password reset for it. */
export default function AdminUsersPage() {
  const { t, i18n } = useTranslation('auth');
  const { user, logout } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [credentialModal, setCredentialModal] = useState<{
    username: string;
    password: string;
    description: string;
    doneText: string;
    warning: string;
    logoutAfterClose: boolean;
  } | null>(null);

  const closeCredentialModal = () => {
    const shouldLogout = credentialModal?.logoutAfterClose;
    setCredentialModal(null);
    if (shouldLogout) {
      void logout();
    }
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
        setCredentialModal({
          username: user.username,
          password: result.temporary_password,
          description: t('admin.resetDialogDescription'),
          doneText: t('admin.resetDialogDone'),
          warning: t('admin.resetDialogWarning'),
          logoutAfterClose: true,
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
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('admin.sectionTitle')}</h1>
          <p className="mt-1 text-sm text-gray-500">{t('admin.sectionDescription')}</p>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-4 py-3">{t('admin.columnUsername')}</th>
              <th className="text-left px-4 py-3">{t('admin.columnRole')}</th>
              <th className="text-left px-4 py-3 hidden md:table-cell">{t('admin.columnLastLogin')}</th>
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
                <td className="px-4 py-3">{roleLabel(user.role, t)}</td>
                <td className="px-4 py-3 hidden md:table-cell whitespace-nowrap">
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

      {credentialModal && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40" onClick={closeCredentialModal} />
          {/* The centering wrapper sits above the backdrop and receives the
              clicks on the dimmed area, so "click outside to close" must be
              handled here (only when the wrapper itself is the target). */}
          <PageDialog onClose={closeCredentialModal}
            className="fixed inset-0 z-50 flex items-center justify-center p-4"

          >
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">{t('admin.resetDialogTitle')}</h3>
                  <p className="mt-1 text-sm text-gray-500">{credentialModal.description}</p>
                </div>
                <button
                  type="button"
                  onClick={closeCredentialModal}
                  className="text-sm text-gray-400 hover:text-gray-600"
                >
                  {t('admin.resetDialogClose')}
                </button>
              </div>
              <div className="mt-5 space-y-4">
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <div className="rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="text-xs text-gray-500">{t('admin.resetDialogUsernameLabel')}</div>
                    <div className="mt-1 font-medium text-gray-900">{credentialModal.username}</div>
                  </div>
                  <div className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs text-gray-500">{t('admin.resetDialogPasswordLabel')}</div>
                        <div className="mt-1 font-mono text-base font-semibold text-gray-900">
                          {credentialModal.password}
                        </div>
                      </div>
                      <CopyButton text={credentialModal.password} />
                    </div>
                  </div>
                  <div className="mt-3 text-sm text-amber-900">{credentialModal.warning}</div>
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={closeCredentialModal}
                    className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                  >
                    {credentialModal.doneText}
                  </button>
                </div>
              </div>
            </div>
          </PageDialog>
        </>
      )}
    </div>
  );
}
