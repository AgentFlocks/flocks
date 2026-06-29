import { Suspense, lazy, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft,
  ArrowUpCircle,
  Brain,
  Check,
  Languages,
  Moon,
  Radio,
  ScrollText,
  Settings as SettingsIcon,
  Sun,
  UserCog,
  type LucideIcon,
} from 'lucide-react';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import { ThemeContext } from '@/contexts/ThemeContext';
import { useAuth } from '@/contexts/AuthContext';

const ConfigPage = lazy(() => import('@/pages/Config'));
const SystemLogPage = lazy(() => import('@/pages/SystemLog'));
const FlocksproUpgradePage = lazy(() => import('@/pages/FlocksproUpgrade'));
const ModelPage = lazy(() => import('@/pages/Model'));
const ChannelPage = lazy(() => import('@/pages/Channel'));

type SettingsSectionId = 'preferences' | 'account' | 'system-logs' | 'flockspro' | 'models' | 'channels';

interface ReturnLocation {
  pathname: string;
  search: string;
  hash: string;
}

interface SettingsLocationState {
  from?: Partial<ReturnLocation>;
}

interface SettingsSection {
  id: SettingsSectionId;
  name: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

interface SettingsGroup {
  name: string;
  items: SettingsSection[];
}

function isSettingsSectionId(value: string | undefined): value is SettingsSectionId {
  return (
    value === 'preferences' ||
    value === 'account' ||
    value === 'system-logs' ||
    value === 'flockspro' ||
    value === 'models' ||
    value === 'channels'
  );
}

function sanitizeReturnLocation(state: unknown): ReturnLocation {
  const from = (state as SettingsLocationState | null)?.from;
  const pathname = typeof from?.pathname === 'string' ? from.pathname : '';
  if (!pathname.startsWith('/') || pathname.startsWith('/settings')) {
    return { pathname: '/', search: '', hash: '' };
  }

  return {
    pathname,
    search: typeof from?.search === 'string' && from.search.startsWith('?') ? from.search : '',
    hash: typeof from?.hash === 'string' && from.hash.startsWith('#') ? from.hash : '',
  };
}

function buildReturnPath(location: ReturnLocation): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

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

function PreferencesPanel() {
  const { t, i18n } = useTranslation('nav');
  const { theme, setTheme } = useContext(ThemeContext);
  const language = i18n.language?.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';

  return (
    <div className="mx-auto w-full max-w-5xl">
      <header className="border-b border-zinc-200 pb-6 dark:border-zinc-800">
        <h1 className="text-2xl font-bold tracking-normal text-zinc-950 dark:text-zinc-50">{t('settingsPreferences')}</h1>
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">{t('settingsPreferencesDescription')}</p>
      </header>

      <div className="mt-2">
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
      </div>
    </div>
  );
}

function SettingsContent({ sectionId }: { sectionId: SettingsSectionId }) {
  if (sectionId === 'preferences') return <PreferencesPanel />;

  return (
    <Suspense fallback={<RoutePageSkeleton />}>
      {sectionId === 'account' && <ConfigPage />}
      {sectionId === 'system-logs' && <SystemLogPage />}
      {sectionId === 'flockspro' && <FlocksproUpgradePage />}
      {sectionId === 'models' && <ModelPage />}
      {sectionId === 'channels' && <ChannelPage />}
    </Suspense>
  );
}

export default function SettingsPage() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { t } = useTranslation('nav');
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const sectionId = params.sectionId;
  const returnLocation = useMemo(() => sanitizeReturnLocation(location.state), [location.state]);
  const settingsRouteState = useMemo(() => ({ from: returnLocation }), [returnLocation]);

  const groups = useMemo<SettingsGroup[]>(
    () => [
      {
        name: t('settingsGroupPreferences'),
        items: [
          { id: 'preferences', name: t('settingsPreferences'), icon: SettingsIcon },
        ],
      },
      {
        name: t('settingsGroupSystem'),
        items: [
          { id: 'account', name: t('accountManagement'), icon: UserCog },
          { id: 'system-logs', name: t('systemLog'), icon: ScrollText },
          { id: 'flockspro', name: t('flocksproUpgrade'), icon: ArrowUpCircle, adminOnly: true },
        ],
      },
      {
        name: t('settingsGroupIntegrations'),
        items: [
          { id: 'models', name: t('models'), icon: Brain },
          { id: 'channels', name: t('channels'), icon: Radio },
        ],
      },
    ],
    [t],
  );

  const visibleGroups = groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.adminOnly || isAdmin),
    }))
    .filter((group) => group.items.length > 0);

  if (!sectionId) {
    return <Navigate to="/settings/preferences" replace state={settingsRouteState} />;
  }

  if (!isSettingsSectionId(sectionId)) {
    return <Navigate to="/settings/preferences" replace state={settingsRouteState} />;
  }

  const currentSection = visibleGroups.flatMap((group) => group.items).find((item) => item.id === sectionId);

  if (!currentSection) {
    return <Navigate to="/settings/preferences" replace state={settingsRouteState} />;
  }

  return (
    <div className="flex h-screen min-h-0 bg-white text-zinc-950 dark:bg-zinc-950 dark:text-zinc-100">
      <aside className="hidden w-64 shrink-0 border-r border-zinc-200 bg-gray-50 dark:border-zinc-800 dark:bg-zinc-900 md:block">
        <div className="border-b border-zinc-200 px-6 py-5 dark:border-zinc-800">
          <button
            type="button"
            onClick={() => navigate(buildReturnPath(returnLocation))}
            className="-ml-1 mb-4 inline-flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-semibold text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50"
          >
            <ArrowLeft className="h-4 w-4" />
            {t('settingsBack')}
          </button>
          <h1 className="text-xl font-bold text-zinc-950 dark:text-zinc-50">{t('settingsTitle')}</h1>
          <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{t('settingsDescription')}</p>
        </div>
        <nav className="space-y-6 px-3 py-4">
          {visibleGroups.map((group) => (
            <div key={group.name}>
              <h2 className="px-3 text-xs font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                {group.name}
              </h2>
              <div className="mt-2 space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = item.id === sectionId;
                  return (
                    <Link
                      key={item.id}
                      to={`/settings/${item.id}`}
                      state={settingsRouteState}
                      className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${
                        active
                          ? 'bg-zinc-100 text-zinc-950 dark:bg-zinc-900 dark:text-zinc-50'
                          : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
                      }`}
                    >
                      <Icon className={`h-5 w-5 ${active ? 'text-zinc-700 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                      <span className="truncate">{item.name}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>

      <div className="min-w-0 flex-1 overflow-y-auto bg-white dark:bg-zinc-950">
        <div className="mx-auto min-h-full w-full px-6 py-6 lg:px-8">
          <SettingsContent sectionId={sectionId} />
        </div>
      </div>
    </div>
  );
}
