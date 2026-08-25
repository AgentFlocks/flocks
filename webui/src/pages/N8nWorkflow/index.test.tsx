import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import N8nWorkflowPage from './index';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  listConnections: vi.fn(),
  listWorkflowRecords: vi.fn(),
  getWorkflowRecord: vi.fn(),
  discoverWorkflowRecords: vi.fn(),
  syncWorkflowRecords: vi.fn(),
  syncWorkflowRecord: vi.fn(),
  retryWorkflowRecordTests: vi.fn(),
  cleanupWorkflowRecord: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) => {
      const translations: Record<string, string> = {
        'n8n.centerTitle': 'n8n 工作流',
        'n8n.centerDescription': 'n8n 工作流中心',
        'n8n.backToFlocks': '返回 Flocks 工作流',
        'n8n.backToCenter': '返回 n8n 工作流',
        'n8n.syncAll': '同步状态',
        'n8n.syncSummary': `新增 ${options?.created || 0}，更新 ${options?.updated || 0}，失联 ${options?.missing || 0}，失败连接 ${options?.failed || 0}`,
        'n8n.create': '创建 n8n',
        'n8n.emptyTitle': '暂无 n8n 工作流',
        'n8n.emptyDescription': '暂无说明',
        'n8n.name': '名称',
        'n8n.connection': 'n8n 连接',
        'n8n.n8nBaseUrl': 'n8n 地址',
        'n8n.remoteStatus': 'n8n 状态',
        'n8n.testStatus': '测试状态',
        'n8n.webhookUrl': 'Webhook URL',
        'n8n.actions': '操作',
        'n8n.viewDetail': '查看详情',
        'n8n.openN8n': '打开 n8n',
        'n8n.cleanup': '清理',
        'n8n.confirmCleanup': `确认清理 n8n workflow「${options?.name || ''}」？`,
        'n8n.cleanupSuccess': '清理完成',
        'n8n.cleanupFailed': '清理失败',
        'n8n.createTitle': '创建 n8n 工作流',
        'n8n.createDescription': '创建说明',
        'n8n.detailDescription': '详情说明',
        'n8n.recordId': '记录 ID',
        'n8n.n8nWorkflowId': 'n8n ID',
        'n8n.source': '来源',
        'n8n.ownership': '权限',
        'n8n.sourceFlocksCreated': 'Flocks 托管',
        'n8n.sourceDiscovered': '可发现',
        'n8n.sourceExternal': '外部只读',
        'n8n.webhookMethod': 'Webhook 方法',
        'n8n.latestExecutionId': '最近执行 ID',
        'n8n.lastSyncedAt': '最近同步',
        'n8n.lastTestedAt': '最近测试',
        'n8n.sync': '同步',
        'n8n.retryTest': '重跑测试',
        'n8n.syncSuccess': '同步完成',
        'n8n.syncFailed': '同步失败',
        'n8n.retrySuccess': '测试完成',
        'n8n.retryFailed': '测试失败',
        'n8n.userRequest': '用户需求',
        'n8n.nativeWorkflowJson': 'n8n Workflow JSON',
        'n8n.lintIssues': 'Lint 结果',
        'n8n.testCases': '测试用例',
        'n8n.testResults': '测试结果',
      };
      return translations[key] ?? key;
    },
  }),
}));

