import { useState } from 'react';
import { authApi } from '@/api/auth';
import CopyButton from '@/components/common/CopyButton';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';

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
  const [resetCredential, setResetCredential] = useState<{
    username: string;
    password: string;
  } | null>(null);

  const closeResetCredentialModal = () => {
    setResetCredential(null);
    if (typeof window !== 'undefined') {
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
        <h1 className="text-xl font-semibold text-gray-900">账号管理</h1>
        <p className="mt-1 text-sm text-gray-500">管理当前管理员账号与密码。</p>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-4 py-3">用户名</th>
              <th className="text-left px-4 py-3">角色</th>
              <th className="text-left px-4 py-3">最近登录</th>
              <th className="text-left px-4 py-3">操作</th>
            </tr>
          </thead>
          <tbody>
            {user && (
              <tr className="border-t border-gray-100 align-top">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-900">{user.username}</div>
                  <div className="mt-1 text-xs text-blue-600">当前登录账号</div>
                </td>
                <td className="px-4 py-3">管理员</td>
                <td className="px-4 py-3 whitespace-nowrap">{formatDateTime(user.last_login_at)}</td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => void resetOwnPassword()}
                    className="text-blue-600 hover:underline"
                  >
                    重置密码
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
                  <h3 className="text-lg font-semibold text-gray-900">一次性密码已生成</h3>
                  <p className="mt-1 text-sm text-gray-500">
                    当前账号已被重置，请先复制一次性密码，关闭后将返回登录页。
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
                    已复制，返回登录
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
