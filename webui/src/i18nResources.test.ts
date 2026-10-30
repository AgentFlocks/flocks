import { describe, expect, it, vi } from 'vitest';

const { registry } = vi.hoisted(() => ({
  registry: {
    routes: [],
    navItems: [],
    settingsSections: [],
    slots: [],
    i18n: {
      'en-US': {
        nav: { extensionEntry: 'Extension entry', flocksHome: 'Overridden home' },
        session: () => Promise.resolve({ default: { chat: { extensionOnly: 'ext' } } }),
        privateNs: { hello: 'hi' },
        auth: () => Promise.resolve({ default: { admin: { extra: 'yes' } } }),
      },
      'zh-CN': {
        privateNs: () => Promise.resolve({ default: { hello: '你好' } }),
      },
    },
  },
}));

vi.mock('@ext/registry', () => ({ default: registry }));

import {
  I18N_NAMESPACES,
  initialI18nResources,
  mergeLocaleResources,
  readLazyLocale,
  registerI18nInstance,
} from './i18nResources';
import enNav from './locales/en-US/nav.json';
import enSession from './locales/en-US/session.json';

function read(language: string, namespace: string) {
  return new Promise<{ error: unknown; data: unknown }>((resolve) => {
    readLazyLocale(language, namespace, (error, data) => resolve({ error, data }));
  });
}

describe('mergeLocaleResources', () => {
  it('deep-merges nested objects and replaces leaves', () => {
    expect(mergeLocaleResources({ a: { b: 1, c: 2 }, d: 'x' }, { a: { c: 3 }, e: 'y' })).toEqual({
      a: { b: 1, c: 3 },
      d: 'x',
      e: 'y',
    });
  });

  it('tolerates non-object inputs', () => {
    expect(mergeLocaleResources(undefined, { a: 1 })).toEqual({ a: 1 });
    expect(mergeLocaleResources({ a: 1 }, undefined)).toEqual({ a: 1 });
  });
});

describe('extension i18n resources', () => {
  it('does not list private namespaces as built-in ones', () => {
    expect(I18N_NAMESPACES).not.toContain('privateNs');
  });

  it('merges static extension resources into bundled namespaces before init', () => {
    const nav = initialI18nResources['en-US'].nav as Record<string, unknown>;
    expect(nav.extensionEntry).toBe('Extension entry');
    expect(nav.flocksHome).toBe('Overridden home');
    expect(nav.sessions).toBe((enNav as Record<string, unknown>).sessions);
    expect((initialI18nResources['zh-CN'].nav as Record<string, unknown>).extensionEntry).toBeUndefined();
  });

  it('deep-merges lazy extension overrides over the built-in bundle', async () => {
    const { error, data } = await read('en-US', 'session');
    expect(error).toBeNull();
    const session = data as Record<string, any>;
    expect(session.chat.extensionOnly).toBe('ext');
    expect(session.chat.error).toBe((enSession as Record<string, any>).chat.error);
  });

  it('serves extension-only namespaces from static objects or loaders', async () => {
    expect((await read('en-US', 'privateNs')).data).toEqual({ hello: 'hi' });
    expect((await read('zh-CN', 'privateNs')).data).toEqual({ hello: '你好' });
  });

  it('returns an empty bundle for unknown namespaces', async () => {
    expect((await read('en-US', 'doesNotExist')).data).toEqual({});
  });

  it('adds lazily provided resources for bundled namespaces once an instance registers', async () => {
    const addResourceBundle = vi.fn();
    registerI18nInstance({ addResourceBundle } as never);
    await Promise.resolve();
    await Promise.resolve();
    expect(addResourceBundle).toHaveBeenCalledWith('en-US', 'auth', { admin: { extra: 'yes' } }, true, true);
  });
});
