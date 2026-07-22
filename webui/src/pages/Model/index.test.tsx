import React from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ModelPage from './index';
import { renderWithRouter } from '@/test/helpers';

const mocks = vi.hoisted(() => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
  useProviders: vi.fn(),
  refetch: vi.fn(),
  getSummary: vi.fn(),
  getResolved: vi.fn(),
  getFallbacks: vi.fn(),
  setFallbacks: vi.fn(),
  listDefinitions: vi.fn(),
  catalogList: vi.fn(),
  createProvider: vi.fn(),
  getCredentials: vi.fn(),
  revealCredentials: vi.fn(),
  setCredentials: vi.fn(),
  testCredentials: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'status.models') return `${params?.count ?? 0} models`;
      if (key === 'dashboard.fallbackAvailability') {
        return `${params?.available ?? 0} / ${params?.total ?? 0} available`;
      }
      const translations: Record<string, string> = {
        pageTitle: 'Models',
        pageDescription: 'Manage providers',
        addProvider: 'Add Provider',
        providerAdded: 'Provider added',
        'providerList.empty': 'No providers',
        'providerList.emptyHint': 'Add one to get started',
        'providerList.addProvider': 'Add First Provider',
        'form.model': 'Model',
        'form.done': 'Done',
        'form.providerType': 'Provider Type',
        'form.selectProvider': 'Select Provider...',
        'form.baseUrlOptional': '(optional, leave empty for default)',
        'form.baseUrlRequired': 'Please enter Base URL',
        'form.apiKeyOptional': '(optional, leave empty for no-auth gateways)',
        'form.apiKeyOptionalHint': 'Leave empty for no-auth gateway',
        'form.apiKeyKeepExisting': 'Leave blank to keep the existing API key',
        'form.searchProvider': 'Search providers...',
        'form.noResults': 'No results',
        'form.alreadyAdded': 'Already added',
      };
      return translations[key] ?? key;
    },
    i18n: {
      language: 'en-US',
    },
  }),
}));

vi.mock('@/hooks/useProviders', () => ({
  useProviders: mocks.useProviders,
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: () => undefined,
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ action }: { action?: React.ReactNode }) => <div>{action}</div>,
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>Loading...</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description }: { title?: React.ReactNode; description?: React.ReactNode }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
    </div>
  ),
}));

vi.mock('@/components/common/EntitySheet', () => ({
  default: ({
    open,
    children,
    submitDisabled,
    submitLabel,
    onSubmit,
  }: {
    open?: boolean;
    children?: React.ReactNode;
    submitDisabled?: boolean;
    submitLabel?: React.ReactNode;
    onSubmit?: () => void;
  }) => open ? (
    <div data-testid="entity-sheet">
      <div>{children}</div>
      <button type="button" disabled={submitDisabled} onClick={onSubmit}>
        {submitLabel}
      </button>
    </div>
  ) : null,
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {
    getCredentials: mocks.getCredentials,
    revealCredentials: mocks.revealCredentials,
    setCredentials: mocks.setCredentials,
    testCredentials: mocks.testCredentials,
  },
  modelV2API: {
    listDefinitions: mocks.listDefinitions,
    createDefinition: vi.fn(),
    deleteDefinition: vi.fn(),
  },
  usageAPI: {
    getSummary: mocks.getSummary,
  },
  customAPI: {
    createProvider: mocks.createProvider,
  },
  modelSettingsAPI: {
    get: vi.fn(),
    update: vi.fn(),
  },
  catalogAPI: {
    list: mocks.catalogList,
  },
  defaultModelAPI: {
    getResolved: mocks.getResolved,
    getFallbacks: mocks.getFallbacks,
    setFallbacks: mocks.setFallbacks,
    delete: vi.fn(),
    set: vi.fn(),
  },
}));

