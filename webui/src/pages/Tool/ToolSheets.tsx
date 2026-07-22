/**
 * ToolSheets — 工具添加/生成的右侧抽屉组件
 *
 * - MCPSheet: 添加 MCP 服务（表单 + AI 对话）
 * - APISheet: 添加 API 工具（表单 + AI 对话）
 * - GenerateToolSheet: AI 生成自定义工具
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Database, Cloud, Code, Info, CheckCircle, XCircle, Activity, Wifi, WifiOff, Loader2 } from 'lucide-react';
import EntitySheet from '@/components/common/EntitySheet';
import { buildGuidedCreateGroups } from '@/components/common/GuidedCreatePanel';
import { useRexComposerControls } from '@/components/common/useRexComposerControls';
import client from '@/api/client';
import { mcpAPI } from '@/api/mcp';

// ─── MCPSheet ─────────────────────────────────────────────────────────────────

const MCP_REX_CONTEXT = `你是 MCP 服务接入专家，帮助用户将 MCP（Model Context Protocol）服务接入系统。

**MCP 连接方式：**
- Stdio（本地进程）：通过命令行启动本地 MCP 服务，如 npx、uvx、python 等
- SSE（远程服务）：连接已部署的 MCP HTTP/SSE 服务

**常见 MCP 服务示例：**
- GitHub: \`npx -y @modelcontextprotocol/server-github\` (需要 GITHUB_TOKEN 环境变量)
- Filesystem: \`npx -y @modelcontextprotocol/server-filesystem /path\`
- Slack: \`npx -y @modelcontextprotocol/server-slack\`
- Playwright: \`npx -y @modelcontextprotocol/server-playwright\`

请帮助用户配置 MCP 服务，询问所需信息，并通过调用工具完成接入。`;

const MCP_REX_WELCOME = `你好！我来帮你接入一个 MCP 服务。

请告诉我：
- 你想接入什么 MCP 服务？（GitHub、Slack、Filesystem、或其他）
- 这个服务的连接方式是本地进程还是远程服务？

我会帮你完成配置和接入。`;

export interface MCPFormData {
  name: string;
  connType: 'stdio' | 'sse';
  command: string;
  args: string;
  url: string;
  transport: 'auto' | 'sse' | 'http';
  authType: 'none' | 'bearer' | 'header' | 'query';
  authValue: string;
  authHeaderName: string;
  authQueryName: string;
  headersText: string;
}

export type ConnStatus = 'idle' | 'saving' | 'testing' | 'tested' | 'connected' | 'failed';

function parseHeadersText(headersText: string): Record<string, string> | undefined {
  const trimmed = headersText.trim();
  if (!trimmed) return undefined;
  const parsed = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('headers must be an object');
  }
  return Object.fromEntries(
    Object.entries(parsed).map(([key, value]) => [String(key), String(value)]),
  );
}

function stringifyHeaders(headers?: Record<string, string>): string {
  if (!headers || Object.keys(headers).length === 0) return '';
  return JSON.stringify(headers, null, 2);
}

export function getMCPFormError(formData: MCPFormData): 'invalidHeaders' | null {
  if (formData.connType !== 'sse') return null;
  try {
    parseHeadersText(formData.headersText);
    return null;
  } catch {
    return 'invalidHeaders';
  }
}

export function buildMCPConfigFromForm(formData: MCPFormData): Record<string, any> {
  const config: Record<string, any> = { type: formData.connType };
  if (formData.connType === 'stdio') {
    const args = formData.args
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
    const command = formData.command.trim();
    config.command = command ? [command, ...args] : args;
  } else {
    config.url = formData.url.trim();
    config.transport = formData.transport;

    try {
      const headers = parseHeadersText(formData.headersText);
      if (headers) config.headers = headers;
    } catch {
      // Validation happens in the submit/test handlers; keep the builder tolerant.
    }

    const authValue = formData.authValue.trim();
    if (formData.authType === 'bearer' && authValue) {
      config.auth = {
        type: 'apikey',
        scheme: 'bearer',
        location: 'header',
        param_name: 'Authorization',
        value: authValue.startsWith('Bearer ') ? authValue.slice('Bearer '.length) : authValue,
      };
    } else if (formData.authType === 'header' && authValue) {
      config.auth = {
        type: 'apikey',
        location: 'header',
        param_name: formData.authHeaderName.trim() || 'X-API-Key',
        value: authValue,
      };
    } else if (formData.authType === 'query' && authValue) {
      config.auth = {
        type: 'apikey',
        location: 'query',
        param_name: formData.authQueryName.trim() || 'apikey',
        value: authValue,
      };
    }
  }
  return config;
}

export function buildMCPFormDataFromConfig(
  name: string,
  config?: {
    type?: 'stdio' | 'sse' | 'local' | 'remote';
    command?: string | string[];
    args?: string | string[];
    url?: string;
    transport?: 'auto' | 'sse' | 'http';
    headers?: Record<string, string>;
    auth?: {
      scheme?: 'bearer' | string;
      location?: 'header' | 'query';
      param_name?: string;
      value?: string;
    } | null;
  } | null,
  fallbackUrl?: string,
): MCPFormData {
  const connType = (config?.type ?? (fallbackUrl ? 'sse' : 'stdio')) as 'stdio' | 'sse';
  const rawCommand = config?.command;
  const commandParts = Array.isArray(rawCommand)
    ? rawCommand.map((item) => String(item).trim()).filter(Boolean)
    : (typeof rawCommand === 'string' && rawCommand.trim() ? [rawCommand.trim()] : []);
  const rawArgs = config?.args;
  const extraArgs = Array.isArray(rawArgs)
    ? rawArgs.map((item) => String(item).trim()).filter(Boolean)
    : (typeof rawArgs === 'string'
      ? rawArgs.split('\n').map((item) => item.trim()).filter(Boolean)
      : []);

  const auth = config?.auth;
  const authScheme = String(auth?.scheme || '').trim().toLowerCase();
  const authLocation = auth?.location;
  const authParamName = auth?.param_name || '';
  const authRawValue = auth?.value || '';
  const isBearerAuth = authScheme === 'bearer' || (
    authLocation === 'header'
    && authParamName.toLowerCase() === 'authorization'
    && (
      authRawValue.startsWith('Bearer ')
      || authRawValue.startsWith('{secret:')
      || authRawValue.startsWith('${')
    )
  );
  const authType: MCPFormData['authType'] = !authRawValue
    ? 'none'
    : isBearerAuth
      ? 'bearer'
      : authLocation === 'query'
        ? 'query'
        : 'header';
  const authValue = isBearerAuth && authRawValue.startsWith('Bearer ')
    ? authRawValue.slice('Bearer '.length)
    : authRawValue;

  return {
    name,
    connType,
    command: connType === 'stdio' ? (commandParts[0] ?? '') : '',
    args: connType === 'stdio' ? [...commandParts.slice(1), ...extraArgs].join('\n') : '',
    url: connType === 'sse' ? (config?.url ?? fallbackUrl ?? '') : '',
    transport: config?.transport ?? (config?.type === 'sse' ? 'sse' : 'auto'),
    authType,
    authValue,
    authHeaderName: authType === 'header' ? (authParamName || 'X-API-Key') : 'X-API-Key',
    authQueryName: authType === 'query' ? (authParamName || 'apikey') : 'apikey',
    headersText: stringifyHeaders(config?.headers),
  };
}

interface MCPSheetProps {
  onClose: () => void;
  onSaved: () => void;
  /** 静默刷新父组件列表，不关闭抽屉 */
  onRefresh?: () => void;
}

