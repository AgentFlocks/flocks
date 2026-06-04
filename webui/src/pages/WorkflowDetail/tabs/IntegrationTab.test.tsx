import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IntegrationTab from './IntegrationTab';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    get: vi.fn(),
    getService: vi.fn(),
    publish: vi.fn(),
    unpublish: vi.fn(),
    getTriggers: vi.fn(),
    createTrigger: vi.fn(),
    updateTrigger: vi.fn(),
    deleteTrigger: vi.fn(),
    previewTriggerMapping: vi.fn(),
    testTrigger: vi.fn(),
    listTriggerPlugins: vi.fn(),
    runPollerOnce: vi.fn(),
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
  const getFieldTextarea = (label: string): HTMLTextAreaElement => {
    const field = screen.getByText(label).closest('div');
    const textarea = field?.querySelector('textarea');
    if (!(textarea instanceof HTMLTextAreaElement)) {
      throw new Error(`textarea not found for field: ${label}`);
    }
    return textarea;
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('confirm', vi.fn(() => true));
    workflowAPI.get.mockResolvedValue({ data: workflow });
    workflowAPI.getService.mockResolvedValue({ data: null });
    workflowAPI.getTriggers.mockResolvedValue({ data: [] });
    workflowAPI.createTrigger.mockResolvedValue({ data: { trigger: { id: 'hook-created' } } });
    workflowAPI.updateTrigger.mockImplementation(async (_workflowId: string, _triggerId: string, trigger: unknown) => ({
      data: { trigger },
    }));
    workflowAPI.deleteTrigger.mockResolvedValue({ data: { ok: true, triggerId: 'hook-1' } });
    workflowAPI.previewTriggerMapping.mockResolvedValue({
      data: {
        triggerId: 'hook-1',
        triggerType: 'custom_webhook',
        matched: true,
        inputs: { event: { ok: true } },
      },
    });
    workflowAPI.testTrigger.mockResolvedValue({
      data: {
        ok: true,
        inputs: { event: { ok: true } },
      },
    });
    workflowAPI.listTriggerPlugins.mockResolvedValue({ data: [] });
    workflowAPI.runPollerOnce.mockResolvedValue({ data: { ok: true, status: { state: 'running' } } });
  });

  it('renders publish section first and unified trigger workspace below', async () => {
    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('发布为 API')).toBeInTheDocument();
    expect(await screen.findByText('集成')).toBeInTheDocument();
    expect(screen.queryByText('Kafka 配置')).not.toBeInTheDocument();
    expect(screen.queryByText('Workflow Poller')).not.toBeInTheDocument();
  });

  it('shows only one empty-state box when there is no trigger', async () => {
    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('还没有配置任何 Trigger。可以从上面的快捷按钮开始。')).toBeInTheDocument();
    expect(screen.queryByText('选择或创建一个 Trigger 后，在这里编辑配置。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Schedule' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Webhook' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Syslog' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Kafka' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Custom Adapter' })).toBeDisabled();
  });

  it('renders trigger list in the unified workspace', async () => {
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'schedule-1',
            name: 'Daily Scan',
            type: 'schedule',
            enabled: true,
            source: { intervalSeconds: 60 },
            mapping: {},
            inputs: {},
            testSamples: [{ name: 'default', payload: {} }],
          },
          status: { state: 'running' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    expect((await screen.findAllByText('Daily Scan')).length).toBeGreaterThan(0);
    expect(screen.getByText('Inputs（JSON）')).toBeInTheDocument();
    expect(screen.getByText('Mapping（JSON）')).toBeInTheDocument();
    expect(screen.getByText('Filter Expr')).toBeInTheDocument();
    expect(screen.getByText('测试样例')).toBeInTheDocument();
  });

  it('does not render duplicated trigger card when only one trigger exists', async () => {
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'kafka-1',
            name: 'Kafka Trigger',
            type: 'kafka',
            enabled: false,
            source: {
              inputBroker: 'localhost:9092',
              inputTopic: 'wf-1.events',
              inputGroupId: 'wf-1-group',
            },
            mapping: {},
            inputs: {},
            testSamples: [],
          },
          status: { state: 'stopped' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByText('Kafka Trigger')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '删除' })).toHaveLength(1);
  });

  it('creates a webhook trigger from the unified toolbar', async () => {
    const user = userEvent.setup();

    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: 'Webhook' }));

    await waitFor(() => {
      expect(workflowAPI.createTrigger).toHaveBeenCalledWith(
        'wf-1',
        expect.objectContaining({
          type: 'custom_webhook',
          name: 'Webhook Trigger',
          enabled: false,
        }),
      );
    });
  });

  it('saves edited schedule trigger through the unified editor', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'schedule-1',
            name: 'Daily Scan',
            type: 'schedule',
            enabled: true,
            source: { mode: 'interval', intervalSeconds: 60 },
            runtime: { timeoutSeconds: 7200, noOverlap: true },
            mapping: {},
            inputs: {},
            testSamples: [{ name: 'default', payload: {} }],
          },
          status: { state: 'running' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    const nameInput = await screen.findByDisplayValue('Daily Scan');
    fireEvent.change(nameInput, { target: { value: 'Updated Scan' } });
    await waitFor(() => {
      expect(nameInput).toHaveValue('Updated Scan');
    });
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(workflowAPI.updateTrigger).toHaveBeenCalledWith(
        'wf-1',
        'schedule-1',
        expect.objectContaining({
          id: 'schedule-1',
          type: 'schedule',
          name: 'Updated Scan',
        }),
      );
    });
  });

  it('persists the current inputs JSON text instead of stale draft data', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'hook-1',
            name: 'Webhook Trigger',
            type: 'custom_webhook',
            enabled: true,
            source: { method: 'POST', path: '/demo' },
            auth: { type: 'none' },
            mapping: { event: '$.body' },
            inputs: { original: true },
            testSamples: [{ name: 'default', payload: { example: true } }],
          },
          status: { state: 'ready' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    await screen.findByText('Inputs（JSON）');
    const inputsEditor = getFieldTextarea('Inputs（JSON）');
    fireEvent.change(inputsEditor, { target: { value: '{\n  "fresh": true\n}' } });
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(workflowAPI.updateTrigger).toHaveBeenCalledWith(
        'wf-1',
        'hook-1',
        expect.objectContaining({
          inputs: { fresh: true },
        }),
      );
    });
  });

  it('disables creating a second schedule trigger', async () => {
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'schedule-1',
            name: 'Daily Scan',
            type: 'schedule',
            enabled: true,
            source: { mode: 'interval', intervalSeconds: 60 },
            mapping: {},
            inputs: {},
            testSamples: [{ name: 'default', payload: {} }],
          },
          status: { state: 'running' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    expect(await screen.findByRole('button', { name: 'Schedule' })).toBeDisabled();
  });

  it('prefills schedule inputs from workflow sample inputs', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'schedule-1',
            name: 'Daily Scan',
            type: 'schedule',
            enabled: true,
            source: { mode: 'interval', intervalSeconds: 60 },
            runtime: { timeoutSeconds: 7200, noOverlap: true },
            mapping: {},
            inputs: {},
            testSamples: [{ name: 'default', payload: {} }],
          },
          status: { state: 'running' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: '使用 Sample Inputs 预填' }));

    await waitFor(() => {
      expect(getFieldTextarea('Inputs（JSON）')).toHaveValue('{\n  "customerId": 42\n}');
    });
  });

  it('toggles trigger enabled state from the trigger list', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'hook-1',
            name: 'Webhook Trigger',
            type: 'custom_webhook',
            enabled: false,
            source: { method: 'POST', path: '/demo' },
            auth: { type: 'none' },
            mapping: { event: '$.body' },
            inputs: {},
            testSamples: [{ name: 'default', payload: { example: true } }],
          },
          status: { state: 'stopped' },
        },
        {
          trigger: {
            id: 'hook-2',
            name: 'Webhook Trigger 2',
            type: 'custom_webhook',
            enabled: true,
            source: { method: 'POST', path: '/demo-2' },
            auth: { type: 'none' },
            mapping: { event: '$.body' },
            inputs: {},
            testSamples: [{ name: 'default', payload: { example: true } }],
          },
          status: { state: 'ready' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    await user.click((await screen.findAllByRole('button', { name: '启用' }))[0]);

    await waitFor(() => {
      expect(workflowAPI.updateTrigger).toHaveBeenCalledWith(
        'wf-1',
        'hook-1',
        expect.objectContaining({ enabled: true }),
      );
    });
  });

  it('runs schedule trigger once from the editor', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'schedule-1',
            name: 'Daily Scan',
            type: 'schedule',
            enabled: true,
            source: { mode: 'interval', intervalSeconds: 60 },
            runtime: { timeoutSeconds: 7200, noOverlap: true },
            mapping: {},
            inputs: {},
            testSamples: [{ name: 'default', payload: {} }],
          },
          status: { state: 'running' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: '立即执行一轮' }));

    await waitFor(() => {
      expect(workflowAPI.runPollerOnce).toHaveBeenCalledWith('wf-1');
    });
  });

  it('deletes selected trigger from the workspace', async () => {
    const user = userEvent.setup();
    workflowAPI.getTriggers.mockResolvedValue({
      data: [
        {
          trigger: {
            id: 'hook-1',
            name: 'Webhook Trigger',
            type: 'custom_webhook',
            enabled: true,
            source: { method: 'POST', path: '/demo' },
            auth: { type: 'none' },
            mapping: { event: '$.body' },
            inputs: {},
            testSamples: [{ name: 'default', payload: { example: true } }],
          },
          status: { state: 'ready' },
        },
      ],
    });

    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(workflowAPI.deleteTrigger).toHaveBeenCalledWith('wf-1', 'hook-1');
    });
  });
});
