import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, CheckCircle2, ChevronDown, ChevronRight, ExternalLink, X, XCircle, Zap } from 'lucide-react';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import { catalogAPI, defaultModelAPI, providerAPI } from '@/api/provider';
import { mcpAPI } from '@/api/mcp';
import {
  onboardingAPI,
  type OnboardingApplyResponse,
  type OnboardingRegion,
  type OnboardingRequest,
  type OnboardingValidateResponse,
} from '@/api/onboarding';
import type { CatalogProvider } from '@/types';

const ONBOARDING_DISMISSED_KEY = 'flocks_onboarding_dismissed';

const TBCLOUD_LINKS = {
  cn: 'https://x.threatbook.com/flocks/activate',
  global: 'https://i.threatbook.io/flocks/activate',
};

const THREATBOOK_PROVIDER_IDS = ['threatbook-cn-llm', 'threatbook-io-llm'] as const;

type SectionTone = 'success' | 'warning' | 'error';

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

export function isOnboardingDismissed(): boolean {
  return localStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true';
}

export function dismissOnboarding(): void {
  localStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
}

export function undismissOnboarding(): void {
  localStorage.removeItem(ONBOARDING_DISMISSED_KEY);
}

interface OnboardingModalProps {
  onClose: () => void;
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

export default function OnboardingModal({ onClose }: OnboardingModalProps) {
  const { t } = useTranslation('common');
  const navigate = useNavigate();

  const [hasLLM, setHasLLM] = useState<boolean | null>(null);
  const [catalog, setCatalog] = useState<CatalogProvider[]>([]);
  const [starting, setStarting] = useState(false);
  const [startStatus, setStartStatus] = useState<SectionStatus | null>(null);
  const [dontShowAgain, setDontShowAgain] = useState(() => isOnboardingDismissed());
  const [resolvedDefaultModel, setResolvedDefaultModel] = useState<ResolvedDefaultModel | null>(null);

  const [primaryProviderId, setPrimaryProviderId] = useState<string>('threatbook-cn-llm');
  const [primaryModelId, setPrimaryModelId] = useState('');
  const [primaryApiKey, setPrimaryApiKey] = useState('');
  const [primaryBaseUrl, setPrimaryBaseUrl] = useState('');
  const [primarySaving, setPrimarySaving] = useState(false);
  const [primaryConfigured, setPrimaryConfigured] = useState(false);
  const [primaryStatus, setPrimaryStatus] = useState<SectionStatus | null>(null);
  const [primarySectionCollapsed, setPrimarySectionCollapsed] = useState(false);
  const [primaryEditing, setPrimaryEditing] = useState(false);

  const [optionalThreatBookRegion, setOptionalThreatBookRegion] = useState<OnboardingRegion>('cn');
  const [optionalThreatBookApiKey, setOptionalThreatBookApiKey] = useState('');
  const [optionalSaving, setOptionalSaving] = useState(false);
  const [optionalConfigured, setOptionalConfigured] = useState(false);
  const [optionalStatus, setOptionalStatus] = useState<SectionStatus | null>(null);
  const [optionalSectionCollapsed, setOptionalSectionCollapsed] = useState(false);
  const [optionalThreatBookLoaded, setOptionalThreatBookLoaded] = useState(false);

  useEffect(() => {
    defaultModelAPI.getResolved()
      .then((res) => {
        const resolved = {
          providerId: res.data.provider_id,
          modelId: res.data.model_id,
        };
        setResolvedDefaultModel(resolved);
        setPrimaryProviderId(resolved.providerId);
        setPrimaryModelId(resolved.modelId);
        setPrimaryConfigured(true);
        setPrimarySectionCollapsed(true);
        setPrimaryEditing(false);
        setHasLLM(true);
      })
      .catch(() => {
        setHasLLM(false);
      });

    catalogAPI.list()
      .then((res) => {
        setCatalog(res.data.providers || []);
      })
      .catch(() => {
        setCatalog([]);
      });

  }, []);

  const primaryProviders = useMemo(() => {
    const filtered = catalog.filter((provider) =>
      THREATBOOK_PROVIDER_IDS.includes(provider.id as any)
      || provider.id === 'openai-compatible'
      || provider.id === resolvedDefaultModel?.providerId
      || provider.models.length > 0
    );

    const preferredOrder = ['threatbook-cn-llm', 'threatbook-io-llm'];
    return filtered.sort((a, b) => {
      const aIndex = preferredOrder.indexOf(a.id);
      const bIndex = preferredOrder.indexOf(b.id);
      if (aIndex !== -1 || bIndex !== -1) {
        return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex);
      }
      return a.name.localeCompare(b.name);
    });
  }, [catalog, resolvedDefaultModel?.providerId]);

