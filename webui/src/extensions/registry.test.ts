import path from 'path';
import { describe, expect, it, vi } from 'vitest';

import registry from './registry';
import { EXTENSION_REGISTRY_ENV, extensionAliases, resolveExtensionRegistry } from '../config/extensionRegistry';

// OSS default build: the bundled registry is empty and the application asks
// the server for the default update channel (OPS-0452).
describe('default WebUI extension registry', () => {
  it('registers nothing', () => {
    expect(registry.routes).toEqual([]);
    expect(registry.navItems).toEqual([]);
    expect(registry.settingsSections).toEqual([]);
    expect(registry.slots).toEqual([]);
    expect(registry.i18n).toEqual({});
    expect(registry.updateChannel).toBeUndefined();
    expect(registry.hideDefault).toBeUndefined();
  });

  it('resolves to src/extensions/registry.ts unless WEBUI_EXTENSION_REGISTRY is set', () => {
    const root = path.resolve(__dirname, '..', '..');
    const resolved = resolveExtensionRegistry({}, root);
    expect(resolved.registryPath).toBe(path.resolve(root, 'src', 'extensions', 'registry.ts'));
    expect(resolved.external).toBe(false);
    expect(extensionAliases(resolved)[0]).toEqual({ find: '@ext/registry', replacement: resolved.registryPath });

    const custom = resolveExtensionRegistry({ [EXTENSION_REGISTRY_ENV]: '/opt/private/webui/registry.ts' }, root);
    expect(custom.registryPath).toBe('/opt/private/webui/registry.ts');
    expect(custom.external).toBe(true);

    const relative = resolveExtensionRegistry({ [EXTENSION_REGISTRY_ENV]: 'ext/registry.ts' }, root);
    expect(relative.registryPath).toBe(path.resolve(root, 'ext', 'registry.ts'));
    expect(relative.external).toBe(false);
  });

  it('omits the channel query parameter when the registry sets no updateChannel', async () => {
    const client = await import('../api/client');
    const get = vi.spyOn(client.default, 'get').mockResolvedValue({ data: {} } as never);
    const { checkUpdate } = await import('../api/update');

    await checkUpdate('zh-CN', registry.updateChannel);
    expect(get).toHaveBeenCalledTimes(1);
    const [, config] = get.mock.calls[0];
    expect((config as { params: Record<string, unknown> }).params).not.toHaveProperty('channel');

    await checkUpdate('zh-CN', 'private-channel');
    const [, custom] = get.mock.calls[1];
    expect((custom as { params: Record<string, unknown> }).params).toMatchObject({ channel: 'private-channel' });
    get.mockRestore();
  });
});
