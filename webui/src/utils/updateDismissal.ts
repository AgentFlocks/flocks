import type { VersionInfo } from '@/api/update';

export const UPDATE_DISMISSED_KEY = 'flocks-update-dismissed';

function normalizeVersionPart(value?: string | null): string {
  const normalized = (value || '').trim();
  return normalized || 'none';
}

export function buildUpdateDismissalKey(info?: VersionInfo | null): string | null {
  if (!info) {
    return null;
  }

  const edition = info.edition || 'flocks';
  if (edition === 'flockspro') {
    return [
      edition,
      normalizeVersionPart(info.latest_bundle_version || info.latest_version),
      normalizeVersionPart(info.latest_core_version),
      normalizeVersionPart(info.latest_pro_component_version),
    ].join(':');
  }

  const latestVersion = info.latest_version || info.current_version;
  if (!latestVersion) {
    return null;
  }
  return `${edition}:${normalizeVersionPart(latestVersion)}`;
}

export function isUpdateDismissed(info: VersionInfo | null | undefined, dismissedValue: string | null): boolean {
  if (!dismissedValue) {
    return false;
  }

  const dismissalKey = buildUpdateDismissalKey(info);
  if (dismissalKey && dismissedValue === dismissalKey) {
    return true;
  }

  // Keep old OSS dismissals working; Pro uses component-aware keys to avoid
  // hiding component-only bundle updates behind the previous current_version.
  return (info?.edition || 'flocks') === 'flocks' && dismissedValue === info?.current_version;
}
