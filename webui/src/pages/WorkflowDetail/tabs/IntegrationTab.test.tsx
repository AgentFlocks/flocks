import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IntegrationTab from './IntegrationTab';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    get: vi.fn(),
    getConfig: vi.fn(),
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
    t: (key: string) => {
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
      };
      return translations[key] ?? key;
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
    workflowAPI.getService.mockResolvedValue({ data: null });
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

  it('does not render broad publish controls without config.json template', async () => {
    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('当前工作流还没有 config.json 发布模板。')).toBeInTheDocument();
    expect(workflowAPI.getConfig).toHaveBeenCalledWith('wf-1');
    expect(workflowAPI.syncConfig).not.toHaveBeenCalled();
    expect(screen.queryByText('发布为 API')).not.toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
    expect(screen.queryByText('Kafka 配置')).not.toBeInTheDocument();
    expect(screen.queryByText('Workflow Poller')).not.toBeInTheDocument();
  });

  it('uses config.json template to render only API publishing', async () => {
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
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Webhook' })).not.toBeInTheDocument();
  });

  it('uses syslog template as start/stop only without config editor', async () => {
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
    expect(screen.getByText('配置来自 config.json')).toBeInTheDocument();
    expect(screen.queryByText('协议')).not.toBeInTheDocument();
    expect(screen.queryByText('Inputs（JSON）')).not.toBeInTheDocument();

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

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('config.json 中没有声明可发布的 API 或触发能力。')).toBeInTheDocument();
    expect(screen.queryByText('发布为 API')).not.toBeInTheDocument();
    expect(screen.queryByText('触发能力')).not.toBeInTheDocument();
  });
});
