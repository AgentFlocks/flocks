import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { IconRouteSquare2, IconInfoCircle } from '@tabler/icons-react';

interface SubworkflowNodeData {
  label?: string;
  description?: string;
  workflow_id?: string;
  join?: boolean;
  join_mode?: string;
  bg?: string;
  border?: string;
  text?: string;
}

export default memo(function SubworkflowNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as SubworkflowNodeData;

  return (
    <div
      className={`
 px-4 py-3 rounded-control border-2 border-dashed min-w-[200px]
 ${d.bg || 'bg-surface'} ${d.border || 'border-ai'}
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
        <IconRouteSquare2 aria-hidden="true" className={`w-4 h-4 ${d.text || 'text-ai'}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${d.text || 'text-ai'}`}>{t('editor.nodeLabel.subworkflow')}</div>
          <div className="text-xs text-fg-secondary font-mono">{d.label || ''}</div>
        </div>
      </div>
      {d.workflow_id && (
        <div className="mt-2 px-2 py-1 bg-ai-soft rounded-tag border border-ai text-xs font-mono text-ai truncate">
          ID: {d.workflow_id}
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
