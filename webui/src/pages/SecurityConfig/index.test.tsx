import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import SecurityConfigPage from './index';

const { getOverview, setRollout, setNetworkRules, toastSuccess, toastError } = vi.hoisted(() => ({
  getOverview: vi.fn(),
  setRollout: vi.fn(),
  setNetworkRules: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
    i18n: { resolvedLanguage: 'zh-CN' },
  }),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
  ),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
  }),
}));

vi.mock('@/api/flocksproSecurity', () => ({
  flocksproSecurityApi: {
    getOverview,
    setRollout,
    setNetworkRules,
  },
}));

describe('SecurityConfigPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getOverview.mockResolvedValue({
      rollout: {
        effective: {
          policy: 'shadow',
          command: 'shadow',
          ingress: 'disabled',
          visibility: 'shadow',
          filesystem: 'shadow',
          network: 'shadow',
        },
        source: 'runtime',
      },
      hardDeny: { systemRuleIds: [] },
      network: {
        hardDeny: ['127.0.0.0/8'],
        allowlist: [],
        blocklist: [],
        trustedTools: [{ name: 'websearch' }],
        revision: 1,
        modeDefaults: {},
      },
      readonlyCeiling: { denyPatterns: [] },
      audit: { webhookConfigured: false },
      filesystem: {
        excludedTools: [],
        policyVersion: 'filesystem-v1',
        decisionMatrix: [
          {
            region: 'workspace_general',
            operation: 'mutation',
            readonly: 'deny',
            requireConfirm: 'ask/confirm',
            autoAllowAll: 'allow',
          },
        ],
        runtimeOverrides: { 'exe-mode': { plugins: 'deny_mutation' } },
        hardDenies: { unknown_region: true },
        sharedPermissionMode: { supported: ['readonly', 'require-confirm', 'auto-allow-all'], default: 'readonly' },
        permissionDefaults: { webui: 'require-confirm' },
        runtimeDefaults: { webui: 'dev-mode' },
      },
    });
  });

  it('supports filesystem policy drawer open and Esc close', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SecurityConfigPage />
      </MemoryRouter>,
    );

    const openButton = await screen.findByRole('button', { name: '查看文件管控详情' });
    await user.click(openButton);
    const dialog = screen.getByRole('dialog', { name: '文件管控配置详情' });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveClass('md:w-2/3', 'md:min-w-[720px]', 'md:max-w-[1200px]');
    expect(screen.getByText('仅审计')).toBeInTheDocument();
    expect(screen.getByText(/Session permission mode.*Runtime mode.*路径区域规则/)).toBeInTheDocument();
    expect(screen.getAllByText('从 WebUI 创建的 Session').length).toBeGreaterThan(0);
    expect(screen.getByText('开发模式（dev-mode）')).toBeInTheDocument();
    expect(screen.getByText('执行模式（exe-mode）')).toBeInTheDocument();
    expect(screen.getAllByText('~/.flocks/plugins').length).toBeGreaterThan(0);
    expect(screen.getAllByText('读取 / 列表 / 搜索').length).toBeGreaterThan(0);
    expect(screen.getAllByText('变更（写入 / 创建 / 编辑 / 删除 / 移动 / 复制）').length).toBeGreaterThan(0);
    expect(screen.getByText('Permission mode 决策矩阵')).toBeInTheDocument();
    expect(screen.getByText('~/.flocks/workspace（不含当前用户 Output / 当前 Project）')).toBeInTheDocument();
    expect(screen.getAllByText('变更（写入 / 创建 / 编辑 / 删除 / 移动 / 复制）').length).toBeGreaterThan(0);
    expect(screen.getByText('ask/confirm')).toBeInTheDocument();
    expect(screen.getByText('纳管')).toBeInTheDocument();
    expect(screen.getByText('不纳管')).toBeInTheDocument();
    expect(screen.getByText(/memory_search/)).toBeInTheDocument();
    expect(document.body.style.overflow).toBe('hidden');

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '文件管控配置详情' })).not.toBeInTheDocument();
    });
    expect(document.body.style.overflow).toBe('');
  });

  it('hides network controls when an older Pro overview omits network', async () => {
    getOverview.mockResolvedValueOnce({
      rollout: {
        effective: {
          policy: 'shadow',
          command: 'shadow',
          ingress: 'disabled',
          visibility: 'shadow',
          filesystem: 'shadow',
        },
        source: 'runtime',
      },
      hardDeny: { systemRuleIds: [] },
      readonlyCeiling: { denyPatterns: [] },
      audit: { webhookConfigured: false },
      filesystem: {
        excludedTools: [],
        policyVersion: 'filesystem-v1',
        decisionMatrix: [],
        runtimeOverrides: [],
        hardDenies: {},
        sharedPermissionMode: { supported: [], default: 'readonly' },
        permissionDefaults: {},
        runtimeDefaults: {},
      },
    });

    render(
      <MemoryRouter>
        <SecurityConfigPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('文件管控')).toBeInTheDocument();
    expect(screen.queryByText('网络策略')).not.toBeInTheDocument();
    expect(screen.queryByText('网络管控')).not.toBeInTheDocument();
  });

  it('accepts ssh allowlist rules supported by backend validation', async () => {
    const user = userEvent.setup();
    setNetworkRules.mockResolvedValueOnce({
      allowlist: ['ssh://example.com:22'],
      blocklist: [],
      trustedTools: [{ name: 'websearch' }],
      revision: 2,
      hardDeny: ['127.0.0.0/8'],
    });

    render(
      <MemoryRouter>
        <SecurityConfigPage />
      </MemoryRouter>,
    );

    const allowlistInput = await screen.findByPlaceholderText(/example\.com/);
    await user.clear(allowlistInput);
    await user.type(allowlistInput, 'ssh://example.com:22');

    const saveButton = screen.getByRole('button', { name: '保存网络规则' });
    expect(saveButton).toBeEnabled();
    await user.click(saveButton);

    await waitFor(() => {
      expect(setNetworkRules).toHaveBeenCalledWith({
        allowlist: ['ssh://example.com:22'],
        blocklist: [],
        trustedTools: [{ name: 'websearch' }],
        revision: 1,
      });
    });
  });
});
