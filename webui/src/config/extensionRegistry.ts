import path from 'path';

/**
 * Environment variable naming the extension registry module used for a build.
 * Absolute path, or a path relative to the webui root. When unset the default
 * (empty) registry at `src/extensions/registry.ts` is used.
 */
export const EXTENSION_REGISTRY_ENV = 'WEBUI_EXTENSION_REGISTRY';

export interface ResolvedExtensionRegistry {
  /** Absolute path of the registry module. */
  registryPath: string;
  /** Directory the `@ext/*` alias maps to. */
  registryDir: string;
  /** True when the registry lives outside the webui root. */
  external: boolean;
}

export function resolveExtensionRegistry(
  env: Record<string, string | undefined>,
  root: string,
): ResolvedExtensionRegistry {
  const configured = (env[EXTENSION_REGISTRY_ENV] || '').trim();
  const defaultPath = path.resolve(root, 'src', 'extensions', 'registry.ts');
  if (!configured) {
    return { registryPath: defaultPath, registryDir: path.dirname(defaultPath), external: false };
  }
  const registryPath = path.isAbsolute(configured) ? path.normalize(configured) : path.resolve(root, configured);
  const relative = path.relative(root, registryPath);
  const external = relative.startsWith('..') || path.isAbsolute(relative);
  return { registryPath, registryDir: path.dirname(registryPath), external };
}

/** Alias entries for Vite / Vitest `resolve.alias` (array form keeps the exact match first). */
export function extensionAliases(resolved: ResolvedExtensionRegistry): Array<{ find: string | RegExp; replacement: string }> {
  return [
    { find: '@ext/registry', replacement: resolved.registryPath },
    { find: /^@ext\//, replacement: `${resolved.registryDir}${path.sep}` },
  ];
}

/**
 * Packages that must resolve to a single copy even when the registry module
 * lives outside the webui tree (React hooks and i18n break with two instances).
 */
export const EXTENSION_DEDUPE_PACKAGES = ['react', 'react-dom', 'react-router-dom', 'react-i18next', 'i18next'];
