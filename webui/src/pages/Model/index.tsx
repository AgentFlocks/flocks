import { useState, useEffect, useMemo, useCallback, useRef, useId } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  Brain, Cog, TestTube, Trash2, Search,
  Zap, Eye, EyeOff, Wrench, MessageSquare,
  Link2, Link2Off, DollarSign, Cpu,
  Plus, ToggleLeft, ToggleRight,
  ChevronDown, Check, AlertCircle, Loader2,
  X, Shield, Pencil, Star, AlertTriangle,
  CheckCircle2, ArrowUp, ArrowDown, ListOrdered,
  Info, ExternalLink,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import EntitySheet from '@/components/common/EntitySheet';
import { useAuth } from '@/contexts/AuthContext';
import { useProviders, type EnrichedProvider } from '@/hooks/useProviders';
import { useSSE } from '@/hooks/useSSE';
import { MODEL_CHANGED_EVENT } from '@/hooks/useDefaultModelVision';
import {
  providerAPI, modelV2API, usageAPI,
  customAPI, modelSettingsAPI, catalogAPI, defaultModelAPI,
} from '@/api/provider';
import ProviderKeyCheck from './ProviderKeyCheck';
import ProviderCatalogPicker from './ProviderCatalogPicker';
import ModelDiscovery, { ModelImportSummary, toModelImport } from './ModelDiscovery';
import TokenUsageCard from './TokenUsageCard';
import { formatPricePerMillion, formatPricingPerMillion, formatPricingTierRange, isPricingFree } from '@/utils/modelPricing';
import { extractErrorMessage } from '@/utils/error';
import {
  convertCurrencyAmount,
  getConvertedTotalCost,
  getDefaultDashboardCurrency,
  toggleDashboardCurrency,
} from './usageDisplay';
import type {
  ProviderCredentials, ModelDefinitionV2, UsageStats,
  CatalogProvider, CatalogCredentialField, ModelSettingV2,
  CustomModelCreate, ProviderCredentialInput, FallbackModelRef,
  DiscoveredModel, ModelImportResult, ModelInventoryDraft, PriceConfigV2,
} from '@/types';

// ==================== Provider Auth Helpers ====================

/**
 * Providers whose API Key field can be left empty.
 *
 * Covers self-hosted scenarios:
 *  - `ollama`: local server, never asks for a key
 *  - `openai-compatible` and `custom-*`: internal LLM gateways / proxies that
 *    front a model behind an OpenAI-compatible URL without enforcing auth
 *
 * Backend endpoints (`POST /providers/{id}/credentials`,
 * `POST /api/custom/providers`) accept empty values for these IDs and persist
 * a `not-needed` placeholder so the OpenAI SDK still constructs cleanly.
 */
function providerAllowsEmptyApiKey(providerId: string): boolean {
  if (!providerId) return false;
  return (
    providerId === 'ollama' ||
    providerId === 'openai-compatible' ||
    providerId.startsWith('custom-')
  );
}

function isCatalogBaseUrlRequired(providerId: string): boolean {
  return providerId === 'openai-compatible';
}

const AZURE_PROVIDER_IDS = new Set(['azure-openai', 'azure']);
const THREATBOOK_LLM_PROVIDER_IDS = new Set(['threatbook-cn-llm', 'threatbook-io-llm']);
const THREATBOOK_FREE_KEY_URL = 'https://portal.agentflocks.com/';

function isAzureProviderId(providerId: string): boolean {
  return AZURE_PROVIDER_IDS.has(providerId);
}

function isThreatBookLLMProviderId(providerId: string): boolean {
  return THREATBOOK_LLM_PROVIDER_IDS.has(providerId);
}

function convertEditablePrice(
  value: string,
  sourceCurrency: string,
  targetCurrency: string,
): string {
  if (value.trim() === '') return value;
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  const converted = convertCurrencyAmount(amount, sourceCurrency, targetCurrency);
  if (converted === null) return value;
  const precision = targetCurrency === 'CNY' ? 4 : 6;
  return String(Number(converted.toFixed(precision)));
}

// ==================== Connection Cache ====================

// v2 drops statuses cached while members' tests were refused (403) and stored as "failed".
const CONNECTION_CACHE_KEY = 'flocks_provider_connection_cache_v2';
const LEGACY_CONNECTION_CACHE_KEY = 'flocks_provider_connection_cache';
const CONNECTION_CACHE_TTL_CONNECTED = 60 * 60 * 1000; // connected: 1 hour
const CONNECTION_CACHE_TTL_FAILED = 5 * 60 * 1000;     // failed: 5 minutes

type CachedStatus = { status: 'connected' | 'failed'; ts: number };

function loadConnectionCache(): Record<string, CachedStatus> {
  try {
    localStorage.removeItem(LEGACY_CONNECTION_CACHE_KEY);
    return JSON.parse(localStorage.getItem(CONNECTION_CACHE_KEY) || '{}');
  } catch {
    return {};
  }
}

