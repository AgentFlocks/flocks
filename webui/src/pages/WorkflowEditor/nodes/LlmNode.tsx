import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { IconSparkles, IconInfoCircle } from '@tabler/icons-react';

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
 px-4 py-3 rounded-control border-2 min-w-[180px]
 ${d.bg || ''} ${d.border || ''}
 ${selected ? 'ring-2 ring-focus ring-offset-2' : ''}
 transition-all duration-200
 `}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-ai !border-2 !border-line"
      />
      <div className="flex items-center gap-2 mb-2">
        <IconSparkles aria-hidden="true" className={`w-4 h-4 ${d.text || 'text-ai'}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${d.text || 'text-ai'}`}>{t('editor.nodeTypes.llm')}</div>
          <div className="text-xs text-fg-secondary font-mono">{d.label || ''}</div>
        </div>
        {d.model && (
          <span className="text-xs text-ai bg-ai-soft px-1.5 py-0.5 rounded-tag font-mono">
            {d.model}
          </span>
        )}
      </div>
      {d.prompt && (
        <div className="mt-2 p-2 bg-ai-soft rounded-tag border border-ai text-xs text-ai line-clamp-3 italic">
          {d.prompt}
        </div>
      )}
      {d.description && (
        <div className="flex items-start gap-1 mt-2 p-2 bg-surface rounded-tag border border-line">
          <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0 mt-0.5" />
          <p className="text-xs text-fg-secondary line-clamp-2">{d.description}</p>
        </div>
      )}
      {d.join && (
        <div className="mt-2 inline-flex rounded-pill border border-ai bg-ai-soft px-2 py-0.5 text-xs font-semibold text-ai">
          Join: {d.join_mode || 'flat'}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-3 h-3 !bg-ai !border-2 !border-line"
      />
    </div>
  );
});
