import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';

export default function ForceChangePasswordPage() {
  const { user, changePassword, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致');
      return;
    }

    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setSuccess('密码已更新，正在恢复正常访问权限');
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '修改密码失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <form onSubmit={onSubmit} className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">请先修改密码</h1>
          <p className="text-sm text-gray-500 mt-1">
            账号
            {' '}
            <span className="font-medium text-gray-700">{user?.username || '-'}</span>
            {' '}
            当前使用的是一次性/重置后的密码，继续使用系统前必须先设置新密码。
          </p>
        </div>

        <div>
          <label className="text-sm text-gray-700 block mb-1">当前密码</label>
          <input
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            placeholder="请输入当前密码"
            autoComplete="current-password"
            required
          />
        </div>

        <div>
          <label className="text-sm text-gray-700 block mb-1">新密码</label>
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            placeholder="请输入新密码（至少 8 位）"
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>

        <div>
          <label className="text-sm text-gray-700 block mb-1">确认新密码</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            placeholder="请再次输入新密码"
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>

        {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
        {success && <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">{success}</div>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 disabled:opacity-60"
        >
          {submitting ? '提交中...' : '更新密码'}
        </button>

        <button
          type="button"
          onClick={() => void logout()}
          className="w-full text-sm text-gray-500 hover:text-gray-700"
        >
          退出登录
        </button>
      </form>
    </div>
  );
}
