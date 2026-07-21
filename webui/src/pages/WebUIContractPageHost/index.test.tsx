import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WebUIContractPageHost from './index';
import { setupSSEMock } from '@/test/mocks/sse';

const { getMock, loadBundleMock, installMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  loadBundleMock: vi.fn(),
  installMock: vi.fn(),
}));

vi.mock('@/api/webuiContractPages', () => ({
  webuiContractPagesAPI: {
    get: getMock,
  },
}));

vi.mock('@/api/client', () => ({
  getApiBase: () => 'https://api.example.test',
}));

vi.mock('./runtime', () => ({
  installWebUIContractPageRuntime: installMock,
  loadWebUIContractPageBundle: loadBundleMock,
}));

vi.mock('@/i18n', () => ({
  default: {
    t: (key: string) => key,
    language: 'en-US',
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en-US' },
  }),
}));

function MockPage() {
  return <div>契约页面内容</div>;
}

describe('WebUIContractPageHost', () => {
  setupSSEMock();

  beforeEach(() => {
    vi.clearAllMocks();
    loadBundleMock.mockResolvedValue(MockPage);
  });

  it('renders the dynamically loaded page component', async () => {
    getMock.mockResolvedValue({
      data: {
        manifest: {
          id: 'dash-1',
          title: '仪表盘',
          route: '/contracts/webui/dash-1',
          icon: 'LayoutDashboard',
          order: 10,
          enabled: true,
          placement: 'home.after',
          entry: 'src/index.tsx',
          updatedAt: 1,
        },
        build: {
          hash: 'abc123',
          builtAt: 1,
          status: 'ready',
          error: null,
        },
        sourceFiles: ['src/Page.tsx'],
      },
    });

    render(
      <MemoryRouter initialEntries={['/contracts/webui/dash-1']}>
        <Routes>
          <Route path="/contracts/webui/:pageId/*" element={<WebUIContractPageHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('契约页面内容')).toBeInTheDocument();
    });
    expect(installMock).toHaveBeenCalledWith('dash-1');
    expect(loadBundleMock).toHaveBeenCalledWith(
      'https://api.example.test/api/contracts/webui/pages/dash-1/bundle.js?v=abc123',
      'host.bundleMissingExport',
    );
  });

  it('shows build error when bundle is not ready', async () => {
    getMock.mockResolvedValue({
      data: {
        manifest: {
          id: 'dash-2',
          title: '失败页',
          route: '/contracts/webui/dash-2',
          icon: 'LayoutDashboard',
          order: 20,
          enabled: true,
          placement: 'home.after',
          entry: 'src/index.tsx',
          updatedAt: 1,
        },
        build: {
          hash: '',
          builtAt: 0,
          status: 'failed',
          error: 'esbuild failed',
        },
        sourceFiles: ['src/Page.tsx'],
      },
    });

    render(
      <MemoryRouter initialEntries={['/contracts/webui/dash-2']}>
        <Routes>
          <Route path="/contracts/webui/:pageId/*" element={<WebUIContractPageHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('esbuild failed')).toBeInTheDocument();
    });
  });
});
