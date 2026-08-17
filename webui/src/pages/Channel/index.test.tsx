import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ChannelPage from './index';

const { client, toast, useAgents, flocksproUsersApi } = vi.hoisted(() => ({
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
  flocksproUsersApi: {
    hasCapability: vi.fn(),
  },
}));

vi.mock('@/api/client', () => ({ default: client }));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toast,
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => useAgents(),
}));

vi.mock('@/api/flocksproUsers', () => ({
  flocksproUsersApi,
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
    flocksproUsersApi.hasCapability.mockResolvedValue(false);
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
