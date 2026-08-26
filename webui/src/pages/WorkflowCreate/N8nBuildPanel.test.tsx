import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import N8nBuildPanel from './N8nBuildPanel';

const { mockN8nAPI, toastMock } = vi.hoisted(() => ({
  mockN8nAPI: {
    listConnections: vi.fn(),
    listBuildRuns: vi.fn(),
    createConnection: vi.fn(),
    updateConnection: vi.fn(),
    updateConnectionById: vi.fn(),
    healthCheck: vi.fn(),
    createBuildRun: vi.fn(),
    retryTests: vi.fn(),
    cleanup: vi.fn(),
    deleteConnection: vi.fn(),
  },
  toastMock: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('@/api/n8n', () => ({
  n8nAPI: mockN8nAPI,
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'create.n8n.title': 'n8n 工作流生成',
        'create.n8n.subtitle': '配置连接',
        'create.n8n.unchecked': '未检测',
        'create.n8n.connection': '选择连接',
        'create.n8n.newConnection': '新增 n8n 连接',
        'create.n8n.connectionName': '连接名称',
        'create.n8n.baseUrl': 'n8n 地址',
        'create.n8n.secretRef': '密钥引用',
        'create.n8n.apiKey': 'n8n API Key',
        'create.n8n.apiKeyPlaceholder': '粘贴 n8n API Key',
        'create.n8n.setDefault': '设为默认连接',
        'create.n8n.save': '保存连接',
        'create.n8n.saving': '保存中',
        'create.n8n.check': '检测连接',
        'create.n8n.checking': '检测中',
        'create.n8n.deleteConnection': '删除连接',
        'create.n8n.userRequest': '自然语言需求',
        'create.n8n.userRequestPlaceholder': '描述需求',
        'create.n8n.sendToWorkbench': '发送到工作台生成',
        'create.n8n.workbenchDisplayTitle': '创建 n8n 工作流',
        'create.n8n.defaultUserRequest': '创建一个可在 n8n 运行的测试 workflow。',
        'create.n8n.irTitle': 'IR 快速构建',
        'create.n8n.irHint': '粘贴 IR',
        'create.n8n.resetSample': '示例',
        'create.n8n.publishAndTest': '发布并测试',
        'create.n8n.latestRun': '最近一次结果',
        'create.n8n.refresh': '刷新',
        'create.n8n.connectionSaved': '保存成功',
        'create.n8n.connectionSaveFailed': '保存失败',
      };
      return translations[key] ?? key;
    },
  }),
}));

function notFoundError() {
  return { response: { status: 404, data: { detail: 'Not Found' } }, message: 'Request failed with status code 404' };
}

function connection(id = 'conn-1') {
  return {
    id,
    name: 'n8n',
    baseUrl: 'http://localhost:5678',
    apiKeySecretRef: 'N8N_API_KEY',
    isDefault: true,
    status: 'unknown',
    apiKeyConfigured: true,
    apiKeyMasked: '***',
  };
}

describe('N8nBuildPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockN8nAPI.listConnections.mockResolvedValue({ data: [] });
    mockN8nAPI.listBuildRuns.mockResolvedValue({ data: [] });
  });

  it('falls back to the legacy single-connection API when creating a connection returns 404', async () => {
    const user = userEvent.setup();
    mockN8nAPI.createConnection.mockRejectedValueOnce(notFoundError());
    mockN8nAPI.updateConnection.mockResolvedValueOnce({ data: connection('default') });

    render(<N8nBuildPanel />);

    await screen.findByRole('button', { name: '保存连接' });
    await user.click(screen.getByRole('button', { name: '保存连接' }));

    await waitFor(() => expect(mockN8nAPI.updateConnection).toHaveBeenCalledTimes(1));
    expect(mockN8nAPI.createConnection).toHaveBeenCalledTimes(1);
    expect(mockN8nAPI.updateConnection).toHaveBeenCalledWith(expect.objectContaining({
      baseUrl: 'http://localhost:5678',
      apiKeySecretRef: 'N8N_API_KEY',
      isDefault: true,
    }));
    expect(toastMock.success).toHaveBeenCalledWith('保存成功');
  });

  it('recreates the connection when the selected connection id no longer exists', async () => {
    const user = userEvent.setup();
    mockN8nAPI.listConnections.mockResolvedValue({ data: [connection('conn-stale')] });
    mockN8nAPI.updateConnectionById.mockRejectedValueOnce(notFoundError());
    mockN8nAPI.createConnection.mockResolvedValueOnce({ data: connection('conn-new') });

    render(<N8nBuildPanel />);

    await screen.findByDisplayValue('n8n');
    await user.click(screen.getByRole('button', { name: '保存连接' }));

    await waitFor(() => expect(mockN8nAPI.createConnection).toHaveBeenCalledTimes(1));
    expect(mockN8nAPI.updateConnectionById).toHaveBeenCalledWith('conn-stale', expect.objectContaining({
      baseUrl: 'http://localhost:5678',
      apiKeySecretRef: 'N8N_API_KEY',
    }));
    expect(mockN8nAPI.createConnection).toHaveBeenCalledWith(expect.objectContaining({
      baseUrl: 'http://localhost:5678',
      apiKeySecretRef: 'N8N_API_KEY',
    }));
    expect(toastMock.success).toHaveBeenCalledWith('保存成功');
  });

  it('shows the full user request as the workbench launch display text', async () => {
    const user = userEvent.setup();
    const onGuidePrompt = vi.fn();
    const requestText = '帮我创建一个 n8n 工作流，从 Kafka security-alerts 消费告警，调用外部情报 API 研判 IOC，然后把高危结果写入告警 topic。';

    render(<N8nBuildPanel onGuidePrompt={onGuidePrompt} />);

    await user.type(await screen.findByRole('textbox', { name: '自然语言需求' }), requestText);
    await user.click(screen.getByRole('button', { name: '发送到工作台生成' }));

    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.any(String),
      `创建 n8n 工作流\n\n${requestText}`,
    );
  });

  it('uses the default request as the workbench launch display text when user request is empty', async () => {
    const user = userEvent.setup();
    const onGuidePrompt = vi.fn();

    render(<N8nBuildPanel onGuidePrompt={onGuidePrompt} />);

    await user.click(await screen.findByRole('button', { name: '发送到工作台生成' }));

    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.any(String),
      '创建 n8n 工作流\n\n创建一个可在 n8n 运行的测试 workflow。',
    );
  });
});
