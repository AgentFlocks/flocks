import path from 'path';
import { describe, expect, it } from 'vitest';
import { EXTENSION_REGISTRY_ENV, extensionAliases, resolveExtensionRegistry } from './extensionRegistry';

const root = path.resolve('/srv/webui');

describe('resolveExtensionRegistry', () => {
  it('falls back to the bundled empty registry', () => {
    const resolved = resolveExtensionRegistry({}, root);
    expect(resolved.registryPath).toBe(path.join(root, 'src', 'extensions', 'registry.ts'));
    expect(resolved.registryDir).toBe(path.join(root, 'src', 'extensions'));
    expect(resolved.external).toBe(false);
  });

  it('uses an absolute registry path from the environment', () => {
    const external = path.resolve('/srv/private/overlay/registry.ts');
    const resolved = resolveExtensionRegistry({ [EXTENSION_REGISTRY_ENV]: external }, root);
    expect(resolved.registryPath).toBe(external);
    expect(resolved.registryDir).toBe(path.dirname(external));
    expect(resolved.external).toBe(true);
  });

  it('resolves a relative registry path against the webui root', () => {
    const resolved = resolveExtensionRegistry({ [EXTENSION_REGISTRY_ENV]: 'src/extensions/custom.ts' }, root);
    expect(resolved.registryPath).toBe(path.join(root, 'src', 'extensions', 'custom.ts'));
    expect(resolved.external).toBe(false);
  });

  it('ignores blank values', () => {
    const resolved = resolveExtensionRegistry({ [EXTENSION_REGISTRY_ENV]: '   ' }, root);
    expect(resolved.external).toBe(false);
  });

  it('builds aliases with the exact registry match first', () => {
    const resolved = resolveExtensionRegistry({}, root);
    const aliases = extensionAliases(resolved);
    expect(aliases[0]).toEqual({ find: '@ext/registry', replacement: resolved.registryPath });
    expect(aliases[1].find).toBeInstanceOf(RegExp);
    expect('@ext/api/thing'.replace(aliases[1].find as RegExp, aliases[1].replacement)).toBe(
      path.join(resolved.registryDir, 'api', 'thing'),
    );
  });
});
