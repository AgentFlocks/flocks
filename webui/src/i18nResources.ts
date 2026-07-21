import type { ReadCallback, ResourceKey, i18n as I18nInstance } from 'i18next';

import enCommon from './locales/en-US/common.json';
import enNav from './locales/en-US/nav.json';
import enHome from './locales/en-US/home.json';
import enAuth from './locales/en-US/auth.json';
import enWebUIContractPage from './locales/en-US/webuiContractPage.json';

import zhCommon from './locales/zh-CN/common.json';
import zhNav from './locales/zh-CN/nav.json';
import zhHome from './locales/zh-CN/home.json';
import zhAuth from './locales/zh-CN/auth.json';
import zhWebUIContractPage from './locales/zh-CN/webuiContractPage.json';

export const INITIAL_NAMESPACES = ['common', 'nav', 'home', 'auth', 'webuiContractPage'] as const;
const LAZY_NAMESPACES = [
  'agent',
  'channel',
  'config',
  'device',
  'flockspro',
  'mcp',
  'model',
  'monitoring',
  'notification',
  'permission',
  'session',
  'skill',
  'task',
  'tool',
  'update',
  'workflow',
  'workspace',
] as const;

export const I18N_NAMESPACES = [...INITIAL_NAMESPACES, ...LAZY_NAMESPACES] as const;

type SupportedLanguage = 'en-US' | 'zh-CN';
type LazyNamespace = (typeof LAZY_NAMESPACES)[number];
type LocaleModule = { default: ResourceKey };
type LocaleLoader = () => Promise<LocaleModule>;

export const initialI18nResources = {
  'en-US': {
    common: enCommon,
    nav: enNav,
    home: enHome,
    auth: enAuth,
    webuiContractPage: enWebUIContractPage,
  },
  'zh-CN': {
    common: zhCommon,
    nav: zhNav,
    home: zhHome,
    auth: zhAuth,
    webuiContractPage: zhWebUIContractPage,
  },
};

const lazyLocaleLoaders: Record<SupportedLanguage, Record<LazyNamespace, LocaleLoader>> = {
  'en-US': {
    agent: () => import('./locales/en-US/agent.json'),
    channel: () => import('./locales/en-US/channel.json'),
    config: () => import('./locales/en-US/config.json'),
    device: () => import('./locales/en-US/device.json'),
    flockspro: () => import('./locales/en-US/flockspro.json'),
    mcp: () => import('./locales/en-US/mcp.json'),
    model: () => import('./locales/en-US/model.json'),
    monitoring: () => import('./locales/en-US/monitoring.json'),
    notification: () => import('./locales/en-US/notification.json'),
    permission: () => import('./locales/en-US/permission.json'),
    session: () => import('./locales/en-US/session.json'),
    skill: () => import('./locales/en-US/skill.json'),
    task: () => import('./locales/en-US/task.json'),
    tool: () => import('./locales/en-US/tool.json'),
    update: () => import('./locales/en-US/update.json'),
    workflow: () => import('./locales/en-US/workflow.json'),
    workspace: () => import('./locales/en-US/workspace.json'),
  },
  'zh-CN': {
    agent: () => import('./locales/zh-CN/agent.json'),
    channel: () => import('./locales/zh-CN/channel.json'),
    config: () => import('./locales/zh-CN/config.json'),
    device: () => import('./locales/zh-CN/device.json'),
    flockspro: () => import('./locales/zh-CN/flockspro.json'),
    mcp: () => import('./locales/zh-CN/mcp.json'),
    model: () => import('./locales/zh-CN/model.json'),
    monitoring: () => import('./locales/zh-CN/monitoring.json'),
    notification: () => import('./locales/zh-CN/notification.json'),
    permission: () => import('./locales/zh-CN/permission.json'),
    session: () => import('./locales/zh-CN/session.json'),
    skill: () => import('./locales/zh-CN/skill.json'),
    task: () => import('./locales/zh-CN/task.json'),
    tool: () => import('./locales/zh-CN/tool.json'),
    update: () => import('./locales/zh-CN/update.json'),
    workflow: () => import('./locales/zh-CN/workflow.json'),
    workspace: () => import('./locales/zh-CN/workspace.json'),
  },
};

const loadingLocaleBundles = new Map<string, Promise<void>>();
let registeredI18n: I18nInstance | null = null;

export function registerI18nInstance(i18n: I18nInstance) {
  registeredI18n = i18n;
}

function normalizeLanguage(language: string): SupportedLanguage {
  return language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}

function getLazyLoader(language: string, namespace: string): LocaleLoader | undefined {
  return lazyLocaleLoaders[normalizeLanguage(language)][namespace as LazyNamespace];
}

async function loadLazyLocaleBundle(
  i18n: I18nInstance,
  language: string,
  namespace: string,
) {
  const normalizedLanguage = normalizeLanguage(language);
  if (i18n.hasResourceBundle(normalizedLanguage, namespace)) return;

  const loader = getLazyLoader(normalizedLanguage, namespace);
  if (!loader) return;

  const bundleKey = `${normalizedLanguage}:${namespace}`;
  const existingLoad = loadingLocaleBundles.get(bundleKey);
  if (existingLoad) {
    await existingLoad;
    return;
  }

  const loadPromise = loader()
    .then((module) => {
      i18n.addResourceBundle(normalizedLanguage, namespace, module.default, true, true);
    })
    .finally(() => {
      loadingLocaleBundles.delete(bundleKey);
    });

  loadingLocaleBundles.set(bundleKey, loadPromise);
  await loadPromise;
}

export function readLazyLocale(language: string, namespace: string, callback: ReadCallback) {
  const loader = getLazyLoader(language, namespace);
  if (!loader) {
    callback(null, {});
    return;
  }

  loader()
    .then((module) => callback(null, module.default))
    .catch((error: unknown) => {
      callback(error instanceof Error ? error : new Error(String(error)), false);
    });
}

export async function preloadI18nNamespaces(namespaces: readonly string[]): Promise<void> {
  const uniqueNamespaces = [...new Set(namespaces)].filter(Boolean);
  if (uniqueNamespaces.length === 0) return;

  const i18n = registeredI18n;
  if (!i18n) {
    if (import.meta.env.MODE === 'test') return;
    throw new Error('i18n instance is unavailable');
  }

  const currentLanguage = normalizeLanguage(i18n.resolvedLanguage || i18n.language || 'en-US');
  const languages = currentLanguage === 'en-US' ? ['en-US'] : [currentLanguage, 'en-US'];

  await Promise.all(
    languages.flatMap((language) => (
      uniqueNamespaces.map((namespace) => loadLazyLocaleBundle(i18n, language, namespace))
    )),
  );
}