describe('ModelPage add provider dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mocks.useProviders.mockReturnValue({
      providers: [],
      connectedIds: [],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getSummary.mockResolvedValue({ data: null });
    mocks.getResolved.mockResolvedValue({ data: null });
    mocks.getFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.setFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models: [] } });
    mocks.catalogList.mockResolvedValue({
      data: {
        providers: [
          {
            id: 'openai-compatible',
            name: 'OpenAI Compatible',
            description: 'Compatible endpoint',
            credential_schemas: [],
            env_vars: [],
            default_base_url: 'https://api.example.com/v1',
            model_count: 0,
            models: [],
            allow_multiple: true,
          },
        ],
      },
    });
    mocks.createProvider.mockResolvedValue({
      data: {
        id: 'custom-my-api',
      },
    });
  });

  it('blocks openai-compatible creation until Base URL is filled and submits once provided', async () => {
    const user = userEvent.setup();

    renderWithRouter(<ModelPage />);

    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    expect(await screen.findByTestId('entity-sheet')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Select Provider...' }));
    await user.click(await screen.findByRole('button', { name: /OpenAI Compatible/i }));

    expect(screen.queryByText('(optional, leave empty for default)')).not.toBeInTheDocument();

    const saveButton = screen.getByRole('button', { name: 'Save' });
    const baseUrlInput = screen.getByPlaceholderText('https://api.example.com/v1');

    await user.type(
      screen.getByPlaceholderText('e.g. SiliconFlow, LM Studio, My API'),
      'My API',
    );

    expect(saveButton).toBeEnabled();

    await user.clear(baseUrlInput);
    expect(saveButton).toBeDisabled();
    expect(mocks.createProvider).not.toHaveBeenCalled();

    await user.type(baseUrlInput, 'https://gateway.example.com/v1');

    expect(saveButton).toBeEnabled();

    await user.click(saveButton);

    await waitFor(() => {
      expect(mocks.createProvider).toHaveBeenCalledWith({
        name: 'My API',
        base_url: 'https://gateway.example.com/v1',
        api_key: 'not-needed',
        description: 'Compatible endpoint',
      });
    });
  });
});

