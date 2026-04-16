import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';

export default function SetupAdminPage() {
  const { bootstrapAdmin } = useAuth();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await bootstrapAdmin(username, password);
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '初始化失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <form onSubmit={onSubmit} className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">初始化管理员账号</h1>
          <p className="text-sm text-gray-500 mt-1">
            当前实例首次启用账号系统。请创建管理员账号，历史旧对话会自动归属到该管理员。
          </p>
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">管理员用户名</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            required
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">管理员密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            required
            minLength={8}
          />
        </div>
        <div>
          <label className="text-sm text-gray-700 block mb-1">确认密码</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
            required
            minLength={8}
          />
        </div>
        {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full bg-slate-900 text-white rounded-lg py-2.5 font-medium hover:bg-slate-800 disabled:opacity-60"
        >
          {submitting ? '初始化中...' : '创建管理员并登录'}
        </button>
      </form>
    </div>
  );
}
