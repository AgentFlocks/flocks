import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

/**
 * The navigation is split by workspace: the built-in Agent workspace and one
 * workspace per enabled scene suite (SOC 工作区, 代码漏洞审计工作区, ...). The
 * header switcher picks the workspace and the sidebar shows its menu.
 *
 * System management (/settings) and the scene manager (/scenes) belong to no
 * workspace: while they are open the sidebar keeps the workspace they were
 * opened from.
 */
export type NavWorkspaceId = string;

export const AGENT_WORKSPACE: NavWorkspaceId = 'agent';
const SCENE_WORKSPACE_PREFIX = 'scene:';

export const AGENT_WORKSPACE_DEFAULT_PATH = '/';
export const SETTINGS_DEFAULT_PATH = '/settings/preferences';
export const SCENE_SUITES_PATH = '/scenes/suites';

const WORKSPACE_PATHS_KEY = 'flocks_layout_workspace_paths';
const CURRENT_WORKSPACE_KEY = 'flocks_layout_current_workspace';
const WORKSPACE_ROUTE_RE = /^\/contracts\/webui\/workspaces\/([^/]+)(?:\/|$)/;

export function sceneNavWorkspace(workspaceId: string): NavWorkspaceId {
  return `${SCENE_WORKSPACE_PREFIX}${workspaceId}`;
}

/** The scene workspace id of a navigation workspace, or null for Agent. */
export function sceneWorkspaceIdOf(workspace: NavWorkspaceId): string | null {
  return workspace.startsWith(SCENE_WORKSPACE_PREFIX) ? workspace.slice(SCENE_WORKSPACE_PREFIX.length) || null : null;
}

export function isSettingsPath(pathname: string): boolean {
  return pathname === '/settings' || pathname.startsWith('/settings/');
}

export function isSceneSuitesPath(pathname: string): boolean {
  return pathname === '/scenes' || pathname.startsWith('/scenes/');
}

/** Workspace id of a `/contracts/webui/workspaces/<id>/...` route, else null. */
export function workspaceIdFromPath(pathname: string): string | null {
  return pathname.match(WORKSPACE_ROUTE_RE)?.[1] ?? null;
}

/** Scene workspaces offered by the switcher: installed, enabled and not locked. */
export function isOpenSceneWorkspace(workspace: WebUIContractWorkspaceListItem): boolean {
  return workspace.placement === 'sceneWorkspace' && workspace.enabled && !workspace.locked;
}

export type NavRoute =
  | { kind: 'workspace'; workspace: NavWorkspaceId }
  | { kind: 'settings' }
  | { kind: 'scenes' };

/**
 * The owner of a route. `workspaces` is the loaded workspace list; leave it
 * out (or pass null) while it is unknown: a workspace route then counts as a
 * scene, which it is unless the workspace puts its pages in the Agent menu.
 */
export function resolveNavRoute(
  pathname: string,
  workspaces?: readonly WebUIContractWorkspaceListItem[] | null,
): NavRoute {
  if (isSettingsPath(pathname)) return { kind: 'settings' };
  if (isSceneSuitesPath(pathname)) return { kind: 'scenes' };
  const workspaceId = workspaceIdFromPath(pathname);
  if (workspaceId) {
    const workspace = workspaces?.find((item) => item.id === workspaceId);
    // Workbench-placed workspaces render their pages under the Agent menu.
    if (workspace?.placement === 'aiWorkbench') return { kind: 'workspace', workspace: AGENT_WORKSPACE };
    return { kind: 'workspace', workspace: sceneNavWorkspace(workspaceId) };
  }
  // Custom pages outside any workspace sit in the Agent menu.
  return { kind: 'workspace', workspace: AGENT_WORKSPACE };
}

export type NavWorkspacePaths = Record<NavWorkspaceId, string>;

function isInternalPath(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//');
}

/** Last visited path per workspace, so switching back lands where it was left. */
export function readWorkspacePaths(): NavWorkspacePaths {
  try {
    const raw = window.localStorage.getItem(WORKSPACE_PATHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: NavWorkspacePaths = {};
    for (const [workspace, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!isInternalPath(value)) continue;
      const route = resolveNavRoute(value.split('?')[0]);
      if (route.kind !== 'workspace') continue;
      const sceneId = sceneWorkspaceIdOf(workspace);
      if (sceneId !== null) {
        // A scene only ever stores one of its own pages.
        if (workspaceIdFromPath(value) === sceneId) result[workspace] = value;
      } else if (workspace === AGENT_WORKSPACE) {
        // Placement of a workspace route is only known once navigation loads;
        // Layout checks stored paths against the current menu anyway.
        result[workspace] = value;
      }
    }
    return result;
  } catch {
    return {};
  }
}

export function saveWorkspacePaths(paths: NavWorkspacePaths): void {
  try {
    window.localStorage.setItem(WORKSPACE_PATHS_KEY, JSON.stringify(paths));
  } catch {
    // Private mode or a full quota: workspace memory is a convenience only.
  }
}

/** The workspace the sidebar showed last, kept across system views and reloads. */
export function readCurrentWorkspace(): NavWorkspaceId {
  try {
    const value = window.localStorage.getItem(CURRENT_WORKSPACE_KEY);
    if (value === AGENT_WORKSPACE || (value && sceneWorkspaceIdOf(value))) return value;
  } catch {
    // Fall through to the default.
  }
  return AGENT_WORKSPACE;
}

export function saveCurrentWorkspace(workspace: NavWorkspaceId): void {
  try {
    window.localStorage.setItem(CURRENT_WORKSPACE_KEY, workspace);
  } catch {
    // Convenience only.
  }
}
