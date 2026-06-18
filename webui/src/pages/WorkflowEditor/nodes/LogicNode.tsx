import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Zap, Info } from 'lucide-react';
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
        relative px-4 py-3 rounded-lg border-2 shadow-md min-w-[220px]
        ${nodeData.bg || ''} ${nodeData.border || ''}
        ${selected ? 'ring-2 ring-green-400 ring-offset-2' : ''}
        transition-all duration-200
      `}
    >
      {/* Input Handle */}
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-green-500 !border-2 !border-white"
      />

      {/* Node Header */}
      <div className="flex items-center gap-2 mb-2">
        <Zap className={`w-4 h-4 ${nodeData.text || ''}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${nodeData.text || ''}`}>{t('editor.nodeTypes.logic')}</div>
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

      {nodeData.select_key && (
        <div className="mt-2 p-2 bg-white rounded border border-gray-200">
          <div className="text-xs text-gray-500">{t('editor.branchKeyLabel')}</div>
          <div className="text-xs font-mono text-gray-800 mt-0.5">{nodeData.select_key}</div>
        </div>
      )}

      {nodeData.join && (
        <div className="mt-2 inline-flex rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold text-sky-700">
          Join: {nodeData.join_mode || 'flat'}
        </div>
      )}

      {/* Code Preview */}
      {nodeData.code && (
        <div className="mt-2 p-2 bg-gray-900 rounded text-xs font-mono text-gray-300 overflow-hidden">
          <div className="line-clamp-3">{nodeData.code}</div>
        </div>
      )}

      {outputHandles.length > 1 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {outputHandles.slice(0, 4).map((handle) => (
            <span
              key={handle.id}
              className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700"
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

      {/* Output Handle */}
      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          type="source"
          position={Position.Bottom}
          id={handle.id === 'default' ? undefined : handle.id}
          className="w-3 h-3 !bg-green-500 !border-2 !border-white"
          style={{ left: `${handle.left}%` }}
        />
      ))}
    </div>
  );
});
