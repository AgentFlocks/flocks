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
  getFallbacks: vi.fn(),
  setFallbacks: vi.fn(),
  listDefinitions: vi.fn(),
  createDefinition: vi.fn(),
  getModelSettings: vi.fn(),
  updateModelSettings: vi.fn(),
  catalogList: vi.fn(),
  createProvider: vi.fn(),
  getCredentials: vi.fn(),
  revealCredentials: vi.fn(),
  setCredentials: vi.fn(),
  testCredentials: vi.fn(),
  discoverModels: vi.fn(),
  importModels: vi.fn(),
  getModelInventory: vi.fn(),
  saveModelInventory: vi.fn(),
  checkKey: vi.fn(),
  deleteDefault: vi.fn(),
  deleteDefinition: vi.fn(),
  sseOptions: null as null | { onReconnect?: () => void },
  authUser: { role: 'admin' } as { role: 'admin' | 'member' },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'status.models') return `${params?.count ?? 0} models`;
      if (key === 'configuration.modelCount') return `${params?.enabled}/${params?.total} enabled`;
      if (key === 'wizard.saveAndAddModels') return `Save and add ${params?.count} models`;
      if (key === 'compact.inventorySaveFailedAfterCredentials') return `Connection information was saved, but the model list was not saved: ${params?.error}. Check and retry.`;
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
        'wizard.saveProvider': 'Save Provider',
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
        'form.save': 'Save',
        'form.configure': 'Configure',
        'form.delete': 'Delete',
        'form.providerName': 'Provider Name',
        'form.retry': 'Retry',
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

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: mocks.authUser }),
}));

beforeEach(() => {
  mocks.authUser = { role: 'admin' };
  mocks.getModelInventory.mockResolvedValue({ data: { models: [] } });
  mocks.saveModelInventory.mockResolvedValue({ data: { added: [], skipped: [], failed: [] } });
  mocks.checkKey.mockResolvedValue({ data: { status: 'valid', code: 'key_valid' } });
  mocks.discoverModels.mockResolvedValue({ data: {
    source: 'api', status: 'success', code: 'models_discovered', message: 'Ready',
    fetched_at: '2026-10-21T08:00:00Z', truncated: false,
    models: [{ id: 'remote/chat-model', name: 'Remote Chat', already_added: false, selectable: true }],
  } });
  mocks.importModels.mockResolvedValue({ data: { added: ['remote/chat-model'], skipped: [], failed: [] } });
  mocks.getModelSettings.mockResolvedValue({ data: { enabled: true, default_parameters: {} } });
  mocks.updateModelSettings.mockResolvedValue({ data: {} });
});

