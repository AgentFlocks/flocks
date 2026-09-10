import { Suspense, lazy, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, ComponentType, ReactNode } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Check,
  ImageIcon,
  Languages,
  Moon,
  RotateCcw,
  Save,
  ShieldCheck,
  Sun,
  TextCursorInput,
  Upload,
  type LucideIcon,
} from 'lucide-react';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import { ThemeContext } from '@/contexts/ThemeContext';
import { useProductName } from '@/contexts/ProductNameContext';
import { useToast } from '@/components/common/Toast';
import { toolFailureConfigApi } from '@/api/toolFailureConfig';
import {
  isSettingsSectionId,
  useSettingsSectionGroups,
  type SettingsSectionId,
} from '@/utils/settingsSections';
import { preloadI18nNamespaces } from '@/i18nResources';

type LazySettingsModule = { default: ComponentType<any> };

function lazySettingsPage<T extends LazySettingsModule>(
  loader: () => Promise<T>,
  namespaces: readonly string[] = [],
) {
  return lazy(() => Promise.all([
    loader(),
    preloadI18nNamespaces(namespaces),
  ]).then(([module]) => module));
}

const ConfigPage = lazySettingsPage(() => import('@/pages/Config'));
const SystemLogPage = lazySettingsPage(() => import('@/pages/SystemLog'));
const FlocksproUpgradePage = lazySettingsPage(() => import('@/pages/FlocksproUpgrade'), ['flockspro']);
const AuditLogsPage = lazySettingsPage(() => import('@/pages/AuditLogs'), ['flockspro']);
const ArchivedDataPage = lazySettingsPage(() => import('./ArchivedDataPanel'), ['session']);

const SecurityConfigPage = lazySettingsPage(() => import('@/pages/SecurityConfig'), ['flockspro']);

function PreferenceRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 border-b border-zinc-200 py-6 last:border-b-0 dark:border-zinc-800 md:grid md:grid-cols-[minmax(0,1fr)_14rem] md:items-center md:gap-8">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-zinc-950 dark:text-zinc-100">{title}</h3>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
        </div>
      </div>
      <div className="flex w-full justify-start md:justify-end">
        {children}
      </div>
    </section>
  );
}

