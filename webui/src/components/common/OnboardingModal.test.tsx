import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import OnboardingModal from './OnboardingModal';
import zhCNCommon from '@/locales/zh-CN/common.json';

const {
  catalogAPI,
  clientPost,
  currentLanguage,
  defaultModelAPI,
  mcpAPI,
  onboardingAPI,
  providerAPI,
  sessionApi,
} = vi.hoisted(() => ({
  catalogAPI: {
    list: vi.fn(),
  },
  clientPost: vi.fn(),
  currentLanguage: {
    value: 'zh-CN',
  },
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  mcpAPI: {
    revealCredentials: vi.fn(),
  },
  onboardingAPI: {
    getStatus: vi.fn(),
    getDraft: vi.fn(),
    saveDraft: vi.fn(),
    validate: vi.fn(),
    apply: vi.fn(),
  },
  providerAPI: {
    getCredentials: vi.fn(),
    revealCredentials: vi.fn(),
    revealServiceCredentials: vi.fn(),
  },
  sessionApi: {
    create: vi.fn(),
  },
}));

vi.mock('@/api/provider', () => ({
  catalogAPI,
  defaultModelAPI,
  providerAPI,
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI,
}));

vi.mock('@/api/onboarding', () => ({
  onboardingAPI,
}));

vi.mock('@/api/session', () => ({
  sessionApi,
}));

vi.mock('@/api/client', () => ({
  default: {
    post: clientPost,
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: currentLanguage.value },
  }),
}));

function makeProvider(id: string, name: string, models: Array<{ id: string; name: string }>) {
  return {
    id,
    name,
    description: null,
    credential_schemas: [
      {
        auth_method: 'api_key',
        fields: [
          {
            name: 'api_key',
            label: 'API Key',
            type: 'secret' as const,
            required: true,
            placeholder: '',
          },
        ],
      },
    ],
    env_vars: [],
    default_base_url: null,
    model_count: models.length,
    models: models.map((model) => ({
      ...model,
      model_type: 'llm',
      status: 'active',
      capabilities: {
        supports_tools: true,
        supports_vision: false,
        supports_reasoning: true,
        supports_streaming: true,
      },
    })),
  };
}

function makeStatus(overrides: Record<string, any> = {}) {
  return {
    completed: false,
    dismissed: false,
    has_default_model: false,
    default_model: null,
    threatbook_intel: {
      configured: false,
      region: null,
      api_configured: false,
      api_service_id: 'threatbook-cn',
      mcp_configured: false,
      mcp_connected: false,
      mcp_status: 'not_configured',
      mcp_name: 'threatbook_mcp',
      service_matrix: {
        cn: ['api', 'mcp'],
        global: ['api'],
      },
    },
    ...overrides,
  };
}

