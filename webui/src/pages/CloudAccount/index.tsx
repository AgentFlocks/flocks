import { useEffect, useState } from 'react';
import { cloudAccountApi, type CloudAccountInfo } from '@/api/cloudAccount';
import { useAuth } from '@/contexts/AuthContext';

export default function CloudAccountPage() {
  const { user } = useAuth();
  const [info, setInfo] = useState<CloudAccountInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [provider, setProvider] = useState('threatbook-passport');
  const [accountId, setAccountId] = useState('');
  const [accountName, setAccountName] = useState('');
  const [token, setToken] = useState('');
  const [mcpQuota, setMcpQuota] = useState('');
  const [apiQuota, setApiQuota] = useState('');
  const [balance, setBalance] = useState('');

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setInfo(await cloudAccountApi.get());
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.response?.data?.detail || err?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const bind = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const payload = {
        provider,
        account_id: accountId,
        account_name: accountName || undefined,
        token: token || undefined,
        mcp_quota: mcpQuota || undefined,
        api_quota: apiQuota || undefined,
        balance: balance || undefined,
      };
      const result = await cloudAccountApi.bind(payload);
      setInfo(result);
      alert('云账号绑定成功');
    } catch (err: any) {
      alert(err?.response?.data?.detail || err?.message || '绑定失败');
    }
  };

  if (loading) return <div>加载中...</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">云账号管理</h1>
        <p className="text-sm text-gray-500 mt-1">
          本地账号与云账号完全隔离。单个 Flocks 实例仅绑定一个云账号，可由管理员重绑并审计。
        </p>
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}

      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-2">
        <div className="text-sm font-medium text-gray-900">当前绑定</div>
        {!info ? (
          <div className="text-sm text-gray-500">尚未绑定云账号</div>
        ) : (
          <div className="text-sm text-gray-700 space-y-1">
            <div>Provider: {info.provider}</div>
            <div>账号ID: {info.account_id}</div>
            <div>账号名: {info.account_name || '-'}</div>
            <div>Token: {info.token_masked || '-'}</div>
            <div>MCP额度: {info.mcp_quota || '-'}</div>
            <div>API额度: {info.api_quota || '-'}</div>
            <div>余额: {info.balance || '-'}</div>
          </div>
        )}
      </div>

      {user?.role === 'admin' && (
        <form onSubmit={bind} className="bg-white border border-gray-200 rounded-xl p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
          <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="provider" className="border border-gray-300 rounded-lg px-3 py-2" required />
          <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="account_id" className="border border-gray-300 rounded-lg px-3 py-2" required />
          <input value={accountName} onChange={(e) => setAccountName(e.target.value)} placeholder="account_name" className="border border-gray-300 rounded-lg px-3 py-2" />
          <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="token (可选)" className="border border-gray-300 rounded-lg px-3 py-2" />
          <input value={mcpQuota} onChange={(e) => setMcpQuota(e.target.value)} placeholder="MCP额度" className="border border-gray-300 rounded-lg px-3 py-2" />
          <input value={apiQuota} onChange={(e) => setApiQuota(e.target.value)} placeholder="API额度" className="border border-gray-300 rounded-lg px-3 py-2" />
          <input value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="余额" className="border border-gray-300 rounded-lg px-3 py-2" />
          <button type="submit" className="bg-slate-900 text-white rounded-lg px-4 py-2 font-medium">绑定/重绑云账号</button>
        </form>
      )}
    </div>
  );
}
