import type { ToolState } from '@/types';

type SummaryTranslator = (key: string, options?: Record<string, unknown>) => string;

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

function resolvePhaseTranslationKey(phase: string): string | null {
  const normalized = phase.trim().toLowerCase();
  if (!normalized) return 'running';
  if (normalized === 'success') return 'success';
  if (normalized === 'error') return 'error';
  if (normalized === 'cancelled') return 'cancelled';
  if (normalized === 'timeout') return 'timeout';
  if (normalized === 'queued') return 'queued';
  if (normalized === 'running') return 'running';
  return null;
}

function translateOrFallback(
  key: string,
  fallback: string,
  t?: SummaryTranslator,
  options?: Record<string, unknown>,
): string {
  if (!t) return fallback;
  const translated = t(key, options);
  return translated && translated !== key ? translated : fallback;
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
  t?: SummaryTranslator,
): string {
  if (toolName !== 'run_workflow') return '';
  if ((state.status || 'pending') !== 'running') return '';

  const metadata = (state.metadata ?? {}) as Record<string, unknown>;
  const workflowName = resolveWorkflowName(state);
  const phaseRaw = metadata.phase;
  const currentNodeRaw = metadata.current_node_id;
  const stepIndexRaw = metadata.step_index;
  const totalNodesRaw = metadata.total_nodes;
  const loopProgress = metadata.loop_progress as Record<string, unknown> | undefined;

  const phase = typeof phaseRaw === 'string' && phaseRaw.trim() ? phaseRaw.trim() : 'running';
  const currentNode =
    typeof currentNodeRaw === 'string' && currentNodeRaw.trim() ? currentNodeRaw.trim() : '';
  const stepIndex =
    typeof stepIndexRaw === 'number' && Number.isFinite(stepIndexRaw) ? stepIndexRaw : null;
  const totalNodes =
    typeof totalNodesRaw === 'number' && Number.isFinite(totalNodesRaw) && totalNodesRaw > 0
      ? totalNodesRaw
      : null;

  const phaseKey = resolvePhaseTranslationKey(phase);
  const phaseLabel = phaseKey
    ? translateOrFallback(
        `chat.tool.workflowPhase.${phaseKey}`,
        resolvePhaseLabel(phase),
        t,
      )
    : resolvePhaseLabel(phase);

  let summary = `${workflowName} ${phaseLabel}`;
  const loopIteration =
    typeof loopProgress?.iteration === 'number' && Number.isFinite(loopProgress.iteration)
      ? loopProgress.iteration
      : null;
  const loopTotal =
    typeof loopProgress?.total_iterations === 'number' && Number.isFinite(loopProgress.total_iterations)
      ? loopProgress.total_iterations
      : null;
  const loopItem =
    typeof loopProgress?.current_item === 'string' && loopProgress.current_item.trim()
      ? loopProgress.current_item.trim()
      : '';
  const loopInnerNode =
    typeof loopProgress?.current_inner_node_id === 'string' && loopProgress.current_inner_node_id.trim()
      ? loopProgress.current_inner_node_id.trim()
      : '';

  if (loopIteration !== null && loopIteration > 0) {
    const loopLabel = translateOrFallback('chat.tool.workflowLoopIteration', 'Loop', t);
    const progressLabel = loopTotal !== null && loopTotal > 0
      ? `${loopIteration}/${loopTotal}`
      : `${loopIteration}`;
    summary += ` · ${loopLabel} ${progressLabel}`;
    if (loopItem) {
      const currentLabel = translateOrFallback('chat.tool.workflowLoopCurrent', 'Current', t);
      summary += ` · ${currentLabel}: ${loopItem}`;
    }
  } else if (stepIndex !== null && stepIndex > 0) {
    const stepLabel = totalNodes !== null ? `${stepIndex}/${totalNodes}` : `${stepIndex}`;
    summary += ` · ${stepLabel}`;
  }
  const nodeForDisplay = loopInnerNode || currentNode;
  if (nodeForDisplay) {
    summary += ` · ${translateOrFallback(
      'chat.tool.workflowNode',
      `node:${nodeForDisplay}`,
      t,
      { node: nodeForDisplay },
    )}`;
  }
  return summary;
}
