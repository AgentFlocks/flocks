import { useState } from 'react';
import {
  Database, X, Settings, MessageSquare, Info, Plus, RefreshCw,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import client from '@/api/client';
import { useToast } from '@/components/common/Toast';

interface AddMCPDialogProps {
  onClose: () => void;
  onSuccess: () => void;
  onSwitchToChat: (prompt: string, title: string, subtitle: string) => void;
}

export default function AddMCPDialog({ onClose, onSuccess, onSwitchToChat }: AddMCPDialogProps) {
  const { t } = useTranslation('tool');
  const toast = useToast();
  const [name, setName] = useState('');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [url, setUrl] = useState('');
  const [connType, setConnType] = useState<'stdio' | 'sse'>('stdio');
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = name.trim() && (connType === 'stdio' ? command.trim() : url.trim());

  const handleSubmit = async () => {
    if (!name.trim()) {
      toast.warning(t('alert.mcpNameRequired'));
      return;
    }
    if (connType === 'stdio' && !command.trim()) {
      toast.warning(t('addMCP.startCommandRequired'));
      return;
    }
    if (connType === 'sse' && !url.trim()) {
      toast.warning(t('addMCP.serviceUrlRequired'));
      return;
    }
    try {
      setSubmitting(true);
      const config: any = { type: connType };
      if (connType === 'stdio') {
        config.command = command;
        if (args.trim()) config.args = args.split('\n').filter(Boolean);
      } else {
        config.url = url;
      }
      await client.post('/api/mcp', { name, config });
      toast.success(t('alert.mcpAddSuccess'));
      onClose();
      onSuccess();
    } catch (err: any) {
      toast.error(t('alert.addFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setSubmitting(false);
    }
  };

  const openChat = () => {
    const prompt = name ? t('addMCP.chatPrompt', { name, defaultValue: `Help me connect an MCP service named "${name}"` }) : '';
    onSwitchToChat(prompt, t('addMCP.title'), t('addMCP.chatSubtitle', { defaultValue: t('addMCP.subtitle') }));
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-gray-600/75 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-red-50 flex items-center justify-center">
                <Database className="w-5 h-5 text-red-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">{t('addMCP.title')}</h2>
                <p className="text-xs text-gray-500">{t('addMCP.subtitle')}</p>
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
                {t('addMCP.serviceName')} <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('addMCP.serviceNamePlaceholder')}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.connectionType')}</label>
              <div className="flex gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    value="stdio"
                    checked={connType === 'stdio'}
                    onChange={() => setConnType('stdio')}
                    className="w-4 h-4 text-red-600 focus:ring-red-500"
                  />
                  <span className="text-sm text-gray-700">{t('addMCP.stdioLocal')}</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    value="sse"
                    checked={connType === 'sse'}
                    onChange={() => setConnType('sse')}
                    className="w-4 h-4 text-red-600 focus:ring-red-500"
                  />
                  <span className="text-sm text-gray-700">{t('addMCP.remoteSSE')}</span>
                </label>
              </div>
            </div>

            {connType === 'stdio' ? (
              <>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    {t('addMCP.startCommand')} <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    placeholder={t('addMCP.startCommandPlaceholder')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.commandArgs')}</label>
                  <textarea
                    value={args}
                    onChange={(e) => setArgs(e.target.value)}
                    placeholder={t('addMCP.commandArgsPlaceholder')}
                    rows={3}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
                  />
                  <p className="mt-1 text-xs text-gray-500">{t('addMCP.oneArgPerLine')}</p>
                </div>
              </>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  {t('addMCP.serviceUrl')} <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={t('addMCP.serviceUrlPlaceholder')}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
                />
              </div>
            )}

            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
              <div className="flex items-start gap-2">
                <Info className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
                <div className="text-xs text-red-800">
                  <p className="font-medium mb-1">{t('addMCP.hintTitle')}</p>
                  <p>{t('addMCP.hintDesc')}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 flex justify-between flex-shrink-0">
          <button onClick={openChat} className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
            <MessageSquare className="w-4 h-4 mr-1.5" />
            {t('addMCP.chatIntegration')}
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">
              {t('button.cancel')}
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting || !canSubmit}
              className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
            >
              {submitting ? (
                <><RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />{t('addMCP.adding')}</>
              ) : (
                <><Plus className="w-4 h-4 mr-1.5" />{t('addMCP.addService')}</>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
