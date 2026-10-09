import type { ReadCallback, ResourceKey, i18n as I18nInstance } from 'i18next';
import registry, { type ExtensionI18nResource } from '@ext/registry';

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
  'mcp',
  'model',
  'monitoring',
  'networkProxy',
  'notification',
  'permission',
  'pluginGroups',
  'session',
  'skill',
  'task',
  'tool',
  'update',
  'voice',
  'workflow',
  'workspace',
] as const;

export const I18N_NAMESPACES = [...INITIAL_NAMESPACES, ...LAZY_NAMESPACES] as const;

type SupportedLanguage = 'en-US' | 'zh-CN';
type InitialNamespace = (typeof INITIAL_NAMESPACES)[number];
type LazyNamespace = (typeof LAZY_NAMESPACES)[number];
type LocaleModule = { default: ResourceKey };
type LocaleLoader = () => Promise<LocaleModule>;
type ResourceObject = Record<string, unknown>;

const SUPPORTED_LANGUAGES: readonly SupportedLanguage[] = ['en-US', 'zh-CN'];

function isPlainObject(value: unknown): value is ResourceObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Recursively merges `override` into a copy of `base`; objects merge, everything else is replaced. */
export function mergeLocaleResources(base: unknown, override: unknown): ResourceObject {
  const result: ResourceObject = isPlainObject(base) ? { ...base } : {};
  if (!isPlainObject(override)) return result;
  for (const [key, value] of Object.entries(override)) {
    const existing = result[key];
    result[key] = isPlainObject(existing) && isPlainObject(value)
      ? mergeLocaleResources(existing, value)
      : value;
  }
  return result;
}

function isInitialNamespace(namespace: string): namespace is InitialNamespace {
  return (INITIAL_NAMESPACES as readonly string[]).includes(namespace);
}

function toLocaleLoader(resource: ExtensionI18nResource): LocaleLoader {
  if (typeof resource === 'function') {
    return resource as LocaleLoader;
  }
  return () => Promise.resolve({ default: resource as ResourceKey });
}

const builtinInitialResources: Record<SupportedLanguage, Record<InitialNamespace, ResourceKey>> = {
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

const builtinLazyLoaders: Record<SupportedLanguage, Record<LazyNamespace, LocaleLoader>> = {
  'en-US': {
    agent: () => import('./locales/en-US/agent.json'),
    channel: () => import('./locales/en-US/channel.json'),
    config: () => import('./locales/en-US/config.json'),
    device: () => import('./locales/en-US/device.json'),
    mcp: () => import('./locales/en-US/mcp.json'),
    model: () => import('./locales/en-US/model.json'),
    monitoring: () => import('./locales/en-US/monitoring.json'),
    networkProxy: () => import('./locales/en-US/networkProxy.json'),
    notification: () => import('./locales/en-US/notification.json'),
    permission: () => import('./locales/en-US/permission.json'),
    pluginGroups: () => import('./locales/en-US/pluginGroups.json'),
    session: () => import('./locales/en-US/session.json'),
    skill: () => import('./locales/en-US/skill.json'),
    task: () => import('./locales/en-US/task.json'),
    tool: () => import('./locales/en-US/tool.json'),
    update: () => import('./locales/en-US/update.json'),
    voice: () => import('./locales/en-US/voice.json'),
    workflow: () => import('./locales/en-US/workflow.json'),
    workspace: () => import('./locales/en-US/workspace.json'),
  },
  'zh-CN': {
    agent: () => import('./locales/zh-CN/agent.json'),
    channel: () => import('./locales/zh-CN/channel.json'),
    config: () => import('./locales/zh-CN/config.json'),
    device: () => import('./locales/zh-CN/device.json'),
    mcp: () => import('./locales/zh-CN/mcp.json'),
    model: () => import('./locales/zh-CN/model.json'),
    monitoring: () => import('./locales/zh-CN/monitoring.json'),
    networkProxy: () => import('./locales/zh-CN/networkProxy.json'),
    notification: () => import('./locales/zh-CN/notification.json'),
    permission: () => import('./locales/zh-CN/permission.json'),
    pluginGroups: () => import('./locales/zh-CN/pluginGroups.json'),
    session: () => import('./locales/zh-CN/session.json'),
    skill: () => import('./locales/zh-CN/skill.json'),
    task: () => import('./locales/zh-CN/task.json'),
    tool: () => import('./locales/zh-CN/tool.json'),
    update: () => import('./locales/zh-CN/update.json'),
    voice: () => import('./locales/zh-CN/voice.json'),
    workflow: () => import('./locales/zh-CN/workflow.json'),
    workspace: () => import('./locales/zh-CN/workspace.json'),
  },
};

// ---------------------------------------------------------------------------
// Extension resources (from the build-time registry), split once at module load:
//  - static objects for bundled namespaces merge into the initial resources;
//  - everything else becomes a lazy loader merged over the built-in bundle.
// ---------------------------------------------------------------------------

const extensionLazyLoaders: Record<SupportedLanguage, Record<string, LocaleLoader>> = { 'en-US': {}, 'zh-CN': {} };
const extensionInitialLoaders: Record<SupportedLanguage, Partial<Record<InitialNamespace, LocaleLoader>>> = { 'en-US': {}, 'zh-CN': {} };

function buildInitialResources() {
  const merged: Record<SupportedLanguage, Record<string, ResourceKey>> = {
    'en-US': { ...builtinInitialResources['en-US'] },
    'zh-CN': { ...builtinInitialResources['zh-CN'] },
  };
  for (const language of SUPPORTED_LANGUAGES) {
    const namespaces = registry.i18n?.[language];
    if (!namespaces) continue;
    for (const [namespace, resource] of Object.entries(namespaces)) {
      if (isInitialNamespace(namespace)) {
        if (typeof resource === 'function') {
          extensionInitialLoaders[language][namespace] = resource as LocaleLoader;
        } else {
          merged[language][namespace] = mergeLocaleResources(merged[language][namespace], resource);
        }
      } else {
        extensionLazyLoaders[language][namespace] = toLocaleLoader(resource);
      }
    }
  }
  return merged;
}

export const initialI18nResources = buildInitialResources();

const loadingLocaleBundles = new Map<string, Promise<void>>();
let registeredI18n: I18nInstance | null = null;

function applyExtensionInitialResources(i18n: I18nInstance) {
  for (const language of SUPPORTED_LANGUAGES) {
    for (const [namespace, loader] of Object.entries(extensionInitialLoaders[language])) {
      if (!loader) continue;
      void loader()
        .then((module) => {
          i18n.addResourceBundle(language, namespace, module.default, true, true);
        })
        .catch(() => {
          // A missing extension bundle must not break the built-in translations.
        });
    }
  }
}

export function registerI18nInstance(i18n: I18nInstance) {
  registeredI18n = i18n;
  applyExtensionInitialResources(i18n);
}

function normalizeLanguage(language: string): SupportedLanguage {
  return language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}

function getLazyLoader(language: string, namespace: string): LocaleLoader | undefined {
  const normalizedLanguage = normalizeLanguage(language);
  const builtin = builtinLazyLoaders[normalizedLanguage][namespace as LazyNamespace];
  const extension = extensionLazyLoaders[normalizedLanguage][namespace];
  if (!extension) return builtin;
  if (!builtin) return extension;
  return () => Promise.all([builtin(), extension()]).then(([base, override]) => ({
    default: mergeLocaleResources(base.default, override.default),
  }));
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
