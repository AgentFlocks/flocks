import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, KnowledgebaseError, type ConnectionSettings } from '@/api/knowledgebase';
import EntitySheet from '@/components/common/EntitySheet';
import PasswordInput from '@/components/common/PasswordInput';
import { useToast } from '@/components/common/Toast';
import { Button, inputClass } from './ui';

const safeSaveErrorCodes = new Set([
  'invalid_request',
  'api_key_required',
  'connection_test_failed',
  'knowledgebase_config_overridden',
  'knowledgebase_config_invalid',
  'knowledgebase_config_changed',
  'knowledgebase_save_failed',
  'request_too_large',
]);

export default function ConnectionSettingsSheet({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation('workspace');
  const toast = useToast();
  const [settings, setSettings] = useState<ConnectionSettings | null>(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [loadRevision, setLoadRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savingRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError(false);
    void knowledgebaseAPI.connection(controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setSettings(data);
      setBaseUrl(data.base_url);
      setApiKey(''); // Never fetch, prefill, or display a stored credential.
      setLoading(false);
    }, () => {
      if (controller.signal.aborted) return;
      setLoadError(true);
      setLoading(false);
    });
    return () => controller.abort();
  }, [loadRevision]);

  const handleClose = () => {
    if (!savingRef.current) onClose();
  };

  const handleSave = async () => {
    if (!settings || loading || savingRef.current) return;
    const url = baseUrl.trim();
    const key = apiKey.trim();
    setSaveError(null);
    if (!url) {
      setSaveError(t('knowledge.connection.urlRequired'));
      return;
    }
    if (!key && !settings.has_api_key) {
      setSaveError(t('knowledge.connection.keyRequired'));
      return;
    }

    savingRef.current = true;
    setSaving(true);
    try {
      const result = await knowledgebaseAPI.saveConnection({ provider: 'ragflow', base_url: url, api_key: key });
      if (result.applied !== true) {
        setSaveError(t('knowledge.connection.notApplied'));
        return;
      }
      toast.success(t('knowledge.connection.saved'));
      onClose();
      onSaved();
    } catch (error) {
      const code = error instanceof KnowledgebaseError ? error.code : '';
      setSaveError(safeSaveErrorCodes.has(code)
        ? t(`knowledge.connection.errors.${code}`)
        : t('knowledge.connection.saveFailed'));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return <EntitySheet
    open
    mode="edit"
    entityType={t('knowledge.connection.title')}
    rexSystemContext=""
    rexWelcomeMessage=""
    hideRex
    hideTest
    submitDisabled={loading || !settings}
    submitLoading={saving}
    onClose={handleClose}
    onSubmit={handleSave}
  >
    {loading && <p role="status" className="text-sm text-gray-500">{t('knowledge.connection.loading')}</p>}
    {!loading && loadError && <div role="alert" className="space-y-2 text-sm text-red-600">
      <p>{t('knowledge.connection.loadFailed')}</p>
      <Button onClick={() => setLoadRevision(value => value + 1)}>{t('knowledge.retry')}</Button>
    </div>}
    {!loading && settings && <div className="space-y-5">
      <div>
        <label htmlFor="knowledge-provider" className="mb-1 block text-sm font-medium text-gray-700">{t('knowledge.connection.provider')}</label>
        <select id="knowledge-provider" defaultValue="ragflow" disabled={saving} className={inputClass}>
          <option value="ragflow">RAGFlow</option>
        </select>
      </div>
      <div>
        <label htmlFor="knowledge-base-url" className="mb-1 block text-sm font-medium text-gray-700">{t('knowledge.connection.baseUrl')}</label>
        <input id="knowledge-base-url" type="url" value={baseUrl} onChange={event => { setBaseUrl(event.target.value); setSaveError(null); }} disabled={saving} placeholder="https://ragflow.example.com" className={inputClass} />
      </div>
      <div>
        <label htmlFor="knowledge-api-key" className="mb-1 block text-sm font-medium text-gray-700">{t('knowledge.connection.apiKey')}</label>
        <PasswordInput id="knowledge-api-key" placeholder={settings.has_api_key ? t('knowledge.connection.keyConfiguredPlaceholder') : undefined} value={apiKey} onChange={event => { setApiKey(event.target.value); setSaveError(null); }} disabled={saving} autoComplete="off" className="text-sm" />
        <p className="mt-1 text-xs text-gray-500">{t(settings.has_api_key ? 'knowledge.connection.keyExistingHint' : 'knowledge.connection.keyNewHint')}</p>
      </div>
      {saveError && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{saveError}</p>}
    </div>}
  </EntitySheet>;
}
