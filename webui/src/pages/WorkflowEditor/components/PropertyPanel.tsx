import React, { useState, useEffect } from 'react';
import { X, Code2, FileText, Key, Settings, Wrench, Sparkles, Globe, Workflow as WorkflowIcon, AlertCircle } from 'lucide-react';
import { Node } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { workflowAPI, Workflow } from '@/api/workflow';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

interface PropertyPanelProps {
  selectedNode: Node | null;
  currentWorkflowId?: string;
  onClose: () => void;
  onUpdate: (nodeId: string, updates: any) => void;
}


function JsonTextarea({
  label,
  value,
  onChange,
  placeholder,
  jsonErrorLabel,
}: {
  label: string;
  value: unknown;
  onChange: (v: unknown) => void;
  placeholder?: string;
  jsonErrorLabel?: string;
}) {
  const [raw, setRaw] = useState(() =>
    value != null ? JSON.stringify(value, null, 2) : ''
  );
  const [error, setError] = useState('');

  const handleBlur = () => {
    if (!raw.trim()) {
      onChange(undefined);
      setError('');
      return;
    }
    try {
      onChange(JSON.parse(raw));
      setError('');
    } catch {
      setError(jsonErrorLabel ?? 'JSON format error');
    }
  };

  return (
    <div>
      <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
        <Settings className="w-4 h-4" />
        {label}
      </label>
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        onBlur={handleBlur}
        rows={4}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-xs resize-none"
        placeholder={placeholder || '{ "key": "value" }'}
        spellCheck={false}
      />
      {error && (
        <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" /> {error}
        </p>
      )}
    </div>
  );
}

