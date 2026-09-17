import {
  StrictMode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, useSearchParams } from 'react-router-dom';
import i18next from 'i18next';
import {
  I18nextProvider,
  initReactI18next,
  useTranslation,
} from 'react-i18next';
import {
  BookOpen,
  Bot,
  ServerCog,
  Sparkles,
  Workflow,
  Wrench,
} from 'lucide-react';
import { ToastProvider } from '@/components/common/Toast';
import ThemeToggle from '@/components/common/ThemeToggle';
import LanguageSwitcher from '@/components/common/LanguageSwitcher';
import { ThemeContext, type Theme } from '@/contexts/ThemeContext';
import enCommon from '@/locales/en-US/common.json';
import enNav from '@/locales/en-US/nav.json';
import enPluginLibrary from '@/locales/en-US/pluginLibrary.json';
import zhCommon from '@/locales/zh-CN/common.json';
import zhNav from '@/locales/zh-CN/nav.json';
import zhPluginLibrary from '@/locales/zh-CN/pluginLibrary.json';
import '@/styles/index.css';

const MODULES = [
  {
    type: 'workflow',
    label: 'workflows',
    section: 'aiWorkbench',
    icon: Workflow,
  },
  {
    type: 'device',
    label: 'deviceIntegration',
    section: 'sceneWorkspaces',
    icon: ServerCog,
  },
  { type: 'agent', label: 'agents', section: 'agentHub', icon: Bot },
  { type: 'skill', label: 'skills', section: 'agentHub', icon: BookOpen },
  { type: 'tool', label: 'tools', section: 'agentHub', icon: Wrench },
] as const;
const SECTIONS = ['aiWorkbench', 'sceneWorkspaces', 'agentHub'] as const;
type PreviewModule = (typeof MODULES)[number];
type PluginType = PreviewModule['type'];

