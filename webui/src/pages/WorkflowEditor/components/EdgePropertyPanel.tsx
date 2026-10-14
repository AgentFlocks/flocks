import { useEffect, useState } from 'react';
import { Edge } from '@xyflow/react';
import { AlertCircle, GitBranch, Hash, Settings, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface EdgeData {
  label?: string;
  order?: number;
  mapping?: Record<string, string>;
  const?: Record<string, unknown>;
}

interface EdgePropertyPanelProps {
  selectedEdge: Edge | null;
  onClose: () => void;
  onUpdate: (edgeId: string, updates: {
    label?: string;
    order: number;
    mapping?: Record<string, string>;
    const?: Record<string, unknown>;
  }) => void;
}

function parseJsonObject(raw: string, fieldName: string): Record<string, unknown> | undefined {
  if (!raw.trim()) return undefined;

  const parsed = JSON.parse(raw) as unknown;
  if (parsed == null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${fieldName} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

export default function EdgePropertyPanel({ selectedEdge, onClose, onUpdate }: EdgePropertyPanelProps) {
  const { t } = useTranslation('workflow');
  const [label, setLabel] = useState('');
  const [order, setOrder] = useState(0);
  const [mappingRaw, setMappingRaw] = useState('');
  const [constRaw, setConstRaw] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!selectedEdge) return;

    const data = selectedEdge.data as EdgeData | undefined;
    setLabel(data && Object.prototype.hasOwnProperty.call(data, 'label') ? data.label ?? '' : '');
    setOrder(Number(data?.order ?? 0));
    setMappingRaw(data?.mapping ? JSON.stringify(data.mapping, null, 2) : '');
    setConstRaw(data?.const ? JSON.stringify(data.const, null, 2) : '');
    setError('');
  }, [selectedEdge]);

  if (!selectedEdge) return null;

  const handleSave = () => {
    try {
      const parsedMapping = parseJsonObject(mappingRaw, 'mapping') as Record<string, string> | undefined;
      const parsedConst = parseJsonObject(constRaw, 'const');
      onUpdate(selectedEdge.id, {
        label: label.trim() || undefined,
        order: Number.isFinite(order) && order >= 0 ? order : 0,
        mapping: parsedMapping,
        const: parsedConst,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'JSON format error');
    }
  };

  return (
    <div className="fixed right-0 top-0 h-full w-96 bg-white shadow-2xl border-l border-gray-200 z-50 flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Edge Properties</h2>
          <p className="text-sm text-gray-500 font-mono">
            {selectedEdge.source} → {selectedEdge.target}
          </p>
        </div>
        <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <GitBranch className="w-4 h-4" />
            Label
          </label>
          <input
            type="text"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm"
            placeholder="default / true / false / continue / exit"
          />
          <p className="text-xs text-gray-500 mt-1">
            {t('editor.branchKey')} values from branch, loop, or logic nodes match this label. Empty label means default fallback.
          </p>
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <Hash className="w-4 h-4" />
            Order
          </label>
          <input
            type="number"
            min={0}
            value={order}
            onChange={(event) => setOrder(Number(event.target.value))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm"
          />
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <Settings className="w-4 h-4" />
            Mapping
          </label>
          <textarea
            value={mappingRaw}
            onChange={(event) => setMappingRaw(event.target.value)}
            rows={5}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-xs resize-none"
            placeholder='{ "dstKey": "source.path" }'
            spellCheck={false}
          />
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <Settings className="w-4 h-4" />
            Const
          </label>
          <textarea
            value={constRaw}
            onChange={(event) => setConstRaw(event.target.value)}
            rows={5}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-xs resize-none"
            placeholder='{ "fixedKey": "fixed-value" }'
            spellCheck={false}
          />
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}
      </div>

      <div className="p-4 border-t border-gray-200 bg-gray-50">
        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={handleSave}
            className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
          >
            {t('editor.applyChanges')}
          </button>
        </div>
      </div>
    </div>
  );
}
