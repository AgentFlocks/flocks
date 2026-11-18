import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ChannelPage from './index';
import { backendError } from '@/test/helpers';

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

describe('ChannelPage saved secret reference', () => {
  const reference = '{secret:channel_wecom_secret}';

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
        // What GET /api/config returns admins: where the secret is stored, not the secret.
        return Promise.resolve({
          data: { channels: { wecom: { enabled: true, botId: 'bot-id', secret: reference } } },
        });
      }
      if (url === '/api/channel/status') {
        return Promise.resolve({ data: {} });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  async function saveAndGetWeComPayload(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/config/', expect.any(Object));
    });
    return client.patch.mock.calls[0][1].channels.wecom;
  }

  it('shows the stored secret as saved instead of its reference text', async () => {
    render(<ChannelPage />);

    const secretInput = await screen.findByPlaceholderText('secretInput.savedPlaceholder');
    expect(secretInput).toHaveValue('');
    expect(screen.getByText('secretInput.saved')).toBeInTheDocument();
    expect(screen.queryByDisplayValue(reference)).not.toBeInTheDocument();
  });

  it('keeps the stored secret when the field is left alone', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);
    await screen.findByPlaceholderText('secretInput.savedPlaceholder');

    expect((await saveAndGetWeComPayload(user)).secret).toBe(reference);
  });

  it('saves a newly typed secret', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.type(await screen.findByPlaceholderText('secretInput.savedPlaceholder'), 'new-secret');
    expect(screen.getByDisplayValue('new-secret')).toBeInTheDocument();

    expect((await saveAndGetWeComPayload(user)).secret).toBe('new-secret');
  });

  it('header says the connection failed while the gateway waits to retry', async () => {
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') {
        return Promise.resolve({
          data: [{
            id: 'wecom',
            label: 'WeCom',
            aliases: [],
            capabilities: { chat_types: ['direct'], media: false, threads: false, reactions: false, edit: false, rich_text: false },
            running: true,
          }],
        });
      }
      if (url === '/api/config') {
        return Promise.resolve({
          data: { channels: { wecom: { enabled: true, botId: 'bot-id', secret: reference } } },
        });
      }
      if (url === '/api/channel/status') {
        return Promise.resolve({
          data: { wecom: { connected: false, last_error: 'auth refused', error_count: 1, reconnect_count: 2 } },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    render(<ChannelPage />);

    expect(await screen.findByText('auth refused')).toBeInTheDocument();
    expect(screen.queryByText('header.connecting')).not.toBeInTheDocument();
  });

  it('leaves the stored secret alone when a typed one is cleared again', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.type(await screen.findByPlaceholderText('secretInput.savedPlaceholder'), 'x');
    await user.clear(screen.getByDisplayValue('x'));

    // Left out of the save, so the server keeps what it stores.
    const payload = await saveAndGetWeComPayload(user);
    expect(Object.prototype.hasOwnProperty.call(payload, 'secret')).toBe(false);
  });
});