export default function PropertyPanel({
  selectedNode,
  currentWorkflowId,
  onClose,
  onUpdate,
}: PropertyPanelProps) {
  const { t, i18n } = useTranslation('workflow');
  const nodeTypeLabels: Record<string, string> = {
    python: t('editor.nodeTypes.python'),
    logic: t('editor.nodeTypes.logic'),
    branch: t('editor.nodeTypes.branch'),
    loop: t('editor.nodeTypes.loop'),
    tool: t('editor.nodeTypes.tool'),
    llm: t('editor.nodeTypes.llm'),
    http_request: t('editor.nodeTypes.http_request'),
    subworkflow: t('editor.nodeTypes.subworkflow'),
  };
  const [formData, setFormData] = useState<any>({});
  const [availableWorkflows, setAvailableWorkflows] = useState<Workflow[]>([]);

  useEffect(() => {
    if (selectedNode) {
      setFormData({ ...selectedNode.data });
    }
  }, [selectedNode]);

  useEffect(() => {
    if (selectedNode?.type === 'subworkflow') {
      workflowAPI
        .list({ excludeId: currentWorkflowId })
        .then((res) => setAvailableWorkflows(res.data))
        .catch(() => setAvailableWorkflows([]));
    }
  }, [selectedNode?.type, currentWorkflowId]);

  if (!selectedNode) return null;

  const nodeType = selectedNode.type as string;

  const set = (field: string, value: any) =>
    setFormData((prev: any) => ({ ...prev, [field]: value }));

  const handleSave = () => onUpdate(selectedNode.id, formData);

  return (
    <div className="fixed right-0 top-0 h-full w-96 bg-white shadow-2xl border-l border-gray-200 z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">{t('editor.nodeProperties')}</h2>
          <p className="text-sm text-gray-500">{nodeTypeLabels[nodeType] || nodeType}</p>
        </div>
        <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-lg transition-colors">
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Node ID */}
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <Key className="w-4 h-4" />
            {t('editor.nodeId')}
          </label>
          <input
            type="text"
            value={formData.label || ''}
            onChange={(e) => set('label', e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm"
            placeholder={t('editor.nodeIdPlaceholder')}
          />
        </div>

        {/* Description */}
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
            <FileText className="w-4 h-4" />
            {t('editor.description')}
          </label>
          <textarea
            value={formData.description || ''}
            onChange={(e) => set('description', e.target.value)}
            rows={3}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm resize-none"
            placeholder={t('editor.descriptionPlaceholder')}
          />
        </div>

        {/* ── python / logic / loop: code ── */}
        {(nodeType === 'python' || nodeType === 'logic' || nodeType === 'loop') && (
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
              <Code2 className="w-4 h-4" />
              {t('editor.code')}
            </label>
            <textarea
              value={formData.code || ''}
              onChange={(e) => set('code', e.target.value)}
              rows={12}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-xs resize-none bg-gray-900 text-gray-300"
              placeholder={t('editor.codePlaceholder')}
              spellCheck={false}
            />
          </div>
        )}

        {/* ── conditional routing: select_key ── */}
        {['branch', 'loop', 'logic'].includes(nodeType) && (
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
              <Settings className="w-4 h-4" />
              {t('editor.branchKey')}
            </label>
            <input
              type="text"
              value={formData.select_key || ''}
              onChange={(e) => set('select_key', e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm"
              placeholder={t('editor.branchKeyPlaceholder') || 'result'}
            />
            <p className="text-xs text-gray-500 mt-1">
              branch / loop / logic use this input path to choose the outgoing edge label.
            </p>
          </div>
        )}

        {/* ── join: applies to any downstream merge node ── */}
        <div className="rounded-lg border border-sky-100 bg-sky-50/60 p-3 space-y-3">
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
              <Settings className="w-4 h-4" />
              {t('editor.joinMerge')}
            </label>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={!!formData.join}
                onChange={(e) => set('join', e.target.checked)}
                className="w-4 h-4 text-red-600 border-gray-300 rounded"
              />
              <span className="text-sm text-gray-600">{t('editor.enableOutputMerge')}</span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              Enable this on a node that merges multiple non-exclusive incoming paths.
            </p>
          </div>
          {formData.join && (
            <>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                  <Settings className="w-4 h-4" />
                  {t('editor.joinMode')}
                </label>
                <select
                  value={formData.join_mode || 'flat'}
                  onChange={(e) => set('join_mode', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm"
                >
                  <option value="flat">{t('editor.joinModeFlat')}</option>
                  <option value="namespace">{t('editor.joinModeNamespace')}</option>
                </select>
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                  <Settings className="w-4 h-4" />
                  Join Conflict
                </label>
                <select
                  value={formData.join_conflict || 'overwrite'}
                  onChange={(e) => set('join_conflict', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm"
                >
                  <option value="overwrite">overwrite</option>
                  <option value="error">error</option>
                </select>
              </div>
              {formData.join_mode === 'namespace' && (
                <div>
                  <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                    <Settings className="w-4 h-4" />
                    Namespace Key
                  </label>
                  <input
                    type="text"
                    value={formData.join_namespace_key || ''}
                    onChange={(e) => set('join_namespace_key', e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm"
                    placeholder="__by_source__"
                  />
                </div>
              )}
            </>
          )}
        </div>

        {/* ── tool node ── */}
        {nodeType === 'tool' && (
          <>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Wrench className="w-4 h-4" />
                {t('editor.toolName')} <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.tool_name || ''}
                onChange={(e) => set('tool_name', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-violet-500 font-mono text-sm"
                placeholder={t('editor.toolNamePlaceholder')}
              />
            </div>
            <JsonTextarea
              label={t('editor.staticArgs')}
              value={formData.tool_args}
              onChange={(v) => set('tool_args', v)}
              placeholder='{ "param": "value" }'
              jsonErrorLabel={t('editor.jsonFormatError')}
            />
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Settings className="w-4 h-4" />
                {t('editor.outputKey')}
              </label>
              <input
                type="text"
                value={formData.output_key || ''}
                onChange={(e) => set('output_key', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-violet-500 font-mono text-sm"
                placeholder={t('editor.outputKeyDefaultResult')}
              />
            </div>
          </>
        )}

        {/* ── llm node ── */}
        {nodeType === 'llm' && (
          <>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Sparkles className="w-4 h-4" />
                {t('editor.promptTemplate')} <span className="text-red-500">*</span>
              </label>
              <textarea
                value={formData.prompt || ''}
                onChange={(e) => set('prompt', e.target.value)}
                rows={8}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500 text-sm resize-none"
                placeholder={t('editor.promptPlaceholder')}
              />
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Settings className="w-4 h-4" />
                {t('editor.model')}
              </label>
              <input
                type="text"
                value={formData.model || ''}
                onChange={(e) => set('model', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500 font-mono text-sm"
                placeholder={t('editor.modelPlaceholder')}
              />
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Settings className="w-4 h-4" />
                {t('editor.outputKey')}
              </label>
              <input
                type="text"
                value={formData.output_key || ''}
                onChange={(e) => set('output_key', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-500 font-mono text-sm"
                placeholder={t('editor.outputKeyDefaultResult')}
              />
            </div>
          </>
        )}

        {/* ── http_request node ── */}
        {nodeType === 'http_request' && (
          <>
            <div className="flex gap-2">
              <div className="w-28">
                <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                  <Globe className="w-4 h-4" />
                  {t('editor.method')} <span className="text-red-500">*</span>
                </label>
                <select
                  value={formData.method || 'GET'}
                  onChange={(e) => set('method', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm"
                >
                  {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                  URL <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={formData.url || ''}
                  onChange={(e) => set('url', e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 font-mono text-sm"
                  placeholder="https://api.example.com/{{ path }}"
                />
              </div>
            </div>
            <JsonTextarea
              label={t('editor.requestHeaders')}
              value={formData.headers}
              onChange={(v) => set('headers', v)}
              placeholder='{ "Authorization": "Bearer {{ token }}" }'
              jsonErrorLabel={t('editor.jsonFormatError')}
            />
            <JsonTextarea
              label={t('editor.requestBody')}
              value={formData.body}
              onChange={(v) => set('body', v)}
              placeholder='{ "key": "{{ value }}" }'
              jsonErrorLabel={t('editor.jsonFormatError')}
            />
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Settings className="w-4 h-4" />
                {t('editor.responseKey')}
              </label>
              <input
                type="text"
                value={formData.response_key || ''}
                onChange={(e) => set('response_key', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 font-mono text-sm"
                placeholder={t('editor.outputKeyDefaultResponse')}
              />
            </div>
          </>
        )}

        {/* ── subworkflow node ── */}
        {nodeType === 'subworkflow' && (
          <>
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <WorkflowIcon className="w-4 h-4" />
                {t('editor.subworkflow')} <span className="text-red-500">*</span>
              </label>
              <select
                value={formData.workflow_id || ''}
                onChange={(e) => set('workflow_id', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 text-sm"
              >
                <option value="">{t('editor.selectWorkflow')}</option>
                {availableWorkflows.map((wf) => (
                  <option key={wf.id} value={wf.id}>
                    {getWorkflowDisplayName(wf, i18n.language)}
                  </option>
                ))}
              </select>
              {availableWorkflows.length === 0 && (
                <p className="text-xs text-gray-400 mt-1">{t('editor.noWorkflowsAvailable')}</p>
              )}
            </div>
            <JsonTextarea
              label={t('editor.inputMapping')}
              value={formData.inputs_mapping}
              onChange={(v) => set('inputs_mapping', v)}
              placeholder='{ "param": "inputs.path" }'
              jsonErrorLabel={t('editor.jsonFormatError')}
            />
            <JsonTextarea
              label={t('editor.inputConst')}
              value={formData.inputs_const}
              onChange={(v) => set('inputs_const', v)}
              placeholder='{ "api_key": "fixed-value" }'
              jsonErrorLabel={t('editor.jsonFormatError')}
            />
            <div>
              <label className="flex items-center gap-2 text-sm font-medium text-gray-700 mb-2">
                <Settings className="w-4 h-4" />
                {t('editor.outputKey')}
              </label>
              <input
                type="text"
                value={formData.output_key || ''}
                onChange={(e) => set('output_key', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500 font-mono text-sm"
                placeholder={t('editor.outputKeyDefaultOutput')}
              />
            </div>
          </>
        )}

        <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-xs text-red-800">
            {t('editor.propertiesHint')}
          </p>
        </div>
      </div>

      {/* Footer */}
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
