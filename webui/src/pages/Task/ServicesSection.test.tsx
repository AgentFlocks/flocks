import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ServicesSection from './ServicesSection';

const mocks = vi.hoisted(() => ({
  listServices: vi.fn(),
  publish: vi.fn(),
  unpublish: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    listServices: mocks.listServices,
    publish: mocks.publish,
    unpublish: mocks.unpublish,
  },
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock('@/components/common/CopyButton', () => ({
  default: ({ text }: { text: string }) => (
    <button type="button" aria-label={`copy:${text}`}>
      copy
    </button>
  ),
}));

vi.mock('@/components/common/WorkflowStatusBadge', () => ({
  default: ({ status }: { status: string }) => <span>{status}</span>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
    </div>
  ),
}));

vi.mock('@/i18n', () => ({
  default: { language: 'zh-CN' },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'services.filterAll': '全部',
        'services.filterRunning': '运行中',
        'services.filterStopped': '已停止',
        'services.publishedAt': `${params?.date ?? ''} 发布`,
        'services.serviceDriver': '服务驱动',
        'services.driverLocal': 'Local',
        'services.driverDocker': 'Docker',
        'services.showKey': '显示',
        'services.hideKey': '隐藏',
        'services.quickCall': '快速调用（curl）',
        'services.restartService': '重启服务',
        'services.restarting': '重启中...',
        'services.stopService': '停止服务',
        'services.stopping': '停止中...',
        'services.openInNewTab': '在新标签打开',
        'services.loadFailed': '加载失败',
        'services.restartFailed': '重启失败',
        'services.stopFailed': '停止失败',
        'services.serviceRestarted': '服务已重启',
        'services.serviceStopped': '服务已停止',
        'services.emptyTitle': '暂无 API 服务',
        'services.emptyDescription': '空状态',
        'common:button.refresh': '刷新',
      };
      return translations[key] ?? key;
    },
  }),
}));

describe('ServicesSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('restarts a stopped service with its persisted driver', async () => {
    const user = userEvent.setup();
    const service = {
      workflowId: 'wf-1',
      workflowName: 'mcp_test',
      serviceUrl: 'http://127.0.0.1:19000',
      invokeUrl: 'http://127.0.0.1:19000/invoke',
      apiKey: 'abcdefghijklmnopqrstuvwxyz123456',
      status: 'stopped' as const,
      publishedAt: Date.now(),
      driver: 'docker' as const,
    };

    mocks.listServices.mockResolvedValue({ data: [service] });
    mocks.publish.mockResolvedValue({ data: { ...service, status: 'running' } });

    render(<ServicesSection />);

    const restartButton = await screen.findByRole('button', { name: '重启服务' });
    await user.click(restartButton);

    await waitFor(() => {
      expect(mocks.publish).toHaveBeenCalledWith('wf-1', { driver: 'docker' });
    });
    expect(screen.getByText('服务驱动')).toBeInTheDocument();
    expect(screen.getByText('Docker')).toBeInTheDocument();
    expect(mocks.toastSuccess).toHaveBeenCalledWith('服务已重启');
  });

  it('shows restart and stop actions for running services', async () => {
    const service = {
      workflowId: 'wf-2',
      workflowName: 'keyword_summary',
      serviceUrl: 'http://127.0.0.1:19001',
      invokeUrl: 'http://127.0.0.1:19001/invoke',
      apiKey: 'abcdefghijklmnopqrstuvwxyz654321',
      status: 'running' as const,
      publishedAt: Date.now(),
      driver: 'local' as const,
    };

    mocks.listServices.mockResolvedValue({ data: [service] });

    render(<ServicesSection />);

    expect(await screen.findByText('Local')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '重启服务' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停止服务' })).toBeInTheDocument();
  });
});
