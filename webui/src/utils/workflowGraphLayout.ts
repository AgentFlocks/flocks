import type { WorkflowEdge, WorkflowJSON } from '@/api/workflow';

export interface GraphPoint {
  x: number;
  y: number;
}

export interface WorkflowGraphOutputHandle {
  id: string;
  label: string;
  left: number;
}

export interface WorkflowGraphEdgeRoute {
  sourceHandle?: string;
  kind: 'default' | 'branch' | 'loop' | 'back';
  label?: string;
}

export interface WorkflowGraphLayout {
  positions: Record<string, GraphPoint>;
  ranks: Record<string, number>;
  outputHandles: Record<string, WorkflowGraphOutputHandle[]>;
  edgeRoutes: Record<string, WorkflowGraphEdgeRoute>;
}

export const WORKFLOW_GRAPH_NODE_WIDTH = 220;
export const WORKFLOW_GRAPH_NODE_HEIGHT = 118;

const HORIZONTAL_GAP = 96;
const VERTICAL_GAP = 106;
const FANOUT_GAP = WORKFLOW_GRAPH_NODE_WIDTH + HORIZONTAL_GAP;

export function workflowGraphEdgeId(edge: WorkflowEdge, index: number): string {
  return `e-${edge.from}-${edge.to}-${index}`;
}

function compareEdges(a: WorkflowEdge, b: WorkflowEdge): number {
  const orderDiff = (a.order ?? 0) - (b.order ?? 0);
  if (orderDiff !== 0) return orderDiff;

  return a.to.localeCompare(b.to);
}

function getConditionalEdgeLabel(edge: WorkflowEdge): string {
  return edge.label || 'default';
}

function getReachableDistances(startId: string | undefined, outgoing: Map<string, WorkflowEdge[]>): Map<string, number> {
  const distances = new Map<string, number>();
  if (!startId) return distances;

  const queue = [startId];
  distances.set(startId, 0);

  while (queue.length > 0) {
    const current = queue.shift()!;
    const nextDistance = (distances.get(current) ?? 0) + 1;

    for (const edge of outgoing.get(current) ?? []) {
      if (distances.has(edge.to)) continue;
      distances.set(edge.to, nextDistance);
      queue.push(edge.to);
    }
  }

  return distances;
}

function calculateRanks(workflowJson: WorkflowJSON, outgoing: Map<string, WorkflowEdge[]>): Record<string, number> {
  const nodeIds = workflowJson.nodes.map((node) => node.id);
  const idSet = new Set(nodeIds);
  const startId = workflowJson.start || nodeIds[0];
  const indegree = new Map<string, number>();
  const ranks = new Map<string, number>();

  for (const id of nodeIds) {
    indegree.set(id, 0);
  }

  for (const edge of workflowJson.edges) {
    if (!idSet.has(edge.from) || !idSet.has(edge.to)) continue;
    if (edge.to === startId) continue;
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }

  if (startId) {
    indegree.set(startId, 0);
    ranks.set(startId, 0);
  }

  const queue = nodeIds
    .filter((id) => (indegree.get(id) ?? 0) === 0)
    .sort((a, b) => (a === startId ? -1 : b === startId ? 1 : nodeIds.indexOf(a) - nodeIds.indexOf(b)));
  const processed = new Set<string>();

  while (queue.length > 0) {
    const current = queue.shift()!;
    if (processed.has(current)) continue;
    processed.add(current);

    const currentRank = ranks.get(current) ?? 0;
    for (const edge of outgoing.get(current) ?? []) {
      if (!idSet.has(edge.to) || edge.to === startId) continue;

      ranks.set(edge.to, Math.max(ranks.get(edge.to) ?? 0, currentRank + 1));
      indegree.set(edge.to, Math.max((indegree.get(edge.to) ?? 0) - 1, 0));

      if ((indegree.get(edge.to) ?? 0) === 0) {
        queue.push(edge.to);
      }
    }
  }

  const distances = getReachableDistances(startId, outgoing);
  let fallbackRank = Math.max(0, ...Array.from(ranks.values()));

  for (const id of nodeIds) {
    if (ranks.has(id)) continue;

    const distance = distances.get(id);
    if (distance !== undefined) {
      ranks.set(id, distance);
    } else {
      fallbackRank += 1;
      ranks.set(id, fallbackRank);
    }
  }

  return Object.fromEntries(ranks.entries());
}

