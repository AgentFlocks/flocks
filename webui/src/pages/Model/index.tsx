import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Brain, Cog, TestTube, Trash2, Search,
  Zap, Eye, EyeOff, Wrench, MessageSquare,
  Link2, Link2Off, DollarSign, Cpu,
  Plus, ToggleLeft, ToggleRight,
  ChevronDown, Check, AlertCircle, Loader2,
  X, Shield, Pencil, Star, AlertTriangle,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import EntitySheet from '@/components/common/EntitySheet';
import { useProviders, type EnrichedProvider } from '@/hooks/useProviders';
import { useSSE } from '@/hooks/useSSE';
import {
  providerAPI, modelV2API, usageAPI,
  customAPI, modelSettingsAPI, catalogAPI, defaultModelAPI,
} from '@/api/provider';
import { hasPendingProviderCredentialChanges } from './providerCredentialUtils';
import type {
  ProviderCredentials, ModelDefinitionV2, UsageStats,
  CatalogProvider, CatalogModel, CatalogCredentialField, ModelSettingV2,
} from '@/types';

// ==================== Connection Cache ====================

const CONNECTION_CACHE_KEY = 'flocks_provider_connection_cache';
const CONNECTION_CACHE_TTL_CONNECTED = 60 * 60 * 1000; // connected: 1 hour
const CONNECTION_CACHE_TTL_FAILED = 5 * 60 * 1000;     // failed: 5 minutes

type CachedStatus = { status: 'connected' | 'failed'; ts: number };

