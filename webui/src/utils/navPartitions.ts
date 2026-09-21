import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

/**
 * The fixed partitions of the top bar. `scene` is the SOC workspace: every
 * installed scene suite lives inside it, so a new scene never adds a tab.
 */
export type NavPartitionId = 'agent' | 'scene' | 'settings';

export const NAV_PARTITION_IDS: readonly NavPartitionId[] = ['agent', 'scene', 'settings'] as const;

export const AGENT_PARTITION_DEFAULT_PATH = '/';
export const SETTINGS_PARTITION_DEFAULT_PATH = '/settings/preferences';
export const SCENE_SUITES_PATH = '/scenes/suites';

const PARTITION_PATHS_KEY = 'flocks_layout_partition_paths';
const WORKSPACE_ROUTE_RE = /^\/contracts\/webui\/workspaces\/([^/]+)(?:\/|$)/;

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

/** Workspace id of a `/contracts/webui/workspaces/<id>/...` route, else null. */
export function workspaceIdFromPath(pathname: string): string | null {
  return pathname.match(WORKSPACE_ROUTE_RE)?.[1] ?? null;
}

export function resolveNavPartition(
  pathname: string,
  workspaces: readonly WebUIContractWorkspaceListItem[] = [],
): NavPartitionId {
  const workspaceId = workspaceIdFromPath(pathname);
  if (workspaceId) {
    const workspace = workspaces.find((item) => item.id === workspaceId);
    // Workbench-placed workspaces render their pages under the Agent menu.
    return workspace?.placement === 'aiWorkbench' ? 'agent' : 'scene';
  }
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
      if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) continue;
      const pathname = value.split('?')[0];
      const inferredPartition = resolveNavPartition(pathname);
      // Workspace placement is unknown until navigation loads: a workspace
      // route stored under Agent may be a workbench page. Layout validates
      // every stored path against the partition's current entries anyway.
      const pendingAgentWorkspace = id === 'agent' && workspaceIdFromPath(pathname) !== null;
      if (inferredPartition === id || pendingAgentWorkspace) {
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
