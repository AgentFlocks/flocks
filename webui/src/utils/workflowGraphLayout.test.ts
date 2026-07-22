import { describe, expect, it } from 'vitest';

import type { WorkflowJSON } from '@/api/workflow';
import { buildWorkflowGraphLayout, workflowGraphEdgeId } from './workflowGraphLayout';

describe('buildWorkflowGraphLayout', () => {
  it('keeps branch children parallel and merge nodes after their parents', () => {
    const workflowJson: WorkflowJSON = {
      start: 'start',
      nodes: [
        { id: 'start', type: 'python' },
        { id: 'choose', type: 'branch' },
        { id: 'hit', type: 'python' },
        { id: 'miss', type: 'python' },
        { id: 'merge', type: 'python' },
      ],
      edges: [
        { from: 'start', to: 'choose', order: 0 },
        { from: 'choose', to: 'hit', order: 0, label: 'hit' },
        { from: 'choose', to: 'miss', order: 1, label: 'miss' },
        { from: 'hit', to: 'merge', order: 0 },
        { from: 'miss', to: 'merge', order: 0 },
      ],
    };

    const layout = buildWorkflowGraphLayout(workflowJson);

    expect(layout.ranks.start).toBe(0);
    expect(layout.ranks.choose).toBe(1);
    expect(layout.ranks.hit).toBe(2);
    expect(layout.ranks.miss).toBe(2);
    expect(layout.ranks.merge).toBe(3);
    expect(layout.positions.hit.x).toBeLessThan(layout.positions.miss.x);
    expect(layout.positions.merge.y).toBeGreaterThan(layout.positions.hit.y);
    expect(layout.outputHandles.choose).toEqual([
      { id: 'branch-0', label: 'hit', left: expect.any(Number) },
      { id: 'branch-1', label: 'miss', left: expect.any(Number) },
    ]);
  });

  it('marks loop edges as back routes without moving the start behind the loop', () => {
    const workflowJson: WorkflowJSON = {
      start: 'start',
      nodes: [
        { id: 'start', type: 'python' },
        { id: 'loop', type: 'loop' },
        { id: 'done', type: 'python' },
      ],
      edges: [
        { from: 'start', to: 'loop', order: 0 },
        { from: 'loop', to: 'start', order: 0, label: 'loop' },
        { from: 'loop', to: 'done', order: 1, label: 'done' },
      ],
    };

    const layout = buildWorkflowGraphLayout(workflowJson);
    const backEdgeId = workflowGraphEdgeId(workflowJson.edges[1], 1);

    expect(layout.ranks.start).toBe(0);
    expect(layout.ranks.loop).toBe(1);
    expect(layout.ranks.done).toBe(2);
    expect(layout.outputHandles.loop).toEqual([
      { id: 'loop-0', label: 'loop', left: expect.any(Number) },
      { id: 'loop-1', label: 'done', left: expect.any(Number) },
    ]);
    expect(layout.edgeRoutes[backEdgeId]).toEqual({
      sourceHandle: 'loop-0',
      kind: 'back',
      label: 'loop',
    });
  });

  it('assigns loop exit edges to real handles so they render in React Flow', () => {
    const workflowJson: WorkflowJSON = {
      start: 'init_hosts',
      nodes: [
        { id: 'init_hosts', type: 'python' },
        { id: 'loop_check', type: 'loop' },
        { id: 'inspect_host', type: 'python' },
        { id: 'finalize_summary', type: 'python' },
      ],
      edges: [
        { from: 'init_hosts', to: 'loop_check', order: 0 },
        { from: 'loop_check', to: 'inspect_host', order: 0, label: 'continue' },
        { from: 'loop_check', to: 'finalize_summary', order: 1, label: 'exit' },
      ],
    };

    const layout = buildWorkflowGraphLayout(workflowJson);
    const exitEdgeId = workflowGraphEdgeId(workflowJson.edges[2], 2);
    const exitHandle = layout.outputHandles.loop_check.find(
      (handle) => handle.id === layout.edgeRoutes[exitEdgeId].sourceHandle
    );

    expect(exitHandle?.label).toBe('exit');
    expect(layout.edgeRoutes[exitEdgeId]).toEqual({
      sourceHandle: 'loop-1',
      kind: 'default',
      label: 'exit',
    });
  });

  it('orders outgoing handles like the backend adjacency order', () => {
    const workflowJson: WorkflowJSON = {
      start: 'choose',
      nodes: [
        { id: 'choose', type: 'branch' },
        { id: 'a_target', type: 'python' },
        { id: 'b_target', type: 'python' },
        { id: 'default_target', type: 'python' },
      ],
      edges: [
        { from: 'choose', to: 'b_target', order: 1, label: 'b' },
        { from: 'choose', to: 'default_target', order: 0 },
        { from: 'choose', to: 'a_target', order: 1, label: 'a' },
      ],
    };

    const layout = buildWorkflowGraphLayout(workflowJson);

    expect(layout.outputHandles.choose.map((handle) => handle.label)).toEqual([
      'default',
      'a',
      'b',
    ]);
  });

  it('treats logic nodes as conditional routers with labeled handles', () => {
    const workflowJson: WorkflowJSON = {
      start: 'decide',
      nodes: [
        { id: 'decide', type: 'logic', select_key: 'decision' },
        { id: 'approve', type: 'python' },
        { id: 'reject', type: 'python' },
      ],
      edges: [
        { from: 'decide', to: 'approve', order: 0, label: 'approve' },
        { from: 'decide', to: 'reject', order: 1, label: 'reject' },
      ],
    };

    const layout = buildWorkflowGraphLayout(workflowJson);

    expect(layout.outputHandles.decide.map((handle) => handle.id)).toEqual([
      'logic-0',
      'logic-1',
    ]);
    expect(layout.outputHandles.decide.map((handle) => handle.label)).toEqual([
      'approve',
      'reject',
    ]);
    expect(layout.edgeRoutes[workflowGraphEdgeId(workflowJson.edges[0], 0)]).toEqual({
      sourceHandle: 'logic-0',
      kind: 'branch',
      label: 'approve',
    });
  });
});
