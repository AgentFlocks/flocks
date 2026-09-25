import PageDialog from '../PageDialog';
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
    nameCn: '',
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
    nameCn: agent.nameCn ?? '',
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
        nameCn: formData.nameCn,
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
  nameCn: string;
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
  const themeColor = typeof document === 'undefined' ? '' : getComputedStyle(document.documentElement).getPropertyValue('--accent-ai').trim();
  const defaultColor = /^#[0-9a-f]{6}$/i.test(themeColor) ? themeColor : '';
  const isSubmitDisabled = loading || !formData.prompt || (nameEditable && !formData.name);

  return (
    <PageDialog onClose={loading ? undefined : onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50 p-4">
      <div className="bg-surface rounded-card shadow-overlay max-w-modal w-full max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-line">
          <h3 className="text-title-card font-semibold text-fg">{title}</h3>
        </div>

        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              {t('agent:form.name')} {nameEditable && <span className="text-fg-muted">*</span>}
            </label>
            {nameEditable ? (
              <input
                type="text"
                value={formData.name}
                onChange={(e) => onChange({ ...formData, name: e.target.value })}
                className="ui-input w-full px-4  border border-line-control rounded-control text-sm"
                placeholder="my-agent"
              />
            ) : (
              <div className="px-4 py-2 bg-subtle border border-line rounded-control text-sm text-fg-secondary font-mono">
                {formData.name}
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('agent:form.nameCn')}</label>
            <input
              type="text"
              value={formData.nameCn}
              onChange={(e) => onChange({ ...formData, nameCn: e.target.value })}
              className="ui-input w-full px-4  border border-line-control rounded-control text-sm"
              placeholder={t('agent:form.nameCnPlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('agent:form.description')}</label>
            <p className="text-xs text-fg-muted mb-1">{t('agent:form.descriptionHint')}</p>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => onChange({ ...formData, description: e.target.value })}
              className="ui-input w-full px-4  border border-line-control rounded-control text-sm"
              placeholder={t('agent:form.descriptionPlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">{t('agent:form.descriptionCn')}</label>
            <input
              type="text"
              value={formData.descriptionCn}
              onChange={(e) => onChange({ ...formData, descriptionCn: e.target.value })}
              className="ui-input w-full px-4  border border-line-control rounded-control text-sm"
              placeholder={t('agent:form.descriptionCnPlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-1">
              System Prompt <span className="text-fg-muted">*</span>
            </label>
            <textarea
              value={formData.prompt}
              onChange={(e) => onChange({ ...formData, prompt: e.target.value })}
              className="ui-textarea w-full h-40 px-4  border border-line-control rounded-control resize-none font-mono text-sm"
              placeholder="You are a helpful assistant..."
            />
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1">{t('agent:form.temperature')}</label>
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={formData.temperature}
                onChange={(e) => onChange({ ...formData, temperature: parseFloat(e.target.value) })}
                className="ui-input w-full px-4  border border-line-control rounded-control text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1">{t('agent:form.mode')}</label>
              <select
                value={formData.mode}
                onChange={(e) => onChange({ ...formData, mode: e.target.value })}
                disabled={!nameEditable}
                className="ui-select w-full px-4  border border-line-control rounded-control text-sm disabled:bg-disabled disabled:text-fg-disabled"
              >
                <option value="primary">{t('agent:form.primaryModeLabel')}</option>
                <option value="subagent">{t('agent:form.subagentModeLabel')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1">
                {t('agent:form.color')}
                <span className="ml-1 text-xs font-normal text-fg-muted">{t('agent:form.colorOptional')}</span>
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={formData.color || defaultColor}
                  onChange={(e) => onChange({ ...formData, color: e.target.value })}
                  className="h-9 w-12 rounded-tag border border-line-control cursor-pointer p-0.5"
                />
                <input
                  type="text"
                  value={formData.color}
                  onChange={(e) => onChange({ ...formData, color: e.target.value })}
                  className="ui-input flex-1 px-3  border border-line-control rounded-control text-sm font-mono"
                  placeholder={t('agent:form.colorOptional')}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-line flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            disabled={loading}
            className="px-4 py-2 text-sm text-fg-secondary hover:bg-hover rounded-control disabled:bg-disabled disabled:text-fg-disabled"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={onSubmit}
            disabled={isSubmitDisabled}
            className="ui-btn ui-btn-primary"
          >
            {loading ? loadingLabel : submitLabel}
          </button>
        </div>
      </div>
    </PageDialog>
  );
}
