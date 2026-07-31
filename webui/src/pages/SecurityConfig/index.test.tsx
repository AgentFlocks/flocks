import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import SecurityConfigPage from './index';

const { getOverview, setRollout, toastSuccess, toastError } = vi.hoisted(() => ({
  getOverview: vi.fn(),
  setRollout: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
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
        },
        source: 'runtime',
      },
      hardDeny: { systemRuleIds: [] },
      readonlyCeiling: { denyPatterns: [] },
      audit: { webhookConfigured: false },
      filesystem: {
        excludedTools: [],
        policyVersion: 'filesystem-v1',
        decisionMatrix: {
          readonly: { read: 'allow' },
          'require-confirm': { read: 'allow' },
          'auto-allow-all': { read: 'allow' },
        },
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

    const openButton = await screen.findByRole('button', { name: '查看详情' });
    await user.click(openButton);
    expect(screen.getByRole('dialog', { name: '文件管控策略矩阵' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '文件管控策略矩阵' })).not.toBeInTheDocument();
    });
  });
});
