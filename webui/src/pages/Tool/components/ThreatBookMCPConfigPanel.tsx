import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  Pencil,
  RefreshCw,
} from 'lucide-react';
import { mcpAPI } from '@/api/mcp';
import {
  getDefaultThreatBookRegion,
  inferThreatBookRegionFromMcpUrl,
  THREATBOOK_REGION_CONFIG,
  type ThreatBookRegion,
} from '@/constants/threatbook';
import type { MCPCredentials, MCPServer } from '@/types';

interface ThreatBookMCPConfigPanelProps {
  serverName: string;
  serverStatus: MCPServer['status'];
  configUrl?: string;
  onConfigured: () => Promise<void>;
}

type OperationResult = {
  success: boolean;
  message: string;
} | null;

export default function ThreatBookMCPConfigPanel({
  serverName,
  serverStatus,
  configUrl,
  onConfigured,
}: ThreatBookMCPConfigPanelProps) {
  const { t, i18n } = useTranslation('tool');
  const configuredRegion = useMemo(
    () => inferThreatBookRegionFromMcpUrl(configUrl),
    [configUrl],
  );
  const languageDefaultRegion = useMemo(
    () => getDefaultThreatBookRegion(i18n.resolvedLanguage || i18n.language),
    [i18n.language, i18n.resolvedLanguage],
  );
  const [region, setRegion] = useState<ThreatBookRegion>(configuredRegion || languageDefaultRegion);
  const [credentials, setCredentials] = useState<MCPCredentials | null>(null);
  const [loadingCredentials, setLoadingCredentials] = useState(true);
  const [editing, setEditing] = useState(true);
  const [apiKey, setApiKey] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [storedKeyLoaded, setStoredKeyLoaded] = useState(false);
  const [revealingKey, setRevealingKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<OperationResult>(null);

  const loadCredentials = async () => {
    try {
      setLoadingCredentials(true);
      const response = await mcpAPI.getCredentials(serverName);
      setCredentials(response.data);
      setEditing(!(response.data.has_credential && configuredRegion));
    } catch {
      setCredentials(null);
      setEditing(true);
    } finally {
      setLoadingCredentials(false);
    }
  };

  useEffect(() => {
    setRegion(configuredRegion || languageDefaultRegion);
  }, [configuredRegion, languageDefaultRegion]);

  useEffect(() => {
    void loadCredentials();
  }, [serverName, configuredRegion]);

  const regionConfig = THREATBOOK_REGION_CONFIG[region];
  const summaryRegion = configuredRegion || region;
  const isConfigured = Boolean(credentials?.has_credential && configuredRegion);
  const isConnected = serverStatus === 'connected';
  const hasStoredKeyForRegion = Boolean(
    credentials?.has_credential && configuredRegion && configuredRegion === region,
  );
  const showingStoredKeyMask = hasStoredKeyForRegion && !showApiKey;
  const displayedApiKey = showingStoredKeyMask
    ? credentials?.api_key_masked || ''
    : apiKey;

  const handleRegionChange = (nextRegion: ThreatBookRegion) => {
    if (nextRegion === region) return;
    setRegion(nextRegion);
    setApiKey('');
    setShowApiKey(false);
    setStoredKeyLoaded(false);
    setResult(null);
  };

  const handleApiKeyVisibility = async () => {
    if (showApiKey) {
      setShowApiKey(false);
      return;
    }

    if (hasStoredKeyForRegion && !storedKeyLoaded) {
      try {
        setRevealingKey(true);
        setResult(null);
        const response = await mcpAPI.revealCredentials(serverName);
        setApiKey(response.data.api_key);
        setStoredKeyLoaded(true);
      } catch (error: any) {
        setResult({
          success: false,
          message: error.response?.data?.detail || error.message || t('detail.threatbookMcp.revealFailed'),
        });
        return;
      } finally {
        setRevealingKey(false);
      }
    }

    setShowApiKey(true);
  };

  const handleSave = async () => {
    const trimmedKey = apiKey.trim();
    if (!trimmedKey) {
      setResult({ success: false, message: t('detail.threatbookMcp.keyRequired') });
      return;
    }

    try {
      setSaving(true);
      setResult(null);
      const response = await mcpAPI.configureThreatBook(serverName, {
        region,
        api_key: trimmedKey,
      });
      if (!response.data.success) {
        setResult({ success: false, message: response.data.message });
        return;
      }

      setApiKey('');
      setShowApiKey(false);
      setStoredKeyLoaded(false);
      setResult({ success: true, message: t('detail.threatbookMcp.saveSuccess') });
      await onConfigured();
      await loadCredentials();
      setEditing(false);
    } catch (error: any) {
      setResult({
        success: false,
        message: error.response?.data?.detail || error.message || t('detail.threatbookMcp.saveFailed'),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRetest = async () => {
    try {
      setTesting(true);
      setResult(null);
      const response = await mcpAPI.testCredentials(serverName);
      setResult({
        success: response.data.success,
        message: response.data.message,
      });
      if (response.data.success) await onConfigured();
    } catch (error: any) {
      setResult({
        success: false,
        message: error.response?.data?.detail || error.message || t('detail.threatbookMcp.testFailed'),
      });
    } finally {
      setTesting(false);
    }
  };

  if (loadingCredentials) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-500">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        <span className="text-sm">{t('detail.threatbookMcp.loading')}</span>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-gray-900">{t('detail.threatbookMcp.title')}</h3>
        <p className="mt-1 text-sm leading-6 text-gray-600">{t('detail.threatbookMcp.description')}</p>
      </div>

      {isConfigured && !editing ? (
        <div className="rounded-lg border border-green-200 bg-green-50/60 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <CheckCircle2 className="mt-0.5 h-5 w-5 flex-shrink-0 text-green-600" />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-gray-900">
                  {t('detail.threatbookMcp.configuredTitle', {
                    region: t(`detail.threatbookMcp.regions.${summaryRegion}`),
                  })}
                </p>
                <p className="mt-1 text-xs text-gray-600">
                  {isConnected
                    ? t('detail.threatbookMcp.connected')
                    : t('detail.threatbookMcp.savedNotConnected')}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleRetest}
                disabled={testing}
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${testing ? 'animate-spin' : ''}`} />
                {testing ? t('detail.threatbookMcp.testing') : t('detail.threatbookMcp.retest')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setRegion(configuredRegion || languageDefaultRegion);
                  setApiKey('');
                  setShowApiKey(false);
                  setStoredKeyLoaded(false);
                  setResult(null);
                  setEditing(true);
                }}
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                <Pencil className="h-4 w-4" />
                {t('detail.threatbookMcp.edit')}
              </button>
            </div>
          </div>

          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-gray-500">{t('detail.threatbookMcp.region')}</dt>
              <dd className="mt-1 text-sm font-medium text-gray-900">
                {t(`detail.threatbookMcp.regions.${summaryRegion}`)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500">{t('detail.threatbookMcp.apiKey')}</dt>
              <dd className="mt-1 text-sm font-medium text-gray-900">
                {credentials?.api_key_masked || t('detail.threatbookMcp.keyConfigured')}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-gray-500">{t('detail.threatbookMcp.endpoint')}</dt>
              <dd className="mt-1 truncate font-mono text-sm text-gray-900" title={THREATBOOK_REGION_CONFIG[summaryRegion].mcpEndpoint}>
                {THREATBOOK_REGION_CONFIG[summaryRegion].mcpEndpoint}
              </dd>
            </div>
          </dl>
        </div>
      ) : (
        <div className="space-y-5 border-t border-gray-200 pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-gray-900">{t('detail.threatbookMcp.region')}</p>
              <p className="mt-1 text-xs text-gray-500">{t('detail.threatbookMcp.regionHint')}</p>
            </div>
            <div className="inline-grid grid-cols-2 rounded-lg border border-gray-200 bg-gray-50 p-1">
              {(['cn', 'global'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => handleRegionChange(item)}
                  disabled={revealingKey || saving}
                  className={`min-w-[96px] rounded-md px-4 py-2 text-sm font-semibold transition-colors ${
                    region === item
                      ? 'bg-white text-green-700 shadow-sm ring-1 ring-green-200'
                      : 'text-gray-500 hover:text-gray-700'
                  } disabled:cursor-not-allowed disabled:opacity-50`}
                >
                  {t(`detail.threatbookMcp.regions.${item}`)}
                </button>
              ))}
            </div>
          </div>

          <div className="inline-flex rounded-full bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 ring-1 ring-green-100">
            {t('detail.threatbookMcp.freeService')}
          </div>

          <div>
            <label htmlFor="threatbook-mcp-api-key" className="mb-1.5 block text-sm font-medium text-gray-700">
              {t('detail.threatbookMcp.apiKey')} <span className="text-red-500">*</span>
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <input
                  id="threatbook-mcp-api-key"
                  type={showApiKey || showingStoredKeyMask ? 'text' : 'password'}
                  value={displayedApiKey}
                  readOnly={showingStoredKeyMask}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setResult(null);
                  }}
                  placeholder={t('detail.threatbookMcp.keyPlaceholder')}
                  autoComplete="off"
                  className="h-10 w-full rounded-lg border border-gray-300 bg-white px-3 pr-10 text-sm focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/30"
                />
                <button
                  type="button"
                  onClick={handleApiKeyVisibility}
                  disabled={revealingKey || (!hasStoredKeyForRegion && !apiKey)}
                  title={showApiKey ? t('detail.hide') : t('detail.show')}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-gray-400 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {revealingKey
                    ? <Loader2 className="h-4 w-4 animate-spin" />
                    : showApiKey
                      ? <EyeOff className="h-4 w-4" />
                      : <Eye className="h-4 w-4" />}
                </button>
              </div>
              <a
                href={regionConfig.activationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-10 flex-shrink-0 items-center justify-center gap-1.5 rounded-lg border border-green-200 bg-green-50 px-4 text-sm font-semibold text-green-700 transition-colors hover:border-green-300 hover:bg-green-100"
              >
                <ExternalLink className="h-4 w-4" />
                {t('detail.threatbookMcp.claimFreeKey')}
              </a>
            </div>
            <p className="mt-1.5 text-xs text-gray-500">{t('detail.threatbookMcp.keyHint')}</p>
          </div>

          <div>
            <label htmlFor="threatbook-mcp-endpoint" className="mb-1.5 block text-sm font-medium text-gray-700">
              {t('detail.threatbookMcp.endpoint')}
            </label>
            <input
              id="threatbook-mcp-endpoint"
              type="text"
              readOnly
              value={regionConfig.mcpEndpoint}
              className="h-10 w-full cursor-default rounded-lg border border-gray-200 bg-gray-50 px-3 font-mono text-sm text-gray-700"
            />
            <p className="mt-1.5 text-xs text-gray-500">{t('detail.threatbookMcp.endpointHint')}</p>
          </div>

          {result && (
            <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${
              result.success
                ? 'border-green-200 bg-green-50 text-green-700'
                : 'border-red-200 bg-red-50 text-red-700'
            }`}>
              {result.success
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0" />
                : <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />}
              <span>{result.message}</span>
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-2">
            {isConfigured && (
              <button
                type="button"
                onClick={() => {
                  setRegion(configuredRegion || languageDefaultRegion);
                  setApiKey('');
                  setShowApiKey(false);
                  setStoredKeyLoaded(false);
                  setResult(null);
                  setEditing(false);
                }}
                disabled={saving}
                className="h-10 rounded-lg border border-gray-300 bg-white px-4 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
              >
                {t('button.cancel')}
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !apiKey.trim()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-red-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {saving
                ? t('detail.threatbookMcp.saving')
                : t('detail.threatbookMcp.saveAndVerify')}
            </button>
          </div>
        </div>
      )}

      {result && isConfigured && !editing && (
        <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${
          result.success
            ? 'border-green-200 bg-green-50 text-green-700'
            : 'border-red-200 bg-red-50 text-red-700'
        }`}>
          {result.success
            ? <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0" />
            : <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />}
          <span>{result.message}</span>
        </div>
      )}
    </div>
  );
}
