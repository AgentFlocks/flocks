import type { Edge, Node } from '@xyflow/react';

/**
 * Keyboard deletion must not fire while the user is editing text in the
 * property panel: a Backspace there is a text edit, not "delete the node".
 */
export function isTextEditingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  const tag = el.tagName.toUpperCase();
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true;
}

/**
 * Remove *removedIds* from the graph together with every edge touching them.
 * Leaving the edges behind produces dangling references the backend rejects
 * on save ("edge references unknown node").
 */
export function removeNodesWithEdges<N extends Node, E extends Edge>(
  nodes: N[],
  edges: E[],
  removedIds: Iterable<string>,
): { nodes: N[]; edges: E[] } {
  const removed = new Set(removedIds);
  return {
    nodes: nodes.filter((node) => !removed.has(node.id)),
    edges: edges.filter((edge) => !removed.has(edge.source) && !removed.has(edge.target)),
  };
}