function getIncomingByNode(edges: WorkflowEdge[]): Map<string, WorkflowEdge[]> {
  const incoming = new Map<string, WorkflowEdge[]>();
  for (const edge of edges) {
    if (!incoming.has(edge.to)) incoming.set(edge.to, []);
    incoming.get(edge.to)!.push(edge);
  }
  return incoming;
}

function getDesiredX(
  nodeId: string,
  rank: number,
  ranks: Record<string, number>,
  positions: Record<string, GraphPoint>,
  incoming: Map<string, WorkflowEdge[]>,
  outgoing: Map<string, WorkflowEdge[]>,
  originalIndex: Map<string, number>
): number {
  const parentTargets = (incoming.get(nodeId) ?? [])
    .filter((edge) => ranks[edge.from] < rank && positions[edge.from])
    .map((edge) => {
      const parentOut = outgoing.get(edge.from) ?? [];
      const edgeIndex = Math.max(parentOut.findIndex((candidate) => candidate === edge), 0);
      const fanoutOffset = (edgeIndex - (parentOut.length - 1) / 2) * FANOUT_GAP;
      return positions[edge.from].x + fanoutOffset;
    });

  if (parentTargets.length > 0) {
    return parentTargets.reduce((sum, value) => sum + value, 0) / parentTargets.length;
  }

  return ((originalIndex.get(nodeId) ?? 0) % 7) * FANOUT_GAP;
}

function resolveRankPositions(
  ids: string[],
  rank: number,
  ranks: Record<string, number>,
  positions: Record<string, GraphPoint>,
  incoming: Map<string, WorkflowEdge[]>,
  outgoing: Map<string, WorkflowEdge[]>,
  originalIndex: Map<string, number>
): void {
  const desired = ids
    .map((id) => ({
      id,
      x: getDesiredX(id, rank, ranks, positions, incoming, outgoing, originalIndex),
    }))
    .sort((a, b) => a.x - b.x || (originalIndex.get(a.id) ?? 0) - (originalIndex.get(b.id) ?? 0));

  const placed = desired.map((item, index) => ({
    ...item,
    x: index === 0 ? item.x : Math.max(item.x, desired[index - 1].x + FANOUT_GAP),
  }));

  for (let index = 1; index < placed.length; index += 1) {
    placed[index].x = Math.max(placed[index].x, placed[index - 1].x + FANOUT_GAP);
  }

  const desiredCenter = desired.reduce((sum, item) => sum + item.x, 0) / Math.max(desired.length, 1);
  const actualCenter =
    placed.length > 0 ? (placed[0].x + placed[placed.length - 1].x) / 2 : 0;
  const shift = desiredCenter - actualCenter;

  for (const item of placed) {
    positions[item.id] = {
      x: item.x + shift,
      y: rank * (WORKFLOW_GRAPH_NODE_HEIGHT + VERTICAL_GAP),
    };
  }
}

function buildOutputHandles(
  workflowJson: WorkflowJSON,
  outgoing: Map<string, WorkflowEdge[]>
): Record<string, WorkflowGraphOutputHandle[]> {
  const handles: Record<string, WorkflowGraphOutputHandle[]> = {};

  for (const node of workflowJson.nodes) {
    const edges = outgoing.get(node.id) ?? [];
    if (!['branch', 'loop', 'logic'].includes(node.type) || edges.length === 0) continue;
    const prefix = node.type === 'loop' ? 'loop' : node.type === 'logic' ? 'logic' : 'branch';

    handles[node.id] = edges.map((edge, index) => ({
      id: `${prefix}-${index}`,
      label: getConditionalEdgeLabel(edge),
      left: ((index + 1) / (edges.length + 1)) * 100,
    }));
  }

  return handles;
}

