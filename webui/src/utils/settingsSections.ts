import { useEffect, useMemo, useReducer } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Archive,
  Mic,
  ScrollText,
  Settings as SettingsIcon,
  Shield,
  UserCog,
  type LucideIcon,
} from 'lucide-react';
import registry, { type ExtensionSettingsSection } from '@ext/registry';
import { useAuth } from '@/contexts/AuthContext';
import { useProductName } from '@/contexts/ProductNameContext';

/** Sections the open product ships. An extension adds its own ids on top. */
export type SettingsSectionId = string;

export const BUILT_IN_SETTINGS_SECTION_IDS: readonly SettingsSectionId[] = [
  'preferences',
  'archived-data',
  'account',
  'system-logs',
  'network-proxy',
  'voice-input',
] as const;

export function isSettingsSectionId(value: string | undefined): value is SettingsSectionId {
  if (!value) return false;
  return BUILT_IN_SETTINGS_SECTION_IDS.includes(value) || registry.settingsSections.some((s) => s.id === value);
}

export interface SettingsSection {
  id: SettingsSectionId;
  name: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  /** Rendered by a registered extension rather than a bundled page. */
  load?: ExtensionSettingsSection['load'];
  /** i18n namespaces the extension page wants preloaded. */
  i18nNamespaces?: ExtensionSettingsSection['i18nNamespaces'];
  /** Resolves false to hide the section for this install. */
  gate?: ExtensionSettingsSection['gate'];
}

export interface SettingsGroup {
  id: 'preferences' | 'data' | 'system';
  name: string;
  items: SettingsSection[];
}

// Gate results are shared by every hook instance (the layout's top bar and
// the settings page both mount it), so a registered section's check runs once
// per trigger instead of once per instance; `refreshOn` events re-run it.
const gateResults: Record<string, boolean> = {};
const gateListeners = new Set<() => void>();
let gateRun: Promise<void> | null = null;
// A refresh event that lands while a check is in flight must not be dropped:
// that check asked about the state before the event (a licence that just got
// activated, say), so one more round runs once it settles.
let rerunRequested = false;
let gatesSettled = registry.settingsSections.every((section) => !section.gate);

function runGates({ refresh = false }: { refresh?: boolean } = {}): Promise<void> {
  if (gateRun) {
    // A mount just needs the answers of the check already running; only a
    // refresh event asks for a fresh round afterwards.
    if (refresh) rerunRequested = true;
    return gateRun;
  }
  const gated = registry.settingsSections.filter((section) => section.gate);
  if (gated.length === 0) {
    gatesSettled = true;
    return Promise.resolve();
  }
  gateRun = Promise.all(
    gated.map((section) => section.gate!.check()
      .then((ok) => { gateResults[section.id] = ok; })
      .catch(() => { gateResults[section.id] = false; })
      .finally(() => { gateListeners.forEach((listener) => listener()); })),
  ).then(() => {
    gatesSettled = true;
    gateRun = null;
    gateListeners.forEach((listener) => listener());
    if (rerunRequested) {
      rerunRequested = false;
      void runGates();
    }
  });
  return gateRun;
}

// The refresh events are subscribed once for all hook instances: with one
// listener per instance, a single event asked for one round per instance.
const refreshEvents = new Set<string>(
  registry.settingsSections.flatMap((section) => section.gate?.refreshOn ?? []),
);
const onRefreshEvent = () => { void runGates({ refresh: true }); };

function subscribe(listener: () => void): () => void {
  if (gateListeners.size === 0) {
    refreshEvents.forEach((event) => window.addEventListener(event, onRefreshEvent));
  }
  gateListeners.add(listener);
  return () => {
    gateListeners.delete(listener);
    if (gateListeners.size === 0) {
      refreshEvents.forEach((event) => window.removeEventListener(event, onRefreshEvent));
    }
  };
}

/** Test hook: forget every gate answer so a spec starts from "not asked yet". */
export function resetSettingsGatesForTests(): void {
  for (const key of Object.keys(gateResults)) delete gateResults[key];
  gateRun = null;
  rerunRequested = false;
  gatesSettled = registry.settingsSections.every((section) => !section.gate);
}

/**
 * Menu of the system-settings partition. Shared by the layout sidebar and the
 * settings page so both show exactly the same sections.
 *
 * The open product ships four sections; a private build registers more through
 * `@ext/registry` (each with its own group, page loader and optional gate), and
 * may hide a default one with `hideDefault`.
 */
export function useSettingsSectionGroups(): { groups: SettingsGroup[]; ready: boolean } {
  const { t, i18n } = useTranslation('nav');
  const { user } = useAuth();
  const { productName } = useProductName();
  const isAdmin = user?.role === 'admin';

  // A registered section may gate itself on a check of its own (a licence, a
  // reachable service). Undefined means "not answered yet", which shows the
  // section; only an explicit false hides it.
  const [, rerender] = useReducer((n: number) => n + 1, 0);

  useEffect(() => {
    const unsubscribe = subscribe(rerender);
    void runGates();
    return unsubscribe;
  }, []);

  const groups = useMemo<SettingsGroup[]>(() => {
    const byGroup: Record<SettingsGroup['id'], SettingsSection[]> = {
      preferences: [{ id: 'preferences', name: t('settingsPreferences'), icon: SettingsIcon }],
      data: [{ id: 'archived-data', name: t('archivedData'), icon: Archive }],
      system: [
        { id: 'account', name: t('accountManagement'), icon: UserCog },
        { id: 'network-proxy', name: t('networkProxy'), icon: Shield, adminOnly: true },
        { id: 'voice-input', name: t('voiceInput'), icon: Mic, adminOnly: true },
        { id: 'system-logs', name: t('systemLog'), icon: ScrollText },
      ],
    };
    for (const section of registry.settingsSections) {
      if (registry.hideDefault?.(section.id)) continue;
      (byGroup[section.group as SettingsGroup['id']] ?? byGroup.system).push({
        id: section.id,
        name: i18n.exists(section.labelKey, { ns: section.ns || 'nav' })
          ? t(section.labelKey, { ns: section.ns || 'nav', productName })
          : section.labelKey,
        icon: section.icon,
        adminOnly: section.adminOnly,
        load: section.load,
        i18nNamespaces: section.i18nNamespaces,
        gate: section.gate,
      });
    }
    return [
      { id: 'preferences', name: t('settingsGroupPreferences'), items: byGroup.preferences },
      { id: 'data', name: t('settingsGroupData'), items: byGroup.data },
      { id: 'system', name: t('settingsGroupSystem'), items: byGroup.system },
    ];
  }, [i18n, productName, t]);

  // Not memoised on purpose: gate results live in module state and change
  // underneath the listener-driven re-render; the filter is a handful of items.
  const visibleGroups = groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        if (registry.hideDefault?.(item.id)) return false;
        if (item.adminOnly && !isAdmin) return false;
        // A gated section stays hidden until its own check passes. Until the
        // check has answered it is shown, so a slow probe does not make the
        // menu jump.
        if (item.gate && gateResults[item.id] === false) return false;
        return true;
      }),
    }))
    .filter((group) => group.items.length > 0);

  return { groups: visibleGroups, ready: gatesSettled };
}
