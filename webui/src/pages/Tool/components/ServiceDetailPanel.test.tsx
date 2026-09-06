import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MCPServerDetailPanel } from './ServiceDetailPanel';

const { currentLanguage, mcpAPI } = vi.hoisted(() => ({
  currentLanguage: { value: 'zh-CN' },
  mcpAPI: {
    get: vi.fn(),
    getCredentials: vi.fn(),
    revealCredentials: vi.fn(),
    configureThreatBook: vi.fn(),
    testCredentials: vi.fn(),
    update: vi.fn(),
    testExisting: vi.fn(),
  },
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI,
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {},
}));

vi.mock('@/api/tool', () => ({
  toolAPI: {},
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('./badges', () => ({
  EnabledBadge: () => null,
}));

vi.mock('../ToolSheets', () => ({
  buildMCPConfigFromForm: (formData: {
    connType: 'stdio' | 'sse';
    command: string;
    args: string;
    url: string;
  }) => (
    formData.connType === 'stdio'
      ? {
          type: 'stdio',
          command: formData.command,
          args: formData.args,
        }
      : {
          type: 'sse',
          url: formData.url,
        }
  ),
  getMCPFormError: () => null,
  buildMCPFormDataFromConfig: (
    name: string,
    config?: { type?: 'stdio' | 'sse' | 'local' | 'remote'; url?: string } | null,
    fallbackUrl?: string,
  ) => ({
    name,
    connType: config?.type === 'stdio' || config?.type === 'local' ? 'stdio' : 'sse',
    command: '',
    args: '',
    url: config?.url ?? fallbackUrl ?? '',
  }),
  MCPFormFields: ({
    formData,
    onChange,
    onTestConnection,
    testResult,
  }: {
    formData: { url: string };
    onChange?: (fields: { url: string }) => void;
    onTestConnection: () => void;
    testResult: { message: string } | null;
  }) => (
    <div>
      <input
        aria-label="service-url"
        value={formData.url}
        onChange={(event) => onChange?.({ url: event.target.value })}
      />
      <button type="button" onClick={onTestConnection}>
        trigger-test
      </button>
      {testResult && <div>{testResult.message}</div>}
    </div>
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      const isChinese = currentLanguage.value.startsWith('zh');
      const translations: Record<string, string> = {
        'detail.tabs.overview': '概览',
        'detail.tabs.tools': '工具',
        'detail.tabs.resources': '资源',
        'detail.registeredTools': '注册工具',
        'detail.availableResources': '可用资源',
        'detail.refreshTools': '刷新工具',
        'detail.disconnectConn': '断开连接',
        'detail.connectConn': '建立连接',
        'detail.removeServer': '删除服务器',
        'detail.disableServer': '禁用服务器',
        'detail.enableServer': '启用服务器',
        'button.cancel': '取消',
        'button.save': '保存',
        'button.saving': '保存中...',
        'detail.testFailed': '连接测试失败',
        'detail.show': isChinese ? '显示' : 'Show',
        'detail.hide': isChinese ? '隐藏' : 'Hide',
        'detail.threatbookMcp.title': isChinese ? '配置 ThreatBook MCP' : 'Configure ThreatBook MCP',
        'detail.threatbookMcp.description': isChinese ? '免费启用微步威胁情报 MCP 服务' : 'Enable the free ThreatBook intelligence MCP service',
        'detail.threatbookMcp.region': isChinese ? '服务区域' : 'Service region',
        'detail.threatbookMcp.regionHint': isChinese ? '选择区域' : 'Choose region',
        'detail.threatbookMcp.regions.cn': isChinese ? '中国区' : 'China',
        'detail.threatbookMcp.regions.global': isChinese ? '国际区' : 'International',
        'detail.threatbookMcp.freeService': isChinese ? '免费 ThreatBook MCP 服务' : 'Free ThreatBook MCP service',
        'detail.threatbookMcp.apiKey': 'API Key',
        'detail.threatbookMcp.keyPlaceholder': isChinese ? '粘贴当前区域的 ThreatBook API Key' : 'Paste the ThreatBook API key for this region',
        'detail.threatbookMcp.keyHint': isChinese ? '领取后填写' : 'Paste after claiming',
        'detail.threatbookMcp.keyRequired': isChinese ? '请填写 Key' : 'Enter API key',
        'detail.threatbookMcp.revealFailed': isChinese ? '读取 Key 失败' : 'Failed to retrieve API key',
        'detail.threatbookMcp.claimFreeKey': isChinese ? '领取免费 API Key' : 'Claim free API key',
        'detail.threatbookMcp.endpoint': isChinese ? 'MCP 服务地址' : 'MCP endpoint',
        'detail.threatbookMcp.endpointHint': isChinese ? '自动生成地址' : 'Endpoint is generated automatically',
        'detail.threatbookMcp.saveAndVerify': isChinese ? '保存并验证连接' : 'Save and verify connection',
        'detail.threatbookMcp.saving': isChinese ? '正在验证并保存...' : 'Verifying and saving...',
        'detail.threatbookMcp.saveSuccess': isChinese ? '配置成功' : 'Configured',
        'detail.threatbookMcp.saveFailed': isChinese ? '配置失败' : 'Setup failed',
        'detail.threatbookMcp.loading': isChinese ? '读取配置' : 'Loading configuration',
        'detail.threatbookMcp.configuredTitle': isChinese ? '已配置{{region}}' : '{{region}} configured',
        'detail.threatbookMcp.connected': isChinese ? '当前已连接' : 'Connected',
        'detail.threatbookMcp.savedNotConnected': isChinese ? '当前未连接' : 'Not connected',
        'detail.threatbookMcp.keyConfigured': isChinese ? '已安全配置' : 'Securely configured',
        'detail.threatbookMcp.edit': isChinese ? '编辑配置' : 'Edit configuration',
        'detail.threatbookMcp.retest': isChinese ? '重新测试' : 'Test again',
        'detail.threatbookMcp.testing': isChinese ? '测试中...' : 'Testing...',
        'alert.connectionOk': '连接成功',
      };
      return (translations[key] ?? key).replace('{{region}}', String(options?.region ?? ''));
    },
    i18n: {
      language: currentLanguage.value,
      resolvedLanguage: currentLanguage.value,
      changeLanguage: vi.fn(),
    },
  }),
  Trans: ({ children }: { children: React.ReactNode }) => children,
  initReactI18next: { type: '3rdParty', init: vi.fn() },
}));

describe('MCPServerDetailPanel', () => {
  const server = {
    name: 'demo-mcp',
    status: 'connected' as const,
    url: 'https://old.example.com/mcp',
    tools: [],
    resources: [],
  };

  const detailResponse = {
    data: {
      name: 'demo-mcp',
      status: {
        status: 'connected',
        tools_count: 2,
        resources_count: 0,
      },
      tools: [],
      resources: [],
      config: {
        type: 'sse' as const,
        url: 'https://old.example.com/mcp',
      },
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    currentLanguage.value = 'zh-CN';
    mcpAPI.get.mockResolvedValue(detailResponse);
    mcpAPI.getCredentials.mockResolvedValue({
      data: { has_credential: false },
    });
    mcpAPI.revealCredentials.mockResolvedValue({
      data: { api_key: '312abcdef321' },
    });
    mcpAPI.configureThreatBook.mockResolvedValue({
      data: {
        success: true,
        message: 'ok',
        region: 'cn',
        endpoint: 'https://mcp.threatbook.cn/mcp',
        connected: true,
        tools_count: 3,
      },
    });
    mcpAPI.testCredentials.mockResolvedValue({
      data: { success: true, message: 'ok', tools_count: 3 },
    });
    mcpAPI.update.mockResolvedValue({
      data: { success: true },
    });
    mcpAPI.testExisting.mockResolvedValue({
      data: {
        success: true,
        message: '连接成功',
        tools_count: 3,
      },
    });
  });

  it('tests edited config without persisting it first', async () => {
    const user = userEvent.setup();
    const onStatusChange = vi.fn().mockResolvedValue(undefined);

    render(
      <MCPServerDetailPanel
        server={server}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onStatusChange={onStatusChange}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    const urlInput = await screen.findByLabelText('service-url');
    await user.clear(urlInput);
    await user.type(urlInput, 'https://new.example.com/mcp');
    await user.click(screen.getByRole('button', { name: 'trigger-test' }));

    await waitFor(() => {
      expect(mcpAPI.testExisting).toHaveBeenCalledWith('demo-mcp', {
        type: 'sse',
        url: 'https://new.example.com/mcp',
      });
    });

    expect(mcpAPI.update).not.toHaveBeenCalled();
    expect(onStatusChange).not.toHaveBeenCalled();
    expect(mcpAPI.get).toHaveBeenCalledTimes(1);
  });

  it('still persists edits only when save is clicked', async () => {
    const user = userEvent.setup();
    const onStatusChange = vi.fn().mockResolvedValue(undefined);

    render(
      <MCPServerDetailPanel
        server={server}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onStatusChange={onStatusChange}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    const urlInput = await screen.findByLabelText('service-url');
    await user.clear(urlInput);
    await user.type(urlInput, 'https://saved.example.com/mcp');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(mcpAPI.update).toHaveBeenCalledWith('demo-mcp', {
        type: 'sse',
        url: 'https://saved.example.com/mcp',
      });
    });

    expect(onStatusChange).toHaveBeenCalledTimes(1);
    expect(mcpAPI.get).toHaveBeenCalledTimes(2);
    expect(mcpAPI.testExisting).not.toHaveBeenCalled();
  });

  it('shows the China free key link for ThreatBook MCP in Chinese', async () => {
    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    const link = await screen.findByRole('link', { name: '领取免费 API Key' });
    expect(link).toHaveAttribute('href', 'https://x.threatbook.com/flocks/activate');
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('button', { name: '中国区' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://mcp.threatbook.cn/mcp')).toBeInTheDocument();
  });

  it('shows the international free key link for ThreatBook MCP in English', async () => {
    currentLanguage.value = 'en-US';

    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    const link = await screen.findByRole('link', { name: 'Claim free API key' });
    expect(link).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');
    expect(screen.getByRole('button', { name: 'International' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://mcp.threatbook.io/mcp')).toBeInTheDocument();
  });

  it('sends the selected region and API key through the closed-loop setup action', async () => {
    const user = userEvent.setup();

    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onStatusChange={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    await user.click(await screen.findByRole('button', { name: '国际区' }));
    await user.type(screen.getByPlaceholderText('粘贴当前区域的 ThreatBook API Key'), 'global-key');
    await user.click(screen.getByRole('button', { name: '保存并验证连接' }));

    await waitFor(() => {
      expect(mcpAPI.configureThreatBook).toHaveBeenCalledWith('threatbook_mcp', {
        region: 'global',
        api_key: 'global-key',
      });
    });
  });

  it('shows the configured region instead of changing it with the interface language', async () => {
    currentLanguage.value = 'en-US';
    mcpAPI.get.mockResolvedValue({
      ...detailResponse,
      data: {
        ...detailResponse.data,
        name: 'threatbook_mcp',
        config: {
          type: 'sse',
          url: 'https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}',
        },
      },
    });
    mcpAPI.getCredentials.mockResolvedValue({
      data: {
        has_credential: true,
        secret_id: 'threatbook_mcp_key',
        api_key_masked: '312xxxx321',
      },
    });

    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    expect(await screen.findByText('China configured')).toBeInTheDocument();
    expect(screen.getByText('https://mcp.threatbook.cn/mcp')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit configuration' })).toBeInTheDocument();
  });

  it('shows the saved key masked in edit mode and reveals it only on demand', async () => {
    const user = userEvent.setup();
    mcpAPI.get.mockResolvedValue({
      ...detailResponse,
      data: {
        ...detailResponse.data,
        name: 'threatbook_mcp',
        config: {
          type: 'sse',
          url: 'https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}',
        },
      },
    });
    mcpAPI.getCredentials.mockResolvedValue({
      data: {
        has_credential: true,
        secret_id: 'threatbook_mcp_key',
        api_key_masked: '312xxxx321',
      },
    });

    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    await user.click(await screen.findByRole('button', { name: '编辑配置' }));

    const keyInput = screen.getByLabelText('API Key *');
    expect(keyInput).toHaveValue('312xxxx321');
    expect(keyInput).toHaveAttribute('readonly');
    expect(screen.queryByDisplayValue('312abcdef321')).not.toBeInTheDocument();

    await user.click(screen.getByTitle('显示'));

    await waitFor(() => {
      expect(mcpAPI.revealCredentials).toHaveBeenCalledWith('threatbook_mcp');
      expect(keyInput).toHaveValue('312abcdef321');
    });
    expect(keyInput).not.toHaveAttribute('readonly');

    await user.click(screen.getByTitle('隐藏'));
    expect(keyInput).toHaveValue('312xxxx321');
    expect(keyInput).toHaveAttribute('readonly');

    await user.click(screen.getByRole('button', { name: '国际区' }));
    expect(keyInput).toHaveValue('');
    expect(keyInput).not.toHaveAttribute('readonly');
  });

  it('locks region switching while the saved key is being revealed', async () => {
    const user = userEvent.setup();
    let resolveReveal: ((value: { data: { api_key: string } }) => void) | undefined;
    mcpAPI.revealCredentials.mockReturnValue(new Promise((resolve) => {
      resolveReveal = resolve;
    }));
    mcpAPI.get.mockResolvedValue({
      ...detailResponse,
      data: {
        ...detailResponse.data,
        name: 'threatbook_mcp',
        config: {
          type: 'sse',
          url: 'https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}',
        },
      },
    });
    mcpAPI.getCredentials.mockResolvedValue({
      data: {
        has_credential: true,
        secret_id: 'threatbook_mcp_key',
        api_key_masked: '312xxxx321',
      },
    });

    render(
      <MCPServerDetailPanel
        server={{ ...server, name: 'threatbook_mcp' }}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    await user.click(await screen.findByRole('button', { name: '编辑配置' }));
    await user.click(screen.getByTitle('显示'));

    expect(screen.getByRole('button', { name: '国际区' })).toBeDisabled();

    resolveReveal?.({ data: { api_key: '312abcdef321' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '国际区' })).toBeEnabled();
    });
  });

  it('does not show the free key link for other MCP servers', async () => {
    render(
      <MCPServerDetailPanel
        server={server}
        serverTools={[]}
        onConnect={vi.fn()}
        onDisconnect={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onRemove={vi.fn()}
        onSelectTool={vi.fn()}
      />,
    );

    await screen.findByLabelText('service-url');
    expect(screen.queryByRole('link', { name: '领取免费 API Key' })).not.toBeInTheDocument();
  });
});