function buildEdgeRoutes(
  workflowJson: WorkflowJSON,
  ranks: Record<string, number>,
  outgoing: Map<string, WorkflowEdge[]>
): Record<string, WorkflowGraphEdgeRoute> {
  const nodeTypes = new Map(workflowJson.nodes.map((node) => [node.id, node.type]));
  const routes: Record<string, WorkflowGraphEdgeRoute> = {};

  workflowJson.edges.forEach((edge, index) => {
    const sourceType = nodeTypes.get(edge.from);
    const edgeIndex = outgoing.get(edge.from)?.findIndex((candidate) => candidate === edge) ?? -1;
    const isBackEdge = (ranks[edge.to] ?? 0) <= (ranks[edge.from] ?? 0);
    const label = (edge.label ?? '').toLowerCase();

    if (sourceType === 'branch') {
      routes[workflowGraphEdgeId(edge, index)] = {
        sourceHandle: `branch-${Math.max(edgeIndex, 0)}`,
        kind: isBackEdge ? 'back' : 'branch',
        label: getConditionalEdgeLabel(edge),
      };
      return;
    }

    if (sourceType === 'loop') {
      routes[workflowGraphEdgeId(edge, index)] = {
        sourceHandle: `loop-${Math.max(edgeIndex, 0)}`,
        kind: isBackEdge ? 'back' : label.includes('loop') || label.includes('continue') ? 'loop' : 'default',
        label: getConditionalEdgeLabel(edge),
      };
      return;
    }

    if (sourceType === 'logic') {
      routes[workflowGraphEdgeId(edge, index)] = {
        sourceHandle: `logic-${Math.max(edgeIndex, 0)}`,
        kind: isBackEdge ? 'back' : 'branch',
        label: getConditionalEdgeLabel(edge),
      };
      return;
    }

    if (label.includes('loop') || label.includes('continue')) {
      routes[workflowGraphEdgeId(edge, index)] = {
        kind: isBackEdge ? 'back' : 'loop',
      };
      return;
    }

    routes[workflowGraphEdgeId(edge, index)] = {
      kind: isBackEdge ? 'back' : 'default',
    };
  });

  return routes;
}

export function buildWorkflowGraphLayout(workflowJson: WorkflowJSON): WorkflowGraphLayout {
  const outgoing = new Map<string, WorkflowEdge[]>();
  const originalIndex = new Map<string, number>();

  workflowJson.nodes.forEach((node, index) => {
    outgoing.set(node.id, []);
    originalIndex.set(node.id, index);
  });

  for (const edge of workflowJson.edges) {
    if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
    outgoing.get(edge.from)!.push(edge);
  }

  for (const edges of outgoing.values()) {
    edges.sort(compareEdges);
  }

  const ranks = calculateRanks(workflowJson, outgoing);
  const incoming = getIncomingByNode(workflowJson.edges);
  const rankGroups = new Map<number, string[]>();

  for (const node of workflowJson.nodes) {
    const rank = ranks[node.id] ?? 0;
    if (!rankGroups.has(rank)) rankGroups.set(rank, []);
    rankGroups.get(rank)!.push(node.id);
  }

  const positions: Record<string, GraphPoint> = {};
  const sortedRanks = Array.from(rankGroups.keys()).sort((a, b) => a - b);

  for (const rank of sortedRanks) {
    resolveRankPositions(rankGroups.get(rank)!, rank, ranks, positions, incoming, outgoing, originalIndex);
  }

  return {
    positions,
    ranks,
    outputHandles: buildOutputHandles(workflowJson, outgoing),
    edgeRoutes: buildEdgeRoutes(workflowJson, ranks, outgoing),
  };
}
