import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import NetworkProxyPanel from './NetworkProxyPanel';
import type { NetworkProxyView } from '@/api/networkProxy';

const { networkProxyApi, toast } = vi.hoisted(() => ({
  networkProxyApi: { get: vi.fn(), update: vi.fn(), test: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: Record<string, unknown>) => (opts ? `${key}:${JSON.stringify(opts)}` : key) }),
}));
vi.mock('@/api/networkProxy', () => ({ networkProxyApi }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toast }));

const systemView: NetworkProxyView = {
  mode: 'system',
  http: null,
  https: null,
  all: null,
  username: null,
  hasPassword: false,
  bypass: [],
  bypassPrivate: true,
  source: 'default',
  effective: { active: false, http: null, https: null, bypass: [], source: 'env' },
  processEnv: {},
};

const manualView: NetworkProxyView = {
  ...systemView,
  mode: 'manual',
  https: 'socks5h://127.0.0.1:7891',
  bypass: ['*.corp.example', '10.0.0.0/8'],
  source: 'config',
  effective: { active: true, http: null, https: 'socks5h://127.0.0.1:7891', bypass: ['*.corp.example', '10.0.0.0/8'], source: 'config' },
  processEnv: { HTTPS_PROXY: 'socks5h://127.0.0.1:7891', NO_PROXY: '127.0.0.1,.corp.example,10.0.0.0/8' },
};

describe('NetworkProxyPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    networkProxyApi.get.mockResolvedValue(systemView);
    networkProxyApi.update.mockResolvedValue(manualView);
  });

  it('switches to manual, validates the URL and saves the whitelist', async () => {
    const user = userEvent.setup();
    render(<NetworkProxyPanel />);

    const manual = await screen.findByRole('radio', { name: /modeManual/ });
    expect(screen.getByRole('radio', { name: /modeSystem/ })).toHaveAttribute('aria-checked', 'true');
    await user.click(manual);
    const save = screen.getByRole('button', { name: 'save' });

    const https = screen.getByLabelText('https');
    await user.type(https, 'ftp://nope');
    expect(https).toHaveAttribute('aria-invalid', 'true');
    expect(save).toBeDisabled();

    await user.clear(https);
    await user.type(https, 'socks5h://127.0.0.1:7891');
    expect(https).not.toHaveAttribute('aria-invalid');
    await user.type(screen.getByLabelText('bypass'), '*.corp.example\n10.0.0.0/8');
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => expect(networkProxyApi.update).toHaveBeenCalledTimes(1));
    // partial update: only the fields that changed travel (the server redacts URLs it hands out)
    expect(networkProxyApi.update).toHaveBeenCalledWith({ mode: 'manual', https: 'socks5h://127.0.0.1:7891', bypass: ['*.corp.example', '10.0.0.0/8'] });
    expect(networkProxyApi.update.mock.calls[0][0]).not.toHaveProperty('password');
    expect(networkProxyApi.update.mock.calls[0][0]).not.toHaveProperty('bypassPrivate');
    expect(toast.success).toHaveBeenCalledWith('saved');
    expect(screen.getByTestId('network-proxy-effective')).toHaveTextContent('effectiveActive');
    expect(screen.getByTestId('network-proxy-effective')).toHaveTextContent('socks5h://127.0.0.1:7891');
  });

  it('shows the backend message when saving is rejected', async () => {
    const user = userEvent.setup();
    networkProxyApi.get.mockResolvedValue(manualView);
    networkProxyApi.update.mockRejectedValue({ response: { data: { error: 'HTTPException', message: 'invalid bypass rule: not a rule!' } } });
    render(<NetworkProxyPanel />);

    const bypass = await screen.findByLabelText('bypass');
    await user.type(bypass, '\nnot a rule!');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('saveFailed: invalid bypass rule: not a rule!'));
  });

  it('runs the connectivity test and reports the route', async () => {
    const user = userEvent.setup();
    networkProxyApi.get.mockResolvedValue(manualView);
    networkProxyApi.test.mockResolvedValue({ ok: true, url: 'https://example.com/', via: 'socks5h://127.0.0.1:7891', status: 204, elapsedMs: 87, error: null });
    render(<NetworkProxyPanel />);

    const url = await screen.findByLabelText('testUrl');
    await user.clear(url);
    await user.type(url, 'https://example.com/');
    await user.click(screen.getByRole('button', { name: 'testRun' }));

    await waitFor(() => expect(networkProxyApi.test).toHaveBeenCalledWith('https://example.com/'));
    expect(await screen.findByText(/testOk:/)).toHaveTextContent('"status":204');
    expect(screen.getByText(/testOk:/)).toHaveTextContent('socks5h://127.0.0.1:7891');
  });
});
