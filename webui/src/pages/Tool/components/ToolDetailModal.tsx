import { pageActionLabel } from '../../pageActionLabel';
import PageDialog from '../../PageDialog';
import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  IconTool,
  IconX,
  IconInfoCircle,
  IconTestPipe,
  IconPlayerPlay,
  IconRefresh,
  IconCircleCheck,
  IconCircleX,
  IconAlertTriangle,
  IconBook,
  IconChevronDown,
} from '@tabler/icons-react';
import type { Tool, ToolFixture } from '@/api/tool';
import { canDirectlyTestTool, toolAPI } from '@/api/tool';
import { SOURCE_BADGE, CATEGORY_LABEL_KEY } from '../constants';
import { EnabledBadge } from './badges';

interface ToolDetailModalProps {
  tool: Tool;
  initialSection?: 'info' | 'test';
  /** When opening from a specific device's panel, pass the device UUID so
   *  it is pre-filled as `device_id` in the test params template. */
  deviceId?: string;
  onClose: () => void;
}

function buildParamsTemplate(tool: Tool, deviceId?: string): string {
  const obj: Record<string, any> = {};
  if (deviceId) {
    obj['device_id'] = deviceId;
  }
  if (tool.parameters && tool.parameters.length > 0) {
    for (const p of tool.parameters) {
      if (p.name === 'device_id' && deviceId) continue; // injected value above wins
      if (p.required) {
        obj[p.name] = p.type === 'number' || p.type === 'integer' ? 0
          : p.type === 'boolean' ? false
          : '';
      }
    }
  }
  return JSON.stringify(obj, null, 2);
}

