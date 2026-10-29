import registry from '@ext/registry';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

/**
 * The top navigation separates the workbench, enabled scene suites and Agent
 * workshop. Each module owns its sidebar and remembers its last visited page.
 *
 * System settings has its own sidebar. Scene management (/scenes) is a
 * separate header entry and does not replace any module's remembered page.
 */
export type NavWorkspaceId = string;

// Keep the legacy id for existing workbench navigation preferences.
export const AGENT_WORKSPACE: NavWorkspaceId = 'agent';
export const AGENT_WORKSHOP: NavWorkspaceId = 'agent-workshop';
const SCENE_WORKSPACE_PREFIX = 'scene:';
export const SOC_WORKSPACE_ID = 'soc_ui';
export const CODE_AUDIT_WORKSPACE_ID = 'code_audit_ui';

export const AGENT_WORKSPACE_DEFAULT_PATH = '/';
export const AGENT_WORKSHOP_DEFAULT_PATH = '/agents';
const WORKSHOP_PATHS = ['/agents', '/skills', '/workflows', '/tools', '/devices', '/models', '/hub', '/channels'];
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
  return pathname === '/settings' || pathname.startsWith('/settings/')
    // Extension routes that belong to settings (a sign-in callback, say) stay
    // out of every module's remembered page.
    || registry.routes.some((route) => {
      if (route.area !== 'settings') return false;
      const base = `/${route.path.replace(/^\/+|\/+$/g, '')}`;
      return pathname === base || pathname.startsWith(`${base}/`);
    });
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
 * `workshopPaths` are the pages extensions add to the workshop's menu.
 */
export function resolveNavRoute(
  path: string,
  workspaces?: readonly WebUIContractWorkspaceListItem[] | null,
  workshopPaths: readonly string[] = [],
): NavRoute {
  const [pathname, search = ''] = path.split('?');
  if (isSettingsPath(pathname)) return { kind: 'settings' };
  if (isSceneSuitesPath(pathname)) return { kind: 'scenes' };
  const workspaceTab = new URLSearchParams(search).get('tab');
  const under = (base: string) => pathname === base || pathname.startsWith(`${base}/`);
  if (WORKSHOP_PATHS.some(under)
    || workshopPaths.some((href) => under(href.split('?')[0]))
    || (pathname === '/workspace' && (workspaceTab === 'knowledge' || workspaceTab === 'memory'))) {
    return { kind: 'workspace', workspace: AGENT_WORKSHOP };
  }
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

/**
 * Last visited path per workspace, so switching back lands where it was left.
 * `workshopPaths` are the pages extensions add to the workshop's menu (resolveNavRoute).
 */
export function readWorkspacePaths(workshopPaths: readonly string[] = []): NavWorkspacePaths {
  try {
    const raw = window.localStorage.getItem(WORKSPACE_PATHS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: NavWorkspacePaths = {};
    let legacyAuditPath: string | undefined;
    for (const [workspace, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!isInternalPath(value)) continue;
      const route = resolveNavRoute(value, null, workshopPaths);
      if (route.kind !== 'workspace') continue;
      const sceneId = sceneWorkspaceIdOf(workspace);
      if (sceneId !== null) {
        const pageWorkspaceId = workspaceIdFromPath(value.split('?')[0]);
        if (pageWorkspaceId === sceneId) result[workspace] = value;
        // Older versions grouped audit under SOC. Move that memory to audit
        // without overwriting an existing, independent audit page preference.
        else if (sceneId === SOC_WORKSPACE_ID && pageWorkspaceId === CODE_AUDIT_WORKSPACE_ID) legacyAuditPath = value;
      } else if (workspace === AGENT_WORKSPACE || workspace === AGENT_WORKSHOP) {
        // Placement of a workspace route is only known once navigation loads;
        // Layout checks stored paths against the current menu anyway.
        if (route.workspace === workspace || (workspace === AGENT_WORKSPACE && workspaceIdFromPath(value))) {
          result[workspace] = value;
        }
      }
    }
    const auditWorkspace = sceneNavWorkspace(CODE_AUDIT_WORKSPACE_ID);
    if (legacyAuditPath && !result[auditWorkspace]) result[auditWorkspace] = legacyAuditPath;
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
    if (value === AGENT_WORKSPACE || value === AGENT_WORKSHOP || (value && sceneWorkspaceIdOf(value))) return value;
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
