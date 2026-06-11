import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IntegrationTab from './IntegrationTab';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    get: vi.fn(),
    getConfig: vi.fn(),
    updateConfig: vi.fn(),
    getService: vi.fn(),
    publish: vi.fn(),
    unpublish: vi.fn(),
    syncConfig: vi.fn(),
    getTriggers: vi.fn(),
    createTrigger: vi.fn(),
    updateTrigger: vi.fn(),
    deleteTrigger: vi.fn(),
    listTriggerPlugins: vi.fn(),
    runPollerOnce: vi.fn(),
    saveSyslogConfig: vi.fn(),
    getSyslogStatus: vi.fn(),
    saveKafkaConfig: vi.fn(),
    getKafkaStatus: vi.fn(),
    savePollerConfig: vi.fn(),
    getPollerStatus: vi.fn(),
  },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI,
}));

vi.mock('@/components/common/CopyButton', () => ({
  default: ({ text }: { text: string }) => (
    <button type="button" data-testid="copy-button" aria-label={`copy:${text}`}>
      copy
    </button>
  ),
}));

vi.mock('@/components/common/WorkflowStatusBadge', () => ({
  default: ({ status }: { status: string }) => <span>{status}</span>,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'detail.run.publishSection': '发布为 API',
        'detail.run.publishDesc': 'publish desc',
        'detail.run.publishAsApi': '发布为 API 服务',
        'detail.run.triggerSection': '触发能力',
        'detail.run.publishFailed': '发布失败',
        'detail.run.stopFailed': '停止失败',
        'detail.run.stopping': '停止中...',
        'detail.run.stopService': '停止服务',
        'detail.run.driverLocal': '本地进程',
        'detail.run.driverDocker': 'Docker 容器',
        'detail.run.driverLocalDesc': 'local desc',
        'detail.run.driverDockerDesc': 'docker desc',
        'detail.run.apiKeyHide': '隐藏',
        'detail.run.apiKeyShow': '显示',
        'detail.run.guidePanelTitle': 'Rex 辅助发布',
        'detail.run.guidePanelDesc': '选择一种发布方式',
        'detail.run.guideApiShort': '发布为 API',
        'detail.run.guideApiDesc': '配置 API 发布',
        'detail.run.guideApiInstruction': '围绕 API 发布读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 不是直接写入目标',
        'detail.run.guideSyslogShort': 'Syslog 接入',
        'detail.run.guideSyslogDesc': '配置 Syslog 接入',
        'detail.run.guideSyslogInstruction': '围绕 Syslog 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 不是直接写入目标',
        'detail.run.guideKafkaShort': 'Kafka 接入',
        'detail.run.guideKafkaDesc': '配置 Kafka 接入',
        'detail.run.guideKafkaInstruction': '围绕 Kafka 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 不是直接写入目标',
        'detail.run.guideWebhookShort': 'Webhook 接入',
        'detail.run.guideWebhookDesc': '配置 Webhook 接入',
        'detail.run.guideWebhookInstruction': '围绕 Webhook 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 不是直接写入目标',
        'detail.run.guideScheduleShort': '定时触发',
        'detail.run.guideScheduleDesc': '配置定时触发',
        'detail.run.guideScheduleInstruction': '围绕定时触发读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 不是直接写入目标',
        'detail.chat.welcome.guideQuestionPrompt': '用户点击了「{{focus}}」按钮。这个按钮的意图是：{{instruction}} 工作流 ID 是 {{id}}，工作流目录是 {{dir}}，工作流配置引导文件是 {{guidePath}}。配置模板接口是 {{configEndpoint}}。第一步必须读取 {{guidePath}}，必须调用 question 工具。',
      };
      return (translations[key] ?? key).replace(/{{(\w+)}}/g, (_match, name: string) => (
        params?.[name] === undefined ? '' : String(params[name])
      ));
    },
  }),
}));

const workflow = {
  id: 'wf-1',
  name: 'Demo Workflow',
  category: 'default',
  workflowJson: {
    start: 'step1',
    nodes: [],
    edges: [],
    metadata: { sampleInputs: { customerId: 42 } },
  },
  status: 'draft' as const,
  createdAt: Date.now(),
  updatedAt: Date.now(),
  markdownContent: '',
  stats: {
    callCount: 0,
    successCount: 0,
    errorCount: 0,
    totalRuntime: 0,
    avgRuntime: 0,
    thumbsUp: 0,
    thumbsDown: 0,
  },
};