vi.mock('@/hooks/useSSE', () => ({
  useSSE: (options: { onReconnect?: () => void }) => {
    mocks.sseOptions = options;
  },
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
    onClose,
  }: {
    open?: boolean;
    children?: React.ReactNode;
    submitDisabled?: boolean;
    submitLabel?: React.ReactNode;
    onSubmit?: () => void;
    onClose?: () => void;
  }) => open ? (
    <div data-testid="entity-sheet">
      <div>{children}</div>
      <button type="button" aria-label="Close sheet" onClick={onClose}>Close</button>
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
    discoverModels: mocks.discoverModels,
    importModels: mocks.importModels,
    getModelInventory: mocks.getModelInventory,
    saveModelInventory: mocks.saveModelInventory,
    checkKey: mocks.checkKey,
  },
  modelV2API: {
    listDefinitions: mocks.listDefinitions,
    createDefinition: mocks.createDefinition,
    deleteDefinition: mocks.deleteDefinition,
  },
  usageAPI: {
    getSummary: mocks.getSummary,
  },
  customAPI: {
    createProvider: mocks.createProvider,
  },
  modelSettingsAPI: {
    get: mocks.getModelSettings,
    update: mocks.updateModelSettings,
  },
  catalogAPI: {
    list: mocks.catalogList,
  },
  defaultModelAPI: {
    getResolved: mocks.getResolved,
    getFallbacks: mocks.getFallbacks,
    setFallbacks: mocks.setFallbacks,
    delete: mocks.deleteDefault,
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
            id: 'threatbook-cn-llm',
            name: 'ThreatBook-cn-llm',
            description: 'ThreatBook China LLM Service',
            credential_schemas: [{
              auth_method: 'api_key',
              fields: [{
                name: 'api_key',
                label: 'API Key',
                type: 'secret',
                required: true,
                placeholder: 'Paste ThreatBook CN LLM API Key',
              }],
            }],
            env_vars: [],
            default_base_url: 'https://llm.threatbook.cn/v1',
            model_count: 1,
            models: [{
              id: 'deepseek-v4-flash-0731',
              name: 'deepseek-v4-flash-0731',
              model_type: 'llm',
              status: 'active',
              capabilities: {
                supports_tools: true,
                supports_vision: false,
                supports_reasoning: true,
                supports_streaming: true,
              },
            }],
            allow_multiple: false,
          },
          {
            id: 'threatbook-io-llm',
            name: 'ThreatBook-io-llm',
            description: 'ThreatBook International LLM Service',
            credential_schemas: [{
              auth_method: 'api_key',
              fields: [{
                name: 'api_key',
                label: 'API Key',
                type: 'secret',
                required: true,
                placeholder: 'Paste ThreatBook IO LLM API Key',
              }],
            }],
            env_vars: [],
            default_base_url: 'https://llm.threatbook.cn/v1',
            model_count: 1,
            models: [{
              id: 'deepseek-v4-flash-0731',
              name: 'deepseek-v4-flash-0731',
              model_type: 'llm',
              status: 'active',
              capabilities: {
                supports_tools: true,
                supports_vision: false,
                supports_reasoning: true,
                supports_streaming: true,
              },
            }],
            allow_multiple: false,
          },
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

  it('keeps ThreatBook CN and IO providers separate and shows a shared free key link', async () => {
    const user = userEvent.setup();

    renderWithRouter(<ModelPage />);

    await user.click(screen.getByRole('button', { name: 'Add Provider' }));

    expect(await screen.findByRole('button', { name: /ThreatBook-cn-llm/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /ThreatBook-io-llm/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /ThreatBook-cn-llm/i }));
    let keyLink = screen.getByRole('link', { name: 'form.claimFreeKey' });
    expect(keyLink).toHaveAttribute('href', 'https://portal.agentflocks.com/');

    await user.click(screen.getByRole('button', { name: /ThreatBook-cn-llm/i }));
    await user.click(screen.getByRole('button', { name: /ThreatBook-io-llm/i }));
    keyLink = screen.getByRole('link', { name: 'form.claimFreeKey' });
    expect(keyLink).toHaveAttribute('href', 'https://portal.agentflocks.com/');
  });

  it('shows searchable providers immediately and preserves a draft when returning to the same provider', async () => {
    const user = userEvent.setup(); renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    expect(await screen.findByRole('button', { name: 'OpenAI Compatible', exact: true })).toBeVisible();
    const search = screen.getByRole('searchbox', { name: 'Search providers...' });
    await user.type(search, 'International');
    expect(screen.queryByRole('button', { name: 'ThreatBook-cn-llm', exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ThreatBook-io-llm', exact: true })).toBeVisible();
    await user.clear(search);
    await user.click(screen.getByRole('button', { name: 'ThreatBook-cn-llm', exact: true }));
    await user.type(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key'), 'draft-key');
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    expect(await screen.findByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: /ThreatBook-cn-llm/ }));
    expect(screen.getByRole('button', { name: 'Save and add 1 models' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'providerPicker.back' }));
    expect(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key')).toHaveValue('draft-key');
    expect(screen.getByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: /ThreatBook-cn-llm/ }));
    await user.click(screen.getByRole('button', { name: 'ThreatBook-cn-llm', exact: true }));
    expect(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key')).toHaveValue('draft-key');
    expect(screen.getByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
  });

  it('recovers from a failed catalog request without leaving an empty selector', async () => {
    mocks.catalogList.mockRejectedValueOnce(new Error('Catalog unavailable'));
    const user = userEvent.setup(); renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('providerPicker.loadFailed');
    await user.click(screen.getByRole('button', { name: 'providerList.retry' }));
    expect(await screen.findByRole('button', { name: 'OpenAI Compatible', exact: true })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'OpenAI Compatible', exact: true }));
    expect(screen.getByPlaceholderText('e.g. SiliconFlow, LM Studio, My API')).toBeVisible();
  });

  it('reloads providers after the event stream reconnects', async () => {
    renderWithRouter(<ModelPage />);
    await waitFor(() => expect(mocks.sseOptions?.onReconnect).toEqual(expect.any(Function)));
    vi.useFakeTimers();
    try {
      // provider.updated events sent while the stream was down are not replayed.
      mocks.sseOptions!.onReconnect!();
      vi.advanceTimersByTime(600);
      expect(mocks.refetch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('blocks openai-compatible creation until Base URL is filled and submits once provided', async () => {
    const user = userEvent.setup();

    renderWithRouter(<ModelPage />);

    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    expect(await screen.findByTestId('entity-sheet')).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: /OpenAI Compatible/i }));

    expect(screen.queryByText('(optional, leave empty for default)')).not.toBeInTheDocument();

    const saveButton = screen.getByRole('button', { name: 'Save Provider' });
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
        skip_catalog_bootstrap: true,
      });
    });
  });

  async function openThreatBook(user: ReturnType<typeof userEvent.setup>) {
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: /ThreatBook-cn-llm/ }));
    await user.type(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key'), 'draft-key');
  }

  it('checks the key independently without fetching or saving any model', async () => {
    const user = userEvent.setup(); await openThreatBook(user);
    await user.click(screen.getByRole('button', { name: 'compact.check' }));
    expect(await screen.findByText('compact.keyCodes.key_valid')).toBeInTheDocument();
    expect(mocks.checkKey).toHaveBeenCalledWith('threatbook-cn-llm', { api_key: 'draft-key', base_url: 'https://llm.threatbook.cn/v1' });
    expect(mocks.discoverModels).not.toHaveBeenCalled();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    expect(mocks.importModels).not.toHaveBeenCalled();
  });

  it('uses compact switches and saves an empty selection without importing models', async () => {
    const user = userEvent.setup(); await openThreatBook(user);
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    expect(await screen.findByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    expect(screen.queryByText('form.contextWindow')).not.toBeInTheDocument();
    expect(screen.queryByText('discovery.filtersTitle')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
    await user.click(screen.getByRole('button', { name: 'Save Provider' }));
    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    expect(mocks.importModels).not.toHaveBeenCalled();
    expect(mocks.testCredentials).not.toHaveBeenCalled();
  });

  it('retries a hand-typed Azure deployment whose first import failed', async () => {
    mocks.catalogList.mockResolvedValue({ data: { providers: [{
      id: 'azure-openai', name: 'Azure OpenAI', description: 'Azure deployments',
      credential_schemas: [{ auth_method: 'api_key', fields: [{
        name: 'api_key', label: 'API Key', type: 'secret', required: true, placeholder: 'Paste Azure key',
      }] }],
      env_vars: [], default_base_url: '', model_count: 0, models: [], allow_multiple: false,
    }] } });
    mocks.importModels.mockRejectedValueOnce(new Error('upstream timeout'));
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: /Azure OpenAI/ }));
    await user.type(screen.getByPlaceholderText('Paste Azure key'), 'azure-key');
    await user.type(screen.getByPlaceholderText('https://api.example.com/v1'), 'https://res.openai.azure.com');
    await user.type(screen.getByPlaceholderText('form.azureDeploymentPlaceholder'), 'prod-gpt4');
    await user.click(screen.getByRole('button', { name: 'Save and add 1 models' }));

    await user.click(await screen.findByRole('button', { name: 'discovery.retryFailed' }));

    await waitFor(() => expect(mocks.importModels).toHaveBeenCalledTimes(2));
    expect(mocks.importModels).toHaveBeenLastCalledWith('azure-openai', [expect.objectContaining({ id: 'prod-gpt4' })]);
  });

  it('imports chosen models with Auto limits only after saving the provider', async () => {
    const user = userEvent.setup(); await openThreatBook(user);
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    await user.click(await screen.findByRole('button', { name: 'compact.editModel remote/chat-model' }));
    const editor = within(screen.getByRole('group', { name: 'compact.editModel' }));
    expect(editor.getAllByPlaceholderText('Auto')).toHaveLength(2);
    await user.click(editor.getByRole('button', { name: 'compact.apply' }));
    expect(mocks.importModels).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Save and add 1 models' }));
    await waitFor(() => expect(mocks.importModels).toHaveBeenCalledWith('threatbook-cn-llm', [expect.objectContaining({ id: 'remote/chat-model', context_window: undefined, max_output_tokens: undefined })]));
    expect(mocks.setCredentials.mock.invocationCallOrder[0]).toBeLessThan(mocks.importModels.mock.invocationCallOrder[0]);
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
    mocks.getModelInventory.mockResolvedValue({ data: { models: [{ id: 'gpt-4o', name: 'GPT-4o', enabled: true }] } });
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

  async function openAddModelDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: 'addModel' }));
    return within(await screen.findByTestId('entity-sheet'));
  }

  it('keeps key checking separate from fetching and never persists the draft during a check', async () => {
    const user = userEvent.setup(); await openConfigureDialog(user);
    await screen.findByRole('switch', { name: 'compact.enableModel gpt-4o' });
    await user.clear(screen.getByLabelText('API Key')); await user.type(screen.getByLabelText('API Key'), 'draft-key');
    await user.click(screen.getByRole('button', { name: 'compact.check' }));
    expect(await screen.findByText('compact.keyCodes.key_valid')).toBeInTheDocument();
    expect(mocks.checkKey).toHaveBeenCalledWith('openai', { api_key: 'draft-key', base_url: 'https://old.example.com/v1' });
    expect(mocks.discoverModels).not.toHaveBeenCalled(); expect(mocks.setCredentials).not.toHaveBeenCalled();
  });

  it('lets saved models be selected and unselected together and persists off without deletion', async () => {
    mocks.saveModelInventory.mockImplementation(async (_id: string, draft: { models: { enabled: boolean }[] }) => {
      const enabled = draft.models[0].enabled;
      mocks.getModelSettings.mockResolvedValue({ data: { enabled } });
      mocks.getModelInventory.mockResolvedValue({ data: { models: [{ id: 'gpt-4o', name: 'GPT-4o', enabled }] } });
      return { data: { added: [], skipped: [], failed: [] } };
    });
    const user = userEvent.setup(); await openConfigureDialog(user);
    expect(await screen.findByRole('switch', { name: 'compact.enableModel gpt-4o' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
    expect(screen.getByRole('switch', { name: 'compact.enableModel gpt-4o' })).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'compact.selectAll' }));
    expect(screen.getByRole('switch', { name: 'compact.enableModel gpt-4o' })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
    expect(mocks.saveModelInventory).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.saveModelInventory).toHaveBeenCalledWith('openai', { models: [expect.objectContaining({ id: 'gpt-4o', enabled: false })], deleted: [] }));
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
    expect(await screen.findByText('detail.addModelHint')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'detail.modelList (0)' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'GPT-4o' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'detail.batchTest' })).toBeDisabled();

    await user.click(screen.getByTitle('Configure'));
    expect(await screen.findByRole('switch', { name: 'compact.enableModel gpt-4o' })).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'compact.selectAll' }));
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    expect(await screen.findByRole('heading', { name: 'GPT-4o' })).toBeInTheDocument();
    expect(screen.queryByText('detail.addModelHint')).not.toBeInTheDocument();
  });

  it('shows only enabled models and removes their test results when the last model is switched off', async () => {
    const hiddenModel = { ...model, id: 'hidden-model', name: 'Hidden Model' };
    mocks.listDefinitions.mockResolvedValue({ data: { models: [model, hiddenModel], total: 2 } });
    mocks.getModelSettings.mockImplementation(async (_provider: string, id: string) => ({ data: { enabled: id !== 'hidden-model' } }));
    const user = userEvent.setup(); renderWithRouter(<ModelPage />);
    expect(await screen.findByRole('heading', { name: 'GPT-4o' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Hidden Model' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'detail.modelList (1)' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'detail.batchTest' }));
    expect(await screen.findByText('10ms')).toBeInTheDocument();
    expect(mocks.testCredentials).toHaveBeenCalledTimes(1);
    expect(mocks.testCredentials).toHaveBeenCalledWith('openai', 'gpt-4o');
    await user.click(screen.getByTitle('status.disabled'));
    expect(await screen.findByText('detail.addModelHint')).toBeInTheDocument();
    expect(screen.queryByText('10ms')).not.toBeInTheDocument();
    expect(screen.queryByText(/detail\.success/)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Hidden Model' })).not.toBeInTheDocument();
  });

  it('applies Auto and explicit model edits on provider save and deletes only through trash', async () => {
    const user = userEvent.setup(); await openConfigureDialog(user);
    await user.click(await screen.findByRole('button', { name: 'compact.editModel gpt-4o' }));
    const editor = within(screen.getByRole('group', { name: 'compact.editModel' }));
    expect(screen.getByRole('button', { name: 'compact.save' })).toBeDisabled();
    expect(editor.getAllByPlaceholderText('Auto')).toHaveLength(2);
    await user.type(editor.getByRole('spinbutton', { name: 'form.maxOutput' }), '8192');
    await user.click(editor.getByRole('button', { name: 'compact.apply' }));
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.saveModelInventory).toHaveBeenCalledWith('openai', { models: [expect.objectContaining({ id: 'gpt-4o', context_window: null, max_output_tokens: 8192, edited: true })], deleted: [] }));
  });

  it('saves without the key the dialog opened with, so a key rotated meanwhile is kept', async () => {
    const user = userEvent.setup(); await openConfigureDialog(user);
    await screen.findByRole('switch', { name: 'compact.enableModel gpt-4o' });
    expect(screen.getByLabelText('API Key')).toHaveValue('sk-existing-secret-1234');
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalledTimes(1));
    expect(mocks.setCredentials.mock.calls[0][1].api_key).toBeUndefined();
    expect(mocks.setCredentials).toHaveBeenCalledWith('openai', expect.objectContaining({ base_url: 'https://old.example.com/v1' }));
  });

  it('stages explicit deletion until save and keeps failures visible for retry', async () => {
    const user = userEvent.setup(); await openConfigureDialog(user);
    await user.click(await screen.findByRole('button', { name: 'compact.deleteModel gpt-4o' }));
    expect(mocks.saveModelInventory).not.toHaveBeenCalled();
    mocks.saveModelInventory.mockRejectedValueOnce(new Error('Inventory save failed'));
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Inventory save failed');
    expect(mocks.saveModelInventory).toHaveBeenLastCalledWith('openai', { models: [], deleted: ['gpt-4o'] });
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.saveModelInventory).toHaveBeenCalledTimes(2));
  });

  it.each(['key', 'url'])('retains explicit model deletions after changing the %s before saving', async (field) => {
    const user = userEvent.setup(); await openConfigureDialog(user);
    await user.click(await screen.findByRole('button', { name: 'compact.deleteModel gpt-4o' }));
    expect(screen.queryByRole('switch', { name: 'compact.enableModel gpt-4o' })).not.toBeInTheDocument();
    if (field === 'key') {
      await user.clear(screen.getByLabelText('API Key')); await user.type(screen.getByLabelText('API Key'), 'new-key');
    } else {
      await user.type(screen.getByPlaceholderText('https://api.example.com/v1'), '/');
    }
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.saveModelInventory).toHaveBeenCalledWith('openai', { models: [], deleted: ['gpt-4o'] }));
    expect(mocks.setCredentials).toHaveBeenCalledWith('openai', expect.objectContaining(field === 'key'
      ? { api_key: 'new-key' } : { base_url: 'https://old.example.com/v1/' }));
  });

  it('identifies the saved connection and failed inventory with the field error, then allows retry', async () => {
    mocks.saveModelInventory.mockRejectedValueOnce({ response: { data: {
      error: 'ValidationError', message: 'Request validation failed',
      details: [{ loc: ['body', 'models', 0, 'name'], msg: 'Invalid model name' }],
    } } });
    const user = userEvent.setup(); await openConfigureDialog(user);
    await screen.findByRole('switch', { name: 'compact.enableModel gpt-4o' });
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection information was saved, but the model list was not saved: models.0.name: Invalid model name');
    expect(mocks.setCredentials).toHaveBeenCalledTimes(1);
    expect(mocks.toast.success).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'compact.save' }));
    await waitFor(() => expect(mocks.saveModelInventory).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mocks.toast.success).toHaveBeenCalled());
  });

  it('offers an empty provider independent key checking and compact discovery', async () => {
    mocks.getModelInventory.mockResolvedValue({ data: { models: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models: [], total: 0 } });
    const user = userEvent.setup(); await openConfigureDialog(user);
    await waitFor(() => expect(screen.getByRole('button', { name: 'compact.save' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    expect(await screen.findByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    expect(mocks.setCredentials).not.toHaveBeenCalled(); expect(mocks.saveModelInventory).not.toHaveBeenCalled();
  });

  it('keeps the automatic add-model list compact and manual fields available', async () => {
    const user = userEvent.setup(); renderWithRouter(<ModelPage />);
    expect(screen.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
    const sheet = await openAddModelDialog(user);
    await user.click(await sheet.findByRole('button', { name: 'compact.refresh' }));
    expect(await sheet.findByRole('switch', { name: 'compact.enableModel remote/chat-model' })).toBeChecked();
    expect(sheet.queryByText('discovery.filtersTitle')).not.toBeInTheDocument();
    await user.click(sheet.getByRole('tab', { name: 'discovery.manual' }));
    expect(sheet.getByText('form.modelId')).toBeVisible();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
  });
});

describe('ModelPage member access', () => {
  const provider = {
    id: 'threatbook-cn-llm',
    name: 'ThreatBook-cn-llm',
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
    id: 'deepseek-v4-flash',
    name: 'deepseek-v4-flash',
    provider_id: 'threatbook-cn-llm',
    model_type: 'llm',
    status: 'active',
    capabilities: {
      features: [],
      supports_streaming: true,
      supports_tools: true,
    },
  };
  // Same body shape as the backend's HTTPException handler (flocks/server/app.py).
  const httpError = (status: number, message: string) => Object.assign(
    new Error(`Request failed with status code ${status}`),
    { response: { status, data: { error: 'HTTPException', message } } },
  );
  const forbidden = httpError(403, '仅管理员可执行该操作');

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.useProviders.mockReturnValue({
      providers: [provider],
      connectedIds: [provider.id],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getSummary.mockResolvedValue({ data: null });
    mocks.getResolved.mockResolvedValue({ data: null });
    mocks.getFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models: [model], total: 1 } });
    mocks.getModelSettings.mockResolvedValue({ data: { enabled: true } });
    mocks.createDefinition.mockResolvedValue({ data: { id: 'qwen3.8-max' } });
    // Mirror the backend: these endpoints are require_admin.
    mocks.getCredentials.mockRejectedValue(forbidden);
    mocks.revealCredentials.mockRejectedValue(forbidden);
    mocks.testCredentials.mockRejectedValue(forbidden);
  });

  it('lets members test models while every configuration change stays with admins', async () => {
    const user = userEvent.setup();
    mocks.authUser = { role: 'member' };
    mocks.testCredentials.mockResolvedValue({
      data: { success: true, message: 'ok', latency_ms: 12, answer: 'Paris' },
    });
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('deepseek-v4-flash', { selector: 'h4' })).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.getModelSettings).toHaveBeenCalledWith('threatbook-cn-llm', 'deepseek-v4-flash');
    });
    expect(screen.getByText('detail.configAdminOnly')).toBeInTheDocument();
    // Nothing that changes providers, models or the default model.
    expect(screen.queryByTitle('Configure')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add Provider' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'addModel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'discovery.checkAndFetch' })).not.toBeInTheDocument();
    expect(screen.queryByTitle('status.disabled')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Delete')).not.toBeInTheDocument();
    expect(screen.queryByTitle('dashboard.setDefaultModel')).not.toBeInTheDocument();
    expect(screen.queryByTitle('dashboard.editFallbackModels')).not.toBeInTheDocument();

    await user.click(screen.getByTitle('form.testConnection'));
    await waitFor(() => {
      expect(mocks.testCredentials).toHaveBeenCalledWith('threatbook-cn-llm', 'deepseek-v4-flash');
    });
    expect(await screen.findByText('12ms')).toBeInTheDocument();

    mocks.testCredentials.mockClear();
    await user.click(screen.getByRole('button', { name: /detail\.batchTest/ }));
    await waitFor(() => {
      expect(mocks.testCredentials).toHaveBeenCalledWith('threatbook-cn-llm', 'deepseek-v4-flash');
    });
    expect(mocks.getCredentials).not.toHaveBeenCalled();
    expect(mocks.revealCredentials).not.toHaveBeenCalled();
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('shows members the model settings read-only', async () => {
    const user = userEvent.setup();
    mocks.authUser = { role: 'member' };
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByText('deepseek-v4-flash', { selector: 'h4' }));
    const sheet = await screen.findByTestId('entity-sheet');
    expect(within(sheet).getByText('detail.configAdminOnly')).toBeInTheDocument();
    expect(within(sheet).getByRole('group')).toBeDisabled();
    const save = within(sheet).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    await user.click(save);
    expect(mocks.createDefinition).not.toHaveBeenCalled();
    expect(mocks.updateModelSettings).not.toHaveBeenCalled();
  });

  it('leaves an invalid default model for an admin to clear', async () => {
    mocks.authUser = { role: 'member' };
    mocks.getResolved.mockResolvedValue({ data: { provider_id: 'gone', model_id: 'gone-model' } });
    renderWithRouter(<ModelPage />);

    await waitFor(() => expect(mocks.toast.warning).toHaveBeenCalled());
    // Nothing was cleared, so members are not told it was.
    expect(mocks.toast.warning).toHaveBeenCalledWith('dashboard.defaultModelInvalidMember');
    expect(mocks.toast.warning).not.toHaveBeenCalledWith('dashboard.defaultModelInvalid');
    expect(mocks.deleteDefault).not.toHaveBeenCalled();
  });

  it('ignores connection failures cached while member tests were refused', async () => {
    mocks.authUser = { role: 'member' };
    // What the old page stored after a member's batch test came back 403.
    localStorage.setItem('flocks_provider_connection_cache', JSON.stringify({
      'threatbook-cn-llm': { status: 'failed', ts: Date.now() },
    }));
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('deepseek-v4-flash', { selector: 'h4' })).toBeInTheDocument();
    expect(screen.getByText('1 models')).toBeInTheDocument();
    expect(screen.queryByText('status.connectionFailed')).not.toBeInTheDocument();
    expect(localStorage.getItem('flocks_provider_connection_cache')).toBeNull();
  });

  it('still restores connection results cached by an admin test', async () => {
    localStorage.setItem('flocks_provider_connection_cache_v2', JSON.stringify({
      'threatbook-cn-llm': { status: 'failed', ts: Date.now() },
    }));
    mocks.getCredentials.mockResolvedValue({ data: null });
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('status.connectionFailed')).toBeInTheDocument();
  });

  it('tells members to ask an administrator when no provider is configured', async () => {
    mocks.authUser = { role: 'member' };
    mocks.useProviders.mockReturnValue({
      providers: [],
      connectedIds: [],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('providerList.emptyHintMember')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add First Provider' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add Provider' })).not.toBeInTheDocument();
  });

  it('tells members to ask an administrator when a provider has no models', async () => {
    mocks.authUser = { role: 'member' };
    mocks.listDefinitions.mockResolvedValue({ data: { models: [], total: 0 } });
    renderWithRouter(<ModelPage />);

    expect(await screen.findByText('detail.noModelsMember')).toBeInTheDocument();
    expect(screen.queryByText('detail.addModelHint')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'addModel' })).not.toBeInTheDocument();
  });

  it('keeps single-model and batch connection tests for admins', async () => {
    const user = userEvent.setup();
    mocks.getCredentials.mockResolvedValue({ data: null });
    mocks.testCredentials.mockResolvedValue({
      data: { success: true, message: 'ok', latency_ms: 12, answer: 'pong' },
    });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('form.testConnection'));
    await waitFor(() => {
      expect(mocks.testCredentials).toHaveBeenCalledWith('threatbook-cn-llm', 'deepseek-v4-flash');
    });
    expect(await screen.findByText('12ms')).toBeInTheDocument();

    mocks.testCredentials.mockClear();
    await user.click(screen.getByRole('button', { name: /detail\.batchTest/ }));
    await waitFor(() => {
      expect(mocks.testCredentials).toHaveBeenCalledWith('threatbook-cn-llm', 'deepseek-v4-flash');
    });
    expect(screen.queryByText('detail.configAdminOnly')).not.toBeInTheDocument();
    expect(screen.getByTitle('Configure')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'addModel' })).toBeInTheDocument();
    expect(screen.getByTitle('status.disabled')).toBeInTheDocument();
    expect(mocks.getCredentials).toHaveBeenCalledWith('threatbook-cn-llm');
  });

  it('shows the backend reason instead of the bare status code when a model test is rejected', async () => {
    const user = userEvent.setup();
    mocks.getCredentials.mockResolvedValue({ data: null });
    mocks.testCredentials.mockRejectedValue(httpError(500, 'upstream exploded'));
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('form.testConnection'));

    const badge = await screen.findByText('detail.failed', { selector: 'span' });
    expect(badge).toHaveAttribute('title', 'upstream exploded');
  });

  it('shows the backend reason when opening the provider configuration fails', async () => {
    const user = userEvent.setup();
    mocks.getCredentials.mockResolvedValue({ data: null });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByTitle('Configure'));

    await waitFor(() => {
      expect(mocks.toast.error).toHaveBeenCalledWith('configFailed', '仅管理员可执行该操作');
    });
    expect(screen.queryByTestId('entity-sheet')).not.toBeInTheDocument();
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
    {
      id: 'disconnected',
      name: 'Disconnected Provider',
      source: 'config',
      env: [],
      key: null,
      options: {},
      models: {},
      configured: false,
      modelCount: 1,
      category: 'available',
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
      pricing: { input: 1.25, output: 5, cache_read: 0.25, unit: 1000000, currency: 'USD' },
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
    mocks.createDefinition.mockResolvedValue({ data: {} });
    mocks.getModelSettings.mockResolvedValue({
      data: { enabled: true, default_parameters: {} },
    });
    mocks.updateModelSettings.mockResolvedValue({ data: {} });
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
    expect(tooltip).toHaveTextContent('$1.25/$5/$0.25/M');
    expect(tooltip).toHaveTextContent(/USD|\$/);
  });

  it('shows and saves cache-read pricing in model details', async () => {
    const user = userEvent.setup();
    mocks.listDefinitions.mockResolvedValue({
      data: {
        models: [
          models[0],
          {
            ...models[1],
            pricing: {
              input: 1,
              output: 2,
              cache_read: 0.2,
              unit: 1000000,
              currency: 'CNY',
            },
          },
        ],
        total: models.length,
      },
    });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByText('MiniMax Vision M3'));
    const cacheReadLabel = await screen.findByText('form.cacheRead');
    const cacheReadInput = cacheReadLabel.parentElement?.querySelector('input');
    const inputPrice = screen.getByText('form.input').parentElement?.querySelector('input');
    const outputPrice = screen.getByText('form.output').parentElement?.querySelector('input');
    const currencySelect = screen.getByText('form.currency').parentElement?.querySelector('select');
    expect(cacheReadInput).toHaveValue(0.2);

    await user.selectOptions(currencySelect as HTMLSelectElement, 'USD');

    expect(inputPrice).toHaveValue(0.142857);
    expect(outputPrice).toHaveValue(0.285714);
    expect(cacheReadInput).toHaveValue(0.028571);

    await user.selectOptions(currencySelect as HTMLSelectElement, 'CNY');
    expect(inputPrice).toHaveValue(1);
    expect(outputPrice).toHaveValue(2);
    expect(cacheReadInput).toHaveValue(0.2);

    await user.selectOptions(currencySelect as HTMLSelectElement, 'USD');

    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(mocks.createDefinition).toHaveBeenCalledWith(
        'minimax',
        expect.objectContaining({
          input_price: 0.142857,
          output_price: 0.285714,
          cache_read_price: 0.028571,
          currency: 'USD',
        }),
      );
    });
  });

  it('keeps prices unchanged when the source currency is unsupported', async () => {
    const user = userEvent.setup();
    mocks.listDefinitions.mockResolvedValue({
      data: {
        models: [
          models[0],
          {
            ...models[1],
            pricing: {
              input: 1,
              output: 2,
              cache_read: 0.2,
              unit: 1000000,
              currency: 'EUR',
            },
          },
        ],
        total: models.length,
      },
    });
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByText('MiniMax Vision M3'));
    const inputPrice = screen.getByText('form.input').parentElement?.querySelector('input');
    const outputPrice = screen.getByText('form.output').parentElement?.querySelector('input');
    const cacheReadPrice = screen.getByText('form.cacheRead').parentElement?.querySelector('input');
    const currencySelect = screen.getByText('form.currency').parentElement?.querySelector('select');

    expect(currencySelect).toHaveValue('EUR');
    await user.selectOptions(currencySelect as HTMLSelectElement, 'CNY');

    expect(currencySelect).toHaveValue('EUR');
    expect(inputPrice).toHaveValue(1);
    expect(outputPrice).toHaveValue(2);
    expect(cacheReadPrice).toHaveValue(0.2);
  });

  it('sends null when the cache-read price is cleared', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);

    await user.click(await screen.findByText('MiniMax Vision M3'));
    const cacheReadPrice = screen.getByText('form.cacheRead').parentElement?.querySelector('input');
    await user.clear(cacheReadPrice as HTMLInputElement);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(mocks.createDefinition).toHaveBeenCalledWith(
        'minimax',
        expect.objectContaining({ cache_read_price: null }),
      );
    });
  });
});

