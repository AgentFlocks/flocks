import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { Routes } from './index';

const { useAuthMock } = vi.hoisted(() => ({ useAuthMock: vi.fn() }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: useAuthMock }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/i18nResources', () => ({ preloadI18nNamespaces: async () => undefined }));
vi.mock('@/utils/chunkLoadRecovery', () => ({
  installVitePreloadErrorRecovery: vi.fn(),
  recoverLazyLoad: <T,>(promise: Promise<T>) => promise,
}));
vi.mock('@/components/common/LazyLoadErrorBoundary', () => ({
  default: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('@/components/common/RoutePageSkeleton', () => ({ default: () => <div>Route loading</div> }));
vi.mock('@/components/layout/AuthLayout', () => ({ default: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/components/layout/Layout', async () => {
  const { Outlet } = await import('react-router-dom');
  return { default: () => <div data-testid="authenticated-layout"><Outlet /></div> };
});
vi.mock('@/pages/Home', () => ({ default: () => <div>Home page</div> }));
vi.mock('@/pages/Login', () => ({ default: () => <div>Login required</div> }));
vi.mock('@/pages/SetupAdmin', () => ({ default: () => <div>Setup required</div> }));
vi.mock('@/pages/ForceChangePassword', () => ({ default: () => <div>Password reset required</div> }));
// Keep this focused on routing and authorization, not the real pages' API effects.
vi.mock('@/pages/Agent', () => ({ default: () => <div>Normal agent page</div> }));
vi.mock('@/pages/Workflow', () => ({ default: () => <div>Normal workflow page</div> }));
vi.mock('@/pages/Skill', () => ({ default: () => <div>Normal skill page</div> }));
vi.mock('@/pages/Tool', () => ({ default: () => <div>Normal tool page</div> }));
vi.mock('@/pages/DeviceIntegration', () => ({ default: () => <div>Normal device page</div> }));
vi.mock('@/pages/PluginLibraryPrototype', () => ({
  default: ({ pluginType }: { pluginType: string }) => <div data-testid="page-local-groups">Grouped {pluginType} page</div>,
}));

const pages = [
  { path: '/agents', type: 'agent' },
  { path: '/workflows', type: 'workflow' },
  { path: '/skills', type: 'skill' },
  { path: '/tools', type: 'tool' },
  { path: '/devices', type: 'device' },
] as const;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="route-location">{location.pathname}{location.search}</div>;
}

function openPage(url: string) {
  return render(<MemoryRouter initialEntries={[url]}><Routes /><LocationProbe /></MemoryRouter>);
}

function authenticated() {
  return {
    loading: false,
    bootstrapped: true,
    error: null,
    user: { role: 'member', must_reset_password: false },
    refresh: vi.fn(),
  };
}

beforeEach(() => {
  vi.stubEnv('DEV', true);
  useAuthMock.mockReturnValue(authenticated());
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe('existing plugin-page grouped preview routes', () => {
  it.each(pages)('renders $path?preview=groups in the authenticated layout with the $type prop', async ({ path, type }) => {
    openPage(`${path}?preview=groups&type=conflicting-query-type&keep=demo`);
    const preview = await screen.findByTestId('page-local-groups');
    expect(preview).toHaveTextContent(`Grouped ${type} page`);
    expect(within(screen.getByTestId('authenticated-layout')).getByTestId('page-local-groups')).toBe(preview);
    expect(screen.queryByText(`Normal ${type} page`)).not.toBeInTheDocument();
    expect(screen.getByTestId('route-location')).toHaveTextContent(`${path}?preview=groups&type=conflicting-query-type&keep=demo`);
  });

  it('retains all five normal pages without the exact preview query, including unrelated and differently cased queries', async () => {
    for (const { path, type } of pages) {
      for (const query of ['', '?type=tool&groups=true', '?preview=other', '?preview=Groups']) {
        const mounted = openPage(path + query);
        expect(await screen.findByText(`Normal ${type} page`)).toBeInTheDocument();
        expect(screen.queryByTestId('page-local-groups')).not.toBeInTheDocument();
        mounted.unmount();
      }
    }
  });

  it('does not bypass authentication, first-run setup or mandatory password reset', async () => {
    const cases = [
      { auth: { ...authenticated(), user: null }, message: 'Login required', redirect: '/login' },
      { auth: { ...authenticated(), bootstrapped: false, user: null }, message: 'Setup required', redirect: '/setup-admin' },
      { auth: { ...authenticated(), user: { role: 'admin', must_reset_password: true } }, message: 'Password reset required', redirect: null },
    ];
    for (const { auth, message, redirect } of cases) {
      useAuthMock.mockReturnValue(auth);
      const mounted = openPage('/devices?preview=groups');
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(screen.queryByTestId('page-local-groups')).not.toBeInTheDocument();
      expect(screen.queryByTestId('authenticated-layout')).not.toBeInTheDocument();
      if (redirect) expect(screen.getByTestId('route-location')).toHaveTextContent(redirect);
      mounted.unmount();
    }
  });

  it('ignores preview=groups outside development and still renders each normal plugin page', async () => {
    vi.stubEnv('DEV', false);
    for (const { path, type } of pages) {
      const mounted = openPage(`${path}?preview=groups`);
      expect(await screen.findByText(`Normal ${type} page`)).toBeInTheDocument();
      expect(screen.queryByTestId('page-local-groups')).not.toBeInTheDocument();
      mounted.unmount();
    }
  });

  it('has no centralized /plugins/preview route in development or production and redirects it home', async () => {
    for (const development of [true, false]) {
      vi.stubEnv('DEV', development);
      const mounted = openPage('/plugins/preview?preview=groups&type=agent');
      expect(await screen.findByText('Home page')).toBeInTheDocument();
      expect(screen.queryByTestId('page-local-groups')).not.toBeInTheDocument();
      expect(screen.getByTestId('route-location')).toHaveTextContent(/^\/$/);
      mounted.unmount();
    }
  });
});
