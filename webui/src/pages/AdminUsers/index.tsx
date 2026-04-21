import { useEffect, useMemo, useRef, useState } from 'react';
import { Info } from 'lucide-react';
import { adminApi, type AdminUser } from '@/api/admin';
import { authApi } from '@/api/auth';
import CopyButton from '@/components/common/CopyButton';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';

const MAX_ADMIN_USERS = 3;
const MAX_MEMBER_USERS = 20;

function formatRole(role: 'admin' | 'member') {
  return role === 'admin' ? '管理员' : '普通用户';
}

function formatStatus(status: 'active' | 'disabled') {
  return status === 'active' ? '启用中' : '已禁用';
}

function formatDateTime(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export default function AdminUsersPage() {
  const { user } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createUsername, setCreateUsername] = useState('');
  const [role, setRole] = useState<'admin' | 'member'>('member');
  const [showCreateUserModal, setShowCreateUserModal] = useState(false);
  const [creatingUser, setCreatingUser] = useState(false);
  const [resetCredential, setResetCredential] = useState<{
    username: string;
    password: string;
    requiresRelogin: boolean;
  } | null>(null);
  const [createdCredential, setCreatedCredential] = useState<{ username: string; password: string } | null>(null);
  const [showPolicyTip, setShowPolicyTip] = useState(false);
  const [policyTipPinned, setPolicyTipPinned] = useState(false);
  const policyTipRef = useRef<HTMLSpanElement | null>(null);

  const isAdmin = user?.role === 'admin';

  const load = async () => {
    if (!isAdmin) {
      setUsers(
        user
          ? [{
              id: user.id,
              username: user.username,
              role: user.role,
              status: user.status,
              must_reset_password: user.must_reset_password,
              created_at: user.created_at || '',
              updated_at: user.updated_at || '',
              last_login_at: user.last_login_at,
            }]
          : [],
      );
      setLoadingUsers(false);
      setError(null);
      return;
    }
    setLoadingUsers(true);
    setError(null);
    try {
      setUsers(await adminApi.listUsers());
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '加载失败');
    } finally {
      setLoadingUsers(false);
    }
  };

  useEffect(() => {
    void load();
  }, [isAdmin, user]);

  const summary = useMemo(() => {
    const adminCount = users.filter((item) => item.role === 'admin').length;
    const memberCount = users.filter((item) => item.role === 'member').length;
    return {
      adminCount,
      memberCount,
      adminRemaining: Math.max(0, MAX_ADMIN_USERS - adminCount),
      memberRemaining: Math.max(0, MAX_MEMBER_USERS - memberCount),
    };
  }, [users]);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setCreatingUser(true);
      const result = await adminApi.createUser({
        username: createUsername,
        role,
        force_reset: true,
      });
      if (result.temporary_password) {
        setCreatedCredential({
          username: result.username,
          password: result.temporary_password,
        });
      }
      await load();
      toast.success('账号已创建', `${result.username} 已加入系统`);
    } catch (err: any) {
      toast.error('创建失败', err?.response?.data?.detail || err?.message || '创建失败');
    } finally {
      setCreatingUser(false);
    }
  };

  const toggleStatus = async (user: AdminUser) => {
    const nextStatus = user.status === 'active' ? 'disabled' : 'active';
    const confirmed = await confirm({
      title: nextStatus === 'disabled' ? '禁用账号' : '启用账号',
      description: nextStatus === 'disabled'
        ? `确认禁用 ${user.username} 吗？该账号将无法继续登录。`
        : `确认启用 ${user.username} 吗？`,
      confirmText: nextStatus === 'disabled' ? '确认禁用' : '确认启用',
      variant: nextStatus === 'disabled' ? 'warning' : 'default',
    });
    if (!confirmed) return;
    try {
      await adminApi.updateStatus(user.id, nextStatus);
      await load();
      toast.success('状态已更新', `${user.username} 当前为${formatStatus(nextStatus)}`);
    } catch (err: any) {
      toast.error('更新状态失败', err?.response?.data?.detail || err?.message || '更新状态失败');
    }
  };

  const resetPassword = async (target: AdminUser) => {
    const confirmed = await confirm({
      title: '重置密码',
      description: `确认重置 ${target.username} 的密码吗？系统会清理该账号现有登录态，并要求用户下次登录后修改密码。`,
      confirmText: '确认重置',
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      const result = await adminApi.resetPassword(target.id, {
        new_password: undefined,
        force_reset: true,
      });
      const isCurrentUser = target.id === user?.id;
      if (result.temporary_password) {
        setResetCredential({
          username: target.username,
          password: result.temporary_password,
          requiresRelogin: isCurrentUser,
        });
        if (!isCurrentUser) {
          toast.success('密码已重置', `已为 ${target.username} 生成一次性密码`);
        }
      } else {
        toast.success('密码已重置', target.username);
      }
      if (!isCurrentUser) {
        await load();
      }
    } catch (err: any) {
      toast.error('重置失败', err?.response?.data?.detail || err?.message || '重置失败');
    }
  };

  const updateRole = async (target: AdminUser, nextRole: 'admin' | 'member') => {
    const confirmed = await confirm({
      title: '修改角色',
      description: `确认将 ${target.username} 调整为${formatRole(nextRole)}吗？`,
      confirmText: '确认修改',
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      await adminApi.updateRole(target.id, nextRole);
      await load();
      toast.success('角色已更新', `${target.username} 现在是${formatRole(nextRole)}`);
    } catch (err: any) {
      toast.error('角色更新失败', err?.response?.data?.detail || err?.message || '角色更新失败');
    }
  };

  const deleteUser = async (target: AdminUser) => {
    const confirmed = await confirm({
      title: '删除账号',
      description: `确认删除 ${target.username} 吗？该账号的登录会话会立即失效，但历史业务会话会保留，并在未来同名账号重建后继续可见。`,
      confirmText: '确认删除',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      const result = await adminApi.deleteUser(target.id);
      await load();
      toast.success('账号已删除', `保留历史会话 ${result.retained_sessions} 条`);
    } catch (err: any) {
      toast.error('删除失败', err?.response?.data?.detail || err?.message || '删除失败');
    }
  };

  const roleQuotaReached = role === 'admin'
    ? summary.adminCount >= MAX_ADMIN_USERS
    : summary.memberCount >= MAX_MEMBER_USERS;

  useEffect(() => {
    if (!policyTipPinned) return undefined;

    const handlePointerDown = (event: MouseEvent) => {
      if (!policyTipRef.current?.contains(event.target as Node)) {
        setPolicyTipPinned(false);
        setShowPolicyTip(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [policyTipPinned]);

  const closeCreateUserModal = () => {
    if (creatingUser) return;
    setShowCreateUserModal(false);
    setCreatedCredential(null);
    setCreateUsername('');
    setRole('member');
  };

  const closeResetCredentialModal = () => {
    const requiresRelogin = resetCredential?.requiresRelogin;
    setResetCredential(null);
    if (requiresRelogin && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('flocks:auth-expired'));
    }
  };

  const resetOwnPassword = async () => {
    const confirmed = await confirm({
      title: '重置密码',
      description: '确认重置当前账号密码吗？系统会清理当前登录态，并生成一次性密码供你重新登录。',
      confirmText: '确认重置',
      variant: 'warning',
    });
    if (!confirmed) return;
    try {
      const result = await authApi.resetPassword();
      if (result.temporary_password && user) {
        setResetCredential({
          username: user.username,
          password: result.temporary_password,
          requiresRelogin: true,
        });
      } else {
        toast.success('密码已重置');
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new Event('flocks:auth-expired'));
        }
      }
    } catch (err: any) {
      toast.error('重置失败', err?.response?.data?.detail || err?.message || '重置失败');
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-start justify-between gap-4">
          <h1 className="text-xl font-semibold text-gray-900">账号管理</h1>
          {isAdmin && (
            <button
              type="button"
              onClick={() => setShowCreateUserModal(true)}
              className="flex-shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              创建账号
            </button>
          )}
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-gray-500">
          普通用户仅能管理自己的账号与密码；管理员可管理全量账号、角色、重置密码和删除账号。
          {isAdmin && (
            <span
              ref={policyTipRef}
              className="relative inline-flex"
              onMouseEnter={() => setShowPolicyTip(true)}
              onMouseLeave={() => {
                if (!policyTipPinned) {
                  setShowPolicyTip(false);
                }
              }}
            >
              <button
                type="button"
                aria-label="查看账号策略"
                onClick={() => {
                  const nextPinned = !policyTipPinned;
                  setPolicyTipPinned(nextPinned);
                  setShowPolicyTip(nextPinned || !showPolicyTip);
                }}
                className="inline-flex h-5 w-5 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              >
                <Info className="h-4 w-4" />
              </button>
              {showPolicyTip && (
                <div className="absolute left-0 top-7 z-10 w-80 rounded-xl border border-gray-200 bg-white p-4 text-left shadow-lg">
                  <div className="text-sm font-semibold text-gray-900">账号策略</div>
                  <div className="mt-2 space-y-2 text-sm text-gray-600">
                    <div>管理员最多 {MAX_ADMIN_USERS} 个，普通用户最多 {MAX_MEMBER_USERS} 个。</div>
                    <div>删除账号会保留其历史会话。</div>
                    <div>重置密码会清理目标账号的所有登录会话，并要求其下次登录后立即修改密码。</div>
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                      <div className="text-xs text-gray-500">管理员</div>
                      <div className="mt-1 font-medium text-gray-900">{summary.adminCount} / {MAX_ADMIN_USERS}</div>
                      <div className="mt-1 text-xs text-gray-500">剩余 {summary.adminRemaining}</div>
                    </div>
                    <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                      <div className="text-xs text-gray-500">普通用户</div>
                      <div className="mt-1 font-medium text-gray-900">{summary.memberCount} / {MAX_MEMBER_USERS}</div>
                      <div className="mt-1 text-xs text-gray-500">剩余 {summary.memberRemaining}</div>
                    </div>
                  </div>
                </div>
              )}
            </span>
          )}
        </p>
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}

      {loadingUsers ? (
        <div>加载中...</div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left px-4 py-3">用户名</th>
                <th className="text-left px-4 py-3">角色</th>
                <th className="text-left px-4 py-3">状态</th>
                <th className="text-left px-4 py-3">最近登录</th>
                <th className="text-left px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((item) => {
                const isCurrentUser = item.id === user?.id;
                const canPromote = item.role !== 'admin' && summary.adminCount < MAX_ADMIN_USERS;
                const canDemote = item.role === 'admin' && summary.memberCount < MAX_MEMBER_USERS;
                return (
                  <tr key={item.id} className="border-t border-gray-100 align-top">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{item.username}</div>
                      {isCurrentUser && <div className="mt-1 text-xs text-blue-600">当前登录账号</div>}
                    </td>
                    <td className="px-4 py-3">{formatRole(item.role)}</td>
                    <td className="px-4 py-3">{formatStatus(item.status)}</td>
                    <td className="px-4 py-3 whitespace-nowrap">{formatDateTime(item.last_login_at)}</td>
                    <td className="px-4 py-3">
                      {isAdmin ? (
                        <div className="flex flex-wrap gap-x-3 gap-y-2">
                          <button onClick={() => void resetPassword(item)} className="text-blue-600 hover:underline">重置密码</button>
                          <button onClick={() => void toggleStatus(item)} disabled={isCurrentUser} className="text-amber-600 hover:underline disabled:text-gray-300 disabled:no-underline">
                            {item.status === 'active' ? '禁用' : '启用'}
                          </button>
                          {item.role === 'admin' ? (
                            <button onClick={() => void updateRole(item, 'member')} disabled={!canDemote || isCurrentUser} className="text-violet-600 hover:underline disabled:text-gray-300 disabled:no-underline">
                              设为普通用户
                            </button>
                          ) : (
                            <button onClick={() => void updateRole(item, 'admin')} disabled={!canPromote || isCurrentUser} className="text-violet-600 hover:underline disabled:text-gray-300 disabled:no-underline">
                              设为管理员
                            </button>
                          )}
                          <button onClick={() => void deleteUser(item)} disabled={isCurrentUser} className="text-red-600 hover:underline disabled:text-gray-300 disabled:no-underline">
                            删除
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void resetOwnPassword()}
                          className="text-blue-600 hover:underline"
                        >
                          重置密码
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showCreateUserModal && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={closeCreateUserModal} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">创建账号</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    {createdCredential
                      ? '请复制并妥善转交一次性初始密码，用户首次登录后需立即修改。'
                      : '填写账号名后，系统会自动生成一次性初始密码，首次登录后需立即修改。'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={closeCreateUserModal}
                  className="text-sm text-gray-400 hover:text-gray-600"
                >
                  关闭
                </button>
              </div>
              {createdCredential ? (
                <div className="mt-5 space-y-4">
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-sm font-medium text-amber-900">一次性密码已生成，请立即复制</div>
                    <div className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-3">
                      <div className="text-xs text-gray-500">账号名</div>
                      <div className="mt-1 font-medium text-gray-900">{createdCredential.username}</div>
                    </div>
                    <div className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-xs text-gray-500">一次性初始密码</div>
                          <div className="mt-1 font-mono text-base font-semibold text-gray-900">{createdCredential.password}</div>
                        </div>
                        <CopyButton text={createdCredential.password} />
                      </div>
                    </div>
                    <div className="mt-3 text-sm text-amber-900">复制后请及时转交给用户，关闭弹窗后将无法再次直接看到这串密码。</div>
                  </div>
                  <div className="flex justify-end gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        setCreatedCredential(null);
                        setCreateUsername('');
                        setRole('member');
                      }}
                      className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
                    >
                      继续创建
                    </button>
                    <button
                      type="button"
                      onClick={closeCreateUserModal}
                      className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                    >
                      完成
                    </button>
                  </div>
                </div>
              ) : (
                <form onSubmit={createUser} className="mt-5 space-y-4">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">账号名</label>
                    <input
                      value={createUsername}
                      onChange={(e) => setCreateUsername(e.target.value)}
                      placeholder="请输入账号名"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2"
                      required
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">角色</label>
                    <select
                      value={role}
                      onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
                      className="w-full rounded-lg border border-gray-300 px-3 py-2"
                    >
                      <option value="member">普通用户</option>
                      <option value="admin">管理员</option>
                    </select>
                    <p className="mt-1 text-xs text-gray-500">
                      {roleQuotaReached ? `${formatRole(role)}配额已满，当前无法创建。` : `${formatRole(role)}剩余 ${role === 'admin' ? summary.adminRemaining : summary.memberRemaining} 个名额。`}
                    </p>
                  </div>
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                    创建成功后会在当前弹窗中展示一次性初始密码，请复制后再关闭。
                  </div>
                  <div className="flex justify-end gap-3">
                    <button
                      type="button"
                      onClick={closeCreateUserModal}
                      className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
                      disabled={creatingUser}
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      disabled={creatingUser || roleQuotaReached}
                      className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                    >
                      {creatingUser ? '创建中...' : '创建并生成密码'}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </div>
        </>
      )}

      {resetCredential && (
        <>
          <div className="fixed inset-0 z-40 bg-black/40" onClick={closeResetCredentialModal} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">一次性密码已生成</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    {resetCredential.requiresRelogin
                      ? '当前账号已被重置，请先复制一次性密码，关闭后将返回登录页。'
                      : '请复制并妥善转交给用户，系统会要求其登录后立即修改密码。'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={closeResetCredentialModal}
                  className="text-sm text-gray-400 hover:text-gray-600"
                >
                  关闭
                </button>
              </div>
              <div className="mt-5 space-y-4">
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <div className="rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="text-xs text-gray-500">账号名</div>
                    <div className="mt-1 font-medium text-gray-900">{resetCredential.username}</div>
                  </div>
                  <div className="mt-3 rounded-lg border border-amber-200 bg-white px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs text-gray-500">一次性密码</div>
                        <div className="mt-1 font-mono text-base font-semibold text-gray-900">{resetCredential.password}</div>
                      </div>
                      <CopyButton text={resetCredential.password} />
                    </div>
                  </div>
                  <div className="mt-3 text-sm text-amber-900">请先复制保存，关闭弹窗后将无法再次直接看到这串密码。</div>
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={closeResetCredentialModal}
                    className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
                  >
                    {resetCredential.requiresRelogin ? '已复制，返回登录' : '完成'}
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
