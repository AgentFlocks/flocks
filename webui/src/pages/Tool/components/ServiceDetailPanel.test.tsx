import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MCPServerDetailPanel } from './ServiceDetailPanel';

const { currentLanguage, mcpAPI } = vi.hoisted(() => ({
  currentLanguage: { value: 'zh-CN' },
  mcpAPI: {
    get: vi.fn(),
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
    serviceUrlAction,
  }: {
    formData: { url: string };
    onChange?: (fields: { url: string }) => void;
    onTestConnection: () => void;
    testResult: { message: string } | null;
    serviceUrlAction?: React.ReactNode;
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
      {serviceUrlAction}
      {testResult && <div>{testResult.message}</div>}
    </div>
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
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
        'detail.claimFreeApiKey': currentLanguage.value.startsWith('zh') ? '免费领取 API Key' : 'Claim free API key',
        'alert.connectionOk': '连接成功',
      };
      return translations[key] ?? key;
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

    const link = await screen.findByRole('link', { name: '免费领取 API Key' });
    expect(link).toHaveAttribute('href', 'https://x.threatbook.com/flocks/activate');
    expect(link).toHaveAttribute('target', '_blank');
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
    expect(screen.queryByRole('link', { name: '免费领取 API Key' })).not.toBeInTheDocument();
  });
});