describe('ModelPage auxiliary model editor', () => {
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
      modelCount: 2,
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
      id: 'unconfigured-model',
      name: 'Unconfigured Model',
      provider_id: 'disconnected',
      model_type: 'llm',
      status: 'active',
      capabilities: { features: [], supports_streaming: true, supports_tools: true },
    },
    {
      id: 'gpt-4o-mini',
      name: 'GPT-4o Mini',
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
    mocks.getResolved.mockResolvedValue({
      data: { provider_id: 'openai', model_id: 'gpt-4o' },
    });
    mocks.getFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.setFallbacks.mockResolvedValue({ data: { fallback_providers: [] } });
    mocks.listDefinitions.mockResolvedValue({ data: { models, total: models.length } });
    mocks.getCredentials.mockResolvedValue({ data: null });
    mocks.testCredentials.mockResolvedValue({ data: { success: true, latency_ms: 10 } });
  });

  async function openEditor(user: ReturnType<typeof userEvent.setup>) {
    renderWithRouter(<ModelPage />);
    await user.click(await screen.findByTitle('dashboard.editFallbackModels'));
    return screen.findByRole('dialog', { name: 'fallbacks.title' });
  }

  it('explains automatic routing when no auxiliary models are configured', async () => {
    const user = userEvent.setup();
    const dialog = await openEditor(user);

    expect(within(dialog).getByText('fallbacks.empty')).toBeInTheDocument();
    expect(within(dialog).getByText('fallbacks.emptyHint')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();
  });

  it('adds and saves an auxiliary model without offering the primary model', async () => {
    const user = userEvent.setup();
    const dialog = await openEditor(user);
    const editor = within(dialog);

    await user.click(editor.getByRole('button', { name: 'fallbacks.add' }));
    expect(editor.queryByRole('button', { name: /GPT-4o gpt-4o$/ })).not.toBeInTheDocument();
    expect(editor.queryByRole('button', { name: /Unconfigured Model/ })).not.toBeInTheDocument();
    await user.click(editor.getByRole('button', { name: /MiniMax M3 minimax-m3/ }));
    await user.click(editor.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.setFallbacks).toHaveBeenCalledWith([
        { provider_id: 'minimax', model_id: 'minimax-m3' },
      ]);
    });
  });

  it('reorders configured auxiliary models before saving', async () => {
    mocks.getFallbacks.mockResolvedValue({
      data: {
        fallback_providers: [
          { provider_id: 'minimax', model_id: 'minimax-m3' },
          { provider_id: 'openai', model_id: 'gpt-4o-mini' },
        ],
      },
    });
    const user = userEvent.setup();
    const dialog = await openEditor(user);
    const editor = within(dialog);

    await user.click(editor.getAllByRole('button', { name: 'fallbacks.moveDown' })[0]);
    await user.click(editor.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.setFallbacks).toHaveBeenCalledWith([
        { provider_id: 'openai', model_id: 'gpt-4o-mini' },
        { provider_id: 'minimax', model_id: 'minimax-m3' },
      ]);
    });
  });

  it('keeps the editor open and reports save failures', async () => {
    mocks.setFallbacks.mockRejectedValueOnce(new Error('configuration is read-only'));
    const user = userEvent.setup();
    const dialog = await openEditor(user);
    const editor = within(dialog);

    await user.click(editor.getByRole('button', { name: 'fallbacks.add' }));
    await user.click(editor.getByRole('button', { name: /MiniMax M3 minimax-m3/ }));
    await user.click(editor.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => {
      expect(mocks.toast.error).toHaveBeenCalledWith(
        'operationFailed',
        'configuration is read-only',
      );
    });
    expect(screen.getByRole('dialog', { name: 'fallbacks.title' })).toBeInTheDocument();
  });

  it('blocks saving stale entries until they are removed, then clears the chain', async () => {
    mocks.getFallbacks.mockResolvedValue({
      data: {
        fallback_providers: [
          { provider_id: 'retired', model_id: 'retired-model' },
        ],
      },
    });
    const user = userEvent.setup();
    const dialog = await openEditor(user);
    const editor = within(dialog);

    expect(editor.getByText('fallbacks.unavailable')).toBeInTheDocument();
    expect(editor.getByText('fallbacks.removeInvalidHint')).toBeInTheDocument();
    expect(editor.getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();

    await user.click(editor.getByRole('button', { name: 'fallbacks.remove' }));
    await user.click(editor.getByRole('button', { name: 'fallbacks.save' }));

    await waitFor(() => expect(mocks.setFallbacks).toHaveBeenCalledWith([]));
  });

  it('blocks edits until a failed configuration load is retried', async () => {
    mocks.getFallbacks
      .mockRejectedValueOnce(new Error('auxiliary request failed'))
      .mockResolvedValueOnce({ data: { fallback_providers: [] } });
    const user = userEvent.setup();
    const dialog = await openEditor(user);
    const editor = within(dialog);

    expect(await editor.findByText('fallbacks.loadFailed')).toBeInTheDocument();
    expect(editor.getByRole('button', { name: 'fallbacks.save' })).toBeDisabled();
    await user.click(editor.getByRole('button', { name: 'fallbacks.retry' }));

    await waitFor(() => expect(mocks.getFallbacks).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      expect(editor.queryByText('fallbacks.loadFailed')).not.toBeInTheDocument();
    });
  });
});