function PrototypeShell({
  children,
}: {
  children: (pluginType: PluginType) => ReactNode;
}) {
  const { t, i18n } = useTranslation('nav');
  const [searchParams] = useSearchParams();
  const currentModule =
    MODULES.find(({ type }) => type === searchParams.get('type')) ?? MODULES[0];
  // Keep preview preferences in memory; never mount the persistent ThemeProvider.
  const [theme, setTheme] = useState<Theme>('light');
  const [temporaryThemeOverride, setTemporaryThemeOverride] =
    useState<Theme | null>(null);
  const effectiveTheme = temporaryThemeOverride ?? theme;
  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
  }, []);
  const themeContext = useMemo(
    () => ({
      theme,
      effectiveTheme,
      toggleTheme,
      setTheme,
      setTemporaryThemeOverride,
    }),
    [theme, effectiveTheme, toggleTheme],
  );

  useLayoutEffect(() => {
    document.documentElement.classList.toggle(
      'dark',
      effectiveTheme === 'dark',
    );
    document.documentElement.style.colorScheme = effectiveTheme;
  }, [effectiveTheme]);

  useEffect(() => {
    document.documentElement.lang = i18n.resolvedLanguage || i18n.language;
    document.title = `Flocks / ${t(currentModule.label)} · ${t('prototypeLabel')}`;
  }, [currentModule.label, i18n.language, i18n.resolvedLanguage, t]);

  const renderModuleLink = (module: PreviewModule) => {
    const params = new URLSearchParams(searchParams);
    params.set('type', module.type);
    const active = currentModule.type === module.type;

    return (
      <Link
        key={module.type}
        to={`?${params.toString()}`}
        aria-current={active ? 'page' : undefined}
        className={`flex shrink-0 items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
          active
            ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
            : 'text-zinc-600 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
        }`}
      >
        <module.icon
          className={`h-5 w-5 shrink-0 ${active ? 'text-zinc-700 dark:text-zinc-100' : 'text-zinc-400 dark:text-zinc-500'}`}
          aria-hidden="true"
        />
        {t(module.label)}
      </Link>
    );
  };

  return (
    <ThemeContext.Provider value={themeContext}>
      <div className="min-h-screen bg-gray-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <header className="sticky top-0 z-30 flex h-16 items-center border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex shrink-0 items-center gap-2 px-4 lg:w-52">
            <Sparkles
              className="h-5 w-5 text-zinc-500 dark:text-zinc-300"
              aria-hidden="true"
            />
            <span className="text-lg font-bold tracking-tight">Flocks</span>
          </div>
          <div className="flex min-w-0 flex-1 items-center justify-between gap-2 pr-4 sm:gap-4 sm:pr-6 lg:pl-6">
            <div className="flex min-w-0 items-center gap-2 text-sm sm:gap-3">
              <span
                className="text-zinc-300 dark:text-zinc-700"
                aria-hidden="true"
              >
                /
              </span>
              <span
                className="truncate text-zinc-600 dark:text-zinc-300"
                title={t(currentModule.label)}
              >
                {t(currentModule.label)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-1 sm:gap-2">
              <span
                className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300"
                title={t('prototypeLocalNotice')}
              >
                Mock
              </span>
              <ThemeToggle />
              <LanguageSwitcher />
            </div>
          </div>
        </header>

        <aside className="fixed bottom-0 left-0 top-16 hidden w-52 flex-col border-r border-zinc-200 bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-950 lg:flex">
          <nav
            className="flex-1 overflow-y-auto px-3 py-6"
            aria-label={t('prototypeNavigation')}
          >
            {SECTIONS.map((section) => (
              <div key={section} className="mb-6">
                <h2 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                  {t(section)}
                </h2>
                <div className="space-y-0.5">
                  {MODULES.filter((module) => module.section === section).map(
                    renderModuleLink,
                  )}
                </div>
              </div>
            ))}
          </nav>
          <p className="border-t border-zinc-200 px-4 py-4 text-xs leading-relaxed text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            {t('prototypeLocalNotice')}
          </p>
        </aside>

        <div className="lg:pl-52">
          <nav
            className="flex gap-1 overflow-x-auto border-b border-zinc-200 bg-zinc-100 px-4 py-2 dark:border-zinc-800 dark:bg-zinc-950 lg:hidden"
            aria-label={t('prototypeNavigation')}
          >
            {MODULES.map(renderModuleLink)}
          </nav>
          <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
            {children(currentModule.type)}
          </main>
        </div>
      </div>
    </ThemeContext.Provider>
  );
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing prototype root element');

if (import.meta.env.DEV) {
  // No detector, shared app i18n, or preference listeners: language stays local.
  const prototypeI18n = i18next.createInstance();
  void Promise.all([
    import('@/pages/PluginLibraryPrototype'),
    prototypeI18n.use(initReactI18next).init({
      resources: {
        'zh-CN': {
          common: zhCommon,
          nav: zhNav,
          pluginLibrary: zhPluginLibrary,
        },
        'en-US': {
          common: enCommon,
          nav: enNav,
          pluginLibrary: enPluginLibrary,
        },
      },
      lng: 'zh-CN',
      fallbackLng: 'en-US',
      supportedLngs: ['zh-CN', 'en-US'],
      ns: ['common', 'nav', 'pluginLibrary'],
      defaultNS: 'common',
      interpolation: { escapeValue: false },
    }),
  ])
    .then(([{ default: PluginLibraryPrototype }]) => {
      createRoot(root).render(
        <StrictMode>
          <I18nextProvider i18n={prototypeI18n}>
            <BrowserRouter>
              <ToastProvider>
                <PrototypeShell>
                  {(pluginType) => (
                    <PluginLibraryPrototype pluginType={pluginType} />
                  )}
                </PrototypeShell>
              </ToastProvider>
            </BrowserRouter>
          </I18nextProvider>
        </StrictMode>,
      );
    })
    .catch((error: unknown) => {
      console.error('Unable to load the grouping prototype', error);
      root.textContent = '无法加载交互原型，请检查开发服务器后刷新。';
    });
} else {
  root.textContent = 'This prototype is available only in development.';
}
