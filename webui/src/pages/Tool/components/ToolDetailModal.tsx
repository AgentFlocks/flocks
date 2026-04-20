import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Wrench, X, Info, TestTube, Play, RefreshCw,
  CheckCircle, XCircle, AlertTriangle,
} from 'lucide-react';
import type { Tool } from '@/api/tool';
import { canDirectlyTestTool, toolAPI } from '@/api/tool';
import { SOURCE_BADGE, CATEGORY_LABEL_KEY } from '../constants';
import { EnabledBadge } from './badges';

interface ToolDetailModalProps {
  tool: Tool;
  initialSection?: 'info' | 'test';
  onClose: () => void;
}

function buildParamsTemplate(tool: Tool): string {
  if (!tool.parameters || tool.parameters.length === 0) return '{}';
  const obj: Record<string, string> = {};
  for (const p of tool.parameters) {
    if (p.required) {
      obj[p.name] = p.type === 'number' || p.type === 'integer' ? '0' as any
        : p.type === 'boolean' ? 'false' as any
        : '';
    }
  }
  return JSON.stringify(obj, null, 2);
}

export default function ToolDetailModal({ tool, initialSection, onClose }: ToolDetailModalProps) {
  const { t } = useTranslation('tool');
  const [section, setSection] = useState<'info' | 'test'>(initialSection || 'info');
  const defaultParams = useMemo(() => buildParamsTemplate(tool), [tool]);
  const [testParams, setTestParams] = useState(defaultParams);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const canDirectTest = canDirectlyTestTool(tool);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const sb = SOURCE_BADGE[tool.source] ?? SOURCE_BADGE.custom;
  const sourceLabel = sb.labelKey ? t(sb.labelKey) : (sb.label ?? tool.source);

  const handleTest = async () => {
    if (!canDirectTest) return;
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const response = await toolAPI.test(tool.name, params);
      setTestResult(response.data);
    } catch (err: any) {
      setTestResult({ success: false, error: err.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-gray-600 bg-opacity-75 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full mx-4 max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-gray-50 rounded-lg flex items-center justify-center">
                <Wrench className="w-5 h-5 text-gray-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900 font-mono">{tool.name}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${sb.className}`}>
                    {sourceLabel}
                  </span>
                  <span className="text-xs text-gray-500">{tool.source_name || 'Flocks'}</span>
                  <span className="text-xs text-gray-400">{t(CATEGORY_LABEL_KEY[tool.category] ?? 'category.custom')}</span>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100">
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex space-x-4 mt-4">
            <button
              onClick={() => setSection('info')}
              className={`flex items-center text-sm font-medium pb-2 border-b-2 transition-colors ${
                section === 'info' ? 'border-red-500 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <Info className="w-4 h-4 mr-1.5" />{t('toolDetail.tabInfo')}
            </button>
            <button
              onClick={() => setSection('test')}
              className={`flex items-center text-sm font-medium pb-2 border-b-2 transition-colors ${
                section === 'test' ? 'border-red-500 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <TestTube className="w-4 h-4 mr-1.5" />{t('toolDetail.tabTest')}
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {section === 'info' ? (
            <div className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.description')}</label>
                <p className="text-sm text-gray-600 leading-relaxed">{tool.description}</p>
              </div>
              <div className="flex flex-wrap gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.status')}</label>
                  <EnabledBadge enabled={tool.enabled} />
                </div>
                {tool.requires_confirmation && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.security')}</label>
                    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                      <AlertTriangle className="w-3.5 h-3.5 mr-1" />{t('toolDetail.requiresConfirmation')}
                    </span>
                  </div>
                )}
              </div>
              {tool.parameters && tool.parameters.length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.params', { count: tool.parameters.length })}</label>
                  <div className="bg-gray-50 rounded-lg border border-gray-200 overflow-hidden">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-100">
                        <tr>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramName')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramType')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramRequired')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramDesc')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {tool.parameters.map((param, idx) => (
                          <tr key={idx}>
                            <td className="px-4 py-2">
                              <code className="text-xs font-mono text-gray-900 bg-white px-1.5 py-0.5 rounded border border-gray-200">{param.name}</code>
                            </td>
                            <td className="px-4 py-2 text-xs text-gray-600">{param.type}</td>
                            <td className="px-4 py-2">
                              {param.required
                                ? <span className="text-xs text-red-600 font-medium">{t('toolDetail.yes')}</span>
                                : <span className="text-xs text-gray-400">{t('toolDetail.no')}</span>}
                            </td>
                            <td className="px-4 py-2 text-xs text-gray-600">{param.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {tool.source === 'mcp' && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                  <h4 className="text-sm font-medium text-red-900 mb-1">{t('toolDetail.mcpToolTitle')}</h4>
                  <p className="text-sm text-red-800">
                    {t('toolDetail.mcpToolDesc', { server: tool.source_name })}
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.testParams')}</label>
                <textarea
                  value={testParams}
                  onChange={(e) => setTestParams(e.target.value)}
                  placeholder='{"param": "value"}'
                  rows={6}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent font-mono text-sm bg-gray-50"
                />
              </div>
              <button
                onClick={handleTest}
                disabled={testing || !tool.enabled || !canDirectTest}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm"
              >
                {testing ? (
                  <><RefreshCw className="w-4 h-4 animate-spin" />{t('toolDetail.executing')}</>
                ) : (
                  <><Play className="w-4 h-4" />{t('toolDetail.runTest')}</>
                )}
              </button>
              {!tool.enabled && (
                <p className="text-xs text-amber-600 text-center">{t('toolDetail.disabledNote')}</p>
              )}
              {canDirectTest ? null : (
                <p className="text-xs text-amber-600 text-center">{t('toolDetail.sessionTestOnly')}</p>
              )}
              {testResult && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.testResult')}</label>
                  <div className={`rounded-lg border p-4 ${testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                    <div className="flex items-center mb-2">
                      {testResult.success
                        ? <CheckCircle className="w-4 h-4 text-green-600 mr-2" />
                        : <XCircle className="w-4 h-4 text-red-600 mr-2" />}
                      <span className={`text-sm font-medium ${testResult.success ? 'text-green-800' : 'text-red-800'}`}>
                        {testResult.success ? t('toolDetail.execSuccess') : t('toolDetail.execFailed')}
                      </span>
                    </div>
                    {testResult.error && <p className="text-sm text-red-700 mb-2">{testResult.error}</p>}
                    {testResult.output != null && (
                      <pre className="text-xs bg-white bg-opacity-60 p-3 rounded-md overflow-x-auto max-h-60 font-mono">
                        {typeof testResult.output === 'string' ? testResult.output : JSON.stringify(testResult.output, null, 2)}
                      </pre>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-200 flex justify-end space-x-3 flex-shrink-0">
          <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">
            {t('toolDetail.close')}
          </button>
          {section === 'info' && (
            <button
              onClick={() => setSection('test')}
              className="px-4 py-2 bg-red-600 rounded-lg text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
              disabled={!canDirectTest}
              title={canDirectTest ? undefined : t('toolDetail.sessionTestOnly')}
            >
              <TestTube className="w-4 h-4 mr-1.5" />{t('toolDetail.testTool')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
