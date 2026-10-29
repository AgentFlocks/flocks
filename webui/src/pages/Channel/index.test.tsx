import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ChannelPage from './index';

const { client, toast, useAgents } = vi.hoisted(() => ({
  client: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  useAgents: vi.fn(),
}));

vi.mock('@/api/client', () => ({ default: client }));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toast,
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => useAgents(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

describe('ChannelPage WeCom configuration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.patch.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') {
        return Promise.resolve({
          data: [{
            id: 'wecom',
            label: 'WeCom',
            aliases: [],
            capabilities: {
              chat_types: ['direct', 'group'],
              media: true,
              threads: false,
              reactions: false,
              edit: false,
              rich_text: false,
            },
            running: false,
          }],
        });
      }
      if (url === '/api/config') {
        return Promise.resolve({
          data: {
            channels: {
              wecom: {
                enabled: false,
                botId: 'bot-id',
                secret: 'secret',
                websocketUrl: 'fafafafaf',
              },
            },
          },
        });
      }
      if (url === '/api/channel/status') {
        return Promise.resolve({ data: {} });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  it('sends an explicit empty websocket URL after the field is cleared', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);

    const websocketUrlInput = await screen.findByDisplayValue('fafafafaf');
    await user.clear(websocketUrlInput);
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/config/', expect.any(Object));
    });

    const payload = client.patch.mock.calls[0][1];
    expect(
      Object.prototype.hasOwnProperty.call(payload.channels.wecom, 'websocketUrl'),
    ).toBe(true);
    expect(payload.channels.wecom.websocketUrl).toBe('');
  });
});

describe('ChannelPage error surfacing', () => {
  const feishuList = [{
    id: 'feishu',
    label: 'Feishu',
    aliases: [],
    capabilities: { chat_types: ['direct', 'group'], media: true, threads: false, reactions: true, edit: false, rich_text: false },
    running: true,
  }];

  beforeEach(() => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.patch.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') return Promise.resolve({ data: feishuList });
      if (url === '/api/config') {
        return Promise.resolve({ data: { channels: { feishu: { enabled: true, appId: 'cli_x', appSecret: 'y' } } } });
      }
      if (url === '/api/channel/status') return Promise.resolve({ data: {} });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  it('save failure toast carries the server message from the HTTPException body', async () => {
    client.patch.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: { status: 400, data: { error: 'HTTPException', message: 'appId must start with cli_' } },
    });
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.click(await screen.findByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('saveFailed', 'appId must start with cli_');
    });
    // The form keeps the user's input after a failed save.
    expect(screen.getByDisplayValue('cli_x')).toBeInTheDocument();
  });

  it('restart reports restartFailed instead of success when the request fails', async () => {
    client.post.mockRejectedValue({
      message: 'Request failed with status code 500',
      response: { status: 500, data: { error: 'InternalServerError', message: 'Internal server error' } },
    });
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.click(await screen.findByRole('button', { name: /restart/ }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('restartFailed', 'Internal server error');
    });
    expect(toast.success).not.toHaveBeenCalled();
    // The button leaves the "restarting" state so the user can retry.
    expect(screen.getByRole('button', { name: /restart/ })).not.toBeDisabled();
  });

  it('restart success still toasts restartSuccess', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.click(await screen.findByRole('button', { name: /restart/ }));

    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith('restartSuccess');
    });
    expect(client.post).toHaveBeenCalledWith('/api/channel/feishu/restart', {}, { timeout: 5000 });
  });
});

describe('ChannelPage feishu transport badge', () => {
  const list = [{
    id: 'feishu', label: 'Feishu', aliases: [],
    capabilities: { chat_types: ['direct'], media: false, threads: false, reactions: false, edit: false, rich_text: false },
    running: true,
  }];
  const setup = (feishu: Record<string, unknown>) => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') return Promise.resolve({ data: list });
      if (url === '/api/config') return Promise.resolve({ data: { channels: { feishu } } });
      if (url === '/api/channel/status') return Promise.resolve({ data: { feishu: { connected: true } } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  };

  it('shows Webhook when feishu is configured in webhook mode', async () => {
    setup({ enabled: true, appId: 'cli_x', connectionMode: 'webhook' });
    render(<ChannelPage />);
    expect(await screen.findByText('Webhook')).toBeInTheDocument();
    expect(screen.queryByText('WebSocket')).not.toBeInTheDocument();
  });

  it('shows WebSocket for the default long-connection mode', async () => {
    setup({ enabled: true, appId: 'cli_x' });
    render(<ChannelPage />);
    expect(await screen.findByText('WebSocket')).toBeInTheDocument();
  });
});
