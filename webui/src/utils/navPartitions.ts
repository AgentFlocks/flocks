/**
 * The three fixed partitions of the top bar. Each partition owns a set of
 * routes and shows only its own menu in the sidebar.
 */
export type NavPartitionId = 'agent' | 'scene' | 'settings';

export const NAV_PARTITION_IDS: readonly NavPartitionId[] = ['agent', 'scene', 'settings'] as const;

export const AGENT_PARTITION_DEFAULT_PATH = '/';
export const SETTINGS_PARTITION_DEFAULT_PATH = '/settings/preferences';

const PARTITION_PATHS_KEY = 'flocks_layout_partition_paths';

function isSettingsPath(pathname: string): boolean {
  return pathname === '/settings' || pathname.startsWith('/settings/');
}

/** Scene workspaces (SOC today), their suite manager and the custom pages. */
function isScenePath(pathname: string): boolean {
  return pathname === '/scenes'
    || pathname.startsWith('/scenes/')
    || pathname === '/contracts/webui'
    || pathname.startsWith('/contracts/webui/')
    || pathname.startsWith('/user-defined-pages/');
}

export function resolveNavPartition(pathname: string): NavPartitionId {
  if (isSettingsPath(pathname)) return 'settings';
  if (isScenePath(pathname)) return 'scene';
  return 'agent';
}

export type NavPartitionPaths = Partial<Record<NavPartitionId, string>>;

/** Last visited path per partition, so switching back lands where you left. */
export function readPartitionPaths(): NavPartitionPaths {
  try {
    const raw = window.localStorage.getItem(PARTITION_PATHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: NavPartitionPaths = {};
    for (const id of NAV_PARTITION_IDS) {
      const value = (parsed as Record<string, unknown>)[id];
      // Only keep a stored path that still belongs to its own partition.
      if (typeof value === 'string' && value.startsWith('/') && resolveNavPartition(value) === id) {
        result[id] = value;
      }
    }
    return result;
  } catch {
    return {};
  }
}

export function savePartitionPaths(paths: NavPartitionPaths): void {
  try {
    window.localStorage.setItem(PARTITION_PATHS_KEY, JSON.stringify(paths));
  } catch {
    // Private mode or a full quota: partition memory is a convenience only.
  }
}
