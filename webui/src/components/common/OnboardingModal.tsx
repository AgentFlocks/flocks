import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ArrowRight, CheckCircle2, ExternalLink, X, XCircle } from 'lucide-react';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import { catalogAPI, defaultModelAPI } from '@/api/provider';
import {
  onboardingAPI,
  type OnboardingApplyResponse,
  type OnboardingRegion,
  type OnboardingRequest,
  type OnboardingStatusResponse,
  type OnboardingValidateResponse,
} from '@/api/onboarding';
import type { CatalogProvider } from '@/types';
import { getDefaultThreatBookRegion, THREATBOOK_REGION_CONFIG } from '@/constants/threatbook';

const MODEL_KEY_LINK = 'https://portal.agentflocks.com';

const THREATBOOK_PROVIDER_IDS = ['threatbook-cn-llm', 'threatbook-io-llm'] as const;
const THREATBOOK_FREE_PROVIDER_OPTION = 'threatbook-free';

type SectionTone = 'success' | 'warning' | 'error';
type OnboardingStep = 'model' | 'intel';
type SkipTarget = OnboardingStep | null;
type ThreatBookIntelStatus = NonNullable<OnboardingStatusResponse['threatbook_intel']>;

interface SectionStatus {
  tone: SectionTone;
  message: string;
  validation?: OnboardingValidateResponse | null;
  apply?: OnboardingApplyResponse | null;
}

interface ResolvedDefaultModel {
  providerId: string;
  modelId: string;
}

interface OnboardingModalProps {
  onClose: () => void;
}

function isThreatBookProvider(providerId: string | null | undefined): boolean {
  return THREATBOOK_PROVIDER_IDS.includes(providerId as any);
}

function providerIdForRegion(region: OnboardingRegion): string {
  return region === 'global' ? 'threatbook-io-llm' : 'threatbook-cn-llm';
}

function regionForProvider(providerId: string | null | undefined): OnboardingRegion {
  return providerId === 'threatbook-io-llm' ? 'global' : 'cn';
}

function statusStyles(tone: SectionTone) {
  if (tone === 'success') {
    return {
      wrapper: 'border-green-200 bg-green-50',
      icon: 'text-green-600',
      text: 'text-green-700',
    };
  }
  if (tone === 'warning') {
    return {
      wrapper: 'border-amber-200 bg-amber-50',
      icon: 'text-amber-600',
      text: 'text-amber-700',
    };
  }
  return {
    wrapper: 'border-red-200 bg-red-50',
    icon: 'text-red-500',
    text: 'text-red-600',
  };
}