describe('IntegrationTab trigger workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('confirm', vi.fn(() => true));
    workflowAPI.get.mockResolvedValue({ data: workflow });
    workflowAPI.getConfig.mockResolvedValue({ data: { exists: false, path: '/tmp/config.json', config: {} } });
    workflowAPI.updateConfig.mockImplementation(async (_id: string, config: unknown) => ({
      data: {
        ok: true,
        exists: true,
        path: '/tmp/config.json',
        config,
      },
    }));
    workflowAPI.getService.mockResolvedValue({ data: null });
    workflowAPI.publish.mockResolvedValue({
      data: {
        workflowId: 'wf-1',
        workflowName: 'Demo Workflow',
        serviceUrl: 'http://127.0.0.1:8080',
        invokeUrl: 'http://127.0.0.1:8080/invoke',
        apiKey: 'secret',
        status: 'running',
        publishedAt: Date.now(),
        driver: 'local',
      },
    });
    workflowAPI.unpublish.mockResolvedValue({ data: { ok: true } });
    workflowAPI.syncConfig.mockResolvedValue({ data: { ok: true, path: '/tmp/config.json', config: {} } });
    workflowAPI.getTriggers.mockResolvedValue({ data: [] });
    workflowAPI.createTrigger.mockResolvedValue({ data: { trigger: { id: 'hook-created' } } });
    workflowAPI.updateTrigger.mockImplementation(async (_workflowId: string, _triggerId: string, trigger: unknown) => ({
      data: { trigger },
    }));
    workflowAPI.deleteTrigger.mockResolvedValue({ data: { ok: true, triggerId: 'hook-1' } });
    workflowAPI.listTriggerPlugins.mockResolvedValue({ data: [] });
    workflowAPI.runPollerOnce.mockResolvedValue({ data: { ok: true, status: { state: 'running' } } });
    workflowAPI.saveSyslogConfig.mockResolvedValue({ data: { ok: true, listener: { state: 'listening' } } });
    workflowAPI.getSyslogStatus.mockResolvedValue({ data: { state: 'stopped' } });
    workflowAPI.saveKafkaConfig.mockResolvedValue({ data: { ok: true, consumer: { state: 'running' } } });
    workflowAPI.getKafkaStatus.mockResolvedValue({ data: { state: 'stopped' } });
    workflowAPI.savePollerConfig.mockResolvedValue({ data: { ok: true, status: { state: 'running' } } });
    workflowAPI.getPollerStatus.mockResolvedValue({ data: { state: 'stopped' } });
  });

  it('shows guided empty state when no publish capability is returned', async () => {
    render(<IntegrationTab workflow={workflow} onGuidePrompt={vi.fn()} />);

    expect(await screen.findByText('当前工作流还没有发布或配置接入方式。')).toBeInTheDocument();
    expect(screen.getByText('Rex 辅助发布')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /发布为 API/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Syslog 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kafka 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Webhook 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /定时触发/ })).toBeInTheDocument();
    expect(workflowAPI.getConfig).toHaveBeenCalledWith('wf-1');
    expect(workflowAPI.syncConfig).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '发布为 API 服务' })).not.toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
    expect(screen.queryByText('Kafka 配置')).not.toBeInTheDocument();
    expect(screen.queryByText('Workflow Poller')).not.toBeInTheDocument();
  });

  it('renders generated publish config capabilities even without a stored template', async () => {
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: false,
        path: '/tmp/config.json',
        source: 'generated',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          publish: { type: 'api_service', driver: 'local' },
          triggers: [
            {
              id: 'syslog-default',
              type: 'syslog',
              name: 'Syslog Listener',
              source: { protocol: 'udp', host: '0.0.0.0', port: 5140, format: 'auto' },
              mapping: { syslog_message: '$.body' },
            },
          ],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} onGuidePrompt={vi.fn()} />);

    expect((await screen.findAllByText('发布为 API')).length).toBeGreaterThan(0);
    expect(within(screen.getByTestId('api-publish-card')).getByRole('button', { name: '发布为 API 服务' })).toBeInTheDocument();
    expect(screen.getByText('触发能力')).toBeInTheDocument();
    expect(screen.getByText('Syslog Listener')).toBeInTheDocument();
    expect(screen.queryByText('当前工作流还没有发布或配置接入方式。')).not.toBeInTheDocument();
  });

  it('uses publish config template to render only API publishing', async () => {
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: true,
        path: '/tmp/config.json',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          publish: { type: 'api_service', driver: 'local' },
          triggers: [],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('发布为 API')).toBeInTheDocument();
    expect(within(screen.getByTestId('api-publish-card')).getByRole('button', { name: '发布为 API 服务' })).toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Webhook' })).not.toBeInTheDocument();
  });

  it('treats api trigger entries as publish templates instead of runtime triggers', async () => {
    const user = userEvent.setup();
    workflowAPI.getService.mockResolvedValue({
      data: {
        workflowId: 'wf-1',
        workflowName: 'Demo Workflow',
        serviceUrl: 'http://127.0.0.1:8080',
        invokeUrl: 'http://127.0.0.1:8080/invoke',
        apiKey: 'secret',
        status: 'running',
        publishedAt: Date.now(),
        driver: 'local',
      },
    });
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: true,
        path: '/tmp/config.json',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          triggers: [
            {
              id: 'api-default',
              type: 'api',
              name: 'Demo API',
              enabled: true,
              source: { method: 'POST', path: '/api/workflow/wf-1/run' },
            },
          ],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('发布为 API')).toBeInTheDocument();
    expect(within(screen.getByTestId('api-publish-card')).getByRole('button', { name: '停止服务' })).toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
    expect(screen.queryByText('Demo API')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '停止服务' }));

    await waitFor(() => {
      expect(workflowAPI.unpublish).toHaveBeenCalledWith('wf-1');
    });
    expect(workflowAPI.createTrigger).not.toHaveBeenCalled();
    expect(workflowAPI.updateTrigger).not.toHaveBeenCalled();
  });

  it('uses syslog template with editable config and runtime start/stop', async () => {
    const user = userEvent.setup();
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: true,
        path: '/tmp/config.json',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          triggers: [
            {
              id: 'syslog-default',
              type: 'syslog',
              name: 'Syslog Listener',
              source: { protocol: 'udp', host: '0.0.0.0', port: 5514, format: 'auto' },
              mapping: { syslog_message: '$.body' },
            },
          ],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('触发能力')).toBeInTheDocument();
    expect(screen.queryByText('发布为 API')).not.toBeInTheDocument();
    expect(screen.getByText('模板来自配置库')).toBeInTheDocument();
    expect(screen.getByText('协议')).toBeInTheDocument();
    expect(screen.getByText('Inputs（JSON）')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => {
      expect(workflowAPI.updateConfig).toHaveBeenCalledWith(
        'wf-1',
        expect.objectContaining({
          triggers: expect.arrayContaining([
            expect.objectContaining({
              id: 'syslog-default',
              type: 'syslog',
              source: expect.objectContaining({
                port: 5514,
              }),
            }),
          ]),
        }),
      );
    });

    await user.click(await screen.findByRole('button', { name: '启动监听' }));

    await waitFor(() => {
      expect(workflowAPI.saveSyslogConfig).toHaveBeenCalledWith(
        'wf-1',
        {
          enabled: true,
          protocol: 'udp',
          host: '0.0.0.0',
          port: 5514,
          format: 'auto',
          inputKey: 'syslog_message',
        },
      );
    });
  });

  it('shows template empty state when config declares no publish capability', async () => {
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: true,
        path: '/tmp/config.json',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          triggers: [],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} onGuidePrompt={vi.fn()} />);

    expect(await screen.findByText('发布配置中没有声明可发布的 API 或触发能力。')).toBeInTheDocument();
    expect(screen.getByText('Rex 辅助发布')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /发布为 API/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Webhook 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /定时触发/ })).toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
  });

  it('offers publish guide actions and routes the selected guide prompt', async () => {
    const user = userEvent.setup();
    const onGuidePrompt = vi.fn();
    workflowAPI.getConfig.mockResolvedValue({
      data: {
        exists: true,
        path: '/tmp/config.json',
        config: {
          version: 1,
          kind: 'workflow.integration-config',
          workflow: { id: 'wf-1' },
          updatedAt: Date.now(),
          publish: { type: 'api_service', driver: 'local' },
          triggers: [
            {
              id: 'syslog-default',
              type: 'syslog',
              name: 'Syslog Listener',
              source: { protocol: 'udp', host: '0.0.0.0', port: 5514 },
            },
            {
              id: 'kafka-default',
              type: 'kafka',
              name: 'Kafka Consumer',
              source: { inputBroker: 'localhost:9092', inputTopic: 'alerts' },
            },
          ],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} onGuidePrompt={onGuidePrompt} />);

    expect((await screen.findAllByRole('button', { name: /^发布为 API$/ })).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Syslog 接入/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Kafka 接入/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /Webhook 接入/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /定时触发/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Rex 辅助发布')).toHaveLength(1);
    expect(screen.getAllByTestId('publish-guide-actions-inline')).toHaveLength(1);
    screen.getAllByTestId('publish-guide-actions-inline').forEach((group) => {
      expect(group).toHaveClass('flex-wrap');
      expect(group).not.toHaveClass('overflow-x-auto');
    });

    await user.click(screen.getAllByRole('button', { name: /Syslog 接入/ })[0]);

    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('用户点击了「Syslog 接入」按钮'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('guide.md'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('必须调用 question 工具'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('/api/workflow/wf-1/config'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('config.json 不是直接写入目标'),
      'Syslog 接入',
    );
  });
});
