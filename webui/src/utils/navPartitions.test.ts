import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AGENT_WORKSPACE,
  AGENT_WORKSHOP,
  isOpenSceneWorkspace,
  readCurrentWorkspace,
  readWorkspacePaths,
  resolveNavRoute,
  saveCurrentWorkspace,
  saveWorkspacePaths,
  sceneNavWorkspace,
  sceneWorkspaceIdOf,
  workspaceIdFromPath,
} from './navPartitions';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

// An extension's sign-in callback counts as settings; the stock registry has none.
vi.mock('@ext/registry', () => ({
  __esModule: true,
  default: {
    routes: [{ path: 'reports-login/callback', area: 'settings', load: async () => ({ default: () => null }) }],
    navItems: [],
    settingsSections: [],
    slots: [],
    i18n: {},
  },
}));

const scene = (over: Partial<WebUIContractWorkspaceListItem>): WebUIContractWorkspaceListItem => ({
  id: 'soc_ui',
  route: '/contracts/webui/workspaces/soc_ui',
  title: 'SOC',
  icon: 'ShieldCheck',
  order: 10,
  enabled: true,
  placement: 'sceneWorkspace',
  pages: [],
  ...over,
});

describe('navPartitions', () => {
  beforeEach(() => localStorage.clear());

  it('reads the workspace id out of a workspace route', () => {
    expect(workspaceIdFromPath('/contracts/webui/workspaces/soc_ui/soc-alerts')).toBe('soc_ui');
    expect(workspaceIdFromPath('/contracts/webui/workspaces/code_audit_ui')).toBe('code_audit_ui');
    expect(workspaceIdFromPath('/contracts/webui/dash-1')).toBeNull();
    expect(workspaceIdFromPath('/sessions')).toBeNull();
  });

  it('round-trips a scene workspace id', () => {
    expect(sceneWorkspaceIdOf(sceneNavWorkspace('soc_ui'))).toBe('soc_ui');
    expect(sceneWorkspaceIdOf(AGENT_WORKSPACE)).toBeNull();
  });

  it('keeps settings and scene management separate from workspaces', () => {
    expect(resolveNavRoute('/settings')).toEqual({ kind: 'settings' });
    expect(resolveNavRoute('/settings/audit-logs')).toEqual({ kind: 'settings' });
    for (const path of ['/scenes', '/scenes/suites', '/scenes/suites?workspace=code_audit_ui']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'scenes' });
    }
  });

  it('classifies an extension route registered under settings as settings', () => {
    for (const path of ['/reports-login/callback', '/reports-login/callback?login_id=test-login', '/reports-login/callback/done']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'settings' });
    }
    // Only that route: a sibling path is an ordinary page.
    expect(resolveNavRoute('/reports-login')).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/reports-login/callback?login_id=test-login' });
    expect(readWorkspacePaths()).toEqual({});
  });

  it('routes ordinary pages to the Agent workspace', () => {
    for (const path of ['/', '/sessions', '/tasks', '/workspace', '/workspace?tab=files', '/contracts/webui/dash-1']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    }
    // A path that merely starts with the same letters is not settings.
    expect(resolveNavRoute('/settings-export')).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
  });

  it('routes capability pages and knowledge or memory tabs to the workshop', () => {
    for (const path of ['/agents', '/agents/a', '/skills', '/workflows/w1', '/tools', '/devices', '/models', '/hub', '/workspace?tab=knowledge', '/workspace?tab=memory']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'workspace', workspace: AGENT_WORKSHOP });
    }
    expect(resolveNavRoute('/agents-extra')).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
  });

  it('routes the pages extensions list in the workshop menu to the workshop', () => {
    const workshopPaths = ['/ext/agent-hub', '/ext/reports?view=all'];
    for (const path of ['/ext/agent-hub', '/ext/agent-hub/item', '/ext/reports', '/ext/reports?view=mine']) {
      expect(resolveNavRoute(path, null, workshopPaths)).toEqual({ kind: 'workspace', workspace: AGENT_WORKSHOP });
    }
    expect(resolveNavRoute('/ext/agent-hubs', null, workshopPaths)).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    expect(resolveNavRoute('/ext/agent-hub')).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    // The workshop's remembered page survives a reload when it is such a page.
    saveWorkspacePaths({ [AGENT_WORKSHOP]: '/ext/agent-hub/item' });
    expect(readWorkspacePaths(workshopPaths)).toEqual({ [AGENT_WORKSHOP]: '/ext/agent-hub/item' });
    expect(readWorkspacePaths()).toEqual({});
  });

  it('drops stale workbench capability paths while retaining workshop query paths', () => {
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/workflows', [AGENT_WORKSHOP]: '/workspace?tab=memory' });
    expect(readWorkspacePaths()).toEqual({ [AGENT_WORKSHOP]: '/workspace?tab=memory' });
    saveCurrentWorkspace(AGENT_WORKSHOP);
    expect(readCurrentWorkspace()).toBe(AGENT_WORKSHOP);
  });

  it('routes a scene workspace page to its scene workspace', () => {
    const workspaces = [scene({}), scene({ id: 'assistant', route: '/contracts/webui/workspaces/assistant', placement: 'aiWorkbench' })];
    expect(resolveNavRoute('/contracts/webui/workspaces/soc_ui/soc-alerts', workspaces))
      .toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('soc_ui') });
    // Workbench-placed workspaces render under the Agent menu.
    expect(resolveNavRoute('/contracts/webui/workspaces/assistant/chat', workspaces))
      .toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    // Unknown workspace routes are treated as scenes until data loads.
    expect(resolveNavRoute('/contracts/webui/workspaces/unknown/page', workspaces))
      .toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('unknown') });
    expect(resolveNavRoute('/contracts/webui/workspaces/soc_ui'))
      .toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('soc_ui') });
  });

  it('offers only installed, enabled, unlocked scenes in the switcher', () => {
    expect(isOpenSceneWorkspace(scene({}))).toBe(true);
    expect(isOpenSceneWorkspace(scene({ enabled: false }))).toBe(false);
    expect(isOpenSceneWorkspace(scene({ locked: true }))).toBe(false);
    expect(isOpenSceneWorkspace(scene({ placement: 'aiWorkbench' }))).toBe(false);
  });

  it('keeps code audit in its own workspace regardless of the alert suite state', () => {
    const audit = scene({ id: 'code_audit_ui', route: '/contracts/webui/workspaces/code_audit_ui' });
    const path = `${audit.route}/audit-home?view=audits`;
    expect(resolveNavRoute(path, [scene({}), audit])).toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('code_audit_ui') });
    for (const workspaces of [
      [audit],
      [scene({ enabled: false }), audit],
      [scene({ locked: true }), audit],
      [scene({}), { ...audit, locked: true }],
      [{ ...audit, enabled: false }],
    ]) {
      expect(resolveNavRoute(path, workspaces)).toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('code_audit_ui') });
    }
  });

  it('migrates legacy SOC navigation memory to the independent code audit workspace', () => {
    const path = '/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits';
    const paths = { [sceneNavWorkspace('soc_ui')]: path };
    saveWorkspacePaths(paths);
    expect(readWorkspacePaths()).toEqual({ [sceneNavWorkspace('code_audit_ui')]: path });
  });

  it('drops the old SOC scene-catalog memory after moving scene management to the header', () => {
    saveWorkspacePaths({ [sceneNavWorkspace('soc_ui')]: '/scenes/suites?workspace=soc_ui', [AGENT_WORKSPACE]: '/tasks' });
    expect(readWorkspacePaths()).toEqual({ [AGENT_WORKSPACE]: '/tasks' });
  });

  it('remembers the current workspace', () => {
    expect(readCurrentWorkspace()).toBe(AGENT_WORKSPACE);
    saveCurrentWorkspace(sceneNavWorkspace('soc_ui'));
    expect(readCurrentWorkspace()).toBe(sceneNavWorkspace('soc_ui'));
  });

  it('keeps each workspace last path and drops paths that no longer fit', () => {
    saveWorkspacePaths({
      [AGENT_WORKSPACE]: '/tasks',
      [sceneNavWorkspace('soc_ui')]: '/contracts/webui/workspaces/soc_ui/soc-alerts',
    });
    expect(readWorkspacePaths()).toEqual({
      [AGENT_WORKSPACE]: '/tasks',
      [sceneNavWorkspace('soc_ui')]: '/contracts/webui/workspaces/soc_ui/soc-alerts',
    });
    // A scene entry must point at its own workspace route.
    saveWorkspacePaths({ [sceneNavWorkspace('soc_ui')]: '/contracts/webui/workspaces/other/page' });
    expect(readWorkspacePaths()).toEqual({});
    // Settings and unsafe values are never stored as a workspace path.
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/settings/account' } as any);
    expect(readWorkspacePaths()).toEqual({});
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '//evil.example' } as any);
    expect(readWorkspacePaths()).toEqual({});
  });

  it('keeps an Agent workspace route candidate until placement is known', () => {
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/contracts/webui/workspaces/assistant/chat?tab=2' });
    expect(readWorkspacePaths()).toEqual({ [AGENT_WORKSPACE]: '/contracts/webui/workspaces/assistant/chat?tab=2' });
  });

  it('survives unreadable storage', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new Error('blocked'); },
    });
    expect(readWorkspacePaths()).toEqual({});
    expect(readCurrentWorkspace()).toBe(AGENT_WORKSPACE);
    expect(() => saveWorkspacePaths({ [AGENT_WORKSPACE]: '/' })).not.toThrow();
    if (original) Object.defineProperty(window, 'localStorage', original);
  });
});
