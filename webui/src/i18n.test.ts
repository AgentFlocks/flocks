import { describe, expect, it } from 'vitest';
import i18n, { preloadI18nNamespaces } from './i18n';

describe('i18n lazy namespaces', () => {
  it('loads route namespaces on demand instead of bundling them up front', async () => {
    await i18n.changeLanguage('en-US');
    i18n.removeResourceBundle('en-US', 'workflow');

    expect(i18n.hasResourceBundle('en-US', 'home')).toBe(true);
    expect(i18n.hasResourceBundle('en-US', 'workflow')).toBe(false);

    await preloadI18nNamespaces(['workflow']);

    expect(i18n.hasResourceBundle('en-US', 'workflow')).toBe(true);
    expect(Object.keys(i18n.getResourceBundle('en-US', 'workflow'))).not.toHaveLength(0);
  });
});
