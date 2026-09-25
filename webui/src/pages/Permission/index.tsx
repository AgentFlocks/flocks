import PageDialog from '../PageDialog';
import { useState, useEffect } from 'react';
import { IconShield, IconPlus, IconTrash } from '@tabler/icons-react';
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
          <p className="text-danger mb-4">{error}</p>
          <button
            onClick={fetchPermissions}
            className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
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
        icon={<IconShield aria-hidden="true" className="w-8 h-8" />}
      />

      <div className="flex items-center justify-between mb-6">
        <div className="text-sm text-fg-secondary">
          {t('ruleCount', { count: permissions.length })}
        </div>
        <button
          onClick={() => setShowAddDialog(true)}
          className="flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
        >
          <IconPlus aria-hidden="true" className="w-5 h-5" />
          {t('addRule')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {pending.length > 0 && (
          <div className="mb-6" data-testid="pending-requests">
            <div className="text-sm font-medium text-fg-secondary mb-2">
              {t('pending.title', { count: pending.length })}
            </div>
            <div className="bg-surface rounded-control shadow-sm border border-warning overflow-x-auto">
              <table className="min-w-full divide-y divide-line">
                <thead className="bg-warning-soft">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">{t('pending.session')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">{t('table.tool')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">{t('table.pattern')}</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">{t('table.createdAt')}</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-fg-muted uppercase tracking-wider">{t('table.operations')}</th>
                  </tr>
                </thead>
                <tbody className="bg-surface divide-y divide-line">
                  {pending.map((req) => (
                    <tr key={req.id}>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-fg-secondary font-mono">{req.sessionID}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-fg">{req.toolID}</td>
                      <td className="px-6 py-4 text-sm text-fg-secondary break-all">{req.patterns.join(', ')}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-fg-secondary">{new Date(req.time.created).toLocaleString()}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-3">
                        <button onClick={() => handleReply(req.id, true)} className="text-success hover:text-success">{t('action.allow')}</button>
                        <button onClick={() => handleReply(req.id, false)} className="text-danger hover:text-danger">{t('action.deny')}</button>
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
            icon={<IconShield aria-hidden="true" className="w-8 h-8" />}
            title={t('emptyTitle')}
            description={t('emptyDescription')}
            action={
              <button
                onClick={() => setShowAddDialog(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
              >
                <IconPlus aria-hidden="true" className="w-5 h-5" />
                {t('addRule')}
              </button>
            }
          />
        ) : (
          <div className="bg-surface rounded-control shadow-sm border border-line overflow-x-auto">
            <table className="min-w-full divide-y divide-line">
              <thead className="bg-subtle">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">
                    {t('table.tool')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">
                    {t('table.action')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">
                    {t('table.pattern')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider">
                    {t('table.createdAt')}
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-fg-muted uppercase tracking-wider">
                    {t('table.operations')}
                  </th>
                </tr>
              </thead>
              <tbody className="bg-surface divide-y divide-line">
                {permissions.map((perm) => (
                  <tr key={perm.id} className="hover:bg-hover">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-fg">
                      {perm.permission}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`px-2 py-1 inline-flex text-xs font-semibold rounded-pill ${
                          perm.action === 'allow'
                            ? 'bg-success-soft text-success'
                            : 'bg-danger-soft text-danger'
                        }`}
                      >
                        {perm.action === 'allow' ? t('action.allow') : t('action.deny')}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-fg-secondary">
                      {perm.pattern}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-fg-secondary">
                      {perm.createdAt ? new Date(perm.createdAt).toLocaleString() : '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <button
                        onClick={() => handleDelete(perm.id)}
                        className="text-danger hover:text-danger"
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
    <PageDialog onClose={loading ? undefined : onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50">
      <div className="bg-surface rounded-control shadow-xl max-w-md w-full mx-4">
        <div className="px-6 py-4 border-b border-line">
          <h3 className="text-title-card font-semibold text-fg">{t('dialog.title')}</h3>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              {t('dialog.toolName')} <span className="text-danger">*</span>
            </label>
            <input
              type="text"
              value={formData.permission}
              onChange={(e) => setFormData({ ...formData, permission: e.target.value })}
              className="ui-input w-full px-4  border border-line-control rounded-control"
              placeholder="tool_name"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              {t('dialog.action')}
            </label>
            <select
              value={formData.action}
              onChange={(e) =>
                setFormData({ ...formData, action: e.target.value as 'allow' | 'deny' })
              }
              className="ui-select w-full px-4  border border-line-control rounded-control"
            >
              <option value="allow">{t('action.allow')}</option>
              <option value="deny">{t('action.deny')}</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              {t('dialog.pattern')}
            </label>
            <input
              type="text"
              value={formData.pattern}
              onChange={(e) => setFormData({ ...formData, pattern: e.target.value })}
              className="ui-input w-full px-4  border border-line-control rounded-control"
              placeholder="*"
            />
            <p className="text-xs text-fg-muted mt-1">{t('dialog.patternHint')}</p>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-line flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 text-fg-secondary hover:bg-hover rounded-control disabled:bg-disabled disabled:text-fg-disabled"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !formData.permission}
            className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled"
          >
            {loading ? t('dialog.adding') : t('dialog.add')}
          </button>
        </div>
      </div>
    </PageDialog>
  );
}
