import { useState, useEffect } from 'react';
import { Shield, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import client from '@/api/client';

interface Permission {
  id: string;
  permission: string;
  action: 'allow' | 'deny';
  pattern: string;
  createdAt: number | null;
}

// A tool call waiting for a human decision (GET /api/permission).
interface PendingRequest {
  id: string;
  sessionID: string;
  toolID: string;
  permission: string;
  patterns: string[];
  time: { created: number };
}

export default function PermissionPage() {
  const { t } = useTranslation('permission');
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [pending, setPending] = useState<PendingRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddDialog, setShowAddDialog] = useState(false);

  useEffect(() => {
    fetchPermissions();
  }, []);

  const fetchPermissions = async () => {
    try {
      setLoading(true);
      setError(null);
      const [rulesRes, pendingRes] = await Promise.all([
        client.get('/api/permission/rules'),
        client.get('/api/permission'),
      ]);
      setPermissions(rulesRes.data);
      setPending(pendingRes.data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t('confirmDelete'))) return;

    try {
      await client.delete(`/api/permission/rules/${encodeURIComponent(id)}`);
      fetchPermissions();
    } catch (err: any) {
      alert(`${t('deleteFailed')}: ${err.message}`);
    }
  };

  const handleReply = async (id: string, allow: boolean) => {
    try {
      await client.post(`/api/permission/${id}/reply`, { allow });
      fetchPermissions();
    } catch (err: any) {
      alert(`${t('replyFailed')}: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={fetchPermissions}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Shield className="w-8 h-8" />}
      />

      <div className="flex items-center justify-between mb-6">
        <div className="text-sm text-gray-600">
          {t('ruleCount', { count: permissions.length })}
        </div>
        <button
          onClick={() => setShowAddDialog(true)}
          className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
        >
          <Plus className="w-5 h-5" />
          {t('addRule')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {pending.length > 0 && (
          <div className="mb-6" data-testid="pending-requests">
            <div className="text-sm font-medium text-gray-700 mb-2">
              {t('pending.title', { count: pending.length })}
            </div>
            <div className="bg-white rounded-lg shadow-sm border border-amber-200 overflow-hidden max-md:overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 max-md:min-w-[40rem]">
                <thead className="bg-amber-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('pending.session')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('table.tool')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('table.pattern')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">{t('table.createdAt')}</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">{t('table.operations')}</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {pending.map((req) => (
                    <tr key={req.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-600">{req.sessionID}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{req.toolID}</td>
                      <td className="px-6 py-4 text-sm text-gray-600 break-all">{req.patterns.join(', ')}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">{new Date(req.time.created).toLocaleString()}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-3">
                        <button onClick={() => handleReply(req.id, true)} className="text-green-600 hover:text-green-900">{t('action.allow')}</button>
                        <button onClick={() => handleReply(req.id, false)} className="text-red-600 hover:text-red-900">{t('action.deny')}</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {permissions.length === 0 ? (
          <EmptyState
            icon={<Shield className="w-16 h-16" />}
            title={t('emptyTitle')}
            description={t('emptyDescription')}
            action={
              <button
                onClick={() => setShowAddDialog(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Plus className="w-5 h-5" />
                {t('addRule')}
              </button>
            }
          />
        ) : (
          <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden max-md:overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 max-md:min-w-[40rem]">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {t('table.tool')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {t('table.action')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {t('table.pattern')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {t('table.createdAt')}
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {t('table.operations')}
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {permissions.map((perm) => (
                  <tr key={perm.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {perm.permission}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${
                          perm.action === 'allow'
                            ? 'bg-green-100 text-green-800'
                            : 'bg-red-100 text-red-800'
                        }`}
                      >
                        {perm.action === 'allow' ? t('action.allow') : t('action.deny')}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      {perm.pattern}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      {perm.createdAt ? new Date(perm.createdAt).toLocaleString() : '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <button
                        onClick={() => handleDelete(perm.id)}
                        className="text-red-600 hover:text-red-900"
                      >
                        {t('common:button.delete')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showAddDialog && (
        <AddPermissionDialog
          onClose={() => setShowAddDialog(false)}
          onAdded={() => {
            fetchPermissions();
            setShowAddDialog(false);
          }}
        />
      )}
    </div>
  );
}

function AddPermissionDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const { t } = useTranslation('permission');
  const [formData, setFormData] = useState({
    permission: '',
    action: 'allow' as 'allow' | 'deny',
    pattern: '*',
  });
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!formData.permission) {
      alert(t('fillToolName'));
      return;
    }

    try {
      setLoading(true);
      await client.post('/api/permission/rules', formData);
      onAdded();
    } catch (err: any) {
      alert(`${t('addFailed')}: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4">
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold text-gray-900">{t('dialog.title')}</h3>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('dialog.toolName')} <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={formData.permission}
              onChange={(e) => setFormData({ ...formData, permission: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500"
              placeholder="tool_name"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('dialog.action')}
            </label>
            <select
              value={formData.action}
              onChange={(e) =>
                setFormData({ ...formData, action: e.target.value as 'allow' | 'deny' })
              }
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              <option value="allow">{t('action.allow')}</option>
              <option value="deny">{t('action.deny')}</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('dialog.pattern')}
            </label>
            <input
              type="text"
              value={formData.pattern}
              onChange={(e) => setFormData({ ...formData, pattern: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500"
              placeholder="*"
            />
            <p className="text-xs text-gray-500 mt-1">{t('dialog.patternHint')}</p>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg disabled:opacity-50"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !formData.permission}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
          >
            {loading ? t('dialog.adding') : t('dialog.add')}
          </button>
        </div>
      </div>
    </div>
  );
}
