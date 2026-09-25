import { pageActionLabel } from '../../pageActionLabel';
import PageSelectionGroup from '../../PageSelectionGroup';
import PageDialog from '../../PageDialog';
import { useState } from 'react';
import {
  IconCloud,
  IconX,
  IconSettings,
  IconMessage,
  IconInfoCircle,
  IconSparkles,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

interface AddAPIDialogProps {
  onClose: () => void;
  onSwitchToChat: (prompt: string, title: string, subtitle: string) => void;
}

export default function AddAPIDialog({ onClose, onSwitchToChat }: AddAPIDialogProps) {
  const { t } = useTranslation('tool');
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [description, setDescription] = useState('');

  const handleSubmit = () => {
    const parts: string[] = [];
    if (name) parts.push(`API Service: ${name}`);
    if (baseUrl) parts.push(`Base URL: ${baseUrl}`);
    if (description) parts.push(`Description: ${description}`);
    if (apiKey) parts.push(`API Key: ${apiKey}`);

    const prompt = parts.length > 0
      ? t('addAPI.chatPrompt', { details: parts.join('\n'), defaultValue: `Help me connect the following API service as a tool:\n${parts.join('\n')}` })
      : '';
    onSwitchToChat(prompt, t('addAPI.title'), t('addAPI.chatSubtitle'));
    onClose();
  };

  const openChat = () => {
    onSwitchToChat('', t('addAPI.title'), t('addAPI.chatOpenSubtitle', { defaultValue: t('addAPI.subtitle') }));
    onClose();
  };

  return (
    <PageDialog onClose={onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50">
      <div className="bg-surface rounded-card shadow-overlay max-w-modal w-full mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-line flex-shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-control bg-ai-soft flex items-center justify-center">
                <IconCloud aria-hidden="true" className="w-5 h-5 text-ai" />
              </div>
              <div>
                <h2 className="text-title-section font-semibold text-fg">{t('addAPI.title')}</h2>
                <p className="text-xs text-fg-muted">{t('addAPI.subtitle')}</p>
              </div>
            </div>
            <button aria-label={pageActionLabel('close')} onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-1 rounded-control hover:bg-hover">
              <IconX aria-hidden="true" className="w-5 h-5" />
            </button>
          </div>
          <PageSelectionGroup className="flex space-x-1 ">
            <button aria-pressed={true} className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-segment text-sm font-medium bg-selected text-fg">
              <IconSettings aria-hidden="true" className="w-4 h-4" />
              {t('addMCP.formConfig')}
            </button>
            <button aria-pressed={false}
              onClick={openChat}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-segment text-sm font-medium text-fg-muted hover:text-fg-secondary"
            >
              <IconMessage aria-hidden="true" className="w-4 h-4" />
              {t('addMCP.chatAssistant')}
            </button>
          </PageSelectionGroup>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">
                {t('addAPI.apiServiceName')} <span className="text-danger">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('addAPI.apiServiceNamePlaceholder')}
                className="ui-input w-full px-3  border border-line-control rounded-control text-sm focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">Base URL</label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={t('addAPI.baseUrlPlaceholder')}
                className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t('addAPI.apiKeyPlaceholder')}
                className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('addAPI.funcDescription')}</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('addAPI.funcDescriptionPlaceholder')}
                rows={3}
                className="ui-textarea w-full px-3  border border-line-control rounded-control text-sm focus:border-transparent"
              />
            </div>

            <div className="p-3 bg-ai-soft border border-ai rounded-control">
              <div className="flex items-start gap-2">
                <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-ai mt-0.5 flex-shrink-0" />
                <div className="text-xs text-ai">
                  <p className="font-medium mb-1">{t('addAPI.hintTitle')}</p>
                  <p>{t('addAPI.hintDesc')}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-line flex justify-between flex-shrink-0">
          <button onClick={openChat} className="inline-flex items-center px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary hover:bg-hover transition-colors">
            <IconMessage aria-hidden="true" className="w-4 h-4 mr-1.5" />
            {t('addAPI.chatIntegration')}
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary hover:bg-hover">
              {t('button.cancel')}
            </button>
            <button
              onClick={handleSubmit}
              disabled={!name.trim()}
              className="px-4 py-2 bg-action text-action-fg rounded-control text-sm font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed flex items-center"
            >
              <IconSparkles aria-hidden="true" className="w-4 h-4 mr-1.5" />
              {t('addAPI.submitToRex')}
            </button>
          </div>
        </div>
      </div>
    </PageDialog>
  );
}
