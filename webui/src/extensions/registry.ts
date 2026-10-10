import type { ComponentType } from 'react';
import { type LucideIcon } from 'lucide-react';

/**
 * Build-time WebUI extension registry.
 *
 * This module is the default (empty) implementation. A private build points the
 * `@ext/registry` alias at its own implementation of {@link WebUIExtensionRegistry}
 * through the `WEBUI_EXTENSION_REGISTRY` environment variable (see
 * `vite.config.ts`). The application only ever reads the registry through the
 * alias, so it never knows which implementation it was built with.
 */

export type ExtensionModule = { default: ComponentType<any> };
export type ExtensionLoader = () => Promise<ExtensionModule>;

export interface ExtensionRoute {
  /** Route path relative to the router root, e.g. `"reports/callback"`. */
  path: string;
  /** Lazy loader for the page component (code-split like every other page). */
  load: ExtensionLoader;
  /** Redirect non-admin users to `/` instead of rendering. */
  adminOnly?: boolean;
  /** i18n namespaces to preload before the page renders. */
  i18nNamespaces?: string[];
  /** `app` (default) renders inside the main layout; `bare` renders without it. */
  layout?: 'app' | 'bare';
}

export interface ExtensionNavItem {
  id: string;
  href: string;
  /** Translation key for the label; interpolates `{{productName}}`. */
  labelKey: string;
  /** Namespace of `labelKey` (default `nav`). Must be bundled synchronously for sidebar items. */
  ns?: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  order?: number;
  /** `sidebar` (default) appends to a sidebar section; `accountMenu` adds an entry to the account dropdown. */
  placement?: 'sidebar' | 'accountMenu';
  /** Sidebar section to append to (default `agentHub`). */
  section?: 'home' | 'aiWorkbench' | 'sceneWorkspaces' | 'agentHub';
}

export interface ExtensionSettingsGate {
  /** Resolve `false` to hide the section (evaluated once per settings visit). */
  check: () => Promise<boolean>;
  /** Window event names that trigger a re-check while the settings page is open. */
  refreshOn?: string[];
}

export interface ExtensionSettingsSection {
  /** Section id, also the URL segment (`/settings/<id>`). Reusing a default id replaces that default section. */
  id: string;
  group: 'preferences' | 'data' | 'system';
  labelKey: string;
  ns?: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  order?: number;
  load: ExtensionLoader;
  i18nNamespaces?: string[];
  gate?: ExtensionSettingsGate;
}

export type SlotName = 'session.settings' | 'channel.settings' | 'layout.version' | 'layout.notifications' | 'chat.composer';

/** Optional composer tools can hold the draft while an asynchronous input is active. */
export interface ChatComposerSlotContext extends Record<string, unknown> {
  sessionId: string | null | undefined;
  input: string;
  setInput: (value: string) => void;
  disabled: boolean;
  setBusy: (owner: string, busy: boolean) => void;
}

export interface ExtensionSlotComponent {
  id: string;
  slot: SlotName;
  component: ComponentType<{ context: Record<string, unknown> }>;
  order?: number;
}

export type ExtensionI18nResource =
  | Record<string, unknown>
  | (() => Promise<{ default: Record<string, unknown> }>);

export interface ExtensionI18nResources {
  [language: string]: { [namespace: string]: ExtensionI18nResource };
}

export interface WebUIExtensionRegistry {
  routes: ExtensionRoute[];
  navItems: ExtensionNavItem[];
  settingsSections: ExtensionSettingsSection[];
  slots: ExtensionSlotComponent[];
  /**
   * Extra locale resources. A namespace the application bundles up-front
   * (`common`, `nav`, `home`, `auth`, `webuiContractPage`) should be given as a
   * plain object so it is merged before first paint; other namespaces may be
   * lazy loaders and are merged over the built-in bundle when that namespace
   * loads.
   */
  i18n: ExtensionI18nResources;
  /**
   * Update channel reported to `/api/update/check`. Left unset, the application
   * asks for the default channel.
   */
  updateChannel?: string;
  /** Return `true` to hide a default nav item or settings section by id. */
  hideDefault?: (id: string) => boolean;
}

const registry: WebUIExtensionRegistry = {
  routes: [],
  navItems: [],
  settingsSections: [],
  slots: [],
  i18n: {},
};

export default registry;
