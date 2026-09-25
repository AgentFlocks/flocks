import { useState, useEffect, useRef, useCallback } from 'react';
import {
  IconShield,
  IconInfoCircle,
  IconCircleCheck,
  IconCircleX,
  IconRefresh,
  IconActivity,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';

interface Credentials {
  // Backend returns JSON ``null`` (not ``undefined``) for empty fields,
  // so widen these to ``string | null | undefined`` to stay assignable from
  // both ProviderCredentials and MCPCredentials.
  secret_id?: string | null;
  api_key_masked?: string | null;
  has_credential: boolean;
}

interface TestResult {
  success: boolean;
  message: string;
  latency_ms?: number;
  tool_tested?: string;
}

interface CredentialsAPI {
  load: () => Promise<Credentials>;
  save: (apiKey: string) => Promise<void>;
  test: () => Promise<TestResult>;
  delete: () => Promise<void>;
}

interface CredentialsCardProps {
  api: CredentialsAPI;
  accent?: 'blue' | 'purple';
  showTimer?: boolean;
  onTestingStart?: () => void;
  onTestingEnd?: () => void;
}

export default function CredentialsCard({
  api,
  showTimer = false,
  onTestingStart,
  onTestingEnd,
}: CredentialsCardProps) {
  const { t } = useTranslation('tool');
  const toast = useToast();
  const [credentials, setCredentials] = useState<Credentials | null>(null);
  const [editing, setEditing] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testElapsed, setTestElapsed] = useState(0);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadCredentials = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.load();
      setCredentials(data);
    } catch {
      setCredentials(null);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  useEffect(() => {
    return () => {
      if (testTimerRef.current) clearInterval(testTimerRef.current);
    };
  }, []);

  const handleSave = async () => {
    if (!apiKey) {
      toast.warning(t('alert.apiKeyRequired'));
      return;
    }
    setSaving(true);
    try {
      await api.save(apiKey);
      toast.success(t('alert.credSaved'));
      setEditing(false);
      setApiKey('');
      await loadCredentials();
    } catch (err: any) {
      toast.error(t('credentials.saveFailedTitle'), err.message || t('alert.unknownError'));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    setTestElapsed(0);
    onTestingStart?.();

    if (showTimer) {
      const startTime = Date.now();
      testTimerRef.current = setInterval(() => {
        setTestElapsed((Date.now() - startTime) / 1000);
      }, 100);
    }

    try {
      const result = await api.test();
      setTestResult(result);
    } catch (err: any) {
      setTestResult({
        success: false,
        message: t('alert.testFailed', { error: err.message || t('alert.unknownError') }),
      });
    } finally {
      if (testTimerRef.current) {
        clearInterval(testTimerRef.current);
        testTimerRef.current = null;
      }
      setTesting(false);
      onTestingEnd?.();
    }
  };

  const handleDelete = async () => {
    try {
      await api.delete();
      toast.success(t('alert.credDeleted'));
      setShowDeleteConfirm(false);
      await loadCredentials();
    } catch (err: any) {
      toast.error(t('credentials.deleteFailedTitle'), err.message || t('alert.unknownError'));
    }
  };

  const accentBtn = 'bg-action hover:bg-action-hover';

  if (loading) {
    return (
      <div className="bg-surface rounded-control border border-line p-4">
        <div className="flex justify-center py-4"><LoadingSpinner /></div>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-control border border-line p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-fg flex items-center">
          <IconShield aria-hidden="true" className="w-4 h-4 mr-2 text-fg-muted" />
          {t('credentials.title')}
        </h4>
        {credentials?.has_credential && !editing && !showDeleteConfirm && (
          <button onClick={() => setShowDeleteConfirm(true)} className="text-xs text-danger hover:text-danger">
            {t('credentials.delete')}
          </button>
        )}
        {showDeleteConfirm && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-danger">{t('alert.confirmDeleteCred')}</span>
            <button onClick={handleDelete} className="text-xs text-action-fg bg-action-danger hover:bg-action-danger px-2 py-0.5 rounded-tag">{t('button.yes')}</button>
            <button onClick={() => setShowDeleteConfirm(false)} className="text-xs text-fg-secondary hover:text-fg">{t('button.cancel')}</button>
          </div>
        )}
      </div>

      {!editing ? (
        <div className="space-y-3">
          {credentials?.has_credential && credentials.secret_id && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">Secret ID</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">{credentials.secret_id}</code>
            </div>
          )}
          {credentials?.api_key_masked && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">API Key</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">{credentials.api_key_masked}</code>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
              onClick={() => setEditing(true)}
              className="flex-1 px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
            >
              {credentials?.has_credential ? t('credentials.modify') : t('credentials.configure')}
            </button>
            {credentials?.has_credential && (
              <button
                onClick={handleTest}
                disabled={testing}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 border rounded-control text-sm transition-colors ${
                  testing
                    ? 'border-danger bg-danger-soft text-danger cursor-not-allowed'
                    : 'border-line-control text-fg-secondary hover:bg-hover'
                }`}
              >
                {testing ? (
                  <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('credentials.testing')}</>
                ) : (
                  <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('credentials.testConn')}</>
                )}
              </button>
            )}
          </div>

          {/* Testing progress (optional timer) */}
          {testing && showTimer && (
            <div className="p-3 rounded-control border border-danger bg-danger-soft text-danger">
              <div className="flex items-center">
                <div className="relative mr-3">
                  <div className="w-8 h-8 rounded-pill border-2 border-danger border-t-danger animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{t('credentials.testingProgress')}</div>
                  <div className="text-xs mt-0.5 text-danger">
                    {t('credentials.testingWait')}
                    <span className="ml-2 font-mono tabular-nums">{testElapsed.toFixed(1)}s</span>
                  </div>
                </div>
              </div>
              <div className="mt-2.5 h-1 bg-danger-soft rounded-pill overflow-hidden">
                <div
                  className="h-full bg-danger rounded-pill animate-pulse"
                  style={{ width: `${Math.min(95, testElapsed * 8)}%`, transition: 'width 0.3s ease-out' }}
                />
              </div>
            </div>
          )}

          {/* Test result */}
          {!testing && testResult && (
            <div className={`p-2.5 rounded-tag text-xs ${
              testResult.success
                ? 'bg-success-soft border border-success text-success'
                : 'bg-danger-soft border border-danger text-danger'
            }`}>
              <div className="flex items-start">
                {testResult.success
                  ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
                  : <IconCircleX aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />}
                <div className="flex-1">
                  <div>{testResult.message}</div>
                  {testResult.latency_ms != null && (
                    <div className="mt-1 text-fg-muted">{t('credentials.latency')}: {testResult.latency_ms}ms</div>
                  )}
                  {testResult.tool_tested && (
                    <div className="mt-1 text-fg-muted">{t('credentials.toolTested')}: {testResult.tool_tested}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-fg-muted mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('credentials.apiKeyPlaceholder')}
              className={`w-full px-3 py-2 border border-line-control rounded-control text-sm font-mono `}
            />
          </div>
          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className={`flex-1 px-3 py-2 ${accentBtn} text-action-fg rounded-control text-sm disabled:bg-disabled disabled:text-fg-disabled`}
            >
              {saving ? t('credentials.saving') : t('credentials.save')}
            </button>
            <button
              onClick={() => { setEditing(false); setApiKey(''); }}
              className="px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
            >
              {t('button.cancel')}
            </button>
          </div>
          <div className="p-2 bg-warning-soft border border-warning rounded-tag text-xs text-warning">
            <div className="flex items-start">
              <IconInfoCircle aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
              <div>{t('credentials.storageNote')}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Build a CredentialsAPI adapter for MCP servers */
export function mcpCredentialsAdapter(serverName: string, mcpAPI: typeof import('@/api/mcp').mcpAPI): CredentialsAPI {
  return {
    load: async () => {
      const res = await mcpAPI.getCredentials(serverName);
      return res.data;
    },
    save: async (apiKey: string) => {
      await mcpAPI.setCredentials(serverName, { api_key: apiKey });
    },
    test: async () => {
      const res = await mcpAPI.testCredentials(serverName);
      return res.data;
    },
    delete: async () => {
      await mcpAPI.deleteCredentials(serverName);
    },
  };
}

/** Build a CredentialsAPI adapter for API providers */
export function providerCredentialsAdapter(providerId: string, providerAPI: typeof import('@/api/provider').providerAPI): CredentialsAPI {
  return {
    load: async () => {
      const res = await providerAPI.getCredentials(providerId);
      return res.data;
    },
    save: async (apiKey: string) => {
      await providerAPI.setCredentials(providerId, { api_key: apiKey });
    },
    test: async () => {
      const res = await providerAPI.testCredentials(providerId);
      return res.data;
    },
    delete: async () => {
      await providerAPI.deleteCredentials(providerId);
    },
  };
}
