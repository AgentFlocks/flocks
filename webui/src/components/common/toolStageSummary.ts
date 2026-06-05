import type { ToolState } from '@/types';

function resolvePhaseLabel(phase: string): string {
  const normalized = phase.trim().toLowerCase();
  if (!normalized) return 'running';
  if (normalized === 'success') return 'completed';
  if (normalized === 'error') return 'failed';
  if (normalized === 'cancelled') return 'cancelled';
  if (normalized === 'timeout') return 'timed out';
  if (normalized === 'queued') return 'queued';
  return normalized;
}

function resolveWorkflowName(state: Partial<ToolState>): string {
  const metadata = (state.metadata ?? {}) as Record<string, unknown>;
  const rawMetadataName = metadata.workflow_name;
  if (typeof rawMetadataName === 'string' && rawMetadataName.trim()) {
    return rawMetadataName.trim();
  }

  const workflowInput = state.input?.workflow;
  if (typeof workflowInput === 'string' && workflowInput.trim()) {
    const normalized = workflowInput.trim().replace(/\\/g, '/');
    const lastSegment = normalized.split('/').filter(Boolean).pop() || normalized;
    return lastSegment.replace(/\.json$/i, '') || lastSegment;
  }
  return 'workflow';
}

export function buildRunWorkflowHeaderSummary(
  toolName: string,
  state: Partial<ToolState>,
): string {
  if (toolName !== 'run_workflow') return '';
  if ((state.status || 'pending') !== 'running') return '';

  const metadata = (state.metadata ?? {}) as Record<string, unknown>;
  const workflowName = resolveWorkflowName(state);
  const phaseRaw = metadata.phase;
  const currentNodeRaw = metadata.current_node_id;
  const stepIndexRaw = metadata.step_index;
  const totalNodesRaw = metadata.total_nodes;

  const phase = typeof phaseRaw === 'string' && phaseRaw.trim() ? phaseRaw.trim() : 'running';
  const currentNode =
    typeof currentNodeRaw === 'string' && currentNodeRaw.trim() ? currentNodeRaw.trim() : '';
  const stepIndex =
    typeof stepIndexRaw === 'number' && Number.isFinite(stepIndexRaw) ? stepIndexRaw : null;
  const totalNodes =
    typeof totalNodesRaw === 'number' && Number.isFinite(totalNodesRaw) && totalNodesRaw > 0
      ? totalNodesRaw
      : null;

  let summary = `${workflowName} ${resolvePhaseLabel(phase)}`;
  if (stepIndex !== null && stepIndex > 0 && totalNodes !== null) {
    summary += ` · ${stepIndex}/${totalNodes}`;
  } else if (stepIndex !== null && stepIndex > 0) {
    summary += ` · ${stepIndex}`;
  }
  if (currentNode) {
    summary += ` node:${currentNode}`;
  }
  return summary;
}