  const selectedPrimaryProvider = useMemo(
    () => primaryProviders.find((provider) => provider.id === primaryProviderId) || null,
    [primaryProviderId, primaryProviders]
  );

  const primaryProviderIsThreatBook = useMemo(
    () => THREATBOOK_PROVIDER_IDS.includes(primaryProviderId as any),
    [primaryProviderId]
  );

  const primaryThreatBookRegion: OnboardingRegion = primaryProviderId === 'threatbook-io-llm' ? 'global' : 'cn';

  const primaryApiPlaceholder = useMemo(() => {
    if (primaryProviderIsThreatBook) return t('onboarding.bootstrap.tbPlaceholder');
    return (
      selectedPrimaryProvider?.credential_schemas?.[0]?.fields?.find((field) => field.name === 'api_key')?.placeholder
      || t('onboarding.bootstrap.thirdPartyKeyPlaceholder')
    );
  }, [primaryProviderIsThreatBook, selectedPrimaryProvider, t]);

  const needsPrimaryBaseUrl = useMemo(() => {
    if (!selectedPrimaryProvider || primaryProviderIsThreatBook) return false;
    return selectedPrimaryProvider.credential_schemas.some((schema) =>
      schema.fields.some((field) => field.name === 'base_url')
    );
  }, [primaryProviderIsThreatBook, selectedPrimaryProvider]);

  useEffect(() => {
    if (!primaryProviderId && primaryProviders.length > 0) {
      setPrimaryProviderId(primaryProviders[0].id);
    }
  }, [primaryProviderId, primaryProviders]);

  useEffect(() => {
    if (!selectedPrimaryProvider) {
      if (!primaryConfigured) {
        setPrimaryModelId('');
      }
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
    primaryModelId,
    primaryProviderIsThreatBook,
    selectedPrimaryProvider,
  ]);

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
    () => THREATBOOK_PROVIDER_IDS.includes(primaryResolvedProviderId as any),
    [primaryResolvedProviderId]
  );

  const getProviderLabel = (provider: CatalogProvider) => {
    if (provider.id === 'threatbook-cn-llm') return t('onboarding.bootstrap.providerThreatBookCn');
    if (provider.id === 'threatbook-io-llm') return t('onboarding.bootstrap.providerThreatBookGlobal');
    return provider.name;
  };

