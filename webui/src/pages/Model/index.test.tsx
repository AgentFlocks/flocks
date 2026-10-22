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

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: mocks.authUser }),
}));

beforeEach(() => {
  mocks.authUser = { role: 'admin' };
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
            default_base_url: 'https://llm.threatbook.io/v1',
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
    await user.click(await screen.findByRole('button', { name: 'Select Provider...' }));

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
        skip_catalog_bootstrap: true,
      });
    });
  });

  it('discovers with draft credentials and adds selected models only after creating the provider', async () => {
    const user = userEvent.setup();
    mocks.createProvider.mockResolvedValue({ data: { id: 'custom-my-api' } });
    // The generic type may already own this ID; a new instance is independent.
    mocks.discoverModels.mockResolvedValue({ data: {
      source: 'api', status: 'success', code: 'models_discovered', message: 'Ready',
      fetched_at: '2026-10-21T08:00:00Z', truncated: false,
      models: [{ id: 'remote/chat-model', name: 'Remote Chat', already_added: true, selectable: true }],
    } });
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: 'Select Provider...' }));
    await user.click(await screen.findByRole('button', { name: /OpenAI Compatible/i }));
    await user.type(screen.getByPlaceholderText('e.g. SiliconFlow, LM Studio, My API'), 'My API');
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    await user.click(await screen.findByRole('checkbox', { name: 'remote/chat-model' }));
    expect(mocks.createProvider).not.toHaveBeenCalled();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    expect(mocks.importModels).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocks.importModels).toHaveBeenCalledWith('custom-my-api', [expect.objectContaining({ id: 'remote/chat-model' })]));
    expect(mocks.createProvider).toHaveBeenCalledWith(expect.objectContaining({ skip_catalog_bootstrap: true }));
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
    expect(mocks.deleteDefault).not.toHaveBeenCalled();
    expect(await screen.findByText('discovery.importSummary')).toBeInTheDocument();
  });

  it('saves a new provider with an explicit empty inventory and does not run an empty connection check', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: 'Select Provider...' }));
    await user.click(await screen.findByRole('button', { name: /ThreatBook-cn-llm/ }));
    await user.type(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key'), 'draft-key');
    expect(screen.getByRole('button', { name: 'connection.saveAndCheck' })).toBeDisabled();
    expect(screen.queryByText('discovery.legacyCatalog')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalledWith('threatbook-cn-llm', expect.objectContaining({ skip_catalog_bootstrap: true })));
    expect(mocks.testCredentials).not.toHaveBeenCalled();
    expect(mocks.importModels).not.toHaveBeenCalled();
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
  });

  it('saves and imports explicitly selected new models before running a real connection check', async () => {
    const user = userEvent.setup();
    mocks.testCredentials.mockResolvedValue({ data: { success: true, message: 'Real chat succeeded' } });
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: 'Select Provider...' }));
    await user.click(await screen.findByRole('button', { name: /ThreatBook-cn-llm/ }));
    await user.type(screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key'), 'draft-key');
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    await user.click(await screen.findByRole('checkbox', { name: 'remote/chat-model' }));
    await user.click(screen.getByRole('button', { name: 'connection.saveAndCheck' }));
    await waitFor(() => expect(mocks.testCredentials).toHaveBeenCalledWith('threatbook-cn-llm', 'remote/chat-model'));
    expect(mocks.setCredentials.mock.invocationCallOrder[0]).toBeLessThan(mocks.importModels.mock.invocationCallOrder[0]);
    expect(mocks.importModels.mock.invocationCallOrder[0]).toBeLessThan(mocks.testCredentials.mock.invocationCallOrder[0]);
    expect(screen.getByText('Real chat succeeded')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();
  });

  it('retries a failed new-provider check with corrected credentials and an already imported model', async () => {
    const user = userEvent.setup();
    mocks.testCredentials
      .mockResolvedValueOnce({ data: { success: false, message: 'The original key was rejected' } })
      .mockResolvedValueOnce({ data: { success: true, message: 'Corrected credentials succeeded' } });
    renderWithRouter(<ModelPage />);
    await user.click(screen.getByRole('button', { name: 'Add Provider' }));
    await user.click(await screen.findByRole('button', { name: 'Select Provider...' }));
    await user.click(await screen.findByRole('button', { name: /ThreatBook-cn-llm/ }));
    const apiKey = screen.getByPlaceholderText('Paste ThreatBook CN LLM API Key');
    await user.type(apiKey, 'rejected-key');
    await user.click(screen.getByRole('button', { name: 'discovery.title' }));
    await user.click(await screen.findByRole('checkbox', { name: 'remote/chat-model' }));
    await user.click(screen.getByRole('button', { name: 'connection.saveAndCheck' }));
    expect(await screen.findByText('The original key was rejected')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();
    expect(mocks.importModels).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'connection.saveAndCheck' })).toBeEnabled();

    await user.clear(apiKey);
    await user.type(apiKey, 'corrected-key');
    expect(screen.queryByText('The original key was rejected')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'connection.saveAndCheck' }));
    expect(await screen.findByText('Corrected credentials succeeded')).toBeInTheDocument();
    expect(mocks.setCredentials).toHaveBeenLastCalledWith('threatbook-cn-llm', expect.objectContaining({ api_key: 'corrected-key', skip_catalog_bootstrap: true }));
    expect(mocks.testCredentials).toHaveBeenCalledTimes(2);
    expect(mocks.testCredentials).toHaveBeenLastCalledWith('threatbook-cn-llm', 'remote/chat-model');
    expect(mocks.importModels).toHaveBeenCalledTimes(1);
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
    expect(mocks.deleteDefault).not.toHaveBeenCalled();

    await user.type(screen.getByPlaceholderText('https://llm.threatbook.cn/v1'), '/changed');
    expect(screen.queryByText('Corrected credentials succeeded')).not.toBeInTheDocument();
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

  async function openAddModelDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('button', { name: 'addModel' }));
    return within(await screen.findByTestId('entity-sheet'));
  }

  it('shows credentials, connection checking and the live managed inventory in that order', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    const key = sheet.getByLabelText('API Key');
    const url = sheet.getByRole('textbox', { name: /configuration.proxyUrl/ });
    const connection = sheet.getByRole('region', { name: 'connection.title' });
    const inventory = sheet.getByRole('region', { name: 'configuration.models' });
    expect(key.compareDocumentPosition(url) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(url.compareDocumentPosition(connection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(connection.compareDocumentPosition(inventory) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sheet.getByText('1/1 enabled')).toBeInTheDocument();
    expect(sheet.getByRole('switch')).toBeChecked();
    expect(sheet.getByRole('button', { name: 'configuration.editModel' })).toBeEnabled();
    expect(sheet.getByRole('button', { name: 'configuration.deleteModel' })).toBeEnabled();
    expect(mocks.catalogList).not.toHaveBeenCalled();
  });

  it('toggles an existing model immediately without saving credential drafts', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    await user.type(sheet.getByRole('textbox', { name: /configuration.proxyUrl/ }), '/draft');
    await user.click(sheet.getByRole('switch'));
    await waitFor(() => expect(mocks.updateModelSettings).toHaveBeenCalledWith('openai', 'gpt-4o', { enabled: false }));
    expect(sheet.getByRole('switch')).not.toBeChecked();
    expect(sheet.getByText('0/1 enabled')).toBeInTheDocument();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
  });

  it('preserves unsaved credentials after an explicit model deletion refreshes parent data', async () => {
    const user = userEvent.setup();
    const confirmDelete = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    const apiKey = sheet.getByLabelText('API Key');
    const url = sheet.getByRole('textbox', { name: /configuration.proxyUrl/ });
    await user.clear(apiKey);
    await user.type(apiKey, 'sk-unsaved-draft');
    await user.clear(url);
    await user.type(url, 'https://unsaved.example.com/v1');
    mocks.listDefinitions.mockResolvedValue({ data: { models: [], total: 0 } });
    await user.click(sheet.getByRole('button', { name: 'configuration.deleteModel' }));
    await waitFor(() => expect(sheet.getByText('configuration.emptyModels')).toBeInTheDocument());
    expect(mocks.deleteDefinition).toHaveBeenCalledWith('openai', 'gpt-4o');
    expect(apiKey).toHaveValue('sk-unsaved-draft');
    expect(url).toHaveValue('https://unsaved.example.com/v1');
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    await user.click(sheet.getByRole('button', { name: 'Close sheet' }));
    expect(screen.queryByTestId('entity-sheet')).not.toBeInTheDocument();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    confirmDelete.mockRestore();
  });

  it('opens manual model creation while leaving provider credential drafts unchanged', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    await user.type(sheet.getByRole('textbox', { name: /configuration.proxyUrl/ }), '/draft');
    await user.click(sheet.getByRole('button', { name: 'configuration.addModel' }));
    expect(screen.getAllByTestId('entity-sheet')).toHaveLength(2);
    const modelSheet = within(screen.getAllByTestId('entity-sheet')[1]);
    await user.click(modelSheet.getByRole('tab', { name: 'discovery.manual' }));
    expect(modelSheet.getByText('form.modelId')).toBeVisible();
    const modelId = modelSheet.getByPlaceholderText('model-id');
    await user.type(modelId, 'manual/private-model');
    await user.click(modelSheet.getByRole('tab', { name: 'discovery.automatic' }));
    expect(modelId).not.toBeVisible();
    await user.click(modelSheet.getByRole('tab', { name: 'discovery.manual' }));
    expect(modelId).toHaveValue('manual/private-model');
    expect(mocks.discoverModels).not.toHaveBeenCalled();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    await user.click(modelSheet.getByRole('button', { name: 'Close sheet' }));
    expect(sheet.getByRole('textbox', { name: /configuration.proxyUrl/ })).toHaveValue('https://old.example.com/v1/draft');
  });

  it('reports connection failure and retries the real model request without rewriting unchanged credentials', async () => {
    const user = userEvent.setup();
    mocks.testCredentials.mockResolvedValueOnce({ data: { success: false, error: 'Proxy refused connection' } });
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    await user.click(sheet.getByRole('button', { name: 'connection.check' }));
    expect(await sheet.findByRole('alert')).toHaveTextContent('Proxy refused connection');
    await user.click(sheet.getByRole('button', { name: 'connection.retry' }));
    expect(await sheet.findByText('connection.success')).toBeInTheDocument();
    expect(mocks.testCredentials).toHaveBeenCalledTimes(2);
    expect(mocks.setCredentials).not.toHaveBeenCalled();
  });

  it('blocks model edits when settings fail to load and allows a successful retry', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const providerSheet = within(screen.getByTestId('entity-sheet'));
    mocks.getModelSettings.mockRejectedValueOnce(new Error('Settings unavailable'));
    await user.click(providerSheet.getByRole('button', { name: 'configuration.editModel' }));
    const editor = within(screen.getAllByTestId('entity-sheet')[1]);
    expect(await editor.findByRole('alert')).toHaveTextContent('configuration.settingsLoadFailed');
    expect(editor.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.click(editor.getByRole('button', { name: 'Save' }));
    expect(mocks.createDefinition).not.toHaveBeenCalled();
    expect(mocks.updateModelSettings).not.toHaveBeenCalled();
    await user.click(editor.getByRole('button', { name: 'configuration.retry' }));
    await waitFor(() => expect(editor.getByRole('button', { name: 'Save' })).toBeEnabled());
    await user.click(editor.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocks.createDefinition).toHaveBeenCalledWith('openai', expect.objectContaining({ model_id: 'gpt-4o' })));
    expect(mocks.setCredentials).not.toHaveBeenCalled();
  });

  it('offers discovery only inside adding models and imports with saved credentials without closing the dialog', async () => {
    const user = userEvent.setup();
    renderWithRouter(<ModelPage />);
    await screen.findByRole('button', { name: 'addModel' });
    expect(screen.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
    const modelSheet = await openAddModelDialog(user);
    expect(modelSheet.getByRole('button', { name: 'Done' })).toBeEnabled();
    await user.click(modelSheet.getByRole('button', { name: 'discovery.title' }));
    await user.click(await modelSheet.findByRole('checkbox', { name: 'remote/chat-model' }));
    expect(mocks.discoverModels).toHaveBeenCalledWith('openai', undefined);
    const definitionCalls = mocks.listDefinitions.mock.calls.length;
    await user.click(modelSheet.getByRole('button', { name: 'discovery.import' }));
    await waitFor(() => expect(mocks.refetch).toHaveBeenCalled());
    expect(mocks.listDefinitions.mock.calls.length).toBeGreaterThan(definitionCalls);
    expect(modelSheet.getByText('discovery.importSummary')).toBeInTheDocument();
    expect(modelSheet.getByRole('checkbox', { name: 'remote/chat-model' })).toBeChecked();
    expect(modelSheet.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    expect(mocks.createDefinition).not.toHaveBeenCalled();
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
    expect(mocks.deleteDefault).not.toHaveBeenCalled();
    await user.click(modelSheet.getByRole('button', { name: 'Done' }));
    expect(screen.queryByTestId('entity-sheet')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
    expect(mocks.createDefinition).not.toHaveBeenCalled();
  });

  it('keeps import results and added states visible across background loading, errors and SSE refreshes', async () => {
    const user = userEvent.setup();
    const loadedProviders = {
      providers: [provider], connectedIds: ['openai'], loading: false, error: null, refetch: mocks.refetch,
    };
    mocks.useProviders.mockReturnValue(loadedProviders);
    const { rerender } = renderWithRouter(<ModelPage />);
    const modelSheet = await openAddModelDialog(user);
    await user.click(modelSheet.getByRole('button', { name: 'discovery.title' }));
    await user.click(await modelSheet.findByRole('checkbox', { name: 'remote/chat-model' }));
    await user.click(modelSheet.getByRole('button', { name: 'discovery.import' }));
    expect(await screen.findByText('discovery.importSummary')).toBeInTheDocument();

    mocks.useProviders.mockReturnValue({ ...loadedProviders, loading: true });
    rerender(<ModelPage />);
    expect(screen.getByText('discovery.importSummary')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();

    mocks.useProviders.mockReturnValue({ ...loadedProviders, error: 'Temporary network failure' });
    rerender(<ModelPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('providerList.refreshFailed');
    expect(screen.getByText('discovery.importSummary')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();

    mocks.useProviders.mockReturnValue(loadedProviders);
    rerender(<ModelPage />);
    mocks.sseOptions?.onReconnect?.();
    await waitFor(() => expect(mocks.refetch.mock.calls.length).toBeGreaterThan(1));
    expect(screen.getByText('discovery.importSummary')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'remote/chat-model' })).toBeDisabled();
  });

  it('preserves an unavailable default when an administrator views and fetches models', async () => {
    const user = userEvent.setup();
    mocks.getResolved.mockResolvedValue({ data: { provider_id: 'old-provider', model_id: 'keep-my-default' } });
    renderWithRouter(<ModelPage />);
    expect(await screen.findByText('keep-my-default')).toBeInTheDocument();
    const modelSheet = await openAddModelDialog(user);
    await user.click(modelSheet.getByRole('button', { name: 'discovery.title' }));
    await modelSheet.findByRole('checkbox', { name: 'remote/chat-model' });
    expect(mocks.deleteDefault).not.toHaveBeenCalled();
    expect(screen.getByText('keep-my-default')).toBeInTheDocument();
  });

  it('saves provider credential drafts without offering discovery or importing any models', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    const baseUrl = sheet.getByPlaceholderText('https://api.example.com/v1');
    await user.clear(baseUrl);
    await user.type(baseUrl, 'https://draft.example.com/v1');
    expect(sheet.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    await user.click(sheet.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    expect(mocks.setCredentials).toHaveBeenCalledWith('openai', expect.objectContaining({ base_url: 'https://draft.example.com/v1', skip_catalog_bootstrap: true }));
    expect(mocks.discoverModels).not.toHaveBeenCalled();
    expect(mocks.importModels).not.toHaveBeenCalled();
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
  });

  it('opens automatic discovery from adding models without using unsaved provider credentials', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);
    const sheet = within(screen.getByTestId('entity-sheet'));
    await user.type(sheet.getByPlaceholderText('https://api.example.com/v1'), '/changed');
    expect(sheet.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
    await user.click(sheet.getByRole('button', { name: 'configuration.addModel' }));
    const modelSheet = within(screen.getAllByTestId('entity-sheet')[1]);
    await user.click(modelSheet.getByRole('button', { name: 'discovery.title' }));
    await modelSheet.findByRole('checkbox', { name: 'remote/chat-model' });
    expect(mocks.discoverModels).toHaveBeenCalledWith('openai', undefined);
    expect(mocks.setCredentials).not.toHaveBeenCalled();
    expect(mocks.importModels).not.toHaveBeenCalled();
    expect(mocks.deleteDefinition).not.toHaveBeenCalled();
    await user.click(modelSheet.getByRole('button', { name: 'Close sheet' }));
    expect(sheet.getByRole('textbox', { name: /configuration.proxyUrl/ })).toHaveValue('https://old.example.com/v1/changed');
    expect(mocks.setCredentials).not.toHaveBeenCalled();
  });

  it('does not test provider credentials when the page loads', async () => {
    renderWithRouter(<ModelPage />);

    await waitFor(() => {
      expect(mocks.getCredentials).toHaveBeenCalledWith('openai');
    });
    expect(mocks.testCredentials).not.toHaveBeenCalled();
  });

  it.each([
    ['threatbook-cn-llm', 'ThreatBook-cn-llm'],
    ['threatbook-io-llm', 'ThreatBook-io-llm'],
  ])('shows the shared free key link when editing %s', async (providerId, providerName) => {
    const user = userEvent.setup();
    const threatbookProvider = { ...provider, id: providerId, name: providerName };
    const threatbookModel = { ...model, provider_id: providerId };
    mocks.useProviders.mockReturnValue({
      providers: [threatbookProvider],
      connectedIds: [providerId],
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.listDefinitions.mockResolvedValue({ data: { models: [threatbookModel], total: 1 } });
    mocks.catalogList.mockResolvedValue({
      data: {
        providers: [{ id: providerId, models: [] }],
      },
    });

    renderWithRouter(<ModelPage />);
    await user.click(await screen.findByTitle('Configure'));

    expect(await screen.findByTestId('entity-sheet')).toBeInTheDocument();
    expect(mocks.revealCredentials).toHaveBeenCalledWith(providerId);
    expect(screen.getByRole('link', { name: 'form.claimFreeKey' })).toHaveAttribute(
      'href',
      'https://portal.agentflocks.com/',
    );
  });

  it('does not show the ThreatBook free key link when editing another provider', async () => {
    const user = userEvent.setup();
    await openConfigureDialog(user);

    expect(screen.queryByRole('link', { name: 'form.claimFreeKey' })).not.toBeInTheDocument();
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
    await user.click(screen.getByRole('button', { name: 'connection.saveAndCheck' }));

    await waitFor(() => expect(mocks.setCredentials).toHaveBeenCalled());
    const payload = mocks.setCredentials.mock.calls[0]?.[1];
    expect(payload).toEqual(expect.objectContaining({
      base_url: 'https://test.example.com/v1',
      skip_catalog_bootstrap: true,
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
    expect(screen.queryByRole('button', { name: 'discovery.title' })).not.toBeInTheDocument();
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
