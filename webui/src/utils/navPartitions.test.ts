import { beforeEach, describe, expect, it } from 'vitest';
import {
  AGENT_WORKSPACE,
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

  it('routes settings and the scene manager to their own kinds', () => {
    expect(resolveNavRoute('/settings')).toEqual({ kind: 'settings' });
    expect(resolveNavRoute('/settings/audit-logs')).toEqual({ kind: 'settings' });
    expect(resolveNavRoute('/scenes')).toEqual({ kind: 'scenes' });
    expect(resolveNavRoute('/scenes/suites')).toEqual({ kind: 'scenes' });
  });

  it('routes ordinary pages to the Agent workspace', () => {
    for (const path of ['/', '/sessions', '/tasks', '/agents', '/hub', '/contracts/webui/dash-1']) {
      expect(resolveNavRoute(path)).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
    }
    // A path that merely starts with the same letters is not settings.
    expect(resolveNavRoute('/settings-export')).toEqual({ kind: 'workspace', workspace: AGENT_WORKSPACE });
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