function ValidationPanel({
  t,
  status,
  onSwitchRegion,
  visibleResourceKeys,
  compactResourceList = false,
  minimal = false,
}: {
  t: (key: string, options?: any) => string;
  status: SectionStatus | null;
  onSwitchRegion?: (() => void) | null;
  visibleResourceKeys?: string[];
  compactResourceList?: boolean;
  minimal?: boolean;
}) {
  if (!status) return null;

  const styles = statusStyles(status.tone);
  const entries = status.validation
    ? Object.entries(status.validation.resource_results).filter(([key]) => (
        !visibleResourceKeys || visibleResourceKeys.includes(key)
      ))
    : [];

  return (
    <div className={minimal ? 'space-y-2' : `rounded-xl border p-4 ${styles.wrapper}`}>
      <div className="flex items-start gap-2">
        {!minimal && (
          status.tone === 'success' ? (
            <CheckCircle2 className={`w-4 h-4 mt-0.5 flex-shrink-0 ${styles.icon}`} />
          ) : (
            <XCircle className={`w-4 h-4 mt-0.5 flex-shrink-0 ${styles.icon}`} />
          )
        )}
        <div className="min-w-0 flex-1">
          <p className={`${minimal ? 'text-[11px]' : 'text-xs'} font-medium ${styles.text}`}>{status.message}</p>

          {status.validation?.error_code === 'region_mismatch' && onSwitchRegion && (
            <button
              onClick={onSwitchRegion}
              className="mt-2 inline-flex items-center gap-1 text-xs text-amber-700 hover:underline"
            >
              {status.validation.suggested_region === 'cn'
                ? t('onboarding.bootstrap.switchToChina')
                : t('onboarding.bootstrap.switchToGlobal')}
            </button>
          )}

          {entries.length > 0 && (
            compactResourceList ? (
              <div className={`${minimal ? 'space-y-1.5' : 'mt-3 space-y-2'}`}>
                {entries.map(([key, result]) => (
                  <div
                    key={key}
                    className={minimal
                      ? 'flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]'
                      : 'border-t border-white/70 pt-2 first:border-t-0 first:pt-0'}
                  >
                    <div className={`${minimal ? 'contents' : 'flex items-center justify-between gap-2'}`}>
                      <span className="font-medium text-gray-700">
                        {t(`onboarding.bootstrap.resourceLabels.${key}`)}
                      </span>
                      <span className={`${minimal ? 'text-[11px]' : 'text-[10px]'} font-medium ${
                        result.success === true
                          ? 'text-green-600'
                          : result.success === false
                            ? 'text-red-500'
                            : 'text-gray-400'
                      }`}>
                        {result.success === true
                          ? t('onboarding.bootstrap.statusPassed')
                          : result.success === false
                            ? t('onboarding.bootstrap.statusFailed')
                            : t('onboarding.bootstrap.statusSkipped')}
                      </span>
                    </div>
                    {result.message && (
                      <p className={`${minimal ? 'text-[11px] text-gray-500' : 'mt-1 text-[10px] text-gray-500'}`}>
                        {result.message}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
                {entries.map(([key, result]) => (
                  <div key={key} className="rounded-lg bg-white/70 border border-white/80 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px] font-medium text-gray-700">
                        {t(`onboarding.bootstrap.resourceLabels.${key}`)}
                      </span>
                      <span className={`text-[10px] font-medium ${
                        result.success === true
                          ? 'text-green-600'
                          : result.success === false
                            ? 'text-red-500'
                            : 'text-gray-400'
                      }`}>
                        {result.success === true
                          ? t('onboarding.bootstrap.statusPassed')
                          : result.success === false
                            ? t('onboarding.bootstrap.statusFailed')
                            : t('onboarding.bootstrap.statusSkipped')}
                      </span>
                    </div>
                    {result.message && (
                      <p className="mt-1 text-[10px] text-gray-500">{result.message}</p>
                    )}
                  </div>
                ))}
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}

function StepPill({
  active,
  label,
}: {
  active: boolean;
  label: string;
}) {
  return (
    <div className={`text-base sm:text-lg ${
      active ? 'font-bold text-green-600' : 'font-medium text-gray-400'
    }`}>
      {label}
    </div>
  );
}

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-white px-3 py-2">
      <p className="text-[11px] text-gray-500">{label}</p>
      <p className="mt-1 break-words text-sm font-medium text-gray-900">{value}</p>
    </div>
  );
}

function RegionChooser({
  value,
  onChange,
  chinaLabel,
  globalLabel,
}: {
  value: OnboardingRegion;
  onChange: (value: OnboardingRegion) => void;
  chinaLabel: string;
  globalLabel: string;
}) {
  return (
    <div className="inline-flex rounded-xl border border-gray-200 bg-white p-1.5 shadow-sm">
      {([
        ['cn', chinaLabel],
        ['global', globalLabel],
      ] as Array<[OnboardingRegion, string]>).map(([candidate, label]) => (
        <button
          key={candidate}
          type="button"
          onClick={() => onChange(candidate)}
          className={`rounded-lg px-5 py-2 text-sm font-semibold transition-colors ${
            value === candidate
              ? 'bg-green-50 text-green-700 ring-1 ring-green-200'
              : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function SkipConfirmDialog({
  t,
  target,
  onCancel,
  onConfirm,
}: {
  t: (key: string, options?: any) => string;
  target: SkipTarget;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!target) return null;

  const isModel = target === 'model';

  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/25 px-4">
      <div className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-500" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-gray-900">
              {isModel ? t('onboarding.bootstrap.skipModelTitle') : t('onboarding.bootstrap.skipIntelTitle')}
            </h3>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              {isModel ? t('onboarding.bootstrap.skipModelDescription') : t('onboarding.bootstrap.skipIntelDescription')}
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            {t('onboarding.bootstrap.returnToConfig')}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700"
          >
            {t('onboarding.bootstrap.confirmSkip')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function OnboardingModal({ onClose }: OnboardingModalProps) {
  const { t, i18n } = useTranslation('common');
  const navigate = useNavigate();
  const defaultIntelRegion = useMemo(() => getDefaultThreatBookRegion(i18n.language), [i18n.language]);

  const [step, setStep] = useState<OnboardingStep>('model');
  const [catalog, setCatalog] = useState<CatalogProvider[]>([]);
  const [statusLoading, setStatusLoading] = useState(true);
  const [hasLLM, setHasLLM] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [startStatus, setStartStatus] = useState<SectionStatus | null>(null);
  const [skipConfirm, setSkipConfirm] = useState<SkipTarget>(null);

  const [resolvedDefaultModel, setResolvedDefaultModel] = useState<ResolvedDefaultModel | null>(null);
  const [primaryProviderId, setPrimaryProviderId] = useState<string>('threatbook-cn-llm');
  const [primaryModelId, setPrimaryModelId] = useState('');
  const [primaryApiKey, setPrimaryApiKey] = useState('');
  const [primaryBaseUrl, setPrimaryBaseUrl] = useState('');
  const [primarySaving, setPrimarySaving] = useState(false);
  const [primaryConfigured, setPrimaryConfigured] = useState(false);
  const [primaryStatus, setPrimaryStatus] = useState<SectionStatus | null>(null);
  const [primaryEditing, setPrimaryEditing] = useState(false);
  const [modelRegion, setModelRegion] = useState<OnboardingRegion>('cn');
  const [modelSkipped, setModelSkipped] = useState(false);

  const [intelRegion, setIntelRegion] = useState<OnboardingRegion>(() => getDefaultThreatBookRegion(i18n.language));
  const [intelApiKey, setIntelApiKey] = useState('');
  const [intelSaving, setIntelSaving] = useState(false);
  const [intelConfigured, setIntelConfigured] = useState(false);
  const [intelStatus, setIntelStatus] = useState<SectionStatus | null>(null);
  const [intelEditing, setIntelEditing] = useState(false);
  const [intelSkipped, setIntelSkipped] = useState(false);
  const [intelRuntimeStatus, setIntelRuntimeStatus] = useState<ThreatBookIntelStatus | null>(null);

  const refreshOnboardingStatus = useCallback(async (silent = false) => {
    if (!silent) setStatusLoading(true);
    try {
      const res = await onboardingAPI.getStatus();
      const defaultModel = res.data.default_model;
      setHasLLM(res.data.completed);
      setPrimaryConfigured(Boolean(res.data.has_default_model));
      setResolvedDefaultModel(defaultModel
        ? {
            providerId: defaultModel.provider_id,
            modelId: defaultModel.model_id,
          }
        : null);
      if (defaultModel) {
        setPrimaryProviderId(defaultModel.provider_id);
        setPrimaryModelId(defaultModel.model_id);
        setModelRegion(regionForProvider(defaultModel.provider_id));
        setPrimaryEditing(false);
      }
      if (res.data.threatbook_intel) {
        const intel = res.data.threatbook_intel;
        setIntelRuntimeStatus(intel);
        setIntelConfigured(intel.configured);
        setIntelRegion(intel.region || defaultIntelRegion);
        if (intel.configured) setIntelEditing(false);
      }
    } catch {
      try {
        const resolved = await defaultModelAPI.getResolved();
        setHasLLM(true);
        setPrimaryConfigured(true);
        setResolvedDefaultModel({
          providerId: resolved.data.provider_id,
          modelId: resolved.data.model_id,
        });
        setPrimaryProviderId(resolved.data.provider_id);
        setPrimaryModelId(resolved.data.model_id);
        setModelRegion(regionForProvider(resolved.data.provider_id));
        setPrimaryEditing(false);
      } catch {
        setHasLLM(false);
        setPrimaryConfigured(false);
        setResolvedDefaultModel(null);
      }
    } finally {
      if (!silent) setStatusLoading(false);
    }
  }, [defaultIntelRegion]);

  useEffect(() => {
    refreshOnboardingStatus();
    catalogAPI.list()
      .then((res) => {
        setCatalog(res.data.providers || []);
      })
      .catch(() => {
        setCatalog([]);
      });
  }, [refreshOnboardingStatus]);

  const primaryProviders = useMemo(() => {
    return catalog.filter((provider) =>
      isThreatBookProvider(provider.id)
      || provider.id === 'openai-compatible'
      || provider.id === resolvedDefaultModel?.providerId
      || provider.models.length > 0
    );
  }, [catalog, resolvedDefaultModel?.providerId]);

  const providerOptions = useMemo(() => {
    const hasThreatBook = primaryProviders.some((provider) => isThreatBookProvider(provider.id))
      || isThreatBookProvider(primaryProviderId);
    const nonThreatBook = primaryProviders
      .filter((provider) => !isThreatBookProvider(provider.id))
      .sort((a, b) => a.name.localeCompare(b.name));

    return {
      hasThreatBook,
      nonThreatBook,
    };
  }, [primaryProviderId, primaryProviders]);

  const primaryProviderIsThreatBook = useMemo(
    () => isThreatBookProvider(primaryProviderId),
    [primaryProviderId]
  );

  const selectedPrimaryProvider = useMemo(
    () => primaryProviders.find((provider) => provider.id === primaryProviderId) || null,
    [primaryProviderId, primaryProviders]
  );

  const selectedPrimaryModel = useMemo(
    () => selectedPrimaryProvider?.models.find((model) => model.id === primaryModelId) || null,
    [primaryModelId, selectedPrimaryProvider]
  );

  const primaryResolvedProviderId = resolvedDefaultModel?.providerId || primaryProviderId;
  const primaryResolvedModelId = resolvedDefaultModel?.modelId || primaryModelId;
  const primaryResolvedProvider = useMemo(
    () => primaryProviders.find((provider) => provider.id === primaryResolvedProviderId) || null,
    [primaryProviders, primaryResolvedProviderId]
  );
  const primaryResolvedModel = useMemo(
    () => primaryResolvedProvider?.models.find((model) => model.id === primaryResolvedModelId) || null,
    [primaryResolvedModelId, primaryResolvedProvider]
  );
  const primaryResolvedProviderIsThreatBook = useMemo(
    () => isThreatBookProvider(primaryResolvedProviderId),
    [primaryResolvedProviderId]
  );

  const needsPrimaryBaseUrl = useMemo(() => {
    if (!selectedPrimaryProvider || primaryProviderIsThreatBook) return false;
    return selectedPrimaryProvider.credential_schemas.some((schema) =>
      schema.fields.some((field) => field.name === 'base_url')
    );
  }, [primaryProviderIsThreatBook, selectedPrimaryProvider]);

  const primaryApiPlaceholder = useMemo(() => {
    if (primaryProviderIsThreatBook) return t('onboarding.bootstrap.modelKeyPlaceholder');
    return (
      selectedPrimaryProvider?.credential_schemas?.[0]?.fields?.find((field) => field.name === 'api_key')?.placeholder
      || t('onboarding.bootstrap.thirdPartyKeyPlaceholder')
    );
  }, [primaryProviderIsThreatBook, selectedPrimaryProvider, t]);

  useEffect(() => {
    if (!primaryProviderId && providerOptions.hasThreatBook) {
      setPrimaryProviderId(providerIdForRegion(modelRegion));
    }
  }, [modelRegion, primaryProviderId, providerOptions.hasThreatBook]);

  useEffect(() => {
    if (!selectedPrimaryProvider) {
      if (!primaryConfigured) setPrimaryModelId('');
      return;
    }
    const hasModels = selectedPrimaryProvider.models.length > 0;
    if (hasModels) {
      const firstModelId = selectedPrimaryProvider.models[0]?.id || '';
      if (!selectedPrimaryProvider.models.some((model) => model.id === primaryModelId)) {
        setPrimaryModelId(firstModelId);
      }
    }
    if (primaryProviderIsThreatBook) {
      setPrimaryBaseUrl('');
    } else if (needsPrimaryBaseUrl && !primaryBaseUrl && selectedPrimaryProvider.default_base_url) {
      setPrimaryBaseUrl(selectedPrimaryProvider.default_base_url);
    }
  }, [
    needsPrimaryBaseUrl,
    primaryBaseUrl,
    primaryConfigured,
    primaryModelId,
    primaryProviderIsThreatBook,
    selectedPrimaryProvider,
  ]);

  const getProviderLabel = (provider: CatalogProvider | null, providerId?: string) => {
    const id = provider?.id || providerId || '';
    if (isThreatBookProvider(id)) return t('onboarding.bootstrap.providerThreatBookFree');
    return provider?.name || id;
  };

  const primaryConfiguredSummary = useMemo(() => {
    if (!primaryConfigured || !primaryResolvedProviderId) return '';
    const providerLabel = getProviderLabel(primaryResolvedProvider, primaryResolvedProviderId);
    const modelLabel = primaryResolvedModel?.name || primaryResolvedModelId;
    if (!providerLabel || !modelLabel) return '';
    return t('onboarding.bootstrap.primaryConfiguredSummary', {
      provider: providerLabel,
      model: modelLabel,
    });
  }, [
    primaryConfigured,
    primaryResolvedModel,
    primaryResolvedModelId,
    primaryResolvedProvider,
    primaryResolvedProviderId,
    t,
  ]);

  const primaryConfiguredStatusValue = useMemo(() => {
    if (!primaryConfigured) return t('onboarding.bootstrap.statusNotConfigured');
    return hasLLM
      ? t('onboarding.bootstrap.statusVerified')
      : t('onboarding.bootstrap.statusSavedNeedsVerify');
  }, [hasLLM, primaryConfigured, t]);

  const intelMcpStatusValue = useMemo(() => {
    if (!intelRuntimeStatus || !intelRuntimeStatus.mcp_configured) return t('onboarding.bootstrap.statusNotConfigured');
    if (intelRuntimeStatus.mcp_connected) return t('onboarding.bootstrap.statusConnected');
    if (intelRuntimeStatus.mcp_status === 'error') return t('onboarding.bootstrap.statusError');
    if (intelRuntimeStatus.mcp_status === 'disabled') return t('onboarding.bootstrap.statusDisabled');
    return t('onboarding.bootstrap.statusConfigured');
  }, [intelRuntimeStatus, t]);

  const intelRegionLabel = useMemo(() => (
    intelRegion === 'cn'
      ? t('onboarding.bootstrap.intelRegionChina')
      : t('onboarding.bootstrap.intelRegionGlobal')
  ), [intelRegion, t]);

  const intelConfiguredStatusValue = useMemo(() => {
    if (!intelConfigured) return t('onboarding.bootstrap.statusNotConfigured');
    return t('onboarding.bootstrap.intelConfiguredVerified', { region: intelRegionLabel });
  }, [intelConfigured, intelRegionLabel, t]);

  const currentIntelCapabilities = useMemo(() => {
    const matrix = intelRuntimeStatus?.service_matrix || { cn: ['api', 'mcp'], global: ['api', 'mcp'] };
    const configuredCapabilities = new Set(matrix[intelRegion] || []);
    configuredCapabilities.add('api');
    configuredCapabilities.add('mcp');
    return ['api', 'mcp'].filter((capability) => configuredCapabilities.has(capability));
  }, [intelRegion, intelRuntimeStatus?.service_matrix]);

  const canSavePrimary = primaryProviderIsThreatBook
    ? Boolean(primaryApiKey.trim())
    : Boolean(primaryProviderId && primaryModelId && primaryApiKey.trim());
  const canSaveIntel = Boolean(intelApiKey.trim());
  const showPrimaryConfiguredDetails = primaryConfigured && !primaryEditing;
  const showIntelConfiguredDetails = intelConfigured && !intelEditing;

  const buildPrimaryPayload = (): OnboardingRequest => {
    if (primaryProviderIsThreatBook) {
      return {
        region: modelRegion,
        use_threatbook_model: true,
        threatbook_api_key: primaryApiKey.trim() || null,
        threatbook_model_only: true,
      };
    }

    return {
      region: modelRegion,
      use_threatbook_model: false,
      threatbook_api_key: null,
      third_party_llm: {
        provider_id: primaryProviderId,
        api_key: primaryApiKey.trim(),
        model_id: primaryModelId,
        base_url: primaryBaseUrl.trim() || undefined,
        provider_name: selectedPrimaryProvider?.name,
      },
    };
  };

  const buildIntelPayload = (): OnboardingRequest => ({
    region: intelRegion,
    use_threatbook_model: false,
    threatbook_api_key: intelApiKey.trim() || null,
    threatbook_services_only: true,
  });

  const buildSuccessStatus = (
    validation: OnboardingValidateResponse,
    apply: OnboardingApplyResponse,
    message: string,
  ): SectionStatus => ({
    tone: 'success',
    message,
    validation,
    apply,
  });

  const buildErrorStatus = (
    validation: OnboardingValidateResponse,
    fallback: string,
  ): SectionStatus => ({
    tone: validation.error_code === 'region_mismatch' ? 'warning' : 'error',
    message: validation.message || fallback,
    validation,
  });

  const handlePrimaryProviderChange = (providerId: string) => {
    if (providerId === THREATBOOK_FREE_PROVIDER_OPTION) {
      setPrimaryProviderId(providerIdForRegion(modelRegion));
    } else {
      setPrimaryProviderId(providerId);
    }
    setPrimaryApiKey('');
    setPrimaryBaseUrl('');
    setPrimaryStatus(null);
    setPrimaryConfigured(false);
    setPrimaryEditing(true);
    setModelSkipped(false);
  };

  const handleSavePrimary = async () => {
    if (!canSavePrimary) return;

    setPrimarySaving(true);
    setPrimaryStatus(null);
    setStartStatus(null);

    try {
      let payload = buildPrimaryPayload();
      let validateRes = await onboardingAPI.validate(payload);
      let validateData = validateRes.data;

      if (
        primaryProviderIsThreatBook
        && validateData.error_code === 'region_mismatch'
        && validateData.suggested_region
      ) {
        payload = {
          ...payload,
          region: validateData.suggested_region,
        };
        validateRes = await onboardingAPI.validate(payload);
        validateData = validateRes.data;
      }

      if (!validateData.can_apply) {
        setPrimaryStatus(buildErrorStatus(validateData, t('onboarding.bootstrap.testFailed')));
        return;
      }

      const applyRes = await onboardingAPI.apply(payload);
      const applyData = applyRes.data;
      const savedProviderId = applyData.default_model?.provider_id
        || (primaryProviderIsThreatBook ? providerIdForRegion(payload.region) : primaryProviderId);
      const savedModelId = applyData.default_model?.model_id
        || primaryModelId
        || selectedPrimaryProvider?.models[0]?.id
        || '';

      setResolvedDefaultModel({
        providerId: savedProviderId,
        modelId: savedModelId,
      });
      setPrimaryProviderId(savedProviderId);
      setPrimaryModelId(savedModelId);
      setModelRegion(regionForProvider(savedProviderId));
      setPrimaryConfigured(true);
      setPrimaryEditing(false);
      setModelSkipped(false);
      setHasLLM(true);

      const successMessage = primaryProviderIsThreatBook
        ? t('onboarding.bootstrap.primaryThreatBookSuccess')
        : t('onboarding.bootstrap.primaryThirdPartySuccess', {
            provider: getProviderLabel(selectedPrimaryProvider, primaryProviderId),
            model: selectedPrimaryModel?.name || primaryModelId,
          });

      setPrimaryStatus(buildSuccessStatus(validateData, applyData, successMessage));
      refreshOnboardingStatus(true);
    } catch (err: any) {
      setPrimaryStatus({
        tone: 'error',
        message: err?.response?.data?.message || err?.response?.data?.detail || err?.message || t('onboarding.bootstrap.saveError'),
      });
    } finally {
      setPrimarySaving(false);
    }
  };

  const handleSaveIntel = async () => {
    if (!canSaveIntel) return;

    setIntelSaving(true);
    setIntelStatus(null);
    setStartStatus(null);

    try {
      let payload = buildIntelPayload();
      let validateRes = await onboardingAPI.validate(payload);
      let validateData = validateRes.data;

      if (validateData.error_code === 'region_mismatch' && validateData.suggested_region) {
        payload = {
          ...payload,
          region: validateData.suggested_region,
        };
        validateRes = await onboardingAPI.validate(payload);
        validateData = validateRes.data;
      }

      if (!validateData.can_apply) {
        setIntelStatus(buildErrorStatus(validateData, t('onboarding.bootstrap.serviceTestFailed')));
        return;
      }

      const applyRes = await onboardingAPI.apply(payload);
      const applyData = applyRes.data;
      setIntelRegion(payload.region);
      setIntelConfigured(true);
      setIntelEditing(false);
      setIntelSkipped(false);
      setIntelStatus(buildSuccessStatus(
        validateData,
        applyData,
        payload.region === 'cn'
          ? t('onboarding.bootstrap.intelChinaSuccess')
          : t('onboarding.bootstrap.intelGlobalSuccess'),
      ));
      refreshOnboardingStatus(true);
    } catch (err: any) {
      setIntelStatus({
        tone: 'error',
        message: err?.response?.data?.message || err?.response?.data?.detail || err?.message || t('onboarding.bootstrap.saveError'),
      });
    } finally {
      setIntelSaving(false);
    }
  };

  const handleStart = async () => {
    setStarting(true);
    setStartStatus(null);
    try {
      const session = await sessionApi.create({ title: t('onboarding.sessionTitle') });
      const initialMessage = t('onboarding.initialMessage');
      client.post(`/api/session/${session.id}/prompt_async`, {
        parts: [{ type: 'text', text: initialMessage }],
      }).catch(() => {});
      setStarting(false);
      onClose();
      navigate(`/sessions?session=${session.id}`);
    } catch (err: any) {
      setStartStatus({
        tone: 'error',
        message: err?.message || t('onboarding.bootstrap.startError'),
      });
      setStarting(false);
    }
  };

  const handleConfirmSkip = () => {
    if (skipConfirm === 'model') {
      setModelSkipped(true);
      setStep('intel');
      setSkipConfirm(null);
      return;
    }
    if (skipConfirm === 'intel') {
      setIntelSkipped(true);
      setSkipConfirm(null);
      handleStart();
    }
  };

  const renderModelStep = () => (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-gray-900">{t('onboarding.bootstrap.modelPageTitle')}</h3>
        <p className="mt-2 text-sm leading-6 text-gray-600">{t('onboarding.bootstrap.modelPageDescription')}</p>
      </div>

      {statusLoading && (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50/70 px-4 py-3">
          <p className="text-xs text-gray-500">{t('status.loading')}</p>
        </div>
      )}

      {!statusLoading && showPrimaryConfiguredDetails && (
        <div className="rounded-xl border border-green-200 bg-green-50/50 p-4 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-gray-800">{t('onboarding.bootstrap.configuredDetailsTitle')}</p>
              <p className="mt-1 text-[11px] text-gray-600">{primaryConfiguredSummary}</p>
            </div>
            <button
              type="button"
              onClick={() => {
                setPrimaryEditing(true);
                setPrimaryStatus(null);
              }}
              className="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              {t('onboarding.bootstrap.editPrimary')}
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatusMetric label={t('onboarding.bootstrap.configuredStatusLabel')} value={primaryConfiguredStatusValue} />
            <StatusMetric
              label={t('onboarding.bootstrap.configuredProviderLabel')}
              value={getProviderLabel(primaryResolvedProvider, primaryResolvedProviderId)}
            />
            <StatusMetric
              label={t('onboarding.bootstrap.configuredModelLabel')}
              value={primaryResolvedModel?.name || primaryResolvedModelId}
            />
          </div>

          <ValidationPanel
            t={t}
            status={primaryStatus}
            visibleResourceKeys={primaryResolvedProviderIsThreatBook ? ['threatbook_llm'] : ['third_party_llm']}
          />
        </div>
      )}

      {!statusLoading && !showPrimaryConfiguredDetails && (
        <div className="rounded-xl border border-gray-200 p-4 space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-gray-600">
              {t('onboarding.bootstrap.providerLabel')}
            </label>
            <select
              value={primaryProviderIsThreatBook ? THREATBOOK_FREE_PROVIDER_OPTION : primaryProviderId}
              onChange={(event) => handlePrimaryProviderChange(event.target.value)}
              className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
            >
              {providerOptions.hasThreatBook && (
                <option value={THREATBOOK_FREE_PROVIDER_OPTION}>
                  {t('onboarding.bootstrap.providerThreatBookFree')}
                </option>
              )}
              {providerOptions.nonThreatBook.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name}
                </option>
              ))}
            </select>
          </div>

          {!primaryProviderIsThreatBook && (selectedPrimaryProvider?.models?.length ? (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-gray-600">
                {t('onboarding.bootstrap.modelLabel')}
              </label>
              <select
                value={primaryModelId}
                onChange={(event) => {
                  setPrimaryModelId(event.target.value);
                  setPrimaryStatus(null);
                }}
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
              >
                {selectedPrimaryProvider.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <input
              type="text"
              value={primaryModelId}
              onChange={(event) => {
                setPrimaryModelId(event.target.value);
                setPrimaryStatus(null);
              }}
              placeholder={t('onboarding.bootstrap.thirdPartyModelIdPlaceholder')}
              className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all placeholder-gray-300 focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
            />
          ))}

          {needsPrimaryBaseUrl && (
            <input
              type="text"
              value={primaryBaseUrl}
              onChange={(event) => {
                setPrimaryBaseUrl(event.target.value);
                setPrimaryStatus(null);
              }}
              placeholder={t('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder')}
              className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all placeholder-gray-300 focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
            />
          )}

          <div className="flex flex-col gap-2 sm:flex-row">
            {primaryConfigured && (
              <button
                type="button"
                onClick={() => {
                  if (resolvedDefaultModel) {
                    setPrimaryProviderId(resolvedDefaultModel.providerId);
                    setPrimaryModelId(resolvedDefaultModel.modelId);
                    setModelRegion(regionForProvider(resolvedDefaultModel.providerId));
                  }
                  setPrimaryApiKey('');
                  setPrimaryBaseUrl('');
                  setPrimaryStatus(null);
                  setPrimaryEditing(false);
                }}
                className="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                {t('onboarding.bootstrap.backToConfiguredDetails')}
              </button>
            )}
            <input
              type="password"
              value={primaryApiKey}
              onChange={(event) => {
                setPrimaryApiKey(event.target.value);
                setPrimaryStatus(null);
              }}
              placeholder={primaryApiPlaceholder}
              className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all placeholder-gray-300 focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
            />
            {primaryProviderIsThreatBook && (
              <a
                href={MODEL_KEY_LINK}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-700 transition-colors hover:border-amber-400 hover:bg-amber-50"
              >
                <ExternalLink className="h-3 w-3" />
                {t('onboarding.bootstrap.modelKeyLink')}
              </a>
            )}
            <button
              type="button"
              onClick={handleSavePrimary}
              disabled={!canSavePrimary || primarySaving}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {primarySaving && (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              )}
              {primarySaving ? t('onboarding.bootstrap.testing') : t('onboarding.bootstrap.savePrimary')}
            </button>
          </div>

          <ValidationPanel
            t={t}
            status={primaryStatus}
            visibleResourceKeys={primaryProviderIsThreatBook ? ['threatbook_llm'] : ['third_party_llm']}
          />
        </div>
      )}
    </div>
  );

  const renderIntelStep = () => (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-gray-900">{t('onboarding.bootstrap.intelPageTitle')}</h3>
        <p className="mt-2 text-sm leading-6 text-gray-600">{t('onboarding.bootstrap.intelPageDescription')}</p>
      </div>

      {showIntelConfiguredDetails && (
        <div className="rounded-xl border border-green-200 bg-green-50/50 p-4 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-gray-800">{t('onboarding.bootstrap.configuredDetailsTitle')}</p>
              <p className="mt-1 text-[11px] text-gray-600">{t('onboarding.bootstrap.intelConfiguredHint')}</p>
            </div>
            <button
              type="button"
              onClick={() => {
                setIntelEditing(true);
                setIntelStatus(null);
              }}
              className="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
            >
              {t('onboarding.bootstrap.editIntel')}
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatusMetric
              label={t('onboarding.bootstrap.configuredStatusLabel')}
              value={intelConfiguredStatusValue}
            />
            <StatusMetric
              label={t('onboarding.bootstrap.configuredRegionLabel')}
              value={intelRegionLabel}
            />
            <StatusMetric
              label={t('onboarding.bootstrap.intelApiLabel')}
              value={intelRuntimeStatus?.api_configured
                ? t('onboarding.bootstrap.statusConfigured')
                : t('onboarding.bootstrap.statusNotConfigured')}
            />
            <StatusMetric
              label={t('onboarding.bootstrap.intelMcpLabel')}
              value={intelMcpStatusValue}
            />
          </div>

          <ValidationPanel
            t={t}
            status={intelStatus}
            visibleResourceKeys={['threatbook_api', 'threatbook_mcp']}
            compactResourceList
            minimal
          />
        </div>
      )}

      {!showIntelConfiguredDetails && (
        <div className="rounded-xl border border-gray-200 p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-gray-800">{t('onboarding.bootstrap.intelRegionTitle')}</p>
              <p className="mt-1 text-xs text-gray-500">{t('onboarding.bootstrap.intelRegionHint')}</p>
            </div>
            <RegionChooser
              value={intelRegion}
              onChange={(region) => {
                setIntelRegion(region);
                setIntelStatus(null);
                setIntelConfigured(false);
                setIntelSkipped(false);
              }}
              chinaLabel={t('onboarding.bootstrap.intelRegionChina')}
              globalLabel={t('onboarding.bootstrap.intelRegionGlobal')}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            {currentIntelCapabilities.includes('api') && (
              <span className="rounded-full bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 ring-1 ring-green-100">
                {t('onboarding.bootstrap.intelApiCapability')}
              </span>
            )}
            {currentIntelCapabilities.includes('mcp') && (
              <span className="rounded-full bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 ring-1 ring-green-100">
                {t('onboarding.bootstrap.intelMcpCapability')}
              </span>
            )}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            {intelConfigured && (
              <button
                type="button"
                onClick={() => {
                  setIntelApiKey('');
                  setIntelStatus(null);
                  setIntelEditing(false);
                }}
                className="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
              >
                {t('onboarding.bootstrap.backToConfiguredDetails')}
              </button>
            )}
            <input
              type="password"
              value={intelApiKey}
              onChange={(event) => {
                setIntelApiKey(event.target.value);
                setIntelStatus(null);
              }}
              placeholder={t('onboarding.bootstrap.intelKeyPlaceholder')}
              className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs transition-all placeholder-gray-300 focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/50"
            />
            <a
              href={THREATBOOK_REGION_CONFIG[intelRegion].activationUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-700 transition-colors hover:border-amber-400 hover:bg-amber-50"
            >
              <ExternalLink className="h-3 w-3" />
              {t('onboarding.bootstrap.intelKeyLink')}
            </a>
            <button
              type="button"
              onClick={handleSaveIntel}
              disabled={!canSaveIntel || intelSaving}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {intelSaving && (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              )}
              {intelSaving ? t('onboarding.bootstrap.testing') : t('onboarding.bootstrap.saveIntel')}
            </button>
          </div>

          <ValidationPanel
            t={t}
            status={intelStatus}
            visibleResourceKeys={['threatbook_api', 'threatbook_mcp']}
            compactResourceList
            onSwitchRegion={
              intelStatus?.validation?.error_code === 'region_mismatch'
                ? () => {
                    if (intelStatus.validation?.suggested_region) {
                      setIntelRegion(intelStatus.validation.suggested_region);
                      setIntelStatus(null);
                    }
                  }
                : null
            }
          />
        </div>
      )}

      <div className="rounded-xl border border-gray-200 bg-gray-50/70 p-4">
        <p className="text-xs font-semibold text-gray-700">{t('onboarding.bootstrap.summaryTitle')}</p>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <StatusMetric
            label={t('onboarding.bootstrap.summaryModel')}
            value={primaryConfigured
              ? primaryConfiguredStatusValue
              : modelSkipped
                ? t('onboarding.bootstrap.statusSkipped')
                : t('onboarding.bootstrap.statusNotConfigured')}
          />
          <StatusMetric
            label={t('onboarding.bootstrap.summaryIntel')}
            value={intelConfigured
              ? intelConfiguredStatusValue
              : intelSkipped
                ? t('onboarding.bootstrap.statusSkipped')
                : t('onboarding.bootstrap.statusNotConfigured')}
          />
          <StatusMetric
            label={t('onboarding.bootstrap.summaryNext')}
            value={t('onboarding.bootstrap.summaryNextValue')}
          />
        </div>
      </div>
    </div>
  );

  const footerPrimaryAction = step === 'model'
    ? {
        label: t('onboarding.bootstrap.nextStep'),
        disabled: statusLoading || primarySaving,
        onClick: () => setStep('intel'),
      }
    : {
        label: starting ? t('onboarding.startingButton') : t('onboarding.startButton'),
        disabled: starting || primarySaving || intelSaving,
        onClick: handleStart,
      };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
      <div className="relative mx-4 flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className="relative px-6 pb-4 pt-5">
          <div className="absolute left-0 right-0 top-0 h-1 bg-green-500" />
          <button
            onClick={onClose}
            className="absolute right-4 top-4 rounded-lg p-1 text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-500"
          >
            <X className="h-4 w-4" />
          </button>
          <h2 className="pr-8 text-lg font-bold text-gray-900">{t('onboarding.title')}</h2>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <StepPill
              active={step === 'model'}
              label={t('onboarding.bootstrap.stepModel')}
            />
            <span className="text-lg font-semibold text-gray-300 sm:text-xl">》</span>
            <StepPill
              active={step === 'intel'}
              label={t('onboarding.bootstrap.stepIntel')}
            />
            <span className="text-lg font-semibold text-gray-300 sm:text-xl">》</span>
            <StepPill active={false} label={t('onboarding.bootstrap.stepRex')} />
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">
          <div className="space-y-5">
            {step === 'model' ? renderModelStep() : renderIntelStep()}
          </div>
        </div>

        <div className="border-t border-gray-100 bg-gray-50/80 px-6 py-4">
          {startStatus && (
            <div className="mb-3">
              <ValidationPanel t={t} status={startStatus} />
            </div>
          )}

          <div className="flex items-center justify-end gap-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setSkipConfirm(step)}
                disabled={starting || primarySaving || intelSaving || statusLoading}
                className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t('onboarding.bootstrap.skipPage')}
              </button>
              {step === 'intel' && (
                <button
                  type="button"
                  onClick={() => setStep('model')}
                  disabled={starting || primarySaving || intelSaving}
                  className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {t('onboarding.bootstrap.previousStep')}
                </button>
              )}
              <button
                type="button"
                onClick={footerPrimaryAction.onClick}
                disabled={footerPrimaryAction.disabled}
                className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-red-600 to-red-600 px-5 py-2 text-sm font-medium text-white shadow-sm shadow-red-500/25 transition-all hover:from-red-700 hover:to-red-700 hover:shadow-md hover:shadow-red-500/30 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {starting && (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                )}
                {footerPrimaryAction.label}
                {!starting && <ArrowRight className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </div>

        <SkipConfirmDialog
          t={t}
          target={skipConfirm}
          onCancel={() => setSkipConfirm(null)}
          onConfirm={handleConfirmSkip}
        />
      </div>
    </div>
  );
}
