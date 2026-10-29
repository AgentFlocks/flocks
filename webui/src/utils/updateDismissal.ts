import { DEFAULT_UPDATE_CHANNEL, type VersionInfo } from '@/api/update';

export const UPDATE_DISMISSED_KEY = 'flocks-update-dismissed';

function normalizeVersionPart(value?: string | null): string {
  const normalized = (value || '').trim();
  return normalized || 'none';
}

/** `<channel>:<latest version>`; the channel keeps dismissals of different update channels apart. */
export function buildUpdateDismissalKey(info?: VersionInfo | null): string | null {
  if (!info) {
    return null;
  }

  const latestVersion = info.latest_version || info.current_version;
  if (!latestVersion) {
    return null;
  }
  return `${info.channel || DEFAULT_UPDATE_CHANNEL}:${normalizeVersionPart(latestVersion)}`;
}

export function isUpdateDismissed(info: VersionInfo | null | undefined, dismissedValue: string | null): boolean {
  if (!dismissedValue) {
    return false;
  }

  const dismissalKey = buildUpdateDismissalKey(info);
  if (dismissalKey && dismissedValue === dismissalKey) {
    return true;
  }

  // Older releases stored either the bare current version or a differently
  // prefixed `<prefix>:<version>` key; keep those dismissals valid for the same version.
  const latestVersion = normalizeVersionPart(info?.latest_version || info?.current_version);
  const separator = dismissedValue.indexOf(':');
  const dismissedVersion = separator === -1 ? dismissedValue : dismissedValue.slice(separator + 1);
  return dismissedVersion === latestVersion || dismissedValue === info?.current_version;
}