  const primaryConfiguredSummary = useMemo(() => {
    if (!primaryConfigured || !primaryProviderId) return '';

    const providerLabel = selectedPrimaryProvider
      ? getProviderLabel(selectedPrimaryProvider)
      : primaryProviderId;
    const modelLabel = selectedPrimaryModel?.name || primaryModelId;

    if (!providerLabel || !modelLabel) return '';
    return t('onboarding.bootstrap.primaryConfiguredSummary', {
      provider: providerLabel,
      model: modelLabel,
    });
  }, [
    primaryConfigured,
    primaryModelId,
    primaryProviderId,
    selectedPrimaryModel,
    selectedPrimaryProvider,
    t,
  ]);
  const primaryConfiguredRegionLabel = useMemo(() => {
    if (!primaryResolvedProviderIsThreatBook) return '';
    return primaryResolvedProviderId === 'threatbook-io-llm'
      ? t('onboarding.bootstrap.regionGlobal')
      : t('onboarding.bootstrap.regionChina');
  }, [primaryResolvedProviderId, primaryResolvedProviderIsThreatBook, t]);
  const primaryConfiguredDetailsHint = useMemo(() => {
    if (!primaryConfigured) return '';
    if (primaryResolvedProviderId === 'threatbook-cn-llm') {
      return t('onboarding.bootstrap.configuredThreatBookCnDetails');
    }
    if (primaryResolvedProviderId === 'threatbook-io-llm') {
      return t('onboarding.bootstrap.configuredThreatBookGlobalDetails');
    }
    return t('onboarding.bootstrap.configuredThirdPartyDetails');
  }, [primaryConfigured, primaryResolvedProviderId, t]);

  const optionalConfiguredSummary = useMemo(() => {
    if (!optionalConfigured) return '';
    return optionalThreatBookRegion === 'cn'
      ? t('onboarding.bootstrap.optionalThreatBookCnSuccess')
      : t('onboarding.bootstrap.optionalThreatBookGlobalSuccess');
  }, [optionalConfigured, optionalThreatBookRegion, t]);

  const canSavePrimary = primaryProviderIsThreatBook
    ? Boolean(primaryApiKey.trim())
    : Boolean(primaryProviderId && primaryModelId && primaryApiKey.trim());

  const canSaveOptionalThreatBook = primaryConfigured && !primaryProviderIsThreatBook && Boolean(optionalThreatBookApiKey.trim());
  const canStart = hasLLM === true || primaryConfigured;
  const showOptionalThreatBookSection = !primaryProviderIsThreatBook && primaryConfigured;
  const showPrimaryConfiguredDetails = primaryConfigured && !primaryEditing;

  useEffect(() => {
    if (!showOptionalThreatBookSection || optionalThreatBookLoaded) return;

    let cancelled = false;

    Promise.allSettled([
      providerAPI.getServiceCredentials('threatbook-cn'),
      providerAPI.getServiceCredentials('threatbook-io'),
      mcpAPI.getCredentials('threatbook_mcp'),
    ]).then((results) => {
      if (cancelled) return;

      const cnServiceConfigured = results[0].status === 'fulfilled' && results[0].value.data.has_credential;
      const globalServiceConfigured = results[1].status === 'fulfilled' && results[1].value.data.has_credential;
      const cnMcpConfigured = results[2].status === 'fulfilled' && results[2].value.data.has_credential;

      if (cnServiceConfigured && cnMcpConfigured) {
        setOptionalThreatBookRegion('cn');
        setOptionalConfigured(true);
        setOptionalSectionCollapsed(true);
      } else if (globalServiceConfigured) {
        setOptionalThreatBookRegion('global');
        setOptionalConfigured(true);
        setOptionalSectionCollapsed(true);
      }

      setOptionalThreatBookLoaded(true);
    });

    return () => {
      cancelled = true;
    };
  }, [optionalThreatBookLoaded, showOptionalThreatBookSection]);

  const buildPrimaryPayload = (threatbookKey?: string): OnboardingRequest => {
    if (primaryProviderIsThreatBook) {
      return {
        region: primaryThreatBookRegion,
        use_threatbook_model: true,
        threatbook_api_key: primaryApiKey.trim() || null,
      };
    }

    return {
      region: optionalThreatBookRegion,
      use_threatbook_model: false,
      threatbook_api_key: threatbookKey?.trim() || null,
      third_party_llm: {
        provider_id: primaryProviderId,
        api_key: primaryApiKey.trim(),
        model_id: primaryModelId,
        base_url: primaryBaseUrl.trim() || undefined,
        provider_name: selectedPrimaryProvider?.name,
      },
    };
  };

