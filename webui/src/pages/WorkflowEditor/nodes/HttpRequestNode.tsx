import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Globe, Info } from 'lucide-react';

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
  GET: 'bg-green-100 text-green-700',
  POST: 'bg-red-100 text-red-700',
  PUT: 'bg-yellow-100 text-yellow-700',
  PATCH: 'bg-orange-100 text-orange-700',
  DELETE: 'bg-red-100 text-red-700',
};

export default memo(function HttpRequestNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as HttpRequestNodeData;
  const methodColor = METHOD_COLORS[d.method?.toUpperCase() || ''] || 'bg-gray-100 text-gray-700';

  return (
    <div
      className={`
        px-4 py-3 rounded-lg border-2 shadow-md min-w-[200px]
        ${d.bg || ''} ${d.border || ''}
        ${selected ? 'ring-2 ring-teal-400 ring-offset-2' : ''}
        transition-all duration-200
      `}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="w-3 h-3 !bg-teal-500 !border-2 !border-white"
      />
      <div className="flex items-center gap-2 mb-2">
        <Globe className={`w-4 h-4 ${d.text || 'text-teal-600'}`} />
        <div className="flex-1">
          <div className={`font-semibold text-sm ${d.text || 'text-teal-600'}`}>{t('editor.nodeLabel.http_request')}</div>
          <div className="text-xs text-gray-600 font-mono">{d.label || ''}</div>
        </div>
        {d.method && (
          <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${methodColor}`}>
            {d.method.toUpperCase()}
          </span>
        )}
      </div>
      {d.url && (
        <div className="mt-2 px-2 py-1 bg-teal-50 rounded border border-teal-200 text-xs font-mono text-teal-700 truncate">
          {d.url}
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
        className="w-3 h-3 !bg-teal-500 !border-2 !border-white"
      />
    </div>
  );
});
