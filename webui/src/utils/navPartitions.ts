import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

/** Scene tabs are identified by the installed workspace, never a fixed label. */
export type NavPartitionId = 'agent' | 'scene' | 'settings' | `workspace:${string}`;

export function workspacePartitionId(workspaceId: string): NavPartitionId {
  return `workspace:${workspaceId}`;
}

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

export function resolveNavPartition(
  pathname: string,
  workspaces: readonly WebUIContractWorkspaceListItem[] = [],
): NavPartitionId {
  const workspace = workspaces.find((item) => pathname === item.route || pathname.startsWith(`${item.route}/`));
  if (workspace) return workspace.placement === 'aiWorkbench' ? 'agent' : workspacePartitionId(workspace.id);
  if (isSettingsPath(pathname)) return 'settings';
  const workspaceMatch = pathname.match(/^\/contracts\/webui\/workspaces\/([^/]+)(?:\/|$)/);
  if (workspaceMatch) return workspacePartitionId(workspaceMatch[1]);
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
    for (const [key, value] of Object.entries(parsed)) {
      if (!['agent', 'scene', 'settings'].includes(key) && !/^workspace:[^/]+$/.test(key)) continue;
      const id = key as NavPartitionId;
      if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) continue;
      const inferredPartition = resolveNavPartition(value.split('?')[0]);
      // Workspace placement is unknown until navigation loads. Keep Agent's
      // workspace candidate too; Layout validates it against that partition's
      // current available hrefs before navigating, including after uninstall.
      const pendingAgentWorkspace = id === 'agent' && inferredPartition.startsWith('workspace:');
      if (inferredPartition === id || id.startsWith('workspace:') || pendingAgentWorkspace) {
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
