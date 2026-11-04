import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReaStatus } from '@/api/rea';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}));

const api = vi.hoisted(() => ({
  status: vi.fn(),
  uninstallPreview: vi.fn(),
  uninstall: vi.fn(),
}));

vi.mock('@/api/rea', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/rea')>();
  return { ...actual, reaApi: api };
});

import { ReaApiError } from '@/api/rea';
import { ReaHubActions } from './ReaHub';
import { __resetReaStateForTesting } from './useRea';

function readyStatus(): ReaStatus {
  return {
    package: 'rea-agents',
    version: '6.4.0',
    state: 'ready',
    installed: true,
    tier: 'light',
    installedVersion: '6.4.0',
    canInstall: true,
    badge: false,
    firstInstallCompleted: true,
    installSessionId: null,
    removable: true,
    degradedProblems: [],
    verification: null,
    environment: { ok: true, nodeRequirement: '>=22', checks: [] },
    job: null,
    uninstallJob: null,
  };
}

beforeEach(() => {
  __resetReaStateForTesting();
  Object.values(api).forEach((fn) => fn.mockReset());
});

afterEach(() => {
  __resetReaStateForTesting();
});

describe('REA uninstall in a managed install', () => {
  it('says REA goes with the deployment release instead of a raw error', async () => {
    api.status.mockResolvedValue(readyStatus());
    api.uninstallPreview.mockResolvedValue({ state: 'ready', tasks: [], removes: ['MCP 服务 rea'], keeps: [] });
    api.uninstall.mockRejectedValue(new ReaApiError(
      "REA is installed and removed with the deployment's release in a managed install", 'managed', 409,
    ));
    render(<MemoryRouter><ReaHubActions /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: '卸载' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认卸载' }));

    expect(await screen.findByText('这是受管部署，REA 随部署的发版安装和移除，不能在这里卸载。')).toBeInTheDocument();
    expect(screen.queryByText(/卸载没有开始/)).not.toBeInTheDocument();
    expect(api.uninstall).toHaveBeenCalledWith(false);
  });
});
