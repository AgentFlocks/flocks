import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Code2, Info } from 'lucide-react';

interface PythonNodeData {
  label?: string;
  description?: string;
  code?: string;
  join?: boolean;
  join_mode?: string;
  bg?: string;
  border?: string;
  text?: string;
}

export default memo(function PythonNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const nodeData = data as PythonNodeData;
  
  return (
    <div
      className={`
        px-4 py-3 rounded-lg border-2 shadow-md min-w-[180px]
        ${nodeData.bg || ''} ${nodeData.border || ''}
        ${selected ? 'ring-2 ring-red-400 ring-offset-2' : ''}
        transition-all duration-200
      `}
    >
      {/* Input Handle */}
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-red-500 !border-2 !border-white"
      />

      {/* Node Header */}
      <div className="flex items-center gap-2 mb-2">
        <Code2 className={`w-4 h-4 ${nodeData.text || ''}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${nodeData.text || ''}`}>{t('editor.nodeTypes.python')}</div>
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

      {/* Output Handle */}
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-3 h-3 !bg-red-500 !border-2 !border-white"
      />
    </div>
  );
});
