import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { APIServiceDetailPanel } from './ServiceDetailPanel';

const { providerAPI } = vi.hoisted(() => ({
  providerAPI: {
    getMetadata: vi.fn(),
    getServiceCredentials: vi.fn(),
    setServiceCredentials: vi.fn(),
    testCredentials: vi.fn(),
  },
}));

vi.mock('@/api/provider', () => ({
  providerAPI,
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI: {},
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
  buildMCPConfigFromForm: vi.fn(),
  buildMCPFormDataFromConfig: vi.fn(),
  MCPFormFields: () => null,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'detail.tabs.overview': '概览',
        'detail.tabs.tools': '工具',
        'detail.connectionStatus': '连接状态',
        'statusBadge.connected': '已连接',
        'statusBadge.error': '错误',
        'statusBadge.unknown': '未知',
        'button.testing': '测试中...',
        'button.testConnectivity': '测试连通',
        'button.save': '保存',
        'button.cancel': '取消',
        'serviceInfo.name': '名称',
        'serviceInfo.description': '描述',
        'serviceInfo.type': '类型',
        'serviceInfo.apiType': 'API 集成',
        'serviceInfo.toolCount': '工具数量',
        'serviceInfo.apiUrl': 'API 地址',
        'serviceInfo.secret': '密钥',
        'serviceInfo.username': '用户名',
        'serviceInfo.password': '密码',
        'serviceInfo.enterApiKey': '输入 API Key',
        'serviceInfo.enterBaseUrl': '输入 API 地址',
        'serviceInfo.enterSecret': '输入密钥',
        'serviceInfo.enterUsername': '输入用户名',
        'serviceInfo.enterPassword': '输入密码',
        'detail.hide': '隐藏',
        'detail.show': '显示',
        'alert.unknownError': '未知错误',
      };
      return translations[key] ?? key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

describe('APIServiceDetailPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    providerAPI.getMetadata.mockResolvedValue({
      data: {
        name: 'SkyEye',
        description: 'SkyEye API service',
        credential_schema: [
          { key: 'api_key', label: 'API Key', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'apiKey', secret_id: 'skyeye_api_key' },
          { key: 'base_url', label: 'Base URL', storage: 'config', sensitive: false, required: false, input_type: 'url', config_key: 'base_url' },
          { key: 'username', label: 'Username', storage: 'config', sensitive: false, required: false, input_type: 'text', config_key: 'username' },
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValue({
      data: {
        api_key: 'old-login-key',
        base_url: 'https://old.example.com/skyeye',
        username: 'old-user',
        fields: {
          api_key: 'old-login-key',
          base_url: 'https://old.example.com/skyeye',
          username: 'old-user',
        },
        has_credential: true,
      },
    });
    providerAPI.setServiceCredentials.mockResolvedValue({
      data: { success: true },
    });
    providerAPI.testCredentials.mockResolvedValue({
      data: { success: true, message: 'ok' },
    });
  });

  it('saves api key, base url and username together', async () => {
    const user = userEvent.setup();

    render(
      <APIServiceDetailPanel
        serviceName="skyeye_api"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const apiKeyInput = await screen.findByPlaceholderText('输入 API Key');
    const baseUrlInput = screen.getByPlaceholderText('输入 API 地址');
    const usernameInput = screen.getByPlaceholderText('输入用户名');

    await user.clear(apiKeyInput);
    await user.type(apiKeyInput, 'new-login-key');
    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://skyeye-domain/skyeye');
    await user.clear(usernameInput);
    await user.type(usernameInput, 'skyeye');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(providerAPI.setServiceCredentials).toHaveBeenCalledWith('skyeye_api', expect.objectContaining({
        api_key: 'new-login-key',
        base_url: 'https://skyeye-domain/skyeye',
        username: 'skyeye',
        fields: {
          api_key: 'new-login-key',
          base_url: 'https://skyeye-domain/skyeye',
          username: 'skyeye',
        },
      }));
    });
  });

  it('keeps non-skyeye services on api-key-only flow', async () => {
    const user = userEvent.setup();
    providerAPI.getMetadata.mockResolvedValueOnce({
      data: {
        name: 'ThreatBook',
        description: 'ThreatBook API service',
        credential_schema: [
          { key: 'api_key', label: 'API Key', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'apiKey', secret_id: 'threatbook_api_key' },
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'old-api-key',
        fields: { api_key: 'old-api-key' },
        has_credential: true,
      },
    });

    render(
      <APIServiceDetailPanel
        serviceName="threatbook_api"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const apiKeyInput = await screen.findByPlaceholderText('输入 API Key');
    expect(screen.queryByPlaceholderText('输入 API 地址')).toBeNull();
    expect(screen.queryByPlaceholderText('输入用户名')).toBeNull();

    await user.clear(apiKeyInput);
    await user.type(apiKeyInput, 'only-api-key');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(providerAPI.setServiceCredentials).toHaveBeenCalledWith('threatbook_api', expect.objectContaining({
        api_key: 'only-api-key',
        fields: {
          api_key: 'only-api-key',
        },
      }));
    });
  });

  it('shows saved base url even when it matches the default value', async () => {
    providerAPI.getMetadata.mockResolvedValueOnce({
      data: {
        name: 'ThreatBook IO',
        description: 'ThreatBook global API service',
        credential_schema: [
          { key: 'api_key', label: 'API Key', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'apiKey', secret_id: 'threatbook_io_api_key' },
          { key: 'base_url', label: 'Base URL', storage: 'config', sensitive: false, required: false, input_type: 'url', config_key: 'base_url', default_value: 'https://api.threatbook.io' },
        ],
        base_url: 'https://api.threatbook.io',
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'tb-key',
        base_url: 'https://api.threatbook.io',
        fields: {
          api_key: 'tb-key',
          base_url: 'https://api.threatbook.io',
        },
        has_credential: true,
      },
    });

    render(
      <APIServiceDetailPanel
        serviceName="threatbook-io"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const baseUrlInput = await screen.findByPlaceholderText('输入 API 地址');
    expect((baseUrlInput as HTMLInputElement).value).toBe('https://api.threatbook.io');
  });

  it('saves api key, base url and secret for tdp service', async () => {
    const user = userEvent.setup();
    providerAPI.getMetadata.mockResolvedValueOnce({
      data: {
        name: 'TDP',
        description: 'TDP API service',
        credential_schema: [
          { key: 'api_key', label: 'API Key', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'apiKey', secret_id: 'tdp_api_key' },
          { key: 'secret', label: 'Secret', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'secret', secret_id: 'tdp_secret' },
          { key: 'base_url', label: 'Base URL', storage: 'config', sensitive: false, required: false, input_type: 'url', config_key: 'base_url' },
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'old-api-key',
        base_url: 'https://old.example.com',
        secret: 'old-secret',
        fields: {
          api_key: 'old-api-key',
          base_url: 'https://old.example.com',
          secret: 'old-secret',
        },
        has_credential: true,
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'new-api-key',
        base_url: 'https://tdp.example.com',
        secret: 'new-secret',
        fields: {
          api_key: 'new-api-key',
          base_url: 'https://tdp.example.com',
          secret: 'new-secret',
        },
        has_credential: true,
      },
    });

    render(
      <APIServiceDetailPanel
        serviceName="tdp_api"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const apiKeyInput = await screen.findByPlaceholderText('输入 API Key');
    const baseUrlInput = screen.getByPlaceholderText('输入 API 地址');
    const secretInput = screen.getByPlaceholderText('输入密钥');

    await user.clear(apiKeyInput);
    await user.type(apiKeyInput, 'new-api-key');
    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://tdp.example.com');
    await user.clear(secretInput);
    await user.type(secretInput, 'new-secret');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(providerAPI.setServiceCredentials).toHaveBeenCalledWith('tdp_api', expect.objectContaining({
        api_key: 'new-api-key',
        base_url: 'https://tdp.example.com',
        secret: 'new-secret',
        fields: {
          api_key: 'new-api-key',
          base_url: 'https://tdp.example.com',
          secret: 'new-secret',
        },
      }));
    });

    expect(screen.queryByPlaceholderText('输入用户名')).toBeNull();
  });

  it('saves api key, base url and secret for onesec service', async () => {
    const user = userEvent.setup();
    providerAPI.getMetadata.mockResolvedValueOnce({
      data: {
        name: 'OneSEC',
        description: 'OneSEC API service',
        credential_schema: [
          { key: 'api_key', label: 'API Key', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'apiKey', secret_id: 'onesec_api_key' },
          { key: 'secret', label: 'Secret', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'secret', secret_id: 'onesec_secret' },
          { key: 'base_url', label: 'Base URL', storage: 'config', sensitive: false, required: false, input_type: 'url', config_key: 'base_url', default_value: 'https://console.onesec.net' },
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'old-api-key',
        base_url: 'https://old.onesec.example.com',
        secret: 'old-secret',
        fields: {
          api_key: 'old-api-key',
          base_url: 'https://old.onesec.example.com',
          secret: 'old-secret',
        },
        has_credential: true,
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        api_key: 'new-api-key',
        base_url: 'https://console.onesec.net',
        secret: 'new-secret',
        fields: {
          api_key: 'new-api-key',
          base_url: 'https://console.onesec.net',
          secret: 'new-secret',
        },
        has_credential: true,
      },
    });

    render(
      <APIServiceDetailPanel
        serviceName="onesec_api"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const apiKeyInput = await screen.findByPlaceholderText('输入 API Key');
    const baseUrlInput = screen.getByPlaceholderText('输入 API 地址');
    const secretInput = screen.getByPlaceholderText('输入密钥');

    await user.clear(apiKeyInput);
    await user.type(apiKeyInput, 'new-api-key');
    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://console.onesec.net');
    await user.clear(secretInput);
    await user.type(secretInput, 'new-secret');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(providerAPI.setServiceCredentials).toHaveBeenCalledWith('onesec_api', expect.objectContaining({
        api_key: 'new-api-key',
        base_url: 'https://console.onesec.net',
        secret: 'new-secret',
        fields: {
          api_key: 'new-api-key',
          base_url: 'https://console.onesec.net',
          secret: 'new-secret',
        },
      }));
    });

    expect(screen.queryByPlaceholderText('输入用户名')).toBeNull();
  });

  it('shows custom tooltip when description is hovered', async () => {
    const user = userEvent.setup();

    render(
      <APIServiceDetailPanel
        serviceName="skyeye_api"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const description = await screen.findByText('SkyEye API service');
    expect(description.hasAttribute('title')).toBe(false);

    await user.hover(description);
    await waitFor(() => {
      expect(screen.getAllByText('SkyEye API service')).toHaveLength(2);
    });
  });

  it('allows qingteng service to save base url changes', async () => {
    const user = userEvent.setup();
    providerAPI.getMetadata.mockResolvedValueOnce({
      data: {
        name: 'Qingteng',
        description: 'Qingteng API service',
        credential_schema: [
          { key: 'base_url', label: 'Base URL', storage: 'config', sensitive: false, required: false, input_type: 'url', config_key: 'base_url' },
          { key: 'username', label: 'Username', storage: 'config', sensitive: false, required: true, input_type: 'text', config_key: 'username' },
          { key: 'password', label: 'Password', storage: 'secret', sensitive: true, required: true, input_type: 'password', config_key: 'password', secret_id: 'qingteng_password' },
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        base_url: 'http://old.qingteng.local:80',
        username: 'old-user',
        fields: {
          base_url: 'http://old.qingteng.local:80',
          username: 'old-user',
          password: '',
        },
        has_credential: false,
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValueOnce({
      data: {
        base_url: 'https://qt.example.com:8443/openapi',
        username: 'alice',
        fields: {
          base_url: 'https://qt.example.com:8443/openapi',
          username: 'alice',
          password: 'new-password',
        },
        has_credential: false,
      },
    });

    render(
      <APIServiceDetailPanel
        serviceName="qingteng"
        serviceTools={[]}
        onSelectTool={vi.fn()}
        enabled
        onToggleEnabled={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const baseUrlInput = await screen.findByPlaceholderText('输入 API 地址');
    const usernameInput = screen.getByPlaceholderText('输入用户名');
    const passwordInput = screen.getByPlaceholderText('输入密码');

    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://qt.example.com:8443/openapi');
    await user.clear(usernameInput);
    await user.type(usernameInput, 'alice');
    await user.type(passwordInput, 'new-password');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(providerAPI.setServiceCredentials).toHaveBeenCalledWith('qingteng', expect.objectContaining({
        base_url: 'https://qt.example.com:8443/openapi',
        username: 'alice',
        fields: {
          base_url: 'https://qt.example.com:8443/openapi',
          username: 'alice',
          password: 'new-password',
        },
      }));
    });
  });
});
