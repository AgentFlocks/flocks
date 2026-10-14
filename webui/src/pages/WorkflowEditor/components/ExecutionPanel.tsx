import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Play, CheckCircle, XCircle, Clock, Code, FileText, StopCircle } from 'lucide-react';
import { WorkflowExecution } from '@/api/workflow';

interface ExecutionPanelProps {
  execution: WorkflowExecution | null;
  onClose: () => void;
  onRunAgain?: () => void;
  onStop?: () => void;
  stopping?: boolean;
}

export default function ExecutionPanel({
  execution,
  onClose,
  onRunAgain,
  onStop,
  stopping = false,
}: ExecutionPanelProps) {
  const { t } = useTranslation('workflow');
  const [activeTab, setActiveTab] = useState<'result' | 'log'>('result');

  if (!execution) {
    return null;
  }

  const statusConfig = {
    running: {
      icon: Clock,
      color: 'text-red-600',
      bg: 'bg-red-50',
      border: 'border-red-500',
      label: t('editor.execution.statusRunning'),
    },
    success: {
      icon: CheckCircle,
      color: 'text-green-600',
      bg: 'bg-green-50',
      border: 'border-green-500',
      label: t('editor.execution.statusSuccess'),
    },
    error: {
      icon: XCircle,
      color: 'text-red-600',
      bg: 'bg-red-50',
      border: 'border-red-500',
      label: t('editor.execution.statusError'),
    },
    timeout: {
      icon: Clock,
      color: 'text-yellow-600',
      bg: 'bg-yellow-50',
      border: 'border-yellow-500',
      label: t('editor.execution.statusTimeout'),
    },
    cancelled: {
      icon: StopCircle,
      color: 'text-gray-600',
      bg: 'bg-gray-100',
      border: 'border-gray-400',
      label: t('editor.execution.statusCancelled'),
    },
  };

  const config = statusConfig[execution.status];
  const Icon = config.icon;
  const duration = execution.duration ? (execution.duration / 1000).toFixed(2) : 'N/A';

  return (
    <div className="fixed bottom-0 left-0 right-0 h-96 bg-white shadow-2xl border-t-2 border-gray-200 z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-semibold text-gray-900">{t('editor.execution.title')}</h2>
          
          <div className={`flex items-center gap-2 px-3 py-1 rounded-full border ${config.bg} ${config.border}`}>
            <Icon className={`w-4 h-4 ${config.color}`} />
            <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
          </div>

          <div className="flex items-center gap-4 text-sm text-gray-600">
            <span>ID: {execution.id.slice(0, 8)}...</span>
            <span>{t('editor.execution.duration', { duration })}</span>
            <span>{t('editor.execution.startTime', { time: new Date(execution.startedAt).toLocaleTimeString() })}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {execution.status === 'running' && onStop && (
            <button
              onClick={onStop}
              disabled={stopping}
              className="flex items-center gap-2 px-3 py-1 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <StopCircle className="w-4 h-4" />
              {stopping ? t('detail.run.stopping') : t('editor.execution.stop')}
            </button>
          )}
          {onRunAgain && (
            <button
              onClick={onRunAgain}
              className="flex items-center gap-2 px-3 py-1 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            >
              <Play className="w-4 h-4" />
              {t('editor.execution.runAgain')}
            </button>
          )}
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-200 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-200 bg-gray-50">
        <button
          onClick={() => setActiveTab('result')}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
            activeTab === 'result'
              ? 'bg-white text-red-600 shadow-sm'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          <Code className="w-4 h-4 inline mr-2" />
          {t('editor.execution.tabResult')}
        </button>
        <button
          onClick={() => setActiveTab('log')}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
            activeTab === 'log'
              ? 'bg-white text-red-600 shadow-sm'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          <FileText className="w-4 h-4 inline mr-2" />
          {t('editor.execution.tabLog')}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === 'result' && (
          <div className="space-y-4">
            {/* Input Parameters */}
            {execution.inputParams && Object.keys(execution.inputParams).length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">{t('editor.execution.inputParams')}</h3>
                <pre className="p-4 bg-gray-900 text-gray-300 rounded-lg text-xs overflow-x-auto font-mono">
                  {JSON.stringify(execution.inputParams, null, 2)}
                </pre>
              </div>
            )}

            {/* Output Results */}
            {execution.status === 'success' && execution.outputResults && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">{t('editor.execution.outputResults')}</h3>
                <pre className="p-4 bg-gray-900 text-gray-300 rounded-lg text-xs overflow-x-auto font-mono">
                  {JSON.stringify(execution.outputResults, null, 2)}
                </pre>
              </div>
            )}

            {/* Error Message */}
            {execution.status === 'error' && execution.errorMessage && (
              <div>
                <h3 className="text-sm font-semibold text-red-700 mb-2">{t('editor.execution.errorMessage')}</h3>
                <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
                  <p className="text-sm text-red-800 font-mono whitespace-pre-wrap">
                    {execution.errorMessage}
                  </p>
                </div>
              </div>
            )}

            {execution.status === 'cancelled' && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">{t('editor.execution.statusCancelled')}</h3>
                <div className="p-4 bg-gray-50 border border-gray-200 rounded-lg">
                  <p className="text-sm text-gray-700">{t('editor.execution.cancelledMessage')}</p>
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'log' && (
          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">{t('editor.execution.tabLog')}</h3>
            {execution.executionLog && execution.executionLog.length > 0 ? (
              <div className="space-y-2">
                {execution.executionLog.map((log: any, index: number) => (
                  <div
                    key={index}
                    className="p-3 bg-gray-50 border border-gray-200 rounded-lg"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-700">
                        {log.node || t('editor.execution.stepLabel', { num: index + 1 })}
                      </span>
                      <span className="text-xs text-gray-500">
                        {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
                      </span>
                    </div>
                    <pre className="text-xs text-gray-600 font-mono whitespace-pre-wrap">
                      {typeof log === 'string' ? log : JSON.stringify(log, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500">
                <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p className="text-sm">{t('editor.execution.noLog')}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