function loadConnectionCache(): Record<string, CachedStatus> {
  try {
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

  // State
  const [selectedProvider, setSelectedProvider] = useState<EnrichedProvider | null>(null);
  const [providerModels, setProviderModels] = useState<ModelDefinitionV2[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [credentials, setCredentials] = useState<ProviderCredentials | null>(null);
  const [showConfigDialog, setShowConfigDialog] = useState(false);

  // Dialog states
  const [showAddProvider, setShowAddProvider] = useState(false);
  const [showAddModel, setShowAddModel] = useState(false);
  const [selectedModelForDetail, setSelectedModelForDetail] = useState<{ provider: EnrichedProvider; model: ModelDefinitionV2 } | null>(null);

  // Model enabled/disabled states (maps "provider/model" -> enabled)
  const [modelEnabledMap, setModelEnabledMap] = useState<Record<string, boolean>>({});
  // Pending provider to auto-select after providers list refreshes (e.g. after adding a new provider)
  const [pendingSelectId, setPendingSelectId] = useState<string | null>(null);

  // Connection test status per provider: 'unknown' | 'connected' | 'failed'
  const [connectionStatus, setConnectionStatus] = useState<Record<string, 'unknown' | 'connected' | 'failed'>>({});

  // Dashboard data
  const [usageStats, setUsageStats] = useState<UsageStats | null>(null);
  const [defaultModel, setDefaultModel] = useState<{ provider_id: string; model_id: string } | null>(null);
  const [showDefaultModelDialog, setShowDefaultModelDialog] = useState(false);

  // Refs for latest handler/state values (avoid stale closures in SSE & one-time effects)
  const sseRefetchTimer = useRef<number | null>(null);
  const selectedProviderRef = useRef(selectedProvider);
  selectedProviderRef.current = selectedProvider;
  const providerLoadSeqRef = useRef(0);
  const handleSelectProviderRef = useRef<(p: EnrichedProvider) => Promise<void>>(null!);

  useSSE({
    url: '/api/event',
    onEvent: useCallback((evt) => {
      if (evt.type === 'provider.updated') {
        if (sseRefetchTimer.current) clearTimeout(sseRefetchTimer.current);
        sseRefetchTimer.current = window.setTimeout(() => {
          refetch();
          if (selectedProviderRef.current) handleSelectProviderRef.current(selectedProviderRef.current);
        }, 500);
      }
    }, [refetch]),
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  // Fetch dashboard data on mount and validate default model
  useEffect(() => {
    usageAPI.getSummary().then(r => setUsageStats(r.data)).catch(() => {});

    Promise.all([
      defaultModelAPI.getResolved().catch(() => ({ data: null })),
      modelV2API.listDefinitions({ enabled_only: true }).catch(() => ({ data: { models: [] } })),
    ]).then(([defaultRes, modelsRes]) => {
      const dm = defaultRes.data;
      if (!dm) return;

      const availableModels = modelsRes.data.models || [];
      const isValid = availableModels.some(
        m => m.provider_id === dm.provider_id && m.id === dm.model_id
      );

      if (isValid) {
        setDefaultModel(dm);
      } else {
        const modelLabel = `${dm.provider_id} / ${dm.model_id}`;
        toast.warning(t('dashboard.defaultModelInvalid', { model: modelLabel }));
        defaultModelAPI.delete('llm').catch(() => {});
        setDefaultModel(null);
      }
    });
  }, []);

  // Auto-test all configured providers on initial load, with cache (connected: 1h, failed: 5min)
  const [autoTested, setAutoTested] = useState(false);
  const handleTestConnectionRef = useRef<(id: string, silent?: boolean) => Promise<void>>(null!);


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
    setSelectedProvider(provider);
    selectedProviderRef.current = provider;
    sessionStorage.setItem('model_page_selected_provider', provider.id);
    setCredentials(null);
    setProviderModels([]);
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
        providerAPI.getCredentials(provider.id),
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

      setCredentials(credentialsResult.status === 'fulfilled' ? credentialsResult.value.data : null);
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

  const handleTestConnection = async (providerId: string, silent = false) => {
    try {
      const response = await providerAPI.testCredentials(providerId);
      if (response.data.success) {
        setConnectionStatus(prev => ({ ...prev, [providerId]: 'connected' }));
        saveConnectionCache(providerId, 'connected');
        if (!silent) toast.success(t('testSuccess'), `${t('latency')}: ${response.data.latency_ms}ms`);
      } else {
        setConnectionStatus(prev => ({ ...prev, [providerId]: 'failed' }));
        saveConnectionCache(providerId, 'failed');
        if (!silent) toast.error(t('testFailed'), response.data.message);
      }
    } catch (err: any) {
      setConnectionStatus(prev => ({ ...prev, [providerId]: 'failed' }));
      saveConnectionCache(providerId, 'failed');
      if (!silent) toast.error(t('testFailed'), err.message);
    }
  };
  handleTestConnectionRef.current = handleTestConnection;

  // Auto-test effect (runs once after handlers are defined)
  useEffect(() => {
    if (!loading && !autoTested && providers.length > 0) {
      setAutoTested(true);
      const cache = loadConnectionCache();
      const configuredList = providers.filter(p => p.configured);
      configuredList.forEach(p => {
        const cached = getCachedStatus(cache, p.id);
        if (cached !== null) {
          setConnectionStatus(prev => ({ ...prev, [p.id]: cached }));
        } else {
          handleTestConnectionRef.current(p.id, true);
        }
      });
    }
  }, [loading, providers, autoTested]);

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
      setTimeout(() => {
        refetch();
        handleTestConnection(addedProviderId, true);
      }, 500);
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

  const handleModelCreated = useCallback(() => {
    if (selectedProvider) {
      handleSelectProvider(selectedProvider);
    }
    refetch();
  }, [selectedProvider, refetch]);

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

      if (selectedProvider) {
        handleSelectProvider(selectedProvider);
      }
      refetch();
    } catch (err: any) {
      toast.error(t('deleteFailed'), err.message);
    }
  };

  // ==================== Render ====================

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button onClick={refetch} className="px-4 py-2 bg-slate-800 text-white rounded-lg hover:bg-slate-900">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Brain className="w-8 h-8" />}
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowAddProvider(true)}
              className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
            >
              <Plus className="w-4 h-4" />
              {t('addProvider')}
            </button>
            {selectedProvider && (
              <button
                onClick={() => setShowAddModel(true)}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
              >
                <Plus className="w-4 h-4" />
                {t('addModel')}
              </button>
            )}
          </div>
        }
      />

      {/* Dashboard Strip */}
      <DashboardStrip
        connectedCount={connectedCount}
        totalModels={totalModels}
        usageStats={usageStats}
        defaultModel={defaultModel}
        onEditDefault={() => setShowDefaultModelDialog(true)}
      />

      {/* Main Content: Provider List + Detail Panel */}
      <div className="flex gap-3 flex-1 overflow-hidden min-h-0">
        {/* Provider List */}
        <div className="w-64 flex-shrink-0 flex flex-col min-w-0">
          <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
            {configuredProviders.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-3">
                  <Brain className="w-8 h-8 text-gray-400" />
                </div>
                <p className="text-sm font-medium text-gray-700 mb-1">{t('providerList.empty')}</p>
                <p className="text-xs text-gray-500 mb-4">{t('providerList.emptyHint')}</p>
                <button
                  onClick={() => setShowAddProvider(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-800 text-white rounded-lg hover:bg-slate-900 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  {t('providerList.addProvider')}
                </button>
              </div>
            ) : (
              configuredProviders.map((provider) => (
                <ProviderCard
                  key={provider.id}
                  provider={provider}
                  isSelected={selectedProvider?.id === provider.id}
                  connStatus={connectionStatus[provider.id] || 'unknown'}
                  onClick={() => handleSelectProvider(provider)}
                  onConfigure={async () => {
                    await handleSelectProvider(provider);
                    if (selectedProviderRef.current?.id === provider.id) {
                      setShowConfigDialog(true);
                    }
                  }}
                />
              ))
            )}
          </div>
        </div>

        {/* Detail Panel */}
        <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
          {selectedProvider ? (
            <ProviderDetail
              key={selectedProvider.id}
              provider={selectedProvider}
              models={providerModels}
              loadingModels={loadingModels}
              modelEnabledMap={modelEnabledMap}
              connStatus={connectionStatus[selectedProvider.id] || 'unknown'}
              onToggleModel={handleToggleModel}
              onDeleteModel={handleDeleteModel}
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
          provider={selectedProvider}
          existingCredentials={credentials}
          models={providerModels}
          onClose={() => setShowConfigDialog(false)}
          onConfigured={async () => {
            if (selectedProvider) {
              const res = await providerAPI.getCredentials(selectedProvider.id).catch(() => ({ data: null }));
              setCredentials(res.data);
              // Refresh model list in right panel
              handleSelectProvider(selectedProvider);
              // Auto-test after credential update
              handleTestConnection(selectedProvider.id, true);
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
          provider={selectedProvider}
          onClose={() => setShowAddModel(false)}
          onCreated={handleModelCreated}
        />
      )}

      {selectedModelForDetail && (
        <ModelDetailSheet
          provider={selectedModelForDetail.provider}
          model={selectedModelForDetail.model}
          onClose={() => setSelectedModelForDetail(null)}
          onSaved={() => {
            setSelectedModelForDetail(null);
            if (selectedProvider && selectedProvider.id === selectedModelForDetail.provider.id) {
              handleSelectProvider(selectedProvider);
            }
          }}
        />
      )}

      {showDefaultModelDialog && (
        <SetDefaultModelDialog
          current={defaultModel}
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
    </div>
  );
}

// ==================== Dashboard Strip ====================

function DashboardStrip({
  connectedCount,
  totalModels,
  usageStats,
  defaultModel,
  onEditDefault,
}: {
  connectedCount: number;
  totalModels: number;
  usageStats: UsageStats | null;
  defaultModel: { provider_id: string; model_id: string } | null;
  onEditDefault: () => void;
}) {
  const { t } = useTranslation('model');
  const todayCost = usageStats?.summary?.total_cost ?? 0;
  const currency = usageStats?.summary?.currency ?? 'USD';

  return (
    <div className="grid grid-cols-4 gap-3 mb-4">
      {/* Default Model Card */}
      <div className="rounded-lg border p-3 bg-purple-50 text-purple-700 border-purple-200">
        <div className="flex items-center justify-between mb-1 opacity-70">
          <div className="flex items-center gap-2">
            <Star className="w-5 h-5" />
            <span className="text-xs font-medium">{t('dashboard.defaultModel')}</span>
          </div>
          <button
            onClick={onEditDefault}
            className="hover:opacity-100 opacity-60 transition-opacity"
            title={t('dashboard.setDefaultModel')}
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="text-lg font-bold truncate">
          {defaultModel ? defaultModel.model_id : t('dashboard.noDefaultModel')}
        </div>
        {defaultModel && (
          <div className="text-xs opacity-60 truncate">{defaultModel.provider_id}</div>
        )}
      </div>
      <StatCard icon={<Link2 className="w-5 h-5" />} label={t('dashboard.connected')} value={String(connectedCount)} color="green" />
      <StatCard icon={<Cpu className="w-5 h-5" />} label={t('dashboard.availableModels')} value={String(totalModels)} color="blue" />
      <StatCard icon={<DollarSign className="w-5 h-5" />} label={t('dashboard.totalUsage')} value={todayCost > 0 ? `${currency === 'USD' ? '$' : '¥'}${todayCost.toFixed(4)}` : t('dashboard.noUsage')} color="amber" />
    </div>
  );
}

function StatCard({ icon, label, value, color, small }: {
  icon: React.ReactNode; label: string; value: string; color: string; small?: boolean;
}) {
  const colorMap: Record<string, string> = {
    green: 'bg-green-50 text-green-700 border-green-200',
    blue: 'bg-sky-50 text-sky-800 border-sky-200',
    purple: 'bg-purple-50 text-purple-700 border-purple-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
  };
  return (
    <div className={`rounded-lg border p-3 ${colorMap[color] || colorMap.blue}`}>
      <div className="flex items-center gap-2 mb-1 opacity-70">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <div className={`font-bold break-words ${small ? 'text-sm' : 'text-lg'}`}>{value}</div>
    </div>
  );
}

// ==================== Provider Card ====================

function ProviderCard({ provider, isSelected, connStatus, onClick, onConfigure }: {
  provider: EnrichedProvider; isSelected: boolean;
  connStatus: 'unknown' | 'connected' | 'failed';
  onClick: () => void;
  onConfigure: () => void;
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
      <button
        onClick={(e) => { e.stopPropagation(); onConfigure(); }}
        className="p-1.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100 flex-shrink-0"
        title="Configure"
      >
        <Cog className="w-3.5 h-3.5" />
      </button>
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
  modelEnabledMap, connStatus, onToggleModel, onDeleteModel,
  onOpenModelDetail, onConnectionStatusChange,
}: {
  provider: EnrichedProvider;
  models: ModelDefinitionV2[];
  loadingModels: boolean;
  modelEnabledMap: Record<string, boolean>;
  connStatus: 'unknown' | 'connected' | 'failed';
  onToggleModel: (providerId: string, modelId: string, enabled: boolean) => void;
  onDeleteModel?: (modelId: string) => void;
  onOpenModelDetail?: (model: ModelDefinitionV2) => void;
  onConnectionStatusChange?: (status: 'connected' | 'failed') => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const [modelTestStatus, setModelTestStatus] = useState<Record<string, ModelTestStatus>>({});
  const [batchTesting, setBatchTesting] = useState(false);

  const batchResultsRef = useRef<Record<string, ModelTestStatus>>({});

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
    } catch (err: any) {
      const result: ModelTestStatus = { status: 'failed', message: err.response?.data?.detail || err.message };
      setModelTestStatus(prev => ({ ...prev, [modelId]: result }));
      batchResultsRef.current[modelId] = result;
      return result;
    }
  };

  const handleBatchTest = async () => {
    const enabledModels = models.filter(m => {
      const key = `${provider.id}/${m.id}`;
      return modelEnabledMap[key] !== false;
    });
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
    toast.info(t('form.batchTestDone'), t('form.batchTestSummary', { success: succeeded.length, failed: failed.length }));

    if (onConnectionStatusChange) {
      onConnectionStatusChange(succeeded.length > 0 ? 'connected' : 'failed');
    }
  };

  const testedCount = Object.values(modelTestStatus).filter(s => s.status === 'success' || s.status === 'failed').length;
  const successCount = Object.values(modelTestStatus).filter(s => s.status === 'success').length;
  const failedCount = Object.values(modelTestStatus).filter(s => s.status === 'failed').length;

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <div className="flex items-center justify-between gap-3 mb-2">
          <h3 className="text-sm font-medium text-gray-800 shrink-0">
            {t('detail.modelList')}{!loadingModels && <span className="text-gray-400 font-normal"> ({models.length})</span>}
          </h3>
          <div className="flex items-center gap-1.5 shrink-0">
            {testedCount > 0 && (
              <span className="text-[11px] text-gray-500">
                <span className="text-green-600">{successCount} {t('detail.success')}</span>
                {failedCount > 0 && <span className="text-red-600 ml-1">{failedCount} {t('detail.failed')}</span>}
                {batchTesting && (
                  <span className="text-red-600 ml-1">
                    {t('detail.testing')} {testedCount}/{models.filter(m => modelEnabledMap[`${provider.id}/${m.id}`] !== false).length}
                  </span>
                )}
              </span>
            )}
            <button
              onClick={handleBatchTest}
              disabled={batchTesting || loadingModels || models.length === 0}
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
        ) : models.length === 0 ? (
          <EmptyState
            icon={<Brain className="w-10 h-10" />}
            title={t('detail.noModels')}
            description={provider.configured ? t('detail.addModelHint') : t('detail.configureFirst')}
          />
        ) : (
          <div className="space-y-1.5">
            {models.map((model) => {
              const key = `${provider.id}/${model.id}`;
              const enabled = modelEnabledMap[key] !== false;
              return (
                <ModelCard
                  key={model.id}
                  model={model}
                  enabled={enabled}
                  testStatus={modelTestStatus[model.id]}
                  onOpenDetail={onOpenModelDetail ? () => onOpenModelDetail(model) : undefined}
                  onTestModel={() => handleTestSingleModel(model.id)}
                  onToggle={() => onToggleModel(provider.id, model.id, !enabled)}
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

function ModelCard({ model, enabled, testStatus, onOpenDetail, onTestModel, onToggle, onDelete }: {
  model: ModelDefinitionV2;
  enabled: boolean;
  testStatus?: ModelTestStatus;
  onOpenDetail?: () => void;
  onTestModel: () => void;
  onToggle: () => void;
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
  const currencySymbol = pricing?.currency === 'CNY' ? '¥' : '$';

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
          {pricing && pricing.input > 0 && (
            <span className="text-[11px] text-gray-500 shrink-0">{currencySymbol}{pricing.input}/{pricing.output}/M</span>
          )}
          {pricing && pricing.input === 0 && pricing.output === 0 && (
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
          {enabled && (
            <button
              onClick={(e) => { e.stopPropagation(); onTestModel(); }}
              disabled={testStatus?.status === 'testing'}
              className="p-1 rounded text-gray-400 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              title={t('form.testConnection')}
            >
              {testStatus?.status === 'testing' ? <Loader2 className="w-4 h-4 animate-spin text-amber-600" /> : <TestTube className="w-4 h-4" />}
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            className={`p-1 rounded ${enabled ? 'text-green-600 hover:text-slate-600' : 'text-gray-400 hover:text-green-600'}`}
            title={enabled ? t('status.disabled') : t('status.available')}
          >
            {enabled ? <ToggleRight className="w-4 h-4" /> : <ToggleLeft className="w-4 h-4" />}
          </button>
          {onDelete && (
            <button onClick={(e) => { e.stopPropagation(); onDelete(); }} className="p-1 rounded text-gray-400 hover:text-slate-700 hover:bg-slate-100" title="Delete">
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

  // Selected provider (catalog tab)
  const [selectedCatalogId, setSelectedCatalogId] = useState<string>('');
  const [showDropdown, setShowDropdown] = useState(false);
  const [dropdownSearch, setDropdownSearch] = useState('');

  // Shared form fields
  const [displayName, setDisplayName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [description, setDescription] = useState('');
  const [providerName, setProviderName] = useState('');

  // Model selection (for catalog providers)
  const [selectedModelIds, setSelectedModelIds] = useState<Set<string>>(new Set());

  // State
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Load catalog
  useEffect(() => {
    catalogAPI.list()
      .then(res => {
        setCatalog(res.data.providers || []);
      })
      .catch(() => {
        toast.error(t('form.loadFailed'));
      })
      .finally(() => setLoadingCatalog(false));
  }, []);

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

  // Filtered dropdown items
  const filteredDropdownItems = useMemo(() => {
    if (!dropdownSearch.trim()) return availableProviders;
    const q = dropdownSearch.toLowerCase();
    return availableProviders.filter(p =>
      p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q)
    );
  }, [availableProviders, dropdownSearch]);

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
    setSelectedCatalogId(providerId);
    setShowDropdown(false);
    setDropdownSearch('');
    setTestResult(null);

    const provider = catalog.find(p => p.id === providerId);
    if (provider) {
      setDisplayName(provider.name);
      setBaseUrl(provider.default_base_url || '');
      setApiKey('');
      setDescription(provider.description || '');
      setSelectedModelIds(new Set(provider.models.map(m => m.id)));
      setProviderName('');
    }
  };

  // Toggle model selection
  const handleToggleModel = (modelId: string) => {
    setSelectedModelIds(prev => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId); else next.add(modelId);
      return next;
    });
  };

  // Select/deselect all models
  const handleToggleAllModels = () => {
    if (!selectedCatalog) return;
    if (selectedModelIds.size === selectedCatalog.models.length) {
      setSelectedModelIds(new Set());
    } else {
      setSelectedModelIds(new Set(selectedCatalog.models.map(m => m.id)));
    }
  };

  // Test credentials
  const handleTest = async () => {
    if (selectedCatalogId === 'openai-compatible') {
      toast.info('OpenAI-compatible Provider 需要先创建，创建后可在配置弹窗中测试连接');
      return;
    }
    if (!apiKey.trim() && selectedCatalogId !== 'ollama') {
      toast.warning('Please enter API Key first');
      return;
    }
    try {
      setTesting(true);
      setTestResult(null);
      await providerAPI.setCredentials(selectedCatalogId, {
        api_key: apiKey.trim(),
        base_url: baseUrl.trim() || undefined,
        provider_name: selectedCatalogId === 'openai-compatible' && providerName.trim() ? providerName.trim() : undefined,
      });
      const res = await providerAPI.testCredentials(selectedCatalogId);
      setTestResult({
        success: res.data.success,
        message: res.data.message || (res.data.success ? t('status.connected') : t('form.testFailed')),
      });
    } catch (err: any) {
      setTestResult({ success: false, message: err.response?.data?.detail || err.message });
    } finally {
      setTesting(false);
    }
  };

  // Save — catalog provider
  const handleSaveCatalog = async () => {
    if (!selectedCatalogId) { toast.warning('Please select a Provider'); return; }
    if (selectedCatalogId === 'openai-compatible' && !providerName.trim()) {
      toast.warning('Please enter Provider Name');
      return;
    }
    if (!apiKey.trim() && selectedCatalogId !== 'ollama') { toast.warning('Please enter API Key'); return; }
    try {
      setSaving(true);
      if (selectedCatalogId === 'openai-compatible') {
        const created = await customAPI.createProvider({
          name: providerName.trim(),
          base_url: baseUrl.trim(),
          api_key: apiKey.trim() || 'not-needed',
          description: description.trim() || selectedCatalog?.description || undefined,
        });
        toast.success(t('modelAdded'), providerName.trim());
        onAdded(created.data.id);
        return;
      }

      await providerAPI.setCredentials(selectedCatalogId, {
        api_key: apiKey.trim() || 'not-needed',
        base_url: baseUrl.trim() || undefined,
      });
      if (selectedCatalog) {
        const unselected = selectedCatalog.models.filter(m => !selectedModelIds.has(m.id)).map(m => m.id);
        await Promise.all(unselected.map(id => modelV2API.deleteDefinition(selectedCatalogId, id).catch(() => {})));
      }
      toast.success(t('modelAdded'), displayName);
      onAdded(selectedCatalogId);
    } catch (err: any) {
      toast.error(t('deleteFailed'), err.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = () => handleSaveCatalog();

  const canSave = !!selectedCatalogId && (selectedCatalogId !== 'openai-compatible' || !!providerName.trim());
  const canTest = !!selectedCatalogId && selectedCatalogId !== 'openai-compatible';

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
      entityType={t('form.model')}
      icon={<Brain className="w-5 h-5" />}
      rexSystemContext={rexContext}
      rexWelcomeMessage={rexWelcome}
      submitDisabled={!canSave}
      submitLoading={saving || testing}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={handleSave}
      initialTab="form"
    >
      <div className="space-y-0">
        <div className="space-y-5">
          {loadingCatalog ? (
              <div className="flex items-center justify-center py-12"><LoadingSpinner /></div>
            ) : (
              <>
                {/* Provider Selector Dropdown */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    {t('form.providerType')} <span className="text-slate-500">*</span>
                  </label>
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowDropdown(!showDropdown)}
                      className="w-full flex items-center justify-between px-3 py-2.5 border border-gray-300 rounded-lg text-left text-sm hover:border-gray-400 focus:outline-none focus:ring-2 focus:ring-slate-400 bg-white"
                    >
                      {selectedCatalog ? (
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{selectedCatalog.name}</span>
                          <span className="text-gray-400 text-xs">
                            {t('status.models', { count: selectedCatalog.model_count })}
                          </span>
                          {connectedSet.has(selectedCatalog.id) && !selectedCatalog.allow_multiple && (
                            <span className="text-amber-600 text-xs">({t('form.alreadyAdded')})</span>
                          )}
                        </div>
                      ) : (
                        <span className="text-gray-400">{t('form.selectProvider')}</span>
                      )}
                      <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${showDropdown ? 'rotate-180' : ''}`} />
                    </button>

                    {showDropdown && (
                      <div className="absolute z-20 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-lg max-h-[50vh] overflow-hidden">
                        <div className="p-2 border-b border-gray-100">
                          <div className="relative">
                            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                            <input
                              type="text"
                              value={dropdownSearch}
                              onChange={e => setDropdownSearch(e.target.value)}
                              placeholder={t('form.searchProvider')}
                              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-slate-400"
                              autoFocus
                            />
                          </div>
                        </div>
                        <div className="overflow-y-auto max-h-[calc(50vh-3rem)]">
                          {filteredDropdownItems.length === 0 ? (
                            <div className="px-3 py-4 text-center text-sm text-gray-500">{t('form.noResults')}</div>
                          ) : (
                            filteredDropdownItems.map(p => {
                              const alreadyAdded = connectedSet.has(p.id) && !p.allow_multiple;
                              return (
                                <button
                                  key={p.id}
                                  type="button"
                                  disabled={alreadyAdded}
                                  onClick={() => handleSelectCatalogProvider(p.id)}
                                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-slate-50 transition-colors ${
                                    alreadyAdded ? 'opacity-40 cursor-not-allowed' : ''
                                  } ${selectedCatalogId === p.id ? 'bg-slate-100' : ''}`}
                                >
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                      <span className="font-medium text-gray-900">{p.name}</span>
                                      {alreadyAdded && (
                                        <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[9px] rounded font-medium">
                                          {t('form.alreadyAdded')}
                                        </span>
                                      )}
                                    </div>
                                    <p className="text-xs text-gray-500 mt-0.5 truncate">{p.description}</p>
                                  </div>
                                  <span className="text-xs text-gray-400 flex-shrink-0">{t('status.models', { count: p.model_count })}</span>
                                  {selectedCatalogId === p.id && <Check className="w-4 h-4 text-slate-700 flex-shrink-0" />}
                                </button>
                              );
                            })
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Catalog provider form */}
                {selectedCatalog && (
                  <>
                    {selectedCatalogId === 'openai-compatible' && (
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Provider Name
                          <span className="text-slate-500 ml-1">*</span>
                        </label>
                        <input
                          type="text"
                          value={providerName}
                          onChange={e => setProviderName(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                          placeholder="e.g. SiliconFlow, LM Studio, My API"
                        />
                        <p className="mt-1 text-xs text-gray-500">
                          用于创建独立的 OpenAI-compatible Provider 实例，不会覆盖已有配置。
                        </p>
                      </div>
                    )}

                    <div className="col-span-2">
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Base URL
                        <span className="text-gray-400 font-normal ml-1">{t('form.baseUrlOptional')}</span>
                      </label>
                      <input
                        type="text"
                        value={baseUrl}
                        onChange={e => setBaseUrl(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                        placeholder={selectedCatalog.default_base_url || 'https://api.example.com/v1'}
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        API Key
                        {selectedCatalogId !== 'ollama' && <span className="text-slate-500"> *</span>}
                        {selectedCatalogId === 'ollama' && <span className="text-gray-400 font-normal ml-1">{t('form.ollamaNoKey')}</span>}
                      </label>
                      <div className="relative">
                        <input
                          type={showApiKey ? 'text' : 'password'}
                          value={apiKey}
                          onChange={e => setApiKey(e.target.value)}
                          className="w-full px-3 py-2 pr-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                          placeholder={credentialFields.find(f => f.name === 'api_key')?.placeholder || 'sk-...'}
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
                    </div>

                    {testResult && (
                      <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
                        testResult.success ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'
                      }`}>
                        {testResult.success ? <Check className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />}
                        <span>{testResult.message}</span>
                      </div>
                    )}

                    {selectedCatalog.models.length > 0 && (
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <label className="text-sm font-medium text-gray-700">
                            {t('form.availableModels')}
                            <span className="text-gray-400 font-normal ml-1">
                              ({selectedModelIds.size}/{selectedCatalog.models.length} {t('form.selected')})
                            </span>
                          </label>
                          <button type="button" onClick={handleToggleAllModels} className="text-xs text-slate-600 hover:text-slate-800">
                            {selectedModelIds.size === selectedCatalog.models.length ? t('form.deselectAll') : t('form.selectAll')}
                          </button>
                        </div>
                        <div className="border border-gray-200 rounded-lg max-h-64 overflow-y-auto divide-y divide-gray-100">
                          {selectedCatalog.models.map(model => (
                            <label key={model.id} className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={selectedModelIds.has(model.id)}
                                onChange={() => handleToggleModel(model.id)}
                                className="w-4 h-4 text-slate-600 rounded border-gray-300 focus:ring-slate-400"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-medium text-gray-900">{model.name}</span>
                                  <CatalogModelBadges model={model} />
                                </div>
                                <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5">
                                  <span className="font-mono">{model.id}</span>
                                  {model.limits && (
                                    <span>
                                      {model.limits.context_window >= 1000000
                                        ? `${(model.limits.context_window / 1000000).toFixed(0)}M`
                                        : `${(model.limits.context_window / 1000).toFixed(0)}K`} ctx
                                    </span>
                                  )}
                                  {model.pricing && model.pricing.input > 0 && (
                                    <span>
                                      {model.pricing.currency === 'CNY' ? '¥' : '$'}
                                      {model.pricing.input}/{model.pricing.currency === 'CNY' ? '¥' : '$'}{model.pricing.output}/M
                                    </span>
                                  )}
                                  {model.pricing && model.pricing.input === 0 && (
                                    <span className="text-green-600">{t('status.free')}</span>
                                  )}
                                </div>
                              </div>
                            </label>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="flex items-start gap-2 p-3 bg-slate-50 border border-slate-200 rounded-lg">
                      <Shield className="w-4 h-4 text-slate-600 mt-0.5 flex-shrink-0" />
                      <p className="text-xs text-slate-700">
                        {t('form.credentialNote')}
                      </p>
                    </div>
                  </>
                )}
              </>
            )}
        </div>

        {/* Test Connection Button (inside form) */}
        <div className="pt-2">
          <button
            onClick={handleTest}
            disabled={!canTest || testing || saving}
            className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm"
          >
            {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <TestTube className="w-4 h-4" />}
            {testing ? t('form.testingConnection') : t('form.testConnection')}
          </button>
        </div>
      </div>
    </EntitySheet>
  );
}

function CatalogModelBadges({ model }: { model: CatalogModel }) {
  return (
    <div className="flex items-center gap-1">
      {model.capabilities.supports_tools && (
        <span className="inline-flex items-center px-1 py-0.5 bg-slate-100 text-slate-700 text-[9px] rounded">
          <Wrench className="w-2.5 h-2.5" />
        </span>
      )}
      {model.capabilities.supports_vision && (
        <span className="inline-flex items-center px-1 py-0.5 bg-purple-100 text-purple-700 text-[9px] rounded">
          <Eye className="w-2.5 h-2.5" />
        </span>
      )}
      {model.capabilities.supports_reasoning && (
        <span className="inline-flex items-center px-1 py-0.5 bg-amber-100 text-amber-700 text-[9px] rounded">
          <Zap className="w-2.5 h-2.5" />
        </span>
      )}
    </div>
  );
}

// ==================== Add Model Dialog ====================

function AddModelDialog({ provider, onClose, onCreated }: {
  provider: EnrichedProvider; onClose: () => void; onCreated: () => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const [modelId, setModelId] = useState('');
  const [name, setName] = useState('');
  const [contextWindow, setContextWindow] = useState('128000');
  const [maxOutput, setMaxOutput] = useState('128000');
  const [supportsVision, setSupportsVision] = useState(false);
  const [supportsTools, setSupportsTools] = useState(true);
  const [supportsStreaming, setSupportsStreaming] = useState(true);
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  const [inputPrice, setInputPrice] = useState('0');
  const [outputPrice, setOutputPrice] = useState('0');
  const [currency, setCurrency] = useState('USD');
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; latency?: number } | null>(null);
  const [testing, setTesting] = useState(false);
  // Track whether a model was successfully saved (used to trigger refresh on close)
  const modelSavedRef = useRef(false);

  const handleSubmit = async () => {
    if (!modelId.trim() || !name.trim()) {
      toast.warning(t('form.fillModelId'));
      return;
    }
    try {
      setLoading(true);
      setTestResult(null);
      await modelV2API.createDefinition(provider.id, {
        model_id: modelId.trim(),
        name: name.trim(),
        context_window: parseInt(contextWindow) || 128000,
        max_output_tokens: parseInt(maxOutput) || 4096,
        supports_vision: supportsVision,
        supports_tools: supportsTools,
        supports_streaming: supportsStreaming,
        supports_reasoning: supportsReasoning,
        input_price: parseFloat(inputPrice) || 0,
        output_price: parseFloat(outputPrice) || 0,
        currency,
      });
      modelSavedRef.current = true;
      // Refresh model list immediately — don't wait for user to click "完成"
      onCreated();
      toast.success(t('modelAdded'), `${name.trim()} (${modelId.trim()})`);

      setTesting(true);
      try {
        const res = await providerAPI.testCredentials(provider.id, modelId.trim());
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

  // Close the dialog; onCreated already fired in handleSubmit so no need to call again
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
      entityType="Custom Model"
      entityName={provider.name}
      icon={<Plus className="w-5 h-5" />}
      rexSystemContext={rexContext}
      rexWelcomeMessage={rexWelcome}
      submitDisabled={loading || testing || !modelId.trim() || !name.trim()}
      submitLoading={loading || testing}
      submitLabel={testResult ? t('form.done') : t('form.addModelBtn')}
      onClose={handleClose}
      onSubmit={testResult ? handleClose : handleSubmit}
      initialTab="form"
    >
      <div className="space-y-4">
        {/* Provider — 默认当前选中的 Provider，只读展示 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Provider</label>
          <input
            type="text"
            readOnly
            value={provider.name}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg bg-gray-50 text-sm text-gray-900 cursor-default"
          />
        </div>

        {/* Model ID + Name */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('form.modelId')} <span className="text-slate-500">*</span>
            </label>
            <input
              type="text"
              value={modelId}
              onChange={e => setModelId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder="gpt-4o-custom"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('form.displayName')} <span className="text-slate-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder="GPT-4o Custom"
            />
          </div>
        </div>

        {/* Context + Output */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.contextWindow')}</label>
            <input
              type="number"
              value={contextWindow}
              onChange={e => setContextWindow(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.maxOutput')}</label>
            <input
              type="number"
              value={maxOutput}
              onChange={e => setMaxOutput(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            />
          </div>
        </div>

        {/* Capabilities */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.capabilities')}</label>
          <div className="grid grid-cols-2 gap-2">
            <ToggleField label={t('form.toolCall')} checked={supportsTools} onChange={setSupportsTools} />
            <ToggleField label={t('form.vision')} checked={supportsVision} onChange={setSupportsVision} />
            <ToggleField label={t('form.streaming')} checked={supportsStreaming} onChange={setSupportsStreaming} />
            <ToggleField label={t('form.reasoning')} checked={supportsReasoning} onChange={setSupportsReasoning} />
          </div>
        </div>

        {/* Pricing */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.pricing')}</label>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('form.input')}</label>
              <input
                type="number"
                step="0.01"
                value={inputPrice}
                onChange={e => setInputPrice(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('form.output')}</label>
              <input
                type="number"
                step="0.01"
                value={outputPrice}
                onChange={e => setOutputPrice(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('form.currency')}</label>
              <select
                value={currency}
                onChange={e => setCurrency(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              >
                <option value="USD">USD</option>
                <option value="CNY">CNY</option>
              </select>
            </div>
          </div>
        </div>

        {/* Auto-test result */}
        {(testing || testResult) && (
          <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
            testing
              ? 'bg-red-50 text-red-800 border border-red-200'
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
      </div>
    </EntitySheet>
  );
}

function ToggleField({ label, checked, onChange }: {
  label: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-3 cursor-pointer w-full min-w-0">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${checked ? 'bg-green-600' : 'bg-gray-400'}`}
      >
        <span className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow-sm transition-transform ${
          checked ? 'translate-x-5 left-0' : 'translate-x-1 left-0'
        }`} />
      </button>
      <span className="text-sm text-gray-700">{label}</span>
    </label>
  );
}

// ==================== Configure Dialog ====================

function ConfigureProviderDialog({ provider, existingCredentials, models, onClose, onConfigured, onTestResult, onDelete }: {
  provider: EnrichedProvider; existingCredentials: ProviderCredentials | null;
  models: ModelDefinitionV2[];
  onClose: () => void; onConfigured: () => void; onTestResult: (success: boolean) => void; onDelete: () => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const hasExisting = existingCredentials?.has_credential ?? false;
  const existingKey = existingCredentials?.api_key ?? '';
  const existingBaseUrl = existingCredentials?.base_url ?? '';

  const [baseUrl, setBaseUrl] = useState(existingCredentials?.base_url ?? '');
  const [apiKey, setApiKey] = useState(existingKey);
  const [providerName, setProviderName] = useState(provider.name);
  const [showApiKey, setShowApiKey] = useState(false);
  const [loading, setLoading] = useState(false);
  const isComposingRef = useRef(false);
  const [testing, setTesting] = useState(false);
  const [testModelId, setTestModelId] = useState(models.length > 0 ? models[0].id : '');
  const [testChat, setTestChat] = useState<{ question: string; answer: string; model: string; latency: number } | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // Catalog model management
  const [catalogModels, setCatalogModels] = useState<CatalogModel[]>([]);
  const [selectedModelIds, setSelectedModelIds] = useState<Set<string>>(new Set(models.map(m => m.id)));

  useEffect(() => {
    setApiKey(existingKey);
    setBaseUrl(existingBaseUrl);
    setProviderName(provider.name);
  }, [existingBaseUrl, existingKey, provider.id, provider.name]);

  useEffect(() => {
    setSelectedModelIds(new Set(models.map(m => m.id)));
    setTestModelId(prev => (
      prev && models.some(m => m.id === prev)
        ? prev
        : (models[0]?.id ?? '')
    ));
  }, [provider.id, models]);

  useEffect(() => {
    catalogAPI.list().then(res => {
      const found = res.data.providers.find(p => p.id === provider.id);
      if (found) setCatalogModels(found.models);
    }).catch(() => {});
  }, [provider.id]);

  const handleToggleCatalogModel = (modelId: string) => {
    setSelectedModelIds(prev => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId); else next.add(modelId);
      return next;
    });
  };

  const handleToggleAllCatalogModels = () => {
    if (catalogModels.length === 0) return;
    const allSelected = catalogModels.every(m => selectedModelIds.has(m.id));
    setSelectedModelIds(new Set(allSelected ? [] : catalogModels.map(m => m.id)));
  };

  const handleSubmit = async () => {
    if (!apiKey.trim() && provider.id !== 'ollama') {
      toast.warning('Please enter API Key');
      return;
    }
    try {
      setLoading(true);
      await providerAPI.setCredentials(provider.id, {
        api_key: apiKey.trim() || 'not-needed',
        base_url: baseUrl.trim() || undefined,
        provider_name: (provider.id === 'openai-compatible' || provider.id.startsWith('custom-'))
          ? (providerName.trim() || undefined)
          : undefined,
      });

      // Sync catalog model list: add newly selected, delete deselected
      if (catalogModels.length > 0) {
        const currentModelIds = new Set(models.map(m => m.id));
        const toDelete = catalogModels.filter(m => currentModelIds.has(m.id) && !selectedModelIds.has(m.id));
        const toAdd = catalogModels.filter(m => !currentModelIds.has(m.id) && selectedModelIds.has(m.id));
        await Promise.all([
          ...toDelete.map(m => modelV2API.deleteDefinition(provider.id, m.id).catch(() => {})),
          ...toAdd.map(m => modelV2API.createDefinition(provider.id, { model_id: m.id, name: m.name }).catch(() => {})),
        ]);
      }

      toast.success(t('credentialsSaved'));
      onConfigured();
    } catch (err: any) {
      toast.error(t('configFailed'), err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async () => {
    if (models.length === 0) {
      toast.warning(t('form.noAvailableModel'));
      return;
    }
    const hasPendingChanges = hasPendingProviderCredentialChanges(
      { apiKey: existingKey, baseUrl: existingBaseUrl },
      { apiKey, baseUrl },
    );

    // Persist pending credential changes before testing so the backend uses
    // the latest base URL even when the API key itself did not change.
    if (apiKey.trim() && hasPendingChanges) {
      try {
        await providerAPI.setCredentials(provider.id, {
          api_key: apiKey.trim(),
          base_url: baseUrl.trim() || undefined,
          provider_name: (provider.id === 'openai-compatible' || provider.id.startsWith('custom-'))
            ? (providerName.trim() || undefined)
            : undefined,
        });
      } catch (err: any) {
        toast.error(t('deleteFailed'), err.message);
        return;
      }
    } else if (!apiKey.trim() && !hasExisting && provider.id !== 'ollama') {
      toast.warning('Please enter API Key first');
      return;
    }
    try {
      setTesting(true);
      setTestChat(null);
      setTestError(null);
      const response = await providerAPI.testCredentials(provider.id, testModelId || undefined);
      if (response.data.success) {
        setTestChat({
          question: response.data.question || '',
          answer: response.data.answer || '',
          model: response.data.model_id || testModelId,
          latency: response.data.latency_ms || 0,
        });
        onTestResult(true);
      } else {
        setTestError(response.data.message || t('testFailed'));
        onTestResult(false);
      }
    } catch (err: any) {
      setTestError(err.response?.data?.detail || err.message);
      onTestResult(false);
    } finally {
      setTesting(false);
    }
  };

  const rexContext = `你是 AI 模型配置助手，帮助用户配置 ${provider.name} 的 API 凭证。

Provider: ${provider.name} (${provider.id})
当前状态: ${hasExisting ? '已配置凭证' : '未配置凭证'}

请帮助用户：
1. 获取正确的 API Key（告知去哪里获取）
2. 解释各个字段的含义
3. 解决配置过程中的问题`;

  const rexWelcome = `你好！我来帮你配置 **${provider.name}** 的连接凭证。

${hasExisting ? '你已有凭证配置，可以更新或测试连接。' : '请告诉我你遇到了什么问题，或者需要如何获取 API Key？'}

我可以帮你：
- 获取 ${provider.name} API Key 的方式
- 解释 Base URL 的作用
- 诊断连接问题`;

  const canSave = true;

  return (
    <EntitySheet
      open
      mode="edit"
      entityType={t('form.model')}
      entityName={provider.name}
      icon={<Brain className="w-5 h-5" />}
      rexSystemContext={rexContext}
      rexWelcomeMessage={rexWelcome}
      submitDisabled={!canSave}
      submitLoading={loading}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={handleSubmit}
    >
      <div className="space-y-5">
        {/* openai-compatible badge */}
        {(provider.id === 'openai-compatible' || provider.id.startsWith('custom-')) && (
          <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-100 border border-slate-200 rounded-md w-fit">
            <span className="text-[11px] font-medium text-slate-700">openai-compatible</span>
          </div>
        )}

        {(provider.id === 'openai-compatible' || provider.id.startsWith('custom-')) && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Provider Name
            </label>
            <input
              type="text"
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder="e.g. SiliconFlow, LM Studio, My API"
            />
          </div>
        )}

        {/* Base URL */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Base URL
            <span className="text-gray-400 font-normal ml-1">{t('form.baseUrlOptional')}</span>
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
            placeholder="https://api.example.com/v1"
          />
        </div>

        {/* API Key */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            API Key
            {provider.id !== 'ollama' && <span className="text-slate-500"> *</span>}
            {provider.id === 'ollama' && <span className="text-gray-400 font-normal ml-1">{t('form.ollamaNoKey')}</span>}
          </label>
          <div className="relative">
            <input
              type={showApiKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full px-3 py-2 pr-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder={provider.id === 'ollama' ? 'Not required, leave empty' : 'sk-...'}
              onCompositionStart={() => { isComposingRef.current = true; }}
              onCompositionEnd={() => { isComposingRef.current = false; }}
              onKeyDown={(e) => e.key === 'Enter' && !isComposingRef.current && handleSubmit()}
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
        </div>

        {/* Catalog model management */}
        {catalogModels.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium text-gray-700">
                {t('form.availableModels')}
                <span className="text-gray-400 font-normal ml-1">
                  ({selectedModelIds.size}/{catalogModels.length} {t('form.selected')})
                </span>
              </label>
              <button type="button" onClick={handleToggleAllCatalogModels} className="text-xs text-slate-600 hover:text-slate-800">
                {catalogModels.every(m => selectedModelIds.has(m.id)) ? t('form.deselectAll') : t('form.selectAll')}
              </button>
            </div>
            <div className="border border-gray-200 rounded-lg max-h-48 overflow-y-auto divide-y divide-gray-100">
              {catalogModels.map(model => (
                <label key={model.id} className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedModelIds.has(model.id)}
                    onChange={() => handleToggleCatalogModel(model.id)}
                    className="w-4 h-4 text-slate-600 rounded border-gray-300 focus:ring-slate-400"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900">{model.name}</span>
                      <CatalogModelBadges model={model} />
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-500 mt-0.5">
                      <span className="font-mono truncate">{model.id}</span>
                      {model.limits && (
                        <span>
                          {model.limits.context_window >= 1000000
                            ? `${(model.limits.context_window / 1000000).toFixed(0)}M`
                            : `${(model.limits.context_window / 1000).toFixed(0)}K`} ctx
                        </span>
                      )}
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Test connection — 与添加模型抽屉一致 */}
        <div>
          <div className="flex items-center gap-2 mb-2">
            <label className="block text-sm font-medium text-gray-700 flex-shrink-0">{t('form.testConnection2')}</label>
            {models.length > 0 ? (
              <select
                value={testModelId}
                onChange={(e) => setTestModelId(e.target.value)}
                className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-slate-400"
              >
                {models.map(m => (
                  <option key={m.id} value={m.id}>{m.name || m.id}</option>
                ))}
              </select>
            ) : (
              <span className="text-sm text-gray-400 flex-1">{t('form.noAvailableModel')}</span>
            )}
            <button
              onClick={handleTest}
              disabled={models.length === 0 || loading || testing}
              className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm flex-shrink-0"
            >
              {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <TestTube className="w-4 h-4" />}
              {testing ? t('form.testingConnection') : t('form.testConnection2')}
            </button>
          </div>

          {testChat && (
            <div className="p-3 rounded-lg border border-green-200 bg-green-50 space-y-1.5">
              <div className="flex items-start gap-2">
                <span className="text-xs font-medium text-slate-600 flex-shrink-0 mt-0.5">Q:</span>
                <p className="text-sm text-gray-700">{testChat.question}</p>
              </div>
              <div className="flex items-start gap-2">
                <span className="text-xs font-medium text-green-600 flex-shrink-0 mt-0.5">A:</span>
                <p className="text-sm text-gray-900 font-medium">{testChat.answer}</p>
              </div>
              <p className="text-xs text-gray-400">{t('form.latencyMs', { model: testChat.model, latency: testChat.latency })}</p>
            </div>
          )}
          {testError && (
            <div className="flex items-start gap-2 p-3 rounded-lg border border-red-200 bg-red-50">
              <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
              <span className="text-sm text-red-700">{testError}</span>
            </div>
          )}
        </div>

        {/* 凭证说明 — 与添加模型抽屉一致 */}
        <div className="flex items-start gap-2 p-3 bg-slate-50 border border-slate-200 rounded-lg">
          <Shield className="w-4 h-4 text-slate-600 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-slate-700">
            {t('form.credentialNote')}
          </p>
        </div>

        {/* 移除此 Provider */}
        <div className="pt-2 border-t border-gray-100">
          <button
            onClick={onDelete}
            className="flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-800 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            {t('form.removeProvider')}
          </button>
        </div>
      </div>
    </EntitySheet>
  );
}

// ==================== Model Detail Sheet ====================

const inputCls = "w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm";
const inputClsReadOnly = inputCls + " bg-gray-50 text-gray-900 cursor-default";

function ModelDetailSheet({
  provider,
  model,
  onClose,
  onSaved,
}: {
  provider: EnrichedProvider;
  model: ModelDefinitionV2;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const { t } = useTranslation('model');
  const features = model.capabilities?.features || [];
  const [name, setName] = useState(model.name);
  const [contextWindow, setContextWindow] = useState(model.limits?.context_window != null ? String(model.limits.context_window) : '128000');
  const [maxOutput, setMaxOutput] = useState(model.limits?.max_output_tokens != null ? String(model.limits.max_output_tokens) : '4096');
  const [supportsTools, setSupportsTools] = useState(features.includes('tool_call') || !!model.capabilities?.supports_tools);
  const [supportsVision, setSupportsVision] = useState(features.includes('vision') || !!model.capabilities?.supports_vision);
  const [supportsStreaming, setSupportsStreaming] = useState(!!model.capabilities?.supports_streaming);
  const [supportsReasoning, setSupportsReasoning] = useState(features.includes('reasoning') || !!model.capabilities?.supports_reasoning);
  const [inputPrice, setInputPrice] = useState(model.pricing ? String(model.pricing.input) : '0');
  const [outputPrice, setOutputPrice] = useState(model.pricing ? String(model.pricing.output) : '0');
  const [currency, setCurrency] = useState(model.pricing?.currency ?? 'USD');
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [loadingSettings, setLoadingSettings] = useState(true);

  useEffect(() => {
    modelSettingsAPI.get(provider.id, model.id).then(r => {
      setEnabled(r.data.enabled !== false);
    }).catch(() => setEnabled(true)).finally(() => setLoadingSettings(false));
  }, [provider.id, model.id]);

  const handleSave = async () => {
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
          supports_reasoning: supportsReasoning,
          input_price: parseFloat(inputPrice) || 0,
          output_price: parseFloat(outputPrice) || 0,
          currency,
        }),
        modelSettingsAPI.update(provider.id, model.id, { enabled }),
      ]);
      toast.success(t('credentialsSaved'));
      onSaved();
    } catch (e: any) {
      toast.error(e?.message || t('deleteFailed'));
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
      submitDisabled={false}
      submitLoading={loading}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={handleSave}
      hideRex
      hideTest
    >
      <div className="space-y-4">
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
                  className={inputCls}
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
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('form.maxOutput')}</label>
                <input
                  type="number"
                  value={maxOutput}
                  onChange={e => setMaxOutput(e.target.value)}
                  className={inputCls}
                />
              </div>
            </div>

            {/* 能力 — 可编辑，与添加模型同款开关（绿色/灰色醒目） */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.capabilities')}</label>
              <div className="grid grid-cols-2 gap-2">
                <ToggleField label={t('form.toolCall')} checked={supportsTools} onChange={setSupportsTools} />
                <ToggleField label={t('form.vision')} checked={supportsVision} onChange={setSupportsVision} />
                <ToggleField label={t('form.streaming')} checked={supportsStreaming} onChange={setSupportsStreaming} />
                <ToggleField label={t('form.reasoning')} checked={supportsReasoning} onChange={setSupportsReasoning} />
              </div>
            </div>

            {/* 价格 — 可编辑 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('form.pricing')}</label>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.input')}</label>
                  <input type="number" step="0.01" value={inputPrice} onChange={e => setInputPrice(e.target.value)} className={inputCls} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.output')}</label>
                  <input type="number" step="0.01" value={outputPrice} onChange={e => setOutputPrice(e.target.value)} className={inputCls} />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">{t('form.currency')}</label>
                  <select value={currency} onChange={e => setCurrency(e.target.value)} className={inputCls}>
                    <option value="USD">USD</option>
                    <option value="CNY">CNY</option>
                  </select>
                </div>
              </div>
            </div>

            {/* 启用此模型 — 可编辑 */}
            <div className="flex items-center justify-between py-2 border-t border-gray-200">
              <label className="text-sm font-medium text-gray-700">{t('form.enableModel')}</label>
              <button
                type="button"
                onClick={() => setEnabled(!enabled)}
                className="transition-colors"
                aria-checked={enabled}
              >
                {enabled ? <ToggleRight className="w-8 h-8 text-green-500" /> : <ToggleLeft className="w-8 h-8 text-gray-300" />}
              </button>
            </div>
          </>
        )}
      </div>
    </EntitySheet>
  );
}

// ==================== Set Default Model Dialog ====================

function SetDefaultModelDialog({
  current,
  onClose,
  onSaved,
  onCleared,
}: {
  current: { provider_id: string; model_id: string } | null;
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

  useEffect(() => {
    setLoading(true);
    setInvalidWarning(null);
    modelV2API.listDefinitions({ enabled_only: true }).then(r => {
      const loadedModels = r.data.models || [];
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

  // Group by provider_id
  const grouped = useMemo(() => {
    const map: Record<string, ModelDefinitionV2[]> = {};
    for (const m of models) {
      if (!map[m.provider_id]) map[m.provider_id] = [];
      map[m.provider_id].push(m);
    }
    return map;
  }, [models]);

  const handleSelect = async (providerId: string, modelId: string) => {
    setSaving(`${providerId}/${modelId}`);
    try {
      await defaultModelAPI.set('llm', providerId, modelId);
      toast.success(t('dashboard.defaultModelUpdated'));
      onSaved({ provider_id: providerId, model_id: modelId });
    } catch {
      toast.error(t('operationFailed'));
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-md max-h-[70vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">{t('dashboard.setDefaultModel')}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {invalidWarning && (
            <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
              <AlertTriangle className="mt-0.5 w-3.5 h-3.5 flex-shrink-0" />
              <span>{invalidWarning}</span>
            </div>
          )}
          {loading ? (
            <div className="flex justify-center py-10">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : Object.keys(grouped).length === 0 ? (
            <div className="py-10 text-center text-sm text-gray-500">{t('detail.noModels')}</div>
          ) : (
            Object.entries(grouped).map(([providerId, provModels]) => (
              <div key={providerId}>
                <div className="px-5 py-2 text-xs font-semibold text-gray-500 bg-gray-50 border-b border-gray-100 uppercase tracking-wide">
                  {providerId}
                </div>
                {provModels.map(m => {
                  const isActive = current?.provider_id === providerId && current?.model_id === m.id;
                  const key = `${providerId}/${m.id}`;
                  return (
                    <button
                      key={m.id}
                      onClick={() => handleSelect(providerId, m.id)}
                      disabled={saving !== null}
                      className={`w-full flex items-center justify-between px-5 py-3 text-sm text-left hover:bg-gray-50 transition-colors border-b border-gray-100 last:border-0 ${isActive ? 'text-purple-700 font-medium' : 'text-gray-700'}`}
                    >
                      <span className="truncate">{m.name || m.id}</span>
                      {saving === key ? (
                        <Loader2 className="w-4 h-4 animate-spin text-gray-400 flex-shrink-0" />
                      ) : isActive ? (
                        <Check className="w-4 h-4 text-purple-600 flex-shrink-0" />
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