function makeDraft(overrides: Record<string, any> = {}) {
  return {
    model: null,
    intel: null,
    model_has_key: false,
    intel_has_key: false,
    dismissed: false,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((fulfill) => { resolve = fulfill; });
  return { promise, resolve };
}

function makeValidation(resourceResults: Record<string, any> = {}) {
  return {
    success: true,
    can_apply: true,
    threatbook_enabled: true,
    threatbook_key_valid: true,
    threatbook_region_match: true,
    suggested_region: null,
    error_code: null,
    message: 'ok',
    threatbook_resources: Object.keys(resourceResults),
    third_party_llm_valid: null,
    resource_results: resourceResults,
  };
}

function RouteProbe() {
  const location = useLocation();
  return <div data-testid="onboarding-route">{location.pathname}</div>;
}

function renderOnboarding(onClose = vi.fn()) {
  return render(
    <MemoryRouter>
      <OnboardingModal onClose={onClose} />
      <RouteProbe />
    </MemoryRouter>,
  );
}

describe('OnboardingModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentLanguage.value = 'zh-CN';
    defaultModelAPI.getResolved.mockRejectedValue(new Error('no default model'));
    providerAPI.getCredentials.mockResolvedValue({ data: { has_credential: false } });
    providerAPI.revealCredentials.mockResolvedValue({
      data: { has_credential: true, api_key: 'saved-model-key' },
    });
    providerAPI.revealServiceCredentials.mockResolvedValue({
      data: { has_credential: true, api_key: 'saved-intel-key' },
    });
    mcpAPI.revealCredentials.mockResolvedValue({
      data: { api_key: 'saved-intel-mcp-key' },
    });
    onboardingAPI.getStatus.mockResolvedValue({
      data: makeStatus(),
    });
    onboardingAPI.getDraft.mockResolvedValue({ data: makeDraft() });
    onboardingAPI.saveDraft.mockResolvedValue({ data: makeDraft() });
    catalogAPI.list.mockResolvedValue({
      data: {
        providers: [
          makeProvider('threatbook-cn-llm', 'ThreatBook CN', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
          ]),
          makeProvider('threatbook-io-llm', 'ThreatBook Global', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
          ]),
          makeProvider('openai-compatible', 'OpenAI Compatible', []),
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' }]),
        ],
      },
    });
    onboardingAPI.validate.mockResolvedValue({
      data: makeValidation({
        threatbook_llm: {
          enabled: true,
          success: true,
          code: null,
          message: 'model ok',
          details: {},
        },
      }),
    });
    onboardingAPI.apply.mockResolvedValue({
      data: {
        success: true,
        message: 'saved',
        region: 'cn',
        threatbook_enabled: true,
        configured: ['threatbook_llm', 'default_llm'],
        skipped: [],
        default_model: {
          provider_id: 'threatbook-cn-llm',
          model_id: 'minimax-m2.7',
        },
      },
    });
    sessionApi.create.mockResolvedValue({ id: 'session-1' });
    clientPost.mockResolvedValue({ data: {} });
  });

  it('merges China and Global ThreatBook providers into one free model option', async () => {
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });

    expect(screen.getByRole('option', { name: 'onboarding.bootstrap.providerThreatBookFree' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ThreatBook CN' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ThreatBook Global' })).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'OpenAI Compatible' })).toBeInTheDocument();

    const keyLink = screen.getByRole('link', { name: 'onboarding.bootstrap.modelKeyLink' });
    expect(keyLink).toHaveAttribute('href', 'https://portal.agentflocks.com');
    expect(screen.queryByText('onboarding.bootstrap.modelRegionTitle')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.regionChina' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.regionGlobal' })).not.toBeInTheDocument();
  });

  it('dismisses empty onboarding without requiring model validation', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderOnboarding(onClose);

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({ dismissed: true }));
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
    expect(onboardingAPI.apply).not.toHaveBeenCalled();
  });

  it('saves a partially entered provider and key before dismissing onboarding', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderOnboarding(onClose);

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.selectOptions(screen.getByRole('combobox'), 'openai-compatible');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'), 'unfinished-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      dismissed: true,
      model: expect.objectContaining({
        use_threatbook_model: false,
        third_party_llm: expect.objectContaining({
          provider_id: 'openai-compatible',
          api_key: 'unfinished-key',
          model_id: '',
        }),
      }),
    }));
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
  });

  it('retains entered credentials and allows retry when dismiss draft saving fails', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    onboardingAPI.saveDraft.mockRejectedValueOnce(new Error('Network unavailable'));
    renderOnboarding(onClose);

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.selectOptions(screen.getByRole('combobox'), 'openai-compatible');
    const key = screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder');
    await user.type(key, 'retry-this-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' }));

    expect(await screen.findByText('onboarding.bootstrap.saveError')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(key).toHaveValue('retry-this-key');
    expect(screen.getByRole('combobox')).toHaveValue('openai-compatible');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenLastCalledWith(expect.objectContaining({
      dismissed: true,
      model: expect.objectContaining({
        third_party_llm: expect.objectContaining({ provider_id: 'openai-compatible', api_key: 'retry-this-key' }),
      }),
    }));
  });

  it('prevents input changes and duplicate actions while the next-step draft save is pending', async () => {
    const user = userEvent.setup();
    const save = deferred<{ data: ReturnType<typeof makeDraft> }>();
    onboardingAPI.saveDraft.mockReturnValueOnce(save.promise);
    renderOnboarding();

    const key = await screen.findByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder');
    await user.type(key, 'pending-model-key');
    const next = screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' });
    await user.click(next);

    expect(key).toBeDisabled();
    expect(next).toBeDisabled();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'close' })).toBeDisabled();
    await user.click(next);
    expect(onboardingAPI.saveDraft).toHaveBeenCalledOnce();

    await act(async () => { save.resolve({ data: makeDraft() }); });
    expect(await screen.findByText('onboarding.bootstrap.intelPageTitle')).toBeInTheDocument();
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({ threatbook_api_key: 'pending-model-key' }),
    }));
  });

  it('saves the selected provider and key when advancing without running validation', async () => {
    const user = userEvent.setup();
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.selectOptions(screen.getByRole('combobox'), 'deepseek');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'), 'possibly-invalid-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    expect(await screen.findByText('onboarding.bootstrap.intelPageTitle')).toBeInTheDocument();
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({
        use_threatbook_model: false,
        third_party_llm: expect.objectContaining({
          provider_id: 'deepseek',
          model_id: 'deepseek-v4-flash',
          api_key: 'possibly-invalid-key',
        }),
      }),
    }));
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
    expect(onboardingAPI.apply).not.toHaveBeenCalled();
  });

  it.each(['invalid-key', 'network-error'])('saves the provider and key before a %s validation failure', async (failure) => {
    const user = userEvent.setup();
    if (failure === 'network-error') {
      onboardingAPI.validate.mockRejectedValueOnce(new Error('Network unavailable'));
    } else {
      onboardingAPI.validate.mockResolvedValueOnce({
        data: {
          ...makeValidation(),
          success: false,
          can_apply: false,
          third_party_llm_valid: false,
          message: 'Invalid API key',
        },
      });
    }
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.selectOptions(screen.getByRole('combobox'), 'deepseek');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'), 'invalid-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await waitFor(() => expect(onboardingAPI.validate).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({
        third_party_llm: expect.objectContaining({
          provider_id: 'deepseek',
          api_key: 'invalid-key',
        }),
      }),
    }));
    expect(onboardingAPI.saveDraft.mock.invocationCallOrder[0]).toBeLessThan(onboardingAPI.validate.mock.invocationCallOrder[0]);
    await waitFor(() => expect(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).toBeEnabled());
    expect(onboardingAPI.apply).not.toHaveBeenCalled();
  });

  it('saves both steps when dismissing from intelligence setup', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderOnboarding(onClose);

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'model-draft-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    await user.type(await screen.findByPlaceholderText('onboarding.bootstrap.intelKeyPlaceholder'), 'intel-draft-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.dontShowAgain' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenLastCalledWith(expect.objectContaining({
      dismissed: true,
      model: expect.objectContaining({ threatbook_api_key: 'model-draft-key' }),
      intel: expect.objectContaining({ threatbook_api_key: 'intel-draft-key', threatbook_services_only: true }),
    }));
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
  });

  it('saves current input and closes onboarding when opening the model list', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderOnboarding(onClose);

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    expect(screen.getByText('onboarding.bootstrap.modelListHint')).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'configure-later-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.openModelList' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({ threatbook_api_key: 'configure-later-key' }),
    }));
    expect(screen.getByTestId('onboarding-route')).toHaveTextContent('/models');
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
  });

  it('restores the draft provider, model and masked key on reopening', async () => {
    onboardingAPI.getDraft.mockResolvedValue({
      data: makeDraft({
        dismissed: true,
        model_has_key: true,
        model: {
          region: 'cn',
          use_threatbook_model: false,
          third_party_llm: {
            provider_id: 'openai-compatible',
            provider_name: 'OpenAI Compatible',
            api_key: '',
            model_id: 'my-draft-model',
          },
        },
      }),
    });
    renderOnboarding();

    const key = await screen.findByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder');
    expect(screen.getByRole('combobox')).toHaveValue('openai-compatible');
    expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyModelIdPlaceholder')).toHaveValue('my-draft-model');
    expect(key).toHaveValue('************');
    expect(key).toHaveAttribute('type', 'password');
    expect(key).toHaveAttribute('readonly');
  });

  it('waits for the catalog and draft before allowing input or advancing', async () => {
    const user = userEvent.setup();
    const catalog = deferred<{ data: { providers: ReturnType<typeof makeProvider>[] } }>();
    const draft = deferred<{ data: ReturnType<typeof makeDraft> }>();
    catalogAPI.list.mockReturnValueOnce(catalog.promise);
    onboardingAPI.getDraft.mockReturnValueOnce(draft.promise);
    renderOnboarding();

    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledOnce());
    const nextButton = screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' });
    expect(nextButton).toBeDisabled();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder')).not.toBeInTheDocument();
    expect(onboardingAPI.getDraft).not.toHaveBeenCalled();
    await user.click(nextButton);
    expect(onboardingAPI.saveDraft).not.toHaveBeenCalled();

    await act(async () => {
      catalog.resolve({ data: { providers: [makeProvider('openai-compatible', 'OpenAI Compatible', [])] } });
    });
    expect(onboardingAPI.getDraft).toHaveBeenCalledOnce();
    expect(nextButton).toBeDisabled();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder')).not.toBeInTheDocument();

    await act(async () => {
      draft.resolve({
        data: makeDraft({
          model_has_key: true,
          model: {
            region: 'cn',
            use_threatbook_model: false,
            third_party_llm: { provider_id: 'openai-compatible', model_id: 'restored-model', api_key: '' },
          },
        }),
      });
    });
    expect(nextButton).toBeEnabled();
    expect(screen.getByRole('combobox')).toHaveValue('openai-compatible');
    expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyModelIdPlaceholder')).toHaveValue('restored-model');
    expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder')).toHaveValue('************');
  });

  it.each([
    ['unconfigured', 'reveal'],
    ['unconfigured', 'validate'],
    ['configured-in-cn', 'reveal'],
    ['configured-in-cn', 'validate'],
  ])('uses the global draft intelligence key with %s runtime status when clicking %s', async (runtime, action) => {
    const user = userEvent.setup();
    const runtimeStatus = makeStatus();
    if (runtime === 'configured-in-cn') {
      Object.assign(runtimeStatus.threatbook_intel, { configured: true, region: 'cn', api_configured: true });
    }
    onboardingAPI.getStatus.mockResolvedValue({ data: runtimeStatus });
    onboardingAPI.getDraft.mockResolvedValue({
      data: makeDraft({
        intel_has_key: true,
        intel: { region: 'global', use_threatbook_model: false, threatbook_api_key: null, threatbook_services_only: true },
      }),
    });
    providerAPI.revealServiceCredentials.mockResolvedValue({ data: { has_credential: true, api_key: 'global-draft-key' } });
    onboardingAPI.validate.mockResolvedValue({
      data: { ...makeValidation(), success: false, can_apply: false, threatbook_key_valid: false },
    });
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    const key = await screen.findByPlaceholderText('onboarding.bootstrap.intelKeyPlaceholder');
    expect(key).toHaveValue('************');
    expect(screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' })).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');

    if (action === 'reveal') {
      await user.click(screen.getByTitle('onboarding.bootstrap.showKey'));
      await waitFor(() => expect(key).toHaveValue('global-draft-key'));
      expect(key).toHaveAttribute('type', 'text');
    } else {
      await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.saveIntel' }));
      await waitFor(() => expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({
        region: 'global',
        threatbook_api_key: 'global-draft-key',
        threatbook_services_only: true,
      })));
    }
    expect(providerAPI.revealServiceCredentials).toHaveBeenCalledOnce();
    expect(providerAPI.revealServiceCredentials).toHaveBeenCalledWith('threatbook-io');
    expect(mcpAPI.revealCredentials).not.toHaveBeenCalled();
  });

  it('keeps an unverified model draft after intelligence setup refreshes runtime status', async () => {
    const user = userEvent.setup();
    const modelStatus = {
      completed: true,
      has_default_model: true,
      default_model: { provider_id: 'threatbook-cn-llm', model_id: 'minimax-m2.7' },
    };
    const refreshedStatus = makeStatus(modelStatus);
    Object.assign(refreshedStatus.threatbook_intel, { configured: true, region: 'cn', api_configured: true });
    onboardingAPI.getStatus
      .mockResolvedValueOnce({ data: makeStatus(modelStatus) })
      .mockResolvedValueOnce({ data: refreshedStatus });
    renderOnboarding();

    await user.click(await screen.findByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));
    await user.selectOptions(screen.getByRole('combobox'), 'deepseek');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'), 'unverified-model-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    await user.type(await screen.findByPlaceholderText('onboarding.bootstrap.intelKeyPlaceholder'), 'verified-intel-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.saveIntel' }));

    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledTimes(2));
    await screen.findByText('onboarding.bootstrap.intelConfiguredHint');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.previousStep' }));
    expect(screen.getAllByRole('combobox')[0]).toHaveValue('deepseek');
    expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder')).toHaveValue('unverified-model-key');
    expect(screen.getAllByRole('combobox')[1]).toHaveValue('deepseek-v4-flash');
    expect(onboardingAPI.validate).toHaveBeenCalledOnce();
    expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({ threatbook_services_only: true }));
  });

  it('uses an Azure deployment name for both draft saving and model validation', async () => {
    const user = userEvent.setup();
    const azureProvider = makeProvider('azure', 'Azure OpenAI', [{ id: 'gpt-4o', name: 'GPT-4o' }]);
    azureProvider.credential_schemas[0].fields.push({
      name: 'base_url',
      label: 'Endpoint',
      type: 'secret',
      required: true,
      placeholder: '',
    });
    catalogAPI.list.mockResolvedValue({ data: { providers: [azureProvider] } });
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.selectOptions(screen.getByRole('combobox'), 'azure');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.azureDeploymentPlaceholder'), '  production-chat-deployment  ');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder'), 'https://my-resource.openai.azure.com');
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'), 'azure-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    const expectedModel = expect.objectContaining({
      provider_id: 'azure',
      api_key: 'azure-key',
      model_id: 'production-chat-deployment',
      base_url: 'https://my-resource.openai.azure.com',
    });
    await waitFor(() => expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({
      third_party_llm: expectedModel,
    })));
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({ third_party_llm: expectedModel }),
    }));
    await waitFor(() => expect(onboardingAPI.apply).toHaveBeenCalledWith(expect.objectContaining({ third_party_llm: expectedModel })));
  });

  it.each([false, true])('preserves the configured Azure endpoint after returning to details: %s', async (returnToDetails) => {
    const user = userEvent.setup();
    const endpoint = 'https://production-resource.openai.azure.com';
    const azureProvider = {
      ...makeProvider('azure', 'Azure OpenAI', [{ id: 'gpt-4o', name: 'GPT-4o' }]),
      default_base_url: 'https://your-resource.openai.azure.com',
    };
    azureProvider.credential_schemas[0].fields.push({
      name: 'base_url', label: 'Endpoint', type: 'secret', required: true, placeholder: '',
    });
    catalogAPI.list.mockResolvedValue({ data: { providers: [azureProvider] } });
    onboardingAPI.getStatus.mockResolvedValue({
      data: makeStatus({
        completed: true,
        has_default_model: true,
        default_model: { provider_id: 'azure', model_id: 'existing-deployment' },
      }),
    });
    providerAPI.getCredentials.mockResolvedValue({ data: { has_credential: true, base_url: endpoint } });
    renderOnboarding();

    await user.click(await screen.findByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));
    expect(providerAPI.getCredentials).toHaveBeenCalledWith('azure');
    expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder')).toHaveValue(endpoint);
    if (returnToDetails) {
      await user.clear(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder'));
      await user.type(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder'), 'https://discarded-resource.openai.azure.com');
      await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.backToConfiguredDetails' }));
      await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));
      expect(screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyBaseUrlPlaceholder')).toHaveValue(endpoint);
    }
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    expect(await screen.findByText('onboarding.bootstrap.intelPageTitle')).toBeInTheDocument();
    expect(onboardingAPI.saveDraft).toHaveBeenCalledWith(expect.objectContaining({
      model: expect.objectContaining({
        third_party_llm: expect.objectContaining({
          provider_id: 'azure',
          model_id: 'existing-deployment',
          base_url: endpoint,
        }),
      }),
    }));
    expect(onboardingAPI.validate).not.toHaveBeenCalled();
  });

  it('reuses a configured MCP-only intelligence key when saving without revealing it first', async () => {
    const user = userEvent.setup();
    const status = makeStatus();
    Object.assign(status.threatbook_intel, {
      configured: true,
      region: 'cn',
      api_configured: false,
      mcp_configured: true,
      mcp_name: 'threatbook_mcp',
    });
    onboardingAPI.getStatus.mockResolvedValue({ data: status });
    providerAPI.revealServiceCredentials.mockResolvedValue({ data: { has_credential: false, api_key: null } });
    renderOnboarding();

    await user.click(await screen.findByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    await user.click(await screen.findByRole('button', { name: 'onboarding.bootstrap.editIntel' }));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.saveIntel' }));

    await waitFor(() => expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({
      region: 'cn',
      threatbook_api_key: 'saved-intel-mcp-key',
      threatbook_services_only: true,
    })));
    expect(mcpAPI.revealCredentials).toHaveBeenCalledWith('threatbook_mcp');
    expect(providerAPI.revealServiceCredentials).not.toHaveBeenCalled();
  });

  it('saves ThreatBook model setup without applying intelligence API or MCP services', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'tb-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await waitFor(() => {
      expect(onboardingAPI.validate).toHaveBeenCalledWith(expect.objectContaining({
        region: 'cn',
        use_threatbook_model: true,
        threatbook_api_key: 'tb-key',
        threatbook_model_only: true,
      }));
    });
    expect(onboardingAPI.apply).toHaveBeenCalledWith(expect.objectContaining({
      threatbook_model_only: true,
    }));
    expect(screen.queryByText('onboarding.bootstrap.resourceLabels.threatbook_api')).not.toBeInTheDocument();
    expect(screen.queryByText('onboarding.bootstrap.resourceLabels.threatbook_mcp')).not.toBeInTheDocument();
  });

  it('automatically retries the suggested region for the merged ThreatBook model option', async () => {
    const user = userEvent.setup();

    onboardingAPI.validate
      .mockResolvedValueOnce({
        data: {
          ...makeValidation({
            threatbook_llm: {
              enabled: true,
              success: false,
              code: 'region_mismatch',
              message: 'wrong region',
              details: {},
            },
          }),
          success: false,
          can_apply: false,
          threatbook_region_match: false,
          suggested_region: 'global',
          error_code: 'region_mismatch',
          message: 'wrong region',
        },
      })
      .mockResolvedValueOnce({
        data: makeValidation({
          threatbook_llm: {
            enabled: true,
            success: true,
            code: null,
            message: 'model ok',
            details: {},
          },
        }),
      });
    onboardingAPI.apply.mockResolvedValueOnce({
      data: {
        success: true,
        message: 'saved',
        region: 'global',
        threatbook_enabled: true,
        configured: ['threatbook_llm', 'default_llm'],
        skipped: [],
        default_model: {
          provider_id: 'threatbook-io-llm',
          model_id: 'minimax-m2.7',
        },
      },
    });

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder'), 'tb-global-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await waitFor(() => {
      expect(onboardingAPI.validate).toHaveBeenCalledTimes(2);
    });
    expect(onboardingAPI.validate).toHaveBeenNthCalledWith(1, expect.objectContaining({
      region: 'cn',
      use_threatbook_model: true,
      threatbook_api_key: 'tb-global-key',
      threatbook_model_only: true,
    }));
    expect(onboardingAPI.validate).toHaveBeenNthCalledWith(2, expect.objectContaining({
      region: 'global',
      use_threatbook_model: true,
      threatbook_api_key: 'tb-global-key',
      threatbook_model_only: true,
    }));
    expect(onboardingAPI.apply).toHaveBeenCalledWith(expect.objectContaining({
      region: 'global',
      threatbook_model_only: true,
    }));
    expect(screen.queryByText('onboarding.bootstrap.switchToGlobal')).not.toBeInTheDocument();
  });

  it('shows the model skip confirmation and advances to intelligence setup', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.skipPage' }));

    expect(screen.getByText('onboarding.bootstrap.skipModelDescription')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.confirmSkip' }));

    expect(screen.getByText('onboarding.bootstrap.intelPageTitle')).toBeInTheDocument();
  });

  it('uses neutral skip descriptions that point users to later setup locations', () => {
    const bootstrap = zhCNCommon.onboarding.bootstrap;

    expect(bootstrap.skipModelDescription).toBe('可以先跳过此步骤，稍后在左侧导航栏「模型清单」中继续完成默认模型配置。');
    expect(bootstrap.skipIntelDescription).toBe('可以先跳过此步骤。稍后可在左侧导航栏「工具清单」的 API 页签中找到 threatbook-cn 或 threatbook-io 配置情报 API；中国区 MCP 可在 MCP 页签的 ThreatBook MCP 中继续配置。');
    expect(bootstrap.skipModelDescription).not.toMatch(/无法|不可用|不能|失败/);
    expect(bootstrap.skipIntelDescription).not.toMatch(/无法|不可用|不能|失败/);
  });

  it('shows domestic and global intelligence activation links by selected region', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    const domesticLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(domesticLink).toHaveAttribute('href', 'https://x.threatbook.com/flocks/activate');
    expect(screen.getByText('onboarding.bootstrap.intelMcpCapability')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionGlobal' }));

    const globalLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(globalLink).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');
    expect(screen.queryByText('onboarding.bootstrap.intelMcpCapability')).not.toBeInTheDocument();
  });

  it('defaults intelligence setup to the international region in English', async () => {
    const user = userEvent.setup();
    currentLanguage.value = 'en-US';

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));

    const keyLink = screen.getByRole('link', { name: 'onboarding.bootstrap.intelKeyLink' });
    expect(keyLink).toHaveAttribute('href', 'https://i.threatbook.io/flocks/activate');
    expect(screen.queryByText('onboarding.bootstrap.intelMcpCapability')).not.toBeInTheDocument();
  });

  it('shows configured keys masked in edit mode and reveals them on demand', async () => {
    const user = userEvent.setup();

    onboardingAPI.getStatus.mockResolvedValue({
      data: makeStatus({
        completed: true,
        has_default_model: true,
        default_model: {
          provider_id: 'threatbook-cn-llm',
          model_id: 'minimax-m2.7',
        },
        threatbook_intel: {
          configured: true,
          region: 'cn',
          api_configured: true,
          api_service_id: 'threatbook-cn',
          mcp_configured: true,
          mcp_connected: true,
          mcp_status: 'connected',
          mcp_name: 'threatbook_mcp',
          service_matrix: {
            cn: ['api', 'mcp'],
            global: ['api'],
          },
        },
      }),
    });

    renderOnboarding();

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).toBeInTheDocument();
    const modelKeyInput = screen.getByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder');
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('type', 'password');
    expect(modelKeyInput).toHaveAttribute('readonly');
    expect(providerAPI.revealCredentials).not.toHaveBeenCalled();

    await user.click(screen.getByTitle('onboarding.bootstrap.showKey'));
    await waitFor(() => {
      expect(providerAPI.revealCredentials).toHaveBeenCalledWith('threatbook-cn-llm');
      expect(modelKeyInput).toHaveValue('saved-model-key');
    });
    expect(modelKeyInput).toHaveAttribute('type', 'text');

    await user.click(screen.getByTitle('onboarding.bootstrap.hideKey'));
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('type', 'password');

    const providerSelect = screen.getByRole('combobox');
    await user.selectOptions(providerSelect, 'openai-compatible');
    expect(modelKeyInput).toHaveValue('');
    expect(modelKeyInput).not.toHaveAttribute('readonly');
    await user.selectOptions(providerSelect, 'threatbook-free');
    expect(modelKeyInput).toHaveValue('************');
    expect(modelKeyInput).toHaveAttribute('readonly');

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    expect(screen.getByText('onboarding.bootstrap.intelConfiguredHint')).toBeInTheDocument();
    expect(screen.getAllByText('onboarding.bootstrap.intelConfiguredVerified').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editIntel' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editIntel' }));
    const intelKeyInput = screen.getByPlaceholderText('onboarding.bootstrap.intelKeyPlaceholder');
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('type', 'password');
    expect(intelKeyInput).toHaveAttribute('readonly');
    expect(providerAPI.revealServiceCredentials).not.toHaveBeenCalled();

    await user.click(screen.getByTitle('onboarding.bootstrap.showKey'));
    await waitFor(() => {
      expect(providerAPI.revealServiceCredentials).toHaveBeenCalledWith('threatbook-cn');
      expect(intelKeyInput).toHaveValue('saved-intel-key');
    });
    expect(intelKeyInput).toHaveAttribute('type', 'text');

    await user.click(screen.getByTitle('onboarding.bootstrap.hideKey'));
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionGlobal' }));
    expect(intelKeyInput).toHaveValue('');
    expect(intelKeyInput).not.toHaveAttribute('readonly');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.intelRegionChina' }));
    expect(intelKeyInput).toHaveValue('************');
    expect(intelKeyInput).toHaveAttribute('readonly');
  });

  it('starts Rex onboarding after skipping intelligence setup', async () => {
    const user = userEvent.setup();

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.nextStep' }));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.skipPage' }));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.confirmSkip' }));

    await waitFor(() => {
      expect(sessionApi.create).toHaveBeenCalledWith({ title: 'onboarding.sessionTitle' });
    });
    expect(clientPost).toHaveBeenCalledWith('/api/session/session-1/prompt_async', {
      parts: [{ type: 'text', text: 'onboarding.initialMessage' }],
    });
  });
});
