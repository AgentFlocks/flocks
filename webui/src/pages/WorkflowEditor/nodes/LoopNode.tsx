import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { RotateCw, Info } from 'lucide-react';
import type { WorkflowGraphOutputHandle } from '@/utils/workflowGraphLayout';

interface LoopNodeData {
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

export default memo(function LoopNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const nodeData = data as LoopNodeData;
  const outputHandles =
    nodeData.outputHandles && nodeData.outputHandles.length > 0
      ? nodeData.outputHandles
      : [
          { id: 'loop-0', label: 'continue', left: 33 },
          { id: 'loop-1', label: 'exit', left: 66 },
        ];
  
  return (
    <div
      className={`
        relative px-4 py-3 rounded-xl border-2 shadow-sm min-w-[220px]
        ${nodeData.bg || ''} ${nodeData.border || ''}
        ${selected ? 'ring-2 ring-purple-400 ring-offset-2' : ''}
        transition-all duration-200 hover:shadow-md
      `}
    >
      {/* Input Handle */}
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-purple-500 !border-2 !border-white"
      />

      {/* Node Header */}
      <div className="flex items-center gap-2 mb-2">
        <RotateCw className={`w-4 h-4 ${nodeData.text || ''}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${nodeData.text || ''}`}>{t('editor.nodeTypes.loop')}</div>
          <div className="text-xs text-gray-600 font-mono">{nodeData.label || ''}</div>
        </div>
      </div>

      {/* Node Description */}
      {nodeData.description && (
        <div className="flex items-start gap-1 mt-2 p-2 bg-white rounded border border-gray-200">
          <Info className="w-3 h-3 text-gray-400 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-gray-600 line-clamp-2">{nodeData.description}</p>
        </div>
      )}

      {/* Code Preview */}
      {nodeData.code && (
        <div className="mt-2 p-2 bg-gray-900 rounded text-xs font-mono text-gray-300 overflow-hidden">
          <div className="line-clamp-3">{nodeData.code}</div>
        </div>
      )}

      {/* Join Info */}
      {nodeData.select_key && (
        <div className="mt-2 p-2 bg-white rounded border border-gray-200">
          <div className="text-xs text-gray-500">{t('editor.branchKeyLabel')}</div>
          <div className="text-xs font-mono text-gray-800 mt-0.5">{nodeData.select_key}</div>
        </div>
      )}

      {/* Join Info */}
      {nodeData.join && (
        <div className="mt-2 flex items-center gap-2 text-xs text-purple-700">
          <span className="px-2 py-0.5 bg-purple-200 rounded-full font-medium">
            Join: {nodeData.join_mode || 'flat'}
          </span>
        </div>
      )}

      {outputHandles.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {outputHandles.slice(0, 4).map((handle) => (
            <span
              key={handle.id}
              className="rounded-full border border-purple-200 bg-purple-50 px-2 py-0.5 text-[10px] font-medium text-purple-700"
            >
              {handle.label}
            </span>
          ))}
          {outputHandles.length > 4 && (
            <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-500">
              +{outputHandles.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Output Handles */}
      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          type="source"
          position={Position.Bottom}
          id={handle.id}
          className="w-3 h-3 !bg-purple-500 !border-2 !border-white"
          style={{ left: `${handle.left}%` }}
        />
      ))}
    </div>
  );
});
