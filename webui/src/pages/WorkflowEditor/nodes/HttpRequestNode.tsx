import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { IconWorld, IconInfoCircle } from '@tabler/icons-react';

interface HttpRequestNodeData {
  label?: string;
  description?: string;
  method?: string;
  url?: string;
  join?: boolean;
  join_mode?: string;
  bg?: string;
  border?: string;
  text?: string;
}

const METHOD_COLORS: Record<string, string> = {
  GET: 'bg-data-soft text-data',
  POST: 'bg-data-soft text-data',
  PUT: 'bg-data-soft text-data',
  PATCH: 'bg-data-soft text-data',
  DELETE: 'bg-data-soft text-data',
};

export default memo(function HttpRequestNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as HttpRequestNodeData;
  const methodColor = METHOD_COLORS[d.method?.toUpperCase() || ''] || 'bg-subtle text-fg-secondary';

  return (
    <div
      className={`
 px-4 py-3 rounded-control border-2 min-w-[200px]
 ${d.bg || ''} ${d.border || ''}
 ${selected ? 'ring-2 ring-focus ring-offset-2' : ''}
 transition-all duration-200
 `}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-data !border-2 !border-line"
      />
      <div className="flex items-center gap-2 mb-2">
        <IconWorld aria-hidden="true" className={`w-4 h-4 ${d.text || 'text-data'}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${d.text || 'text-data'}`}>{t('editor.nodeLabel.http_request')}</div>
          <div className="text-xs text-fg-secondary font-mono">{d.label || ''}</div>
        </div>
        {d.method && (
          <span className={`text-xs font-bold px-1.5 py-0.5 rounded-tag ${methodColor}`}>
            {d.method.toUpperCase()}
          </span>
        )}
      </div>
      {d.url && (
        <div className="mt-2 px-2 py-1 bg-data-soft rounded-tag border border-data text-xs font-mono text-data truncate">
          {d.url}
        </div>
      )}
      {d.description && (
        <div className="flex items-start gap-1 mt-2 p-2 bg-surface rounded-tag border border-line">
          <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0 mt-0.5" />
          <p className="text-xs text-fg-secondary line-clamp-2">{d.description}</p>
        </div>
      )}
      {d.join && (
        <div className="mt-2 inline-flex rounded-pill border border-data bg-data-soft px-2 py-0.5 text-xs font-semibold text-data">
          Join: {d.join_mode || 'flat'}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="w-3 h-3 !bg-data !border-2 !border-line"
      />
    </div>
  );
});
