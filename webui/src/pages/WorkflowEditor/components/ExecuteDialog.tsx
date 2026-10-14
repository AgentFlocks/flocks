import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Play, AlertCircle } from 'lucide-react';

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
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">{t('editor.dialog.title')}</h2>
            <p className="text-sm text-gray-500 mt-1">{t('editor.dialog.subtitle')}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* Input Parameters */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {t('editor.dialog.inputParams')}
            </label>
            <textarea
              value={paramsText}
              onChange={(e) => {
                setParamsText(e.target.value);
                setError(null);
              }}
              rows={12}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm resize-none bg-gray-900 text-gray-300"
              placeholder='{\n  "key1": "value1",\n  "key2": "value2"\n}'
              spellCheck={false}
            />
            {error && (
              <div className="mt-2 flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
                <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-800">{error}</p>
              </div>
            )}
          </div>

          {/* Options */}
          <div className="space-y-3 pt-4 border-t border-gray-200">
            <h3 className="text-sm font-semibold text-gray-900">{t('editor.dialog.options')}</h3>
            
            {/* Trace */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={trace}
                onChange={(e) => setTrace(e.target.checked)}
                className="w-4 h-4 text-red-600 border-gray-300 rounded focus:ring-red-500"
              />
              <label className="text-sm text-gray-700">
                {t('editor.dialog.traceLabel')}
              </label>
            </div>

            {/* Timeout */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('editor.dialog.timeout')}
              </label>
              <input
                type="number"
                value={timeoutS}
                onChange={(e) => setTimeoutS(parseInt(e.target.value) || 300)}
                min={10}
                max={3600}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                {t('editor.dialog.timeoutHint')}
              </p>
            </div>
          </div>

          {/* Tips */}
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-xs text-red-800">
              {t('editor.dialog.tip')}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200 bg-gray-50">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-100 transition-colors"
          >
            {t('common:button.cancel')}
          </button>
          <button
            onClick={handleExecute}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
          >
            <Play className="w-4 h-4" />
            {t('editor.dialog.startExecute')}
          </button>
        </div>
      </div>
    </div>
  );
}