function ConnStatusBadge({ status }: { status: ConnStatus }) {
  const { t } = useTranslation('tool');
  if (status === 'idle') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
      <WifiOff className="w-3 h-3" />{t('sheet.connStatus.idle')}
    </span>
  );
  if (status === 'saving') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700">
      <Loader2 className="w-3 h-3 animate-spin" />{t('sheet.connStatus.saving')}
    </span>
  );
  if (status === 'testing') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700">
      <Activity className="w-3 h-3 animate-pulse" />{t('sheet.connStatus.testing')}
    </span>
  );
  if (status === 'tested') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">
      <CheckCircle className="w-3 h-3" />{t('sheet.connStatus.tested')}
    </span>
  );
  if (status === 'connected') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">
      <Wifi className="w-3 h-3" />{t('sheet.connStatus.connected')}
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700">
      <WifiOff className="w-3 h-3" />{t('sheet.connStatus.failed')}
    </span>
  );
}

// ─── MCPFormFields（可复用表单内容）─────────────────────────────────────────

interface MCPFormFieldsProps {
  formData: MCPFormData;
  /** 不传则为只读模式 */
  onChange?: (fields: Partial<MCPFormData>) => void;
  disabledFields?: Partial<Record<keyof MCPFormData, boolean>>;
  connStatus: ConnStatus;
  testResult: { success: boolean; message: string; tools_count?: number } | null;
  onTestConnection: () => void;
  isTesting: boolean;
}

