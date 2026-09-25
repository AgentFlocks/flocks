import PageSelectionGroup from '../PageSelectionGroup';
import { Suspense, lazy, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, ComponentType, ReactNode } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  IconCheck,
  IconPhoto,
  IconLanguage,
  IconDeviceDesktop,
  IconMoon,
  IconRotate,
  IconDeviceFloppy,
  IconShieldCheck,
  IconSun,
  IconForms,
  IconUpload,
  type TablerIcon,
} from '@tabler/icons-react';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import { ThemeContext } from '@/contexts/ThemeContext';
import { useProductName } from '@/contexts/ProductNameContext';
import { useToast } from '@/components/common/Toast';
import registry, { type ExtensionSettingsSection } from '@ext/registry';
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
const ArchivedDataPage = lazySettingsPage(() => import('./ArchivedDataPanel'), ['session']);
const NetworkProxyPage = lazySettingsPage(() => import('./NetworkProxyPanel'), ['networkProxy']);
const VoiceSettingsPage = lazySettingsPage(() => import('@/pages/VoiceSettings'), ['voice']);


function PreferenceRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: TablerIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 border-b border-line py-6 last:border-b-0 md:grid md:grid-cols-[minmax(0,1fr)_20rem] md:items-center md:gap-8">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-subtle text-fg-muted ">
          <Icon aria-hidden="true" className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg ">{title}</h3>
          <p className="mt-1 text-sm text-fg-muted ">{description}</p>
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
  icon: TablerIcon;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex h-control-sm min-w-0 flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-segment px-3 text-sm font-semibold transition-colors ${
        active
          ? 'bg-selected text-fg '
          : 'text-fg-secondary hover:bg-selected hover:text-fg '
      }`}
    >
      <Icon aria-hidden="true" className="h-4 w-4" />
      {children}
      {active && <IconCheck aria-hidden="true" className="h-4 w-4" />}
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
  const { t } = useTranslation('common');
  return (
    <span className="inline-flex items-center gap-2">
      <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
        className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={checked} aria-disabled={disabled} />
</button>
      <span className={disabled ? 'text-sm text-fg-disabled' : 'text-sm text-fg-secondary'}>
        {t(checked ? 'status.enabled' : 'status.disabled')}
      </span>
    </span>
  );
}

function PreferencesPanel() {
  const { t, i18n } = useTranslation('nav');
  const { theme, mode: themeMode, setTheme } = useContext(ThemeContext);
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
      <header className="border-b border-line pb-6 ">
        <h1 className="text-title-page font-bold tracking-normal text-fg">{t('settingsPreferences')}</h1>
        <p className="mt-2 text-sm text-fg-muted ">{t('settingsPreferencesDescription')}</p>
      </header>

      <div className="mt-2">
        <PreferenceRow
          icon={IconForms}
          title={t('displayName')}
          description={t('displayNameDescription')}
        >
          <div className="flex w-full flex-col gap-2 md:max-w-64">
            <input
              value={displayNameDraft}
              onChange={(event) => setDisplayNameDraft(event.target.value)}
              maxLength={48}
              placeholder={t('displayNamePlaceholder')}
              className="ui-input h-9 w-full rounded-control border border-line bg-surface px-3 text-sm text-fg transition-colors placeholder:text-fg-muted focus:border-line-control"
            />
            <div className="text-xs text-fg-muted ">
              {t('displayNameCurrent', { name: productName })}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleSaveDisplayName()}
                disabled={savingDisplayName || !displayNameChanged}
                className="ui-btn ui-btn-primary flex-1"
              >
                <IconDeviceFloppy aria-hidden="true" className="h-4 w-4" />
                {t('saveDisplayName')}
              </button>
              <button
                type="button"
                onClick={() => void handleResetDisplayName()}
                disabled={savingDisplayName || !configuredDisplayName}
                title={t('resetDisplayName')}
                aria-label={t('resetDisplayName')}
                className="ui-btn-icon inline-flex items-center justify-center inline-flex   items-center justify-center rounded-control border border-line text-fg-muted transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                <IconRotate aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={IconPhoto}
          title={t('favicon')}
          description={t('faviconDescription')}
        >
          <div className="flex w-full flex-col gap-3 md:max-w-64">
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-control border border-line bg-surface ">
                <img src={faviconUrl} alt="" className="h-7 w-7 rounded-tag object-contain" />
              </span>
              <div className="min-w-0 text-xs text-fg-muted ">
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
                className="ui-btn ui-btn-primary flex-1"
              >
                <IconUpload aria-hidden="true" className="h-4 w-4" />
                {t('uploadFavicon')}
              </button>
              <button
                type="button"
                onClick={() => void handleResetFavicon()}
                disabled={savingFavicon || !hasCustomFavicon}
                title={t('resetFavicon')}
                aria-label={t('resetFavicon')}
                className="ui-btn-icon inline-flex items-center justify-center inline-flex   items-center justify-center rounded-control border border-line text-fg-muted transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled "
              >
                <IconRotate aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </div>
        </PreferenceRow>

        <PreferenceRow
          icon={IconLanguage}
          title={t('language')}
          description={t('languageDescription')}
        >
          <PageSelectionGroup className="grid w-56 grid-cols-2">
            <SegmentedOption
              active={language === 'en-US'}
              icon={IconLanguage}
              onClick={() => void i18n.changeLanguage('en-US')}
            >
              EN
            </SegmentedOption>
            <SegmentedOption
              active={language === 'zh-CN'}
              icon={IconLanguage}
              onClick={() => void i18n.changeLanguage('zh-CN')}
            >
              中
            </SegmentedOption>
          </PageSelectionGroup>
        </PreferenceRow>

        <PreferenceRow
          icon={theme === 'dark' ? IconMoon : IconSun}
          title={t('theme')}
          description={t('themeDescription')}
        >
          <PageSelectionGroup className="grid w-full grid-cols-3">
            <SegmentedOption
              active={themeMode === 'system'}
              icon={IconDeviceDesktop}
              onClick={() => setTheme('system')}
            >
              {t('systemTheme')}
            </SegmentedOption>
            <SegmentedOption
              active={themeMode === 'light'}
              icon={IconSun}
              onClick={() => setTheme('light')}
            >
              {t('lightTheme')}
            </SegmentedOption>
            <SegmentedOption
              active={themeMode === 'dark'}
              icon={IconMoon}
              onClick={() => setTheme('dark')}
            >
              {t('darkTheme')}
            </SegmentedOption>
          </PageSelectionGroup>
        </PreferenceRow>

        <PreferenceRow
          icon={IconShieldCheck}
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

const extensionSectionPages = new Map<string, ComponentType>();

function extensionSectionPage(section: ExtensionSettingsSection): ComponentType {
  const cached = extensionSectionPages.get(section.id);
  if (cached) return cached;
  const page = lazySettingsPage(section.load, section.i18nNamespaces);
  extensionSectionPages.set(section.id, page);
  return page;
}

function SettingsContent({ sectionId }: { sectionId: SettingsSectionId }) {
  if (sectionId === 'preferences') return <PreferencesPanel />;

  const extension = registry.settingsSections.find((section) => section.id === sectionId);
  const ExtensionPage = extension ? extensionSectionPage(extension) : null;

  return (
    <Suspense fallback={<RoutePageSkeleton delayMs={180} />}>
      {sectionId === 'account' && <ConfigPage />}
      {sectionId === 'archived-data' && <ArchivedDataPage />}
      {sectionId === 'system-logs' && <SystemLogPage />}
      {sectionId === 'network-proxy' && <NetworkProxyPage />}
      {sectionId === 'voice-input' && <VoiceSettingsPage />}
      {ExtensionPage && <ExtensionPage />}
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