vi.mock('@/api/n8n', () => ({
  n8nAPI: {
    listWorkflowRecords: mocks.listWorkflowRecords,
    listConnections: mocks.listConnections,
    getWorkflowRecord: mocks.getWorkflowRecord,
    discoverWorkflowRecords: mocks.discoverWorkflowRecords,
    syncWorkflowRecords: mocks.syncWorkflowRecords,
    syncWorkflowRecord: mocks.syncWorkflowRecord,
    retryWorkflowRecordTests: mocks.retryWorkflowRecordTests,
    cleanupWorkflowRecord: mocks.cleanupWorkflowRecord,
  },
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
  }),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string; icon?: ReactNode }) => (
    <header>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

vi.mock('@/pages/WorkflowCreate/N8nBuildPanel', () => ({
  default: ({
    onGuidePrompt,
    onBuildRunCreated,
  }: {
    onGuidePrompt?: (prompt: string, label: string) => void;
    onBuildRunCreated?: (run: { recordId: string }) => void;
  }) => (
    <div>
      <button type="button" onClick={() => onGuidePrompt?.('n8n prompt', '发送到工作台生成')}>
        mock send to workbench
      </button>
      <button type="button" onClick={() => onBuildRunCreated?.({ recordId: 'n8n-created-1' })}>
        mock build n8n
      </button>
    </div>
  ),
}));

const record = {
  id: 'n8n-wf-1',
  name: 'hello n8n',
  engine: 'n8n',
  connectionId: 'default',
  connectionName: 'Default n8n',
  source: 'flocks_created',
  ownership: 'managed',
  n8nWorkflowId: 'wf-1',
  n8nBaseUrl: 'http://localhost:5678',
  apiKeySecretRef: 'N8N_API_KEY',
  workflowUrl: 'http://localhost:5678/workflow/wf-1',
  webhookUrl: 'http://localhost:5678/webhook/hello',
  webhookPath: 'hello',
  webhookMethod: 'POST',
  remoteStatus: 'active',
  testStatus: 'test_passed',
  buildStatus: 'success',
  userRequest: 'return hello',
  ir: { name: 'hello n8n' },
  workflowJson: { name: 'hello n8n' },
  lintIssues: [],
  testCases: [{ name: 'ok' }],
  testResults: [{ name: 'ok', success: true }],
  latestBuildRunId: 'run-1',
  latestExecutionId: 'exec-1',
  createdAt: '2026-08-20T00:00:00Z',
  updatedAt: '2026-08-20T00:00:00Z',
  lastSyncedAt: null,
  lastTestedAt: null,
  error: null,
};

function renderPage(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/workflows/n8n" element={<N8nWorkflowPage />} />
        <Route path="/workflows/n8n/new" element={<N8nWorkflowPage />} />
        <Route path="/workflows/n8n/:recordId" element={<N8nWorkflowPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('N8nWorkflowPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    mocks.listWorkflowRecords.mockResolvedValue({ data: [record] });
    mocks.getWorkflowRecord.mockResolvedValue({ data: record });
    mocks.discoverWorkflowRecords.mockResolvedValue({ data: [record] });
    mocks.syncWorkflowRecords.mockResolvedValue({
      data: {
        status: 'completed',
        connectionsTotal: 1,
        connectionsSuccess: 1,
        connectionsFailed: 0,
        created: 1,
        updated: 0,
        missing: 0,
        external: 0,
        errors: [],
        connections: [],
        records: [record],
      },
    });
    mocks.syncWorkflowRecord.mockResolvedValue({ data: { ...record, lastSyncedAt: '2026-08-20T00:01:00Z' } });
    mocks.retryWorkflowRecordTests.mockResolvedValue({ data: { ...record, lastTestedAt: '2026-08-20T00:02:00Z' } });
    mocks.cleanupWorkflowRecord.mockResolvedValue({ data: { ...record, remoteStatus: 'cleaned' } });
  });

  it('lists n8n workflow records separately and supports cleanup', async () => {
    const user = userEvent.setup();
    renderPage('/workflows/n8n');

    expect(await screen.findByText('hello n8n')).toBeInTheDocument();
    expect(screen.getByText('wf-1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /打开 n8n/ })).toHaveAttribute('href', record.workflowUrl);

    await user.click(screen.getByRole('button', { name: '查看详情' }));
    expect(mocks.navigate).toHaveBeenCalledWith('/workflows/n8n/n8n-wf-1');

    await user.click(screen.getByRole('button', { name: /清理/ }));
    await waitFor(() => expect(mocks.cleanupWorkflowRecord).toHaveBeenCalledWith('n8n-wf-1'));
    expect(window.confirm).toHaveBeenCalledWith('确认清理 n8n workflow「hello n8n」？');
    expect(mocks.toastSuccess).toHaveBeenCalledWith('清理完成');
  });

  it('keeps the list controls focused on a single Flocks-managed sync action', async () => {
    renderPage('/workflows/n8n');

    expect(await screen.findByText('hello n8n')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '同步外部' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(mocks.listConnections).not.toHaveBeenCalled();
  });

  it('discovers remote flocks-prefixed n8n workflows when syncing from an empty list', async () => {
    const user = userEvent.setup();
    mocks.listWorkflowRecords.mockResolvedValue({ data: [] });
    renderPage('/workflows/n8n');

    expect(await screen.findByText('暂无 n8n 工作流')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '同步状态' }));

    await waitFor(() => expect(mocks.syncWorkflowRecords).toHaveBeenCalledWith({ includeExternal: false }));
    expect(await screen.findByText('hello n8n')).toBeInTheDocument();
    expect(mocks.toastSuccess).toHaveBeenCalledWith('同步完成', '新增 1，更新 0，失联 0，失败连接 0');
  });

  it('does not show external n8n workflow records in the Flocks-managed list', async () => {
    mocks.listWorkflowRecords.mockResolvedValue({
      data: [
        record,
        {
          ...record,
          id: 'n8n-external-1',
          name: 'external workflow',
          source: 'external',
          ownership: 'readonly',
          n8nWorkflowId: 'external-1',
        },
      ],
    });

    renderPage('/workflows/n8n');

    expect(await screen.findByText('hello n8n')).toBeInTheDocument();
    expect(screen.queryByText('external workflow')).not.toBeInTheDocument();
  });

  it('shows detail actions for sync, retry test, open n8n, and cleanup', async () => {
    const user = userEvent.setup();
    renderPage('/workflows/n8n/n8n-wf-1');

    expect(await screen.findByRole('heading', { name: 'hello n8n' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /打开 n8n/ })).toHaveAttribute('href', record.workflowUrl);

    await user.click(screen.getByRole('button', { name: '同步' }));
    await waitFor(() => expect(mocks.syncWorkflowRecord).toHaveBeenCalledWith('n8n-wf-1'));
    expect(mocks.toastSuccess).toHaveBeenCalledWith('同步完成');

    await user.click(screen.getByRole('button', { name: '重跑测试' }));
    await waitFor(() => expect(mocks.retryWorkflowRecordTests).toHaveBeenCalledWith('n8n-wf-1'));
    expect(mocks.toastSuccess).toHaveBeenCalledWith('测试完成');

    await user.click(screen.getByRole('button', { name: /清理/ }));
    await waitFor(() => expect(mocks.cleanupWorkflowRecord).toHaveBeenCalledWith('n8n-wf-1'));
  });

  it('routes to the created n8n record after a build run creates one', async () => {
    const user = userEvent.setup();
    renderPage('/workflows/n8n/new');

    await user.click(await screen.findByRole('button', { name: 'mock build n8n' }));

    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith('/workflows/n8n/n8n-created-1'));
  });

  it('routes guide prompts from standalone n8n creation to the workflow workbench', async () => {
    const user = userEvent.setup();
    renderPage('/workflows/n8n/new');

    await user.click(await screen.findByRole('button', { name: 'mock send to workbench' }));

    expect(mocks.navigate).toHaveBeenCalledWith('/workflows/new', {
      state: {
        freshCreate: true,
        chatLaunchRequest: expect.objectContaining({
          prompt: 'n8n prompt',
          displayLabel: '发送到工作台生成',
        }),
      },
    });
  });
});