function saveConnectionCache(providerId: string, status: 'connected' | 'failed') {
  try {
    const cache = loadConnectionCache();
    cache[providerId] = { status, ts: Date.now() };
    localStorage.setItem(CONNECTION_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // ignore storage errors
  }
}

function getCachedStatus(cache: Record<string, CachedStatus>, providerId: string): 'connected' | 'failed' | null {
  const entry = cache[providerId];
  if (!entry) return null;
  const ttl = entry.status === 'connected' ? CONNECTION_CACHE_TTL_CONNECTED : CONNECTION_CACHE_TTL_FAILED;
  return Date.now() - entry.ts < ttl ? entry.status : null;
}

// ==================== Main Page ====================

export default function ModelPage() {
  const { providers, connectedIds, loading, error, refetch } = useProviders();
  const toast = useToast();
  const { t } = useTranslation('model');
  // Members may run connection tests but change no model configuration: the
  // backend keeps credentials, providers, models and default models admin-only.
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const isAdminRef = useRef(isAdmin);
  isAdminRef.current = isAdmin;

  // State
  const [selectedProvider, setSelectedProvider] = useState<EnrichedProvider | null>(null);
  const [providerModels, setProviderModels] = useState<ModelDefinitionV2[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [credentials, setCredentials] = useState<ProviderCredentials | null>(null);
  const [showConfigDialog, setShowConfigDialog] = useState(false);

  // Dialog states
  const [showAddProvider, setShowAddProvider] = useState(false);
  const [showAddModel, setShowAddModel] = useState(false);
  const [addModelMethod, setAddModelMethod] = useState<'automatic' | 'manual'>('automatic');
  const [selectedModelForDetail, setSelectedModelForDetail] = useState<{ provider: EnrichedProvider; model: ModelDefinitionV2 } | null>(null);

  // Model enabled/disabled states (maps "provider/model" -> enabled)
  const [modelEnabledMap, setModelEnabledMap] = useState<Record<string, boolean>>({});
  // Pending provider to auto-select after providers list refreshes (e.g. after adding a new provider)
  const [pendingSelectId, setPendingSelectId] = useState<string | null>(null);

  // Connection test status per provider: 'unknown' | 'connected' | 'failed'
  const [connectionStatus, setConnectionStatus] = useState<Record<string, 'unknown' | 'connected' | 'failed'>>({});

  // Dashboard data
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);
  const [usageLoadError, setUsageLoadError] = useState(false);
  const usageLoadSeqRef = useRef(0);
  const [defaultModel, setDefaultModel] = useState<{ provider_id: string; model_id: string } | null>(null);
  const [showDefaultModelDialog, setShowDefaultModelDialog] = useState(false);
  const [fallbackModels, setFallbackModels] = useState<FallbackModelRef[]>([]);
  const [fallbackModelsLoading, setFallbackModelsLoading] = useState(true);
  const [fallbackModelsLoadError, setFallbackModelsLoadError] = useState<string | null>(null);
  const [availableRoutingModels, setAvailableRoutingModels] = useState<ModelDefinitionV2[]>([]);
  const [showFallbackModelsDialog, setShowFallbackModelsDialog] = useState(false);

  // Refs for latest handler/state values (avoid stale closures in SSE & one-time effects)
  const sseRefetchTimer = useRef<number | null>(null);
  const selectedProviderRef = useRef(selectedProvider);
  selectedProviderRef.current = selectedProvider;
  const providerLoadSeqRef = useRef(0);
  const handleSelectProviderRef = useRef<(p: EnrichedProvider) => Promise<void>>(null!);

  const refreshProvidersSoon = useCallback(() => {
    if (sseRefetchTimer.current) clearTimeout(sseRefetchTimer.current);
    sseRefetchTimer.current = window.setTimeout(() => {
      refetch();
      if (selectedProviderRef.current) handleSelectProviderRef.current(selectedProviderRef.current);
    }, 500);
  }, [refetch]);
  useSSE({
    url: '/api/event',
    onEvent: useCallback((evt) => {
      if (evt.type === 'provider.updated') refreshProvidersSoon();
    }, [refreshProvidersSoon]),
    // Changes made while the stream was down (or the page sat in the back/forward cache) are not replayed.
    onReconnect: refreshProvidersSoon,
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  const loadFallbackModels = useCallback(async (): Promise<boolean> => {
    setFallbackModelsLoading(true);
    setFallbackModelsLoadError(null);
    try {
      const response = await defaultModelAPI.getFallbacks();
      setFallbackModels(response.data.fallback_providers || []);
      return true;
    } catch (loadError) {
      setFallbackModelsLoadError(
        loadError instanceof Error ? loadError.message : 'Failed to load auxiliary models',
      );
      return false;
    } finally {
      setFallbackModelsLoading(false);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    const sequence = ++usageLoadSeqRef.current;
    setUsageLoading(true);
    setUsageLoadError(false);
    try {
      const response = await usageAPI.getSummary();
      if (sequence === usageLoadSeqRef.current) setUsageStats(response.data);
    } catch {
      if (sequence === usageLoadSeqRef.current) setUsageLoadError(true);
    } finally {
      if (sequence === usageLoadSeqRef.current) setUsageLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUsage();
    return () => { usageLoadSeqRef.current += 1; };
  }, [loadUsage]);

  // Fetch dashboard data on mount and validate default model
  useEffect(() => {
    void loadFallbackModels();

    Promise.all([
      defaultModelAPI.getResolved().catch(() => ({ data: null })),
      modelV2API.listDefinitions({ enabled_only: true }),
    ]).then(([defaultRes, modelsRes]) => {
      const availableModels = modelsRes.data.models || [];
      setAvailableRoutingModels(availableModels);

      const dm = defaultRes.data;
      if (!dm) return;

      const isValid = availableModels.some(
        m => m.provider_id === dm.provider_id && m.id === dm.model_id
      );

      // Viewing this page must never mutate routing configuration. Keep an
      // unavailable default visible so an administrator can change it explicitly.
      setDefaultModel(dm);
      if (!isValid) {
        const modelLabel = `${dm.provider_id} / ${dm.model_id}`;
        if (isAdminRef.current) {
          toast.warning(t('dashboard.defaultModelInvalid', { model: modelLabel }));
        } else {
          toast.warning(t('dashboard.defaultModelInvalidMember', { model: modelLabel }));
        }
      }
    }).catch(() => {
      // Keep the current default model when model definitions cannot be loaded.
    });
  }, [loadFallbackModels]);

  // Only show configured (connected) providers
  const configuredProviders = useMemo(() => {
    return providers.filter(p => p.configured).sort((a, b) => a.name.localeCompare(b.name));
  }, [providers]);

  const totalModels = useMemo(() =>
    configuredProviders
      .filter(p => connectionStatus[p.id] !== 'failed')
      .reduce((sum, p) => sum + p.modelCount, 0),
    [configuredProviders, connectionStatus]
  );

  const connectedCount = useMemo(() =>
    configuredProviders.filter(p => connectionStatus[p.id] === 'connected').length,
    [configuredProviders, connectionStatus]
  );

  const availableFallbackCount = useMemo(() => {
    const configuredProviderIds = new Set(configuredProviders.map(provider => provider.id));
    const availableKeys = new Set(
      availableRoutingModels
        .filter(model => model.model_type === 'llm')
        .map(model => `${model.provider_id}\u0000${model.id}`),
    );
    return fallbackModels.filter(model => (
      configuredProviderIds.has(model.provider_id)
      && availableKeys.has(`${model.provider_id}\u0000${model.model_id}`)
      && !(defaultModel
        && model.provider_id === defaultModel.provider_id
        && model.model_id === defaultModel.model_id)
    )).length;
  }, [availableRoutingModels, configuredProviders, defaultModel, fallbackModels]);

  // Auto-select last-used provider (persisted in sessionStorage), fallback to first
  const autoSelectedRef = useRef(false);
  useEffect(() => {
    if (!loading && configuredProviders.length > 0 && !autoSelectedRef.current) {
      autoSelectedRef.current = true;
      const lastId = sessionStorage.getItem('model_page_selected_provider');
      const toSelect = (lastId && configuredProviders.find(p => p.id === lastId)) || configuredProviders[0];
      handleSelectProvider(toSelect);
    }
  }, [loading, configuredProviders]);

  // ==================== Handlers ====================

  const handleSelectProvider = useCallback(async (provider: EnrichedProvider) => {
    const requestSeq = ++providerLoadSeqRef.current;
    const switchingProvider = selectedProviderRef.current?.id !== provider.id;
    setSelectedProvider(provider);
    selectedProviderRef.current = provider;
    sessionStorage.setItem('model_page_selected_provider', provider.id);
    if (switchingProvider) {
      setCredentials(null);
      setProviderModels([]);
    }
    setLoadingModels(true);

    try {
      const modelsRes = await modelV2API
        .listDefinitions({ provider: provider.id })
        .catch(() => ({ data: { models: [], total: 0 } }));

      if (providerLoadSeqRef.current !== requestSeq) return;

      const models = modelsRes.data.models || [];
      setProviderModels(models);

      const enabledMap: Record<string, boolean> = {};
      const [credentialsResult] = await Promise.allSettled([
        // Credentials are only used by the admin configure dialog; members would just get a 403.
        isAdminRef.current
          ? providerAPI.getCredentials(provider.id).then(res => res.data)
          : Promise.resolve(null),
        Promise.allSettled(
          models.map(async (m) => {
            try {
              const res = await modelSettingsAPI.get(provider.id, m.id);
              enabledMap[`${provider.id}/${m.id}`] = res.data.enabled !== false;
            } catch {
              enabledMap[`${provider.id}/${m.id}`] = true;
            }
          })
        ),
      ]);

      if (providerLoadSeqRef.current !== requestSeq) return;

      setCredentials(credentialsResult.status === 'fulfilled' ? credentialsResult.value : null);
      setModelEnabledMap(prev => ({ ...prev, ...enabledMap }));
    } catch {
      if (providerLoadSeqRef.current !== requestSeq) return;
      setProviderModels([]);
      setCredentials(null);
    } finally {
      if (providerLoadSeqRef.current === requestSeq) {
        setLoadingModels(false);
      }
    }
  }, []);
  handleSelectProviderRef.current = handleSelectProvider;

  // Restore statuses from explicit connection tests without issuing API calls.
  useEffect(() => {
    if (!loading && providers.length > 0) {
      const cachedStatuses = loadConnectionCache();
      setConnectionStatus(prev => {
        let changed = false;
        const next = { ...prev };
        providers.filter(p => p.configured).forEach(p => {
          const cached = getCachedStatus(cachedStatuses, p.id);
          if (cached !== null && next[p.id] !== cached) {
            next[p.id] = cached;
            changed = true;
          }
        });
        if (changed) {
          return next;
        } else {
          return prev;
        }
      });
    }
  }, [loading, providers]);

  const handleDeleteProvider = async (providerId: string) => {
    const isDefaultAffected = defaultModel && defaultModel.provider_id === providerId;
    const confirmMsg = isDefaultAffected
      ? t('confirmDeleteProviderWithDefault', { model: defaultModel.model_id })
      : t('confirmDeleteProvider');
    if (!confirm(confirmMsg)) return;

    try {
      // Delete credentials
      await providerAPI.deleteCredentials(providerId).catch(() => {});
      // If it's a custom provider, also delete it
      if (providerId.startsWith('custom-')) {
        await customAPI.deleteProvider(providerId).catch(() => {});
      }
      toast.success(t('providerRemoved'));

      if (isDefaultAffected) {
        setDefaultModel(null);
        toast.success(t('defaultModelCleared'));
      }

      // Clear connection status
      setConnectionStatus(prev => {
        const next = { ...prev };
        delete next[providerId];
        return next;
      });

      if (selectedProvider?.id === providerId) {
        setSelectedProvider(null);
        setProviderModels([]);
        setCredentials(null);
      }
      refetch();
    } catch (err: any) {
      toast.error(t('removeFailed'), err.message);
    }
  };

  const handleToggleModel = async (providerId: string, modelId: string, enabled: boolean) => {
    const key = `${providerId}/${modelId}`;
    try {
      await modelSettingsAPI.update(providerId, modelId, { enabled });
      setModelEnabledMap(prev => ({ ...prev, [key]: enabled }));
      void refreshModelRouting();
      toast.success(enabled ? t('modelEnabled') : t('modelDisabled'), modelId);
    } catch (err: any) {
      toast.error(t('operationFailed'), err.message);
    }
  };

  const handleProviderAdded = (addedProviderId?: string) => {
    setShowAddProvider(false);
    refetch();
    if (addedProviderId) {
      setConnectionStatus(prev => ({ ...prev, [addedProviderId]: 'unknown' }));
      setPendingSelectId(addedProviderId);
      setTimeout(() => refetch(), 500);
    } else {
      setTimeout(() => refetch(), 300);
    }
  };

  // When providers list changes, auto-select a pending provider (e.g. newly added)
  useEffect(() => {
    if (!pendingSelectId || loading) return;
    const found = providers.find(p => p.id === pendingSelectId);
    if (found) {
      handleSelectProvider(found);
      setPendingSelectId(null);
    }
  }, [providers, pendingSelectId, loading]);

  const refreshModelRouting = useCallback(async () => {
    const [defaultResult, modelsResult] = await Promise.allSettled([
      defaultModelAPI.getResolved(),
      modelV2API.listDefinitions({ enabled_only: true }),
      loadFallbackModels(),
    ]);
    if (defaultResult.status === 'fulfilled') setDefaultModel(defaultResult.value.data);
    else if (defaultResult.reason?.response?.status === 404) setDefaultModel(null);
    if (modelsResult.status === 'fulfilled') setAvailableRoutingModels(modelsResult.value.data.models || []);
  }, [loadFallbackModels]);

  const handleModelCreated = useCallback(() => {
    // Manual additions and explicit model changes may affect routing references.
    void refreshModelRouting();
    if (selectedProvider) {
      handleSelectProvider(selectedProvider);
    }
    refetch();
  }, [selectedProvider, refetch, refreshModelRouting]);

  const handleDeleteModel = async (modelId: string) => {
    if (!selectedProvider) return;

    const isDefaultAffected = defaultModel
      && defaultModel.provider_id === selectedProvider.id
      && defaultModel.model_id === modelId;
    const confirmMsg = isDefaultAffected
      ? t('confirmDeleteModelWithDefault', { modelId })
      : t('confirmDeleteModel', { modelId });
    if (!confirm(confirmMsg)) return;

    try {
      await modelV2API.deleteDefinition(selectedProvider.id, modelId);
      toast.success(t('modelDeleted'));

      if (isDefaultAffected) {
        setDefaultModel(null);
        toast.success(t('defaultModelCleared'));
      }

      await refreshModelRouting();

      if (selectedProvider) {
        handleSelectProvider(selectedProvider);
      }
      refetch();
    } catch (err: any) {
      toast.error(t('deleteFailed'), err.message);
    }
  };

  // ==================== Render ====================

  if (loading && providers.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  if (error && providers.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button onClick={refetch} className="px-4 py-2 bg-slate-800 text-white rounded-lg hover:bg-slate-900">
            {t('providerList.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-y-auto" aria-busy={loading}>
      {/* Header */}
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Brain className="w-8 h-8" />}
        action={
          <div className="flex items-center gap-2">
            {isAdmin && (
              <button
                onClick={() => setShowAddProvider(true)}
                className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
              >
                <Plus className="w-4 h-4" />
                {t('addProvider')}
              </button>
            )}
            {isAdmin && selectedProvider && (
              <button
                onClick={() => { setAddModelMethod('automatic'); setShowAddModel(true); }}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
              >
                <Plus className="w-4 h-4" />
                {t('addModel')}
              </button>
            )}
          </div>
        }
      />

      {error && (
        <div role="alert" className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span>{t('providerList.refreshFailed', { error })}</span>
          <button type="button" onClick={refetch} disabled={loading} className="shrink-0 rounded border border-amber-300 px-2 py-1 text-xs hover:bg-amber-100 disabled:opacity-50">{t('providerList.retry')}</button>
        </div>
      )}

      {/* Dashboard Strip */}
      <DashboardStrip
        connectedCount={connectedCount}
        totalModels={totalModels}
        usageStats={usageStats}
        usageLoading={usageLoading}
        usageLoadError={usageLoadError}
        onRefreshUsage={() => void loadUsage()}
        defaultModel={defaultModel}
        onEditDefault={isAdmin ? () => setShowDefaultModelDialog(true) : undefined}
        fallbackCount={fallbackModels.length}
        availableFallbackCount={availableFallbackCount}
        onEditFallbacks={isAdmin ? () => setShowFallbackModelsDialog(true) : undefined}
      />

      {/* Main Content: Provider List + Detail Panel */}
      <div className="flex flex-col md:flex-row gap-3 flex-1 overflow-hidden min-h-[18rem]">
        {/* Provider List */}
        <div className="w-full md:w-64 max-h-40 md:max-h-none flex-shrink-0 flex flex-col min-w-0">
          <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
            {configuredProviders.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-3">
                  <Brain className="w-8 h-8 text-gray-400" />
                </div>
                <p className="text-sm font-medium text-gray-700 mb-1">{t('providerList.empty')}</p>
                <p className="text-xs text-gray-500 mb-4">
                  {isAdmin ? t('providerList.emptyHint') : t('providerList.emptyHintMember')}
                </p>
                {isAdmin && (
                  <button
                    onClick={() => setShowAddProvider(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-800 text-white rounded-lg hover:bg-slate-900 transition-colors"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    {t('providerList.addProvider')}
                  </button>
                )}
              </div>
            ) : (
              configuredProviders.map((provider) => (
                <ProviderCard
                  key={provider.id}
                  provider={provider}
                  isSelected={selectedProvider?.id === provider.id}
                  connStatus={connectionStatus[provider.id] || 'unknown'}
                  onClick={() => handleSelectProvider(provider)}
                  onConfigure={isAdmin ? async () => {
                    await handleSelectProvider(provider);
                    if (selectedProviderRef.current?.id === provider.id) {
                      try {
                        const res = await providerAPI.revealCredentials(provider.id);
                        if (selectedProviderRef.current?.id === provider.id) {
                          setCredentials(res.data);
                          setShowConfigDialog(true);
                        }
                      } catch (err: any) {
                        toast.error(t('configFailed'), extractErrorMessage(err));
                      }
                    }
                  } : undefined}
                />
              ))
            )}
          </div>
        </div>

        {/* Detail Panel */}
        <div className="flex-1 min-h-0 min-w-0 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
          {selectedProvider ? (
            <ProviderDetail
              key={selectedProvider.id}
              provider={selectedProvider}
              models={providerModels}
              loadingModels={loadingModels}
              modelEnabledMap={modelEnabledMap}
              connStatus={connectionStatus[selectedProvider.id] || 'unknown'}
              canEdit={isAdmin}
              onToggleModel={isAdmin ? handleToggleModel : undefined}
              onDeleteModel={isAdmin ? handleDeleteModel : undefined}
              onOpenModelDetail={(model) => setSelectedModelForDetail({ provider: selectedProvider, model })}
              onConnectionStatusChange={(status) => {
                setConnectionStatus(prev => ({ ...prev, [selectedProvider.id]: status }));
                saveConnectionCache(selectedProvider.id, status);
              }}
            />
          ) : (
            <div className="h-full flex items-center justify-center">
              <EmptyState
                icon={<Brain className="w-16 h-16" />}
                title={configuredProviders.length === 0 ? t('emptyDetail.addFirst') : t('emptyDetail.selectProvider')}
                description={
                  configuredProviders.length === 0
                    ? t('emptyDetail.addFirstHint')
                    : t('emptyDetail.selectProviderHint')
                }
              />
            </div>
          )}
        </div>
      </div>

      {/* Dialogs */}
      {showConfigDialog && selectedProvider && (
        <ConfigureProviderDialog
          key={selectedProvider.id}
          provider={selectedProvider}
          existingCredentials={credentials}
          models={providerModels}
          loadingModels={loadingModels}
          modelEnabledMap={modelEnabledMap}
          defaultModelId={defaultModel?.provider_id === selectedProvider.id ? defaultModel.model_id : undefined}
          onAddModel={() => { setAddModelMethod('manual'); setShowAddModel(true); }}
          onEditModel={(model) => setSelectedModelForDetail({ provider: selectedProvider, model })}
          onToggleModel={(modelId, enabled) => handleToggleModel(selectedProvider.id, modelId, enabled)}
          onDeleteModel={handleDeleteModel}
          onModelsChanged={handleModelCreated}
          onClose={() => {
            setShowConfigDialog(false);
            setCredentials(null);
          }}
          onConfigured={async () => {
            if (selectedProvider) {
              const res = await providerAPI.getCredentials(selectedProvider.id).catch(() => ({ data: null }));
              setCredentials(res.data);
              // Refresh model list in right panel
              handleSelectProvider(selectedProvider);
            }
            refetch();
            setShowConfigDialog(false);
          }}
          onTestResult={(success) => {
            if (selectedProvider) {
              setConnectionStatus(prev => ({ ...prev, [selectedProvider.id]: success ? 'connected' : 'failed' }));
            }
          }}
          onDelete={() => {
            setShowConfigDialog(false);
            handleDeleteProvider(selectedProvider.id);
          }}
        />
      )}

      {showAddProvider && (
        <AddProviderDialog
          connectedIds={connectedIds}
          onClose={() => setShowAddProvider(false)}
          onAdded={handleProviderAdded}
        />
      )}

      {showAddModel && selectedProvider && (
        <AddModelDialog
          initialMethod={addModelMethod}
          provider={selectedProvider}
          models={providerModels}
          loadingModels={loadingModels}
          onClose={() => setShowAddModel(false)}
          onCreated={handleModelCreated}
        />
      )}

      {selectedModelForDetail && (
        <ModelDetailSheet
          provider={selectedModelForDetail.provider}
          model={selectedModelForDetail.model}
          readOnly={!isAdmin}
          onClose={() => setSelectedModelForDetail(null)}
          onSaved={() => {
            setSelectedModelForDetail(null);
            void refreshModelRouting();
            if (selectedProvider && selectedProvider.id === selectedModelForDetail.provider.id) {
              handleSelectProvider(selectedProvider);
            }
          }}
        />
      )}

      {showDefaultModelDialog && (
        <SetDefaultModelDialog
          current={defaultModel}
          providers={providers}
          onClose={() => setShowDefaultModelDialog(false)}
          onSaved={(m) => {
            setDefaultModel(m);
            setShowDefaultModelDialog(false);
          }}
          onCleared={() => {
            setDefaultModel(null);
          }}
        />
      )}

      {showFallbackModelsDialog && (
        <FallbackModelsDialog
          current={fallbackModels}
          currentLoading={fallbackModelsLoading}
          currentLoadError={fallbackModelsLoadError}
          primary={defaultModel}
          providers={providers}
          onRetryCurrent={loadFallbackModels}
          onClose={() => setShowFallbackModelsDialog(false)}
          onSaved={(models, availableModels) => {
            setFallbackModels(models);
            setAvailableRoutingModels(availableModels);
            setShowFallbackModelsDialog(false);
          }}
        />
      )}

    </div>
  );
}

// ==================== Dashboard Strip ====================

function DashboardStrip({
  connectedCount,
  totalModels,
  usageStats,
  usageLoading,
  usageLoadError,
  onRefreshUsage,
  defaultModel,
  onEditDefault,
  fallbackCount,
  availableFallbackCount,
  onEditFallbacks,
}: {
  connectedCount: number;
  totalModels: number;
  usageStats: UsageStats | null;
  usageLoading: boolean;
  usageLoadError: boolean;
  onRefreshUsage: () => void;
  defaultModel: { provider_id: string; model_id: string } | null;
  /** Absent for members: default and auxiliary models are admin settings. */
  onEditDefault?: () => void;
  fallbackCount: number;
  availableFallbackCount: number;
  onEditFallbacks?: () => void;
}) {
  const { t, i18n } = useTranslation('model');
  const defaultCurrency = getDefaultDashboardCurrency(i18n.language);
  const [displayCurrency, setDisplayCurrency] = useState(defaultCurrency);
  const totalCost = useMemo(
    () => getConvertedTotalCost(usageStats, displayCurrency),
    [displayCurrency, usageStats],
  );

  useEffect(() => {
    setDisplayCurrency(getDefaultDashboardCurrency(i18n.language));
  }, [i18n.language]);

  return (
    <div className="grid shrink-0 grid-cols-2 gap-3 mb-4 lg:grid-cols-6">
      {/* Default Model Card */}
      <div className="rounded-lg border p-3 bg-purple-50 text-purple-700 border-purple-200">
        <div className="flex items-center justify-between mb-1 opacity-70">
          <div className="flex items-center gap-2">
            <Star className="w-5 h-5" />
            <span className="text-xs font-medium">{t('dashboard.defaultModel')}</span>
          </div>
          {onEditDefault && (
            <button
              onClick={onEditDefault}
              className="hover:opacity-100 opacity-60 transition-opacity"
              title={t('dashboard.setDefaultModel')}
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <div className="text-lg font-bold truncate">
          {defaultModel ? defaultModel.model_id : t('dashboard.noDefaultModel')}
        </div>
        {defaultModel && (
          <div className="text-xs opacity-60 truncate">{defaultModel.provider_id}</div>
        )}
      </div>
      <StatCard
        icon={<ListOrdered className="w-5 h-5" />}
        label={t('dashboard.fallbackModels')}
        value={fallbackCount > 0
          ? t('dashboard.fallbackAvailability', { available: availableFallbackCount, total: fallbackCount })
          : t('dashboard.noFallbackModels')}
        color="purple"
        small={fallbackCount > 0}
        onClick={onEditFallbacks}
        title={onEditFallbacks ? t('dashboard.editFallbackModels') : undefined}
      />
      <StatCard icon={<Link2 className="w-5 h-5" />} label={t('dashboard.connected')} value={String(connectedCount)} color="green" />
      <StatCard icon={<Cpu className="w-5 h-5" />} label={t('dashboard.availableModels')} value={String(totalModels)} color="blue" />
      <TokenUsageCard usageStats={usageStats} loading={usageLoading} error={usageLoadError} onRefresh={onRefreshUsage} />
      <StatCard
        icon={<DollarSign className="w-5 h-5" />}
        label={t('dashboard.totalCost')}
        value={totalCost ?? t('dashboard.noCost')}
        color="amber"
        onClick={() => setDisplayCurrency(current => toggleDashboardCurrency(current))}
        title={t('dashboard.toggleCurrency')}
      />
    </div>
  );
}

function StatCard({ icon, label, value, color, small, onClick, title, hint, hintTitle }: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  color: string;
  small?: boolean;
  onClick?: () => void;
  title?: string;
  hint?: React.ReactNode;
  hintTitle?: string;
}) {
  const colorMap: Record<string, string> = {
    green: 'bg-green-50 text-green-700 border-green-200',
    blue: 'bg-sky-50 text-sky-800 border-sky-200',
    purple: 'bg-purple-50 text-purple-700 border-purple-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
  };
  return (
    <div
      className={`rounded-lg border p-3 ${colorMap[color] || colorMap.blue} ${onClick ? 'cursor-pointer transition-opacity hover:opacity-90' : ''}`}
      onClick={onClick}
      title={title}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      } : undefined}
    >
      <div className="flex items-center gap-2 mb-1 opacity-70">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <div className={`font-bold break-words ${small ? 'text-sm' : 'text-lg'}`}>{value}</div>
      {hint && (
        <div className="mt-0.5 text-xs leading-snug opacity-70 break-words" title={hintTitle}>{hint}</div>
      )}
    </div>
  );
}

// ==================== Provider Card ====================

function ProviderCard({ provider, isSelected, connStatus, onClick, onConfigure }: {
  provider: EnrichedProvider; isSelected: boolean;
  connStatus: 'unknown' | 'connected' | 'failed';
  onClick: () => void;
  onConfigure?: () => void;
}) {
  const { t } = useTranslation('model');
  const dotColor = connStatus === 'connected' ? 'bg-green-500'
    : connStatus === 'failed' ? 'bg-red-500'
    : 'bg-amber-400';

  return (
    <div
      onClick={onClick}
      className={`px-3 py-2.5 rounded-lg cursor-pointer transition-all flex items-center gap-2 ${
        isSelected
          ? 'bg-gray-50 border-2 border-gray-400'
          : 'bg-white border border-gray-200 hover:border-gray-300 hover:shadow-sm'
      }`}
    >
      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${dotColor}`} title={
        connStatus === 'connected' ? t('status.connected') : connStatus === 'failed' ? t('status.connectionFailed') : t('status.notTested')
      } />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-gray-900 text-sm truncate">{provider.name}</span>
        </div>
        <div className="text-xs mt-0.5">
          {connStatus === 'failed'
            ? <span className="text-red-500">{t('status.connectionFailed')}</span>
            : <span className="text-gray-500">{t('status.models', { count: provider.modelCount })}</span>
          }
        </div>
      </div>
      {onConfigure && (
        <button
          onClick={(e) => { e.stopPropagation(); onConfigure(); }}
          className="p-1.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100 flex-shrink-0"
          title={t('form.configure')}
        >
          <Cog className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

// ==================== Provider Detail Panel ====================

type ModelTestStatus = {
  status: 'untested' | 'testing' | 'success' | 'failed';
  message?: string;
  latency?: number;
};

function ProviderDetail({
  provider, models, loadingModels,
  modelEnabledMap, connStatus, canEdit, onToggleModel, onDeleteModel,
  onOpenModelDetail, onConnectionStatusChange,
}: {
  provider: EnrichedProvider;
  models: ModelDefinitionV2[];
  loadingModels: boolean;
  modelEnabledMap: Record<string, boolean>;
  connStatus: 'unknown' | 'connected' | 'failed';
  /** Members test models but change nothing; the page says so. */
  canEdit: boolean;
  onToggleModel?: (providerId: string, modelId: string, enabled: boolean) => void;
  onDeleteModel?: (modelId: string) => void;
  onOpenModelDetail?: (model: ModelDefinitionV2) => void;
  onConnectionStatusChange?: (status: 'connected' | 'failed') => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const [modelTestStatus, setModelTestStatus] = useState<Record<string, ModelTestStatus>>({});
  const [batchTesting, setBatchTesting] = useState(false);

  const batchResultsRef = useRef<Record<string, ModelTestStatus>>({});
  const enabledModels = useMemo(
    () => models.filter(model => modelEnabledMap[`${provider.id}/${model.id}`] !== false),
    [models, modelEnabledMap, provider.id],
  );

  const handleTestSingleModel = async (modelId: string): Promise<ModelTestStatus> => {
    setModelTestStatus(prev => ({ ...prev, [modelId]: { status: 'testing' } }));
    try {
      const res = await providerAPI.testCredentials(provider.id, modelId);
      const result: ModelTestStatus = res.data.success
        ? { status: 'success', latency: res.data.latency_ms, message: res.data.answer }
        : { status: 'failed', message: res.data.error || res.data.message || t('status.connectionFailed') };
      setModelTestStatus(prev => ({ ...prev, [modelId]: result }));
      batchResultsRef.current[modelId] = result;
      if (result.status === 'success' && connStatus !== 'connected' && onConnectionStatusChange) {
        onConnectionStatusChange('connected');
      }
      return result;
    } catch (err) {
      const result: ModelTestStatus = { status: 'failed', message: extractErrorMessage(err) };
      setModelTestStatus(prev => ({ ...prev, [modelId]: result }));
      batchResultsRef.current[modelId] = result;
      return result;
    }
  };

  const handleBatchTest = async () => {
    if (enabledModels.length === 0) {
      toast.warning(t('form.noModelsToTest'));
      return;
    }
    setBatchTesting(true);
    batchResultsRef.current = {};
    const initial: Record<string, ModelTestStatus> = {};
    enabledModels.forEach(m => { initial[m.id] = { status: 'testing' }; });
    setModelTestStatus(prev => ({ ...prev, ...initial }));

    const CONCURRENCY = 3;
    let idx = 0;
    const run = async () => {
      while (idx < enabledModels.length) {
        const m = enabledModels[idx++];
        await handleTestSingleModel(m.id);
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, enabledModels.length) }, () => run()));
    setBatchTesting(false);

    const results = batchResultsRef.current;
    const succeeded = enabledModels.filter(m => results[m.id]?.status === 'success');
    const failed = enabledModels.filter(m => results[m.id]?.status === 'failed');
    const summary = t('form.batchTestSummary', { success: succeeded.length, failed: failed.length });
    if (failed.length === 0) {
      toast.success(t('form.batchTestDone'), summary);
    } else {
      toast.error(t('form.batchTestDone'), summary);
    }

    if (onConnectionStatusChange) {
      onConnectionStatusChange(succeeded.length > 0 ? 'connected' : 'failed');
    }
  };

  const visibleTestStatuses = enabledModels.map(model => modelTestStatus[model.id]);
  const testedCount = visibleTestStatuses.filter(s => s?.status === 'success' || s?.status === 'failed').length;
  const successCount = visibleTestStatuses.filter(s => s?.status === 'success').length;
  const failedCount = visibleTestStatuses.filter(s => s?.status === 'failed').length;

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <div className="flex items-center justify-between gap-3 mb-2">
          <h3 className="text-sm font-medium text-gray-800 shrink-0">
            {t('detail.modelList')}{!loadingModels && <span className="text-gray-400 font-normal"> ({enabledModels.length})</span>}
          </h3>
          <div className="flex items-center gap-1.5 shrink-0">
            {testedCount > 0 && (
              <span className="text-[11px] text-gray-500">
                <span className="text-green-600">{successCount} {t('detail.success')}</span>
                {failedCount > 0 && <span className="text-red-600 ml-1">{failedCount} {t('detail.failed')}</span>}
                {batchTesting && (
                  <span className="text-red-600 ml-1">
                    {t('detail.testing')} {testedCount}/{enabledModels.length}
                  </span>
                )}
              </span>
            )}
            {!canEdit && <span className="text-[11px] text-gray-400">{t('detail.configAdminOnly')}</span>}
            <button
              onClick={handleBatchTest}
              disabled={batchTesting || loadingModels || enabledModels.length === 0}
              className="flex items-center gap-1 px-2 py-1 text-xs border border-gray-300 text-gray-600 rounded hover:border-slate-400 hover:text-slate-800 hover:bg-slate-50 disabled:opacity-50"
            >
              {batchTesting ? <Loader2 className="w-3 h-3 animate-spin" /> : <TestTube className="w-3 h-3" />}
              {batchTesting ? t('detail.testing') : t('detail.batchTest')}
            </button>
          </div>
        </div>

        {loadingModels ? (
          <div className="flex items-center justify-center py-8">
            <LoadingSpinner />
          </div>
        ) : enabledModels.length === 0 ? (
          <EmptyState
            icon={<Brain className="w-10 h-10" />}
            title={t('detail.noModels')}
            description={!canEdit
              ? t('detail.noModelsMember')
              : provider.configured ? t('detail.addModelHint') : t('detail.configureFirst')}
          />
        ) : (
          <div className="space-y-1.5">
            {enabledModels.map((model) => {
              return (
                <ModelCard
                  key={model.id}
                  model={model}
                  enabled
                  testStatus={modelTestStatus[model.id]}
                  onOpenDetail={onOpenModelDetail ? () => onOpenModelDetail(model) : undefined}
                  onTestModel={() => handleTestSingleModel(model.id)}
                  onToggle={onToggleModel ? () => onToggleModel(provider.id, model.id, false) : undefined}
                  onDelete={onDeleteModel ? () => onDeleteModel(model.id) : undefined}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ==================== Model Card (V2) ====================

function PricingTierTable({ pricing }: { pricing: PriceConfigV2 }) {
  const { t } = useTranslation('model');
  const tiers = pricing.tiers ?? [];
  const hasCacheWrite = tiers.some(tier => tier.cache_write != null);
  if (!tiers.length) return null;

  return (
    <div className="space-y-2 mb-3">
      <p className="text-xs text-gray-500">{t('pricing.tierBasis')}</p>
      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-xs text-left">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-2 py-2 font-medium">{t('pricing.inputRange')}</th>
              <th className="px-2 py-2 font-medium">{t('form.input')}</th>
              <th className="px-2 py-2 font-medium">{t('form.output')}</th>
              <th className="px-2 py-2 font-medium">{t('form.cacheRead')}</th>
              {hasCacheWrite && <th className="px-2 py-2 font-medium">{t('pricing.cacheWrite')}</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 text-gray-700">
            {tiers.map((tier, index) => (
              <tr key={tier.max_input_tokens ?? 'unbounded'}>
                <td className="px-2 py-2 whitespace-nowrap">{formatPricingTierRange(tiers, index)}</td>
                <td className="px-2 py-2 whitespace-nowrap">{formatPricePerMillion(tier.input, pricing.currency, pricing.unit)}</td>
                <td className="px-2 py-2 whitespace-nowrap">{formatPricePerMillion(tier.output, pricing.currency, pricing.unit)}</td>
                <td className="px-2 py-2 whitespace-nowrap" title={tier.cache_read == null ? t('pricing.cacheFallback') : undefined}>
                  {formatPricePerMillion(tier.cache_read ?? tier.input, pricing.currency, pricing.unit)}
                </td>
                {hasCacheWrite && (
                  <td className="px-2 py-2 whitespace-nowrap" title={tier.cache_write == null ? t('pricing.cacheFallback') : undefined}>
                    {formatPricePerMillion(tier.cache_write ?? tier.input, pricing.currency, pricing.unit)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ModelCard({ model, enabled, testStatus, onOpenDetail, onTestModel, onToggle, onDelete }: {
  model: ModelDefinitionV2;
  enabled: boolean;
  testStatus?: ModelTestStatus;
  onOpenDetail?: () => void;
  onTestModel?: () => void;
  onToggle?: () => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation('model');
  const features = model.capabilities?.features || [];
  const hasVision = features.includes('vision') || model.capabilities?.supports_vision;
  const hasTools = features.includes('tool_call') || model.capabilities?.supports_tools;
  const hasReasoning = features.includes('reasoning') || model.capabilities?.supports_reasoning;

  const contextK = model.limits?.context_window
    ? model.limits.context_window >= 1000000
      ? `${(model.limits.context_window / 1000000).toFixed(0)}M`
      : `${(model.limits.context_window / 1000).toFixed(0)}K`
    : null;

  const pricing = model.pricing;

  return (
    <div className={`rounded border px-2.5 py-1.5 transition-colors ${
      !enabled
        ? 'bg-gray-100 border-gray-200 opacity-60'
        : 'bg-gray-50 border-gray-200 hover:border-gray-300'
    }`}>
      <div className="flex items-center gap-2 min-w-0">
        <div
          className="flex-1 min-w-0 flex items-center gap-2 flex-wrap cursor-pointer"
          onClick={(e) => { if (onOpenDetail && !(e.target as HTMLElement).closest('button')) onOpenDetail(); }}
          role={onOpenDetail ? 'button' : undefined}
          tabIndex={onOpenDetail ? 0 : undefined}
          onKeyDown={onOpenDetail ? (e) => e.key === 'Enter' && onOpenDetail() : undefined}
        >
          <h4 className={`font-medium text-sm break-words ${enabled ? 'text-gray-900' : 'text-gray-500 line-through'}`}>
            {model.name}
          </h4>
          {!enabled && (
            <span className="px-1 py-0.5 bg-gray-200 text-gray-500 text-[10px] rounded shrink-0">{t('status.disabled')}</span>
          )}
          {model.status === 'deprecated' && (
            <span className="px-1 py-0.5 bg-gray-200 text-gray-600 text-[10px] rounded shrink-0">{t('status.deprecated')}</span>
          )}
          {testStatus?.status === 'testing' && (
            <span className="inline-flex items-center gap-0.5 px-1 py-0.5 bg-amber-100 text-amber-800 text-[10px] rounded shrink-0">
              <Loader2 className="w-2.5 h-2.5 animate-spin" /> {t('status.testingModel')}
            </span>
          )}
          {testStatus?.status === 'success' && (
            <span className="inline-flex items-center gap-0.5 px-1 py-0.5 bg-green-100 text-green-700 text-[10px] rounded shrink-0" title={t('testSuccess')}>
              <Link2 className="w-2.5 h-2.5" /> {testStatus.latency ? `${testStatus.latency}ms` : t('status.available')}
            </span>
          )}
          {testStatus?.status === 'failed' && (
            <span className="inline-flex items-center gap-0.5 px-1 py-0.5 bg-red-100 text-red-700 text-[10px] rounded shrink-0" title={testStatus.message ?? ''}>
              <AlertCircle className="w-2.5 h-2.5" /> {t('detail.failed')}
            </span>
          )}
          <span className="text-[11px] text-gray-400 font-mono break-all" title={`${model.provider_id}/${model.id}`}>
            {model.id}
          </span>
          {contextK && <span className="text-[11px] text-gray-500 shrink-0">{contextK}</span>}
          {pricing && !isPricingFree(pricing) && (
            <span className="text-[11px] text-gray-500 min-w-0 break-words" title={t('pricing.priceOrder')}>{formatPricingPerMillion(pricing)}</span>
          )}
          {pricing && isPricingFree(pricing) && (
            <span className="text-[11px] text-green-600 font-medium shrink-0">{t('status.free')}</span>
          )}
          {enabled && (
            <span className="flex items-center gap-0.5 shrink-0">
              {hasTools && <span className="text-slate-600" title={t('form.toolCall')}><Wrench className="w-3 h-3" /></span>}
              {hasVision && <span className="text-purple-500" title={t('form.vision')}><Eye className="w-3 h-3" /></span>}
              {hasReasoning && <span className="text-amber-500" title={t('form.reasoning')}><Zap className="w-3 h-3" /></span>}
              {model.capabilities?.supports_streaming && <span className="text-gray-400" title={t('form.streaming')}><MessageSquare className="w-3 h-3" /></span>}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {enabled && onTestModel && (
            <button
              onClick={(e) => { e.stopPropagation(); onTestModel(); }}
              disabled={testStatus?.status === 'testing'}
              className="p-1 rounded text-gray-400 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              title={t('form.testConnection')}
            >
              {testStatus?.status === 'testing' ? <Loader2 className="w-4 h-4 animate-spin text-amber-600" /> : <TestTube className="w-4 h-4" />}
            </button>
          )}
          {onToggle && (
            <button
              onClick={(e) => { e.stopPropagation(); onToggle(); }}
              className={`p-1 rounded ${enabled ? 'text-green-600 hover:text-slate-600' : 'text-gray-400 hover:text-green-600'}`}
              title={enabled ? t('status.disabled') : t('status.available')}
            >
              {enabled ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
            </button>
          )}
          {onDelete && (
            <button onClick={(e) => { e.stopPropagation(); onDelete(); }} className="p-1 rounded text-gray-400 hover:text-slate-700 hover:bg-slate-100" title={t('form.delete')}>
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {testStatus?.status === 'failed' && testStatus.message && (
        <div className="mt-1.5 flex items-start gap-1.5 px-2 py-1 bg-red-50 border border-red-200 rounded text-[11px] text-red-700">
          <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
          <span className="break-all">{testStatus.message}</span>
        </div>
      )}
    </div>
  );
}

function CapBadge({ icon, label, color }: { icon: React.ReactNode; label: string; color: string }) {
  const colorMap: Record<string, string> = {
    blue: 'bg-sky-100 text-sky-800',
    purple: 'bg-purple-100 text-purple-700',
    amber: 'bg-amber-100 text-amber-700',
    gray: 'bg-gray-100 text-gray-600',
    green: 'bg-green-100 text-green-700',
  };
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium ${colorMap[color] || colorMap.gray}`}>
      {icon} {label}
    </span>
  );
}

// ==================== Add Provider Dialog ====================

function AddProviderDialog({ connectedIds, onClose, onAdded }: {
  connectedIds: string[];
  onClose: () => void;
  onAdded: (addedProviderId?: string) => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');

  // Catalog data
  const [catalog, setCatalog] = useState<CatalogProvider[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [catalogFailed, setCatalogFailed] = useState(false);

  // Selected provider (catalog tab)
  const [selectedCatalogId, setSelectedCatalogId] = useState<string>('');
  const [showProviderPicker, setShowProviderPicker] = useState(true);

  // Shared form fields
  const [displayName, setDisplayName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [description, setDescription] = useState('');
  const [providerName, setProviderName] = useState('');
  const [azureDeploymentName, setAzureDeploymentName] = useState('');
  const [azureDeploymentDisplayName, setAzureDeploymentDisplayName] = useState('');

  // Model selection (for catalog providers)
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([]);
  const [discoveryImportResult, setDiscoveryImportResult] = useState<ModelImportResult | null>(null);
  // What the last import submitted, Azure deployments typed by hand included: retries pick from it.
  const lastImportCandidates = useRef<DiscoveredModel[]>([]);
  const [discoveryReady, setDiscoveryReady] = useState(true);

  // State
  const [saving, setSaving] = useState(false);

  // ── Wizard step management ──
  const [step, setStep] = useState<'provider' | 'success' | 'add-model'>('provider');
  const [savedProviderId, setSavedProviderId] = useState<string | null>(null);
  const [savedProviderName, setSavedProviderName] = useState('');
  const [addedModelCount, setAddedModelCount] = useState(0);

  // ── Model form state (for add-model step) ──
  const modelForm = useModelForm();
  const [addingModel, setAddingModel] = useState(false);
  const [modelTestResult, setModelTestResult] = useState<{ success: boolean; message: string; latency?: number } | null>(null);
  const [modelTesting, setModelTesting] = useState(false);

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    setCatalogFailed(false);
    try { setCatalog((await catalogAPI.list()).data.providers || []); }
    catch { setCatalogFailed(true); }
    finally { setLoadingCatalog(false); }
  }, []);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);

  // Connected set for uniqueness check
  const connectedSet = useMemo(() => new Set(connectedIds), [connectedIds]);

  // Available providers (exclude already connected unless allow_multiple); openai-compatible first
  const availableProviders = useMemo(() => {
    const list = catalog.filter(p => p.allow_multiple || !connectedSet.has(p.id));
    return [...list].sort((a, b) => {
      if (a.id === 'openai-compatible') return -1;
      if (b.id === 'openai-compatible') return 1;
      return 0;
    });
  }, [catalog, connectedSet]);

  // Selected catalog provider
  const selectedCatalog = useMemo(
    () => catalog.find(p => p.id === selectedCatalogId) || null,
    [catalog, selectedCatalogId]
  );

  // Get credential fields for the selected provider
  const credentialFields = useMemo<CatalogCredentialField[]>(() => {
    if (!selectedCatalog) return [];
    const schema = selectedCatalog.credential_schemas[0];
    return schema?.fields || [];
  }, [selectedCatalog]);

  // Auto-fill when selecting a catalog provider
  const handleSelectCatalogProvider = (providerId: string) => {
    setShowProviderPicker(false);
    if (providerId === selectedCatalogId) return;
    setSelectedCatalogId(providerId);
    setDiscoveredModels([]);
    setDiscoveryImportResult(null);
    setDiscoveryReady(true);

    const provider = catalog.find(p => p.id === providerId);
    if (provider) {
      setDisplayName(provider.name);
      setBaseUrl(provider.default_base_url || '');
      setApiKey('');
      setDescription(provider.description || '');
      setProviderName('');
      setAzureDeploymentName('');
      setAzureDeploymentDisplayName('');
    }
  };

  // Save — catalog provider (transitions to next wizard step instead of closing)
  const importSelectedModels = async (providerId: string, models = discoveredModels): Promise<ModelImportResult | null> => {
    if (!models.length) return null;
    lastImportCandidates.current = models;
    let result: ModelImportResult;
    try {
      result = (await providerAPI.importModels(providerId, models.map(toModelImport))).data;
    } catch (importError) {
      result = { added: [], skipped: [], failed: models.map(model => ({ id: model.id, error: extractErrorMessage(importError) })) };
    }
    setDiscoveryImportResult(result);
    setAddedModelCount(count => count + result.added.length);
    return result;
  };

  const handleSaveCatalog = async () => {
    if (saving || !discoveryReady) return;
    if (!selectedCatalogId) { toast.warning(t('form.selectProviderRequired')); return; }
    if (selectedCatalogId === 'openai-compatible' && !providerName.trim()) {
      toast.warning(t('form.providerNameRequired'));
      return;
    }
    if (isCatalogBaseUrlRequired(selectedCatalogId) && !baseUrl.trim()) {
      toast.warning(t('form.baseUrlRequired'));
      return;
    }
    if (!apiKey.trim() && !providerAllowsEmptyApiKey(selectedCatalogId)) {
      toast.warning(t('form.apiKeyRequired'));
      return;
    }
    const azureModelId = isAzureProviderId(selectedCatalogId) ? azureDeploymentName.trim() : '';
    try {
      setSaving(true);
      if (selectedCatalogId === 'openai-compatible') {
        const created = await customAPI.createProvider({
          name: providerName.trim(),
          base_url: baseUrl.trim(),
          api_key: apiKey.trim() || 'not-needed',
          description: description.trim() || selectedCatalog?.description || undefined,
          skip_catalog_bootstrap: true,
        });
        toast.success(t('providerAdded'), providerName.trim());
        setSavedProviderId(created.data.id);
        setSavedProviderName(providerName.trim());
        await importSelectedModels(created.data.id);
        setStep('success');
        return;
      }

      await providerAPI.setCredentials(selectedCatalogId, {
        api_key: apiKey.trim() || 'not-needed',
        base_url: baseUrl.trim() || undefined,
        skip_catalog_bootstrap: true,
      });
      const selectedCandidates = [...discoveredModels];
      if (azureModelId) {
        if (!selectedCandidates.some(model => model.id === azureModelId)) selectedCandidates.push({
          id: azureModelId, name: azureDeploymentDisplayName.trim() || azureModelId,
          already_added: false, selectable: true,
        });
      }
      await importSelectedModels(selectedCatalogId, selectedCandidates);
      toast.success(t('providerAdded'), displayName);
      setSavedProviderId(selectedCatalogId);
      setSavedProviderName(displayName);
      setStep('success');
    } catch (err: any) {
      toast.error(t('configFailed'), err.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = () => handleSaveCatalog();

  // Complete wizard — notify parent to refresh & close
  const handleComplete = () => {
    onAdded(savedProviderId || undefined);
  };

  // Close handler — if provider was already saved, treat as complete
  const handleWizardClose = () => {
    if (savedProviderId) {
      onAdded(savedProviderId);
    } else {
      onClose();
    }
  };

  // Add model (wizard step 2)
  const handleAddModel = async () => {
    if (!savedProviderId || !modelForm.isValid) return;
    try {
      setAddingModel(true);
      setModelTestResult(null);
      const payload = modelForm.toPayload();
      await modelV2API.createDefinition(savedProviderId, payload);
      setAddedModelCount(prev => prev + 1);
      toast.success(t('modelAdded'), `${payload.name} (${payload.model_id})`);

      setModelTesting(true);
      try {
        const res = await providerAPI.testCredentials(savedProviderId, payload.model_id);
        setModelTestResult({
          success: res.data.success,
          message: res.data.success
            ? `${t('status.connected')}${res.data.latency_ms ? ` (${res.data.latency_ms}ms)` : ''}`
            : (res.data.error || res.data.message || t('form.testFailed')),
          latency: res.data.latency_ms,
        });
      } catch (err: any) {
        setModelTestResult({ success: false, message: err.response?.data?.detail || 'Request failed' });
      } finally {
        setModelTesting(false);
      }
    } catch (err: any) {
      toast.error(t('operationFailed'), err.response?.data?.detail || err.message);
    } finally {
      setAddingModel(false);
    }
  };

  const resetModelFormState = () => {
    modelForm.reset();
    setModelTestResult(null);
    setModelTesting(false);
  };

  const canSave = !showProviderPicker && !!selectedCatalogId && (
    selectedCatalogId !== 'openai-compatible' ||
    (!!providerName.trim() && !!baseUrl.trim())
  );
  const pendingModelCount = new Set([
    ...discoveredModels.map(model => model.id),
    ...(isAzureProviderId(selectedCatalogId) && azureDeploymentName.trim() ? [azureDeploymentName.trim()] : []),
  ]).size;

  // Dynamic EntitySheet props based on wizard step
  const getSheetProps = () => {
    switch (step) {
      case 'provider':
        return {
          submitDisabled: !canSave || saving || !discoveryReady,
          submitLoading: saving,
          submitLabel: t(pendingModelCount > 0 ? 'wizard.saveAndAddModels' : 'wizard.saveProvider', { count: pendingModelCount }),
          onSubmit: handleSave,
          footerLeft: undefined as React.ReactNode,
        };
      case 'success':
        return {
          submitDisabled: false,
          submitLoading: false,
          submitLabel: t('form.done'),
          onSubmit: handleComplete,
          footerLeft: (
            <button
              onClick={() => setStep('add-model')}
              className="flex items-center gap-1.5 px-4 py-2 text-sm text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors"
            >
              <Plus className="w-4 h-4" />
              {t('wizard.addCustomModel')}
            </button>
          ),
        };
      case 'add-model': {
        if (modelTestResult) {
          return {
            submitDisabled: false,
            submitLoading: false,
            submitLabel: t('form.done'),
            onSubmit: handleComplete,
            footerLeft: (
              <button
                onClick={resetModelFormState}
                className="flex items-center gap-1.5 px-4 py-2 text-sm text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors"
              >
                <Plus className="w-4 h-4" />
                {t('wizard.addAnother')}
              </button>
            ),
          };
        }
        return {
          submitDisabled: !modelForm.isValid || addingModel || modelTesting,
          submitLoading: addingModel || modelTesting,
          submitLabel: t('form.addModelBtn'),
          onSubmit: handleAddModel,
          footerLeft: (
            <button
              onClick={handleComplete}
              className="flex items-center gap-1.5 px-4 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
            >
              {t('wizard.skipForNow')}
            </button>
          ),
        };
      }
    }
  };

  const sheetProps = getSheetProps();

  const rexContext = `你是 AI 模型配置助手，帮助用户添加和配置 AI 模型 Provider。

**支持的 Provider 类型：**
- OpenAI Compatible：任意兼容 OpenAI API 格式的服务（如 SiliconFlow、LM Studio、Ollama、自建服务）
- 主流云 API：OpenAI、Anthropic、Google、阿里云、百度等

**配置步骤：**
1. 选择 Provider 类型（推荐首选 OpenAI Compatible）
2. 填写 API Key 和 Base URL
3. 选择要启用的模型

请帮用户找到合适的 Provider 并完成配置。`;

  const rexWelcome = `你好！我来帮你添加一个 AI 模型 Provider。

请告诉我：
- 你要接入哪个服务？（OpenAI、Anthropic、阿里云百炼、本地 Ollama...）
- 或者描述你的使用场景，我来推荐合适的模型

我可以帮你找到 API Key 的获取方式，以及配置建议。`;

  return (
    <EntitySheet
      open
      mode="create"
      entityType={step === 'add-model' ? t('form.model') : t('configuration.provider')}
      width={720}
      minWidth={360}
      maxWidth={960}
      entityName={step !== 'provider' ? savedProviderName : undefined}
      icon={<Brain className="w-5 h-5" />}
      rexSystemContext={rexContext}
      rexWelcomeMessage={rexWelcome}
      submitDisabled={sheetProps.submitDisabled}
      submitLoading={sheetProps.submitLoading}
      submitLabel={sheetProps.submitLabel}
      onClose={handleWizardClose}
      onSubmit={sheetProps.onSubmit}
      footerLeft={sheetProps.footerLeft}
      hideRex={step !== 'add-model'}
      hideTest
      initialTab="form"
    >
      {/* ── Step 1: Provider Configuration ── */}
      {step === 'provider' && (
        <div className="space-y-0">
          <div className="space-y-5">
            {showProviderPicker || !selectedCatalog ? (
              <ProviderCatalogPicker
                providers={availableProviders}
                selectedId={selectedCatalogId}
                loading={loadingCatalog}
                failed={catalogFailed}
                onSelect={handleSelectCatalogProvider}
                onRetry={loadCatalog}
                onBack={selectedCatalog ? () => setShowProviderPicker(false) : undefined}
              />
            ) : (
              <div>
                <label className="mb-1.5 block text-sm font-medium text-gray-700">{t('form.providerType')}</label>
                <button type="button" disabled={saving} onClick={() => setShowProviderPicker(true)} className="flex w-full items-center justify-between gap-3 rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-left text-sm hover:border-gray-400">
                  <span className="font-medium text-gray-900">{selectedCatalog.name}</span>
                  <span className="flex shrink-0 items-center gap-1 text-xs text-gray-500">{t('providerPicker.change')}<ChevronDown className="h-3.5 w-3.5" /></span>
                </button>
              </div>
            )}

            {/* Catalog provider form */}
            {selectedCatalog && (
              <div hidden={showProviderPicker} className="space-y-5">
                {selectedCatalogId === 'openai-compatible' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {t('configuration.providerName')}
                      <span className="text-slate-500 ml-1">*</span>
                    </label>
                    <input
                      type="text"
                      value={providerName}
                      onChange={e => setProviderName(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                      placeholder="e.g. SiliconFlow, LM Studio, My API"
                    />
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    API Key
                    {!providerAllowsEmptyApiKey(selectedCatalogId) && <span className="text-slate-500"> *</span>}
                    {selectedCatalogId === 'ollama' && <span className="text-gray-400 font-normal ml-1">{t('form.ollamaNoKey')}</span>}
                    {selectedCatalogId !== 'ollama' && providerAllowsEmptyApiKey(selectedCatalogId) && (
                      <span className="text-gray-400 font-normal ml-1">{t('form.apiKeyOptional')}</span>
                    )}
                  </label>
                  <div className="relative">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      value={apiKey}
                      onChange={e => setApiKey(e.target.value)}
                      className="w-full px-3 py-2 pr-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                      placeholder={
                        providerAllowsEmptyApiKey(selectedCatalogId) && selectedCatalogId !== 'ollama'
                          ? t('form.apiKeyOptionalPlaceholder')
                          : (credentialFields.find(f => f.name === 'api_key')?.placeholder || 'sk-...')
                      }
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(!showApiKey)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 rounded"
                      title={showApiKey ? t('form.hide') : t('form.show')}
                    >
                      {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  {isThreatBookLLMProviderId(selectedCatalogId) && (
                    <a
                      href={THREATBOOK_FREE_KEY_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm font-medium text-green-700 transition-colors hover:border-green-300 hover:bg-green-100"
                    >
                      <ExternalLink className="h-4 w-4" />
                      {t('form.claimFreeKey')}
                    </a>
                  )}
                </div>

                <div className="col-span-2">
                  <div className="mb-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <label htmlFor="catalog-provider-base-url" className="text-sm font-medium text-gray-700">
                      {t('configuration.proxyUrl')}
                      {isCatalogBaseUrlRequired(selectedCatalogId) ? (
                        <span className="text-slate-500 ml-1">*</span>
                      ) : (
                        <span className="text-gray-400 font-normal ml-1">{t('form.baseUrlOptional')}</span>
                      )}
                    </label>
                    {selectedCatalogId === 'threatbook-io-llm' && <span id="catalog-provider-node-notice" className="text-xs text-gray-500">{t('form.threatbookIoNodeNotice')}</span>}
                  </div>
                  <input
                    id="catalog-provider-base-url"
                    aria-describedby={selectedCatalogId === 'threatbook-io-llm' ? 'catalog-provider-node-notice' : undefined}
                    type="text"
                    value={baseUrl}
                    onChange={e => setBaseUrl(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                    placeholder={selectedCatalog.default_base_url || 'https://api.example.com/v1'}
                  />
                </div>

                {isAzureProviderId(selectedCatalogId) && (
                  <div className="space-y-3 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        {t('form.azureDeploymentName')}
                      </label>
                      <input
                        type="text"
                        value={azureDeploymentName}
                        onChange={e => setAzureDeploymentName(e.target.value)}
                        className="w-full px-3 py-2 border border-blue-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 text-sm bg-white"
                        placeholder={t('form.azureDeploymentPlaceholder')}
                      />
                      <p className="mt-1 text-xs text-blue-800">{t('form.azureDeploymentHint')}</p>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        {t('form.azureDeploymentDisplayName')}
                      </label>
                      <input
                        type="text"
                        value={azureDeploymentDisplayName}
                        onChange={e => setAzureDeploymentDisplayName(e.target.value)}
                        className="w-full px-3 py-2 border border-blue-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 text-sm bg-white"
                        placeholder={azureDeploymentName.trim() || t('form.azureDeploymentDisplayPlaceholder')}
                      />
                    </div>
                  </div>
                )}

                <ProviderKeyCheck providerId={selectedCatalogId} credentials={{ api_key: apiKey.trim(), base_url: baseUrl.trim() || undefined }} disabled={saving} />
                <ModelDiscovery
                  providerId={selectedCatalogId}
                  mode="stage"
                  newInstance={selectedCatalogId === 'openai-compatible'}
                  credentials={{ api_key: apiKey.trim(), base_url: baseUrl.trim() || undefined }}
                  disabled={saving}
                  onReadyChange={setDiscoveryReady}
                  onSelectionChange={setDiscoveredModels}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Step: Success (catalog providers with predefined models) ── */}
      {step === 'success' && (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center mb-4">
            <CheckCircle2 className="w-8 h-8 text-green-600" />
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('wizard.providerSaved')}</h3>
          <p className="text-sm text-gray-500 max-w-xs">
            {t('wizard.providerSavedHint', { name: savedProviderName })}
          </p>
          {addedModelCount > 0 && (
            <p className="mt-3 text-sm text-green-600 font-medium">
              {t('wizard.modelsAdded', { count: addedModelCount })}
            </p>
          )}
          {discoveryImportResult && (
            <div className="mt-4 w-full space-y-3 text-left">
              <ModelImportSummary result={discoveryImportResult} />
              {discoveryImportResult.failed.length > 0 && savedProviderId && (
                <button type="button" disabled={saving} onClick={async () => {
                  if (saving) return;
                  setSaving(true);
                  const failedIds = new Set(discoveryImportResult.failed.map(item => item.id));
                  try { await importSelectedModels(savedProviderId, lastImportCandidates.current.filter(model => failedIds.has(model.id))); }
                  finally { setSaving(false); }
                }} className="rounded-lg border border-amber-300 px-3 py-2 text-sm text-amber-800 disabled:opacity-50">{saving ? t('discovery.importing') : t('discovery.retryFailed')}</button>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Step: Add Model (wizard step 2) ── */}
      {step === 'add-model' && (
        <div className="space-y-4">
          {addedModelCount === 0 && (
            <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg">
              <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-amber-800">{t('wizard.noModelsNote')}</p>
            </div>
          )}
          {addedModelCount > 0 && !modelTestResult && (
            <div className="flex items-center gap-2 p-3 bg-green-50 border border-green-200 rounded-lg">
              <CheckCircle2 className="w-4 h-4 text-green-600 flex-shrink-0" />
              <p className="text-xs text-green-800">{t('wizard.modelsAdded', { count: addedModelCount })}</p>
            </div>
          )}
          <ModelFormFields
            form={modelForm}
            testResult={modelTestResult}
            testing={modelTesting}
            modelIdPlaceholder={savedProviderId && isAzureProviderId(savedProviderId) ? t('form.azureDeploymentPlaceholder') : undefined}
            modelIdHint={savedProviderId && isAzureProviderId(savedProviderId) ? t('form.azureModelIdHint') : undefined}
          />
        </div>
      )}
    </EntitySheet>
  );
}

// ==================== Shared Model Form ====================

function useModelForm() {
  const [modelId, setModelId] = useState('');
  const [name, setName] = useState('');
  const [contextWindow, setContextWindow] = useState('');
  const [maxOutput, setMaxOutput] = useState('');
  const [supportsVision, setSupportsVision] = useState(false);
  const [supportsTools, setSupportsTools] = useState(true);
  const [supportsStreaming, setSupportsStreaming] = useState(true);
  const [supportsReasoning, setSupportsReasoning] = useState(true);
  const [inputPrice, setInputPrice] = useState('0');
  const [outputPrice, setOutputPrice] = useState('0');
  const [cacheReadPrice, setCacheReadPrice] = useState('');
  const [currency, setCurrency] = useState('USD');

  const changeCurrency = useCallback((nextCurrency: string) => {
    if (nextCurrency === currency) return;
    if (convertCurrencyAmount(1, currency, nextCurrency) === null) return;
    setInputPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setOutputPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setCacheReadPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setCurrency(nextCurrency);
  }, [currency]);

  const reset = useCallback(() => {
    setModelId(''); setName('');
    setContextWindow(''); setMaxOutput('');
    setSupportsVision(false); setSupportsTools(true);
    setSupportsStreaming(true); setSupportsReasoning(true);
    setInputPrice('0'); setOutputPrice('0'); setCacheReadPrice(''); setCurrency('USD');
  }, []);

  const toPayload = useCallback(() => {
    const payload: CustomModelCreate = {
      model_id: modelId.trim(),
      name: name.trim(),
      supports_vision: supportsVision,
      supports_tools: supportsTools,
      supports_streaming: supportsStreaming,
      supports_reasoning: supportsReasoning,
      input_price: parseFloat(inputPrice) || 0,
      output_price: parseFloat(outputPrice) || 0,
      currency,
    };
    const parsedCacheReadPrice = parseFloat(cacheReadPrice);
    if (Number.isFinite(parsedCacheReadPrice) && parsedCacheReadPrice >= 0) {
      payload.cache_read_price = parsedCacheReadPrice;
    }
    const parsedContextWindow = parseInt(contextWindow);
    if (Number.isFinite(parsedContextWindow) && parsedContextWindow > 0) {
      payload.context_window = parsedContextWindow;
    }
    const parsedMaxOutput = parseInt(maxOutput);
    if (Number.isFinite(parsedMaxOutput) && parsedMaxOutput > 0) {
      payload.max_output_tokens = parsedMaxOutput;
    }
    return payload;
  }, [modelId, name, contextWindow, maxOutput, supportsVision, supportsTools, supportsStreaming, supportsReasoning, inputPrice, outputPrice, cacheReadPrice, currency]);

  const isValid = modelId.trim() !== '' && name.trim() !== '';

  return {
    modelId, setModelId, name, setName,
    contextWindow, setContextWindow, maxOutput, setMaxOutput,
    supportsVision, setSupportsVision, supportsTools, setSupportsTools,
    supportsStreaming, setSupportsStreaming, supportsReasoning, setSupportsReasoning,
    inputPrice, setInputPrice, outputPrice, setOutputPrice,
    cacheReadPrice, setCacheReadPrice,
    currency, setCurrency: changeCurrency,
    reset, toPayload, isValid,
  };
}

function ModelFormFields({ form, testResult, testing, modelIdPlaceholder, modelIdHint }: {
  form: ReturnType<typeof useModelForm>;
  testResult: { success: boolean; message: string; latency?: number } | null;
  testing: boolean;
  modelIdPlaceholder?: string;
  modelIdHint?: string;
}) {
  const { t } = useTranslation('model');
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('form.modelId')} <span className="text-slate-500">*</span>
          </label>
          <input
            type="text"
            value={form.modelId}
            onChange={e => form.setModelId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder={modelIdPlaceholder || 'model-id'}
          />
          {modelIdHint && <p className="mt-1 text-xs text-gray-500">{modelIdHint}</p>}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('form.displayName')} <span className="text-slate-500">*</span>
          </label>
          <input
            type="text"
            value={form.name}
            onChange={e => form.setName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder="model-name"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.contextWindow')}</label>
          <input
            type="number"
            value={form.contextWindow}
            onChange={e => form.setContextWindow(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder="Auto"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.maxOutput')}</label>
          <input
            type="number"
            value={form.maxOutput}
            onChange={e => form.setMaxOutput(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder="Auto"
          />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.capabilities')}</label>
        <div className="grid grid-cols-2 gap-2">
          <ToggleField label={t('form.toolCall')} checked={form.supportsTools} onChange={form.setSupportsTools} />
          <ToggleField label={t('form.vision')} checked={form.supportsVision} onChange={form.setSupportsVision} />
          <ToggleField label={t('form.streaming')} checked={form.supportsStreaming} onChange={form.setSupportsStreaming} />
          <ToggleField label={t('form.reasoning')} checked={form.supportsReasoning} onChange={form.setSupportsReasoning} />
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.pricing')}</label>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('form.input')}</label>
            <input
              type="number"
              step="0.01"
              value={form.inputPrice}
              onChange={e => form.setInputPrice(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('form.output')}</label>
            <input
              type="number"
              step="0.01"
              value={form.outputPrice}
              onChange={e => form.setOutputPrice(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('form.cacheRead')}</label>
            <input
              type="number"
              step="0.01"
              value={form.cacheReadPrice}
              onChange={e => form.setCacheReadPrice(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder="—"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('form.currency')}</label>
            <select
              value={form.currency}
              onChange={e => form.setCurrency(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            >
              <option value="USD">USD</option>
              <option value="CNY">CNY</option>
            </select>
          </div>
        </div>
      </div>

      {(testing || testResult) && (
        <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
          testing
            ? 'bg-slate-50 text-slate-800 border border-slate-200'
            : testResult?.success
              ? 'bg-green-50 text-green-800 border border-green-200'
              : 'bg-red-50 text-red-800 border border-red-200'
        }`}>
          {testing ? (
            <Loader2 className="w-4 h-4 mt-0.5 flex-shrink-0 animate-spin" />
          ) : testResult?.success ? (
            <Check className="w-4 h-4 mt-0.5 flex-shrink-0" />
          ) : (
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          )}
          <span>{testing ? t('form.testingConnection') : testResult?.message}</span>
        </div>
      )}
    </>
  );
}

// ==================== Add Model Dialog ====================

function AddModelDialog({ provider, models, loadingModels, onClose, onCreated, initialMethod = 'automatic' }: {
  initialMethod?: 'automatic' | 'manual';
  provider: EnrichedProvider;
  models: ModelDefinitionV2[];
  loadingModels: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const form = useModelForm();
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; latency?: number } | null>(null);
  const [testing, setTesting] = useState(false);
  const [method, setMethod] = useState<'automatic' | 'manual'>(initialMethod);
  const [discoveryReady, setDiscoveryReady] = useState(true);

  const handleSubmit = async () => {
    if (!form.isValid) {
      toast.warning(t('form.fillModelId'));
      return;
    }
    try {
      setLoading(true);
      setTestResult(null);
      await modelV2API.createDefinition(provider.id, form.toPayload());
      onCreated();
      toast.success(t('modelAdded'), `${form.name.trim()} (${form.modelId.trim()})`);

      setTesting(true);
      try {
        const res = await providerAPI.testCredentials(provider.id, form.modelId.trim());
        setTestResult({
          success: res.data.success,
          message: res.data.success
            ? `${t('status.connected')}${res.data.latency_ms ? ` (${res.data.latency_ms}ms)` : ''}`
            : (res.data.error || res.data.message || t('form.testFailed')),
          latency: res.data.latency_ms,
        });
      } catch (err: any) {
        setTestResult({ success: false, message: err.response?.data?.detail || 'Request failed' });
      } finally {
        setTesting(false);
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.message;
      toast.error(t('operationFailed'), detail);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => onClose();

  const rexContext = `你是 AI 模型配置助手，帮助用户为 ${provider.name} 添加自定义模型定义。

Provider: ${provider.name} (${provider.id})

**模型参数说明：**
- 模型 ID：API 调用时使用的标识符（如 gpt-4o, claude-3-5-sonnet）
- 上下文窗口：模型支持的最大 Token 数
- 能力：工具调用、视觉理解、推理等
- 价格：每百万 Token 的费用

请帮用户填写正确的模型参数。`;

  const rexWelcome = `你好！我来帮你向 **${provider.name}** 添加自定义模型。

请告诉我你要添加的模型名称，我可以帮你：
- 查找正确的模型 ID
- 填写上下文窗口大小
- 配置能力标志（工具调用、视觉等）
- 设置正确的价格`;

  return (
    <EntitySheet
      open
      mode="create"
      entityType={t('form.model')}
      width={720}
      minWidth={360}
      maxWidth={960}
      entityName={provider.name}
      icon={<Plus className="w-5 h-5" />}
      rexSystemContext={rexContext}
      rexWelcomeMessage={rexWelcome}
      submitDisabled={loading || testing || (method === 'automatic' ? !discoveryReady : !form.isValid)}
      submitLoading={loading || testing}
      submitLabel={method === 'automatic' ? t('form.close') : testResult ? t('form.done') : t('form.addModelBtn')}
      onClose={handleClose}
      onSubmit={method === 'automatic' || testResult ? handleClose : handleSubmit}
      hideRex={method === 'automatic'}
      hideTest={method === 'automatic'}
      initialTab="form"
    >
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
          <input
            type="text"
            readOnly
            value={provider.name}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg bg-gray-50 text-sm text-gray-900 cursor-default"
          />
        </div>
        <div role="tablist" aria-label={t('discovery.addMethod')} className="flex gap-1 rounded-lg bg-gray-100 p-1">
          {(['automatic', 'manual'] as const).map(value => (
            <button key={value} type="button" role="tab" aria-selected={method === value} aria-controls={`add-model-${value}`} id={`add-model-tab-${value}`} disabled={loading || testing} onClick={() => setMethod(value)} className={`flex-1 rounded-md px-3 py-2 text-sm transition-colors disabled:opacity-50 ${method === value ? 'bg-white font-medium text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'}`}>
              {t(`discovery.${value}`)}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="add-model-automatic" aria-labelledby="add-model-tab-automatic" hidden={method !== 'automatic'} className="space-y-3">
          <ModelDiscovery providerId={provider.id} mode="import" existingModelIds={models.map(model => model.id)} disabled={loading || testing || loadingModels} onReadyChange={setDiscoveryReady} onImported={onCreated} />
        </div>
        <div role="tabpanel" id="add-model-manual" aria-labelledby="add-model-tab-manual" hidden={method !== 'manual'} className="space-y-4">
          <ModelFormFields
            form={form}
            testResult={testResult}
            testing={testing}
            modelIdPlaceholder={isAzureProviderId(provider.id) ? t('form.azureDeploymentPlaceholder') : undefined}
            modelIdHint={isAzureProviderId(provider.id) ? t('form.azureModelIdHint') : undefined}
          />
        </div>
      </div>
    </EntitySheet>
  );
}

function ToggleField({ label, checked, onChange, disabled, disabledHint }: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  /** Tooltip text shown when the toggle is disabled. */
  disabledHint?: string;
}) {
  return (
    <label
      className={`flex items-center gap-3 w-full min-w-0 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      title={disabled ? disabledHint : undefined}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${
          disabled
            ? checked
              ? 'bg-green-200 cursor-not-allowed'   // locked-ON: muted green
              : 'bg-gray-200 cursor-not-allowed'    // locked-OFF: light gray
            : checked ? 'bg-green-600' : 'bg-gray-400'
        }`}
      >
        <span className={`absolute top-1 w-4 h-4 rounded-full shadow-sm transition-transform ${
          disabled
            ? checked ? 'bg-white' : 'bg-gray-400'
            : 'bg-white'
        } ${checked ? 'translate-x-5 left-0' : 'translate-x-1 left-0'}`} />
      </button>
      <span className={`text-sm ${disabled ? 'text-gray-400' : 'text-gray-700'}`}>{label}</span>
    </label>
  );
}

function getDefaultReasoningToggleValue(providerId: string, modelId: string): boolean {
  const lowered = modelId.toLowerCase();

  if (providerId === 'threatbook-cn-llm') return true;

  if (lowered.includes('claude')) return true;
  if (providerId === 'openai' && ['o1', 'o3', 'gpt-5'].some(tag => lowered.includes(tag))) return true;
  if (providerId === 'google' && lowered.includes('gemini') && (lowered.includes('2.5') || lowered.includes('gemini-3'))) return true;
  if (providerId === 'groq') return true;

  if (['threatbook-cn-llm', 'threatbook-io-llm', 'alibaba', 'moonshot'].includes(providerId)) {
    if (lowered.includes('qwen3.6-plus')) return true;
  }

  return true;
}

function allowsBuiltInVisionToggle(modelId: string): boolean {
  const lowered = modelId.toLowerCase();
  return (
    lowered.includes('qwen3.8-max')
    || lowered.includes('qwen3.7-plus')
    || lowered.includes('qwen3.7-flash')
    || lowered.includes('qwen3.6-plus')
    || lowered.includes('kimi-k2.6')
    || lowered.includes('kimi-k2.7-code')
  );
}

// ==================== Configure Dialog ====================

function ConfigureProviderDialog({ provider, existingCredentials, loadingModels, onClose, onConfigured, onDelete, onAddModel, onModelsChanged, models }: {
  provider: EnrichedProvider;
  existingCredentials: ProviderCredentials | null;
  models: ModelDefinitionV2[];
  loadingModels: boolean;
  modelEnabledMap: Record<string, boolean>;
  defaultModelId?: string;
  onClose: () => void;
  onConfigured: () => void;
  onTestResult: (success: boolean) => void;
  onDelete: () => void;
  onAddModel: () => void;
  onEditModel: (model: ModelDefinitionV2) => void;
  onToggleModel: (modelId: string, enabled: boolean) => Promise<void>;
  onDeleteModel: (modelId: string) => Promise<void>;
  onModelsChanged: () => void;
}) {
  const { t } = useTranslation('model');
  const toast = useToast();
  const [apiKey, setApiKey] = useState(existingCredentials?.api_key ?? '');
  // The key shown when the dialog opened. Saving sends a key only when it differs:
  // sending this one back would undo a rotation made elsewhere in the meantime.
  const [openedApiKey] = useState(() => (existingCredentials?.api_key ?? '').trim());
  const [baseUrl, setBaseUrl] = useState(existingCredentials?.base_url ?? '');
  const [name, setName] = useState(provider.name);
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ready, setReady] = useState(true);
  const [inventory, setInventory] = useState<ModelInventoryDraft>({ models: [], deleted: [] });
  const [saveError, setSaveError] = useState<string | null>(null);
  const lock = useRef(false);
  const custom = provider.id === 'openai-compatible' || provider.id.startsWith('custom-');
  const credentials = { api_key: apiKey.trim() || undefined, base_url: baseUrl.trim() || undefined };
  const save = async () => {
    if (lock.current || !ready || loadingModels) return;
    if (!apiKey.trim() && !existingCredentials?.has_credential && !providerAllowsEmptyApiKey(provider.id)) { setSaveError(t('discovery.codes.missing_credentials')); return; }
    if ((custom || isAzureProviderId(provider.id)) && !baseUrl.trim()) { setSaveError(t('form.baseUrlRequired')); return; }
    lock.current = true; setSaving(true); setSaveError(null);
    let credentialsSaved = false;
    try {
      await providerAPI.setCredentials(provider.id, {
        ...credentials,
        api_key: credentials.api_key !== openedApiKey ? credentials.api_key : undefined,
        provider_name: custom ? name.trim() || undefined : undefined,
        skip_catalog_bootstrap: true,
      });
      credentialsSaved = true;
      await providerAPI.saveModelInventory(provider.id, inventory);
      onModelsChanged(); toast.success(t('credentialsSaved')); onConfigured();
    } catch (error) {
      const message = extractErrorMessage(error);
      setSaveError(credentialsSaved ? t('compact.inventorySaveFailedAfterCredentials', { error: message }) : message);
    }
    finally { lock.current = false; setSaving(false); }
  };
  return <EntitySheet open mode="edit" entityType={t('configuration.provider')} entityName={provider.name} width={720} minWidth={360} maxWidth={960} icon={<Brain className="h-5 w-5" />} rexSystemContext="" rexWelcomeMessage="" hideRex hideTest initialTab="form" submitLabel={t('compact.save')} submitLoading={saving} submitDisabled={saving || !ready || loadingModels} onSubmit={save} onClose={onClose} footerLeft={<button type="button" disabled={saving} onClick={onDelete} className="flex items-center gap-1.5 text-xs text-red-600"><Trash2 className="h-3.5 w-3.5" />{t('form.removeProvider')}</button>}>
    <div className="space-y-6">
      <div className="space-y-4">
        {custom && <label className="block space-y-1.5 text-sm font-medium text-gray-700">{t('configuration.providerName')}<input value={name} disabled={saving} onChange={e => setName(e.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2.5 font-normal" /></label>}
        <div><label className="mb-1.5 block text-sm font-medium text-gray-700" htmlFor="provider-api-key">API Key</label><div className="relative"><input id="provider-api-key" type={showKey ? 'text' : 'password'} value={apiKey} disabled={saving} onChange={e => setApiKey(e.target.value)} placeholder={existingCredentials?.has_credential ? t('form.apiKeyKeepExisting') : 'sk-...'} className="w-full rounded-lg border border-gray-300 px-3 py-2.5 pr-10 text-sm" /><button type="button" onClick={() => setShowKey(value => !value)} title={t(showKey ? 'form.hide' : 'form.show')} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400">{showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div></div>
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <label htmlFor="provider-base-url" className="text-sm font-medium text-gray-700">{t('configuration.proxyUrl')}<span className="ml-1 font-normal text-gray-400">{custom || isAzureProviderId(provider.id) ? '*' : t('form.baseUrlOptional')}</span></label>
            {provider.id === 'threatbook-io-llm' && <span id="provider-node-notice" className="text-xs text-gray-500">{t('form.threatbookIoNodeNotice')}</span>}
          </div>
          <input id="provider-base-url" aria-describedby={provider.id === 'threatbook-io-llm' ? 'provider-node-notice' : undefined} value={baseUrl} disabled={saving} onChange={e => setBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm font-normal" />
        </div>
        <ProviderKeyCheck providerId={provider.id} credentials={credentials} disabled={saving} />
      </div>
      <ModelDiscovery providerId={provider.id} mode="stage" existingModelIds={models.map(model => model.id)} credentials={credentials} disabled={saving} onInventoryChange={setInventory} onReadyChange={setReady} onAddModel={onAddModel} />
      {saveError && <p role="alert" className="text-xs text-red-600">{saveError}</p>}
    </div>
  </EntitySheet>;
}

// ==================== Model Detail Sheet ====================

const inputCls = "w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm";
const inputClsReadOnly = inputCls + " bg-gray-50 text-gray-900 cursor-default";

function ModelDetailSheet({
  provider,
  model,
  readOnly = false,
  onClose,
  onSaved,
}: {
  provider: EnrichedProvider;
  model: ModelDefinitionV2;
  /** Members see the model's settings but cannot change them. */
  readOnly?: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const features = model.capabilities?.features || [];
  const modelSupportsReasoning = features.includes('reasoning') || !!model.capabilities?.supports_reasoning;
  const isPredefined = model.fetch_from === 'predefined';
  const visionToggleLocked = isPredefined && !allowsBuiltInVisionToggle(model.id);
  const readOnlyHint = readOnly ? t('detail.configAdminOnly') : undefined;
  // Same look as the fields that are always read-only (Provider, model ID), dark mode included.
  const fieldCls = readOnly ? inputClsReadOnly : inputCls;
  const [name, setName] = useState(model.name);
  const [contextWindow, setContextWindow] = useState(model.limits?.context_window != null ? String(model.limits.context_window) : '128000');
  const [maxOutput, setMaxOutput] = useState(model.limits?.max_output_tokens != null ? String(model.limits.max_output_tokens) : '4096');
  const [supportsTools, setSupportsTools] = useState(features.includes('tool_call') || !!model.capabilities?.supports_tools);
  const [supportsVision, setSupportsVision] = useState(
    features.includes('vision') || !!model.capabilities?.supports_vision,
  );
  const [supportsStreaming, setSupportsStreaming] = useState(!!model.capabilities?.supports_streaming);
  const [supportsReasoning, setSupportsReasoning] = useState(modelSupportsReasoning);
  const [inputPrice, setInputPrice] = useState(model.pricing ? String(model.pricing.input) : '0');
  const [outputPrice, setOutputPrice] = useState(model.pricing ? String(model.pricing.output) : '0');
  const [cacheReadPrice, setCacheReadPrice] = useState(
    model.pricing?.cache_read != null ? String(model.pricing.cache_read) : '',
  );
  const [currency, setCurrency] = useState(model.pricing?.currency ?? 'USD');
  const hasTieredPricing = Boolean(model.pricing?.tiers?.length);
  const [overrideTieredPricing, setOverrideTieredPricing] = useState(false);
  const [pricingEdited, setPricingEdited] = useState(false);
  const saveFixedPricing = hasTieredPricing ? overrideTieredPricing : pricingEdited;
  const [enabled, setEnabled] = useState(true);
  const [defaultParameters, setDefaultParameters] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);

  const handleCurrencyChange = (nextCurrency: string) => {
    if (nextCurrency === currency) return;
    if (convertCurrencyAmount(1, currency, nextCurrency) === null) return;
    setInputPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setOutputPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setCacheReadPrice(value => convertEditablePrice(value, currency, nextCurrency));
    setCurrency(nextCurrency);
    setPricingEdited(true);
  };

  const loadModelSettings = useCallback(async () => {
    setLoadingSettings(true);
    setSettingsLoadError(null);
    try {
      const r = await modelSettingsAPI.get(provider.id, model.id);
      setEnabled(r.data.enabled !== false);
      setDefaultParameters(r.data.default_parameters || {});
      if (modelSupportsReasoning) {
        const configured = r.data.default_parameters?.enable_thinking;
        setSupportsReasoning(
          typeof configured === 'boolean'
            ? configured
            : getDefaultReasoningToggleValue(provider.id, model.id)
        );
      }
    } catch (settingsError) {
      setSettingsLoadError(extractErrorMessage(settingsError));
    } finally {
      setLoadingSettings(false);
    }
  }, [model.id, modelSupportsReasoning, provider.id]);

  useEffect(() => { void loadModelSettings(); }, [loadModelSettings]);

  const handleSave = async () => {
    if (readOnly || loading || loadingSettings || settingsLoadError) return;
    if (saveFixedPricing && (
      [inputPrice, outputPrice].some(value => value.trim() === '' || !Number.isFinite(Number(value)) || Number(value) < 0)
      || (cacheReadPrice.trim() !== '' && (!Number.isFinite(Number(cacheReadPrice)) || Number(cacheReadPrice) < 0))
    )) {
      toast.error(t('pricing.invalidPrice'));
      return;
    }
    setLoading(true);
    try {
      await Promise.all([
        modelV2API.createDefinition(provider.id, {
          model_id: model.id,
          name: name.trim() || model.id,
          context_window: parseInt(contextWindow) || undefined,
          max_output_tokens: parseInt(maxOutput) || undefined,
          supports_vision: supportsVision,
          supports_tools: supportsTools,
          supports_streaming: supportsStreaming,
          supports_reasoning: modelSupportsReasoning ? modelSupportsReasoning : supportsReasoning,
          ...(saveFixedPricing ? {
            input_price: Number(inputPrice),
            output_price: Number(outputPrice),
            cache_read_price: cacheReadPrice.trim() === '' ? null : Number(cacheReadPrice),
            currency,
          } : {}),
        }),
        modelSettingsAPI.update(provider.id, model.id, {
          enabled,
          default_parameters: modelSupportsReasoning
            ? {
              ...defaultParameters,
              enable_thinking: supportsReasoning,
            }
            : undefined,
        }),
      ]);
      toast.success(t('credentialsSaved'));
      onSaved();
    } catch (e: any) {
      toast.error(e?.message || t('configFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <EntitySheet
      open
      mode="edit"
      entityType={t('form.model')}
      entityName={model.name}
      icon={<Brain className="w-5 h-5" />}
      rexSystemContext=""
      rexWelcomeMessage=""
      submitDisabled={readOnly || loading || loadingSettings || !!settingsLoadError}
      submitLoading={loading}
      submitLabel={t('form.save')}
      onClose={onClose}
      onSubmit={handleSave}
      hideRex
      hideTest
    >
      {settingsLoadError && (
        <div role="alert" className="mb-4 space-y-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <p>{t('configuration.settingsLoadFailed')}</p>
          <p className="break-words text-xs">{settingsLoadError}</p>
          <button type="button" onClick={loadModelSettings} disabled={loadingSettings} className="rounded border border-red-300 px-3 py-1.5 text-xs hover:bg-red-100 disabled:opacity-40">{t('configuration.retry')}</button>
        </div>
      )}
      <fieldset disabled={readOnly} className="space-y-4 min-w-0">
        {readOnly && <p className="text-xs text-gray-500">{t('detail.configAdminOnly')}</p>}
        {loadingSettings ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : (
          <>
            {/* Provider — 只读 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
              <input type="text" readOnly value={provider.name} className={inputClsReadOnly} />
            </div>

            {/* 模型 ID + 显示名称：模型 ID 只读，显示名称可编辑 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.modelId')}</label>
                <input type="text" readOnly value={model.id} className={inputClsReadOnly} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.displayName')}</label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  className={fieldCls}
                />
              </div>
            </div>

            {/* 上下文窗口 + 最大输出 — 可编辑 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.contextWindow')}</label>
                <input
                  type="number"
                  value={contextWindow}
                  onChange={e => setContextWindow(e.target.value)}
                  className={fieldCls}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.maxOutput')}</label>
                <input
                  type="number"
                  value={maxOutput}
                  onChange={e => setMaxOutput(e.target.value)}
                  className={fieldCls}
                />
              </div>
            </div>

            {/* 能力 — 可编辑，与添加模型同款开关（绿色/灰色醒目） */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.capabilities')}</label>
              <div className="grid grid-cols-2 gap-2">
                <ToggleField
                  label={t('form.toolCall')}
                  checked={supportsTools}
                  onChange={setSupportsTools}
                  disabled={readOnly}
                  disabledHint={readOnlyHint}
                />
                <ToggleField
                  label={t('form.vision')}
                  checked={supportsVision}
                  onChange={setSupportsVision}
                  disabled={readOnly || visionToggleLocked}
                  disabledHint={readOnlyHint ?? (visionToggleLocked ? t('form.visionPredefinedHint') : undefined)}
                />
                <ToggleField
                  label={t('form.streaming')}
                  checked={supportsStreaming}
                  onChange={setSupportsStreaming}
                  disabled={readOnly}
                  disabledHint={readOnlyHint}
                />
                <ToggleField
                  label={t('form.reasoning')}
                  checked={supportsReasoning}
                  onChange={setSupportsReasoning}
                  disabled={readOnly}
                  disabledHint={readOnlyHint}
                />
              </div>
            </div>

            {/* 价格 — 可编辑 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.pricing')}</label>
              {model.pricing?.price_version && (
                <p className="text-xs text-gray-500 mb-2">{t('pricing.version', { version: model.pricing.price_version })}</p>
              )}
              {hasTieredPricing && model.pricing && <PricingTierTable pricing={model.pricing} />}
              {hasTieredPricing && !readOnly && (
                <label className="flex items-start gap-2 text-sm text-gray-700 mb-2">
                  <input
                    type="checkbox"
                    checked={overrideTieredPricing}
                    onChange={event => setOverrideTieredPricing(event.target.checked)}
                    className="mt-0.5 rounded border-gray-300"
                  />
                  <span>{t('pricing.overrideTiers')}</span>
                </label>
              )}
              {!readOnly && <p className="text-xs text-gray-500 mb-3">{t(hasTieredPricing ? 'pricing.overrideTiersHint' : 'pricing.editHint')}</p>}
              {(!hasTieredPricing || overrideTieredPricing) && <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.input')}</label>
                  <input type="number" min="0" step="any" value={inputPrice} onChange={e => { setInputPrice(e.target.value); setPricingEdited(true); }} className={fieldCls} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.output')}</label>
                  <input type="number" min="0" step="any" value={outputPrice} onChange={e => { setOutputPrice(e.target.value); setPricingEdited(true); }} className={fieldCls} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.cacheRead')}</label>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={cacheReadPrice}
                    onChange={e => { setCacheReadPrice(e.target.value); setPricingEdited(true); }}
                    className={fieldCls}
                    placeholder="—"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.currency')}</label>
                  <select value={currency} onChange={e => handleCurrencyChange(e.target.value)} className={fieldCls}>
                    {currency !== 'USD' && currency !== 'CNY' && (
                      <option value={currency}>{currency}</option>
                    )}
                    <option value="USD">USD</option>
                    <option value="CNY">CNY</option>
                  </select>
                </div>
              </div>}
            </div>

            {/* 启用此模型 — 可编辑 */}
            <div className="flex items-center justify-between py-2 border-t border-gray-200">
              <label className="text-sm font-medium text-gray-700">{t('form.enableModel')}</label>
              <button
                type="button"
                onClick={() => setEnabled(!enabled)}
                className={`transition-colors ${readOnly ? 'cursor-not-allowed' : ''}`}
                aria-checked={enabled}
                title={readOnlyHint}
              >
                {enabled
                  ? <ToggleRight className={`w-8 h-8 ${readOnly ? 'text-green-300' : 'text-green-500'}`} />
                  : <ToggleLeft className={`w-8 h-8 ${readOnly ? 'text-gray-200' : 'text-gray-300'}`} />}
              </button>
            </div>
          </>
        )}
      </fieldset>
    </EntitySheet>
  );
}

// ==================== Shared Model Selection ====================

type ModelSelectionGroup = {
  providerId: string;
  providerName: string;
  models: ModelDefinitionV2[];
};

function modelSupportsVision(model: ModelDefinitionV2): boolean {
  const capabilities = model.capabilities;
  return capabilities?.supports_vision === true
    || capabilities?.features?.includes('vision') === true
    || capabilities?.modalities?.input?.includes('image') === true;
}

function groupModelsForSelection(
  models: ModelDefinitionV2[],
  providerNames: Map<string, string>,
): ModelSelectionGroup[] {
  const grouped = new Map<string, ModelDefinitionV2[]>();
  models.forEach(model => {
    const entries = grouped.get(model.provider_id) ?? [];
    entries.push(model);
    grouped.set(model.provider_id, entries);
  });
  return Array.from(grouped.entries())
    .map(([providerId, providerModels]) => ({
      providerId,
      providerName: providerNames.get(providerId) || providerId,
      models: providerModels.sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id)),
    }))
    .sort((a, b) => a.providerName.localeCompare(b.providerName));
}

function formatModelContextWindow(model: ModelDefinitionV2): string {
  const contextWindow = model.limits?.context_window;
  if (!contextWindow) return '—';
  if (contextWindow >= 1_000_000) {
    return `${Number((contextWindow / 1_000_000).toFixed(1))}M`;
  }
  if (contextWindow >= 1_000) {
    return `${Number((contextWindow / 1_000).toFixed(1))}K`;
  }
  return String(contextWindow);
}

function formatModelPricing(
  model: ModelDefinitionV2,
  freeLabel: string,
  unavailableLabel: string,
): string {
  const pricing = model.pricing;
  if (!pricing) return unavailableLabel;
  if (isPricingFree(pricing)) return freeLabel;
  return formatPricingPerMillion(pricing);
}

function ModelSelectionInfo({ model }: { model: ModelDefinitionV2 }) {
  const { t } = useTranslation('model');
  const tooltipId = useId();
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);

  const show = useCallback((target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    const tooltipHalfWidth = 128;
    const viewportPadding = 8;
    setPosition({
      x: Math.min(
        window.innerWidth - tooltipHalfWidth - viewportPadding,
        Math.max(tooltipHalfWidth + viewportPadding, rect.left + rect.width / 2),
      ),
      y: rect.top - 8,
    });
  }, []);
  const hide = useCallback(() => setPosition(null), []);
  const label = `${t('modelSelection.info')} ${model.name || model.id}`;

  return (
    <>
      <button
        type="button"
        aria-label={label}
        aria-describedby={position ? tooltipId : undefined}
        onClick={event => {
          event.stopPropagation();
          show(event.currentTarget);
        }}
        onFocus={event => show(event.currentTarget)}
        onBlur={hide}
        onPointerEnter={event => show(event.currentTarget)}
        onPointerLeave={hide}
        onMouseEnter={event => show(event.currentTarget)}
        onMouseLeave={hide}
        className="mr-3 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1"
      >
        <Info className="h-4 w-4" aria-hidden="true" />
      </button>
      {position && typeof document !== 'undefined' && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none fixed z-[1000] w-64 -translate-x-1/2 -translate-y-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-xs leading-relaxed text-gray-600 shadow-lg"
          style={{ left: position.x, top: position.y }}
        >
          <div className="mb-1.5 truncate font-semibold text-gray-900">{model.name || model.id}</div>
          <dl className="space-y-1">
            <div className="flex gap-2">
              <dt className="shrink-0 text-gray-400">{t('form.modelId')}</dt>
              <dd className="min-w-0 flex-1 break-all text-right font-mono text-[11px] text-gray-700">{model.id}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="shrink-0 text-gray-400">{t('form.contextWindow')}</dt>
              <dd className="min-w-0 flex-1 text-right text-gray-700">{formatModelContextWindow(model)}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="shrink-0 text-gray-400">{t('form.pricing')}</dt>
              <dd className="min-w-0 flex-1 break-words text-right text-gray-700" title={t('pricing.priceOrder')}>
                {formatModelPricing(model, t('modelSelection.free'), t('modelSelection.unavailable'))}
              </dd>
            </div>
          </dl>
          <div className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-gray-200" />
        </div>,
        document.body,
      )}
    </>
  );
}

function ModelSelectionList({
  groups,
  selectedKey,
  pendingKey,
  disabled = false,
  onSelect,
}: {
  groups: ModelSelectionGroup[];
  selectedKey?: string | null;
  pendingKey?: string | null;
  disabled?: boolean;
  onSelect: (model: ModelDefinitionV2) => void;
}) {
  const { t } = useTranslation('model');

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      {groups.map(group => (
        <div key={group.providerId}>
          <div className="border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            {group.providerName}
          </div>
          {group.models.map(model => {
            const key = `${model.provider_id}/${model.id}`;
            const selected = selectedKey === key;
            return (
              <div
                key={key}
                className={`flex min-w-0 items-center border-b border-gray-100 transition-colors last:border-0 ${
                  selected ? 'bg-slate-50' : 'bg-white hover:bg-gray-50'
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelect(model)}
                  disabled={disabled}
                  aria-pressed={selected}
                  className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left text-sm text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className={`min-w-0 flex-1 truncate ${selected ? 'font-semibold text-slate-900' : 'font-medium text-gray-800'}`}>
                    {model.name || model.id}
                  </span>
                  {modelSupportsVision(model) && (
                    <span
                      title={t('form.vision')}
                      className="shrink-0 rounded-md bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-600"
                    >
                      {t('form.vision')}
                    </span>
                  )}
                  <span className="max-w-[42%] shrink-0 truncate text-xs text-gray-400" title={model.id}>
                    {model.id}
                  </span>
                  {pendingKey === key ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-gray-400" />
                  ) : selected ? (
                    <Check className="h-4 w-4 shrink-0 text-slate-700" />
                  ) : null}
                </button>
                <ModelSelectionInfo model={model} />
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ==================== Set Default Model Dialog ====================

function SetDefaultModelDialog({
  current,
  providers,
  onClose,
  onSaved,
  onCleared,
}: {
  current: { provider_id: string; model_id: string } | null;
  providers: EnrichedProvider[];
  onClose: () => void;
  onSaved: (m: { provider_id: string; model_id: string }) => void;
  onCleared?: () => void;
}) {
  const { t } = useTranslation('model');
  const toast = useToast();
  const [models, setModels] = useState<ModelDefinitionV2[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [invalidWarning, setInvalidWarning] = useState<string | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || saving) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, saving]);

  useEffect(() => {
    setLoading(true);
    setInvalidWarning(null);
    modelV2API.listDefinitions({ enabled_only: true }).then(r => {
      const loadedModels = (r.data.models || []).filter(
        model => model.model_type === 'llm',
      );
      setModels(loadedModels);

      // 校验当前默认模型是否仍在可用列表中
      if (current) {
        const isValid = loadedModels.some(
          m => m.provider_id === current.provider_id && m.id === current.model_id
        );
        if (!isValid) {
          const modelLabel = `${current.provider_id} / ${current.model_id}`;
          setInvalidWarning(t('dashboard.defaultModelInvalid', { model: modelLabel }));
          defaultModelAPI.delete('llm').catch(() => {});
          onCleared?.();
        }
      }
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const providerNames = useMemo(
    () => new Map(providers.map(provider => [provider.id, provider.name || provider.id])),
    [providers],
  );
  const grouped = useMemo(
    () => groupModelsForSelection(models, providerNames),
    [models, providerNames],
  );

  const handleSelect = async (providerId: string, modelId: string) => {
    setSaving(`${providerId}/${modelId}`);
    try {
      await defaultModelAPI.set('llm', providerId, modelId);
      toast.success(t('dashboard.defaultModelUpdated'));
      onSaved({ provider_id: providerId, model_id: modelId });
      // Tell every chat composer (Session, Agent / Workflow / Skill creators,
      // EntitySheet, ChatDialog, …) to re-resolve the default model's vision
      // capability so the "model does not support images" hint reflects the
      // new selection without a page reload.
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent(MODEL_CHANGED_EVENT));
      }
    } catch {
      toast.error(t('operationFailed'));
    } finally {
      setSaving(null);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={() => {
        if (!saving) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="default-model-dialog-title"
        className="bg-white rounded-xl shadow-xl w-full max-w-xl max-h-[78vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 id="default-model-dialog-title" className="text-lg font-semibold text-gray-900">{t('dashboard.setDefaultModel')}</h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            disabled={saving !== null}
            aria-label={t('modelSelection.closeDefault')}
            className="text-gray-400 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {invalidWarning && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              <AlertTriangle className="mt-0.5 w-3.5 h-3.5 flex-shrink-0" />
              <span>{invalidWarning}</span>
            </div>
          )}
          {loading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : grouped.length === 0 ? (
            <div className="py-10 text-center text-sm text-gray-500">{t('detail.noModels')}</div>
          ) : (
            <ModelSelectionList
              groups={grouped}
              selectedKey={current ? `${current.provider_id}/${current.model_id}` : null}
              pendingKey={saving}
              disabled={saving !== null}
              onSelect={model => void handleSelect(model.provider_id, model.id)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ==================== Auxiliary Models Dialog ====================

function FallbackModelsDialog({
  current,
  currentLoading,
  currentLoadError,
  primary,
  providers,
  onRetryCurrent,
  onClose,
  onSaved,
}: {
  current: FallbackModelRef[];
  currentLoading: boolean;
  currentLoadError: string | null;
  primary: { provider_id: string; model_id: string } | null;
  providers: EnrichedProvider[];
  onRetryCurrent: () => Promise<boolean>;
  onClose: () => void;
  onSaved: (models: FallbackModelRef[], availableModels: ModelDefinitionV2[]) => void;
}) {
  const { t } = useTranslation('model');
  const toast = useToast();
  const [draft, setDraft] = useState<FallbackModelRef[]>(() => current.map(model => ({ ...model })));
  const [models, setModels] = useState<ModelDefinitionV2[]>([]);
  const [loading, setLoading] = useState(true);
  const [modelsLoadError, setModelsLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);

  const loadModels = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    setModelsLoadError(null);
    try {
      const response = await modelV2API.listDefinitions({ enabled_only: true });
      setModels(response.data.models || []);
      return true;
    } catch (loadError) {
      setModelsLoadError(
        loadError instanceof Error ? loadError.message : 'Failed to load model definitions',
      );
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  useEffect(() => {
    if (currentLoading || currentLoadError) return;
    setDraft(current.map(model => ({ ...model })));
  }, [current, currentLoadError, currentLoading]);

  const handleRetryLoad = useCallback(async () => {
    await Promise.all([
      currentLoadError ? onRetryCurrent() : Promise.resolve(true),
      modelsLoadError ? loadModels() : Promise.resolve(true),
    ]);
  }, [currentLoadError, loadModels, modelsLoadError, onRetryCurrent]);

  const configuredProviderIds = useMemo(
    () => new Set(providers.filter(provider => provider.configured).map(provider => provider.id)),
    [providers],
  );
  const providerNames = useMemo(
    () => new Map(providers.map(provider => [provider.id, provider.name || provider.id])),
    [providers],
  );
  const availableModels = useMemo(
    () => models.filter(model => (
      model.model_type === 'llm'
      && configuredProviderIds.has(model.provider_id)
    )),
    [configuredProviderIds, models],
  );
  const availableByKey = useMemo(
    () => new Map(availableModels.map(model => [`${model.provider_id}\u0000${model.id}`, model])),
    [availableModels],
  );
  const draftKeys = useMemo(
    () => new Set(draft.map(model => `${model.provider_id}\u0000${model.model_id}`)),
    [draft],
  );
  const selectableModels = useMemo(
    () => availableModels.filter(model => {
      const key = `${model.provider_id}\u0000${model.id}`;
      const isPrimary = primary?.provider_id === model.provider_id && primary.model_id === model.id;
      return !isPrimary && !draftKeys.has(key);
    }),
    [availableModels, draftKeys, primary],
  );
  const selectableGroups = useMemo(
    () => groupModelsForSelection(selectableModels, providerNames),
    [providerNames, selectableModels],
  );
  const dirty = JSON.stringify(draft) !== JSON.stringify(current);
  const loadFailed = Boolean(currentLoadError || modelsLoadError);
  const routingDataLoading = currentLoading || loading;
  const hasInvalidDraft = useMemo(
    () => draft.some((fallback) => {
      const key = `${fallback.provider_id}\u0000${fallback.model_id}`;
      const isPrimary = primary?.provider_id === fallback.provider_id
        && primary.model_id === fallback.model_id;
      return isPrimary || !availableByKey.has(key);
    }),
    [availableByKey, draft, primary],
  );
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || saving) return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, saving]);

  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= draft.length) return;
    setDraft(previous => {
      const next = [...previous];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const handleSave = async () => {
    if (!dirty || saving || routingDataLoading || loadFailed || hasInvalidDraft) return;
    setSaving(true);
    try {
      await defaultModelAPI.setFallbacks(draft);
      toast.success(t('fallbacks.saved'));
      onSaved(draft, models);
    } catch (error: unknown) {
      toast.error(
        t('operationFailed'),
        error instanceof Error ? error.message : undefined,
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
      onClick={() => {
        if (!saving) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="fallback-models-dialog-title"
        className="flex max-h-[78vh] w-full max-w-xl flex-col rounded-xl bg-white shadow-xl"
        onClick={event => event.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 id="fallback-models-dialog-title" className="text-lg font-semibold text-gray-900">
              {t('fallbacks.title')}
            </h2>
            <p className="mt-0.5 text-xs text-gray-500">{t('fallbacks.description')}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            disabled={saving}
            aria-label={t('fallbacks.close')}
            className="text-gray-400 hover:text-gray-600 disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {routingDataLoading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
            </div>
          ) : loadFailed ? (
            <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-6 text-center">
              <AlertCircle className="mx-auto mb-2 h-6 w-6 text-amber-500" />
              <p className="text-sm font-medium text-amber-800">{t('fallbacks.loadFailed')}</p>
              <p className="mt-1 text-xs text-amber-700">{currentLoadError || modelsLoadError}</p>
              <button
                type="button"
                onClick={() => void handleRetryLoad()}
                className="mt-3 rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
              >
                {t('fallbacks.retry')}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              {draft.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-300 px-4 py-8 text-center">
                  <ListOrdered className="mx-auto mb-2 h-6 w-6 text-gray-300" />
                  <p className="text-sm font-medium text-gray-600">{t('fallbacks.empty')}</p>
                  <p className="mt-1 text-xs text-gray-400">{t('fallbacks.emptyHint')}</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {draft.map((fallback, index) => {
                    const key = `${fallback.provider_id}\u0000${fallback.model_id}`;
                    const definition = availableByKey.get(key);
                    const isPrimary = primary?.provider_id === fallback.provider_id
                      && primary.model_id === fallback.model_id;
                    const available = Boolean(definition) && !isPrimary;
                    return (
                      <div key={`${key}-${index}`} className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2.5">
                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-xs font-semibold text-gray-500">
                          {index + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium text-gray-800">
                              {definition?.name || fallback.model_id}
                            </span>
                            {!available && (
                              <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                                {t('fallbacks.unavailable')}
                              </span>
                            )}
                          </div>
                          <div className="truncate text-xs text-gray-400">
                            {providerNames.get(fallback.provider_id) || fallback.provider_id} / {fallback.model_id}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-0.5">
                          <button
                            type="button"
                            onClick={() => move(index, -1)}
                            disabled={index === 0 || saving}
                            title={t('fallbacks.moveUp')}
                            aria-label={t('fallbacks.moveUp')}
                            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-30"
                          >
                            <ArrowUp className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => move(index, 1)}
                            disabled={index === draft.length - 1 || saving}
                            title={t('fallbacks.moveDown')}
                            aria-label={t('fallbacks.moveDown')}
                            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-30"
                          >
                            <ArrowDown className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setDraft(previous => previous.filter((_, itemIndex) => itemIndex !== index))}
                            disabled={saving}
                            title={t('fallbacks.remove')}
                            aria-label={t('fallbacks.remove')}
                            className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-30"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {hasInvalidDraft && (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {t('fallbacks.removeInvalidHint')}
                </p>
              )}

              <button
                type="button"
                onClick={() => setAdding(value => !value)}
                disabled={saving || selectableGroups.length === 0}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
                {selectableGroups.length === 0 ? t('fallbacks.noModelsToAdd') : t('fallbacks.add')}
                {selectableGroups.length > 0 && (
                  <ChevronDown className={`h-4 w-4 transition-transform ${adding ? 'rotate-180' : ''}`} />
                )}
              </button>

              {adding && selectableGroups.length > 0 && (
                <ModelSelectionList
                  groups={selectableGroups}
                  disabled={saving}
                  onSelect={model => {
                    setDraft(previous => [...previous, {
                      provider_id: model.provider_id,
                      model_id: model.id,
                    }]);
                    setAdding(false);
                  }}
                />
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {t('fallbacks.cancel')}
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={!dirty || saving || routingDataLoading || loadFailed || hasInvalidDraft}
            className="flex items-center gap-1.5 rounded-lg bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('fallbacks.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
