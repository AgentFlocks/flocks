import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { IconGitBranch, IconInfoCircle } from '@tabler/icons-react';
import type { WorkflowGraphOutputHandle } from '@/utils/workflowGraphLayout';

interface BranchNodeData {
  label?: string;
  description?: string;
  select_key?: string;
  join?: boolean;
  join_mode?: string;
  outputHandles?: WorkflowGraphOutputHandle[];
  bg?: string;
  border?: string;
  text?: string;
}

export default memo(function BranchNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const nodeData = data as BranchNodeData;
  const outputHandles =
    nodeData.outputHandles && nodeData.outputHandles.length > 0
      ? nodeData.outputHandles
      : [
          { id: 'branch-0', label: 'out 1', left: 33 },
          { id: 'branch-1', label: 'out 2', left: 66 },
        ];
  
  return (
    <div
      className={`
 relative px-4 py-3 rounded-card border-2 shadow-sm min-w-[220px]
 ${nodeData.bg || ''} ${nodeData.border || ''}
 ${selected ? 'ring-2 ring-focus ring-offset-2' : ''}
 transition-all duration-200
 `}
    >
      {/* Input Handle */}
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-fg-muted !border-2 !border-line"
      />

      {/* Node Header */}
      <div className="flex items-center gap-2 mb-2">
        <IconGitBranch aria-hidden="true" className={`w-4 h-4 ${nodeData.text || ''}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${nodeData.text || ''}`}>{t('editor.nodeTypes.branch')}</div>
          <div className="text-xs text-fg-secondary font-mono">{nodeData.label || ''}</div>
        </div>
      </div>

      {/* Node Description */}
      {nodeData.description && (
        <div className="flex items-start gap-1 mt-2 p-2 bg-surface rounded-tag border border-line">
          <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0 mt-0.5" />
          <p className="text-xs text-fg-secondary line-clamp-2">{nodeData.description}</p>
        </div>
      )}

      {/* Select Key */}
      {nodeData.select_key && (
        <div className="mt-2 p-2 bg-surface rounded-tag border border-line">
          <div className="text-xs text-fg-muted">{t('editor.branchKeyLabel')}</div>
          <div className="text-xs font-mono text-fg mt-0.5">{nodeData.select_key}</div>
        </div>
      )}

      {/* Join Info */}
      {nodeData.join && (
        <div className="mt-2 flex items-center gap-2 text-xs text-fg-secondary">
          <span className="px-2 py-0.5 bg-subtle rounded-pill font-medium">
            Join: {nodeData.join_mode || 'flat'}
          </span>
        </div>
      )}

      {outputHandles.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {outputHandles.slice(0, 4).map((handle) => (
            <span
              key={handle.id}
              className="rounded-pill border border-line-control bg-subtle px-2 py-0.5 text-xs font-medium text-fg-secondary"
            >
              {handle.label}
            </span>
          ))}
          {outputHandles.length > 4 && (
            <span className="rounded-pill border border-line bg-subtle px-2 py-0.5 text-xs font-medium text-fg-muted">
              +{outputHandles.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Multiple Output Handles for branches */}
      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          type="source"
          position={Position.Bottom}
          id={handle.id}
          className="w-3 h-3 !bg-fg-muted !border-2 !border-line"
          style={{ left: `${handle.left}%` }}
        />
      ))}
    </div>
  );
});
