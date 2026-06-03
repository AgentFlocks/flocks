import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import IntegrationTab from './IntegrationTab';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    getService: vi.fn(),
    publish: vi.fn(),
    unpublish: vi.fn(),
    getKafkaConfig: vi.fn(),
    saveKafkaConfig: vi.fn(),
    getKafkaStatus: vi.fn(),
    getPollerConfig: vi.fn(),
    savePollerConfig: vi.fn(),
    getPollerStatus: vi.fn(),
    runPollerOnce: vi.fn(),
    getSampleInputs: vi.fn(),
    getSyslogConfig: vi.fn(),
    saveSyslogConfig: vi.fn(),
    getSyslogStatus: vi.fn(),
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
        'detail.run.publishDesc': 'desc',
        'detail.run.publishAsApi': '发布为 API 服务',
        'detail.run.serviceDriver': '运行方式',
        'detail.run.driverLocal': '本地进程',
        'detail.run.driverDocker': 'Docker 容器',
        'detail.run.recommended': '推荐',
        'detail.run.driverLocalDesc': 'local desc',
        'detail.run.driverDockerDesc': 'docker desc',
        'detail.run.kafkaSection': 'Kafka 配置',
        'detail.run.kafkaExperimental': '实验性',
        'detail.run.kafkaEnabled': '启用消费',
        'detail.run.kafkaInputKey': 'Inputs 键名',
        'detail.run.kafkaInputs': '额外 Inputs JSON',
        'detail.run.kafkaInputsHint': 'kafka inputs hint',
        'detail.run.kafkaInputsJsonError': 'Kafka Inputs 必须是合法的 JSON 对象',
        'detail.run.inputConfig': '输入配置',
        'detail.run.savingConfig': '保存中',
        'detail.run.savedConfig': '已保存',
        'detail.run.saveConfig': '保存配置',
        'detail.run.kafkaHint': 'hint',
        'detail.run.pollerSection': 'Workflow Poller',
        'detail.run.pollerEnabled': '启用轮询服务',
        'detail.run.pollerNoOverlap': '禁止重叠执行',
        'detail.run.pollerInterval': '轮询间隔（秒）',
        'detail.run.pollerTimeout': '执行超时（秒）',
        'detail.run.pollerInputs': 'Inputs JSON',
        'detail.run.pollerInputsJsonError': 'Inputs 必须是合法的 JSON 对象',
        'detail.run.pollerInputsHint': 'poller inputs hint',
        'detail.run.pollerRunOnce': '立即执行一轮',
        'detail.run.pollerRunningOnce': '执行中...',
        'detail.run.pollerRunOnceFailed': '立即执行失败',
        'detail.run.pollerStatus': '轮询状态',
        'detail.run.pollerRunning': '运行中',
        'detail.run.pollerEnabledIdle': '已启用，等待下一轮',
        'detail.run.pollerFailed': '轮询器异常',
        'detail.run.pollerLastRunAt': '上次执行',
        'detail.run.pollerNextRunAt': '下次执行',
        'detail.run.pollerLastStatus': '最近结果',
        'detail.run.pollerLastDuration': '最近耗时',
        'detail.run.pollerSelectedCount': '本轮选中数量',
        'detail.run.pollerActiveRuns': '活跃执行数',
        'detail.run.pollerProcessedMarkCount': 'processed 总数',
        'detail.run.pollerChannelStatus': '通道通知状态',
        'detail.run.pollerHint': 'poller hint',
        'detail.run.syslogSection': 'Syslog',
        'detail.run.syslogExperimental': '实验性',
        'detail.run.syslogEnabled': '启用监听',
        'detail.run.syslogProtocol': '协议',
        'detail.run.syslogHost': '监听地址',
        'detail.run.syslogPort': '端口',
        'detail.run.syslogFormat': '解析格式',
        'detail.run.syslogInputKey': 'Inputs 键名',
        'detail.run.syslogHint': 'syslog hint',
      };
      return translations[key] ?? key;
    },
  }),
}));

