import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import UserDefinedPageHost from './index';
import { setupSSEMock } from '@/test/mocks/sse';

const { getMock, loadBundleMock, installMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  loadBundleMock: vi.fn(),
  installMock: vi.fn(),
}));

vi.mock('@/api/userDefinedPages', () => ({
  userDefinedPagesAPI: {
    get: getMock,
  },
}));

vi.mock('./runtime', () => ({
  installUserDefinedPageRuntime: installMock,
  loadUserDefinedPageBundle: loadBundleMock,
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
  return <div>自定义页面内容</div>;
}

describe('UserDefinedPageHost', () => {
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
          route: '/user-defined-pages/dash-1',
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
      <MemoryRouter initialEntries={['/user-defined-pages/dash-1']}>
        <Routes>
          <Route path="/user-defined-pages/:pageId/*" element={<UserDefinedPageHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('自定义页面内容')).toBeInTheDocument();
    });
    expect(installMock).toHaveBeenCalledWith('dash-1');
  });

  it('shows build error when bundle is not ready', async () => {
    getMock.mockResolvedValue({
      data: {
        manifest: {
          id: 'dash-2',
          title: '失败页',
          route: '/user-defined-pages/dash-2',
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
      <MemoryRouter initialEntries={['/user-defined-pages/dash-2']}>
        <Routes>
          <Route path="/user-defined-pages/:pageId/*" element={<UserDefinedPageHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('esbuild failed')).toBeInTheDocument();
    });
  });
});
