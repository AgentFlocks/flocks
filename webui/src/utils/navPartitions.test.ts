import { beforeEach, describe, expect, it } from 'vitest';
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

  it('classifies legacy Pro upgrade and cloud-login callback URLs as settings', () => {
    for (const path of ['/flockspro-upgrade', '/flockspro-upgrade?login=success', '/flockspro-upgrade/callback?console_login_id=test-login']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'settings' });
    }
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/flockspro-upgrade?login=success' });
    expect(readWorkspacePaths()).toEqual({});
    saveWorkspacePaths({ [AGENT_WORKSPACE]: '/flockspro-upgrade/callback?console_login_id=test-login' });
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

  it('assigns code audit to SOC independently of the alert suite', () => {
    const audit = scene({ id: 'code_audit_ui', route: '/contracts/webui/workspaces/code_audit_ui' });
    const path = `${audit.route}/audit-home?view=audits`;
    for (const workspaces of [[scene({}), audit], [audit], [scene({ enabled: false }), audit], [scene({ locked: true }), audit]]) {
      expect(resolveNavRoute(path, workspaces)).toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('soc_ui') });
    }
    for (const workspaces of [[scene({}), { ...audit, locked: true }], [{ ...audit, enabled: false }]]) {
      expect(resolveNavRoute(path, workspaces)).toEqual({ kind: 'workspace', workspace: sceneNavWorkspace('code_audit_ui') });
    }
  });

  it('preserves SOC navigation memory for code audit', () => {
    const path = '/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits';
    const paths = { [sceneNavWorkspace('soc_ui')]: path };
    saveWorkspacePaths(paths);
    expect(readWorkspacePaths()).toEqual(paths);
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
