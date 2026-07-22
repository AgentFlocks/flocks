import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { authApi, type LocalUser } from '@/api/auth';
import { flocksproUsersApi, type FlocksproUserQuota } from '@/api/flocksproUsers';
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

function normalizeQuotaLimit(...values: Array<number | null | undefined>): number {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      return Math.floor(value);
    }
  }
  return 0;
}

export default function AdminUsersPage() {
  const { t, i18n } = useTranslation('auth');
  const { user, logout } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [loading, setLoading] = useState(true);
  const [isProEnabled, setIsProEnabled] = useState(false);
  const [users, setUsers] = useState<LocalUser[]>([]);
  const [quota, setQuota] = useState<FlocksproUserQuota | null>(null);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createForm, setCreateForm] = useState<{ username: string; role: 'admin' | 'member' }>({
    username: '',
    role: 'member',
  });
  const [creating, setCreating] = useState(false);
  const [credentialModal, setCredentialModal] = useState<{
    username: string;
    password: string;
    description: string;
    doneText: string;
    warning: string;
    logoutAfterClose: boolean;
  } | null>(null);

  const showProAdminView = isProEnabled && user?.role === 'admin';
  const sortedUsers = useMemo(
    () => [...users].sort((a, b) => a.username.localeCompare(b.username)),
    [users],
  );

  const closeCredentialModal = () => {
    const shouldLogout = credentialModal?.logoutAfterClose;
    setCredentialModal(null);
    if (shouldLogout) {
      void logout();
    }
  };

  const loadProUsers = useCallback(async () => {
    const [userList, licenseStatus, quotaSnapshot] = await Promise.all([
      flocksproUsersApi.listUsers(),
      flocksproUsersApi.getLicenseStatus(),
      flocksproUsersApi.getQuota().catch(() => null),
    ]);
    const adminCount = userList.filter((item) => item.role === 'admin').length;
    const memberCount = userList.filter((item) => item.role !== 'admin').length;
    const maxAdmins = normalizeQuotaLimit(
      licenseStatus.max_admins,
      licenseStatus.effective_max_admins,
      quotaSnapshot?.max_admins,
    );
    const maxMembers = normalizeQuotaLimit(
      licenseStatus.max_members,
      licenseStatus.effective_max_members,
      quotaSnapshot?.max_members,
    );
    setUsers(userList);
    setQuota({
      max_admins: maxAdmins,
      max_members: maxMembers,
      admin_count: adminCount,
      member_count: memberCount,
      pro_enabled: licenseStatus.pro_enabled ?? quotaSnapshot?.pro_enabled,
      license_status: licenseStatus.license_status ?? licenseStatus.status ?? quotaSnapshot?.license_status,
    });
  }, []);

  const refreshProCapabilityAndData = useCallback(async (showErrorToast = true) => {
    if (!user || user.role !== 'admin') {
      setIsProEnabled(false);
      setUsers([]);
      setQuota(null);
      return;
    }
    const enabled = await flocksproUsersApi.hasCapability();
    setIsProEnabled(enabled);
    if (!enabled) {
      setUsers([]);
      setQuota(null);
      return;
    }
    try {
      await loadProUsers();
    } catch (err: any) {
      if (showErrorToast) {
        toast.error(
          t('admin.pro.loadFailed'),
          err?.response?.data?.detail || err?.message || t('admin.pro.loadFailed'),
        );
      }
    }
  }, [loadProUsers, t, toast, user]);

  useEffect(() => {
    let mounted = true;
    const loadMode = async () => {
      if (!user) {
        if (mounted) setLoading(false);
        return;
      }
      setLoading(true);
      if (!mounted) return;
      try {
        await refreshProCapabilityAndData();
      } finally {
        if (mounted) setLoading(false);
      }
    };
    void loadMode();
    return () => {
      mounted = false;
    };
  }, [refreshProCapabilityAndData, user]);

  useEffect(() => {
    if (!showProAdminView) return undefined;

    const refreshSilently = () => {
      void refreshProCapabilityAndData(false);
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshSilently();
      }
    };

    window.addEventListener('flockspro-license-status-changed', refreshSilently);
    window.addEventListener('focus', refreshSilently);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.removeEventListener('flockspro-license-status-changed', refreshSilently);
      window.removeEventListener('focus', refreshSilently);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [refreshProCapabilityAndData, showProAdminView]);

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

  const createUser = async () => {
    const username = createForm.username.trim();
    if (!username) {
      toast.error(t('admin.pro.createUsernameRequired'));
      return;
    }
    setCreating(true);
    try {
      const result = await flocksproUsersApi.createUser({
        username,
        role: createForm.role,
      });
      await loadProUsers();
      setCreateModalOpen(false);
      setCreateForm({ username: '', role: 'member' });
      setCredentialModal({
        username: result.user.username,
        password: result.temporary_password,
        description: t('admin.pro.createDialogDescription'),
        doneText: t('admin.pro.createDialogDone'),
        warning: t('admin.pro.createDialogWarning'),
        logoutAfterClose: false,
      });
    } catch (err: any) {
      toast.error(
        t('admin.pro.createFailed'),
        err?.response?.data?.detail || err?.message || t('admin.pro.createFailed'),
      );
    } finally {
      setCreating(false);
    }
  };

  const updateUserRole = async (
    targetUserId: string,
    username: string,
    role: 'admin' | 'member',
    prevRole: 'admin' | 'member',
  ) => {
    if (role === prevRole) return;
    const confirmed = await confirm({
      title: t('admin.pro.updateRoleConfirmTitle'),
      description: t('admin.pro.updateRoleConfirmDescription', {
        username,
        role: roleLabel(role, t),
      }),
      confirmText: t('admin.pro.updateRoleConfirmButton'),
    });
    if (!confirmed) return;
    try {
      await flocksproUsersApi.updateUserRole(targetUserId, role);
      await loadProUsers();
      toast.success(t('admin.pro.updateRoleSuccess'));
    } catch (err: any) {
      toast.error(
        t('admin.pro.updateRoleFailed'),
        err?.response?.data?.detail || err?.message || t('admin.pro.updateRoleFailed'),
      );
      await loadProUsers();
    }
  };

  const resetOtherUserPassword = async (targetUserId: string, username: string) => {
    const confirmed = await confirm({
      title: t('admin.pro.resetOtherConfirmTitle'),
      description: t('admin.pro.resetOtherConfirmDescription', { username }),
      confirmText: t('admin.pro.resetOtherConfirmButton'),
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      const result = await flocksproUsersApi.resetUserPassword(targetUserId, { force_reset: true });
      if (result.temporary_password) {
        setCredentialModal({
          username,
          password: result.temporary_password,
          description: t('admin.pro.resetOtherDialogDescription'),
          doneText: t('admin.pro.resetOtherDialogDone'),
          warning: t('admin.pro.resetOtherDialogWarning'),
          logoutAfterClose: false,
        });
      } else {
        toast.success(t('admin.pro.resetOtherSuccess'));
      }
    } catch (err: any) {
      toast.error(
        t('admin.pro.resetOtherFailed'),
        err?.response?.data?.detail || err?.message || t('admin.pro.resetOtherFailed'),
      );
    }
  };

  const deleteUser = async (targetUserId: string, username: string) => {
    const confirmed = await confirm({
      title: t('admin.pro.deleteConfirmTitle'),
      description: t('admin.pro.deleteConfirmDescription', { username }),
      confirmText: t('admin.pro.deleteConfirmButton'),
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await flocksproUsersApi.deleteUser(targetUserId);
      await loadProUsers();
      toast.success(t('admin.pro.deleteSuccess'));
    } catch (err: any) {
      toast.error(
        t('admin.pro.deleteFailed'),
        err?.response?.data?.detail || err?.message || t('admin.pro.deleteFailed'),
      );
    }
  };

  if (loading) {
    return (
      <div className="py-8 text-sm text-gray-500">
        {t('admin.pro.loading')}
      </div>
    );
  }

  const sectionDescription = showProAdminView ? t('admin.pro.sectionDescription') : t('admin.sectionDescription');

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('admin.sectionTitle')}</h1>
          <p className="mt-1 text-sm text-gray-500">{sectionDescription}</p>
          {showProAdminView && quota && (
            <p className="mt-2 text-xs text-gray-500">
              {t('admin.pro.quotaHint', {
                adminCount: quota.admin_count,
                adminMax: quota.max_admins,
                memberCount: quota.member_count,
                memberMax: quota.max_members,
              })}
            </p>
          )}
        </div>
        {showProAdminView && (
          <button
            type="button"
            onClick={() => setCreateModalOpen(true)}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            {t('admin.pro.createButton')}
          </button>
        )}
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
            {showProAdminView ? sortedUsers.map((item) => {
              const isCurrent = user?.id === item.id;
              return (
                <tr key={item.id} className="border-t border-gray-100 align-top">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{item.username}</div>
                    {isCurrent && (
                      <div className="mt-1 text-xs text-blue-600">{t('admin.currentAccountTag')}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {isCurrent ? (
                      <span>{roleLabel(item.role, t)}</span>
                    ) : (
                      <select
                        className="rounded-md border border-gray-300 px-2 py-1 text-sm"
                        value={item.role}
                        onChange={(event) => {
                          void updateUserRole(
                            item.id,
                            item.username,
                            event.target.value as 'admin' | 'member',
                            item.role as 'admin' | 'member',
                          );
                        }}
                      >
                        <option value="admin">{t('admin.roleAdmin')}</option>
                        <option value="member">{t('admin.roleMember')}</option>
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {formatDateTime(item.last_login_at, i18n.language)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-3">
                      {isCurrent ? (
                        <button
                          type="button"
                          onClick={() => void resetOwnPassword()}
                          className="text-blue-600 hover:underline"
                        >
                          {t('admin.resetAction')}
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => void resetOtherUserPassword(item.id, item.username)}
                            className="text-blue-600 hover:underline"
                          >
                            {t('admin.pro.resetOtherAction')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void deleteUser(item.id, item.username)}
                            className="text-red-600 hover:underline"
                          >
                            {t('admin.pro.deleteAction')}
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              );
            }) : user && (
              <tr className="border-t border-gray-100 align-top">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-900">{user.username}</div>
                  <div className="mt-1 text-xs text-blue-600">{t('admin.currentAccountTag')}</div>
                </td>
                <td className="px-4 py-3">{roleLabel(user.role, t)}</td>
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

      {createModalOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={() => setCreateModalOpen(false)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <h3 className="text-lg font-semibold text-gray-900">{t('admin.pro.createDialogTitle')}</h3>
              <p className="mt-1 text-sm text-gray-500">{t('admin.pro.createDialogFormDescription')}</p>
              <div className="mt-4 space-y-4">
                <div>
                  <label className="mb-1 block text-sm text-gray-600" htmlFor="create-username">
                    {t('admin.columnUsername')}
                  </label>
                  <input
                    id="create-username"
                    value={createForm.username}
                    onChange={(event) => setCreateForm((prev) => ({ ...prev, username: event.target.value }))}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    placeholder={t('fields.usernamePlaceholder')}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm text-gray-600" htmlFor="create-role">
                    {t('admin.columnRole')}
                  </label>
                  <select
                    id="create-role"
                    value={createForm.role}
                    onChange={(event) => setCreateForm((prev) => ({ ...prev, role: event.target.value as 'admin' | 'member' }))}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  >
                    <option value="member">{t('admin.roleMember')}</option>
                    <option value="admin">{t('admin.roleAdmin')}</option>
                  </select>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setCreateModalOpen(false)}
                  className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
                >
                  {t('admin.pro.cancelButton')}
                </button>
                <button
                  type="button"
                  onClick={() => void createUser()}
                  disabled={creating}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                >
                  {creating ? t('admin.pro.creatingButton') : t('admin.pro.confirmCreateButton')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {credentialModal && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={closeCredentialModal} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
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
          </div>
        </>
      )}
    </div>
  );
}