export default function ToolDetailModal({ tool, initialSection, deviceId, onClose }: ToolDetailModalProps) {
  const { t, i18n } = useTranslation('tool');
  const [section, setSection] = useState<'info' | 'test'>(initialSection || 'info');
  const defaultParams = useMemo(() => buildParamsTemplate(tool, deviceId), [tool, deviceId]);
  const [testParams, setTestParams] = useState(defaultParams);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const canDirectTest = canDirectlyTestTool(tool);
  const [fixtures, setFixtures] = useState<ToolFixture[]>([]);
  const [fixturesOpen, setFixturesOpen] = useState(false);

  useEffect(() => {
    if (canDirectTest) {
      toolAPI.listFixtures(tool.name)
        .then((res) => setFixtures(res.data || []))
        .catch(() => setFixtures([]));
    }
  }, [tool.name, canDirectTest]);

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
    <PageDialog onClose={onClose} className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50" >
      <div className="bg-surface rounded-card shadow-overlay max-w-modal w-full mx-4 max-h-[85vh] min-h-0 flex flex-col" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="px-6 py-4 border-b border-line flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="w-10 h-10 bg-subtle rounded-control flex items-center justify-center">
                <IconTool aria-hidden="true" className="w-5 h-5 text-fg-secondary" />
              </div>
              <div>
                <h2 className="text-title-section font-semibold text-fg font-mono">{tool.name}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-tag text-xs font-medium ${sb.className}`}>
                    {sourceLabel}
                  </span>
                  <span className="text-xs text-fg-muted">{tool.source_name || 'Flocks'}</span>
                  <span className="text-xs text-fg-muted">{t(CATEGORY_LABEL_KEY[tool.category] ?? 'category.custom')}</span>
                </div>
              </div>
            </div>
            <button aria-label={pageActionLabel('close')} onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-1 rounded-control hover:bg-hover">
              <IconX aria-hidden="true" className="w-5 h-5" />
            </button>
          </div>
          <div className="flex space-x-4 mt-4">
            <button
              onClick={() => setSection('info')}
              className={`flex items-center text-sm font-medium pb-2 border-b-2 transition-colors ${
                section === 'info' ? 'border-danger text-danger' : 'border-transparent text-fg-muted hover:text-fg-secondary'
              }`}
            >
              <IconInfoCircle aria-hidden="true" className="w-4 h-4 mr-1.5" />{t('toolDetail.tabInfo')}
            </button>
            <button
              onClick={() => setSection('test')}
              className={`flex items-center text-sm font-medium pb-2 border-b-2 transition-colors ${
                section === 'test' ? 'border-danger text-danger' : 'border-transparent text-fg-muted hover:text-fg-secondary'
              }`}
            >
              <IconTestPipe aria-hidden="true" className="w-4 h-4 mr-1.5" />{t('toolDetail.tabTest')}
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          {section === 'info' ? (
            <div className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.description')}</label>
                <p className="text-sm text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{tool.description}</p>
              </div>
              <div className="flex flex-wrap gap-4">
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.status')}</label>
                  <EnabledBadge enabled={tool.enabled} />
                </div>
                {tool.requires_confirmation && (
                  <div>
                    <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.security')}</label>
                    <span className="inline-flex items-center px-2.5 py-1 rounded-pill text-xs font-medium bg-warning-soft text-warning">
                      <IconAlertTriangle aria-hidden="true" className="w-4 h-4 mr-1" />{t('toolDetail.requiresConfirmation')}
                    </span>
                  </div>
                )}
              </div>
              {tool.parameters && tool.parameters.length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.params', { count: tool.parameters.length })}</label>
                  <div className="bg-subtle rounded-control border border-line overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="min-w-full table-fixed divide-y divide-line">
                      <thead className="bg-subtle">
                        <tr>
                          <th className="w-[28%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramName')}</th>
                          <th className="w-[16%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramType')}</th>
                          <th className="w-[14%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramRequired')}</th>
                          <th className="w-[42%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramDesc')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {tool.parameters.map((param, idx) => (
                          <tr key={idx}>
                            <td className="px-4 py-2 align-top">
                              <code className="text-xs font-mono text-fg bg-surface px-1.5 py-0.5 rounded-tag border border-line break-all">{param.name}</code>
                            </td>
                            <td className="px-4 py-2 align-top text-xs text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{param.type}</td>
                            <td className="px-4 py-2 align-top">
                              {param.required
                                ? <span className="text-xs text-danger font-medium">{t('toolDetail.yes')}</span>
                                : <span className="text-xs text-fg-muted">{t('toolDetail.no')}</span>}
                            </td>
                            <td className="px-4 py-2 align-top text-xs text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{param.description}</td>
                          </tr>
                        ))}
                      </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}
              {tool.source === 'mcp' && (
                <div className="bg-danger-soft border border-danger rounded-control p-4">
                  <h4 className="text-sm font-medium text-danger mb-1">{t('toolDetail.mcpToolTitle')}</h4>
                  <p className="text-sm text-danger">
                    {t('toolDetail.mcpToolDesc', { server: tool.source_name })}
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {/* Fixtures quick-fill */}
              {fixtures.length > 0 && (
                <div className="rounded-control border border-info bg-info-soft overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setFixturesOpen((v) => !v)}
                    className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-info hover:bg-info-soft transition-colors"
                  >
                    <span className="flex items-center gap-1.5">
                      <IconBook aria-hidden="true" className="w-4 h-4" />
                      测试示例 ({fixtures.length})
                    </span>
                    <IconChevronDown aria-hidden="true" className={`w-4 h-4 transition-transform ${fixturesOpen ? 'rotate-180' : ''}`} />
                  </button>
                  {fixturesOpen && (
                    <div className="border-t border-info divide-y divide-info">
                      {fixtures.map((fx, idx) => {
                        const label = (i18n.language.startsWith('zh') && fx.label_cn) ? fx.label_cn : fx.label;
                        return (
                          <button
                            key={idx}
                            type="button"
                            onClick={() => {
                              const merged = deviceId
                                ? { ...fx.params, device_id: deviceId }
                                : fx.params;
                              setTestParams(JSON.stringify(merged, null, 2));
                              setFixturesOpen(false);
                            }}
                            className="w-full flex items-start gap-3 px-4 py-2.5 hover:bg-info-soft transition-colors text-left"
                          >
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium text-info truncate">{label}</p>
                              <p className="text-xs text-info font-mono truncate mt-0.5">
                                {JSON.stringify(fx.params)}
                              </p>
                            </div>
                            {fx.has_assertion && (
                              <span className="text-xs bg-success-soft text-success px-1.5 py-0.5 rounded-tag flex-shrink-0 mt-0.5">断言</span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              {fixtures.length === 0 && canDirectTest && (
                <p className="text-xs text-fg-muted flex items-center gap-1">
                  <IconBook aria-hidden="true" className="w-4 h-4" />
                  此工具暂无预置测试示例，请手动填写参数
                </p>
              )}

              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testParams')}</label>
                {deviceId && (
                  <p className="text-xs text-info mb-1.5 flex items-center gap-1">
                    <span className="inline-block w-1.5 h-1.5 rounded-pill bg-info flex-shrink-0" />
                    已自动填入当前设备 ID（<code className="font-mono">{deviceId}</code>）
                  </p>
                )}
                <textarea
                  value={testParams}
                  onChange={(e) => setTestParams(e.target.value)}
                  placeholder='{"param": "value"}'
                  rows={6}
                  className="ui-textarea w-full px-4  border border-line-control rounded-control focus:border-transparent font-mono text-sm bg-subtle"
                />
              </div>
              <button
                onClick={handleTest}
                disabled={testing || !tool.enabled || !canDirectTest}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed font-medium text-sm"
              >
                {testing ? (
                  <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('toolDetail.executing')}</>
                ) : (
                  <><IconPlayerPlay aria-hidden="true" className="w-4 h-4" />{t('toolDetail.runTest')}</>
                )}
              </button>
              {!tool.enabled && (
                <p className="text-xs text-warning text-center">{t('toolDetail.disabledNote')}</p>
              )}
              {canDirectTest ? null : (
                <p className="text-xs text-warning text-center">{t('toolDetail.sessionTestOnly')}</p>
              )}
              {testResult && (
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testResult')}</label>
                  <div className={`rounded-control border p-4 ${testResult.success ? 'bg-success-soft border-success' : 'bg-danger-soft border-danger'}`}>
                    <div className="flex items-center mb-2">
                      {testResult.success
                        ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2" />
                        : <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2" />}
                      <span className={`text-sm font-medium ${testResult.success ? 'text-success' : 'text-danger'}`}>
                        {testResult.success ? t('toolDetail.execSuccess') : t('toolDetail.execFailed')}
                      </span>
                    </div>
                    {testResult.error && <p className="text-sm text-danger mb-2">{testResult.error}</p>}
                    {testResult.output != null && (
                      <pre className="text-xs bg-surface p-3 rounded-control overflow-x-auto max-h-60 font-mono">
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
        <div className="px-6 py-4 border-t border-line flex justify-end space-x-3 flex-shrink-0">
          <button onClick={onClose} className="px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary hover:bg-hover">
            {t('toolDetail.close')}
          </button>
          {section === 'info' && (
            <button
              onClick={() => setSection('test')}
              className="px-4 py-2 bg-action rounded-control text-sm font-medium text-action-fg hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed flex items-center"
              disabled={!canDirectTest}
              title={canDirectTest ? undefined : t('toolDetail.sessionTestOnly')}
            >
              <IconTestPipe aria-hidden="true" className="w-4 h-4 mr-1.5" />{t('toolDetail.testTool')}
            </button>
          )}
        </div>
      </div>
    </PageDialog>
  );
}
