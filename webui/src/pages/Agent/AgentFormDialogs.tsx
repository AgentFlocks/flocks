import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { agentAPI, Agent } from '@/api/agent';

// ============================================================================
// Create Agent Dialog
// ============================================================================

export function CreateAgentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation(['agent', 'common']);
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    descriptionCn: '',
    prompt: '',
    temperature: 0.7,
    mode: 'primary',
    color: '',
  });
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!formData.name || !formData.prompt) {
      alert(t('agent:error.requiredFields'));
      return;
    }
    try {
      setLoading(true);
      await agentAPI.create({
        ...formData,
        color: formData.color || undefined,
      });
      onCreated();
      onClose();
    } catch (err: any) {
      alert(t('agent:error.createFailed', { message: err.message }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AgentFormModal
      title={t('agent:dialog.createTitle')}
      formData={formData}
      onChange={setFormData}
      onSubmit={handleSubmit}
      onClose={onClose}
      loading={loading}
      submitLabel={t('common:button.create')}
      loadingLabel={t('common:button.creating')}
      nameEditable
    />
  );
}

// ============================================================================
// Edit Agent Dialog
// ============================================================================

export function EditAgentDialog({
  agent,
  onClose,
  onUpdated,
}: {
  agent: Agent;
  onClose: () => void;
  /** Called after a successful update — parent should refresh the agent list. */
  onUpdated: () => void;
}) {
  const { t } = useTranslation(['agent', 'common']);
  const [formData, setFormData] = useState({
    name: agent.name,
    description: agent.description ?? '',
    descriptionCn: agent.descriptionCn ?? '',
    prompt: agent.prompt ?? '',
    temperature: agent.temperature ?? 0.7,
    mode: agent.mode,
    color: agent.color ?? '',
  });
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!formData.prompt) {
      alert(t('agent:error.requiredFields'));
      return;
    }
    try {
      setLoading(true);
      await agentAPI.update(agent.name, {
        description: formData.description || undefined,
        descriptionCn: formData.descriptionCn || undefined,
        prompt: formData.prompt,
        temperature: formData.temperature,
        color: formData.color || undefined,
      });
      onUpdated();
      // onClose is called by the parent via onUpdated → setEditingAgent(null);
      // calling it here again would set state on an already-unmounted component.
    } catch (err: any) {
      alert(t('agent:error.updateFailed', { detail: err.response?.data?.detail ?? err.message }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AgentFormModal
      title={t('agent:dialog.editTitle', { name: agent.name })}
      formData={formData}
      onChange={setFormData}
      onSubmit={handleSubmit}
      onClose={onClose}
      loading={loading}
      submitLabel={t('common:button.save')}
      loadingLabel={t('common:button.saving')}
      nameEditable={false}
    />
  );
}

// ============================================================================
// Shared form modal
// ============================================================================

interface FormData {
  name: string;
  description: string;
  descriptionCn: string;
  prompt: string;
  temperature: number;
  mode: string;
  color: string;
}

function AgentFormModal({
  title,
  formData,
  onChange,
  onSubmit,
  onClose,
  loading,
  submitLabel,
  loadingLabel,
  nameEditable,
}: {
  title: string;
  formData: FormData;
  onChange: (data: FormData) => void;
  onSubmit: () => void;
  onClose: () => void;
  loading: boolean;
  submitLabel: string;
  loadingLabel: string;
  nameEditable: boolean;
}) {
  const { t } = useTranslation(['agent', 'common']);
  const isSubmitDisabled = loading || !formData.prompt || (nameEditable && !formData.name);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('agent:form.name')} {nameEditable && <span className="text-slate-500">*</span>}
            </label>
            {nameEditable ? (
              <input
                type="text"
                value={formData.name}
                onChange={(e) => onChange({ ...formData, name: e.target.value })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
                placeholder="my-agent"
              />
            ) : (
              <div className="px-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 font-mono">
                {formData.name}
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('agent:form.description')}</label>
            <p className="text-xs text-gray-500 mb-1">{t('agent:form.descriptionHint')}</p>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => onChange({ ...formData, description: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder={t('agent:form.descriptionPlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('agent:form.descriptionCn')}</label>
            <input
              type="text"
              value={formData.descriptionCn}
              onChange={(e) => onChange({ ...formData, descriptionCn: e.target.value })}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              placeholder={t('agent:form.descriptionCnPlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              System Prompt <span className="text-slate-500">*</span>
            </label>
            <textarea
              value={formData.prompt}
              onChange={(e) => onChange({ ...formData, prompt: e.target.value })}
              className="w-full h-40 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 resize-none font-mono text-sm"
              placeholder="You are a helpful assistant..."
            />
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('agent:form.temperature')}</label>
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={formData.temperature}
                onChange={(e) => onChange({ ...formData, temperature: parseFloat(e.target.value) })}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('agent:form.mode')}</label>
              <select
                value={formData.mode}
                onChange={(e) => onChange({ ...formData, mode: e.target.value })}
                disabled={!nameEditable}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm disabled:bg-gray-50 disabled:text-gray-500"
              >
                <option value="primary">{t('agent:form.primaryModeLabel')}</option>
                <option value="subagent">{t('agent:form.subagentModeLabel')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('agent:form.color')}
                <span className="ml-1 text-xs font-normal text-gray-400">{t('agent:form.colorOptional')}</span>
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={formData.color || '#6B7280'}
                  onChange={(e) => onChange({ ...formData, color: e.target.value })}
                  className="h-9 w-12 rounded border border-gray-300 cursor-pointer p-0.5"
                />
                <input
                  type="text"
                  value={formData.color}
                  onChange={(e) => onChange({ ...formData, color: e.target.value })}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 text-sm font-mono"
                  placeholder="#6B7280"
                />
              </div>
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg disabled:opacity-50"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={onSubmit}
            disabled={isSubmitDisabled}
            className="px-4 py-2 text-sm bg-slate-800 text-white rounded-lg hover:bg-slate-900 disabled:opacity-50"
          >
            {loading ? loadingLabel : submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
