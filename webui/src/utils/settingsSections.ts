import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Archive,
  ArrowUpCircle,
  ScrollText,
  Settings as SettingsIcon,
  Shield,
  ShieldCheck,
  UserCog,
  type LucideIcon,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useProductName } from '@/contexts/ProductNameContext';
import { flocksproUsersApi } from '@/api/flocksproUsers';

export type SettingsSectionId =
  | 'preferences'
  | 'archived-data'
  | 'account'
  | 'security-config'
  | 'system-logs'
  | 'audit-logs'
  | 'flockspro';

export const SETTINGS_SECTION_IDS: readonly SettingsSectionId[] = [
  'preferences',
  'archived-data',
  'account',
  'security-config',
  'system-logs',
  'audit-logs',
  'flockspro',
] as const;

export function isSettingsSectionId(value: string | undefined): value is SettingsSectionId {
  return SETTINGS_SECTION_IDS.includes(value as SettingsSectionId);
}

export interface SettingsSection {
  id: SettingsSectionId;
  name: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  requiresFlockspro?: boolean;
}

export interface SettingsGroup {
  id: 'preferences' | 'data' | 'system';
  name: string;
  items: SettingsSection[];
}

/**
 * Menu of the system-settings partition. Shared by the layout sidebar and the
 * settings page so both show exactly the same sections.
 */
export function useSettingsSectionGroups(): { groups: SettingsGroup[]; ready: boolean } {
  const { t } = useTranslation('nav');
  const { user } = useAuth();
  const { proProductName } = useProductName();
  const isAdmin = user?.role === 'admin';
  const [capabilityReady, setCapabilityReady] = useState(false);
  const [hasFlockspro, setHasFlockspro] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!isAdmin) {
      setHasFlockspro(false);
      setCapabilityReady(true);
      return () => {
        cancelled = true;
      };
    }

    setCapabilityReady(false);
    const refreshCapability = () => {
      void flocksproUsersApi.hasCapability()
        .then((ok) => {
          if (!cancelled) setHasFlockspro(ok);
        })
        .catch(() => {
          if (!cancelled) setHasFlockspro(false);
        })
        .finally(() => {
          if (!cancelled) setCapabilityReady(true);
        });
    };

    refreshCapability();
    window.addEventListener('flockspro-license-status-changed', refreshCapability);
    return () => {
      cancelled = true;
      window.removeEventListener('flockspro-license-status-changed', refreshCapability);
    };
  }, [isAdmin]);

  const groups = useMemo<SettingsGroup[]>(
    () => [
      {
        id: 'preferences',
        name: t('settingsGroupPreferences'),
        items: [
          { id: 'preferences', name: t('settingsPreferences'), icon: SettingsIcon },
        ],
      },
      {
        id: 'data',
        name: t('settingsGroupData'),
        items: [
          { id: 'archived-data', name: t('archivedData'), icon: Archive },
        ],
      },
      {
        id: 'system',
        name: t('settingsGroupSystem'),
        items: [
          { id: 'account', name: t('accountManagement'), icon: UserCog },
          { id: 'security-config', name: t('securityConfig'), icon: Shield, adminOnly: true, requiresFlockspro: true },
          { id: 'system-logs', name: t('systemLog'), icon: ScrollText },
          { id: 'audit-logs', name: t('auditLogs'), icon: ShieldCheck, adminOnly: true, requiresFlockspro: true },
          { id: 'flockspro', name: proProductName, icon: ArrowUpCircle, adminOnly: true },
        ],
      },
    ],
    [proProductName, t],
  );

  const visibleGroups = useMemo(
    () => groups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => {
          if (item.adminOnly && !isAdmin) return false;
          if (item.requiresFlockspro && capabilityReady && !hasFlockspro) return false;
          return true;
        }),
      }))
      .filter((group) => group.items.length > 0),
    [capabilityReady, groups, hasFlockspro, isAdmin],
  );

  return { groups: visibleGroups, ready: capabilityReady };
}
