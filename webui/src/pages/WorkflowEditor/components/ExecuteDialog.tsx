import { pageActionLabel } from '../../pageActionLabel';
import PageDialog from '../../PageDialog';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IconX, IconPlayerPlay, IconAlertCircle } from '@tabler/icons-react';

interface ExecuteDialogProps {
  onClose: () => void;
  onExecute: (params: Record<string, any>, options: { trace: boolean; timeoutS: number }) => void;
}

export default function ExecuteDialog({ onClose, onExecute }: ExecuteDialogProps) {
  const { t } = useTranslation('workflow');
  const [paramsText, setParamsText] = useState('{}');
  const [trace, setTrace] = useState(true);
  const [timeoutS, setTimeoutS] = useState(300);
  const [error, setError] = useState<string | null>(null);

  const handleExecute = () => {
    try {
      const params = JSON.parse(paramsText);
      onExecute(params, { trace, timeoutS });
      onClose();
    } catch (err: any) {
      setError(t('editor.dialog.jsonError', { error: err.message }));
    }
  };

  return (
    <PageDialog onClose={onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50">
      <div className="bg-surface rounded-control shadow-overlay w-full max-w-modal max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-line">
          <div>
            <h2 className="text-xl font-semibold text-fg">{t('editor.dialog.title')}</h2>
            <p className="text-sm text-fg-muted mt-1">{t('editor.dialog.subtitle')}</p>
          </div>
          <button aria-label={pageActionLabel('close')}
            onClick={onClose}
            className="ui-btn-icon inline-flex items-center justify-center p-2 hover:bg-hover rounded-control transition-colors"
          >
            <IconX aria-hidden="true" className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Input Parameters */}
          <div>
            <label className="block text-sm font-medium text-fg-secondary mb-2">
              {t('editor.dialog.inputParams')}
            </label>
            <textarea
              value={paramsText}
              onChange={(e) => {
                setParamsText(e.target.value);
                setError(null);
              }}
              rows={12}
              className="ui-textarea w-full px-3  border border-line-control rounded-control font-mono text-sm resize-none bg-subtle text-fg"
              placeholder='{\n  "key1": "value1",\n  "key2": "value2"\n}'
              spellCheck={false}
            />
            {error && (
              <div className="mt-2 flex items-start gap-2 p-3 bg-danger-soft border border-danger rounded-control">
                <IconAlertCircle aria-hidden="true" className="w-4 h-4 text-danger flex-shrink-0 mt-0.5" />
                <p className="text-sm text-danger">{error}</p>
              </div>
            )}
          </div>

          {/* Options */}
          <div className="space-y-3 pt-4 border-t border-line">
            <h3 className="text-sm font-semibold text-fg">{t('editor.dialog.options')}</h3>
            
            {/* Trace */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={trace}
                onChange={(e) => setTrace(e.target.checked)}
                className="w-4 h-4 text-danger border-line-control rounded-tag "
              />
              <label className="text-sm text-fg-secondary">
                {t('editor.dialog.traceLabel')}
              </label>
            </div>

            {/* Timeout */}
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1">
                {t('editor.dialog.timeout')}
              </label>
              <input
                type="number"
                value={timeoutS}
                onChange={(e) => setTimeoutS(parseInt(e.target.value) || 300)}
                min={10}
                max={3600}
                className="ui-input w-full px-3  border border-line-control rounded-control text-sm"
              />
              <p className="text-xs text-fg-muted mt-1">
                {t('editor.dialog.timeoutHint')}
              </p>
            </div>
          </div>

          {/* Tips */}
          <div className="p-3 bg-danger-soft border border-danger rounded-control">
            <p className="text-xs text-danger">
              {t('editor.dialog.tip')}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-line bg-subtle">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-line-control text-fg-secondary rounded-control hover:bg-hover transition-colors"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={handleExecute}
            className="flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
          >
            <IconPlayerPlay aria-hidden="true" className="w-4 h-4" />
            {t('editor.dialog.startExecute')}
          </button>
        </div>
      </div>
    </PageDialog>
  );
}
