import React from 'react';
import { screen, waitFor, within } from '@testing-library/react';
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
      if (key === 'modelSelection.info') {
        return 'modelSelection.info';
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

  it('does not test provider credentials when the page loads', async () => {
    renderWithRouter(<ModelPage />);

    await waitFor(() => {
      expect(mocks.getCredentials).toHaveBeenCalledWith('openai');
    });
    expect(mocks.testCredentials).not.toHaveBeenCalled();
  });

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
    expect(mocks.testCredentials).not.toHaveBeenCalled();
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

describe('ModelPage default model selector', () => {
  const providers = [
    {
      id: 'openai',
      name: 'OpenAI Gateway',
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
      name: 'MiniMax Cloud',
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
      name: 'MiniMax Vision M3',
      provider_id: 'minimax',
      model_type: 'llm',
      status: 'active',
      capabilities: {
        features: [],
        supports_streaming: true,
        supports_tools: true,
        supports_vision: false,
        modalities: { input: ['text', 'image'], output: ['text'] },
      },
      limits: { context_window: 200000, max_output_tokens: 8192 },
      pricing: { input: 1.25, output: 5, unit: 1000000, currency: 'USD' },
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
    mocks.listDefinitions.mockResolvedValue({ data: { models, total: models.length } });
    mocks.getCredentials.mockResolvedValue({ data: null });
    mocks.testCredentials.mockResolvedValue({ data: { success: true, latency_ms: 10 } });
  });

  it('shows provider groups, model identity, vision, and details', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('dashboard.setDefaultModel'));
    const heading = await screen.findByRole('heading', { name: 'dashboard.setDefaultModel' });
    const selector = within(heading.parentElement?.parentElement as HTMLElement);

    expect(selector.getByText('MiniMax Cloud')).toBeInTheDocument();
    expect(selector.getByText('MiniMax Vision M3')).toBeInTheDocument();
    expect(selector.getByText('minimax-m3')).toBeInTheDocument();
    expect(selector.getByText('form.vision')).toBeInTheDocument();
    expect(selector.getByRole('button', {
      name: 'modelSelection.info GPT-4o',
    })).toBeInTheDocument();

    await user.click(selector.getByRole('button', {
      name: 'modelSelection.info MiniMax Vision M3',
    }));

    const tooltip = await screen.findByRole('tooltip');
    expect(within(tooltip).getByText('form.modelId')).toBeInTheDocument();
    expect(within(tooltip).getByText('form.contextWindow')).toBeInTheDocument();
    expect(within(tooltip).getByText('form.pricing')).toBeInTheDocument();
    expect(tooltip).toHaveTextContent('minimax-m3');
    expect(tooltip).toHaveTextContent(/200(?:K|,?000)/);
    expect(tooltip).toHaveTextContent(/1\.25/);
    expect(tooltip).toHaveTextContent(/\b5(?:\.0+)?\b/);
    expect(tooltip).toHaveTextContent(/USD|\$/);
  });
});