export function MCPFormFields({
  formData,
  onChange,
  disabledFields,
  connStatus,
  testResult,
  onTestConnection,
  isTesting,
}: MCPFormFieldsProps) {
  const { t } = useTranslation('tool');
  const readOnly = !onChange;
  const baseInputClass =
    'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-red-500';

  const update = (fields: Partial<MCPFormData>) => onChange?.(fields);
  const isFieldReadOnly = (field: keyof MCPFormData) => readOnly || !!disabledFields?.[field];
  const inputClassFor = (field: keyof MCPFormData, extra = '') =>
    `${baseInputClass}${isFieldReadOnly(field) ? ' bg-gray-50 text-gray-700 cursor-default' : ''}${extra ? ` ${extra}` : ''}`;

  return (
    <div className="space-y-4">
      {/* 服务名称 + 状态 */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="block text-sm font-medium text-gray-700">
            {t('addMCP.serviceName')}{!readOnly && <span className="text-red-500"> *</span>}
          </label>
          <ConnStatusBadge status={connStatus} />
        </div>
        <input
          type="text"
          value={formData.name}
          onChange={isFieldReadOnly('name') ? undefined : (e) => update({ name: e.target.value })}
          readOnly={isFieldReadOnly('name')}
          placeholder={t('addMCP.serviceNamePlaceholder')}
          className={inputClassFor('name')}
        />
      </div>

      {/* 连接方式 */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.connectionType')}</label>
        <div className="flex gap-4">
          <label className={`flex items-center gap-2 ${isFieldReadOnly('connType') ? 'cursor-default' : 'cursor-pointer'}`}>
            <input
              type="radio"
              value="stdio"
              checked={formData.connType === 'stdio'}
              onChange={isFieldReadOnly('connType') ? undefined : () => update({ connType: 'stdio' })}
              disabled={isFieldReadOnly('connType')}
              className="w-4 h-4 text-red-600 focus:ring-red-500"
            />
            <span className="text-sm text-gray-700">{t('addMCP.stdioLocal')}</span>
          </label>
          <label className={`flex items-center gap-2 ${isFieldReadOnly('connType') ? 'cursor-default' : 'cursor-pointer'}`}>
            <input
              type="radio"
              value="sse"
              checked={formData.connType === 'sse'}
              onChange={isFieldReadOnly('connType') ? undefined : () => update({ connType: 'sse' })}
              disabled={isFieldReadOnly('connType')}
              className="w-4 h-4 text-red-600 focus:ring-red-500"
            />
            <span className="text-sm text-gray-700">{t('addMCP.remoteSSE')}</span>
          </label>
        </div>
      </div>

      {/* Stdio 字段 */}
      {formData.connType === 'stdio' && (
        <>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              {t('addMCP.startCommand')}{!readOnly && <span className="text-red-500"> *</span>}
            </label>
            <input
              type="text"
              value={formData.command}
              onChange={isFieldReadOnly('command') ? undefined : (e) => update({ command: e.target.value })}
              readOnly={isFieldReadOnly('command')}
              placeholder={t('addMCP.startCommandPlaceholder')}
              className={inputClassFor('command', 'font-mono')}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.commandArgs')}</label>
            <textarea
              value={formData.args}
              onChange={isFieldReadOnly('args') ? undefined : (e) => update({ args: e.target.value })}
              readOnly={isFieldReadOnly('args')}
              placeholder={t('addMCP.commandArgsPlaceholder')}
              rows={3}
              className={inputClassFor('args', 'font-mono')}
            />
            {!readOnly && <p className="mt-1 text-xs text-gray-500">{t('addMCP.oneArgPerLine')}</p>}
          </div>
        </>
      )}

      {/* 远程 MCP 字段 */}
      {formData.connType === 'sse' && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              {t('addMCP.serviceUrl')}{!readOnly && <span className="text-red-500"> *</span>}
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={formData.url}
                onChange={isFieldReadOnly('url') ? undefined : (e) => update({ url: e.target.value })}
                readOnly={isFieldReadOnly('url')}
                placeholder={t('addMCP.serviceUrlPlaceholder')}
                className={`${inputClassFor('url', 'font-mono')} flex-1 min-w-0`}
              />
              <button
                type="button"
                onClick={onTestConnection}
                disabled={isTesting}
                className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-2 bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium transition-colors whitespace-nowrap"
              >
                {isTesting ? (
                  <><Activity className="w-3.5 h-3.5 animate-pulse" />{t('detail.testingConn')}</>
                ) : (
                  <><Activity className="w-3.5 h-3.5" />{t('detail.testConnection')}</>
                )}
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.transport')}</label>
            <select
              value={formData.transport}
              onChange={isFieldReadOnly('transport') ? undefined : (e) => update({ transport: e.target.value as MCPFormData['transport'] })}
              disabled={isFieldReadOnly('transport')}
              className={inputClassFor('transport')}
            >
              <option value="auto">{t('addMCP.transportAuto')}</option>
              <option value="sse">{t('addMCP.transportSSE')}</option>
              <option value="http">{t('addMCP.transportHTTP')}</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authMethod')}</label>
            <select
              value={formData.authType}
              onChange={isFieldReadOnly('authType') ? undefined : (e) => update({ authType: e.target.value as MCPFormData['authType'] })}
              disabled={isFieldReadOnly('authType')}
              className={inputClassFor('authType')}
            >
              <option value="none">{t('addMCP.authNone')}</option>
              <option value="bearer">{t('addMCP.authBearer')}</option>
              <option value="header">{t('addMCP.authHeader')}</option>
              <option value="query">{t('addMCP.authQuery')}</option>
            </select>
          </div>

          {formData.authType === 'bearer' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authToken')}</label>
              <input
                type="text"
                value={formData.authValue}
                onChange={isFieldReadOnly('authValue') ? undefined : (e) => update({ authValue: e.target.value })}
                readOnly={isFieldReadOnly('authValue')}
                placeholder={t('addMCP.authTokenPlaceholder')}
                className={`${inputClassFor('authValue')} font-mono`}
              />
            </div>
          )}

          {formData.authType === 'header' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authHeaderName')}</label>
                <input
                  type="text"
                  value={formData.authHeaderName}
                  onChange={isFieldReadOnly('authHeaderName') ? undefined : (e) => update({ authHeaderName: e.target.value })}
                  readOnly={isFieldReadOnly('authHeaderName')}
                  placeholder="X-API-Key"
                  className={`${inputClassFor('authHeaderName')} font-mono`}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authHeaderValue')}</label>
                <input
                  type="text"
                  value={formData.authValue}
                  onChange={isFieldReadOnly('authValue') ? undefined : (e) => update({ authValue: e.target.value })}
                  readOnly={isFieldReadOnly('authValue')}
                  placeholder={t('addMCP.authHeaderValuePlaceholder')}
                  className={`${inputClassFor('authValue')} font-mono`}
                />
              </div>
            </div>
          )}

          {formData.authType === 'query' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authQueryName')}</label>
                <input
                  type="text"
                  value={formData.authQueryName}
                  onChange={isFieldReadOnly('authQueryName') ? undefined : (e) => update({ authQueryName: e.target.value })}
                  readOnly={isFieldReadOnly('authQueryName')}
                  placeholder="apikey"
                  className={`${inputClassFor('authQueryName')} font-mono`}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.authQueryValue')}</label>
                <input
                  type="text"
                  value={formData.authValue}
                  onChange={isFieldReadOnly('authValue') ? undefined : (e) => update({ authValue: e.target.value })}
                  readOnly={isFieldReadOnly('authValue')}
                  placeholder={t('addMCP.authQueryValuePlaceholder')}
                  className={`${inputClassFor('authValue')} font-mono`}
                />
              </div>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('addMCP.extraHeaders')}</label>
            <textarea
              value={formData.headersText}
              onChange={isFieldReadOnly('headersText') ? undefined : (e) => update({ headersText: e.target.value })}
              readOnly={isFieldReadOnly('headersText')}
              placeholder={t('addMCP.extraHeadersPlaceholder')}
              rows={4}
              className={inputClassFor('headersText', 'font-mono')}
            />
            {!readOnly && <p className="mt-1 text-xs text-gray-500">{t('addMCP.extraHeadersHint')}</p>}
          </div>
        </div>
      )}

      {/* Stdio：测试连接按钮独立一行 */}
      {formData.connType === 'stdio' && (
        <button
          type="button"
          onClick={onTestConnection}
          disabled={isTesting}
          className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium transition-colors"
        >
          {isTesting ? (
            <><Activity className="w-4 h-4 animate-pulse" />{t('detail.testingConn')}</>
          ) : (
            <><Activity className="w-4 h-4" />{t('detail.testConnection')}</>
          )}
        </button>
      )}

      {/* 测试结果 */}
      {testResult && (
        <div className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${
          testResult.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800'
        }`}>
          {testResult.success
            ? <CheckCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            : <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          }
          <span>{testResult.message}</span>
        </div>
      )}

      {/* 提示（仅新建模式显示） */}
      {!readOnly && (
        <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
          <Info className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-red-800">
            {t('sheet.mcpHintDesc')}
          </p>
        </div>
      )}
    </div>
  );
}

export function MCPSheet({ onClose, onSaved, onRefresh }: MCPSheetProps) {
  const { t } = useTranslation('tool');
  const guideGroups = useMemo(() => buildGuidedCreateGroups([
    { title: t('create.mcp.guideSectionTitle'), actions: t('create.mcp.guideActions', { returnObjects: true }) },
    { title: t('create.mcp.caseSectionTitle'), actions: t('create.mcp.caseActions', { returnObjects: true }) },
  ]), [t]);
  const rexComposerControls = useRexComposerControls();
  const [formData, setFormData] = useState<MCPFormData>({
    name: '',
    connType: 'sse',
    command: '',
    args: '',
    url: '',
    transport: 'auto',
    authType: 'none',
    authValue: '',
    authHeaderName: 'X-API-Key',
    authQueryName: 'apikey',
    headersText: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [connStatus, setConnStatus] = useState<ConnStatus>('idle');
  const [testResult, setTestResult] = useState<{
    success: boolean;
    message: string;
    tools_count?: number;
  } | null>(null);

  const canSubmit = !!(formData.name.trim() &&
    (formData.connType === 'stdio' ? formData.command.trim() : formData.url.trim()));
  const formError = getMCPFormError(formData);

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    if (formError === 'invalidHeaders') {
      alert(t('alert.invalidHeaders'));
      return;
    }
    try {
      setSubmitting(true);
      await client.post('/api/mcp', { name: formData.name, config: buildMCPConfigFromForm(formData) });
      onSaved();
    } catch (err: any) {
      alert(t('alert.addFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setSubmitting(false);
    }
  };

  const update = (fields: Partial<MCPFormData>) => {
    setFormData(prev => ({ ...prev, ...fields }));
    // 表单变化时重置测试状态
    if (connStatus !== 'idle') {
      setConnStatus('idle');
      setTestResult(null);
    }
  };

  /** 仅测试连接，不保存到配置 */
  const handleTestConnection = async () => {
    if (!canSubmit || connStatus === 'testing') return;
    if (formError === 'invalidHeaders') {
      alert(t('alert.invalidHeaders'));
      return;
    }
    setTestResult(null);
    setConnStatus('testing');
    try {
      const res = await mcpAPI.test(formData.name, buildMCPConfigFromForm(formData));
      const data = res.data;
      const success = data.success ?? false;
      setConnStatus(success ? 'tested' : 'failed');
      setTestResult({
        success,
        message: data.message ?? (success ? t('sheet.connSuccess') : t('sheet.connFailed')),
        tools_count: data.tools_count,
      });
    } catch (err: any) {
      setConnStatus('failed');
      setTestResult({
        success: false,
        message: err.response?.data?.detail ?? err.message ?? t('detail.testFailed'),
      });
    }
  };

  const isTesting = connStatus === 'testing';

  return (
    <EntitySheet
      open
      mode="create"
      entityType={t('sheet.mcpEntityType')}
      icon={<Database className="w-5 h-5" />}
      rexSystemContext={MCP_REX_CONTEXT}
      rexWelcomeMessage={MCP_REX_WELCOME}
      rexGuideGroups={guideGroups}
      rexGuidePanelTitle={t('create.mcp.guidePanelTitle')}
      rexGuidePanelDesc={t('create.mcp.guidePanelDesc')}
      rexGuideEmptyTitle={t('create.mcp.emptyStateTitle')}
      rexGuideIcon={<Database className="h-5 w-5" />}
      {...rexComposerControls}
      submitDisabled={!canSubmit}
      submitLoading={submitting}
      submitLabel={t('button.addService')}
      onClose={onClose}
      onSubmit={handleSubmit}
      initialTab="rex"
    >
      <MCPFormFields
        formData={formData}
        onChange={update}
        connStatus={connStatus}
        testResult={testResult}
        onTestConnection={handleTestConnection}
        isTesting={isTesting}
      />
      {/* 测试后展示工具数/资源数（与详情页样式一致） */}
      {testResult?.tools_count != null && (
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-xs text-gray-500">{t('sheet.toolCountLabel')}</div>
            <div className="text-lg font-semibold text-gray-900">{testResult.tools_count}</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-xs text-gray-500">{t('sheet.resourceCountLabel')}</div>
            <div className="text-lg font-semibold text-gray-900">—</div>
          </div>
        </div>
      )}
    </EntitySheet>
  );
}

// ─── APISheet ─────────────────────────────────────────────────────────────────

const API_REX_CONTEXT = `你是 API 工具接入助手。用户希望通过对话将外部 API 接入为 Flocks API工具。

请先加载并遵守项目内 .flocks/plugins/skills/tool-builder（tool-builder skill）完成接入，所有产物写入 ~/.flocks/plugins/tools/api/ 目录。

**接入流程：**
1. 先确认 API 功能、Base URL、认证方式（API Key / Bearer 等）
2. 按 skill 使用 YAML-HTTP 或 YAML-Script 模式（外部 API 禁止用纯 Python 模式）
3. 生成 YAML 配置（及必要的 handler），执行 skill 要求的验证与冒烟测试
4. 创建后立即启用（enabled: true），确保工具在 Web UI 的 API 服务中可见

**重要约束：**
- 必须先加载 .flocks/plugins/skills/tool-builder，再动手写文件
- 禁止写入 flocks/tool/、flocks/tool/generated/ 等项目源码路径
- 复杂预处理/后处理使用 YAML-Script handler，仍放在 api/ 目录下

请先引导用户描述需求，必要时追问 API 文档或凭证，然后按 skill 一次性完成接入与验证。`;

const API_REX_WELCOME = `你好！我来帮你接入一个 API 服务作为工具。

请告诉我：
- 你想接入什么 API？（ThreatBook、VirusTotal、OpenWeather 或其他）
- API 的主要功能是什么？
- 是否有 API Key 需要配置？

我会按 tool-builder 规范生成 YAML 工具配置并完成接入。

如果你有 API 文档，请提供 API 文档链接或全文，我可以根据文档更准确地生成工具。`;

interface APISheetProps {
  onClose: () => void;
}

export function APISheet({ onClose }: APISheetProps) {
  const { t } = useTranslation('tool');
  const guideGroups = useMemo(() => buildGuidedCreateGroups([
    { title: t('create.api.guideSectionTitle'), actions: t('create.api.guideActions', { returnObjects: true }) },
    { title: t('create.api.caseSectionTitle'), actions: t('create.api.caseActions', { returnObjects: true }) },
  ]), [t]);
  const rexComposerControls = useRexComposerControls();
  const handleSubmit = () => {};

  return (
    <EntitySheet
      open
      mode="create"
      entityType={t('sheet.apiEntityType')}
      icon={<Cloud className="w-5 h-5" />}
      rexSystemContext={API_REX_CONTEXT}
      rexWelcomeMessage={API_REX_WELCOME}
      rexGuideGroups={guideGroups}
      rexGuidePanelTitle={t('create.api.guidePanelTitle')}
      rexGuidePanelDesc={t('create.api.guidePanelDesc')}
      rexGuideEmptyTitle={t('create.api.emptyStateTitle')}
      rexGuideIcon={<Cloud className="h-5 w-5" />}
      {...rexComposerControls}
      submitDisabled
      submitLabel={t('button.submitToRex')}
      onClose={onClose}
      onSubmit={handleSubmit}
      hideForm
      initialTab="rex"
    >
      <div />
    </EntitySheet>
  );
}

// ─── GenerateToolSheet ────────────────────────────────────────────────────────

const GENERATE_REX_CONTEXT = `你是工具创建助手。用户希望通过对话创建一个新的 Flocks python工具。

请先加载并遵守项目内 .flocks/plugins/skills/tool-builder（tool-builder skill），再根据用户需求完成工具创建，所有产物写入 ~/.flocks/plugins/tools/python 目录。

**创建流程：**
1. 先确认用户需求：工具名称、功能、输入输出、是否为外部 API 集成
2. skill 选择模式：本地工具（无远程 API）→ Python
3. 生成文件，执行 skill 要求的验证与冒烟测试
4. 创建后立即启用，确保工具可用

**重要约束：**
- 必须先加载 .flocks/plugins/skills/tool-builder，再动手写文件
- 禁止写入 flocks/tool/、flocks/tool/generated/ 等项目源码路径
- 外部 API 集成必须提醒用户使用“添加 API”，创建工具默认指 python 工具

请先引导用户描述需求，信息不足时可追问，然后按 skill 一次性完成创建与验证。`;

const GENERATE_REX_WELCOME = `你好！我来帮你生成一个自定义工具。

请描述你想要的工具：
- 这个工具做什么？（搜索、数据处理、文件操作...）
- 需要什么输入参数？
- 期望什么格式的输出？

描述越具体，生成的工具代码越准确。`;

interface GenerateToolSheetProps {
  onClose: () => void;
}

export function GenerateToolSheet({ onClose }: GenerateToolSheetProps) {
  const { t } = useTranslation('tool');
  const guideGroups = useMemo(() => buildGuidedCreateGroups([
    { title: t('create.local.guideSectionTitle'), actions: t('create.local.guideActions', { returnObjects: true }) },
    { title: t('create.local.caseSectionTitle'), actions: t('create.local.caseActions', { returnObjects: true }) },
  ]), [t]);
  const rexComposerControls = useRexComposerControls();
  const handleSubmit = () => {
    onClose();
  };

  return (
    <EntitySheet
      open
      mode="create"
      entityType={t('sheet.generateEntityType')}
      icon={<Code className="w-5 h-5" />}
      rexSystemContext={GENERATE_REX_CONTEXT}
      rexWelcomeMessage={GENERATE_REX_WELCOME}
      rexGuideGroups={guideGroups}
      rexGuidePanelTitle={t('create.local.guidePanelTitle')}
      rexGuidePanelDesc={t('create.local.guidePanelDesc')}
      rexGuideEmptyTitle={t('create.local.emptyStateTitle')}
      rexGuideIcon={<Code className="h-5 w-5" />}
      {...rexComposerControls}
      submitLabel={t('sheet.doneLabel')}
      onClose={onClose}
      onSubmit={handleSubmit}
      hideForm
    >
      <div className="flex flex-col items-center justify-center py-12 gap-4 text-center">
        <div className="w-16 h-16 rounded-2xl bg-red-50 flex items-center justify-center">
          <Code className="w-8 h-8 text-red-500" />
        </div>
        <div>
          <p className="text-sm font-medium text-gray-700 mb-1">{t('sheet.generateIntro')}</p>
          <p className="text-xs text-gray-500">
            {t('sheet.generateDesc')}
          </p>
        </div>
      </div>
    </EntitySheet>
  );
}
