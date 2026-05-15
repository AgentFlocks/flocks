import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import OnboardingModal from './OnboardingModal';

const {
  catalogAPI,
  defaultModelAPI,
  mcpAPI,
  onboardingAPI,
  providerAPI,
  sessionApi,
} = vi.hoisted(() => ({
  catalogAPI: {
    list: vi.fn(),
  },
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  mcpAPI: {
    getCredentials: vi.fn(),
  },
  onboardingAPI: {
    validate: vi.fn(),
    apply: vi.fn(),
  },
  providerAPI: {
    getServiceCredentials: vi.fn(),
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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
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

function renderOnboarding() {
  return render(
    <MemoryRouter>
      <OnboardingModal onClose={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('OnboardingModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    defaultModelAPI.getResolved.mockRejectedValue(new Error('no default model'));
    catalogAPI.list.mockResolvedValue({
      data: {
        providers: [
          makeProvider('threatbook-cn-llm', 'ThreatBook CN', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('threatbook-io-llm', 'ThreatBook Global', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('openai-compatible', 'OpenAI Compatible', []),
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-chat', name: 'DeepSeek V3.2' }]),
        ],
      },
    });
    providerAPI.getServiceCredentials.mockResolvedValue({
      data: { has_credential: false },
    });
    mcpAPI.getCredentials.mockResolvedValue({
      data: { has_credential: false },
    });
    onboardingAPI.apply.mockResolvedValue({
      data: { success: true },
    });
    sessionApi.create.mockResolvedValue({ id: 'session-1' });
  });

  it('does not probe ThreatBook service credentials before optional section is visible', async () => {
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });

    await waitFor(() => {
      expect(catalogAPI.list).toHaveBeenCalled();
    });

    expect(providerAPI.getServiceCredentials).not.toHaveBeenCalled();
    expect(mcpAPI.getCredentials).not.toHaveBeenCalled();
  });

  it('includes openai-compatible in the onboarding provider dropdown', async () => {
    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });

    expect(screen.getByRole('option', { name: 'OpenAI Compatible' })).toBeInTheDocument();
  });

  it('shows a loading state instead of the primary edit form while the default model is still resolving', () => {
    defaultModelAPI.getResolved.mockImplementation(() => new Promise(() => {}));
    catalogAPI.list.mockImplementation(() => new Promise(() => {}));

    renderOnboarding();

    expect(screen.getByText('status.loading')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.tbPlaceholder')).not.toBeInTheDocument();
  });

  it('shows configured details instead of the edit form when a default model already exists', async () => {
    const user = userEvent.setup();

    defaultModelAPI.getResolved.mockResolvedValue({
      data: {
        provider_id: 'threatbook-cn-llm',
        model_id: 'minimax-m2.7',
      },
    });

    renderOnboarding();

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');

    await user.click(screen.getByText('onboarding.bootstrap.primaryTitle'));

    expect(screen.getByText('onboarding.bootstrap.configuredDetailsTitle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.tbPlaceholder')).not.toBeInTheDocument();
  });

  it('enters the edit form only after explicitly choosing to edit an existing configuration', async () => {
    const user = userEvent.setup();

    defaultModelAPI.getResolved.mockResolvedValue({
      data: {
        provider_id: 'threatbook-cn-llm',
        model_id: 'minimax-m2.7',
      },
    });

    renderOnboarding();

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');

    await user.click(screen.getByText('onboarding.bootstrap.primaryTitle'));
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' }));

    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('onboarding.bootstrap.tbPlaceholder')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.backToConfiguredDetails' })).toBeInTheDocument();
  });

  it('shows only third-party validation details when a third-party model validation fails', async () => {
    const user = userEvent.setup();

    onboardingAPI.validate.mockResolvedValue({
      data: {
        success: false,
        can_apply: false,
        threatbook_enabled: false,
        threatbook_key_valid: null,
        threatbook_region_match: null,
        suggested_region: null,
        error_code: 'third_party_validation_failed',
        message: '第三方模型验证失败',
        threatbook_resources: ['threatbook_api'],
        third_party_llm_valid: false,
        resource_results: {
          threatbook_api: {
            enabled: false,
            success: null,
            code: 'skipped',
            message: '未填写 ThreatBook key，已跳过该资源配置。',
            details: {},
          },
          third_party_llm: {
            enabled: true,
            success: false,
            code: 'third_party_validation_failed',
            message: 'Model Not Exist',
            details: {},
          },
        },
      },
    });

    renderOnboarding();

    const [providerSelect] = await screen.findAllByRole('combobox');
    await user.selectOptions(providerSelect, 'deepseek');
    await user.type(
      screen.getByPlaceholderText('onboarding.bootstrap.thirdPartyKeyPlaceholder'),
      'sk-test',
    );
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await screen.findByText('Model Not Exist');

    expect(screen.queryByText('未填写 ThreatBook key，已跳过该资源配置。')).not.toBeInTheDocument();
    expect(providerAPI.getServiceCredentials).not.toHaveBeenCalled();
    expect(mcpAPI.getCredentials).not.toHaveBeenCalled();
  });

  it('uses the backend returned default ThreatBook model after saving', async () => {
    const user = userEvent.setup();

    onboardingAPI.validate.mockResolvedValue({
      data: {
        success: true,
        can_apply: true,
        threatbook_enabled: true,
        threatbook_key_valid: true,
        threatbook_region_match: true,
        suggested_region: null,
        error_code: null,
        message: '验证成功',
        threatbook_resources: ['threatbook_llm', 'threatbook_api', 'threatbook_mcp'],
        third_party_llm_valid: null,
        resource_results: {},
      },
    });
    onboardingAPI.apply.mockResolvedValue({
      data: {
        success: true,
        message: '配置成功',
        region: 'cn',
        threatbook_enabled: true,
        configured: ['threatbook_llm', 'threatbook_api', 'threatbook_mcp', 'default_llm'],
        skipped: [],
        default_model: {
          provider_id: 'threatbook-cn-llm',
          model_id: 'minimax-m2.7',
        },
      },
    });

    renderOnboarding();

    await screen.findByRole('button', { name: 'onboarding.bootstrap.savePrimary' });
    await user.type(screen.getByPlaceholderText('onboarding.bootstrap.tbPlaceholder'), 'tb-key');
    await user.click(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' }));

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');
    await user.click(screen.getByText('onboarding.bootstrap.primaryTitle'));

    expect(screen.getByText('MiniMax M2.7')).toBeInTheDocument();
    expect(screen.queryByText('Qwen3.6 Plus')).not.toBeInTheDocument();
    expect(screen.queryByText('Qwen 3 Max')).not.toBeInTheDocument();
  });
});
