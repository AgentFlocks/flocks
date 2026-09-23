import { beforeEach, describe, expect, it } from 'vitest';
import {
  hasEnabledSceneWorkspace,
  readPartitionPaths,
  resolveNavPartition,
  savePartitionPaths,
  workspaceIdFromPath,
} from './navPartitions';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';
import { findActiveTabHref } from './layoutTabs';

describe('navPartitions', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('reads the workspace id out of a workspace route', () => {
    expect(workspaceIdFromPath('/contracts/webui/workspaces/soc_ui/soc-alerts')).toBe('soc_ui');
    expect(workspaceIdFromPath('/contracts/webui/workspaces/code_audit_ui')).toBe('code_audit_ui');
    expect(workspaceIdFromPath('/contracts/webui/dash-1')).toBeNull();
    expect(workspaceIdFromPath('/sessions')).toBeNull();
  });

  it('maps a route to the partition that owns it', () => {
    expect(resolveNavPartition('/')).toBe('agent');
    expect(resolveNavPartition('/sessions')).toBe('agent');
    expect(resolveNavPartition('/devices')).toBe('agent');
    expect(resolveNavPartition('/hub')).toBe('agent');
    // Every scene workspace belongs to the one SOC workspace partition.
    expect(resolveNavPartition('/contracts/webui/workspaces/soc_ui/soc-alerts')).toBe('scene');
    expect(resolveNavPartition('/contracts/webui/workspaces/code_audit_ui')).toBe('scene');
    expect(resolveNavPartition('/scenes/suites')).toBe('scene');
    expect(resolveNavPartition('/settings')).toBe('settings');
    expect(resolveNavPartition('/settings/audit-logs')).toBe('settings');
    // A path that merely starts with the same letters is not the settings partition.
    expect(resolveNavPartition('/settings-export')).toBe('agent');
  });

  it('puts custom pages next to an enabled scene and under Agent while no scene is enabled', () => {
    const scene = {
      id: 'soc_ui', route: '/contracts/webui/workspaces/soc_ui', placement: 'sceneWorkspace', enabled: true,
    } as WebUIContractWorkspaceListItem;
    const workbench = {
      id: 'assistant', route: '/contracts/webui/workspaces/assistant', placement: 'aiWorkbench', enabled: true,
    } as WebUIContractWorkspaceListItem;
    // The suite manager follows the same rule, so it never shows an empty SOC menu.
    for (const path of ['/contracts/webui/dash-1', '/user-defined-pages/dash-1', '/scenes/suites']) {
      expect(resolveNavPartition(path, [scene])).toBe('scene');
      expect(resolveNavPartition(path, [{ ...scene, enabled: false }])).toBe('agent');
      // A workbench workspace is not a scene: it does not bring the SOC workspace tab.
      expect(resolveNavPartition(path, [workbench])).toBe('agent');
      expect(resolveNavPartition(path, [])).toBe('agent');
      // Not loaded yet: keep the default-install partition instead of flashing Agent.
      expect(resolveNavPartition(path)).toBe('scene');
      expect(resolveNavPartition(path, null)).toBe('scene');
    }
    expect(hasEnabledSceneWorkspace([scene])).toBe(true);
    expect(hasEnabledSceneWorkspace([{ ...scene, enabled: false }, workbench])).toBe(false);
    // The scene routes themselves keep the scene partition, enabled or not (Layout redirects them).
    expect(resolveNavPartition(`${scene.route}/alerts`, [{ ...scene, enabled: false }])).toBe('scene');
  });

  it('keeps a stored custom page under Agent or the SOC workspace until the scene state is known', () => {
    savePartitionPaths({ agent: '/contracts/webui/dash-1?tab=2', scene: '/contracts/webui/dash-2' });
    expect(readPartitionPaths()).toEqual({ agent: '/contracts/webui/dash-1?tab=2', scene: '/contracts/webui/dash-2' });
    savePartitionPaths({ settings: '/contracts/webui/dash-1' });
    expect(readPartitionPaths()).toEqual({});
  });

  it('keeps AI workbench extensions under Agent once workspace placement is known', () => {
    const workspaces = [
      { id: 'audit', route: '/contracts/webui/workspaces/audit', placement: 'sceneWorkspace' },
      { id: 'assistant', route: '/contracts/webui/workspaces/assistant', placement: 'aiWorkbench' },
    ] as WebUIContractWorkspaceListItem[];
    expect(resolveNavPartition('/contracts/webui/workspaces/audit/findings', workspaces)).toBe('scene');
    expect(resolveNavPartition('/contracts/webui/workspaces/assistant/chat', workspaces)).toBe('agent');
    // Unknown workspace routes default to the scene partition.
    expect(resolveNavPartition('/contracts/webui/workspaces/unknown/page', workspaces)).toBe('scene');
  });

  it('round-trips the last path of each partition', () => {
    savePartitionPaths({
      agent: '/workflows/wf-1',
      scene: '/contracts/webui/workspaces/soc_ui/soc-alerts?severity=high',
      settings: '/settings/account',
    });
    expect(readPartitionPaths()).toEqual({
      agent: '/workflows/wf-1',
      scene: '/contracts/webui/workspaces/soc_ui/soc-alerts?severity=high',
      settings: '/settings/account',
    });
  });

  it('drops a stored path that no longer belongs to its partition', () => {
    localStorage.setItem('flocks_layout_partition_paths', JSON.stringify({
      agent: '/settings/account',
      scene: 'not-a-path',
      settings: '/settings/preferences',
      'workspace:soc_ui': '/contracts/webui/workspaces/soc_ui/soc-alerts',
      unexpected: '/sessions',
    }));
    expect(readPartitionPaths()).toEqual({ settings: '/settings/preferences' });
  });

  it('preserves an AI workbench extension route until workspace placement becomes available', () => {
    const workspace = {
      id: 'assistant', route: '/contracts/webui/workspaces/assistant', placement: 'aiWorkbench',
    } as WebUIContractWorkspaceListItem;
    const path = `${workspace.route}/chat?thread=one`;
    expect(resolveNavPartition(path.split('?')[0], [workspace])).toBe('agent');
    savePartitionPaths({ agent: path });
    expect(readPartitionPaths()).toEqual({ agent: path });
    expect(findActiveTabHref(['/', '/sessions', `${workspace.route}/chat`], readPartitionPaths().agent!.split('?')[0]))
      .toBe(`${workspace.route}/chat`);
  });

  it('does not accept another scene or a removed page when validating an Agent workspace candidate', () => {
    const agentHrefs = ['/', '/sessions', '/contracts/webui/workspaces/assistant/chat'];
    const scene = {
      id: 'soc_ui', route: '/contracts/webui/workspaces/soc_ui', placement: 'sceneWorkspace',
    } as WebUIContractWorkspaceListItem;
    expect(resolveNavPartition(`${scene.route}/alerts`, [scene])).toBe('scene');
    for (const path of [`${scene.route}/alerts`, '/contracts/webui/workspaces/assistant/removed-page']) {
      savePartitionPaths({ agent: path });
      const candidate = readPartitionPaths().agent;
      expect(candidate).toBe(path);
      // This is the current-href guard used by Layout.partitionTargetPath.
      expect(findActiveTabHref(agentHrefs, candidate!.split('?')[0])).toBeNull();
    }
  });

  it.each([
    '//example.com/contracts/webui/workspaces/assistant/chat',
    'https://example.com/contracts/webui/workspaces/assistant/chat',
    'contracts/webui/workspaces/assistant/chat',
    '/settings/account',
    '/scenes/suites',
  ])('still rejects unsafe or known non-Agent stored routes: %s', (path) => {
    savePartitionPaths({ agent: path });
    expect(readPartitionPaths()).toEqual({});
  });

  it('survives unreadable storage', () => {
    localStorage.setItem('flocks_layout_partition_paths', '{oops');
    expect(readPartitionPaths()).toEqual({});
  });
});
