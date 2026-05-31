import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
        'detail.run.kafkaOutputEnabled': '启用输出',
        'detail.run.kafkaInputKey': 'Inputs 键名',
        'detail.run.inputConfig': '输入配置',
        'detail.run.outputConfig': '输出配置',
        'detail.run.savingConfig': '保存中',
        'detail.run.savedConfig': '已保存',
        'detail.run.saveConfig': '保存配置',
        'detail.run.kafkaHint': 'hint',
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
    workflowAPI.getSyslogConfig.mockResolvedValue({ data: null });
    workflowAPI.getSyslogStatus.mockResolvedValue({ data: { state: 'stopped', error: null } });
  });

  it('saves output-only Kafka config without enabling consumer', async () => {
    const user = userEvent.setup();
    render(<IntegrationTab workflow={workflow} />);

    await user.click(await screen.findByRole('button', { name: /Kafka 配置/ }));
    const brokerFields = screen.getAllByPlaceholderText('localhost:9092');
    await user.type(brokerFields[1], 'localhost:9092');
    await user.type(screen.getByPlaceholderText('workflow-output'), 'workflow-output');
    await user.click(screen.getByLabelText('启用输出'));
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => {
      expect(workflowAPI.saveKafkaConfig).toHaveBeenCalledWith('wf-1', {
        enabled: false,
        inputBroker: '',
        inputTopic: '',
        inputGroupId: '',
        inputKey: 'kafka_message',
        outputEnabled: true,
        outputBroker: 'localhost:9092',
        outputTopic: 'workflow-output',
      });
    });
  });
});