describe('ModelPage configure provider dialog', () => {
  const provider = {
    id: 'openai',
    name: 'OpenAI',
    source: 'config',
    env: [],
    key: null,
    options: {},
    models: {},
    configured: true,
    modelCount: 1,
    category: 'connected',
  };
  const model = {
    id: 'gpt-4o',
    name: 'GPT-4o',
    provider_id: 'openai',
    model_type: 'chat',
    status: 'active',
    capabilities: {
      features: [],
      supports_streaming: true,
      supports_tools: true,
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.useProviders.mockReturnValue({
      providers: [provider],
      connectedIds: ['openai'],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getSummary.mockResolvedValue({ data: null });
    mocks.getResolved.mockResolvedValue({ data: null });
    mocks.getFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.setFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models: [model], total: 1 } });
    mocks.catalogList.mockResolvedValue({
      data: {
        providers: [{ id: 'openai', models: [] }],
      },
    });
    mocks.getCredentials.mockResolvedValue({
      data: {
        secret_id: 'openai_llm_key',
        api_key: null,
        api_key_masked: 'sk-***1234',
        base_url: 'https://old.example.com/v1',
        has_credential: true,
      },
    });
    mocks.revealCredentials.mockResolvedValue({
      data: {
        secret_id: 'openai_llm_key',
        api_key: 'sk-existing-secret-1234',
        api_key_masked: 'sk-***1234',
        base_url: 'https://old.example.com/v1',
        has_credential: true,
      },
    });
    mocks.setCredentials.mockResolvedValue({ data: { success: true } });
    mocks.testCredentials.mockResolvedValue({
      data: {
        success: true,
        message: 'ok',
        model_id: 'gpt-4o',
        question: 'ping',
        answer: 'pong',
        latency_ms: 10,
      },
    });
  });

  async function openConfigureDialog(user: ReturnType<typeof userEvent.setup>) {
    renderWithRouter(<ModelPage />);
    await user.click(await screen.findByTitle('Configure'));
    expect(await screen.findByTestId('entity-sheet')).toBeInTheDocument();
    expect(mocks.revealCredentials).toHaveBeenCalledWith('openai');
  }

  it('displays and preserves the existing API key when saving a new base URL', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);

    const apiKeyInput = screen.getByDisplayValue('sk-existing-secret-1234');
    expect(apiKeyInput).toHaveAttribute('type', 'password');
    expect(apiKeyInput).not.toHaveValue('sk-***1234');
    await user.click(screen.getByTitle('form.show'));
    expect(apiKeyInput).toHaveAttribute('type', 'text');

    const baseUrlInput = screen.getByPlaceholderText('https://api.example.com/v1');
    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://new.example.com/v1');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    const payload = mocks.setCredentials.mock.calls.at(-1)?.[1];
    expect(payload).toEqual(expect.objectContaining({
      base_url: 'https://new.example.com/v1',
    }));
    expect(payload).not.toHaveProperty('api_key');
  });

  it('persists the displayed API key and a base URL change before testing the connection', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    mocks.setCredentials.mockClear();
    mocks.testCredentials.mockClear();

    const baseUrlInput = screen.getByPlaceholderText('https://api.example.com/v1');
    await user.clear(baseUrlInput);
    await user.type(baseUrlInput, 'https://test.example.com/v1');
    await user.click(screen.getByRole('button', { name: 'form.testConnection2' }));

    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    const payload = mocks.setCredentials.mock.calls[0]?.[1];
    expect(payload).toEqual(expect.objectContaining({
      base_url: 'https://test.example.com/v1',
    }));
    expect(payload).not.toHaveProperty('api_key');
    await waitFor(() => {
      expect(mocks.testCredentials).toHaveBeenCalledWith('openai', 'gpt-4o');
    });
  });

  it('submits a replacement API key when the displayed value changes', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);

    const apiKeyInput = screen.getByDisplayValue('sk-existing-secret-1234');
    await user.clear(apiKeyInput);
    await user.type(apiKeyInput, 'sk-replacement-secret');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    expect(mocks.setCredentials.mock.calls.at(-1)?.[1]).toEqual(expect.objectContaining({
      api_key: 'sk-replacement-secret',
    }));
  });
});

