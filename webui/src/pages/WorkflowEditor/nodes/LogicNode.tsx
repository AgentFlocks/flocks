import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { IconBolt, IconInfoCircle } from '@tabler/icons-react';
import type { WorkflowGraphOutputHandle } from '@/utils/workflowGraphLayout';

interface LogicNodeData {
  label?: string;
  description?: string;
  code?: string;
  select_key?: string;
  join?: boolean;
  join_mode?: string;
  outputHandles?: WorkflowGraphOutputHandle[];
  bg?: string;
  border?: string;
  text?: string;
}

export default memo(function LogicNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const nodeData = data as LogicNodeData;
  const outputHandles =
    nodeData.outputHandles && nodeData.outputHandles.length > 0
      ? nodeData.outputHandles
      : [{ id: 'default', label: '', left: 50 }];
  
  return (
    <div
      className={`
 relative px-4 py-3 rounded-control border-2 min-w-[220px]
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
        <IconBolt aria-hidden="true" className={`w-4 h-4 ${nodeData.text || ''}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${nodeData.text || ''}`}>{t('editor.nodeTypes.logic')}</div>
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

      {nodeData.select_key && (
        <div className="mt-2 p-2 bg-surface rounded-tag border border-line">
          <div className="text-xs text-fg-muted">{t('editor.branchKeyLabel')}</div>
          <div className="text-xs font-mono text-fg mt-0.5">{nodeData.select_key}</div>
        </div>
      )}

      {nodeData.join && (
        <div className="mt-2 inline-flex rounded-pill border border-line-control bg-subtle px-2 py-0.5 text-xs font-semibold text-fg-secondary">
          Join: {nodeData.join_mode || 'flat'}
        </div>
      )}

      {/* Code Preview */}
      {nodeData.code && (
        <div className="mt-2 p-2 bg-subtle rounded-tag text-xs font-mono text-fg-placeholder overflow-hidden">
          <div className="line-clamp-3">{nodeData.code}</div>
        </div>
      )}

      {outputHandles.length > 1 && (
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

      {/* Output Handle */}
      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          type="source"
          position={Position.Bottom}
          id={handle.id === 'default' ? undefined : handle.id}
          className="w-3 h-3 !bg-fg-muted !border-2 !border-line"
          style={{ left: `${handle.left}%` }}
        />
      ))}
    </div>
  );
});
