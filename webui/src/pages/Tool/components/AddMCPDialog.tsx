import { pageActionLabel } from '../../pageActionLabel';
import PageSelectionGroup from '../../PageSelectionGroup';
import PageDialog from '../../PageDialog';
import { useState } from 'react';
import {
  IconDatabase,
  IconX,
  IconSettings,
  IconMessage,
  IconInfoCircle,
  IconPlus,
  IconRefresh,
} from '@tabler/icons-react';
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
    <PageDialog onClose={submitting ? undefined : onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50">
      <div className="bg-surface rounded-card shadow-overlay max-w-modal w-full mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-line flex-shrink-0">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-control bg-danger-soft flex items-center justify-center">
                <IconDatabase aria-hidden="true" className="w-5 h-5 text-danger" />
              </div>
              <div>
                <h2 className="text-title-section font-semibold text-fg">{t('addMCP.title')}</h2>
                <p className="text-xs text-fg-muted">{t('addMCP.subtitle')}</p>
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
                {t('addMCP.serviceName')} <span className="text-danger">*</span>
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('addMCP.serviceNamePlaceholder')}
                className="ui-input w-full px-3  border border-line-control rounded-control text-sm focus:border-transparent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('addMCP.connectionType')}</label>
              <div className="flex gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    value="stdio"
                    checked={connType === 'stdio'}
                    onChange={() => setConnType('stdio')}
                    className="w-4 h-4 text-danger "
                  />
                  <span className="text-sm text-fg-secondary">{t('addMCP.stdioLocal')}</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    value="sse"
                    checked={connType === 'sse'}
                    onChange={() => setConnType('sse')}
                    className="w-4 h-4 text-danger "
                  />
                  <span className="text-sm text-fg-secondary">{t('addMCP.remoteSSE')}</span>
                </label>
              </div>
            </div>

            {connType === 'stdio' ? (
              <>
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-1.5">
                    {t('addMCP.startCommand')} <span className="text-danger">*</span>
                  </label>
                  <input
                    type="text"
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    placeholder={t('addMCP.startCommandPlaceholder')}
                    className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('addMCP.commandArgs')}</label>
                  <textarea
                    value={args}
                    onChange={(e) => setArgs(e.target.value)}
                    placeholder={t('addMCP.commandArgsPlaceholder')}
                    rows={3}
                    className="ui-textarea w-full px-3  border border-line-control rounded-control text-sm font-mono focus:border-transparent"
                  />
                  <p className="mt-1 text-xs text-fg-muted">{t('addMCP.oneArgPerLine')}</p>
                </div>
              </>
            ) : (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1.5">
                  {t('addMCP.serviceUrl')} <span className="text-danger">*</span>
                </label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={t('addMCP.serviceUrlPlaceholder')}
                  className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono focus:border-transparent"
                />
              </div>
            )}

            <div className="p-3 bg-danger-soft border border-danger rounded-control">
              <div className="flex items-start gap-2">
                <IconInfoCircle aria-hidden="true" className="w-4 h-4 text-danger mt-0.5 flex-shrink-0" />
                <div className="text-xs text-danger">
                  <p className="font-medium mb-1">{t('addMCP.hintTitle')}</p>
                  <p>{t('addMCP.hintDesc')}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-line flex justify-between flex-shrink-0">
          <button onClick={openChat} className="inline-flex items-center px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary hover:bg-hover transition-colors">
            <IconMessage aria-hidden="true" className="w-4 h-4 mr-1.5" />
            {t('addMCP.chatIntegration')}
          </button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary hover:bg-hover">
              {t('button.cancel')}
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting || !canSubmit}
              className="px-4 py-2 bg-action text-action-fg rounded-control text-sm font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed flex items-center"
            >
              {submitting ? (
                <><IconRefresh aria-hidden="true" className="w-4 h-4 mr-1.5 animate-spin" />{t('addMCP.adding')}</>
              ) : (
                <><IconPlus aria-hidden="true" className="w-4 h-4 mr-1.5" />{t('addMCP.addService')}</>
              )}
            </button>
          </div>
        </div>
      </div>
    </PageDialog>
  );
}
