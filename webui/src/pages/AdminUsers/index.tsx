import { useEffect, useState } from 'react';
import { adminApi, type AdminUser } from '@/api/admin';

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'admin' | 'member'>('member');

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await adminApi.listUsers());
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await adminApi.createUser({ username, password, role });
      setUsername('');
      setPassword('');
      setRole('member');
      await load();
    } catch (err: any) {
      alert(err?.response?.data?.detail || err?.message || '创建失败');
    }
  };

  const toggleStatus = async (user: AdminUser) => {
    const nextStatus = user.status === 'active' ? 'disabled' : 'active';
    try {
      await adminApi.updateStatus(user.id, nextStatus);
      await load();
    } catch (err: any) {
      alert(err?.response?.data?.detail || err?.message || '更新状态失败');
    }
  };

  const resetPassword = async (user: AdminUser) => {
    const pwd = window.prompt(`请输入 ${user.username} 的临时密码（为空则自动生成）`, '');
    try {
      const result = await adminApi.resetPassword(user.id, {
        new_password: (pwd || '').trim() || undefined,
        force_reset: true,
      });
      if (result.temporary_password) {
        alert(`已生成临时密码：${result.temporary_password}`);
      } else {
        alert('密码已重置');
      }
    } catch (err: any) {
      alert(err?.response?.data?.detail || err?.message || '重置失败');
    }
  };

  if (loading) return <div>加载中...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">账号管理</h1>
        <p className="text-sm text-gray-500 mt-1">仅管理员可访问，支持新增账号、禁用账号和重置密码。</p>
      </div>

      <form onSubmit={createUser} className="bg-white border border-gray-200 rounded-xl p-4 grid grid-cols-1 md:grid-cols-4 gap-3">
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名"
          className="border border-gray-300 rounded-lg px-3 py-2"
          required
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          placeholder="初始密码"
          className="border border-gray-300 rounded-lg px-3 py-2"
          required
          minLength={8}
        />
        <select value={role} onChange={(e) => setRole(e.target.value as 'admin' | 'member')} className="border border-gray-300 rounded-lg px-3 py-2">
          <option value="member">member</option>
          <option value="admin">admin</option>
        </select>
        <button type="submit" className="bg-slate-900 text-white rounded-lg px-4 py-2 font-medium">新增账号</button>
      </form>

      {error && <div className="text-sm text-red-600">{error}</div>}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-4 py-2">用户名</th>
              <th className="text-left px-4 py-2">角色</th>
              <th className="text-left px-4 py-2">状态</th>
              <th className="text-left px-4 py-2">首次改密</th>
              <th className="text-left px-4 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-gray-100">
                <td className="px-4 py-2">{u.username}</td>
                <td className="px-4 py-2">{u.role}</td>
                <td className="px-4 py-2">{u.status}</td>
                <td className="px-4 py-2">{u.must_reset_password ? '是' : '否'}</td>
                <td className="px-4 py-2 space-x-2">
                  <button onClick={() => void resetPassword(u)} className="text-blue-600 hover:underline">重置密码</button>
                  <button onClick={() => void toggleStatus(u)} className="text-amber-600 hover:underline">
                    {u.status === 'active' ? '禁用' : '启用'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