  const buildOptionalThreatBookPayload = (): OnboardingRequest => ({
    region: optionalThreatBookRegion,
    use_threatbook_model: false,
    threatbook_api_key: optionalThreatBookApiKey.trim() || null,
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

  const handleToggleDismiss = (checked: boolean) => {
    setDontShowAgain(checked);
    if (checked) {
      dismissOnboarding();
    } else {
      undismissOnboarding();
    }
  };

  const handlePrimaryProviderChange = (providerId: string) => {
    setPrimaryProviderId(providerId);
    setPrimaryApiKey('');
    setPrimaryBaseUrl('');
    setPrimaryStatus(null);
    setPrimaryConfigured(false);
    setPrimarySectionCollapsed(false);
    setOptionalStatus(null);
    setOptionalConfigured(false);
    setOptionalThreatBookLoaded(false);
  };

  const handleTogglePrimarySection = () => {
    if (primarySectionCollapsed) {
      setPrimarySectionCollapsed(false);
      setPrimaryEditing(false);
      return;
    }
    setPrimarySectionCollapsed(true);
    setPrimaryEditing(false);
  };

  const handleEditPrimary = () => {
    setPrimaryEditing(true);
    setPrimaryStatus(null);
  };

  const handleBackToPrimaryDetails = () => {
    if (!primaryConfigured) return;
    if (resolvedDefaultModel) {
      setPrimaryProviderId(resolvedDefaultModel.providerId);
      setPrimaryModelId(resolvedDefaultModel.modelId);
    }
    setPrimaryApiKey('');
    setPrimaryBaseUrl('');
    setPrimaryStatus(null);
    setPrimaryEditing(false);
  };

  const handleSavePrimary = async () => {
    if (!canSavePrimary) return;

    setPrimarySaving(true);
    setPrimaryStatus(null);
    setOptionalStatus(null);
    setStartStatus(null);

    try {
      const payload = buildPrimaryPayload();
      const validateRes = await onboardingAPI.validate(payload);
      const validateData = validateRes.data;

      if (!validateData.can_apply) {
        setPrimaryStatus(buildErrorStatus(validateData, t('onboarding.bootstrap.testFailed')));
        return;
      }

      const applyRes = await onboardingAPI.apply(payload);
      const applyData = applyRes.data;
      const savedProviderId = applyData.default_model?.provider_id || primaryProviderId;
      const savedModelId = applyData.default_model?.model_id
        || primaryModelId
        || selectedPrimaryProvider?.models[0]?.id
        || '';
      setResolvedDefaultModel({
        providerId: savedProviderId,
        modelId: savedModelId,
      });
      setPrimaryModelId(savedModelId);
      setPrimaryConfigured(true);
      setPrimarySectionCollapsed(true);
      setPrimaryEditing(false);
      setHasLLM(true);

      const successMessage = primaryProviderIsThreatBook
        ? (primaryThreatBookRegion === 'cn'
            ? t('onboarding.bootstrap.primaryThreatBookCnSuccess')
            : t('onboarding.bootstrap.primaryThreatBookGlobalSuccess'))
        : t('onboarding.bootstrap.primaryThirdPartySuccess', {
            provider: getProviderLabel(selectedPrimaryProvider!),
            model: selectedPrimaryModel?.name || primaryModelId,
          });

      if (primaryProviderIsThreatBook) {
        setPrimaryStatus(buildSuccessStatus(validateData, applyData, successMessage));
      } else {
        setPrimaryStatus({
          tone: 'success',
          message: successMessage,
          apply: applyData,
        });
      }
    } catch (err: any) {
      setPrimaryStatus({
        tone: 'error',
        message: err?.response?.data?.message || err?.response?.data?.detail || err?.message || t('onboarding.bootstrap.saveError'),
      });
    } finally {
      setPrimarySaving(false);
    }
  };

  const handleSaveOptionalThreatBook = async () => {
    if (!canSaveOptionalThreatBook) return;

    setOptionalSaving(true);
    setOptionalStatus(null);
    setStartStatus(null);

    try {
      const payload = buildOptionalThreatBookPayload();
      const validateRes = await onboardingAPI.validate(payload);
      const validateData = validateRes.data;

      if (!validateData.can_apply) {
        setOptionalStatus(buildErrorStatus(validateData, t('onboarding.bootstrap.serviceTestFailed')));
        return;
      }

      const applyRes = await onboardingAPI.apply(payload);
      const applyData = applyRes.data;
      setOptionalConfigured(true);
      setOptionalSectionCollapsed(true);

      const successMessage = optionalThreatBookRegion === 'cn'
        ? t('onboarding.bootstrap.optionalThreatBookCnSuccess')
        : t('onboarding.bootstrap.optionalThreatBookGlobalSuccess');

      setOptionalStatus(buildSuccessStatus(validateData, applyData, successMessage));
    } catch (err: any) {
      setOptionalStatus({
        tone: 'error',
        message: err?.response?.data?.message || err?.response?.data?.detail || err?.message || t('onboarding.bootstrap.saveError'),
      });
    } finally {
      setOptionalSaving(false);
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

  const isLoading = hasLLM === null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] mx-4 overflow-hidden flex flex-col">
        <div className="relative px-6 pt-5 pb-4">
          <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-red-500 via-red-500 to-violet-500" />
          <button
            onClick={onClose}
            className="absolute top-4 right-4 p-1 text-gray-300 hover:text-gray-500 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
          <h2 className="text-lg font-bold text-gray-900 pr-8">{t('onboarding.title')}</h2>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-4">
          <div className="space-y-4">
          <div className="rounded-xl border border-amber-200 bg-amber-50/60 overflow-hidden">
            <div className="px-4 py-4">
              <div className="flex items-center gap-2">
                <Zap className="w-4 h-4 text-amber-500 flex-shrink-0" />
                <span className="text-sm font-semibold text-amber-800">{t('onboarding.bootstrap.welcomeTitle')}</span>
              </div>
              <p className="mt-2 text-sm text-amber-800/90 leading-relaxed">
                {t('onboarding.bootstrap.welcomeIntro')}
              </p>
              <p className="mt-2 text-xs text-amber-700/80 leading-relaxed">
                {t('onboarding.bootstrap.welcomeHint')}
              </p>
            </div>
          </div>

          {!isLoading && hasLLM && (
            <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-lg bg-green-50 border border-green-200">
              <CheckCircle2 className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
              <span className="text-xs text-green-700">{t('onboarding.bootstrap.configured')}</span>
            </div>
          )}

          <div className="rounded-xl border border-gray-200 p-4 space-y-4">
            <button
              type="button"
              onClick={handleTogglePrimarySection}
              className="w-full flex items-center justify-between gap-3 text-left"
            >
              <div>
                <p className="text-xs font-semibold text-gray-700">{t('onboarding.bootstrap.primaryTitle')}</p>
                {primaryConfiguredSummary && (
                  <p className="mt-1 text-[11px] text-green-700">{primaryConfiguredSummary}</p>
                )}
                {t('onboarding.bootstrap.primarySubtitle') && (
                  <p className="mt-1 text-[11px] text-gray-500">{t('onboarding.bootstrap.primarySubtitle')}</p>
                )}
              </div>
              {primarySectionCollapsed ? (
                <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
              ) : (
                <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
              )}
            </button>

            {isLoading && (
              <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-3">
                <p className="text-xs text-gray-500">{t('status.loading')}</p>
              </div>
            )}

            {!isLoading && !primarySectionCollapsed && showPrimaryConfiguredDetails && (
              <div className="rounded-xl border border-green-200 bg-green-50/50 p-4 space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-gray-800">
                      {t('onboarding.bootstrap.configuredDetailsTitle')}
                    </p>
                    <p className="mt-1 text-[11px] text-gray-600">
                      {primaryConfiguredDetailsHint}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={handleEditPrimary}
                    className="inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    {t('onboarding.bootstrap.editPrimary')}
                  </button>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border border-white/80 bg-white/80 px-3 py-2">
                    <p className="text-[11px] text-gray-500">
                      {t('onboarding.bootstrap.configuredStatusLabel')}
                    </p>
                    <p className="mt-1 text-sm font-medium text-green-700">
                      {t('onboarding.bootstrap.configuredReadyValue')}
                    </p>
                  </div>
                  <div className="rounded-lg border border-white/80 bg-white/80 px-3 py-2">
                    <p className="text-[11px] text-gray-500">
                      {t('onboarding.bootstrap.configuredProviderLabel')}
                    </p>
                    <p className="mt-1 text-sm font-medium text-gray-900">
                      {primaryResolvedProvider
                        ? getProviderLabel(primaryResolvedProvider)
                        : primaryResolvedProviderId}
                    </p>
                  </div>
                  <div className="rounded-lg border border-white/80 bg-white/80 px-3 py-2">
                    <p className="text-[11px] text-gray-500">
                      {t('onboarding.bootstrap.configuredModelLabel')}
                    </p>
                    <p className="mt-1 text-sm font-medium text-gray-900">
                      {primaryResolvedModel?.name || primaryResolvedModelId}
                    </p>
                  </div>
                  {primaryConfiguredRegionLabel && (
                    <div className="rounded-lg border border-white/80 bg-white/80 px-3 py-2">
                      <p className="text-[11px] text-gray-500">
                        {t('onboarding.bootstrap.configuredRegionLabel')}
                      </p>
                      <p className="mt-1 text-sm font-medium text-gray-900">
                        {primaryConfiguredRegionLabel}
                      </p>
                    </div>
                  )}
                </div>

                <ValidationPanel
                  t={t}
                  status={primaryStatus}
                  visibleResourceKeys={primaryResolvedProviderIsThreatBook ? undefined : ['third_party_llm']}
                />
              </div>
            )}

            {!isLoading && !primarySectionCollapsed && !showPrimaryConfiguredDetails && (
              <>
                <select
                  value={primaryProviderId}
                  onChange={(e) => handlePrimaryProviderChange(e.target.value)}
                  className="w-full text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all"
                >
                  {primaryProviders.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {getProviderLabel(provider)}
                    </option>
                  ))}
                </select>

                {primaryProviderIsThreatBook && (
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-amber-700">
                    <span>
                      {primaryThreatBookRegion === 'cn'
                        ? t('onboarding.bootstrap.primaryThreatBookCnFreeHint')
                        : t('onboarding.bootstrap.primaryThreatBookGlobalFreeHint')}
                    </span>
                    <a
                      href={primaryThreatBookRegion === 'cn' ? TBCLOUD_LINKS.cn : TBCLOUD_LINKS.global}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-amber-300 bg-white text-[11px] font-medium text-amber-700 hover:bg-amber-50 hover:border-amber-400 transition-colors"
                    >
                      <ExternalLink className="w-3 h-3" />
                      {primaryThreatBookRegion === 'cn'
                        ? t('onboarding.bootstrap.primaryThreatBookCnLink')
                        : t('onboarding.bootstrap.primaryThreatBookGlobalLink')}
                    </a>
                  </div>
                )}

                {!primaryProviderIsThreatBook && (selectedPrimaryProvider?.models?.length ? (
                  <select
                    value={primaryModelId}
                    onChange={(e) => {
                      setPrimaryModelId(e.target.value);
                      setPrimaryStatus(null);
                    }}
                    className="w-full text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all"
                  >
                    {selectedPrimaryProvider.models.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={primaryModelId}
                    onChange={(e) => {
                      setPrimaryModelId(e.target.value);
                      setPrimaryStatus(null);
                    }}
                    placeholder={t('onboarding.bootstrap.thirdPartyModelIdPlaceholder')}
                    className="w-full text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all placeholder-gray-300"
                  />
                ))}

                {needsPrimaryBaseUrl && (
                  <input
                    type="text"
                    value={primaryBaseUrl}
                    onChange={(e) => {
                      setPrimaryBaseUrl(e.target.value);
                      setPrimaryStatus(null);
                    }}
                    placeholder={t('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder')}
                    className="w-full text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all placeholder-gray-300"
                  />
                )}

                <div className="flex gap-2">
                  {primaryConfigured && (
                    <button
                      type="button"
                      onClick={handleBackToPrimaryDetails}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 text-sm font-medium transition-colors hover:bg-gray-50"
                    >
                      {t('onboarding.bootstrap.backToConfiguredDetails')}
                    </button>
                  )}
                  <input
                    type="password"
                    value={primaryApiKey}
                    onChange={(e) => {
                      setPrimaryApiKey(e.target.value);
                      setPrimaryStatus(null);
                    }}
                    placeholder={primaryApiPlaceholder}
                    className="flex-1 text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all placeholder-gray-300"
                  />
                  <button
                    onClick={handleSavePrimary}
                    disabled={!canSavePrimary || primarySaving}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
                  >
                    {primarySaving && (
                      <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    )}
                    {primarySaving ? t('onboarding.bootstrap.testing') : t('onboarding.bootstrap.savePrimary')}
                  </button>
                </div>

                {primaryProviderIsThreatBook ? (
                  <p className="text-[11px] text-gray-500">
                    {primaryThreatBookRegion === 'cn'
                      ? t('onboarding.bootstrap.primaryThreatBookCnHint')
                      : t('onboarding.bootstrap.primaryThreatBookGlobalHint')}
                  </p>
                ) : (selectedPrimaryModel || primaryModelId) ? (
                  <p className="text-[11px] text-gray-500">
                    {t('onboarding.bootstrap.thirdPartyModelHint', { model: selectedPrimaryModel?.name || primaryModelId })}
                  </p>
                ) : null}

                <ValidationPanel
                  t={t}
                  status={primaryStatus}
                  visibleResourceKeys={primaryProviderIsThreatBook ? undefined : ['third_party_llm']}
                  onSwitchRegion={
                    primaryStatus?.validation?.error_code === 'region_mismatch'
                      ? () => {
                          const suggested = primaryStatus.validation?.suggested_region;
                          if (suggested === 'cn') handlePrimaryProviderChange('threatbook-cn-llm');
                          if (suggested === 'global') handlePrimaryProviderChange('threatbook-io-llm');
                        }
                      : null
                  }
                />
              </>
            )}
          </div>

          {showOptionalThreatBookSection && (
            <div className="rounded-xl border border-gray-200 p-4 space-y-4">
              <button
                type="button"
                onClick={() => setOptionalSectionCollapsed((prev) => !prev)}
                className="w-full flex items-center justify-between gap-3 text-left"
              >
                <div>
                  <p className="text-xs font-semibold text-gray-700">{t('onboarding.bootstrap.optionalThreatBookTitle')}</p>
                  {optionalConfiguredSummary && (
                    <p className="mt-1 text-[11px] text-green-700">{optionalConfiguredSummary}</p>
                  )}
                  <p className="mt-1 text-[11px] text-gray-500">{t('onboarding.bootstrap.optionalThreatBookSubtitle')}</p>
                </div>
                {optionalSectionCollapsed ? (
                  <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                )}
              </button>

              {!optionalSectionCollapsed && (
                <>
                  <div className="flex flex-wrap items-center gap-4">
                    <div className="flex flex-wrap items-center gap-4">
                      {(['cn', 'global'] as OnboardingRegion[]).map((candidate) => (
                        <label
                          key={candidate}
                          className="inline-flex items-center gap-2 cursor-pointer text-xs text-gray-700"
                        >
                          <input
                            type="radio"
                            name="optional-threatbook-region"
                            checked={optionalThreatBookRegion === candidate}
                            onChange={() => {
                              setOptionalThreatBookRegion(candidate);
                              setOptionalStatus(null);
                              setOptionalConfigured(false);
                            }}
                            className="h-3.5 w-3.5 border-gray-300 text-red-600 focus:ring-red-500"
                          />
                          <span>{candidate === 'cn' ? t('onboarding.bootstrap.regionChina') : t('onboarding.bootstrap.regionGlobal')}</span>
                        </label>
                      ))}
                    </div>

                    <a
                      href={optionalThreatBookRegion === 'cn' ? TBCLOUD_LINKS.cn : TBCLOUD_LINKS.global}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-amber-300 bg-white text-xs font-medium text-amber-700 hover:bg-amber-50 hover:border-amber-400 transition-colors"
                    >
                      <ExternalLink className="w-3 h-3" />
                      {optionalThreatBookRegion === 'cn'
                        ? t('onboarding.bootstrap.primaryThreatBookCnLink')
                        : t('onboarding.bootstrap.primaryThreatBookGlobalLink')}
                    </a>
                  </div>

                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={optionalThreatBookApiKey}
                      onChange={(e) => {
                        setOptionalThreatBookApiKey(e.target.value);
                        setOptionalStatus(null);
                        setOptionalConfigured(false);
                      }}
                      placeholder={t('onboarding.bootstrap.tbPlaceholder')}
                      className="flex-1 text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-red-400/50 focus:border-red-400 transition-all placeholder-gray-300"
                    />
                    <button
                      onClick={handleSaveOptionalThreatBook}
                      disabled={!canSaveOptionalThreatBook || optionalSaving}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
                    >
                      {optionalSaving && (
                        <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                      )}
                      {optionalSaving ? t('onboarding.bootstrap.testing') : t('onboarding.bootstrap.saveOptionalThreatBook')}
                    </button>
                  </div>

                  {!optionalConfigured && (
                    <p className="text-[11px] text-gray-500">{t('onboarding.bootstrap.tbSkipHint')}</p>
                  )}

                  <ValidationPanel
                    t={t}
                    status={optionalStatus}
                    visibleResourceKeys={['threatbook_api', 'threatbook_mcp']}
                    compactResourceList
                    minimal
                    onSwitchRegion={
                      optionalStatus?.validation?.error_code === 'region_mismatch'
                        ? () => {
                            if (optionalStatus.validation?.suggested_region) {
                              setOptionalThreatBookRegion(optionalStatus.validation.suggested_region);
                              setOptionalStatus(null);
                            }
                          }
                        : null
                    }
                  />
                </>
              )}
            </div>
          )}
          </div>
        </div>

        <div className="px-6 py-4 bg-gray-50/80 border-t border-gray-100">
          {startStatus && (
            <div className="mb-3">
              <ValidationPanel t={t} status={startStatus} />
            </div>
          )}

          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 cursor-pointer select-none group">
              <input
                type="checkbox"
                checked={dontShowAgain}
                onChange={(e) => handleToggleDismiss(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-gray-300 text-red-600 focus:ring-red-500 focus:ring-offset-0 cursor-pointer"
              />
              <span className="text-xs text-gray-400 group-hover:text-gray-500 transition-colors">
                {t('onboarding.dismissButton')}
              </span>
            </label>
            <button
              onClick={handleStart}
              disabled={!canStart || starting || isLoading || primarySaving || optionalSaving}
              title={!canStart ? t('onboarding.bootstrap.startBlockedHint') : undefined}
              className="flex items-center gap-2 px-5 py-2 bg-gradient-to-r from-red-600 to-red-600 hover:from-red-700 hover:to-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg shadow-sm shadow-red-500/25 hover:shadow-md hover:shadow-red-500/30 transition-all"
            >
              {starting ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                  {t('onboarding.startingButton')}
                </>
              ) : (
                <>
                  {t('onboarding.startButton')}
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