function SegmentedOption({
  active,
  children,
  icon: Icon,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  icon: LucideIcon;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex h-9 min-w-0 flex-1 items-center justify-center gap-2 rounded-md px-3 text-sm font-semibold transition-colors ${
        active
          ? 'bg-zinc-950 text-white dark:bg-zinc-100 dark:text-zinc-950'
          : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-950 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-white'
      }`}
    >
      <Icon className="h-4 w-4" />
      {children}
      {active && <Check className="h-3.5 w-3.5" />}
    </button>
  );
}

function PreferenceSwitch({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60 ${
        checked ? 'bg-zinc-950 dark:bg-zinc-100' : 'bg-zinc-300 dark:bg-zinc-700'
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow transition-transform dark:bg-zinc-950 ${
          checked ? 'translate-x-5' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

function PreferencesPanel() {
  const { t, i18n } = useTranslation('nav');
  const { theme, setTheme } = useContext(ThemeContext);
  const {
    productName,
    configuredDisplayName,
    faviconUrl,
    hasCustomFavicon,
    updateProductName,
    uploadProductFavicon,
    resetProductFavicon,
  } = useProductName();
  const { error: showToastError, success: showToastSuccess } = useToast();
  const language = i18n.language?.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
  const faviconInputRef = useRef<HTMLInputElement | null>(null);
  const [displayNameDraft, setDisplayNameDraft] = useState(configuredDisplayName ?? '');
  const [savingDisplayName, setSavingDisplayName] = useState(false);
  const [savingFavicon, setSavingFavicon] = useState(false);
  const [toolFailureAutoDisable, setToolFailureAutoDisable] = useState(true);
  const [loadingToolFailure, setLoadingToolFailure] = useState(true);
  const [savingToolFailure, setSavingToolFailure] = useState(false);
  const normalizedDisplayName = displayNameDraft.trim();
  const displayNameChanged = normalizedDisplayName !== (configuredDisplayName ?? '');
  const toolFailureSettingLoadFailedMessage = t('toolFailureSettingLoadFailed');

  useEffect(() => {
    setDisplayNameDraft(configuredDisplayName ?? '');
  }, [configuredDisplayName]);

  useEffect(() => {
    let cancelled = false;
    setLoadingToolFailure(true);
    toolFailureConfigApi.get()
      .then((config) => {
        if (!cancelled) {
          setToolFailureAutoDisable(config.disableOnRepeatedFailure);
        }
      })
      .catch((err: any) => {
        if (!cancelled) {
          showToastError(
            toolFailureSettingLoadFailedMessage,
            err?.response?.data?.detail || err?.message,
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingToolFailure(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [showToastError, toolFailureSettingLoadFailedMessage]);

  const handleSaveDisplayName = async () => {
    setSavingDisplayName(true);
    try {
      await updateProductName(normalizedDisplayName || null);
      showToastSuccess(t('displayNameSaved'));
    } catch (err: any) {
      showToastError(t('displayNameSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingDisplayName(false);
    }
  };

  const handleResetDisplayName = async () => {
    setSavingDisplayName(true);
    try {
      await updateProductName(null);
      showToastSuccess(t('displayNameSaved'));
    } catch (err: any) {
      showToastError(t('displayNameSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingDisplayName(false);
    }
  };

  const handleFaviconUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    setSavingFavicon(true);
    try {
      await uploadProductFavicon(file);
      showToastSuccess(t('faviconSaved'));
    } catch (err: any) {
      showToastError(t('faviconSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingFavicon(false);
    }
  };

  const handleResetFavicon = async () => {
    setSavingFavicon(true);
    try {
      await resetProductFavicon();
      showToastSuccess(t('faviconSaved'));
    } catch (err: any) {
      showToastError(t('faviconSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingFavicon(false);
    }
  };

  const handleToolFailureAutoDisableChange = async () => {
    const nextValue = !toolFailureAutoDisable;
    setSavingToolFailure(true);
    try {
      const config = await toolFailureConfigApi.update(nextValue);
      setToolFailureAutoDisable(config.disableOnRepeatedFailure);
      showToastSuccess(t('toolFailureSettingSaved'));
    } catch (err: any) {
      showToastError(
        t('toolFailureSettingSaveFailed'),
        err?.response?.data?.detail || err?.message,
      );
    } finally {
      setSavingToolFailure(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl">
      <header className="border-b border-zinc-200 pb-6 dark:border-zinc-800">
        <h1 className="text-2xl font-bold tracking-normal text-zinc-950 dark:text-zinc-50">{t('settingsPreferences')}</h1>
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">{t('settingsPreferencesDescription')}</p>
      </header>

      <div className="mt-2">
        <PreferenceRow
          icon={TextCursorInput}
          title={t('displayName')}
          description={t('displayNameDescription')}
        >
          <div className="flex w-full flex-col gap-2 md:max-w-64">
            <input
              value={displayNameDraft}
              onChange={(event) => setDisplayNameDraft(event.target.value)}
              maxLength={48}
              placeholder={t('displayNamePlaceholder')}
              className="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-sm text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-600"
            />
            <div className="text-xs text-zinc-500 dark:text-zinc-400">
              {t('displayNameCurrent', { name: productName })}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleSaveDisplayName()}
                disabled={savingDisplayName || !displayNameChanged}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-md bg-zinc-950 px-3 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
              >
                <Save className="h-4 w-4" />
                {t('saveDisplayName')}
              </button>
              <button
                type="button"
                onClick={() => void handleResetDisplayName()}
                disabled={savingDisplayName || !configuredDisplayName}
                title={t('resetDisplayName')}
                aria-label={t('resetDisplayName')}
                className="inline-flex h-9 w-10 items-center justify-center rounded-md border border-zinc-200 text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={ImageIcon}
          title={t('favicon')}
          description={t('faviconDescription')}
        >
          <div className="flex w-full flex-col gap-3 md:max-w-64">
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
                <img src={faviconUrl} alt="" className="h-7 w-7 rounded-sm object-contain" />
              </span>
              <div className="min-w-0 text-xs text-zinc-500 dark:text-zinc-400">
                {hasCustomFavicon ? t('faviconCustom') : t('faviconDefault')}
              </div>
            </div>
            <input
              ref={faviconInputRef}
              type="file"
              accept=".ico,.png,.svg,.jpg,.jpeg,.webp,image/x-icon,image/png,image/svg+xml,image/jpeg,image/webp"
              className="hidden"
              onChange={(event) => void handleFaviconUpload(event)}
            />
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => faviconInputRef.current?.click()}
                disabled={savingFavicon}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-md bg-zinc-950 px-3 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
              >
                <Upload className="h-4 w-4" />
                {t('uploadFavicon')}
              </button>
              <button
                type="button"
                onClick={() => void handleResetFavicon()}
                disabled={savingFavicon || !hasCustomFavicon}
                title={t('resetFavicon')}
                aria-label={t('resetFavicon')}
                className="inline-flex h-9 w-10 items-center justify-center rounded-md border border-zinc-200 text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={Languages}
          title={t('language')}
          description={t('languageDescription')}
        >
          <div className="grid w-56 grid-cols-2 rounded-lg border border-zinc-200 bg-white p-1 dark:border-zinc-800 dark:bg-zinc-900">
            <SegmentedOption
              active={language === 'en-US'}
              icon={Languages}
              onClick={() => void i18n.changeLanguage('en-US')}
            >
              EN
            </SegmentedOption>
            <SegmentedOption
              active={language === 'zh-CN'}
              icon={Languages}
              onClick={() => void i18n.changeLanguage('zh-CN')}
            >
              中
            </SegmentedOption>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={theme === 'dark' ? Moon : Sun}
          title={t('theme')}
          description={t('themeDescription')}
        >
          <div className="grid w-56 grid-cols-2 rounded-lg border border-zinc-200 bg-white p-1 dark:border-zinc-800 dark:bg-zinc-900">
            <SegmentedOption
              active={theme === 'light'}
              icon={Sun}
              onClick={() => setTheme('light')}
            >
              {t('lightTheme')}
            </SegmentedOption>
            <SegmentedOption
              active={theme === 'dark'}
              icon={Moon}
              onClick={() => setTheme('dark')}
            >
              {t('darkTheme')}
            </SegmentedOption>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={ShieldCheck}
          title={t('toolFailureAutoDisable')}
          description={t('toolFailureAutoDisableDescription')}
        >
          <PreferenceSwitch
            checked={toolFailureAutoDisable}
            disabled={loadingToolFailure || savingToolFailure}
            label={t('toolFailureAutoDisable')}
            onChange={() => void handleToolFailureAutoDisableChange()}
          />
        </PreferenceRow>
      </div>
    </div>
  );
}

function SettingsContent({ sectionId }: { sectionId: SettingsSectionId }) {
  if (sectionId === 'preferences') return <PreferencesPanel />;

  return (
    <Suspense fallback={<RoutePageSkeleton delayMs={180} />}>
      {sectionId === 'account' && <ConfigPage />}
      {sectionId === 'archived-data' && <ArchivedDataPage />}
      {sectionId === 'security-config' && <SecurityConfigPage />}
      {sectionId === 'system-logs' && <SystemLogPage />}
      {sectionId === 'audit-logs' && <AuditLogsPage />}
      {sectionId === 'flockspro' && <FlocksproUpgradePage />}
    </Suspense>
  );
}

export default function SettingsPage() {
  const params = useParams();
  const sectionId = params.sectionId;
  // Same source as the layout sidebar, so menu and page can never disagree.
  const { groups } = useSettingsSectionGroups();

  if (!sectionId) {
    return <Navigate to="/settings/preferences" replace />;
  }

  if (sectionId === 'models') {
    return <Navigate to="/models" replace />;
  }

  if (sectionId === 'channels') {
    return <Navigate to="/channels" replace />;
  }

  if (!isSettingsSectionId(sectionId)) {
    return <Navigate to="/settings/preferences" replace />;
  }

  const visible = groups.flatMap((group) => group.items).some((item) => item.id === sectionId);
  if (!visible) {
    return <Navigate to="/settings/preferences" replace />;
  }

  return <SettingsContent sectionId={sectionId} />;
}
