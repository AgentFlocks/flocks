import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IntegrationTab from './IntegrationTab';

const { workflowAPI, workflowAPIEndpoints } = vi.hoisted(() => ({
  workflowAPI: {
    get: vi.fn(),
    getConfig: vi.fn(),
    updateConfig: vi.fn(),
    getService: vi.fn(),
    deleteService: vi.fn(),
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
  workflowAPIEndpoints: (id: string, triggerId = '{triggerId}') => {
    const workflowBase = `/api/workflow/${id}`;
    const triggerBase = `${workflowBase}/triggers`;
    const triggerRecord = `${triggerBase}/${triggerId}`;
    return {
      config: {
        read: `GET ${workflowBase}/config`,
        write: `PUT ${workflowBase}/config`,
        syncFallback: `POST ${workflowBase}/config/sync`,
      },
      apiService: {
        read: `GET ${workflowBase}/service`,
        publish: `POST ${workflowBase}/publish`,
        unpublish: `POST ${workflowBase}/unpublish`,
        delete: `DELETE ${workflowBase}/service`,
      },
      triggers: {
        list: `GET ${triggerBase}`,
        create: `POST ${triggerBase}`,
        update: `PUT ${triggerRecord}`,
        delete: `DELETE ${triggerRecord}`,
        status: `GET ${triggerRecord}/status`,
        previewMapping: `POST ${triggerRecord}/preview-mapping`,
        test: `POST ${triggerRecord}/test`,
        invokeWebhook: `/webhook/workflows/${id}/${triggerId}`,
        plugins: 'GET /api/workflow-trigger-plugins',
      },
      legacyAdapters: {
        kafkaConfig: `GET/POST ${workflowBase}/kafka-config`,
        kafkaStatus: `GET ${workflowBase}/kafka-status`,
        pollerConfig: `GET/POST ${workflowBase}/poller-config`,
        pollerStatus: `GET ${workflowBase}/poller-status`,
        pollerRunOnce: `POST ${workflowBase}/poller-run-once`,
        syslogConfig: `GET/POST ${workflowBase}/syslog-config`,
        syslogStatus: `GET ${workflowBase}/syslog-status`,
      },
    };
  },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI,
  workflowAPIEndpoints,
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
        'detail.run.deleteService': '删除 API 发布配置',
        'detail.run.deleteServiceShort': '删除配置',
        'detail.run.deletingService': '删除中...',
        'detail.run.deleteServiceConfirm': '确认删除 API 发布配置？',
        'detail.run.deleteServiceFailed': '删除 API 发布配置失败',
        'detail.run.driverLocal': '本地进程',
        'detail.run.driverDocker': 'Docker 容器',
        'detail.run.driverLocalDesc': 'local desc',
        'detail.run.driverDockerDesc': 'docker desc',
        'detail.run.apiKeyHide': '隐藏',
        'detail.run.apiKeyShow': '显示',
        'detail.chat.backendConfigAccessGuide': '后端配置库认证方式：使用 server_api_token，并通过 Authorization: Bearer 访问 {{configEndpoint}}；兜底迁移接口是 {{configSyncEndpoint}}。',
        'detail.run.guidePanelTitle': 'Rex 辅助发布',
        'detail.run.guidePanelDesc': '选择一种发布方式',
        'detail.run.cardGuideTitle': 'Flocks辅助配置',
        'detail.run.cardGuideAction': '辅助配置',
        'detail.run.cardGuideApiFocus': 'API 发布配置',
        'detail.run.cardGuideApiDesc': '结合当前 API 服务状态、运行方式和工作流功能，引导确认发布、鉴权、调用样例和是否启动。',
        'detail.run.cardGuideTriggerDesc': '结合当前 {{trigger}} 卡片配置和工作流功能，引导确认接入参数、字段映射、样例和生效方式。',
        'detail.run.cardGuideDisplayLabel': 'Flocks辅助配置：{{focus}}',
        'detail.run.guideApiShort': '发布为 API',
        'detail.run.guideApiDesc': '配置 API 发布',
        'detail.run.guideApiInstruction': '围绕 API 发布读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideSyslogShort': 'Syslog 接入',
        'detail.run.guideSyslogDesc': '配置 Syslog 接入',
        'detail.run.guideSyslogInstruction': '围绕 Syslog 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideKafkaShort': 'Kafka 接入',
        'detail.run.guideKafkaDesc': '配置 Kafka 接入',
        'detail.run.guideKafkaInstruction': '围绕 Kafka 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideWebhookShort': 'Webhook 接入',
        'detail.run.guideWebhookDesc': '配置 Webhook 接入',
        'detail.run.guideWebhookInstruction': '围绕 Webhook 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideScheduleShort': '定时触发',
        'detail.run.guideScheduleDesc': '配置定时触发',
        'detail.run.guideScheduleInstruction': '围绕定时触发读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.chat.welcome.guideQuestionPrompt': '用户点击了「{{focus}}」按钮。这个按钮的意图是：{{instruction}} 工作流 ID 是 {{id}}，工作流目录是 {{dir}}，工作流配置引导文件是 {{guidePath}}。配置模板接口是 {{configEndpoint}}。前端当前 API 清单：{{apiEndpoints}}。第一步必须读取 {{guidePath}}，必须调用 question 工具。',
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
    workflowAPI.deleteService.mockResolvedValue({ data: { ok: true, workflowId: 'wf-1' } });
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

  it('renders guide with persistent API and trigger workspaces when no runtime records exist', async () => {
    const onGuidePrompt = vi.fn();
    render(<IntegrationTab workflow={workflow} onGuidePrompt={onGuidePrompt} />);

    const apiCard = await screen.findByTestId('api-publish-card');
    const guideActions = screen.getByTestId('publish-guide-actions-inline');
    expect(screen.getByText('Rex 辅助发布')).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /发布为 API/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /Syslog 接入/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /Kafka 接入/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /Webhook 接入/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /定时触发/ })).toBeInTheDocument();
    expect(workflowAPI.getConfig).toHaveBeenCalledWith('wf-1');
    expect(workflowAPI.syncConfig).not.toHaveBeenCalled();
    expect(within(apiCard).getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布为 API 服务' })).not.toBeInTheDocument();
    expect(screen.getByText('触发能力')).toBeInTheDocument();
    expect(screen.getByText('还没有配置任何 Trigger。可以从上面的快捷按钮开始。')).toBeInTheDocument();
    expect(screen.queryByText('Kafka 配置')).not.toBeInTheDocument();
    expect(screen.queryByText('Workflow Poller')).not.toBeInTheDocument();
  });

  it('renders runtime publish and trigger records with delete actions below the Rex guide', async () => {
    const user = userEvent.setup();
    const service = {
      workflowId: 'wf-1',
      workflowName: 'Demo Workflow',
      serviceUrl: 'http://127.0.0.1:8080',
      invokeUrl: 'http://127.0.0.1:8080/invoke',
      apiKey: 'secret',
      status: 'stopped' as const,
      publishedAt: Date.now(),
      driver: 'local' as const,
    };
    const triggerRecord = {
      trigger: {
        id: 'syslog-default',
        type: 'syslog' as const,
        name: 'Syslog Listener',
        enabled: false,
        source: { protocol: 'udp', host: '0.0.0.0', port: 5140, format: 'auto' },
        auth: { type: 'api_key', headerName: 'X-Api-Key', apiKey: 'super-secret-api-key' },
        mapping: { syslog_message: '$.body' },
      },
      status: { state: 'stopped' },
    };
    workflowAPI.getService.mockResolvedValue({ data: service });
    workflowAPI.getTriggers.mockResolvedValue({ data: [triggerRecord] });
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
          publish: { type: 'api_service', enabled: false, driver: 'local' },
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
        runtime: {
          publish: {
            type: 'api_service',
            enabled: false,
            status: 'stopped',
            driver: 'local',
            invokeUrl: 'http://127.0.0.1:8080/invoke',
            apiKeyConfigured: true,
            publishedAt: service.publishedAt,
          },
          triggers: [triggerRecord],
        },
      },
    });

    const onGuidePrompt = vi.fn();
    render(<IntegrationTab workflow={workflow} onGuidePrompt={onGuidePrompt} />);

    const guideActions = await screen.findByTestId('publish-guide-actions-inline');
    const apiCard = await screen.findByTestId('api-publish-card');
    expect(Boolean(guideActions.compareDocumentPosition(apiCard) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(within(apiCard).getByRole('button', { name: '启用' })).toBeInTheDocument();
    expect(within(apiCard).getByRole('button', { name: '删除 API 发布配置' })).toBeInTheDocument();
    expect(within(apiCard).queryByTestId('api-publish-config')).not.toBeInTheDocument();
    await user.click(within(apiCard).getByRole('button', { name: '配置' }));
    expect(within(apiCard).getByTestId('api-publish-config')).toBeInTheDocument();
    expect(within(apiCard).getByText('Flocks辅助配置')).toBeInTheDocument();
    await user.click(within(apiCard).getByRole('button', { name: '辅助配置' }));
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('当前发布卡片上下文'),
      'Flocks辅助配置：API 发布配置',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('"selectedDriver": "local"'),
      'Flocks辅助配置：API 发布配置',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('"delete": "DELETE /api/workflow/wf-1/service"'),
      'Flocks辅助配置：API 发布配置',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('不要继续询问用户要对 workflow.json 模板触发器做什么'),
      'Flocks辅助配置：API 发布配置',
    );
    expect(screen.getByText('触发能力')).toBeInTheDocument();
    const triggerCard = screen.getByTestId('trigger-card-syslog-default');
    expect(within(triggerCard).getByText('Syslog Listener')).toBeInTheDocument();
    expect(within(triggerCard).queryByText('Inputs（JSON）')).not.toBeInTheDocument();
    await user.click(within(triggerCard).getByRole('button', { name: '配置' }));
    expect(within(triggerCard).getByText('Inputs（JSON）')).toBeInTheDocument();
    expect(within(triggerCard).getByText('Flocks辅助配置')).toBeInTheDocument();
    await user.click(within(triggerCard).getByRole('button', { name: '辅助配置' }));
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('"id": "syslog-default"'),
      'Flocks辅助配置：Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('"triggerType": "syslog"'),
      'Flocks辅助配置：Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('"update": "PUT /api/workflow/wf-1/triggers/syslog-default"'),
      'Flocks辅助配置：Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('后端配置库或运行态接口不可达，请停止配置流程'),
      'Flocks辅助配置：Syslog 接入',
    );
    const triggerPrompt = onGuidePrompt.mock.calls.find(
      ([, label]) => label === 'Flocks辅助配置：Syslog 接入',
    )?.[0] as string | undefined;
    expect(triggerPrompt).toContain('"apiKeyConfigured": true');
    expect(triggerPrompt).toContain('"headerName": "X-Api-Key"');
    expect(triggerPrompt).not.toContain('super-secret-api-key');
    expect(triggerPrompt).not.toContain('"apiKey":');
    expect(within(triggerCard).getByRole('button', { name: '删除 Syslog Listener' })).toBeInTheDocument();
    expect(screen.queryByText('当前工作流还没有发布或配置接入方式。')).not.toBeInTheDocument();

    await user.click(within(triggerCard).getByRole('button', { name: '删除 Syslog Listener' }));

    await waitFor(() => {
      expect(workflowAPI.deleteTrigger).toHaveBeenCalledWith('wf-1', 'syslog-default');
    });

    await user.click(within(apiCard).getByRole('button', { name: '删除 API 发布配置' }));

    await waitFor(() => {
      expect(workflowAPI.deleteService).toHaveBeenCalledWith('wf-1');
    });
    expect(within(screen.getByTestId('api-publish-card')).getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(within(screen.getByTestId('api-publish-card')).queryByRole('button', { name: '删除 API 发布配置' })).not.toBeInTheDocument();
  });

  it('keeps template-only triggers out of runtime cards while preserving publish controls', async () => {
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
              id: 'syslog-template',
              type: 'syslog',
              name: 'Syslog Template',
              source: { protocol: 'udp', host: '0.0.0.0', port: 5514 },
            },
          ],
        },
        runtime: {
          publish: { type: 'api_service', enabled: false, status: 'stopped' },
          triggers: [],
        },
      },
    });

    render(<IntegrationTab workflow={workflow} onGuidePrompt={vi.fn()} />);

    const apiCard = await screen.findByTestId('api-publish-card');
    const guideActions = screen.getByTestId('publish-guide-actions-inline');
    expect(screen.getByText('Rex 辅助发布')).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /^发布为 API$/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /Syslog 接入/ })).toBeInTheDocument();
    expect(within(apiCard).getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(screen.getByText('触发能力')).toBeInTheDocument();
    expect(screen.getByText('还没有配置任何 Trigger。可以从上面的快捷按钮开始。')).toBeInTheDocument();
    expect(screen.queryByText('Syslog Template')).not.toBeInTheDocument();
  });

  it('shows default publish controls when config declares no publish capability', async () => {
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

    const apiCard = await screen.findByTestId('api-publish-card');
    const guideActions = screen.getByTestId('publish-guide-actions-inline');
    expect(screen.getByText('Rex 辅助发布')).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /发布为 API/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /Webhook 接入/ })).toBeInTheDocument();
    expect(within(guideActions).getByRole('button', { name: /定时触发/ })).toBeInTheDocument();
    expect(within(apiCard).getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(screen.getByText('触发能力')).toBeInTheDocument();
  });

  it('offers publish guide actions and routes the selected guide prompt', async () => {
    const user = userEvent.setup();
    const onGuidePrompt = vi.fn();
    const service = {
      workflowId: 'wf-1',
      workflowName: 'Demo Workflow',
      serviceUrl: 'http://127.0.0.1:8080',
      invokeUrl: 'http://127.0.0.1:8080/invoke',
      apiKey: 'secret',
      status: 'running' as const,
      publishedAt: Date.now(),
      driver: 'local' as const,
    };
    const triggerRecords = [
      {
        trigger: {
          id: 'syslog-default',
          type: 'syslog' as const,
          name: 'Syslog Listener',
          source: { protocol: 'udp', host: '0.0.0.0', port: 5514 },
        },
        status: { state: 'stopped' },
      },
      {
        trigger: {
          id: 'kafka-default',
          type: 'kafka' as const,
          name: 'Kafka Consumer',
          source: { inputBroker: 'localhost:9092', inputTopic: 'alerts' },
        },
        status: { state: 'stopped' },
      },
    ];
    workflowAPI.getService.mockResolvedValue({ data: service });
    workflowAPI.getTriggers.mockResolvedValue({ data: triggerRecords });
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
        runtime: {
          publish: {
            type: 'api_service',
            enabled: true,
            status: 'running',
            driver: 'local',
            invokeUrl: 'http://127.0.0.1:8080/invoke',
            apiKeyConfigured: true,
            publishedAt: service.publishedAt,
          },
          triggers: triggerRecords,
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
      expect.stringContaining('DELETE /api/workflow/wf-1/triggers/{triggerId}'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('config.json 和 workflow.json 不是直接写入目标'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('server_api_token'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('Authorization: Bearer'),
      'Syslog 接入',
    );
    expect(onGuidePrompt).toHaveBeenCalledWith(
      expect.stringContaining('后端接口不可用时必须停止配置流程'),
      'Syslog 接入',
    );
  });
});