describe('ModelPage fallback model editor', () => {
  const providers = [
    {
      id: 'openai',
      name: 'OpenAI',
      source: 'config',
      env: [],
      key: null,
      options: {},
      models: {},
      configured: true,
      modelCount: 1,
      category: 'connected',
    },
    {
      id: 'minimax',
      name: 'MiniMax',
      source: 'config',
      env: [],
      key: null,
      options: {},
      models: {},
      configured: true,
      modelCount: 1,
      category: 'connected',
    },
  ];
  const models = [
    {
      id: 'gpt-4o',
      name: 'GPT-4o',
      provider_id: 'openai',
      model_type: 'llm',
      status: 'active',
      capabilities: { features: [], supports_streaming: true, supports_tools: true },
    },
    {
      id: 'minimax-m3',
      name: 'MiniMax M3',
      provider_id: 'minimax',
      model_type: 'llm',
      status: 'active',
      capabilities: { features: [], supports_streaming: true, supports_tools: true },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.useProviders.mockReturnValue({
      providers,
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getSummary.mockResolvedValue({ data: null });
    mocks.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    mocks.getFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.setFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models, total: models.length } });
    mocks.getCredentials.mockResolvedValue({ data: null });
    mocks.testCredentials.mockResolvedValue({ data: { success: true, latency_ms: 10 } });
  });

  it('adds and explicitly saves an ordered fallback list', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    await user.click(await screen.findByRole('button', { name: 'fallbacks.add' }));
    const matchingModels = await screen.findAllByRole('button', { name: /MiniMax M3/i });
    await user.click(matchingModels[matchingModels.length - 1]);
    await user.click(screen.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.setFallbacks).toHaveBeenCalledWith([
        { provider_id: 'minimax', model_id: 'minimax-m3' },
      ]);
    });
  });

  it('blocks fallback edits until a failed fallback load is retried', async () => {
    const user = userEvent.setup();
    mocks.getFallbacks
      .mockRejectedValueOnce(new Error('fallback request failed'))
      .mockResolvedValueOnce({
        data: {
          fallback_providers: [{ provider_id: 'minimax', model_id: 'minimax-m3' }],
        },
      });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    expect(await screen.findByText('fallbacks.loadFailed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();
    expect(mocks.setFallbacks).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'fallbacks.retry' }));
    expect((await screen.findAllByText('MiniMax M3')).length).toBeGreaterThan(0);
    expect(mocks.getFallbacks).toHaveBeenCalledTimes(2);
  });

  it('blocks fallback edits until model definitions load successfully', async () => {
    const user = userEvent.setup();
    mocks.listDefinitions
      .mockResolvedValueOnce({ data: { models, total: models.length } })
      .mockResolvedValueOnce({ data: { models, total: models.length } })
      .mockRejectedValueOnce(new Error('model definitions failed'))
      .mockResolvedValueOnce({ data: { models, total: models.length } });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    expect(await screen.findByText('fallbacks.loadFailed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'fallbacks.retry' }));
    await user.click(await screen.findByRole('button', { name: 'fallbacks.add' }));
    expect((await screen.findAllByRole('button', { name: /MiniMax M3/i })).length).toBeGreaterThan(1);
  });

  it('requires invalid entries to be removed before saving', async () => {
    const user = userEvent.setup();
    mocks.getFallbacks.mockResolvedValue({
      data: {
        fallback_providers: [
          { provider_id: 'missing', model_id: 'retired-model' },
          { provider_id: 'minimax', model_id: 'minimax-m3' },
        ],
      },
    });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    expect(await screen.findByText('fallbacks.unavailable')).toBeInTheDocument();
    expect(screen.getByText('fallbacks.removeInvalidHint')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();

    await user.click(screen.getAllByRole('button', { name: 'fallbacks.remove' })[0]);
    expect(screen.getByRole('button', { name: 'fallbacks.save' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.setFallbacks).toHaveBeenCalledWith([
        { provider_id: 'minimax', model_id: 'minimax-m3' },
      ]);
    });
  });

  it('does not count a fallback from an unconfigured provider as available', async () => {
    const user = userEvent.setup();
    mocks.useProviders.mockReturnValue({
      providers: [providers[0], { ...providers[1], configured: false }],
      connectedIds: ['openai'],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getFallbacks.mockResolvedValue({
      data: {
        fallback_providers: [{ provider_id: 'minimax', model_id: 'minimax-m3' }],
      },
    });
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('0 / 1 available')).toBeInTheDocument();
    await user.click(screen.getByTitle('dashboard.editFallbackModels'));
    expect(await screen.findByText('fallbacks.unavailable')).toBeInTheDocument();
  });

  it('allows an unconfigured provider model to be saved for later repair', async () => {
    const user = userEvent.setup();
    mocks.useProviders.mockReturnValue({
      providers: [providers[0], { ...providers[1], configured: false }],
      connectedIds: ['openai'],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    await user.click(await screen.findByRole('button', { name: 'fallbacks.add' }));
    const matchingModels = await screen.findAllByRole('button', { name: /MiniMax M3/i });
    await user.click(matchingModels[matchingModels.length - 1]);
    expect(screen.getByText('fallbacks.unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'fallbacks.save' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.setFallbacks).toHaveBeenCalledWith([
        { provider_id: 'minimax', model_id: 'minimax-m3' },
      ]);
    });
  });
});
