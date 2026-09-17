import { beforeEach, describe, expect, it } from 'vitest';
import {
  NAV_PARTITION_IDS,
  readPartitionPaths,
  resolveNavPartition,
  savePartitionPaths,
} from './navPartitions';

describe('navPartitions', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('has exactly the three fixed partitions', () => {
    expect(NAV_PARTITION_IDS).toEqual(['agent', 'scene', 'settings']);
  });

  it('maps a route to the partition that owns it', () => {
    expect(resolveNavPartition('/')).toBe('agent');
    expect(resolveNavPartition('/sessions')).toBe('agent');
    expect(resolveNavPartition('/devices')).toBe('agent');
    expect(resolveNavPartition('/hub')).toBe('agent');
    expect(resolveNavPartition('/contracts/webui/workspaces/soc_ui/soc-alerts')).toBe('scene');
    expect(resolveNavPartition('/contracts/webui/dash-1')).toBe('scene');
    expect(resolveNavPartition('/user-defined-pages/dash-1')).toBe('scene');
    expect(resolveNavPartition('/settings')).toBe('settings');
    expect(resolveNavPartition('/settings/audit-logs')).toBe('settings');
    // A path that merely starts with the same letters is not the settings partition.
    expect(resolveNavPartition('/settings-export')).toBe('agent');
  });

  it('round-trips the last path of each partition', () => {
    savePartitionPaths({ agent: '/workflows/wf-1', scene: '/contracts/webui/dash-1' });
    expect(readPartitionPaths()).toEqual({
      agent: '/workflows/wf-1',
      scene: '/contracts/webui/dash-1',
    });
  });

  it('drops a stored path that no longer belongs to its partition', () => {
    localStorage.setItem('flocks_layout_partition_paths', JSON.stringify({
      agent: '/settings/account',
      scene: 'not-a-path',
      settings: '/settings/preferences',
    }));
    expect(readPartitionPaths()).toEqual({ settings: '/settings/preferences' });
  });

  it('survives unreadable storage', () => {
    localStorage.setItem('flocks_layout_partition_paths', '{oops');
    expect(readPartitionPaths()).toEqual({});
  });
});
