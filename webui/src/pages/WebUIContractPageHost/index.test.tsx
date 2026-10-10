import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WebUIContractPageHost from './index';
import PageRuntimeHost from './PageRuntimeHost';
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
  const sse = setupSSEMock();

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
    // The loader installs the page runtime itself, right before the bundle evaluates.
    expect(installMock).not.toHaveBeenCalled();
    expect(loadBundleMock).toHaveBeenCalledWith(
      'https://api.example.test/api/contracts/webui/pages/dash-1/bundle.js?v=abc123',
      'host.bundleMissingExport',
      'dash-1',
    );
  });

  it('loads a known workspace bundle without refetching its build metadata', async () => {
    render(<PageRuntimeHost pageId="dash-cached" initialBuildHash="cached123" />);

    await waitFor(() => {
      expect(screen.getByText('契约页面内容')).toBeInTheDocument();
    });
    expect(getMock).not.toHaveBeenCalled();
    expect(loadBundleMock).toHaveBeenCalledWith(
      'https://api.example.test/api/contracts/webui/pages/dash-cached/bundle.js?v=cached123',
      'host.bundleMissingExport',
      'dash-cached',
    );
  });

  it('re-reads the build after the event stream reconnects and reloads only a changed bundle', async () => {
    const meta = (hash: string) => ({ data: { manifest: { id: 'dash-r', title: 'r', route: '/contracts/webui/dash-r', icon: 'LayoutDashboard', order: 1, enabled: true, placement: 'home.after', entry: 'src/index.tsx', updatedAt: 1 }, build: { hash, builtAt: 1, status: 'ready' } } });
    getMock.mockResolvedValue(meta('h1'));
    render(<PageRuntimeHost pageId="dash-r" />);
    await waitFor(() => expect(screen.getByText('契约页面内容')).toBeInTheDocument());
    act(() => { sse.open(); });
    expect(loadBundleMock).toHaveBeenCalledTimes(1);

    // The page is rebuilt while it sits in the back/forward cache; the update event is lost.
    getMock.mockResolvedValue(meta('h2'));
    const hidden = new Event('pagehide');
    Object.defineProperty(hidden, 'persisted', { value: true });
    const shown = new Event('pageshow');
    Object.defineProperty(shown, 'persisted', { value: true });
    act(() => { window.dispatchEvent(hidden); window.dispatchEvent(shown); });
    act(() => { sse.open(); });

    await waitFor(() => expect(loadBundleMock).toHaveBeenLastCalledWith(
      'https://api.example.test/api/contracts/webui/pages/dash-r/bundle.js?v=h2',
      'host.bundleMissingExport',
      'dash-r',
    ));

    // Same build after another reconnect: nothing reloads, the page stays mounted.
    const calls = loadBundleMock.mock.calls.length;
    act(() => { window.dispatchEvent(hidden); window.dispatchEvent(shown); });
    act(() => { sse.open(); });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(3));
    expect(loadBundleMock).toHaveBeenCalledTimes(calls);
    expect(screen.getByText('契约页面内容')).toBeInTheDocument();
  });

  describe('event stream recovery', () => {
    const meta = (hash: string, status = 'ready', error?: string) => ({ data: { manifest: { id: 'dash-x', title: 'x', route: '/contracts/webui/dash-x', icon: 'LayoutDashboard', order: 1, enabled: true, placement: 'home.after', entry: 'src/index.tsx', updatedAt: 1 }, build: { hash, builtAt: 1, status, error } } });
    const bundleUrl = (hash: string) => `https://api.example.test/api/contracts/webui/pages/dash-x/bundle.js?v=${hash}`;
    const reconnect = () => {
      const hidden = new Event('pagehide');
      Object.defineProperty(hidden, 'persisted', { value: true });
      const shown = new Event('pageshow');
      Object.defineProperty(shown, 'persisted', { value: true });
      act(() => { window.dispatchEvent(hidden); window.dispatchEvent(shown); });
      act(() => { sse.open(); });
    };

    it('shows a build that failed while the stream was down', async () => {
      getMock.mockResolvedValue(meta('h1'));
      render(<PageRuntimeHost pageId="dash-x" />);
      await waitFor(() => expect(screen.getByText('契约页面内容')).toBeInTheDocument());
      act(() => { sse.open(); });

      getMock.mockResolvedValue(meta('', 'failed', 'compile error'));
      reconnect();
      await waitFor(() => expect(screen.getByText('compile error')).toBeInTheDocument());
    });

    it('retries a page whose bundle failed to load, even on the same build', async () => {
      getMock.mockResolvedValue(meta('h1'));
      loadBundleMock.mockRejectedValueOnce(new Error('network down'));
      render(<PageRuntimeHost pageId="dash-x" />);
      await waitFor(() => expect(screen.getByText('network down')).toBeInTheDocument());
      act(() => { sse.open(); });

      reconnect();
      await waitFor(() => expect(screen.getByText('契约页面内容')).toBeInTheDocument());
      expect(loadBundleMock).toHaveBeenLastCalledWith(bundleUrl('h1'), 'host.bundleMissingExport', 'dash-x');
    });

    it('ignores a late reconnect answer once a newer build has loaded', async () => {
      getMock.mockResolvedValue(meta('h1'));
      render(<PageRuntimeHost pageId="dash-x" />);
      await waitFor(() => expect(screen.getByText('契约页面内容')).toBeInTheDocument());
      act(() => { sse.open(); });

      let answer: (value: unknown) => void = () => undefined;
      getMock.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
      reconnect();
      // The update event for h2 arrives while the reconnect check is still out.
      act(() => { sse.send({ type: 'contracts.webui.pages.updated', properties: { id: 'dash-x', hash: 'h2' } }); });
      await waitFor(() => expect(loadBundleMock).toHaveBeenLastCalledWith(bundleUrl('h2'), 'host.bundleMissingExport', 'dash-x'));
      const calls = loadBundleMock.mock.calls.length;

      await act(async () => { answer(meta('h1')); });
      expect(loadBundleMock).toHaveBeenCalledTimes(calls);
      expect(loadBundleMock).toHaveBeenLastCalledWith(bundleUrl('h2'), 'host.bundleMissingExport', 'dash-x');
    });
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
