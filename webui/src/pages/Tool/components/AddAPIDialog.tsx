import { useState } from 'react';
import {
  Cloud, X, Settings, MessageSquare, Info, Sparkles,
} from 'lucide-react';
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
    <div className="fixed inset-0 bg-gray-600/75 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-purple-50 flex items-center justify-center">
                <Cloud className="w-5 h-5 text-purple-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">{t('addAPI.title')}</h2>
                <p className="text-xs text-gray-500">{t('addAPI.subtitle')}</p>
              </div>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100">
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex space-x-1 bg-gray-100 rounded-lg p-1">
            <button className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium bg-white text-gray-900 shadow-sm">
              <Settings className="w-4 h-4" />
              {t('addMCP.formConfig')}
            </button>
            <button
              onClick={openChat}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium text-gray-500 hover:text-gray-700"
            >
              <MessageSquare className="w-4 h-4" />
              {t('addMCP.chatAssistant')}
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                {t('addAPI.apiServiceName')} <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('addAPI.apiServiceNamePlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Base URL</label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={t('addAPI.baseUrlPlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t('addAPI.apiKeyPlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addAPI.funcDescription')}</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('addAPI.funcDescriptionPlaceholder')}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            <div className="p-3 bg-purple-50 border border-purple-200 rounded-lg">
              <div className="flex items-start gap-2">
                <Info className="w-4 h-4 text-purple-600 mt-0.5 flex-shrink-0" />
                <div className="text-xs text-purple-800">
                  <p className="font-medium mb-1">{t('addAPI.hintTitle')}</p>
                  <p>{t('addAPI.hintDesc')}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 flex justify-between flex-shrink-0">
          <button onClick={openChat} className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
            <MessageSquare className="w-4 h-4 mr-1.5" />
            {t('addAPI.chatIntegration')}
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">
              {t('button.cancel')}
            </button>
            <button
              onClick={handleSubmit}
              disabled={!name.trim()}
              className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
            >
              <Sparkles className="w-4 h-4 mr-1.5" />
              {t('addAPI.submitToRex')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