describe('ChannelPage feishu account credentials', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.patch.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') {
        return Promise.resolve({
          data: [{
            id: 'feishu',
            label: 'Feishu',
            aliases: [],
            capabilities: { chat_types: ['direct', 'group'], media: true, threads: true, reactions: true, edit: true, rich_text: true },
            running: false,
          }],
        });
      }
      if (url === '/api/config') {
        return Promise.resolve({
          data: {
            channels: {
              feishu: {
                enabled: false,
                accounts: { a: { enabled: true, appId: 'cli_a', appSecret: '{secret:feishu_account_a_secret}' } },
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

  it('keeps a nested account secret when its field is typed in and cleared again', async () => {
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.click(await screen.findByText('feishu.multiAccount'));
    await user.type(await screen.findByPlaceholderText('secretInput.savedPlaceholder'), 'x');
    await user.clear(screen.getByDisplayValue('x'));
    await user.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/config/', expect.any(Object));
    });

    // As sent on the wire: an empty string here would overwrite the stored reference.
    const sent = JSON.parse(JSON.stringify(client.patch.mock.calls[0][1]));
    const account = sent.channels.feishu.accounts.a;
    expect(account.appId).toBe('cli_a');
    expect(Object.prototype.hasOwnProperty.call(account, 'appSecret')).toBe(false);
  });
});

describe('ChannelPage email credentials across auth modes', () => {
  function mockEmail(email: Record<string, unknown>) {
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') {
        return Promise.resolve({
          data: [{
            id: 'email',
            label: 'Email',
            aliases: [],
            capabilities: { chat_types: ['direct'], media: true, threads: false, reactions: false, edit: false, rich_text: false },
            running: false,
          }],
        });
      }
      if (url === '/api/config') {
        return Promise.resolve({ data: { channels: { email } } });
      }
      if (url === '/api/channel/status') {
        return Promise.resolve({ data: {} });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.patch.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
  });

  async function switchTypeClearAndSave(
    user: ReturnType<typeof userEvent.setup>,
    mode: string,
    placeholder: string,
  ) {
    // The auth mode <select>, found by the option it currently shows.
    const current = mode === 'xoauth2' ? 'email.authModePassword' : 'email.authModeXoauth2';
    await user.selectOptions(screen.getByDisplayValue(current), mode);
    const input = await screen.findByPlaceholderText(placeholder);
    await user.type(input, 'x');
    await user.clear(screen.getByDisplayValue('x'));
    // The other credential's saved reference must not come back into this field.
    expect(screen.getByPlaceholderText(placeholder)).toHaveValue('');
    expect(screen.queryByPlaceholderText('secretInput.savedPlaceholder')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/config/', expect.any(Object));
    });
    return client.patch.mock.calls[0][1].channels.email;
  }

  it('does not carry the saved password into the access token field', async () => {
    mockEmail({ enabled: false, authMode: 'password', username: 'u', password: '{secret:channel_email_password}' });
    const user = userEvent.setup();
    render(<ChannelPage />);
    await screen.findByPlaceholderText('secretInput.savedPlaceholder');

    const payload = await switchTypeClearAndSave(user, 'xoauth2', 'ya29...');
    expect(payload.authMode).toBe('xoauth2');
    expect(Object.prototype.hasOwnProperty.call(payload, 'accessToken')).toBe(false);
  });

  it('does not carry the saved access token into the password field', async () => {
    mockEmail({ enabled: false, authMode: 'xoauth2', username: 'u', accessToken: '{secret:channel_email_accessToken}' });
    const user = userEvent.setup();
    render(<ChannelPage />);
    await screen.findByPlaceholderText('secretInput.savedPlaceholder');

    const payload = await switchTypeClearAndSave(user, 'password', 'app password');
    expect(payload.authMode).toBe('password');
    expect(Object.prototype.hasOwnProperty.call(payload, 'password')).toBe(false);
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

describe('ChannelPage pairing failures', () => {
  const capabilities = { chat_types: ['direct'], media: true, threads: false, reactions: false, edit: false, rich_text: false };

  function serveChannel(id: string, saved: Record<string, unknown>) {
    client.get.mockImplementation((url: string) => {
      if (url === '/api/channel/list') {
        return Promise.resolve({ data: [{ id, label: id, aliases: [], capabilities, running: false }] });
      }
      if (url === '/api/config') return Promise.resolve({ data: { channels: { [id]: saved } } });
      if (url === '/api/channel/status') return Promise.resolve({ data: {} });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    useAgents.mockReturnValue({ agents: [] });
    client.patch.mockResolvedValue({ data: {} });
  });

  it('says why a Telegram pairing code was refused', async () => {
    serveChannel('telegram', { enabled: true, botToken: 'token', allowFrom: ['1001'] });
    client.post.mockRejectedValue(backendError(400, '配对码无效或已过期'));
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.type(await screen.findByPlaceholderText('telegram.pairingCodePlaceholder'), 'abc123');
    await user.click(screen.getByRole('button', { name: 'telegram.pairingButton' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('telegram.pairingError', '配对码无效或已过期');
    });
    expect(client.post).toHaveBeenCalledWith('/api/channel/telegram/pair', { code: 'ABC123' });
  });

  it('says why the Weixin QR login could not start', async () => {
    serveChannel('weixin', { enabled: true });
    client.post.mockRejectedValue(backendError(502, '无法连接微信 iLink 服务'));
    const user = userEvent.setup();
    render(<ChannelPage />);

    await user.click(await screen.findByRole('button', { name: /weixin.qrLoginButton/ }));

    expect(await screen.findByText('无法连接微信 iLink 服务')).toBeInTheDocument();
    expect(screen.queryByText(/status code 502/)).not.toBeInTheDocument();
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
