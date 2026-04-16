import { useEffect, useState } from 'react';
import { adminApi, type AuditLog } from '@/api/admin';

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const run = async () => {
      setLoading(true);
      setError(null);
      try {
        setLogs(await adminApi.listAuditLogs());
      } catch (err: any) {
        setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '加载失败');
      } finally {
        setLoading(false);
      }
    };
    void run();
  }, []);

  if (loading) return <div>加载中...</div>;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">审计日志</h1>
        <p className="text-sm text-gray-500 mt-1">仅管理员可查看全量账号/密码/共享/云绑定审计日志。</p>
      </div>
      {error && <div className="text-sm text-red-600">{error}</div>}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-4 py-2">时间</th>
              <th className="text-left px-4 py-2">动作</th>
              <th className="text-left px-4 py-2">结果</th>
              <th className="text-left px-4 py-2">操作者</th>
              <th className="text-left px-4 py-2">目标</th>
              <th className="text-left px-4 py-2">IP</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((item) => (
              <tr key={item.id} className="border-t border-gray-100">
                <td className="px-4 py-2 whitespace-nowrap">{item.created_at}</td>
                <td className="px-4 py-2">{item.action}</td>
                <td className="px-4 py-2">{item.result}</td>
                <td className="px-4 py-2">{item.operator_user_id || '-'}</td>
                <td className="px-4 py-2">{item.target_user_id || '-'}</td>
                <td className="px-4 py-2">{item.ip || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
