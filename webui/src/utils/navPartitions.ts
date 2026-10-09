import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

/**
 * The fixed partitions of the top bar. `scene` is the SOC workspace: every
 * enabled scene suite lives inside it, so a new scene never adds a tab, and
 * the tab goes away while no scene suite is enabled.
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

function isSceneSuitesPath(pathname: string): boolean {
  return pathname === '/scenes' || pathname.startsWith('/scenes/');
}

/** Custom pages that do not belong to a workspace (built with the page builder skill). */
function isCustomPagePath(pathname: string): boolean {
  return pathname === '/contracts/webui'
    || pathname.startsWith('/contracts/webui/')
    || pathname.startsWith('/user-defined-pages/');
}

/** Workspace id of a `/contracts/webui/workspaces/<id>/...` route, else null. */
export function workspaceIdFromPath(pathname: string): string | null {
  return pathname.match(WORKSPACE_ROUTE_RE)?.[1] ?? null;
}

/**
 * The SOC workspace tab exists only while a scene suite is enabled; custom
 * pages sit next to the scenes then and fall back to the Agent menu otherwise.
 */
export function hasEnabledSceneWorkspace(workspaces: readonly WebUIContractWorkspaceListItem[]): boolean {
  return workspaces.some((workspace) => workspace.placement === 'sceneWorkspace' && workspace.enabled);
}

/**
 * `workspaces` is the loaded workspace list; leave it out (or pass null) while
 * it is unknown. The suite manager and the custom pages then stay with the SOC
 * workspace, as in a default install where a scene is enabled, so a reload does
 * not flash the Agent menu. Once no scene is enabled they belong to Agent.
 */
export function resolveNavPartition(
  pathname: string,
  workspaces?: readonly WebUIContractWorkspaceListItem[] | null,
): NavPartitionId {
  const workspaceId = workspaceIdFromPath(pathname);
  if (workspaceId) {
    const workspace = workspaces?.find((item) => item.id === workspaceId);
    // Workbench-placed workspaces render their pages under the Agent menu.
    return workspace?.placement === 'aiWorkbench' ? 'agent' : 'scene';
  }
  if (isSettingsPath(pathname)) return 'settings';
  if (isSceneSuitesPath(pathname) || isCustomPagePath(pathname)) {
    return !workspaces || hasEnabledSceneWorkspace(workspaces) ? 'scene' : 'agent';
  }
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
      // Workspace placement and scene state are unknown until navigation
      // loads: a workspace route stored under Agent may be a workbench page,
      // and a custom page belongs to the SOC workspace or to Agent depending on
      // whether a scene is enabled. Layout validates every stored path against
      // the partition's current entries anyway.
      const pendingAgentWorkspace = id === 'agent' && workspaceIdFromPath(pathname) !== null;
      const pendingCustomPage = (id === 'agent' || id === 'scene')
        && isCustomPagePath(pathname)
        && workspaceIdFromPath(pathname) === null;
      if (inferredPartition === id || pendingAgentWorkspace || pendingCustomPage) {
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
