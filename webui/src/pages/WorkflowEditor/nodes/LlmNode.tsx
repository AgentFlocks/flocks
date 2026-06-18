import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Sparkles, Info } from 'lucide-react';

interface LlmNodeData {
  label?: string;
  description?: string;
  prompt?: string;
  model?: string;
  join?: boolean;
  join_mode?: string;
  bg?: string;
  border?: string;
  text?: string;
}

export default memo(function LlmNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as LlmNodeData;

  return (
    <div
      className={`
        px-4 py-3 rounded-lg border-2 shadow-md min-w-[180px]
        ${d.bg || ''} ${d.border || ''}
        ${selected ? 'ring-2 ring-pink-400 ring-offset-2' : ''}
        transition-all duration-200
      `}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-pink-500 !border-2 !border-white"
      />
      <div className="flex items-center gap-2 mb-2">
        <Sparkles className={`w-4 h-4 ${d.text || 'text-pink-600'}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${d.text || 'text-pink-600'}`}>{t('editor.nodeTypes.llm')}</div>
          <div className="text-xs text-gray-600 font-mono">{d.label || ''}</div>
        </div>
        {d.model && (
          <span className="text-xs text-pink-500 bg-pink-50 px-1.5 py-0.5 rounded font-mono">
            {d.model}
          </span>
        )}
      </div>
      {d.prompt && (
        <div className="mt-2 p-2 bg-pink-50 rounded border border-pink-200 text-xs text-pink-800 line-clamp-3 italic">
          {d.prompt}
        </div>
      )}
      {d.description && (
        <div className="flex items-start gap-1 mt-2 p-2 bg-white rounded border border-gray-200">
          <Info className="w-3 h-3 text-gray-400 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-gray-600 line-clamp-2">{d.description}</p>
        </div>
      )}
      {d.join && (
        <div className="mt-2 inline-flex rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold text-sky-700">
          Join: {d.join_mode || 'flat'}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-3 h-3 !bg-pink-500 !border-2 !border-white"
      />
    </div>
  );
});
