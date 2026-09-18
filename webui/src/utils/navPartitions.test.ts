import { beforeEach, describe, expect, it } from 'vitest';
import {
  readPartitionPaths,
  resolveNavPartition,
  savePartitionPaths,
  workspacePartitionId,
} from './navPartitions';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';
import { findActiveTabHref } from './layoutTabs';

describe('navPartitions', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('identifies each installed scene workspace separately', () => {
    expect(workspacePartitionId('soc_ui')).toBe('workspace:soc_ui');
    expect(workspacePartitionId('code_audit_ui')).toBe('workspace:code_audit_ui');
  });

  it('maps a route to the partition that owns it', () => {
    expect(resolveNavPartition('/')).toBe('agent');
    expect(resolveNavPartition('/sessions')).toBe('agent');
    expect(resolveNavPartition('/devices')).toBe('agent');
    expect(resolveNavPartition('/hub')).toBe('agent');
    expect(resolveNavPartition('/contracts/webui/workspaces/soc_ui/soc-alerts')).toBe('workspace:soc_ui');
    expect(resolveNavPartition('/contracts/webui/workspaces/code_audit_ui')).toBe('workspace:code_audit_ui');
    expect(resolveNavPartition('/scenes/suites')).toBe('scene');
    expect(resolveNavPartition('/contracts/webui/dash-1')).toBe('scene');
    expect(resolveNavPartition('/user-defined-pages/dash-1')).toBe('scene');
    expect(resolveNavPartition('/settings')).toBe('settings');
    expect(resolveNavPartition('/settings/audit-logs')).toBe('settings');
    // A path that merely starts with the same letters is not the settings partition.
    expect(resolveNavPartition('/settings-export')).toBe('agent');
  });

  it('resolves custom workspace routes and keeps AI workbench extensions under Agent', () => {
    const workspaces = [
      { id: 'audit', route: '/audit', placement: 'sceneWorkspace' },
      { id: 'assistant', route: '/contracts/webui/workspaces/assistant', placement: 'aiWorkbench' },
    ] as WebUIContractWorkspaceListItem[];
    expect(resolveNavPartition('/audit/findings', workspaces)).toBe('workspace:audit');
    expect(resolveNavPartition('/auditing', workspaces)).toBe('agent');
    expect(resolveNavPartition('/contracts/webui/workspaces/assistant/chat', workspaces)).toBe('agent');
  });

  it('round-trips the last path of each partition', () => {
    savePartitionPaths({
      agent: '/workflows/wf-1',
      scene: '/contracts/webui/dash-1',
      'workspace:soc_ui': '/contracts/webui/workspaces/soc_ui/soc-alerts?severity=high',
      'workspace:audit': '/audit/findings',
    });
    expect(readPartitionPaths()).toEqual({
      agent: '/workflows/wf-1',
      scene: '/contracts/webui/dash-1',
      'workspace:soc_ui': '/contracts/webui/workspaces/soc_ui/soc-alerts?severity=high',
      'workspace:audit': '/audit/findings',
    });
  });

  it('drops a stored path that no longer belongs to its partition', () => {
    localStorage.setItem('flocks_layout_partition_paths', JSON.stringify({
      agent: '/settings/account',
      scene: 'not-a-path',
      settings: '/settings/preferences',
      'workspace:bad/id': '/contracts/webui/workspaces/bad/id',
      'workspace:external': '//example.com',
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
    expect(resolveNavPartition(`${scene.route}/alerts`, [scene])).toBe('workspace:soc_ui');
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
    '/contracts/webui/custom-page',
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