const workflow = {
  id: 'wf-1',
  name: 'Demo Workflow',
  category: 'default',
  workflowJson: { start: 'step1', nodes: [], edges: [] },
  status: 'draft' as const,
  createdAt: Date.now(),
  updatedAt: Date.now(),
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

describe('IntegrationTab Kafka config', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workflowAPI.getService.mockResolvedValue({ data: null });
    workflowAPI.getKafkaConfig.mockResolvedValue({ data: null });
    workflowAPI.getKafkaStatus.mockResolvedValue({ data: { state: 'stopped', error: null } });
    workflowAPI.saveKafkaConfig.mockResolvedValue({ data: { ok: true, consumer: { state: 'stopped', error: null } } });
    workflowAPI.getPollerConfig.mockResolvedValue({ data: null });
    workflowAPI.getPollerStatus.mockResolvedValue({ data: { state: 'stopped', error: null } });
    workflowAPI.savePollerConfig.mockResolvedValue({ data: { ok: true, status: { state: 'running', lastStatus: null } } });
    workflowAPI.runPollerOnce.mockResolvedValue({ data: { ok: true, status: { state: 'stopped', lastStatus: 'success' } } });
    workflowAPI.getSampleInputs.mockResolvedValue({ data: { sampleInputs: {} } });
    workflowAPI.getSyslogConfig.mockResolvedValue({ data: null });
    workflowAPI.getSyslogStatus.mockResolvedValue({ data: { state: 'stopped', error: null } });
  });

  it('does not show experimental badges for Kafka and Syslog sections', () => {
    render(<IntegrationTab workflow={workflow} />);

    expect(screen.queryByText('实验性')).not.toBeInTheDocument();
  });

  it('saves Kafka consumer config without output fields', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Kafka 配置/ }));
    await user.type(screen.getByPlaceholderText('localhost:9092'), 'localhost:9092');
    await user.type(screen.getByPlaceholderText('workflow-input'), 'workflow-input');
    await user.click(screen.getByLabelText('启用消费'));
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => {
      expect(workflowAPI.saveKafkaConfig).toHaveBeenCalledWith('wf-1', {
        enabled: true,
        inputBroker: 'localhost:9092',
        inputTopic: 'workflow-input',
        inputGroupId: '',
        inputKey: 'kafka_message',
        inputs: {},
      });
    });
    expect(screen.queryByText('输出配置')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('启用输出')).not.toBeInTheDocument();
  });

  it('prefills kafka extra inputs from sample inputs without kafka raw payload keys', async () => {
    workflowAPI.getSampleInputs.mockResolvedValue({
      data: {
        sampleInputs: {
          _comment: 'ignore me',
          kafka_message: { id: 1 },
          source: 'demo',
          kafka_output_enabled: true,
        },
      },
    });

    render(<IntegrationTab workflow={workflow} />);
    await userEvent.setup().click(await screen.findByRole('button', { name: /Kafka 配置/ }));

    const textarea = await screen.findByLabelText('额外 Inputs JSON');
    expect(textarea).toHaveValue(`{
  "source": "demo",
  "kafka_output_enabled": true
}`);
  });

  it('blocks saving kafka config when extra inputs json is invalid', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Kafka 配置/ }));
    const textarea = screen.getByLabelText('额外 Inputs JSON');
    fireEvent.change(textarea, { target: { value: '{"broken": ' } });
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    expect(await screen.findByText('Kafka Inputs 必须是合法的 JSON 对象')).toBeInTheDocument();
    expect(workflowAPI.saveKafkaConfig).not.toHaveBeenCalled();
  });

  it('strips execution-only comment keys before saving kafka extra inputs', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Kafka 配置/ }));
    const textarea = screen.getByLabelText('额外 Inputs JSON');
    fireEvent.change(textarea, {
      target: {
        value: `{
  "_comment": "remove me",
  "kafka_output_enabled": true,
  "nested": {
    "_comment_nested": "remove too",
    "topic": "topic_soc_flocks_result_log"
  }
}`,
      },
    });
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => {
      expect(workflowAPI.saveKafkaConfig).toHaveBeenCalledWith('wf-1', {
        enabled: false,
        inputBroker: '',
        inputTopic: '',
        inputGroupId: '',
        inputKey: 'kafka_message',
        inputs: {
          kafka_output_enabled: true,
          nested: {
            topic: 'topic_soc_flocks_result_log',
          },
        },
      });
    });
  });

  it('renders poller status badge when runtime is running', async () => {
    workflowAPI.getPollerStatus.mockResolvedValue({
      data: {
        state: 'running',
        lastStatus: 'success',
        selectedCount: 12,
        activeRuns: 1,
      },
    });

    render(<IntegrationTab workflow={workflow} />);
    await userEvent.setup().click(await screen.findByRole('button', { name: /Workflow Poller/ }));

    expect(await screen.findByText('运行中')).toBeInTheDocument();
    expect(screen.getByText(/本轮选中数量: 12/)).toBeInTheDocument();
  });

  it('saves poller config from the integration tab', async () => {
    const user = userEvent.setup();
    workflowAPI.getSampleInputs.mockResolvedValue({
      data: {
        sampleInputs: {
          _comment: 'for display only',
          _comment_dispose: 'dispose note',
          severity: 'high',
          notify: true,
        },
      },
    });
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Workflow Poller/ }));
    await user.click(screen.getByLabelText('启用轮询服务'));
    const intervalInput = screen.getByLabelText('轮询间隔（秒）');
    await user.clear(intervalInput);
    await user.type(intervalInput, '45');
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => {
      expect(workflowAPI.savePollerConfig).toHaveBeenCalledWith('wf-1', {
        enabled: true,
        intervalSeconds: 45,
        timeoutSeconds: 7200,
        noOverlap: true,
        inputs: {
          severity: 'high',
          notify: true,
        },
      });
    });
  });

  it('prefills poller inputs from current workflow sample inputs', async () => {
    workflowAPI.getSampleInputs.mockResolvedValue({
      data: {
        sampleInputs: {
          _comment: 'ignore me',
          _comment_cache: 'cache note',
          eventType: 'alert',
          source: 'demo',
        },
      },
    });

    render(<IntegrationTab workflow={workflow} />);
    await userEvent.setup().click(await screen.findByRole('button', { name: /Workflow Poller/ }));

    const textarea = await screen.findByLabelText('Inputs JSON');
    expect(textarea).toHaveValue(`{
  "eventType": "alert",
  "source": "demo"
}`);
  });

  it('blocks saving poller config when inputs json is invalid', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Workflow Poller/ }));
    const textarea = screen.getByLabelText('Inputs JSON');
    fireEvent.change(textarea, { target: { value: '{"broken": ' } });
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    expect(await screen.findByText('Inputs 必须是合法的 JSON 对象')).toBeInTheDocument();
    expect(workflowAPI.savePollerConfig).not.toHaveBeenCalled();
  });

  it('runs poller once from the integration tab', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Workflow Poller/ }));
    await user.click(screen.getByRole('button', { name: '立即执行一轮' }));

    await waitFor(() => {
      expect(workflowAPI.runPollerOnce).toHaveBeenCalledWith('wf-1');
    });
  });
});
